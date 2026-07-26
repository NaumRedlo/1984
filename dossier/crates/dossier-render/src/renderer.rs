//! Drawing one instant of a play.
//!
//! The renderer reads the timeline and the judgement rather than the snapshot
//! the simulator hands out, for one reason: it needs to know *when a note was
//! actually hit*. A circle leaves the screen when the player clicked it, not
//! when the map says it was due, and a note nobody touched lingers until its
//! window shuts. Drawing from nominal times alone gives an animation that is
//! subtly out of step with the play it claims to show.

use dossier_beatmap::Point;
use dossier_sim::{GameState, Judgement, TimedKind, TimedObject};
use tiny_skia::{
    FillRule, LineCap, LineJoin, Paint, PathBuilder, Pixmap, Shader, Stroke, Transform,
};

use crate::layout::Layout;
use crate::skin::{darken, with_alpha, Skin};
use crate::text::{Align, Label};

/// How long a judged note takes to fade out.
const HIT_FADE_MS: f64 = 220.0;

/// Cursor trail: how far back to sample, and how many samples.
const TRAIL_SPAN_MS: f64 = 110.0;
const TRAIL_SAMPLES: usize = 14;

/// What the renderer needs to know about an object beyond its geometry.
#[derive(Debug, Clone)]
struct Annotation {
    /// Index into the combo palette.
    colour: usize,
    /// Position within its combo, starting at one — the number osu! prints on
    /// the note, and the only cue for which of two overlapping notes comes
    /// first.
    number: u32,
    /// When the object left the screen, and how it went.
    resolved_ms: f64,
    missed: bool,
    /// First and last instant this object is worth drawing.
    spawn_ms: f64,
    gone_ms: f64,
    /// Slider ticks, in absolute time. Computing these per frame allocated a
    /// vector per slider per frame for a list that never changes.
    ticks_ms: Vec<f64>,
    /// The slider outline in playfield coordinates, built once. Rebuilding a
    /// few hundred line segments every frame was the single largest cost in the
    /// renderer.
    outline: Option<tiny_skia::Path>,
}

/// A map and a play, prepared for drawing.
///
/// Combo colours and judgement times are worked out once here rather than per
/// frame — at 60fps a two-minute map is 7000 frames, and none of this changes
/// between them.
pub struct Scene<'a> {
    state: &'a GameState,
    skin: Skin,
    annotations: Vec<Annotation>,
    /// The longest an object stays on screen, used to bound the search for
    /// what to draw: nothing that started earlier than this can still be up.
    longest_life_ms: f64,
}

impl<'a> Scene<'a> {
    pub fn new(state: &'a GameState, skin: Skin) -> Self {
        let objects = &state.timeline().objects;
        let window = state.difficulty().hit_window_50();

        let mut annotations = Vec::with_capacity(objects.len());
        let mut colour = 0usize;
        let mut number = 0u32;
        for (index, object) in objects.iter().enumerate() {
            // The palette advances on every new combo. The first object starts
            // one, but there is nothing before it to advance from.
            if object.new_combo && index > 0 {
                colour += 1;
                number = 0;
            }
            number += 1;

            let judged = state.judge().and_then(|judge| {
                judge
                    .events_for(index)
                    .find(|e| e.part.counts_for_accuracy())
                    .map(|e| (e.time_ms, e.result == Judgement::Miss))
            });
            let (resolved_ms, missed) = match judged {
                Some(pair) => pair,
                // No replay to judge: notes simply run their course.
                None => (object.end_ms.max(object.start_ms + window), false),
            };

            let spawn_ms = object.start_ms - state.difficulty().preempt_ms();
            let gone_ms = resolved_ms.max(object.end_ms) + HIT_FADE_MS;

            annotations.push(Annotation {
                colour,
                number,
                resolved_ms,
                missed,
                spawn_ms,
                gone_ms,
                ticks_ms: object.tick_times(),
                outline: outline_of(object),
            });
        }

        let longest_life_ms = annotations
            .iter()
            .zip(objects)
            .map(|(a, o)| a.gone_ms - o.start_ms)
            .fold(0.0f64, f64::max);

        Self {
            state,
            skin,
            annotations,
            longest_life_ms,
        }
    }

