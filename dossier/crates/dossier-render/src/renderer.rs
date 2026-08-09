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
use tiny_skia::{FillRule, Paint, Pixmap, Rect, Shader, Stroke, Transform};

use crate::layout::Layout;
use crate::skin::{blend, lighten, with_alpha, Skin};
use crate::text::{Align, Label};

mod format;

mod paint;
use paint::rounded_rect;

mod objects;
mod hud;
mod scoreboard;
mod overlay;

/// How long a judged note takes to leave.
///
/// Down from 220ms, which read as sluggish: on a dense map the note being taken
/// away was still on screen when the next two had arrived, so the playfield
/// always carried a layer of things that had already happened.
/// Hidden's two multipliers on preempt: the note arrives over four tenths of
/// it and is taken away again over the next three.
///
/// ```csharp
/// public const double FADE_IN_DURATION_MULTIPLIER = 0.4;
/// public const double FADE_OUT_DURATION_MULTIPLIER = 0.3;
/// ```
/// How much of the approach a slider's body takes to grow, as a share of it.
///
/// A third, which is danser's — see [`Scene::snake`] for the source and for the
/// two wrong answers this had before anybody read it.
const SNAKE_SHARE_OF_APPROACH: f64 = 1.0 / 3.0;

/// A slider tick fades in over this, and grows into place over four times it.
///
/// ```csharp
/// public const double ANIM_DURATION = 150;
/// this.FadeOut().FadeIn(ANIM_DURATION);
/// this.ScaleTo(0.5f).ScaleTo(1f, ANIM_DURATION * 4, Easing.OutElasticHalf);
/// ```
const TICK_FADE_MS: f64 = 150.0;
/// How much warning a tick gets on the way out, as a fraction of preempt…
const TICK_FIRST_LEAD: f64 = 0.66;
/// …and on every slide back, where the player has already seen the ticks once.
const TICK_REPEAT_LEAD_MS: f64 = 200.0;

const HIDDEN_FADE_IN: f64 = 0.4;
const HIDDEN_FADE_OUT: f64 = 0.3;

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
/// How far right of the centre the RPM reading sits, in playfield units — clear
/// of the centre mark and inside where the ring spends most of its time.
/// The scoreboard's sizes, as fractions of the frame's height.
///
/// Anchored to the frame rather than to the playfield, like the rest of the HUD.
/// A playfield-relative margin lands off-screen on a 4:3 render, where the field
/// is as wide as the frame and there is no left of it to be left of.
const BOARD_LEFT: f64 = 0.022;
/// Space between rows. Two lines of text each, so this is not the text size —
/// and it is sized *from* them: the card runs from 1.15 text-heights above the
/// first baseline to below the second's descender, which is about 2.55 of them.
/// Set from the text size instead and the second line hangs out of its own card,
/// which is what the first attempt did.
const BOARD_STEP: f64 = 0.067;
const BOARD_TEXT: f64 = 0.0245;
/// How wide the cards are, as a fraction of the frame's height.
///
/// Shortened twice and then let back out once. That was settled while the
/// playfield's edge was drawn on the frame, which made how much room the board
/// takes from the play a thing you could see rather than guess at. The outline
/// is gone — it was scaffolding, and this width is what it was for.
///
/// It began sized so a ScoreV1 total and an accuracy could sit at opposite ends
/// of one line, which made a panel a third of the frame wide for the sake of the
/// gap in the middle. The floor is the second line: eleven digits, an accuracy
/// and a mod acronym, shrunk to fit rather than allowed past the edge.
const BOARD_WIDTH: f64 = 0.262;
/// How much of a row's step the card fills, leaving the rest as the gap between
/// them. Enough to hold both lines — see [`BOARD_STEP`].
const BOARD_CARD_FILL: f32 = 0.92;
/// How solid a rival's card is, and the player's.
/// How many lines the board shows. Five: enough to be a standing, short enough
/// that the eye takes it in without reading, and short enough not to run into
/// the notes on a busy map.
const BOARD_ROWS: usize = 5;
/// Corner radius, as a share of a card's height.
const BOARD_RADIUS: f32 = 0.30;
/// Where the heavy dim gives way to the light one, across the card. The left is
/// where the avatar and the name are; the right has fewer words in it and can
/// afford to show more of the cover.
const BOARD_DARK_SPLIT: f32 = 0.46;
const BOARD_DARK_LEFT: f32 = 0.78;
const BOARD_DARK_RIGHT: f32 = 0.42;
/// The same over a cover, which can be any brightness at all — heavier, because
/// a profile cover includes white snow and a bright sky and the words have to
/// win over both.
const BOARD_DARK_LEFT_COVER: f32 = 0.84;
const BOARD_DARK_RIGHT_COVER: f32 = 0.58;
/// How much of the heavy end is still left at the knee. Below one this bends the
/// ramp so most of the letting-go happens after the words, rather than spreading
/// evenly and being too light where they start.
const BOARD_DARK_KNEE: f32 = 0.86;
/// The avatar's side, as a share of the card's height.
const BOARD_FACE: f32 = 0.72;
/// How much of the card's right end the place keeps to itself.
const BOARD_RANK_COLUMN: f32 = 0.62;
/// How small a row is at the moment it arrives, or the moment before it goes.
const BOARD_GROW: f32 = 0.55;
/// How far an ordinary place is lifted toward white. The podium three have
/// their own colours and do not need it.
const BOARD_RANK_LIFT: f32 = 0.35;
/// Its ring: how thick, and how far the glow reaches past it.
const BOARD_RING: f32 = 0.05;
const BOARD_GLOW: f32 = 0.06;
/// How far the card is lifted off the background before it is laid down.
const BOARD_CARD_LIFT: f32 = 0.16;
/// How far a rival's row is taken down from the player's.
const BOARD_RIVAL_DIM: f32 = 0.35;

