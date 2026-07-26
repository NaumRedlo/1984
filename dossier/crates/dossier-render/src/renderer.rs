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

/// How long a judged note takes to fade out.
const HIT_FADE_MS: f64 = 220.0;

/// Cursor trail: how far back to sample, and how many samples.
const TRAIL_SPAN_MS: f64 = 110.0;
const TRAIL_SAMPLES: usize = 14;

/// What the renderer needs to know about an object beyond its geometry.
#[derive(Debug, Clone, Copy)]
struct Annotation {
    /// Index into the combo palette.
    colour: usize,
    /// When the object left the screen, and how it went.
    resolved_ms: f64,
    missed: bool,
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
}

impl<'a> Scene<'a> {
    pub fn new(state: &'a GameState, skin: Skin) -> Self {
        let objects = &state.timeline().objects;
        let window = state.difficulty().hit_window_50();

        let mut annotations = Vec::with_capacity(objects.len());
        let mut colour = 0usize;
        for (index, object) in objects.iter().enumerate() {
            // The palette advances on every new combo. The first object starts
            // one, but there is nothing before it to advance from.
            if object.new_combo && index > 0 {
                colour += 1;
            }

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

            annotations.push(Annotation {
                colour,
                resolved_ms,
                missed,
            });
        }

        Self {
            state,
            skin,
            annotations,
        }
    }

    pub fn skin(&self) -> &Skin {
        &self.skin
    }

    /// Draw the playfield at `time_ms` in map time.
    pub fn frame(&self, time_ms: f64, layout: &Layout) -> Pixmap {
        let mut pixmap = Pixmap::new(layout.width, layout.height)
            .expect("a frame with a zero dimension was requested");
        pixmap.fill(self.skin.background);

        // Back to front: later notes sit underneath earlier ones, so the one
        // due next is always the one on top.
        let mut visible: Vec<usize> = (0..self.state.timeline().objects.len())
            .filter(|&i| self.alpha_of(i, time_ms) > 0.0)
            .collect();
        visible.reverse();

        for index in visible {
            self.draw_object(&mut pixmap, index, time_ms, layout);
        }
        self.draw_cursor(&mut pixmap, time_ms, layout);
        pixmap
    }

    /// Opacity of an object: zero before it spawns and after it has faded.
    fn alpha_of(&self, index: usize, time_ms: f64) -> f32 {
        let object = &self.state.timeline().objects[index];
        let annotation = self.annotations[index];
        let difficulty = self.state.difficulty();

        let spawn = object.start_ms - difficulty.preempt_ms();
        if time_ms < spawn {
            return 0.0;
        }
        // A slider stays whole until its own end even if the head was judged
        // long before; only then does the fade start.
        let leaves = annotation.resolved_ms.max(object.end_ms);
        if time_ms > leaves + HIT_FADE_MS {
            return 0.0;
        }

        let fade_in = difficulty.fade_in_ms().max(1.0);
        let appearing = ((time_ms - spawn) / fade_in).clamp(0.0, 1.0);
        let leaving = 1.0 - ((time_ms - leaves) / HIT_FADE_MS).clamp(0.0, 1.0);
        (appearing * leaving) as f32
    }

    fn draw_object(&self, pixmap: &mut Pixmap, index: usize, time_ms: f64, layout: &Layout) {
        let object = &self.state.timeline().objects[index];
        let annotation = self.annotations[index];
        let alpha = self.alpha_of(index, time_ms);
        let colour = self.skin.combo_colour(annotation.colour);
        let radius = layout.length(self.state.difficulty().circle_radius());

        match &object.kind {
            TimedKind::Spinner => self.draw_spinner(pixmap, object, time_ms, alpha, layout),
            TimedKind::Slider { path, .. } => {
                self.draw_slider_body(pixmap, path.points(), radius, colour, alpha, layout);
                for tick in object.tick_times() {
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
                }
            }
            TimedKind::Circle => {
                self.draw_circle(pixmap, object.pos, radius, colour, alpha, layout)
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

    fn draw_slider_body(
        &self,
        pixmap: &mut Pixmap,
        points: &[Point],
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        if points.len() < 2 {
            return;
        }
        let mut builder = PathBuilder::new();
        let (x, y) = layout.map(points[0]);
        builder.move_to(x, y);
        for point in &points[1..] {
            let (x, y) = layout.map(*point);
            builder.line_to(x, y);
        }
        let Some(path) = builder.finish() else {
            return;
        };

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
            pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
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