    /// The stretch of the object list that could be on screen at `time_ms`.
    ///
    /// Objects are in time order, so this is a contiguous range and both ends
    /// can be found by binary search. Testing every object on the map each
    /// frame worked, but cost the same on frame one as on a map of three
    /// thousand notes.
    fn candidates(&self, time_ms: f64) -> std::ops::Range<usize> {
        let objects = &self.state.timeline().objects;
        let preempt = self.state.difficulty().preempt_ms();
        let first = objects.partition_point(|o| o.start_ms < time_ms - self.longest_life_ms);
        let last = objects.partition_point(|o| o.start_ms - preempt <= time_ms);
        first..last
    }

    pub fn skin(&self) -> &Skin {
        &self.skin
    }

    /// Draw the playfield at `time_ms` in map time.
    pub fn frame(&self, time_ms: f64, layout: &Layout) -> Pixmap {
        let mut pixmap = Pixmap::new(layout.width, layout.height)
            .expect("a frame with a zero dimension was requested");
        self.draw_into(&mut pixmap, time_ms, layout);
        pixmap
    }

    /// Draw into a buffer that already exists.
    ///
    /// Video wants this: a 1080p frame is eight megabytes, and allocating and
    /// dropping one per frame is several gigabytes of churn over a map for no
    /// gain — the previous frame is entirely overwritten anyway.
    pub fn draw_into(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        pixmap.fill(self.skin.background);

        // Back to front: later notes sit underneath earlier ones, so the one
        // due next is always the one on top.
        // Back to front within the window that could be showing anything.
        for index in self.candidates(time_ms).rev() {
            if self.alpha_of(index, time_ms) > 0.0 {
                self.draw_object(pixmap, index, time_ms, layout);
            }
        }
        self.draw_cursor(pixmap, time_ms, layout);
        self.draw_hud(pixmap, time_ms, layout);
    }

    /// Combo and accuracy, in the corners osu! puts them.
    ///
    /// Only drawn when there is a play to report. A map opened without a replay
    /// has no score, and printing `0x 100.00%` over it would be stating
    /// something untrue rather than leaving a gap.
    fn draw_hud(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let (Some(font), Some(judge)) = (&self.skin.font, self.state.judge()) else {
            return;
        };
        let score = judge.state_at(time_ms);
        let height = f64::from(layout.height);
        let margin = (height * 0.03) as f32;

        let accuracy_size = (height * 0.045) as f32;
        font.draw(
            pixmap,
            Label {
                text: &format!("{:.2}%", score.accuracy()),
                x: layout.width as f32 - margin,
                y: margin + accuracy_size,
                size: accuracy_size,
                colour: self.skin.hud,
                align: Align::Right,
            },
        );

        let combo_size = (height * 0.06) as f32;
        font.draw(
            pixmap,
            Label {
                text: &format!("{}x", score.combo),
                x: margin,
                y: layout.height as f32 - margin,
                size: combo_size,
                colour: self.skin.hud,
                align: Align::Left,
            },
        );
    }

    /// Opacity of an object: zero before it spawns and after it has faded.
    fn alpha_of(&self, index: usize, time_ms: f64) -> f32 {
        let annotation = &self.annotations[index];
        if time_ms < annotation.spawn_ms || time_ms > annotation.gone_ms {
            return 0.0;
        }
        // A slider stays whole until its own end even if the head was judged
        // long before; only then does the fade start.
        let leaves = annotation.gone_ms - HIT_FADE_MS;
        let fade_in = self.state.difficulty().fade_in_ms().max(1.0);
        let appearing = ((time_ms - annotation.spawn_ms) / fade_in).clamp(0.0, 1.0);
        let leaving = 1.0 - ((time_ms - leaves) / HIT_FADE_MS).clamp(0.0, 1.0);
        (appearing * leaving) as f32
    }