/// How long the error bar takes to give the bottom of the frame over to the
/// spinner's speed, and to take it back.
const SPIN_SWAP_MS: f64 = 260.0;
/// The size of that readout, as a share of the frame's height.
const SPIN_READOUT_SIZE: f64 = 0.026;

/// How far below the centre the bonus total sits.
const SPINNER_BONUS_BELOW: f64 = 52.0;
const SPINNER_BONUS_SIZE: f64 = 38.0;
/// How much bigger it is at the instant an award lands.
const SPINNER_BONUS_SWELL: f32 = 0.45;
/// How long the pulse takes to settle back to grey.
const SPINNER_BONUS_PULSE_MS: f64 = 200.0;
/// How far the resting number is taken down toward grey between awards.
const SPINNER_BONUS_REST: f32 = 0.45;
/// What one award adds to the number on screen. osu! shows a thousand and pays
/// eleven hundred — see [`Scene::draw_spin_bonus`].
const SPINNER_BONUS_STEP: u32 = 1000;

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
/// The bar has to be under this before the edges say anything. A warning that
/// is always on is not a warning.
///
/// Taken from the simulator rather than restated here: Exhibit reads the same
/// number to decide a dip was worth showing, and a reel claiming the bar nearly
/// emptied over a frame with no warning on it would be the engine contradicting
/// itself in the same second.
use dossier_sim::DANGER_LEVEL as DANGER_FROM;
/// How red it gets at nothing left.
const DANGER_MAX: f32 = 0.85;
/// How far in from each edge, as a fraction of the frame's height.
const DANGER_REACH: f32 = 0.30;
/// Bands per edge. Enough that the steps do not show, few enough to be free.
const DANGER_BANDS: usize = 24;

/// lazer's fail animation, `FailAnimationContainer`:
///
/// ```csharp
/// private const float duration = 2500;
/// ```
pub const FAIL_ANIMATION_MS: f64 = 2500.0;

/// How much of the release the frame takes to go dark over.
///
/// All of it, on a curve that spends most of itself immediately. The frame
/// darkens *while* it springs back, not after: the two are one gesture, and
/// they end on the same frame.
///
/// This took two goes to get right and both wrong answers were about *where*
/// rather than how fast. It faded over a fifth of a second **after** the
/// movement first, which is a second smaller ending trailing the first. Then
/// it cut instantly at the same instant, which is a beat with nothing in it.
/// Neither was what the movement is: the release is the frame letting go, and
/// letting go is when the picture should leave.
///
/// Ending exactly with the animation matters as much as starting with it.
/// Faster than that and the frame is black before it has finished coming back,
/// so nobody sees it arrive — and the arrival is the thing.
const FAIL_CLEAR_OF_RELEASE: f32 = 1.0;

/// How long the empty frame is held after everything has gone.
///
/// A second of nothing is what turns "the picture stopped" into "the run
/// ended".
pub const FAIL_EMPTY_MS: f64 = 1000.0;
/// How far in the frame pulls before it is let go again.
const FAIL_SQUEEZE: f32 = 0.72;
/// When the release starts, as a fraction of the animation.
///
/// Late, so the frame is still closing while the music is still dying and the
/// two end together. The return then has a fifth of the animation to itself,
/// which at two and a half seconds is half a second — fast enough to read as
/// letting go.
const FAIL_RELEASE_AT: f32 = 0.80;
/// `redFlashLayer.FadeOutFromOne(1000)`, at `Color4.Red.Opacity(0.6f)`.
const FAIL_FLASH_MS: f64 = 1000.0;
/// Well under lazer's 0.6 — see [`Scene::compose_fail`]. Additive red over a
/// black field is not the same thing as additive red over a lit one.
const FAIL_FLASH_ALPHA: f32 = 0.30;

/// How long the play takes to come up at the start, in map milliseconds.
///
/// The first frame is the lead-in — before any note is on screen — so without
/// this the render opens on a hard cut to a lit but empty field, which reads as
/// the file starting mid-thought. Kept under the lead-in so it is finished
/// before the first note is approaching and never competes with one.
const INTRO_FADE_MS: f64 = 450.0;

