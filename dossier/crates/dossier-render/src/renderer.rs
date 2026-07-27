//! Drawing one instant of a play.
//!
//! The renderer reads the timeline and the judgement rather than the snapshot
//! the simulator hands out, for one reason: it needs to know *when a note was
//! actually hit*. A circle leaves the screen when the player clicked it, not
//! when the map says it was due, and a note nobody touched lingers until its
//! window shuts. Drawing from nominal times alone gives an animation that is
//! subtly out of step with the play it claims to show.

use dossier_beatmap::Point;
use dossier_sim::{GameState, Judgement, Part, TimedKind, TimedObject};
use tiny_skia::{
    FillRule, LineCap, LineJoin, Paint, PathBuilder, Pixmap, Shader, Stroke, Transform,
};

use crate::layout::Layout;
use crate::skin::{darken, lighten, with_alpha, ArrowShape, Skin};
use crate::text::{Align, Label};

/// How long a judged note takes to leave.
///
/// Down from 220ms, which read as sluggish: on a dense map the note being taken
/// away was still on screen when the next two had arrived, so the playfield
/// always carried a layer of things that had already happened.
const HIT_FADE_MS: f64 = 140.0;

/// How big the ball's inner core starts, as a fraction of the outer ball. It
/// grows from here to the full ball over the slider's span.
const BALL_CORE_SCALE: f32 = 0.34;

/// The reverse arrow, sized against the circle radius — which is also the
/// body's half-width, so the arrow keeps the same share of the track whatever
/// the circle size and whatever the output resolution.
const ARROW_SCALE: f32 = 0.52;
/// How long an arrow takes to go out once its last turn has passed.
const ARROW_FADE_MS: f64 = 120.0;
/// The kick when the ball strikes a turn, and how long it takes to settle.
const ARROW_PULSE: f32 = 0.35;
const ARROW_PULSE_MS: f64 = 150.0;
/// How much of the path an arrow fades in over as the body reaches its end.
const ARROW_REACH: f64 = 0.12;

/// Warning arrows before the map resumes: how long they are up, how fast they
/// flash, and where they sit on the field.
///
/// A break is the one stretch where the rhythm stops telling the player when
/// the next note is coming, so the game supplies the cue instead. They blink,
/// and the blinking strengthens as the break runs out: the flashing is what
/// catches an eye that has stopped watching the field, and the envelope under
/// it is what says how much time is left rather than merely that some is.
const WARNING_MS: f64 = 900.0;
/// How fast they clear once the map has resumed. Short, because by then the
/// player is reading notes and anything else on the field is in the way — but
/// not instant, because a mark that blinks out is a mark that was never there.
const WARNING_EXIT_MS: f64 = 130.0;
/// Hard into the corners of the playfield. The field takes 80% of the frame
/// height, so there is margin beyond it — an arrow this far out still has room
/// and is unmistakably not an object.
const WARNING_INSET: f64 = 16.0;
const WARNING_ROWS: [f64; 2] = [42.0, 342.0];
/// How many times they blink over the window.
const WARNING_FLASHES: f64 = 3.0;

/// A refused click shakes the note: how wide, how fast, and for how long.
///
/// Sideways only, and small — the note has to stay where the player is aiming
/// while it says "not yet". A wobble large enough to move the target would
/// punish them twice for the same mistake.
const SHAKE_MS: f64 = 120.0;
const SHAKE_WIDTH: f64 = 0.22;
const SHAKE_CYCLES: f64 = 3.0;

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
    /// The same, for a slider's head alone.
    ///
    /// Kept apart from the object's own verdict rather than folded into it. A
    /// slider is judged as a whole when it *ends*, so reusing that time left the
    /// head circle sitting on the playfield for the entire slide, on top of its
    /// own reverse arrow, when the player had clicked it at the first frame.
    /// The head is a separate thing that happens at a separate time, and the
    /// only safe way to draw it is to say so.
    head_ms: f64,
    head_missed: bool,
    /// First and last instant this object is worth drawing.
    spawn_ms: f64,
    gone_ms: f64,
    /// Slider ticks, in absolute time. Computing these per frame allocated a
    /// vector per slider per frame for a list that never changes.
    ticks_ms: Vec<f64>,
    /// When the game refused a click aimed at this note, so it can shake.
    shakes_ms: Vec<f64>,
    /// Where a repeating slider turns around, and which way the arrow points
    /// at each end. `None` for anything that never turns.
    turns: Option<(Turn, Turn)>,
}