    fn draw_object(&self, pixmap: &mut Pixmap, index: usize, time_ms: f64, layout: &Layout) {
        let object = &self.state.timeline().objects[index];
        let annotation = &self.annotations[index];
        let alpha = self.alpha_of(index, time_ms);
        let colour = self.skin.combo_colour(annotation.colour);
        let radius = layout.length(self.state.difficulty().circle_radius());

        match &object.kind {
            TimedKind::Spinner => self.draw_spinner(pixmap, object, time_ms, alpha, layout),
            TimedKind::Slider { .. } => {
                self.draw_slider_body(pixmap, annotation.outline.as_ref(), colour, alpha, layout);
                for &tick in &annotation.ticks_ms {
                    if tick > time_ms {
                        if let Some(at) = object.ball_at(tick) {
                            self.dot(
                                pixmap,
                                at,
                                radius * 0.14,
                                self.skin.circle_border,
                                alpha * 0.8,
                                layout,
                            );
                        }
                    }
                }
                if let Some(ball) = object.ball_at(time_ms) {
                    self.ring(
                        pixmap,
                        ball,
                        radius * 2.4,
                        radius * 0.06,
                        self.skin.circle_border,
                        alpha * 0.5,
                        layout,
                    );
                    self.dot(pixmap, ball, radius * 0.62, colour, alpha, layout);
                }
                // The head only stays until it is clicked.
                if time_ms <= annotation.resolved_ms {
                    self.draw_circle(pixmap, object.pos, radius, colour, alpha, layout);
                    self.draw_number(pixmap, object.pos, radius, annotation.number, alpha, layout);
                }
            }
            TimedKind::Circle => {
                self.draw_circle(pixmap, object.pos, radius, colour, alpha, layout);
                self.draw_number(pixmap, object.pos, radius, annotation.number, alpha, layout);
            }
        }

        // The approach circle only exists while the note is still coming.
        if !object.is_spinner() && time_ms < object.start_ms {
            let progress = self.state.timeline().approach_progress(object, time_ms);
            let scale = 1.0 + 3.0 * (1.0 - progress.clamp(0.0, 1.0)) as f32;
            self.ring(
                pixmap,
                object.pos,
                radius * scale,
                (radius * 0.09).max(1.0),
                colour,
                alpha,
                layout,
            );
        }

        if annotation.missed && time_ms > annotation.resolved_ms {
            // A miss is worth seeing: the note stops being a target and turns
            // into a mark of what went wrong.
            self.ring(
                pixmap,
                object.pos,
                radius,
                radius * 0.18,
                self.skin.spinner,
                alpha * 0.7,
                layout,
            );
        }
    }

    fn draw_circle(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let border = radius * self.skin.border_ratio;
        self.dot(pixmap, centre, radius, darken(colour, 0.25), alpha, layout);
        self.dot(pixmap, centre, radius - border, colour, alpha, layout);
        self.ring(
            pixmap,
            centre,
            radius - border / 2.0,
            border,
            self.skin.circle_border,
            alpha,
            layout,
        );
    }

    /// The combo number, centred on a note.
    ///
    /// Centred on the *ink*, not on the baseline: digits sit above the baseline
    /// by their own height, and hanging them off it would leave every number
    /// riding high in its circle.
    fn draw_number(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        number: u32,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(font) = &self.skin.font else {
            return;
        };
        let size = radius * 0.9;
        let (x, y) = layout.map(centre);
        font.draw(
            pixmap,
            Label {
                text: &number.to_string(),
                x,
                y: y + font.digit_height(size) / 2.0,
                size,
                colour: with_alpha(self.skin.circle_border, alpha),
                align: Align::Centre,
            },
        );
    }