/// And how long it takes to go at the end.
///
/// Longer than the opening. Arriving wants to be brisk — there is a play waiting
/// behind it — and leaving wants to be unhurried, because there is nothing
/// waiting behind that.
pub const OUTRO_FADE_MS: f64 = 700.0;


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

/// Cubic ease-out: fast away from zero, settling toward one.
///
/// The shape a key has. A press is an impact and its motion belongs at the
/// start; linear motion on something this small reads as a slide.
fn ease_out(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    1.0 - (1.0 - x).powi(3)
}

/// The two buttons the overlay draws a counter for.
///
/// osu! shows four — K1, K2, M1, M2 — and two of them are almost always zero.
/// Measured on a real replay: 719, 695, 41, 0. Two empty plates every frame is
/// two plates of nothing, and what the element is *for* is showing how the
/// player is holding the map, which the two live ones say on their own.
///
/// A press made with the mouse falls into the same box as the keyboard button
/// beside it rather than disappearing. The label is then not literally true for
/// a player who drags with the mouse — but "K1 0, K2 0" all game would be worse
/// than a label that is approximate, and it keeps every press counted once.
const KEY_NAMES: [&str; 2] = ["K1", "K2"];

/// When each button was held, and for how long.
///
/// A table built once rather than a walk per frame. A frame has to answer *how
/// many times has this been pressed by now*, which is a walk over thirty
/// thousand input samples asked of every one of a hundred thousand frames — and
/// the answer never changes, so it is a binary search over a table built at the
/// start.
///
/// That is not only about speed. Every frame must be drawable without its
/// predecessors or they cannot be drawn in parallel, and a counter that
/// incremented as the render walked forwards would be exactly the kind of state
/// that rules out.
///
/// The reading of the key bitmask — which is where the subtlety is — belongs to
/// [`dossier_sim::CursorTrack::holds`], because Exhibit reads the same presses
/// to find where the tapping is hardest and two copies of that rule would be
/// one copy and a future bug.
#[derive(Debug, Default)]
struct KeyTrack {
    /// `(pressed_at, released_at)` per button, in time order.
    holds: [Vec<(f64, f64)>; 2],
}

impl KeyTrack {
    /// How far down this button is at `time_ms`, from 0 to 1.
    ///
    /// Not the bare "is it held": a box that switched between two states on one
    /// frame flickered through a stream, because a tap is shorter than the gap
    /// between two frames at 60fps and half of them landed between samples.
    /// Eased, the same taps read as taps.
    ///
    /// Worked out from the press table alone, so it is still a function of the
    /// instant and nothing before it — an animation that accumulated frame by
    /// frame is exactly the state that would stop frames being drawn in
    /// parallel.
    ///
    /// A press shorter than the fall never reaches the bottom, and the release
    /// then starts from wherever it got to. Without that a fast stream would
    /// pump the box to full depth on every tap, which is louder than the tap.
    fn pressed(&self, key: usize, time_ms: f64, rate: f64) -> f32 {
        let (down_ms, up_ms) = (KEYS_PRESS_DOWN_MS * rate, KEYS_PRESS_UP_MS * rate);
        let holds = &self.holds[key];
        let index = holds.partition_point(|(from, _)| *from <= time_ms);
        let Some(&(down, up)) = index.checked_sub(1).and_then(|i| holds.get(i)) else {
            return 0.0;
        };
        let fell = |elapsed: f64, over: f64| ((elapsed / over.max(1e-6)).clamp(0.0, 1.0)) as f32;
        if time_ms < up {
            // Going down: quick, and easing out so it settles rather than stops.
            return ease_out(fell(time_ms - down, down_ms));
        }
        let reached = ease_out(fell(up - down, down_ms));
        reached * (1.0 - ease_out(fell(time_ms - up, up_ms)))
    }

    fn build(cursor: &dossier_sim::CursorTrack) -> Self {
        Self {
            holds: cursor.holds(),
        }
    }

    /// How many times this button had gone down by `time_ms`.
    fn count(&self, key: usize, time_ms: f64) -> usize {
        self.holds[key].partition_point(|(from, _)| *from <= time_ms)
    }
}

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
    /// Hidden, which is a rendering mod and nothing else: it changes what the
    /// player could see and not one thing about how the play was judged.
    hidden: bool,
    /// Which client recorded the play, and which build of it.
    signature: Option<Signature>,
    /// Who else has played this map. Empty unless somebody supplied it.
    leaderboard: crate::leaderboard::Leaderboard,
    /// Avatars and covers, decoded once rather than per frame.
    pictures: std::collections::HashMap<std::path::PathBuf, Pixmap>,
    /// When each of the two buttons was down.
    keys: KeyTrack,
    /// Draw the play and nothing that talks about it.
    bare: bool,
}