/// One end of a repeating slider.
#[derive(Debug, Clone, Copy)]
struct Turn {
    at: Point,
    /// Unit vector pointing the way the ball leaves after turning — which is
    /// what the arrow has to say.
    dir: (f64, f64),
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
                // No replay to judge: the note resolves when its own window
                // shuts. A slider's *head* goes then too — tying it to the
                // slider's end left the head circle sitting on the playfield
                // for the whole slide, over the top of its own reverse arrow.
                None => (object.start_ms + window, false),
            };

            // The head's own click, when there is a replay to have clicked it.
            // Falls back to the window shutting, which is where an unclicked
            // head goes anyway.
            let head = state.judge().and_then(|judge| {
                judge
                    .events_for(index)
                    .find(|e| e.part == Part::SliderHead)
                    .map(|e| (e.time_ms, e.result == Judgement::Miss))
            });
            let (head_ms, head_missed) =
                head.unwrap_or((object.start_ms + window, missed && object.is_slider()));

            let spawn_ms = object.start_ms - state.difficulty().preempt_ms();
            let gone_ms = resolved_ms.max(object.end_ms) + HIT_FADE_MS;

            annotations.push(Annotation {
                colour,
                number,
                resolved_ms,
                missed,
                head_ms,
                head_missed,
                spawn_ms,
                gone_ms,
                ticks_ms: object.tick_times(),
                shakes_ms: state
                    .judge()
                    .map(|judge| {
                        judge
                            .shakes()
                            .iter()
                            .filter(|(at, _)| *at == index)
                            .map(|(_, when)| *when)
                            .collect()
                    })
                    .unwrap_or_default(),
                turns: turns_of(object),
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
        // due next is always the one on top. Only the window that could be
        // showing anything is considered.
        for index in self.candidates(time_ms).rev() {
            if self.alpha_of(index, time_ms) > 0.0 {
                self.draw_object(pixmap, index, time_ms, layout);
            }
        }
        self.draw_break_warning(pixmap, time_ms, layout);
        self.draw_cursor(pixmap, time_ms, layout);
        self.draw_hud(pixmap, time_ms, layout);
    }

    /// Arrows down both sides while a break is running out.
    ///
    /// Drawn under the cursor and over the field: they are a message to the
    /// player, not part of the map, and nothing about the play should be
    /// hidden behind them.
    fn draw_break_warning(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(ends) = self
            .state
            .timeline()
            .breaks
            .iter()
            .find(|(starts, ends)| time_ms >= *starts && time_ms < *ends + WARNING_EXIT_MS)
            .map(|&(_, ends)| ends)
        else {
            return;
        };

        let (alpha, scale) = if time_ms < ends {
            // Rising: out of nothing, brightest at the moment play resumes.
            // Squared rather than linear so the early part of the window stays
            // faint — the arrow should grow into the eye, not sit there
            // half-lit for most of a second.
            let closing = 1.0 - ((ends - time_ms) / WARNING_MS).clamp(0.0, 1.0);
            if closing <= 0.0 {
                return;
            }
            let flash = (closing * WARNING_FLASHES * std::f64::consts::TAU)
                .sin()
                .abs();
            // The blink never goes fully dark: an arrow that disappears between
            // beats reads as a rendering fault rather than as a signal.
            (
                (0.30 + 0.70 * flash) as f32 * (closing * closing) as f32,
                1.0,
            )
        } else {
            // Gone: quickly, and shrinking as it goes so the exit is a
            // movement rather than a dimming.
            let leaving = ((time_ms - ends) / WARNING_EXIT_MS).clamp(0.0, 1.0);
            let left = 1.0 - leaving;
            ((left * left) as f32, (1.0 - 0.45 * leaving) as f32)
        };
        if alpha <= 0.01 {
            return;
        }

        let size = layout.length(self.state.difficulty().circle_radius()) * 0.8 * scale;
        for y in WARNING_ROWS {
            for (x, dir) in [
                (WARNING_INSET, (1.0, 0.0)),
                (
                    dossier_beatmap::PLAYFIELD_WIDTH - WARNING_INSET,
                    (-1.0, 0.0),
                ),
            ] {
                self.draw_chevron(
                    pixmap,
                    Turn {
                        at: Point { x, y },
                        dir,
                    },
                    size,
                    alpha,
                    ArrowShape::Rounded,
                    layout,
                );
            }
        }
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
        let appearing = ((time_ms - annotation.spawn_ms) / fade_in).clamp(0.0, 1.0) as f32;
        let leaving = fade((((time_ms - leaves) / HIT_FADE_MS).clamp(0.0, 1.0)) as f32);
        appearing * leaving
    }

    /// How far through leaving the screen a resolved note is: 0 while it is
    /// still a target, 1 once it has finished going.
    ///
    /// Separate from the alpha because the two are not the same curve on a
    /// slider — the body holds full opacity until the slider ends, while its
    /// head left the moment it was clicked.
    fn exit_progress(&self, from_ms: f64, time_ms: f64) -> f32 {
        (((time_ms - from_ms) / HIT_FADE_MS).clamp(0.0, 1.0)) as f32
    }

    /// The stretch of a slider's path that is drawn right now, as fractions.
    ///
    /// Two things move. Coming in, the body grows from the head over the same
    /// window the note fades in on — a slider that appears whole tells the
    /// player nothing about which way it goes, and the growth is the cue.
    /// Going out, the body retracts behind the ball, so the part already played
    /// stops competing for attention with the part still to play.
    ///
    /// A slider with repeats only retracts on its final pass: while there is
    /// still a turn ahead, the whole body is the target.
    fn snake(&self, object: &TimedObject, index: usize, time_ms: f64) -> (f64, f64) {
        let TimedKind::Slider { slides, .. } = &object.kind else {
            return (0.0, 1.0);
        };
        let annotation = &self.annotations[index];

        let fade_in = self.state.difficulty().fade_in_ms().max(1.0);
        let grown = ((time_ms - annotation.spawn_ms) / fade_in).clamp(0.0, 1.0);
        if time_ms < object.start_ms {
            return (0.0, grown);
        }

        // Clamped to the last slide so that once the slider is over the body
        // holds its retracted shape through the fade, instead of springing back
        // to full length for the final few frames.
        let slides = (*slides).max(1);
        let span = (object.end_ms - object.start_ms).max(1.0);
        let travelled =
            ((time_ms - object.start_ms) / span * f64::from(slides)).clamp(0.0, f64::from(slides));
        let last = f64::from(slides - 1);
        if travelled < last {
            return (0.0, 1.0);
        }

        let local = (travelled - last).clamp(0.0, 1.0);
        if slides % 2 == 1 {
            (local, 1.0) // the final pass runs forwards, so the start retreats
        } else {
            (0.0, 1.0 - local) // …and backwards, so the far end does
        }
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
                let (from, to) = self.snake(object, index, time_ms);
                self.draw_slider_body(pixmap, object, (from, to), colour, alpha, layout);
                for &tick in &annotation.ticks_ms {
                    // A tick belongs to the body, so it cannot precede it. It
                    // used to be drawn as soon as the note appeared, which put
                    // dots in empty space ahead of a slider that had not grown
                    // that far — and a dot with no line under it does not read
                    // as sitting on the line.
                    let on_body =
                        path_fraction(object, tick).is_some_and(|frac| frac >= from && frac <= to);
                    if tick > time_ms && on_body {
                        if let Some(at) = object.ball_at(tick) {
                            self.dot(
                                pixmap,
                                at,
                                radius * 0.14,
                                lighten(self.skin.circle_border, 0.5),
                                alpha,
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
                    // Two balls, one inside the other. The outer one is the
                    // full-size ball the game draws; the inner one grows to
                    // meet it as the slider runs out, so how far through you
                    // are is readable from the ball itself instead of only
                    // from where it sits on the body.
                    //
                    // The inner one is lifted toward white rather than made
                    // translucent: a paler combo colour still says which combo
                    // this is, where a see-through one would just take on the
                    // body underneath it.
                    let done = ((time_ms - object.start_ms)
                        / (object.end_ms - object.start_ms).max(1.0))
                    .clamp(0.0, 1.0) as f32;
                    self.dot(pixmap, ball, radius, colour, alpha, layout);
                    self.dot(
                        pixmap,
                        ball,
                        radius * (BALL_CORE_SCALE + (1.0 - BALL_CORE_SCALE) * done),
                        lighten(colour, 0.45),
                        alpha,
                        layout,
                    );
                }
                self.draw_reverse_arrow(
                    pixmap,
                    object,
                    annotation,
                    time_ms,
                    radius,
                    alpha,
                    (from, to),
                    layout,
                );
                // The head leaves on its own click rather than with the rest of
                // the slider — but it leaves, it does not vanish. Popping out of
                // existence mid-slide was the most artificial thing on screen.
                let exit = self.exit_progress(annotation.head_ms, time_ms);
                if exit < 1.0 {
                    let leaving = alpha * fade(exit);
                    let grown = radius * hit_expansion(exit, annotation.head_missed);
                    let at = shaken(object.pos, annotation, time_ms, self.state);
                    self.draw_circle(pixmap, at, grown, colour, leaving, layout);
                    // The number goes the instant the note is judged, while the
                    // circle keeps swelling out. It is a label on a target, and
                    // once the target has been taken it is answering a question
                    // nobody is asking any more — stretched and faded along
                    // with the circle it just smears.
                    if exit <= 0.0 {
                        self.draw_number(pixmap, at, grown, annotation.number, leaving, layout);
                    }
                }
            }
            TimedKind::Circle => {
                // A hit circle swells as it goes; a missed one only fades. The
                // difference is the whole point — it says which happened without
                // waiting for the combo counter to drop.
                let exit = self.exit_progress(annotation.resolved_ms, time_ms);
                let grown = radius * hit_expansion(exit, annotation.missed);
                let at = shaken(object.pos, annotation, time_ms, self.state);
                self.draw_circle(pixmap, at, grown, colour, alpha, layout);
                if exit <= 0.0 {
                    self.draw_number(pixmap, at, grown, annotation.number, alpha, layout);
                }
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
    /// The arrow telling the player they'll be coming back.
    ///
    /// Only one shows at a time, at the end the ball is heading for, and only
    /// while a turn is still to come. Without it a repeating slider is drawn
    /// exactly like one that ends where it stops — the map is being
    /// misrepresented, not merely under-decorated.
    #[allow(clippy::too_many_arguments)]
    fn draw_reverse_arrow(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        annotation: &Annotation,
        time_ms: f64,
        radius: f32,
        alpha: f32,
        (from, to): (f64, f64),
        layout: &Layout,
    ) {
        let (
            Some((head, tail)),
            TimedKind::Slider {
                slides,
                slide_duration_ms,
                ..
            },
        ) = (annotation.turns, &object.kind)
        else {
            return;
        };

        if *slide_duration_ms <= 0.0 {
            return;
        }

        // Turns happen at the slide boundaries: the first is at the tail, the
        // next at the head, alternating. Both ends carry an arrow while both
        // still have a turn coming — showing only the nearest one made the
        // far end's arrow vanish the moment the near one appeared, which reads
        // as the slider changing its mind about where it goes.
        for (at_tail, turn) in [(true, tail), (false, head)] {
            let turns = (1..*slides)
                .filter(|k| k.is_multiple_of(2) != at_tail)
                .map(|k| object.start_ms + f64::from(k) * slide_duration_ms);

            let turns: Vec<f64> = turns.collect();
            let (leaving, pulse) = arrow_life(&turns, time_ms);
            // An arrow cannot sit on a part of the body that has not grown
            // yet, for the same reason a tick cannot — and it arrives with the
            // body rather than appearing whole on top of it.
            let arriving = if at_tail {
                ((to - (1.0 - ARROW_REACH)) / ARROW_REACH).clamp(0.0, 1.0) as f32
            } else {
                ((ARROW_REACH - from) / ARROW_REACH).clamp(0.0, 1.0) as f32
            };

            let showing = alpha * leaving * arriving;
            if showing <= 0.0 {
                continue;
            }
            self.draw_chevron(
                pixmap,
                turn,
                radius * ARROW_SCALE * (1.0 + pulse),
                showing,
                self.skin.arrow,
                layout,
            );
        }
    }

    /// A filled triangle pointing along `turn.dir`.
    #[allow(clippy::too_many_arguments)]
    fn draw_chevron(
        &self,
        pixmap: &mut Pixmap,
        turn: Turn,
        size: f32,
        alpha: f32,
        shape: ArrowShape,
        layout: &Layout,
    ) {
        let (dx, dy) = turn.dir;
        let (px, py) = (-dy, dx); // perpendicular, for the base corners
        let (cx, cy) = layout.map(turn.at);
        let scale = size;

        let point = |along: f64, across: f64| {
            (
                cx + (dx * along + px * across) as f32 * scale,
                cy + (dy * along + py * across) as f32 * scale,
            )
        };

        // The swept shape carries a notch in its tail, so it needs the extra
        // vertex; the plain triangle closes straight across.
        let outline: &[(f64, f64)] = match shape {
            ArrowShape::Triangle | ArrowShape::Rounded => {
                &[(1.0, 0.0), (-0.55, 0.85), (-0.55, -0.85)]
            }
            ArrowShape::Swept => &[(1.0, 0.0), (-0.78, 0.82), (-0.38, 0.0), (-0.78, -0.82)],
        };

        let mut builder = PathBuilder::with_capacity(outline.len() + 1, outline.len() + 1);
        let (first_x, first_y) = point(outline[0].0, outline[0].1);
        builder.move_to(first_x, first_y);
        for &(along, across) in &outline[1..] {
            let (x, y) = point(along, across);
            builder.line_to(x, y);
        }
        builder.close();
        let Some(path) = builder.finish() else {
            return;
        };

        let paint = Paint {
            shader: Shader::SolidColor(with_alpha(self.skin.circle_border, alpha)),
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
        // Corners rounded by stroking the same outline over the fill. Sharp
        // points on a mark this small read as jagged rather than as crisp,
        // and the drawn shape this is after has generous rounding.
        if shape != ArrowShape::Triangle {
            let stroke = Stroke {
                width: size * 0.22,
                line_cap: LineCap::Round,
                line_join: LineJoin::Round,
                ..Default::default()
            };
            pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
        }
    }

    fn draw_slider_body(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        snake: (f64, f64),
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(path) = body_path(object, snake) else {
            return;
        };
        let path = &path;
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
/// The note's drawn position, shaken if it has just refused a click.
fn shaken(pos: Point, annotation: &Annotation, time_ms: f64, state: &GameState) -> Point {
    let radius = state.difficulty().circle_radius();
    let dx = shake_offset(&annotation.shakes_ms, time_ms, radius);
    Point {
        x: pos.x + dx,
        y: pos.y,
    }
}

/// Sideways offset of a note that has just refused a click, in osu!pixels.
///
/// A decaying sine: it starts at full swing on the frame the click landed and
/// settles inside a tenth of a second, so a note being clicked at repeatedly
/// shakes on each one rather than blurring into a single long wobble.
fn shake_offset(shakes: &[f64], time_ms: f64, radius: f64) -> f64 {
    let Some(last) = shakes
        .iter()
        .copied()
        .filter(|&at| at <= time_ms && time_ms - at < SHAKE_MS)
        .fold(None::<f64>, |best, at| {
            Some(best.map_or(at, |b: f64| b.max(at)))
        })
    else {
        return 0.0;
    };
    let progress = (time_ms - last) / SHAKE_MS;
    let swing = (progress * SHAKE_CYCLES * std::f64::consts::TAU).sin();
    swing * (1.0 - progress) * radius * SHAKE_WIDTH
}

/// How an arrow at one end of a slider presents itself: how bright, and how
/// much bigger than its resting size.
///
/// `turns` is every moment the ball turns around at *that* end. The arrow is
/// full while one of them is still coming, then goes out over its own window
/// rather than blinking off on the frame the ball touches it. Landing gives it
/// a kick, which is the cue that the direction just changed — it decays
/// quadratically so the kick is over well before the fade is.
///
/// Split out from the drawing because it cannot be measured through pixels:
/// the ball and the ticks pass through the same few square pixels at exactly
/// the moment in question, and there is no telling their brightness from the
/// arrow's.
fn arrow_life(turns: &[f64], time_ms: f64) -> (f32, f32) {
    let ahead = turns.iter().any(|&at| at > time_ms);
    let behind = turns
        .iter()
        .copied()
        .filter(|&at| at <= time_ms)
        .fold(None::<f64>, |best, at| {
            Some(best.map_or(at, |b: f64| b.max(at)))
        });

    let leaving = match (ahead, behind) {
        (true, _) => 1.0,
        (false, Some(last)) => 1.0 - ((time_ms - last) / ARROW_FADE_MS).clamp(0.0, 1.0) as f32,
        (false, None) => 0.0,
    };
    let pulse = behind.map_or(0.0, |last| {
        let since = ((time_ms - last) / ARROW_PULSE_MS).clamp(0.0, 1.0) as f32;
        ARROW_PULSE * (1.0 - since) * (1.0 - since)
    });
    (leaving, pulse)
}

/// Where along the path a moment of a slider falls, as a fraction.
///
/// Reversed slides walk the path backwards, so their local progress is
/// mirrored — which is what makes this the right thing to compare against the
/// grown stretch of the body rather than raw elapsed time.
fn path_fraction(object: &TimedObject, time_ms: f64) -> Option<f64> {
    let TimedKind::Slider {
        slides,
        slide_duration_ms,
        ..
    } = &object.kind
    else {
        return None;
    };
    if *slide_duration_ms <= 0.0 {
        return None;
    }
    let travelled = (time_ms - object.start_ms) / slide_duration_ms;
    let last = f64::from(slides.saturating_sub(1));
    let slide = travelled.floor().clamp(0.0, last);
    let local = (travelled - slide).clamp(0.0, 1.0);
    Some(if (slide as u32).is_multiple_of(2) {
        local
    } else {
        1.0 - local
    })
}

/// How much a note swells as it leaves, as a multiple of its radius.
///
/// A hit expands while it fades — the note is being taken away, and the growth
/// reads as the taking. A miss does not: it stays the size it was and simply
/// stops being there, which is what missing looks like. Making both expand
/// would throw away the only difference between them a still frame can show.
fn hit_expansion(exit: f32, missed: bool) -> f32 {
    if missed {
        1.0
    } else {
        // Eased out, so nearly all the growth is over in the first third. The
        // note has to read as struck, and a strike is not a linear ramp — a
        // linear one looks like the note is being inflated.
        1.0 + 0.4 * (1.0 - (1.0 - exit) * (1.0 - exit))
    }
}

/// Opacity of a note that is on its way out, from its exit progress.
///
/// Squared, so it is half gone a third of the way through. Together with the
/// shorter window this is what makes the note read as taken rather than as
/// slowly dissolving — the shape lingers a moment at its new size while the
/// colour has already left.
fn fade(exit: f32) -> f32 {
    let left = 1.0 - exit;
    left * left
}

/// The slider body between two progress fractions, ready to stroke.
///
/// Built per frame rather than once, because the stretch it covers changes
/// every frame while the slider is growing or retracting. The prebuilt path it
/// replaces was described in this file as the renderer's largest cost, which
/// turned out to be wrong: building a 240-point body measures at 0.0022ms
/// against 1.2441ms to stroke it once, and it is stroked twice. Under a fifth
/// of a percent. See the `path_building_against_stroking` benchmark below —
/// comparing two binaries end to end could not tell, the machine noise being
/// larger than the effect in both directions on successive runs.
fn body_path(object: &TimedObject, (from, to): (f64, f64)) -> Option<tiny_skia::Path> {
    let TimedKind::Slider { path, .. } = &object.kind else {
        return None;
    };
    let (start, interior, end) = path.segment(from, to)?;
    // Sized up front: the builder otherwise regrows both of its buffers a dozen
    // times over a path of a few hundred points, once per slider per frame.
    let mut builder = PathBuilder::with_capacity(interior.len() + 2, interior.len() + 2);
    builder.move_to(start.x as f32, start.y as f32);
    for point in interior {
        builder.line_to(point.x as f32, point.y as f32);
    }
    builder.line_to(end.x as f32, end.y as f32);
    builder.finish()
}

/// The ends of a repeating slider, with the direction the ball leaves each.
///
/// `None` when the slider never turns: a one-slide slider has no arrow, and
/// drawing one would tell the player to go back over something that ends there.
fn turns_of(object: &TimedObject) -> Option<(Turn, Turn)> {
    let TimedKind::Slider { path, slides, .. } = &object.kind else {
        return None;
    };
    if *slides < 2 {
        return None;
    }
    let points = path.points();
    let first = points.first()?;
    let second = points.get(1)?;
    let last = points.last()?;
    let before = points.get(points.len().checked_sub(2)?)?;

    Some((
        // At the head the ball turns and heads off down the path…
        Turn {
            at: *first,
            dir: unit(second.x - first.x, second.y - first.y),
        },
        // …and at the tail it turns and comes back.
        Turn {
            at: *last,
            dir: unit(before.x - last.x, before.y - last.y),
        },
    ))
}

fn unit(dx: f64, dy: f64) -> (f64, f64) {
    let length = dx.hypot(dy);
    if length < 1e-9 {
        (1.0, 0.0)
    } else {
        (dx / length, dy / length)
    }
}

#[cfg(test)]
mod exits {
    use super::*;

    #[test]
    fn an_arrow_holds_while_a_turn_is_coming_and_then_goes_out() {
        let turns = [1000.0, 3000.0];
        assert_eq!(arrow_life(&turns, 500.0).0, 1.0, "before the first");
        assert_eq!(arrow_life(&turns, 2000.0).0, 1.0, "another is still coming");

        // After the last one it decays rather than blinking off.
        let half = arrow_life(&turns, 3000.0 + ARROW_FADE_MS / 2.0).0;
        assert!(half > 0.0 && half < 1.0, "{half}");
        assert_eq!(
            arrow_life(&turns, 3000.0 + ARROW_FADE_MS).0,
            0.0,
            "and is gone"
        );
    }

    #[test]
    fn landing_kicks_the_arrow_and_the_kick_settles_first() {
        let turns = [1000.0];
        assert_eq!(
            arrow_life(&turns, 999.0).1,
            0.0,
            "nothing has struck it yet"
        );

        let struck = arrow_life(&turns, 1000.0).1;
        assert!(
            (struck - ARROW_PULSE).abs() < 1e-6,
            "full kick on landing: {struck}"
        );

        // Quadratic decay, so the kick is over before the fade is.
        let later = arrow_life(&turns, 1000.0 + ARROW_PULSE_MS / 2.0).1;
        assert!(later < struck / 2.0, "{later} against {struck}");
        assert_eq!(arrow_life(&turns, 1000.0 + ARROW_PULSE_MS).1, 0.0);
    }

    #[test]
    fn an_end_that_never_turns_shows_nothing() {
        assert_eq!(arrow_life(&[], 1234.0), (0.0, 0.0));
    }

    #[test]
    fn a_hit_swells_as_it_goes_and_a_miss_does_not() {
        // The two exits have to look different, or a still frame cannot say
        // which happened without waiting for the combo counter to drop.
        assert_eq!(hit_expansion(0.0, false), 1.0, "nothing has happened yet");
        assert!(hit_expansion(1.0, false) > hit_expansion(0.5, false));
        assert_eq!(hit_expansion(1.0, true), 1.0, "a miss keeps its size");
        assert_eq!(hit_expansion(0.5, true), 1.0);
    }
}

#[cfg(test)]
mod cost {
    use super::*;

    /// What building a slider body actually costs, against what stroking one
    /// costs. Run on demand:
    ///
    ///     cargo test --release -p dossier-render path_building -- --ignored --nocapture
    #[test]
    #[ignore = "a measurement, not an assertion"]
    fn path_building_against_stroking() {
        use std::time::Instant;

        // A slider body flattens to a few hundred points at a quarter-pixel.
        let points: Vec<(f32, f32)> = (0..240)
            .map(|i| (i as f32 * 1.7, (i as f32 * 0.11).sin() * 40.0 + 200.0))
            .collect();

        let rounds = 10_000;
        let mark = Instant::now();
        let mut kept = 0usize;
        for _ in 0..rounds {
            let mut builder = PathBuilder::with_capacity(points.len(), points.len());
            builder.move_to(points[0].0, points[0].1);
            for p in &points[1..] {
                builder.line_to(p.0, p.1);
            }
            kept += builder.finish().map_or(0, |p| p.len());
        }
        let building = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

        let mut pixmap = Pixmap::new(1920, 1080).unwrap();
        let mut builder = PathBuilder::with_capacity(points.len(), points.len());
        builder.move_to(points[0].0, points[0].1);
        for p in &points[1..] {
            builder.line_to(p.0, p.1);
        }
        let path = builder.finish().unwrap();
        let paint = Paint::default();
        let stroke = Stroke {
            width: 64.0,
            line_cap: LineCap::Round,
            line_join: LineJoin::Round,
            ..Default::default()
        };

        let strokes = 200;
        let mark = Instant::now();
        for _ in 0..strokes {
            pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
        }
        let stroking = mark.elapsed().as_secs_f64() / f64::from(strokes) * 1000.0;

        println!(
            "slider body: building {building:.4}ms, stroking {stroking:.4}ms \
             — building is {:.2}% of one stroke ({kept} verbs kept)",
            building / stroking * 100.0
        );
    }
}