    /// The slider track: a wide white stroke with a darker one inside it.
    ///
    /// The outline is in playfield coordinates and the transform does the
    /// scaling, so the stroke width is stated in osu!pixels and comes out right
    /// at any output size.
    fn draw_slider_body(
        &self,
        pixmap: &mut Pixmap,
        outline: Option<&tiny_skia::Path>,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(path) = outline else {
            return;
        };
        let radius = self.state.difficulty().circle_radius() as f32;
        let border = radius * self.skin.border_ratio * 2.0;

        for (width, shade) in [
            (radius * 2.0, self.skin.slider_border),
            (
                radius * 2.0 - border,
                darken(colour, self.skin.slider_body_dim),
            ),
        ] {
            let paint = Paint {
                shader: Shader::SolidColor(with_alpha(shade, alpha * self.skin.slider_body_alpha)),
                anti_alias: true,
                ..Default::default()
            };
            let stroke = Stroke {
                width,
                line_cap: LineCap::Round,
                line_join: LineJoin::Round,
                ..Default::default()
            };
            pixmap.stroke_path(path, &paint, &stroke, layout.transform(), None);
        }
    }

    fn draw_spinner(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        time_ms: f64,
        alpha: f32,
        layout: &Layout,
    ) {
        // The ring closes in as the spinner runs, which is how the player sees
        // time left rather than progress made.
        let progress =
            ((time_ms - object.start_ms) / object.duration_ms().max(1.0)).clamp(0.0, 1.0);
        let outer = layout.length(180.0) * (1.0 - 0.75 * progress as f32);
        self.ring(
            pixmap,
            Point::CENTRE,
            outer,
            layout.length(4.0),
            self.skin.spinner,
            alpha,
            layout,
        );
    }

    fn draw_cursor(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let track = self.state.cursor_track();
        let radius = layout.length(9.0);

        for step in (1..=TRAIL_SAMPLES).rev() {
            let age = step as f64 / TRAIL_SAMPLES as f64;
            let Some(sample) = track.sample(time_ms - age * TRAIL_SPAN_MS) else {
                continue;
            };
            let fade = (1.0 - age) as f32;
            self.dot(
                pixmap,
                sample.pos,
                radius * (0.45 + 0.4 * fade),
                self.skin.cursor_trail,
                0.35 * fade,
                layout,
            );
        }

        if let Some(sample) = track.sample(time_ms) {
            let held = sample.keys.is_pressed();
            self.dot(
                pixmap,
                sample.pos,
                radius * 1.25,
                self.skin.cursor_trail,
                0.5,
                layout,
            );
            self.dot(
                pixmap,
                sample.pos,
                radius * if held { 0.95 } else { 0.75 },
                self.skin.cursor,
                1.0,
                layout,
            );
        }
    }

    fn dot(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        if radius <= 0.0 || alpha <= 0.0 {
            return;
        }
        let (x, y) = layout.map(centre);
        let Some(path) = PathBuilder::from_circle(x, y, radius) else {
            return;
        };
        let paint = Paint {
            shader: Shader::SolidColor(with_alpha(colour, alpha)),
            anti_alias: true,
            ..Default::default()
        };
        pixmap.fill_path(
            &path,
            &paint,
            FillRule::Winding,
            Transform::identity(),
            None,
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn ring(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        width: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        if radius <= 0.0 || alpha <= 0.0 {
            return;
        }
        let (x, y) = layout.map(centre);
        let Some(path) = PathBuilder::from_circle(x, y, radius) else {
            return;
        };
        let paint = Paint {
            shader: Shader::SolidColor(with_alpha(colour, alpha)),
            anti_alias: true,
            ..Default::default()
        };
        let stroke = Stroke {
            width: width.max(0.5),
            ..Default::default()
        };
        pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
    }
}

/// A slider's centre line as a path in playfield coordinates.
fn outline_of(object: &TimedObject) -> Option<tiny_skia::Path> {
    let TimedKind::Slider { path, .. } = &object.kind else {
        return None;
    };
    let points = path.points();
    if points.len() < 2 {
        return None;
    }
    let mut builder = PathBuilder::new();
    builder.move_to(points[0].x as f32, points[0].y as f32);
    for point in &points[1..] {
        builder.line_to(point.x as f32, point.y as f32);
    }
    builder.finish()
}