/// Where a replay came from, for the corner of the frame.
///
/// Worth showing because the two clients do not judge the same play the same
/// way, and a viewer comparing two renders has no other way to know which set
/// of rules produced what they are looking at. Worth showing *quietly*: it is
/// provenance, not gameplay, and it should be there when looked for and
/// invisible when not.
#[derive(Debug, Clone)]
pub struct Signature {
    /// The mods, run together the way osu! writes them: `HDDT`. Empty on a
    /// no-mod play, where a line saying so would be noise.
    pub mods: String,
    /// `stable`, `lazer`, or `lazer (classic)`.
    pub client: String,
    /// The build, as the client names itself. lazer knows its own version;
    /// stable's header carries a date stamp instead.
    pub version: String,
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
            hidden: state.mods().contains(dossier_replay::bits::HIDDEN),
            signature: None,
            leaderboard: crate::leaderboard::Leaderboard::default(),
            pictures: std::collections::HashMap::new(),
            keys: KeyTrack::build(state.cursor_track()),
            bare: false,
        }
    }

    /// Note in the corner which client recorded this and which build of it.
    pub fn signed_by(mut self, replay: &dossier_replay::Replay) -> Self {
        // lazer's own list when there is one: the legacy bitmask cannot say
        // Classic, and Classic changes how the play was judged.
        let lazer = replay.lazer_mods();
        let mods = if lazer.is_empty() {
            match replay.mods.to_string() {
                m if m == "NM" => String::new(),
                m => m,
            }
        } else {
            lazer.iter().map(|m| m.acronym.as_str()).collect()
        };
        self.signature = Some(Signature {
            mods,
            client: dossier_sim::Ruleset::of_replay(replay).name().to_owned(),
            version: replay.client_version(),
        });
        self
    }

    /// Set the rivals to stand the play against.
    ///
    /// Their pictures are decoded here, once. A row is drawn thousands of times
    /// over a render and reading a PNG off the disk for each of them would cost
    /// more than the frame does — and a decoder in the frame path is a decoder
    /// that can fail halfway through a video.
    #[must_use]
    /// Draw the play and nothing that talks about it.
    ///
    /// For a clip that has to stand next to somebody's own footage rather than
    /// explain itself: no score, no accuracy, no combo, no key counters, no
    /// scoreboard, no signature. What is left is the map and the cursor.
    ///
    /// The red closing in from the edges of a dying play stays, and that is the
    /// line this draws: a readout is *about* the play and comes off, while the
    /// screen reddening is the play itself and would be missed. The fail
    /// animation stays for the same reason.
    ///
    /// A leaderboard handed to a bare scene is loaded and then not drawn. That
    /// is the caller's business rather than an error — nothing about asking for
    /// one is wrong, and refusing the render over it would be.
    pub fn bare(mut self) -> Self {
        self.bare = true;
        self
    }

    pub fn with_leaderboard(mut self, board: crate::leaderboard::Leaderboard) -> Self {
        let mut wanted: Vec<std::path::PathBuf> = Vec::new();
        for entry in &board.rivals {
            wanted.extend(entry.avatar.clone());
            wanted.extend(entry.cover.clone());
        }
        wanted.extend(board.avatar.clone());
        wanted.extend(board.cover.clone());
        for path in wanted {
            if self.pictures.contains_key(&path) {
                continue;
            }
            match std::fs::read(&path).ok().and_then(|bytes| Pixmap::decode_png(&bytes).ok()) {
                Some(picture) => {
                    self.pictures.insert(path, picture);
                }
                None => eprintln!(
                    "dossier: {} could not be read as a PNG — the row will draw without it",
                    path.display()
                ),
            }
        }
        self.leaderboard = board;
        self
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
        // Once the bar has emptied the play is over and the clock is only
        // there to drive the animation. The field is drawn frozen at the
        // instant it stopped and then taken away.
        if let Some(progress) = self.fail_progress(time_ms) {
            // Past the movement the frame clears. Not a fade running underneath
            // the squeeze — that read as the render giving up rather than the
            // play ending — but a separate step after the release, which is a
            // frame that lets go and *then* empties.
            let clear = self.fail_clear(progress);
            if clear >= 1.0 {
                pixmap.fill(self.skin.background);
                return;
            }
            let frozen = self
                .state
                .ending()
                .map_or(time_ms, |end| end.time_ms.min(time_ms));
            // Two layers, because they do not leave together: lazer fades the
            // hit objects out over half the animation and leaves everything
            // else alone, tilting and greying the lot.
            let mut field = Pixmap::new(layout.width, layout.height)
                .expect("a frame with a zero dimension was requested");
            let mut overlay = Pixmap::new(layout.width, layout.height)
                .expect("a frame with a zero dimension was requested");
            self.draw_field(&mut field, frozen, layout);
            self.draw_overlay(&mut overlay, frozen, layout);
            // The curve in `fail_clear` is the whole of the speed; squaring it
            // here as well was a second opinion about the same thing.
            let presence = 1.0 - clear;
            self.compose_fail(pixmap, &field, &overlay, progress, presence, layout);
            return;
        }

        let intro = self.intro_presence(time_ms).min(self.outro_presence(time_ms));
        if intro < 1.0 {
            // A whole extra frame, but only for a third of a second at each end
            // — the alternative is threading an opacity through every draw call
            // in the scene for the sake of forty frames.
            let mut frame = Pixmap::new(layout.width, layout.height)
                .expect("a frame with a zero dimension was requested");
            self.draw_play(&mut frame, time_ms, layout);
            pixmap.fill(self.skin.background);
            let paint = tiny_skia::PixmapPaint {
                opacity: intro,
                quality: tiny_skia::FilterQuality::Nearest,
                ..Default::default()
            };
            pixmap.draw_pixmap(0, 0, frame.as_ref(), &paint, Transform::identity(), None);
            return;
        }
        self.draw_play(pixmap, time_ms, layout);
    }

    /// How far into the fail animation, if it has started.
    fn fail_progress(&self, time_ms: f64) -> Option<f32> {
        let end = self.state.ending()?;
        (time_ms > end.time_ms)
            .then(|| (((time_ms - end.time_ms) / FAIL_ANIMATION_MS).clamp(0.0, 1.0)) as f32)
    }

    /// How far into the clearing, which runs over the release.
    ///
    /// Nothing at all while the frame is still pulling in — the darkening is
    /// the *letting go*, and starting it earlier would be the picture leaving
    /// during the death rather than after it.
    fn fail_clear(&self, progress: f32) -> f32 {
        if progress <= FAIL_RELEASE_AT {
            return 0.0;
        }
        let released = (progress - FAIL_RELEASE_AT) / (1.0 - FAIL_RELEASE_AT);
        let t = (released / FAIL_CLEAR_OF_RELEASE).clamp(0.0, 1.0);
        // Cubic ease-out: most of the way gone in the first third of the
        // release, and the rest of it is the frame arriving on almost nothing.
        1.0 - (1.0 - t).powi(3)
    }

    /// How much of the play is up yet, at the opening.
    ///
    /// Squared, so it leaves black quickly and arrives at full gently — a
    /// linear ramp on a nearly black field spends most of its length looking
    /// like nothing is happening.
    fn intro_presence(&self, time_ms: f64) -> f32 {
        let (from, _) = self.state.span_ms();
        let t = ((time_ms - from) / INTRO_FADE_MS).clamp(0.0, 1.0) as f32;
        1.0 - (1.0 - t) * (1.0 - t)
    }

    /// How much of the play is still up, at the close.
    ///
    /// The mirror of the opening, and for the mirror of its reason: a render that
    /// ends on a hard cut reads as a file that was trimmed rather than as a run
    /// that finished. Squared the other way about, so it holds full brightness
    /// and then goes — a linear ramp spends its first half looking like nothing
    /// is happening, which at the end of a play is the half that matters.
    ///
    /// Only for a play that ran to the end. A failed one has its own ending —
    /// the frame closes in, springs back and clears — and fading that as well
    /// would be two endings on top of each other.
    fn outro_presence(&self, time_ms: f64) -> f32 {
        if self.state.ending().is_some() {
            return 1.0;
        }
        let (_, to) = self.state.span_ms();
        // *After* the last object, never over it. Fading the closing seven
        // hundred milliseconds of the span would dim the last notes of the map —
        // the part of a play people most want to see — so the render carries a
        // tail past the end and the fade lives in that.
        let t = (((time_ms - to) / OUTRO_FADE_MS).clamp(0.0, 1.0)) as f32;
        1.0 - t * t
    }

    /// Everything that is not the fail animation.
    fn draw_play(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        pixmap.fill(self.skin.background);
        self.draw_field(pixmap, time_ms, layout);
        self.draw_overlay(pixmap, time_ms, layout);
    }

    /// The playfield: what the player was aiming at, and where they were.
    fn draw_field(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {

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
    }

    /// The interface, which outlives the playfield when a play ends.
    fn draw_overlay(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        // The danger is on either side of the line: it is the screen reacting
        // rather than a readout about the play, so a bare scene keeps it.
        if self.bare {
            self.draw_danger(pixmap, time_ms, layout);
            return;
        }
        self.draw_hud(pixmap, time_ms, layout);
        self.draw_danger(pixmap, time_ms, layout);
        self.draw_keys(pixmap, time_ms, layout, self.hud_presence(time_ms));
        self.draw_leaderboard(pixmap, time_ms, layout);
        self.draw_signature(pixmap, layout);
    }

    /// Which client recorded this, in the bottom corner.
    ///
    /// Two lines: the client on top, larger, with the build tucked under it —
    /// both far enough into the background to read as a watermark. Drawn at a
    /// fixed
    /// opacity through breaks and fails alike — it says where the frame came
    /// from, which does not change while the play does.
    ///
    /// It earns its place because the two clients genuinely judge differently:
    /// the same replay rendered under the other one is a different play, and
    /// without this a viewer comparing two videos has no way to tell which
    /// rules produced which.
    /// Whether this play could have been lost at all.
    ///
    /// NoFail, and the bar and the warning both come off. The warning is the
    /// clearer case: red creeping in from the edges means *this is about to
    /// end*, and under NoFail it never was — a warning that cannot come true is
    /// worse than no warning, because a viewer who learns to discount it
    /// discounts the real one too.
    ///
    /// The bar goes with it, which is the deliberate part. It is not
    /// meaningless under NoFail — the drain still runs and the bar still moves —
    /// but everything it is *for* is gone. Its whole job on screen is to say how
    /// close the play is to being over, and on a play that cannot be over it
    /// reads as a threat that is not there.
    fn cannot_die(&self) -> bool {
        self.state.mods().contains(dossier_replay::bits::NO_FAIL)
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

/// How far in from the right edge the key column sits, as a share of the width.
const KEYS_INSET: f64 = 0.018;
/// One button's box, as a share of the frame height.
const KEYS_BOX: f64 = 0.052;
/// How much wider than tall a box is.
///
/// A tap counter reaches four figures on a marathon and the box has to hold
/// them without the type shrinking away — square, "1234" filled the plate edge
/// to edge and read as a smear at a glance.
const KEYS_WIDTH: f32 = 1.35;
/// Gap between boxes, as a share of one box.
const KEYS_GAP: f32 = 0.18;
/// How long a stretch of tapping the trail shows, in milliseconds of watching.
///
/// A second and a bit. Two seconds held more of the play and moved at a crawl:
/// the marks barely travelled between frames, which reads as a static pattern
/// rather than as tapping happening. Shorter is faster over the same reach, and
/// faster is what makes the trail look like an instrument.
///
/// The floor on it is legibility at speed: a 200 BPM stream is about thirteen
/// presses a second, so this window holds seventeen of them, which is a pattern
/// the eye can still resolve.
const KEYS_TRAIL_MS: f64 = 1_300.0;

/// How far the trail reaches left of the boxes, as a share of the frame width.
const KEYS_TRAIL_REACH: f64 = 0.135;

/// The shortest a mark may be drawn, as a share of the trail's reach.
///
/// A tap is twenty-odd milliseconds, which over any window worth showing is a
/// hairline — literally one pixel at 1280 wide. Drawn at its true length the
/// trail was a row of scratches; given a floor, each press is a block with a
/// shape, and the shape is the whole point. The length still grows with a long
/// hold, so a dragged slider is visibly one bar and not a tap.
const KEYS_MARK_MIN: f32 = 0.03;

/// How tall a mark is against its box.
const KEYS_MARK_HEIGHT: f32 = 0.6;

/// How far a box shrinks while its button is down.
///
/// Small. The counter jumping is what says a press happened; this is what says
/// it is still happening, and a box that visibly leapt about would pull the eye
/// off the play every time somebody tapped.
const KEYS_PRESS_SHRINK: f32 = 0.14;

/// How long a box takes to go down, and to come back up, in milliseconds of
/// watching.
///
/// Down fast and up slower. A press that eased in would read as late — the
/// sound and the note have already happened — while a release that snapped back
/// made a stream look like a strobe. Coming up over twice the time turns the
/// same taps into something the eye can follow.
const KEYS_PRESS_DOWN_MS: f64 = 45.0;
const KEYS_PRESS_UP_MS: f64 = 110.0;

impl Scene<'_> {
    /// The stretch of tapping behind each counter, running off to the left.
    ///
    /// The counter says how much; this says *how*. A stream alternates in even
    /// pairs, a doubletap comes in twos with a gap, a dragged slider is one long
    /// block, and somebody struggling taps unevenly — none of which a number
    /// climbing by one can show, and all of which are the whole reason to watch
    /// somebody else's replay.
    ///
    /// Time runs right to left, newest against the box, so the marks flow away
    /// the way the play has just gone. Older ones fade out rather than stopping
    /// at a hard edge, which would read as a wall the taps are hitting.
    fn draw_key_trail(
        &self,
        pixmap: &mut Pixmap,
        key: usize,
        time_ms: f64,
        layout: &Layout,
        presence: f32,
        place: (f32, f32, f32),
    ) {
        let (right, top, height) = place;
        let reach = (f64::from(layout.width) * KEYS_TRAIL_REACH) as f32;
        let rate = self.state.playback_rate().max(0.001);
        // The window is map time, so under a rate mod it covers more of the map
        // — which is right: two seconds of *watching* is what the eye is given,
        // whatever the map is doing underneath.
        let window = KEYS_TRAIL_MS * rate;
        let from = time_ms - window;

        let bar = height * KEYS_MARK_HEIGHT;
        let bar_top = top + (height - bar) / 2.0;
        let x_of = |at: f64| right - ((time_ms - at) / window) as f32 * reach;

        for &(down, up) in self.keys.holds[key]
            .iter()
            .rev()
            .take_while(|(_, up)| *up >= from)
        {
            let (a, b) = (down.max(from), up.min(time_ms));
            if b <= a {
                continue;
            }
            let (left, width) = (x_of(a), (x_of(b) - x_of(a)).max(1.0));
            // A tap is an instant and would be a hairline; given a floor it
            // reads as a block. The floor is a share of the reach rather than a
            // number of milliseconds because what is at stake is whether it can
            // be seen, and that is a question about pixels.
            let width = width.max(reach * KEYS_MARK_MIN);
            let Some(mark) = rounded_rect(left, bar_top, width, bar, bar * 0.35) else {
                continue;
            };
            // Fading with age, so the trail thins into the frame instead of
            // ending at a line.
            let age = ((time_ms - b) / window).clamp(0.0, 1.0) as f32;
            let mut paint = Paint {
                anti_alias: true,
                ..Default::default()
            };
            paint.set_color(with_alpha(
                self.skin.verdict_miss,
                0.75 * (1.0 - age) * presence,
            ));
            pixmap.fill_path(&mark, &paint, FillRule::Winding, Transform::identity(), None);
        }
    }

    /// The button counters down the right edge.
    ///
    /// osu!'s key overlay, which is the one part of its HUD that says something
    /// about the *player* rather than about the play: how they are holding the
    /// map — alternating, single-tapping, dragging with the mouse — is legible
    /// here and nowhere else on the screen.
    ///
    /// The right edge because that is where osu! puts it and because it is the
    /// only free side: the scoreboard has the left, and the score and accuracy
    /// have the top.
    fn draw_keys(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let Some(font) = &self.skin.font else {
            return;
        };
        if presence <= 0.01 {
            return;
        }
        let (width, height) = (f64::from(layout.width), f64::from(layout.height));
        let box_side = (height * KEYS_BOX) as f32;
        let box_wide = box_side * KEYS_WIDTH;
        let step = box_side * (1.0 + KEYS_GAP);
        let right = (width * (1.0 - KEYS_INSET)) as f32;
        // Centred on the frame, so the column is a fixed landmark rather than
        // something that moves with whatever is above or below it.
        let top = (height as f32 - (step * 4.0 - box_side * KEYS_GAP)) / 2.0;

        let rate = self.state.playback_rate().max(0.001);
        for (index, name) in KEY_NAMES.iter().enumerate() {
            // How far down, not whether down: every part of the box follows the
            // same number, so the shrink, the fill and the border move together
            // instead of one snapping while the others slide.
            let down = self.keys.pressed(index, time_ms, rate);
            self.draw_key_trail(pixmap, index, time_ms, layout, presence, {
                let y = top + step * index as f32;
                (right - box_wide, y, box_side)
            });
            let count = self.keys.count(index, time_ms);
            let shrink = KEYS_PRESS_SHRINK * down;
            let side = box_side * (1.0 - shrink);
            let wide = box_wide * (1.0 - shrink);
            let x = right - box_wide + (box_wide - wide) / 2.0;
            let y = top + step * index as f32 + (box_side - side) / 2.0;

            let Some(card) = rounded_rect(x, y, wide, side, side * 0.3) else {
                continue;
            };
            let mut fill = Paint {
                anti_alias: true,
                ..Default::default()
            };
            // Held, the box fills with the same red the engine uses for
            // everything that is happening *now*; loose, it is a dark plate
            // that keeps the column readable over a bright background.
            // The plate crossfades to the engine's red rather than switching to
            // it, which is what makes a fast stream read as a pulse instead of
            // a strobe.
            let body = with_alpha(
                blend(self.skin.background, self.skin.verdict_miss, down),
                (0.55 + 0.30 * down) * presence,
            );
            let ink = self.skin.hud;
            fill.set_color(body);
            pixmap.fill_path(&card, &fill, FillRule::Winding, Transform::identity(), None);

            let mut edge = Paint {
                anti_alias: true,
                ..Default::default()
            };
            edge.set_color(with_alpha(ink, (0.35 + 0.55 * down) * presence));
            pixmap.stroke_path(
                &card,
                &edge,
                &Stroke {
                    width: (side * 0.05).max(1.0),
                    ..Default::default()
                },
                Transform::identity(),
                None,
            );

            // The name above the count and much smaller: which button this is
            // never changes, and the number does.
            font.draw(
                pixmap,
                Label {
                    text: name,
                    x: x + wide / 2.0,
                    y: y + side * 0.34,
                    size: side * 0.26,
                    colour: with_alpha(ink, 0.7 * presence),
                    align: Align::Centre,
                },
            );
            font.draw(
                pixmap,
                Label {
                    text: &count.to_string(),
                    x: x + wide / 2.0,
                    y: y + side * 0.78,
                    size: side * 0.42,
                    colour: with_alpha(ink, 0.95 * presence),
                    align: Align::Centre,
                },
            );
        }
    }
}

#[cfg(test)]
mod keys {
    use super::KeyTrack;
    use dossier_replay::{Keys, ReplayFrame};

    fn track(script: &[(i64, u8)]) -> KeyTrack {
        let frames = script
            .iter()
            .map(|&(time_ms, keys)| ReplayFrame {
                time_ms,
                x: 0.0,
                y: 0.0,
                keys: Keys(keys),
            })
            .collect();
        KeyTrack::build(&dossier_sim::CursorTrack::new(frames))
    }

    /// The detail the whole element turns on. osu! sets the mouse bit as well
    /// when a keyboard button goes down, so K1 arrives as `M1 | K1` — and the
    /// two bits read together are one press, not two.
    #[test]
    fn a_keyboard_press_is_one_press_and_not_two() {
        let track = track(&[
            (0, 0),
            (10, Keys::M1 | Keys::K1),
            (20, 0),
            (30, Keys::M1 | Keys::K1),
            (40, 0),
            (50, Keys::M1 | Keys::K1),
            (60, 0),
        ]);
        assert_eq!(track.count(0, 100.0), 3);
    }

    /// …and a press made with the mouse alone lands in the same button rather
    /// than disappearing, which is what keeps a mouse player's counters from
    /// reading zero all game.
    #[test]
    fn a_mouse_press_lands_in_the_same_button() {
        let track = track(&[(0, 0), (10, Keys::M1), (20, 0), (30, Keys::M2), (40, 0)]);
        assert_eq!(track.count(0, 100.0), 1);
        assert_eq!(track.count(1, 100.0), 1);
    }

    /// The counter is what it was at that instant, not what it ends at — a
    /// frame has to be drawable without the frames before it, which is what
    /// lets them be drawn in parallel.
    #[test]
    fn a_count_is_as_of_the_instant_asked_for() {
        let track = track(&[
            (0, 0),
            (100, Keys::K1),
            (150, 0),
            (200, Keys::K1),
            (250, 0),
        ]);
        assert_eq!(track.count(0, 50.0), 0);
        assert_eq!(track.count(0, 120.0), 1);
        assert_eq!(track.count(0, 220.0), 2);
    }

    /// The box follows a number between nought and one, not a switch. A tap is
    /// shorter than the gap between two frames at 60fps, so half of them land
    /// between samples — switched, a stream flickers; eased, the same taps read
    /// as taps.
    #[test]
    fn a_press_goes_down_over_time_rather_than_at_once() {
        let track = track(&[(0, 0), (100, Keys::K1), (400, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);

        assert_eq!(at(99.0), 0.0, "nothing before the press");
        assert!(at(100.0) < 0.05, "the press starts at the top");
        assert!(at(115.0) > at(105.0), "and travels");
        assert!(at(200.0) > 0.99, "arriving well inside the hold");
        assert!(at(390.0) > 0.99, "and staying there");
    }

    /// Coming back up takes longer than going down, and long enough after a
    /// release the box is at rest.
    #[test]
    fn a_release_comes_back_slower_than_the_press_went_down() {
        let track = track(&[(0, 0), (100, Keys::K1), (400, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);
        assert!(at(430.0) > 0.2, "still visibly down a frame or two after");
        assert!(at(520.0) < 0.01, "and at rest well after");

        // The claim stated properly: thirty milliseconds after each edge, the
        // press has travelled further down than the release has travelled back
        // up. Down in 45ms and up in 110ms is what makes that true.
        let gone_down = at(130.0);
        let come_up = 1.0 - at(430.0);
        assert!(
            come_up < gone_down,
            "the release ({come_up:.3}) kept up with the press ({gone_down:.3})"
        );
    }

    /// A tap shorter than the fall never reaches the bottom, and the release
    /// starts from wherever it got to. Without that a fast stream pumps the box
    /// to full depth on every tap, which is louder than the tap.
    #[test]
    fn a_tap_too_short_to_land_starts_back_from_where_it_reached() {
        let track = track(&[(0, 0), (100, Keys::K1), (110, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);
        let peak = at(110.0);
        assert!(peak > 0.0 && peak < 0.7, "a 10ms tap reached {peak}");
        assert!(at(140.0) < peak, "and comes back from there");
    }

    /// The animation is in seconds of watching, so a rate mod does not make it
    /// twice as quick: a press should feel the same whatever the map is doing.
    #[test]
    fn the_animation_keeps_its_pace_under_a_rate_mod() {
        let track = track(&[(0, 0), (100, Keys::K1), (900, 0)]);
        let plain = track.pressed(0, 130.0, 1.0);
        // Under DoubleTime the same instant of watching is half again as much
        // map time, so the same point in the animation is further along it.
        let fast = track.pressed(0, 100.0 + 30.0 * 1.5, 1.5);
        assert!((plain - fast).abs() < 1e-6, "{plain} against {fast}");
    }

    /// A button still down when the recording stops was still pressed, and a
    /// finish is exactly where somebody would be looking at the counter.
    #[test]
    fn a_button_still_down_at_the_end_still_counts() {
        let track = track(&[(0, 0), (100, Keys::K2)]);
        assert_eq!(track.count(1, 200.0), 1);
        // …and the box is down at that last instant rather than at rest,
        // which is what the one-millisecond close is for.
        assert!(track.pressed(1, 100.5, 1.0) > 0.0);
    }
}
