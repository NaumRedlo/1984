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
use tiny_skia::{Color, FillRule, LineCap, LineJoin, Paint, PathBuilder, Pixmap, Rect, Shader, Stroke, Transform,
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
/// the next note is coming, so the game supplies the cue instead.
///
/// They pulse on the map's own beat rather than on a rhythm of their own. The
/// music does not stop during a break, so the beat is the one clock the player
/// is still reading — a cue that moves with it says something they can already
/// feel, which is what makes it easy to catch. An arbitrary blink competes
/// with the music instead of riding it.
const WARNING_MS: f64 = 900.0;
/// How fast they clear once the map has resumed. Short, because by then the
/// player is reading notes and anything else on the field is in the way — but
/// not instant, because a mark that blinks out is a mark that was never there.
const WARNING_EXIT_MS: f64 = 130.0;
/// Size of a warning arrow against the circle radius.
const WARNING_SIZE: f64 = 0.8;
/// Width of the stroke that rounds an arrow's corners, against its size. Half
/// of it sits outside the outline, so it is also how far the drawn shape
/// reaches past the geometry — which anything positioning an arrow by its tip
/// has to allow for.
const ARROW_ROUNDING: f32 = 0.22;
/// The rows they sit on, near the top and bottom of the field.
const WARNING_ROWS: [f64; 2] = [42.0, 342.0];
/// Resting brightness, and how much a beat adds on top.
const WARNING_REST: f32 = 0.42;
const WARNING_BEAT: f32 = 0.58;
/// How much bigger a beat makes them. Small: this is a pulse, not a bounce.
const WARNING_SWELL: f32 = 0.10;
/// A short entry so they do not simply appear.
const WARNING_ENTRY_MS: f64 = 150.0;

/// The spinner: where its ring starts, and the centre it closes onto.
///
/// The dot is drawn as a bright core inside a ring, after an icon by Radhe Icon
/// on Flaticon. On the game's near-black field the two tones are the other way
/// round from the drawing — there the ring is the dark part against white, here
/// it is the core that has to carry the light.
const SPINNER_RADIUS: f64 = 180.0;
const SPINNER_CORE: f64 = 12.0;
const SPINNER_DOT: f64 = 20.0;

/// A refused click shakes the note: how wide, how fast, and for how long.
///
/// Sideways only, and small — the note has to stay where the player is aiming
/// while it says "not yet". A wobble large enough to move the target would
/// punish them twice for the same mistake.
const SHAKE_MS: f64 = 120.0;

/// How long a verdict stays at the note it belongs to.
///
/// A receipt, not a caption — and on a stream at 200bpm the next note is due
/// in 75ms, so anything slower stacks up into a wall of old news.
const VERDICT_MS: f64 = 240.0;

/// How much larger a verdict starts than it ends.
///
/// It collapses into itself rather than drifting off: a mark that moves pulls
/// the eye away from the playfield, and the eye should stay where the cursor
/// is. Shrinking in place reads as *something happened here* and then gets out
/// of the way.
const VERDICT_SHRINK: f32 = 1.45;

/// How long the interface takes to get out of the way at a break, and to come
/// back before the next note.
const BREAK_HUD_FADE_MS: f64 = 400.0;

/// How long a combo pulse lasts, and how far it swells.
///
/// Two sizes: a small kick every time the counter goes up, and a larger one
/// when a run ends. The second has to be visible out of the corner of an eye —
/// a break is the only thing the counter ever has to *announce*.
const COMBO_PULSE_MS: f64 = 110.0;
const COMBO_PULSE_GAIN: f32 = 0.07;
const COMBO_BREAK_PULSE_MS: f64 = 260.0;
const COMBO_BREAK_PULSE_GAIN: f32 = 0.26;

/// How long a failed play takes to dim out, in map milliseconds.
const FAIL_FADE_MS: f64 = 1100.0;

/// The error bar's half-width, in multiples of the fifty window.
const ERROR_BAR_SPAN: f64 = 1.0;

/// How many recent hits the error bar shows.
const ERROR_BAR_TICKS: usize = 28;
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
    /// The verdict itself, for the flash that marks it. `None` when there is
    /// no replay and so nothing was judged.
    verdict: Option<Judgement>,
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
    /// Every moment the combo counter changed, and whether it was a break.
    ///
    /// Worked out once: finding it per frame means walking the event list on
    /// every one of a hundred thousand frames to answer a question whose
    /// answer never changes.
    combo_changes: Vec<(f64, bool)>,
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

            // A play that ended early never reached the notes past its end.
            // The judge has verdicts for them — it walks the whole map — but
            // they are nobody's, so those notes resolve the way they do on a
            // map with no replay behind it rather than as the player's misses.
            let reached = index < state.objects_played();
            let judged = state.judge().filter(|_| reached).and_then(|judge| {
                judge
                    .events_for(index)
                    .find(|e| e.part.counts_for_accuracy())
                    .map(|e| (e.time_ms, e.result == Judgement::Miss))
            });
            let verdict = state.judge().filter(|_| reached).and_then(|judge| {
                judge
                    .events_for(index)
                    .find(|e| e.part.counts_for_accuracy())
                    .map(|e| e.result)
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
            let head = state.judge().filter(|_| reached).and_then(|judge| {
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
                verdict,
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

        // Every instant the counter moved, with a flag for the ones that took
        // it to zero. `combo_after` is what each event left behind, so a drop
        // is a break and a rise is a hit.
        let mut combo_changes = Vec::new();
        if let Some(judge) = state.judge() {
            let mut previous = 0u32;
            for event in judge.events() {
                if event.combo_after != previous {
                    combo_changes.push((event.time_ms, event.combo_after < previous));
                    previous = event.combo_after;
                }
            }
        }

        Self {
            state,
            skin,
            annotations,
            longest_life_ms,
            combo_changes,
        }
    }

    /// How much the combo counter is swelling at `time_ms`, as a multiplier.
    ///
    /// One kick per hit and a bigger one per break, decaying quickly. The
    /// counter is the only number on screen that a viewer watches continuously,
    /// and a number that never moves stops being watched.
    fn combo_pulse(&self, time_ms: f64) -> f32 {
        let i = self.combo_changes.partition_point(|(at, _)| *at <= time_ms);
        if i == 0 {
            return 1.0;
        }
        let (at, broke) = self.combo_changes[i - 1];
        let (span, gain) = if broke {
            (COMBO_BREAK_PULSE_MS, COMBO_BREAK_PULSE_GAIN)
        } else {
            (COMBO_PULSE_MS, COMBO_PULSE_GAIN)
        };
        let age = time_ms - at;
        if age < 0.0 || age >= span {
            return 1.0;
        }
        // Out fast, back slowly: a linear return reads as a wobble rather than
        // a beat.
        let progress = (age / span) as f32;
        1.0 + gain * (1.0 - progress).powf(2.2)
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
        self.draw_verdicts(pixmap, time_ms, layout);
        self.draw_break_warning(pixmap, time_ms, layout);
        self.draw_cursor(pixmap, time_ms, layout);
        self.draw_hud(pixmap, time_ms, layout);
        self.draw_fail_fade(pixmap, time_ms, layout);
    }

    /// A failed play dims out rather than stopping mid-frame.
    ///
    /// The render already ends where the play did; without this it ends on a
    /// hard cut, which reads as the file having been trimmed rather than as
    /// the run having ended. Paired with the slow-down in `video.rs`, the last
    /// second becomes the play giving out.
    ///
    /// Only for a play that actually failed — a run that saw the map out
    /// finishes on its last note, and fading that would be inventing a defeat.
    fn draw_fail_fade(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(end) = self.state.ending() else {
            return;
        };
        let from = end.time_ms - FAIL_FADE_MS;
        if time_ms <= from {
            return;
        }
        let progress = ((time_ms - from) / FAIL_FADE_MS).clamp(0.0, 1.0) as f32;
        // Quadratic, so most of the darkening happens at the very end and the
        // play stays legible until it is over.
        let alpha = progress * progress * 0.92;
        let mut paint = Paint::default();
        paint.set_color(with_alpha(self.skin.background, alpha));
        paint.anti_alias = false;
        if let Some(rect) = Rect::from_xywh(0.0, 0.0, layout.width as f32, layout.height as f32) {
            pixmap.fill_rect(rect, &paint, Transform::identity(), None);
        }
    }

    /// How far into the current beat we are, as a kick that decays across it.
    ///
    /// Zero when the map states no timing at all, which leaves anything built
    /// on it sitting still rather than guessing at a tempo.
    fn beat_kick(&self, time_ms: f64) -> f32 {
        let Some(point) = self.state.timeline().timing.timing_point_at(time_ms) else {
            return 0.0;
        };
        if point.beat_length <= 0.0 {
            return 0.0;
        }
        let phase = ((time_ms - point.time_ms) / point.beat_length).rem_euclid(1.0) as f32;
        (1.0 - phase) * (1.0 - phase)
    }

    /// Arrows down both sides while a break is running out.
    ///
    /// Drawn under the cursor and over the field: they are a message to the
    /// player, not part of the map, and nothing about the play should be
    /// hidden behind them.
    /// The verdict each note earned, flashed where the note was.
    ///
    /// osu! does this with a sprite per judgement; here it is the score itself
    /// in the skin's own colours, rising a little and fading out. It answers
    /// the question a viewer actually has watching a replay — *what did that
    /// one give?* — which the combo counter only answers when it breaks.
    ///
    /// A 300 is deliberately the quietest of the four. A clean play should not
    /// be covered in confirmations of its own cleanliness; the eye should be
    /// drawn to the note that went wrong.
    fn draw_verdicts(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(font) = &self.skin.font else {
            return;
        };
        let radius = self.state.difficulty().circle_radius();

        for index in self.candidates(time_ms) {
            let annotation = &self.annotations[index];
            let Some(verdict) = annotation.verdict else {
                continue;
            };
            let age = time_ms - annotation.resolved_ms;
            if !(0.0..VERDICT_MS).contains(&age) {
                continue;
            }
            if verdict == Judgement::Great && !self.skin.show_300 {
                continue;
            }
            let progress = (age / VERDICT_MS) as f32;
            // Out quickly at first, then linger: the flash is read in its
            // first fifty milliseconds and the rest is it leaving.
            let alpha = (1.0 - progress).powf(0.6);
            let (text, colour, scale) = match verdict {
                Judgement::Great => ("300", self.skin.verdict_300, 0.42),
                Judgement::Ok => ("100", self.skin.verdict_100, 0.42),
                Judgement::Meh => ("50", self.skin.verdict_50, 0.46),
                // The miss stays the largest of the four: it is the thing the
                // viewer is here to see.
                Judgement::Miss => ("×", self.skin.verdict_miss, 0.85),
            };
            // Still stepped, but far less: the colours already separate them,
            // so this only keeps a wall of 300s from shouting on the classic
            // skin.
            let presence = match verdict {
                Judgement::Great => 0.70,
                Judgement::Ok => 0.85,
                Judgement::Meh => 0.92,
                Judgement::Miss => 1.0,
            };

            let object = &self.state.timeline().objects[index];
            let at = layout.map(object.pos);
            // Collapsing: it arrives oversized and settles onto the note. A
            // miss collapses less, so it is still legible when it goes.
            let settle = if verdict == Judgement::Miss {
                1.0 + (VERDICT_SHRINK - 1.0) * 0.4 * (1.0 - progress)
            } else {
                1.0 + (VERDICT_SHRINK - 1.0) * (1.0 - progress)
            };
            let size = layout.length(radius * scale) * settle;
            font.draw(
                pixmap,
                Label {
                    text,
                    x: at.0,
                    y: at.1 + size * 0.35,
                    size,
                    colour: with_alpha(colour, alpha * presence),
                    align: Align::Centre,
                },
            );
        }
    }

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
            let left = ends - time_ms;
            if left > WARNING_MS {
                return;
            }
            // Full strength across the window, with only a short entry so they
            // do not simply appear. Dimming most of the window made the cue
            // arrive late — the window itself is the warning.
            let entering = ((WARNING_MS - left) / WARNING_ENTRY_MS).clamp(0.0, 1.0) as f32;
            let kick = self.beat_kick(time_ms);
            // Never fully dark between beats: an arrow that disappears reads as
            // a rendering fault rather than as a signal.
            (
                (WARNING_REST + WARNING_BEAT * kick) * entering,
                1.0 + WARNING_SWELL * kick,
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

        // Placed so the tip just touches the field's edge, which puts the body
        // of the arrow wholly outside it. Derived from the arrow's own size
        // rather than fixed: the size follows the circle radius, so a constant
        // inset would have them overlapping the field on a small-circle map and
        // floating away from it on a large-circle one.
        let arrow = self.state.difficulty().circle_radius() * WARNING_SIZE;
        // The tip of the *drawn* shape, not of the geometry: the rounding
        // stroke reaches half its width past the outline, and an arrow placed
        // without allowing for that pokes into the field.
        let reach = arrow * (1.0 + f64::from(ARROW_ROUNDING) / 2.0);
        let size = layout.length(arrow) * scale;
        for y in WARNING_ROWS {
            for (x, dir) in [
                (-reach, (1.0, 0.0)),
                (dossier_beatmap::PLAYFIELD_WIDTH + reach, (-1.0, 0.0)),
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
        // A break thins the interface rather than clearing it. The timeline,
        // the accuracy and the combo stay — a viewer still wants to know where
        // they are and how the play stands — while the health bar and the
        // error meter go, because neither says anything while nobody is
        // playing.
        let presence = self.hud_presence(time_ms);
        let score = judge.state_at(time_ms);
        let height = f64::from(layout.height);
        let margin = (height * 0.03) as f32;

        // The score sits above the accuracy and is drawn larger, because it
        // is the number the play is finally judged on. Which arithmetic it is
        // follows the client that recorded the replay: stable's climbs into
        // the hundreds of millions on a long map, lazer's is capped at a
        // million on every map. Grouping the digits is not decoration — nine
        // unbroken figures cannot be read at a glance in motion.
        let score_size = (height * 0.058) as f32;
        let accuracy_size = (height * 0.045) as f32;
        let mut top = margin;
        if let Some(value) = self.state.score_at(time_ms) {
            font.draw(
                pixmap,
                Label {
                    text: &grouped(value),
                    x: layout.width as f32 - margin,
                    y: top + score_size,
                    size: score_size,
                    colour: self.skin.hud,
                    align: Align::Right,
                },
            );
            top += score_size * 1.15;
        }
        font.draw(
            pixmap,
            Label {
                text: &format!("{:.2}%", score.accuracy()),
                x: layout.width as f32 - margin,
                y: top + accuracy_size,
                size: accuracy_size,
                colour: self.skin.hud,
                align: Align::Right,
            },
        );

        // Bigger than the accuracy, and pulsing: it is the number a viewer
        // actually follows.
        let combo_size = (height * 0.085) as f32 * self.combo_pulse(time_ms);
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

        // The tally, under the accuracy and in the verdict colours. A viewer
        // watching a replay wants the shape of the play, and "two hundreds and
        // a miss" is a different play from "three hundreds" at the same
        // percentage.
        let tally_size = (height * 0.028) as f32;
        let counts = score.counts;
        let tally = [
            (u32::from(counts.count_300), self.skin.verdict_300),
            (u32::from(counts.count_100), self.skin.verdict_100),
            (u32::from(counts.count_50), self.skin.verdict_50),
            (u32::from(counts.count_miss), self.skin.verdict_miss),
        ];
        // Laid out right to left from the same margin as the accuracy, so the
        // two line up however wide the numbers get.
        let mut x = layout.width as f32 - margin;
        for (value, colour) in tally.iter().rev() {
            let text = format!("{value}");
            font.draw(
                pixmap,
                Label {
                    text: &text,
                    x,
                    y: top + accuracy_size + tally_size * 1.5,
                    size: tally_size,
                    colour: with_alpha(*colour, presence),
                    align: Align::Right,
                },
            );
            x -= font.width(&text, tally_size) + tally_size * 0.9;
        }

        // Always on: they orient rather than report.
        self.draw_progress(pixmap, time_ms, layout, 1.0);
        self.draw_health(pixmap, time_ms, layout, presence);
        self.draw_error_bar(pixmap, time_ms, layout, presence);
    }

    /// How present the interface should be: one during play, nothing in the
    /// middle of a break, easing across the edges.
    fn hud_presence(&self, time_ms: f64) -> f32 {
        let mut presence = 1.0f32;
        for &(from, to) in &self.state.timeline().breaks {
            if to - from < BREAK_HUD_FADE_MS * 2.0 {
                continue;
            }
            if time_ms < from || time_ms > to {
                continue;
            }
            let into = ((time_ms - from) / BREAK_HUD_FADE_MS).clamp(0.0, 1.0) as f32;
            let out_of = ((to - time_ms) / BREAK_HUD_FADE_MS).clamp(0.0, 1.0) as f32;
            presence = presence.min(1.0 - into.min(out_of));
        }
        presence
    }

    /// The three bars: health at the very top, progress under it, and the
    /// hit-error meter at the foot of the screen.
    ///
    /// All of them are thin and quiet. A replay render is watched for the
    /// play, and an interface that competes with it has failed — these are
    /// there to be glanced at, not read.
    fn draw_bar(
        &self,
        pixmap: &mut Pixmap,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
        colour: Color,
    ) {
        // Every guard here has earned its place: a NaN slips past `<= 0.0`
        // and panics deep inside the rasteriser, where the message says
        // nothing about which bar was at fault.
        if !(width.is_finite() && height.is_finite() && x.is_finite() && y.is_finite()) {
            return;
        }
        if width <= 0.0 || height <= 0.0 {
            return;
        }
        // Clip to the canvas ourselves. A rect running off the bottom edge is
        // legal arithmetic and an assertion failure three crates down, and the
        // panic names a rasteriser scanline rather than the bar that caused it.
        let (max_x, max_y) = (pixmap.width() as f32, pixmap.height() as f32);
        let (x0, y0) = (x.max(0.0), y.max(0.0));
        let (x1, y1) = ((x + width).min(max_x), (y + height).min(max_y));
        let (width, height) = (x1 - x0, y1 - y0);
        let (x, y) = (x0, y0);
        if width <= 0.0 || height <= 0.0 {
            return;
        }
        // Rounded out to whole pixels, and drawn without anti-aliasing. A
        // sub-pixel rect asks tiny-skia for an anti-aliased hairline, which is
        // both slower and, at these sizes, an assertion failure. Bars are
        // axis-aligned; there is nothing for AA to smooth.
        let width = width.max(1.0).round();
        let height = height.max(1.0).round();
        let mut paint = Paint::default();
        paint.set_color(colour);
        paint.anti_alias = false;
        if let Some(rect) = Rect::from_xywh(x.round(), y.round(), width, height) {
            pixmap.fill_rect(rect, &paint, Transform::identity(), None);
        }
    }

    /// A rounded bar, which is what everything in the interface is made of.
    fn draw_pill(
        &self,
        pixmap: &mut Pixmap,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
        colour: Color,
    ) {
        if !(x.is_finite() && y.is_finite() && width.is_finite() && height.is_finite()) {
            return;
        }
        if width <= 0.0 || height <= 0.0 {
            return;
        }
        let r = (height * 0.5).min(width * 0.5);
        let mut path = PathBuilder::new();
        path.move_to(x + r, y);
        path.line_to(x + width - r, y);
        path.quad_to(x + width, y, x + width, y + r);
        path.line_to(x + width, y + height - r);
        path.quad_to(x + width, y + height, x + width - r, y + height);
        path.line_to(x + r, y + height);
        path.quad_to(x, y + height, x, y + height - r);
        path.line_to(x, y + r);
        path.quad_to(x, y, x + r, y);
        path.close();
        let Some(path) = path.finish() else {
            return;
        };
        let mut paint = Paint::default();
        paint.set_color(colour);
        paint.anti_alias = true;
        pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
    }

    /// Where the two bars live: a centred strip, inset from the edges.
    ///
    /// Full-width bars pinned to the very top read as a browser's loading
    /// indicator — they belong to the window rather than to the play. Pulled
    /// in and given room, they become part of the piece.
    fn strip(&self, layout: &Layout) -> (f32, f32, f32) {
        let width = layout.width as f32;
        let inset = width * 0.16;
        (inset, width - inset * 2.0, (layout.height as f32) * 0.028)
    }

    /// The timeline: how far in, where the breaks are, and where we are now.
    ///
    /// The breaks are the point. A viewer dropping into a render cannot tell a
    /// map that has been relentless for ninety seconds from one that just had
    /// a rest, and the timeline is the only place that can say so without
    /// taking up room.
    fn draw_progress(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let (from, to) = self.state.span_ms();
        if to <= from {
            return;
        }
        let (x, width, y) = self.strip(layout);
        let height = (f64::from(layout.height) * 0.0075).max(3.0) as f32;
        let at = |ms: f64| x + width * (((ms - from) / (to - from)).clamp(0.0, 1.0) as f32);

        self.draw_pill(
            pixmap,
            x,
            y,
            width,
            height,
            with_alpha(self.skin.hud, 0.14 * presence),
        );
        // Breaks, marked on the track itself before the fill goes over them.
        for &(bf, bt) in &self.state.timeline().breaks {
            let (bx, bw) = (at(bf), at(bt) - at(bf));
            self.draw_pill(
                pixmap,
                bx,
                y,
                bw,
                height,
                with_alpha(self.skin.hud, 0.30 * presence),
            );
        }
        let played = at(time_ms) - x;
        self.draw_pill(
            pixmap,
            x,
            y,
            played,
            height,
            with_alpha(self.skin.hud, 0.62 * presence),
        );
        // The head: a dot riding the line, the only part that moves.
        let dot = height * 2.2;
        self.draw_pill(
            pixmap,
            x + played - dot * 0.5,
            y + height * 0.5 - dot * 0.5,
            dot,
            dot,
            with_alpha(self.skin.hud, 0.95 * presence),
        );
    }

    /// Health, as a thick bar in the top-left.
    ///
    /// Given weight and its own corner rather than tucked under the timeline:
    /// it is the one reading that decides whether the play survives, and on a
    /// failed run it is the thing the viewer watches. Everything else on
    /// screen is a record of what happened; this is the only part that says
    /// what is *about* to.
    fn draw_health(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let Some(health) = self.state.health_at(time_ms) else {
            return;
        };
        let height = f64::from(layout.height);
        let margin = (height * 0.03) as f32;
        let width = layout.width as f32 * 0.26;
        let thickness = (height * 0.022).max(6.0) as f32;
        // Clear of the timeline, which runs across the top of the frame — at
        // the old height the two overlapped whenever the strip was wide.
        let y = margin + (height * 0.045) as f32;

        self.draw_pill(
            pixmap,
            margin,
            y,
            width,
            thickness,
            with_alpha(self.skin.hud, 0.13 * presence),
        );
        // Below a third it turns the miss colour: a play about to end should
        // say so before it does.
        let (colour, alpha) = if health < 0.33 {
            (self.skin.verdict_miss, 0.95)
        } else {
            (self.skin.hud, 0.62)
        };
        self.draw_pill(
            pixmap,
            margin,
            y,
            width * health,
            thickness,
            with_alpha(colour, alpha * presence),
        );
    }

    /// Recent timing errors, as osu!'s hit-error bar.
    ///
    /// A tick per recent hit, placed by how early or late it was, over three
    /// bands standing for the 300, 100 and 50 windows. It is the one part of
    /// the interface that says *how* a player is playing rather than how well:
    /// a cloud sitting left of centre is somebody rushing, and no total shows
    /// that.
    fn draw_error_bar(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let Some(judge) = self.state.judge() else {
            return;
        };
        let difficulty = self.state.difficulty();
        let (w300, w100, w50) = (
            difficulty.hit_window_300(),
            difficulty.hit_window_100(),
            difficulty.hit_window_50(),
        );
        if w50 <= 0.0 {
            return;
        }

        let height = f64::from(layout.height);
        let full_width = (layout.width as f64 * 0.22) as f32;
        let centre_x = layout.width as f32 * 0.5;
        let y = (height * 0.955) as f32;
        let band = (height * 0.006).max(2.0) as f32;
        let span = w50 * ERROR_BAR_SPAN;
        let half = |window: f64| (window / span) as f32 * full_width * 0.5;

        // The windows themselves, widest first so the narrow ones sit on top.
        for (window, colour) in [
            (w50, self.skin.verdict_50),
            (w100, self.skin.verdict_100),
            (w300, self.skin.verdict_300),
        ] {
            let w = half(window);
            self.draw_bar(
                pixmap,
                centre_x - w,
                y,
                w * 2.0,
                band,
                with_alpha(colour, 0.30 * presence),
            );
        }

        // The last few hits, the most recent brightest.
        let mut recent: Vec<(f64, f64)> = judge
            .errors_ms()
            .filter(|&(at, _)| at <= time_ms)
            .collect();
        // Most recent first, so the brightest tick is the newest.
        recent.reverse();
        recent.truncate(ERROR_BAR_TICKS);
        let tick_w = (height * 0.0035).max(1.0) as f32;
        for (i, (_, error)) in recent.iter().enumerate() {
            let age = i as f32 / ERROR_BAR_TICKS as f32;
            let offset = (*error / span).clamp(-1.0, 1.0) as f32 * full_width * 0.5;
            let colour = if error.abs() < w300 {
                self.skin.verdict_300
            } else if error.abs() < w100 {
                self.skin.verdict_100
            } else {
                self.skin.verdict_50
            };
            self.draw_bar(
                pixmap,
                centre_x + offset - tick_w * 0.5,
                y - band * 1.6,
                tick_w,
                band * 4.2,
                with_alpha(colour, (1.0 - age) * 0.9 * presence),
            );
        }

        // Dead centre, so early and late read at a glance.
        self.draw_bar(
            pixmap,
            centre_x - tick_w * 0.5,
            y - band * 2.4,
            tick_w,
            band * 5.8,
            with_alpha(self.skin.hud, 0.75 * presence),
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
            // Each turn with the moment it becomes the next one at this end:
            // the start of the slide that ends on it.
            let turns = (1..*slides)
                .filter(|k| k.is_multiple_of(2) != at_tail)
                .map(|k| {
                    (
                        object.start_ms + f64::from(k) * slide_duration_ms,
                        object.start_ms + f64::from(k - 1) * slide_duration_ms,
                    )
                });

            let turns: Vec<(f64, f64)> = turns.collect();
            // Read from when the ball sets off, not from now, so the first
            // turn's arrow is up while the slider is still approaching: a
            // player has to know a slider comes back before they start it.
            let (leaving, pulse) =
                arrow_life(&turns, time_ms, time_ms.max(object.start_ms), object.start_ms);
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
                width: size * ARROW_ROUNDING,
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
        // time left rather than progress made. It closes onto the centre dot
        // rather than onto empty space: a ring shrinking towards nothing says
        // only that it is shrinking, while one arriving at a mark says how far
        // it still has to go.
        let progress =
            ((time_ms - object.start_ms) / object.duration_ms().max(1.0)).clamp(0.0, 1.0);
        let closing = SPINNER_RADIUS + (SPINNER_DOT - SPINNER_RADIUS) * progress;
        self.ring(
            pixmap,
            Point::CENTRE,
            layout.length(closing),
            layout.length(4.0),
            self.skin.spinner,
            alpha,
            layout,
        );

        // The mark at the middle: a ring with a lit core inside it, drawn after
        // the closing ring so nothing crosses it at the end.
        let band = SPINNER_DOT - SPINNER_CORE;
        self.ring(
            pixmap,
            Point::CENTRE,
            layout.length(SPINNER_DOT - band / 2.0),
            layout.length(band),
            self.skin.spinner,
            alpha,
            layout,
        );
        self.dot(
            pixmap,
            Point::CENTRE,
            layout.length(SPINNER_CORE),
            lighten(self.skin.spinner, 0.55),
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
/// `turns` is every moment the ball turns around at *that* end, and `span_ms`
/// is how long one traversal takes. The arrow is full while a turn is coming
/// within one traversal — arriving as the ball sets off towards it, the way
/// lazer brings a repeat in — then goes out over its own window rather than
/// blinking off on the frame the ball touches it. Landing gives it a kick,
/// which is the cue that the direction just changed; it decays quadratically so
/// the kick is over well before the fade is.
///
/// Both ends can therefore be lit at once, which is the point: at a turn the
/// arrow just struck is still fading while the far end's is already up.
///
/// Split out from the drawing because it cannot be measured through pixels:
/// the ball and the ticks pass through the same few square pixels at exactly
/// the moment in question, and there is no telling their brightness from the
/// arrow's.
fn arrow_life(
    turns: &[(f64, f64)],
    time_ms: f64,
    reading_ms: f64,
    started_ms: f64,
) -> (f32, f32) {
    // A turn is due once the ball is on the slide that ends at it — `due` is
    // when that slide begins. Stated as a moment rather than as "within one
    // traversal", because the two are the same in arithmetic and not in
    // floating point: `start + span - start` comes out an ulp above `span`, so
    // the comparison failed at exactly the boundary and the first turn's arrow
    // stayed dark for the whole approach.
    let ahead = turns
        .iter()
        .any(|&(at, due)| at > time_ms && reading_ms >= due);
    let behind = turns
        .iter()
        .map(|&(at, _)| at)
        .filter(|&at| at <= time_ms)
        .fold(None::<f64>, |best, at| {
            Some(best.map_or(at, |b: f64| b.max(at)))
        });

    // How far into its arrival the next turn's arrow is.
    //
    // Only for an arrow that becomes due *during* the slide. The first one is
    // due before the slider has even started and arrives with the body as it
    // snakes out, which is its animation; giving it a second one would fade it
    // in over a slider that is already there. A later arrow had none at all
    // and snapped on at full brightness, which reads as a second slider
    // materialising out of nothing.
    let arriving = turns
        .iter()
        .filter(|&&(at, due)| at > time_ms && reading_ms >= due)
        .map(|&(_, due)| {
            if due <= started_ms {
                1.0
            } else {
                ((reading_ms - due) / ARROW_FADE_MS).clamp(0.0, 1.0) as f32
            }
        })
        .fold(0.0f32, f32::max);

    let leaving = match (ahead, behind) {
        (true, _) => arriving,
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

    /// One traversal, for the tests that care how far ahead a turn is.
    const SPAN: f64 = 2000.0;

    /// A turn at `at`, due from one traversal before it.
    fn turn(at: f64) -> (f64, f64) {
        (at, at - SPAN)
    }

    #[test]
    fn an_arrow_waits_until_the_ball_sets_off_towards_it() {
        // The end of a slider is where its head circle sits, so an arrow that
        // stands from the start sits underneath the note for the whole first
        // slide. It is due when the slide that ends on it begins.
        let turns = [turn(5000.0)];
        assert_eq!(
            arrow_life(&turns, 2000.0, 2000.0, 0.0).0,
            0.0,
            "two traversals out, nothing there yet"
        );
        // Exactly on the boundary — which is the case that broke. Written as
        // `at - now <= span` this failed, because `start + span - start` comes
        // out an ulp above `span` and the arrow stayed dark all approach.
        //
        // The arrow now *starts* arriving here rather than snapping on: a
        // later turn becomes due mid-slide, and appearing at full brightness
        // reads as a second slider materialising out of nothing.
        assert_eq!(
            arrow_life(&turns, 3000.0, 3000.0, 2500.0).0,
            0.0,
            "one traversal out, to the millisecond: it begins arriving"
        );
        let midway = arrow_life(&turns, 3000.0 + ARROW_FADE_MS * 0.5, 3000.0 + ARROW_FADE_MS * 0.5, 2500.0).0;
        assert!(
            (0.3..0.7).contains(&midway),
            "halfway through arriving: {midway}"
        );
        assert_eq!(
            arrow_life(&turns, 3000.0 + ARROW_FADE_MS, 3000.0 + ARROW_FADE_MS, 2500.0).0,
            1.0,
            "and fully there once its fade is done"
        );
    }

    #[test]
    fn an_arrow_holds_while_a_turn_is_coming_and_then_goes_out() {
        let turns = [turn(1000.0), turn(3000.0)];
        assert_eq!(arrow_life(&turns, 500.0, 500.0, 0.0).0, 1.0, "before the first");
        assert_eq!(
            arrow_life(&turns, 2500.0, 2500.0, 0.0).0,
            1.0,
            "another is still coming, and has finished arriving"
        );

        // After the last one it decays rather than blinking off.
        let half = arrow_life(&turns, 3000.0 + ARROW_FADE_MS / 2.0, 3000.0 + ARROW_FADE_MS / 2.0, 0.0)
        .0;
        assert!(half > 0.0 && half < 1.0, "{half}");
        assert_eq!(
            arrow_life(&turns, 3000.0 + ARROW_FADE_MS, 3000.0 + ARROW_FADE_MS, 2500.0).0,
            0.0,
            "and is gone"
        );
    }

    #[test]
    fn landing_kicks_the_arrow_and_the_kick_settles_first() {
        let turns = [turn(1000.0)];
        assert_eq!(
            arrow_life(&turns, 999.0, 999.0, 0.0).1,
            0.0,
            "nothing has struck it yet"
        );

        let struck = arrow_life(&turns, 1000.0, 1000.0, 0.0).1;
        assert!(
            (struck - ARROW_PULSE).abs() < 1e-6,
            "full kick on landing: {struck}"
        );

        // Quadratic decay, so the kick is over before the fade is.
        let later = arrow_life(&turns, 1000.0 + ARROW_PULSE_MS / 2.0, 1000.0 + ARROW_PULSE_MS / 2.0, 0.0)
        .1;
        assert!(later < struck / 2.0, "{later} against {struck}");
        assert_eq!(
            arrow_life(&turns, 1000.0 + ARROW_PULSE_MS, 1000.0 + ARROW_PULSE_MS, 0.0).1,
            0.0
        );
    }

    #[test]
    fn an_end_that_never_turns_shows_nothing() {
        assert_eq!(arrow_life(&[], 1234.0, 1234.0, 0.0), (0.0, 0.0));
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

/// A number with its thousands separated.
///
/// Nine unbroken digits are unreadable at a glance, and a viewer glancing is
/// the only kind there is in a video. A space rather than a comma or a full
/// stop because the audience is not all in one country and both of those mean
/// the decimal point somewhere — and an ordinary space rather than the thin
/// one typography would ask for, because a display face need not carry U+2009
/// and Torus does not: it drew a tofu box between every group.
fn grouped(value: u64) -> String {
    let digits = value.to_string();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i).is_multiple_of(3) {
            out.push(' ');
        }
        out.push(c);
    }
    out
}

#[cfg(test)]
mod grouping {
    use super::grouped;

    #[test]
    fn digits_group_in_threes_from_the_right() {
        assert_eq!(grouped(0), "0");
        assert_eq!(grouped(999), "999");
        assert_eq!(grouped(1_000), "1 000");
        assert_eq!(grouped(317_279_960), "317 279 960");
        // The leading group is whatever is left over, not padded to three.
        assert_eq!(grouped(12_345), "12 345");
    }
}
