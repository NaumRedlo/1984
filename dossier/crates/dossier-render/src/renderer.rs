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
/// Hidden's two multipliers on preempt: the note arrives over four tenths of
/// it and is taken away again over the next three.
///
/// ```csharp
/// public const double FADE_IN_DURATION_MULTIPLIER = 0.4;
/// public const double FADE_OUT_DURATION_MULTIPLIER = 0.3;
/// ```
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
/// The playfield outline: how thick, and how faint.
const FIELD_EDGE_WIDTH: f64 = 0.0018;
const FIELD_EDGE_ALPHA: f32 = 0.55;

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
/// Shortened twice and then let back out once, which is what the outlined field
/// bought: with the edge of the playfield drawn, how much room the board takes
/// from the play is a thing you can see instead of guess.
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

/// How long the frame takes to empty once it is back at full size.
///
/// The play does not fade out *during* the movement — it springs back to size
/// with everything still on it. What happens then is a fifth of a second, which
/// is long enough to be a movement and short enough that nothing is read in it:
/// the eye sees the frame let go, and then sees it clear. A hard cut in the
/// same place reads as a dropped frame rather than as an ending.
pub const FAIL_CLEAR_MS: f64 = 220.0;

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
    fn build(cursor: &dossier_sim::CursorTrack) -> Self {
        Self {
            holds: cursor.holds(),
        }
    }

    /// How many times this button had gone down by `time_ms`.
    fn count(&self, key: usize, time_ms: f64) -> usize {
        self.holds[key].partition_point(|(from, _)| *from <= time_ms)
    }

    /// Whether it is down at `time_ms`.
    fn held(&self, key: usize, time_ms: f64) -> bool {
        let holds = &self.holds[key];
        let index = holds.partition_point(|(from, _)| *from <= time_ms);
        index > 0 && holds[index - 1].1 > time_ms
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
    /// When each of the four buttons was down.
    keys: KeyTrack,
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
            let clear = self.fail_clear(time_ms);
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
            // Fast at first, so it is gone early and the tail of the movement
            // is only there to keep it from being a cut.
            let presence = (1.0 - clear) * (1.0 - clear);
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

    /// How far into the clearing that follows the movement.
    fn fail_clear(&self, time_ms: f64) -> f32 {
        let Some(end) = self.state.ending() else {
            return 0.0;
        };
        (((time_ms - end.time_ms - FAIL_ANIMATION_MS) / FAIL_CLEAR_MS).clamp(0.0, 1.0)) as f32
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
        self.draw_hud(pixmap, time_ms, layout);
        self.draw_danger(pixmap, time_ms, layout);
        self.draw_playfield_edge(pixmap, layout);
        self.draw_keys(pixmap, time_ms, layout, self.hud_presence(time_ms));
        self.draw_leaderboard(pixmap, time_ms, layout);
        self.draw_signature(pixmap, layout);
    }

    /// The fail.
    ///
    /// ```csharp
    /// private const float duration = 2500;
    /// ...
    /// drawableRuleset.Playfield.HitObjectContainer.FadeOut(duration / 2);
    /// redFlashLayer.FadeOutFromOne(1000);      // Color4.Red.Opacity(0.6f), additive
    /// Content.ScaleTo(0.85f, duration, Easing.OutQuart);
    /// Content.RotateTo(1, duration, Easing.OutQuart);
    /// Content.FadeColour(Color4.Gray, duration);
    /// ```
    ///
    /// The timing is lazer's — two and a half seconds, the notes gone by
    /// halfway, a red flash across the first second — and the movement is not.
    /// lazer tilts the frame a degree and drops it; this catches its breath
    /// instead. The whole screen pulls in — hard at first and then still
    /// closing, for as long as the music has left — and lets go in the last
    /// half second, back to full size with nothing on it, which is the field
    /// the play started from.
    ///
    /// Two reasons for the change. A tilt is a permanent state — the frame is
    /// left crooked and nothing puts it back — where a squeeze is a movement
    /// that completes, which is what a render wants at its end rather than in
    /// the middle of a stream. And these frames get cut together with others:
    /// a clip that finishes level can be followed by anything, and one that
    /// finishes at a slight angle cannot.
    fn compose_fail(
        &self,
        out: &mut Pixmap,
        field: &Pixmap,
        overlay: &Pixmap,
        progress: f32,
        presence: f32,
        layout: &Layout,
    ) {
        out.fill(self.skin.background);

        // In hard and then still going, out all at once. The pull takes most
        // of its distance in the first moment — that is the death — and then
        // keeps creeping inward for as long as the music has left, so the
        // frame is still closing when the sound gives out. The release is the
        // last fifth, and it is meant to look like something let go rather
        // than like something eased.
        let scale = if progress <= FAIL_RELEASE_AT {
            let t = progress / FAIL_RELEASE_AT;
            1.0 - (1.0 - FAIL_SQUEEZE) * (1.0 - (1.0 - t).powi(3))
        } else {
            let t = (progress - FAIL_RELEASE_AT) / (1.0 - FAIL_RELEASE_AT);
            FAIL_SQUEEZE + (1.0 - FAIL_SQUEEZE) * (1.0 - (1.0 - t).powi(3))
        };

        // Around the middle of the frame, so it pulls into itself rather than
        // towards a corner.
        let (cx, cy) = (layout.width as f32 / 2.0, layout.height as f32 / 2.0);
        let transform = Transform::from_translate(cx, cy)
            .pre_scale(scale, scale)
            .pre_translate(-cx, -cy);

        let blit = |out: &mut Pixmap, src: &Pixmap, opacity: f32| {
            let paint = tiny_skia::PixmapPaint {
                opacity,
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            };
            out.draw_pixmap(0, 0, src.as_ref(), &paint, transform, None);
        };

        // Nothing fades while the frame is moving. lazer takes the notes away
        // over the first half and drains the colour out of the rest, and both
        // were here until the whole thing read as the render giving up rather
        // than the play ending. The frame keeps everything it had and springs
        // back to size; only once it is still does `presence` take it away.
        blit(out, field, presence);
        blit(out, overlay, presence);

        let wash = |out: &mut Pixmap, colour: Color, blend: tiny_skia::BlendMode| {
            let mut paint = Paint::default();
            paint.set_color(colour);
            paint.anti_alias = false;
            paint.blend_mode = blend;
            if let Some(rect) =
                Rect::from_xywh(0.0, 0.0, layout.width as f32, layout.height as f32)
            {
                out.fill_rect(rect, &paint, Transform::identity(), None);
            }
        };

        // The red flash is additive and gone within the first second, so it is
        // a blow rather than a tint — but squared on the way out, and at a
        // fraction of lazer's opacity.
        //
        // The constant is lazer's; the surface it lands on is not. There the
        // red goes over a dimmed beatmap background with a lit playfield on
        // top, and 0.6 additive reads as a flash across a picture. Here the
        // field is very nearly black, so the same 0.6 has nothing to compete
        // with and floods the frame into a flat red card for a full second.
        let linear =
            1.0 - (progress * FAIL_ANIMATION_MS as f32 / FAIL_FLASH_MS as f32).clamp(0.0, 1.0);
        let flash = linear * linear;
        if flash > 0.0 {
            wash(
                out,
                with_alpha(self.skin.verdict_miss, flash * FAIL_FLASH_ALPHA),
                tiny_skia::BlendMode::Plus,
            );
        }
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
    /// Red creeping in from the edges as the health runs down.
    ///
    /// The health bar already says the number, and a number in a corner is not
    /// something anyone reads while watching a stream being played. This is the
    /// same fact put where it cannot be missed: the field itself starts to go
    /// red, and by the time it is obvious the play is nearly over.
    ///
    /// It only speaks in the last third of the bar. Above that it is silent,
    /// because a warning that is always on is not a warning — and it is drawn
    /// as four bands rather than one wash so the middle of the playfield, where
    /// the notes are, stays clean.
    fn draw_danger(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        if self.cannot_die() {
            return;
        }
        let Some(health) = self.state.health_at(time_ms) else {
            return;
        };
        if health >= DANGER_FROM {
            return;
        }
        // Squared, so it stays faint through most of the range and only takes
        // over when the bar is genuinely nearly out.
        let closeness = ((DANGER_FROM - health) / DANGER_FROM).clamp(0.0, 1.0);
        let strength = closeness * closeness * DANGER_MAX;

        let (w, h) = (layout.width as f32, layout.height as f32);
        let reach = h * DANGER_REACH;
        // A hand-made gradient: bands of falling opacity marching inwards. A
        // real one would be a shader per edge, four of them, rebuilt every
        // frame — this costs a few dozen rectangles and looks the same.
        for step in 0..DANGER_BANDS {
            let t = step as f32 / DANGER_BANDS as f32;
            let alpha = strength * (1.0 - t) * (1.0 - t) / DANGER_BANDS as f32 * 3.0;
            let colour = with_alpha(self.skin.verdict_miss, alpha);
            let band = reach / DANGER_BANDS as f32;
            let inset = t * reach;
            for rect in [
                Rect::from_xywh(0.0, inset, w, band),
                Rect::from_xywh(0.0, h - inset - band, w, band),
                Rect::from_xywh(inset, 0.0, band, h),
                Rect::from_xywh(w - inset - band, 0.0, band, h),
            ]
            .into_iter()
            .flatten()
            {
                let mut paint = Paint::default();
                paint.set_color(colour);
                paint.anti_alias = false;
                pixmap.fill_rect(rect, &paint, Transform::identity(), None);
            }
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
        // The first line is centred on the band the health bar and the
        // timeline share, so the three read as one row across the top rather
        // than as a bar with a paragraph hanging beside it.
        let leads = if self.state.score_at(time_ms).is_some() {
            score_size
        } else {
            accuracy_size
        };
        let mut top = self.top_band(layout) + font.digit_height(leads) / 2.0 - leads;
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

        // The tally, stacked under the accuracy in the verdict colours. A
        // viewer watching a replay wants the shape of the play, and "two
        // hundreds and a miss" is a different play from "three hundreds" at
        // the same percentage.
        //
        // Vertical, because a row of four spread four hundred pixels across
        // the top of the frame is not a corner — it is a banner, and it reads
        // as one. A column right-aligned on the same edge as the score keeps
        // the whole block the shape of the corner it is in, and takes the
        // question of the numbers moving with it: a right edge cannot shift.
        let tally_size = (height * 0.030) as f32;
        let counts = score.counts;
        let tally = [
            (u32::from(counts.count_300), self.skin.verdict_300),
            (u32::from(counts.count_100), self.skin.verdict_100),
            (u32::from(counts.count_50), self.skin.verdict_50),
            (u32::from(counts.count_miss), self.skin.verdict_miss),
        ];
        let mut y = top + accuracy_size + tally_size * 1.6;
        for (value, colour) in tally {
            font.draw(
                pixmap,
                Label {
                    text: &format!("{value}"),
                    x: layout.width as f32 - margin,
                    y,
                    size: tally_size,
                    colour: with_alpha(colour, presence),
                    align: Align::Right,
                },
            );
            y += tally_size * 1.25;
        }

        // Always on: they orient rather than report.
        self.draw_progress(pixmap, time_ms, layout, 1.0);
        self.draw_health(pixmap, time_ms, layout, presence);
        // The error bar and the spinner's speed share one place, because during
        // a spinner the bar has nothing to say: there are no clicks to time, so
        // it would sit there showing the last note before the spinner for as
        // long as the spinner lasts — a stale reading, which is worse than an
        // empty space and much worse than a live one.
        let spinning = self.spinner_grip(time_ms);
        if spinning < 1.0 {
            self.draw_error_bar(pixmap, time_ms, layout, presence * (1.0 - spinning));
        }
        if spinning > 0.0 {
            self.draw_spin_readout(pixmap, time_ms, layout, presence * spinning);
        }
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

    /// The bounds of the 512×384 field, drawn faintly.
    ///
    /// Everything a map contains happens inside this rectangle and nothing ever
    /// happens outside it, but a note near an edge and a note in open space look
    /// the same on a black frame — so where the field *ends* has to be taken on
    /// trust while placing the HUD, the scoreboard and anything else. Drawn, it
    /// is not a matter of trust: free space is visibly free.
    ///
    /// Faint on purpose. It is a guide for whoever is arranging the frame, and a
    /// guide that competes with the play has stopped being one.
    fn draw_playfield_edge(&self, pixmap: &mut Pixmap, layout: &Layout) {
        let Some(colour) = self.skin.playfield_edge else {
            return;
        };
        let top_left = layout.map(Point { x: 0.0, y: 0.0 });
        let bottom_right = layout.map(Point {
            x: dossier_beatmap::PLAYFIELD_WIDTH,
            y: dossier_beatmap::PLAYFIELD_HEIGHT,
        });
        let Some(rect) = Rect::from_ltrb(top_left.0, top_left.1, bottom_right.0, bottom_right.1)
        else {
            return;
        };
        let Some(path) = PathBuilder::from_rect(rect).stroke(
            &Stroke {
                width: (f64::from(layout.height) * FIELD_EDGE_WIDTH) as f32,
                ..Default::default()
            },
            1.0,
        ) else {
            return;
        };
        let mut paint = Paint {
            anti_alias: true,
            ..Default::default()
        };
        paint.set_color(with_alpha(colour, FIELD_EDGE_ALPHA));
        pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
    }

    /// The standings, down the left, climbing to the best score on the map.
    ///
    /// Read upwards: the worst kept score at the top, the leader at the bottom.
    /// A board with the leader on top is a table; one that climbs to them is a
    /// story, and the player's row rising through it is the only thing on screen
    /// that changes place.
    ///
    /// Drawn from the score the engine is already computing, so the row moves at
    /// the moment it actually passes somebody — and the move is worked out from
    /// the score curve rather than from the frame before, because a frame here
    /// has to stand alone or they cannot be drawn in parallel.
    fn draw_leaderboard(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let (Some(font), false) = (&self.skin.font, self.leaderboard.is_empty()) else {
            return;
        };
        let Some(track) = self.state.score_track() else {
            return;
        };
        let rows = self
            .leaderboard
            .standings_at(&ScoreCurve(track), time_ms, BOARD_ROWS);

        let height = f64::from(layout.height);
        let size = (height * BOARD_TEXT) as f32;
        let step = (height * BOARD_STEP) as f32;
        let left = (height * BOARD_LEFT) as f32;
        let width = (height * BOARD_WIDTH) as f32;
        let card_height = step * BOARD_CARD_FILL;
        // Anchored across the middle of the left edge, which is where the
        // playfield is emptiest whatever the aspect ratio. The block is as tall
        // as the window is long, whatever places happen to be in it — sizing it
        // from the places themselves put the leader three thousand pixels below
        // the frame on a map forty people had played.
        let drawn = BOARD_ROWS as f32;
        let top = pixmap.height() as f32 / 2.0 + (drawn / 2.0 - 1.0) * step;

        for row in &rows {
            let eased = {
                // Ease out, so it leaves briskly and settles rather than
                // arriving at speed.
                let t = row.moving.clamp(0.0, 1.0);
                1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t)
            };
            // Slot zero is the worst score kept and it is drawn at the *bottom*,
            // so the block reads best-first downwards and the player climbs it
            // from below. Drawn the other way round — worst at the top,
            // descending to the leader — was tried and looked wrong: the eye
            // starts at the top of a list, and starting it on the row that
            // matters least buries the one that matters most.
            let slot = row.from_slot + (row.slot - row.from_slot) * eased;
            let y = top - slot * step + size * 1.15;
            // Three states, three shapes. A row on its way out shrinks and fades
            // as it travels into the row that overtook it; one arriving at the
            // top grows into place from nothing; one merely changing slot stays
            // whole and slides. Sliding all three would make the board look like
            // a list being sorted, which is what it is and not what it is *for*.
            let t = row.moving.clamp(0.0, 1.0);
            // A leaver has to still be *there* while it travels, or it is not
            // flying into anything — it is a row dissolving where it stood. So it
            // holds its size and its colour for most of the trip and gives them
            // up at the end, on top of the row that took its place. Fading with
            // the same ease-out that carries it made it invisible before it
            // arrived, which is why the first attempt looked like no change at
            // all: the movement was right and nobody could see it.
            let late = 1.0 - t * t * t;
            let settling = if row.leaving {
                late
            } else if row.entering {
                eased
            } else if (row.slot - row.from_slot).abs() < f32::EPSILON {
                1.0
            } else {
                eased
            };
            let presence = if row.leaving || row.entering {
                settling
            } else {
                0.45 + 0.55 * settling
            };
            let shrink = if row.leaving || row.entering {
                BOARD_GROW + (1.0 - BOARD_GROW) * settling
            } else {
                0.94 + 0.06 * settling
            };

            // The card shrinks with the text. Scaling only the letters is what
            // made a collapsing row read as a fading one — the panel stayed its
            // full size underneath and nothing appeared to shrink at all.
            let card_w = width * shrink;
            let card_h = card_height * shrink;
            self.draw_board_row(
                pixmap,
                font,
                row,
                left + (width - card_w) / 2.0,
                y - (card_height - card_h) / 2.0,
                card_w,
                card_h,
                size * shrink,
                presence,
            );
        }
    }

    /// One card: the cover behind it, the avatar, the place, and the numbers.
    #[allow(clippy::too_many_arguments)]
    fn draw_board_row(
        &self,
        pixmap: &mut Pixmap,
        font: &crate::text::Font,
        row: &crate::leaderboard::Row,
        left: f32,
        baseline: f32,
        width: f32,
        card_height: f32,
        size: f32,
        presence: f32,
    ) {
        let top = baseline - size * 1.15;
        let Some(card) = rounded_rect(left, top, width, card_height, card_height * BOARD_RADIUS)
        else {
            return;
        };

        // The cover first, clipped to the card, then two washes over it: heavy on
        // the left where the avatar and the name sit, lighter on the right. One
        // flat dim would either drown the picture or lose the text; the point of
        // a cover is to be seen behind the half of the row that has fewer words
        // in it.
        let mut paint = Paint {
            anti_alias: true,
            ..Default::default()
        };
        let has_cover = row.entry.cover.as_deref().is_some_and(|p| self.pictures.contains_key(p));
        if let Some(cover) = row.entry.cover.as_deref().and_then(|p| self.pictures.get(p)) {
            let scale = (width / cover.width() as f32).max(card_height / cover.height() as f32);
            let shader = tiny_skia::Pattern::new(
                cover.as_ref(),
                tiny_skia::SpreadMode::Pad,
                tiny_skia::FilterQuality::Bilinear,
                presence,
                Transform::from_translate(left, top).pre_scale(scale, scale),
            );
            paint.shader = shader;
            pixmap.fill_path(&card, &paint, FillRule::Winding, Transform::identity(), None);
            paint.shader = Shader::SolidColor(Color::BLACK);
        }

        let base = if row.is_player {
            lighten(self.skin.background, BOARD_CARD_LIFT)
        } else {
            self.skin.background
        };
        // A gradient across the card rather than two flat bands.
        //
        // Bands were tried and are wrong twice over. They leave a seam where
        // they meet — one card reads as two — and the heavy one has to be heavy
        // enough for text over the *worst* cover, which on the left of the card
        // meant ninety per cent of near-black: the cover simply was not there,
        // and half of every row was a black rectangle. A ramp puts the weight
        // where the words are and lets go of it where they stop, so the picture
        // survives the half of the row that has fewer of them.
        let (heavy, light) = if has_cover {
            (BOARD_DARK_LEFT_COVER, BOARD_DARK_RIGHT_COVER)
        } else {
            (BOARD_DARK_LEFT, BOARD_DARK_RIGHT)
        };
        if let Some(shade) = tiny_skia::LinearGradient::new(
            tiny_skia::Point::from_xy(left, top),
            tiny_skia::Point::from_xy(left + width, top),
            vec![
                tiny_skia::GradientStop::new(0.0, with_alpha(base, heavy * presence)),
                tiny_skia::GradientStop::new(
                    BOARD_DARK_SPLIT,
                    with_alpha(base, heavy * BOARD_DARK_KNEE * presence),
                ),
                tiny_skia::GradientStop::new(1.0, with_alpha(base, light * presence)),
            ],
            tiny_skia::SpreadMode::Pad,
            Transform::identity(),
        ) {
            let wash = Paint {
                shader: shade,
                anti_alias: true,
                ..Default::default()
            };
            pixmap.fill_path(&card, &wash, FillRule::Winding, Transform::identity(), None);
        }

        let colour = if row.is_player {
            self.skin.hud
        } else {
            darken(self.skin.hud, BOARD_RIVAL_DIM)
        };

        // The avatar, square and inside a ring that glows a little. Red because
        // it is the house colour and because on a board of grey rows one warm
        // edge is enough to find your own line without reading it.
        let face = card_height * BOARD_FACE;
        let face_x = left + card_height * 0.16;
        let face_y = top + (card_height - face) / 2.0;
        if let Some(avatar) = row.entry.avatar.as_deref().and_then(|p| self.pictures.get(p)) {
            if let Some(clip) = rounded_rect(face_x, face_y, face, face, face * 0.28) {
                let scale = face / avatar.width().max(1) as f32;
                let mut art = Paint {
                    anti_alias: true,
                    ..Default::default()
                };
                art.shader = tiny_skia::Pattern::new(
                    avatar.as_ref(),
                    tiny_skia::SpreadMode::Pad,
                    tiny_skia::FilterQuality::Bilinear,
                    presence,
                    Transform::from_translate(face_x, face_y).pre_scale(scale, scale),
                );
                pixmap.fill_path(&clip, &art, FillRule::Winding, Transform::identity(), None);
            }
        }
        // The ring is drawn whether or not there is a face behind it: an empty
        // frame still says which row is which, where a missing one would leave
        // the layout jumping between players who have an avatar and players who
        // do not.
        for (grow, alpha) in [(BOARD_GLOW, 0.22), (0.0, 0.95)] {
            let Some(ring) = rounded_rect(
                face_x - face * grow,
                face_y - face * grow,
                face * (1.0 + grow * 2.0),
                face * (1.0 + grow * 2.0),
                face * 0.28,
            ) else {
                continue;
            };
            let mut edge = Paint {
                anti_alias: true,
                ..Default::default()
            };
            edge.set_color(with_alpha(self.skin.verdict_miss, alpha * presence));
            pixmap.stroke_path(
                &ring,
                &edge,
                &Stroke {
                    width: face * BOARD_RING,
                    ..Default::default()
                },
                Transform::identity(),
                None,
            );
        }

        // The place, large and lit, in a column of its own at the right edge with
        // the text stopping short of it.
        //
        // It was a dim watermark, on the reasoning that the order already says
        // the place so the number is optional. That reasoning holds for a
        // scoreboard you are reading and not for one you are watching: a row goes
        // past in a second and a half and the number is the only part of it that
        // says *where in the field* this is happening. Lit, it is the first thing
        // the eye finds on the card; dim, it was the last.
        //
        // The first three carry the bot's own gold, silver and bronze, so a
        // podium here and a podium on a leaderboard card are the same three
        // colours rather than two people's separate idea of gold.
        let rank_column = card_height * BOARD_RANK_COLUMN;
        let rank_colour = match row.place {
            0..=2 => self.skin.podium[row.place],
            _ => lighten(colour, BOARD_RANK_LIFT),
        };
        font.draw(
            pixmap,
            Label {
                text: &format!("{}", row.place + 1),
                x: left + width - card_height * 0.18,
                y: baseline + size * 0.62,
                size: size * 1.75,
                colour: with_alpha(rank_colour, 0.95 * presence),
                align: Align::Right,
            },
        );

        let text_x = face_x + face + card_height * 0.2;
        let text_room = left + width - rank_column - text_x;
        font.draw(
            pixmap,
            Label {
                text: &row.entry.name,
                x: text_x,
                y: baseline,
                size: name_size(&row.entry.name, font, size),
                colour: with_alpha(colour, 0.95 * presence),
                align: Align::Left,
            },
        );
        let mut under = compact(row.entry.score);
        if let Some(accuracy) = row.entry.accuracy {
            under.push_str(&format!("  {accuracy:.2}%"));
        }
        if !row.entry.mods.is_empty() {
            under.push_str(&format!("  {}", row.entry.mods));
        }
        // Shrunk to fit rather than allowed past the card. A ScoreV1 total with
        // an accuracy and mods after it is the widest line the board ever draws,
        // and sizing for the average left it hanging into the playfield.
        let mut under_size = size * 0.78;
        let measured = font.width(&under, under_size);
        if measured > text_room && measured > 0.0 {
            under_size *= text_room / measured;
        }
        font.draw(
            pixmap,
            Label {
                text: &under,
                x: text_x,
                y: baseline + size * 1.05,
                size: under_size,
                colour: with_alpha(darken(colour, 0.22), 0.9 * presence),
                align: Align::Left,
            },
        );
    }


    fn draw_signature(&self, pixmap: &mut Pixmap, layout: &Layout) {
        let (Some(font), Some(signature)) = (&self.skin.font, &self.signature) else {
            return;
        };
        let height = f64::from(layout.height);
        let margin = (height * 0.03) as f32;
        let client_size = (height * 0.028) as f32;
        let version_size = (height * 0.015) as f32;

        // Faint, and the version fainter still: the client is the part worth
        // catching at a glance, the build only matters to whoever goes looking.
        let bottom = layout.height as f32 - margin;
        font.draw(
            pixmap,
            Label {
                text: &signature.version,
                x: layout.width as f32 - margin,
                y: bottom,
                size: version_size,
                colour: with_alpha(self.skin.hud, 0.20),
                align: Align::Right,
            },
        );
        font.draw(
            pixmap,
            Label {
                text: &signature.client,
                x: layout.width as f32 - margin,
                y: bottom - version_size * 1.15,
                size: client_size,
                colour: with_alpha(self.skin.hud, 0.30),
                align: Align::Right,
            },
        );
        if !signature.mods.is_empty() {
            font.draw(
                pixmap,
                Label {
                    text: &signature.mods,
                    x: layout.width as f32 - margin,
                    y: bottom - version_size * 1.15 - client_size * 1.35,
                    size: client_size * 1.35,
                    colour: with_alpha(self.skin.hud, 0.80),
                    align: Align::Right,
                },
            );
        }
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
    /// The line everything along the top of the frame is centred on.
    ///
    /// One band, three things: the health bar in the left corner, the timeline
    /// in the middle, the score in the right. The timeline used to run nearly
    /// the full width, which left the corners to be stacked underneath it — so
    /// the bar sat below the strip rather than beside it and the top of the
    /// frame was three rows deep for no reason.
    fn top_band(&self, layout: &Layout) -> f32 {
        layout.height as f32 * 0.042
    }

    fn strip(&self, layout: &Layout) -> (f32, f32, f32) {
        let width = layout.width as f32;
        // Short enough to leave both corners alone. It is a progress bar with
        // break marks on it; it does not get more legible for being longer,
        // and every pixel it gives up is one the corners can use.
        let inset = width * 0.315;
        let height = (f64::from(layout.height) * 0.0075).max(3.0) as f32;
        (inset, width - inset * 2.0, self.top_band(layout) - height / 2.0)
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
        if self.cannot_die() {
            return;
        }
        let Some(health) = self.state.health_at(time_ms) else {
            return;
        };
        let height = f64::from(layout.height);
        let margin = (height * 0.03) as f32;
        let width = layout.width as f32 * 0.21;
        let thickness = (height * 0.022).max(6.0) as f32;
        // Beside the timeline rather than below it, sharing its centre line.
        // Three times its thickness, which is the right way round: one says
        // where the play is, the other says whether it is about to end.
        let y = self.top_band(layout) - thickness / 2.0;

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
    /// How much a spinner owns the bottom of the frame, 0 to 1.
    ///
    /// Faded rather than switched. A bar that vanishes and a number that appears
    /// on the same frame reads as a glitch; a quarter of a second of one giving
    /// way to the other reads as the display changing its mind, which is what it
    /// is doing.
    fn spinner_grip(&self, time_ms: f64) -> f32 {
        let mut grip: f32 = 0.0;
        for object in &self.state.timeline().objects {
            if !object.is_spinner() {
                continue;
            }
            // Open a little before it starts and shut a little after it ends, so
            // the swap has happened by the time the ring appears and is undone
            // by the time the next note is due.
            let opening = ((time_ms - (object.start_ms - SPIN_SWAP_MS)) / SPIN_SWAP_MS) as f32;
            let closing = (((object.end_ms + SPIN_SWAP_MS) - time_ms) / SPIN_SWAP_MS) as f32;
            grip = grip.max(opening.clamp(0.0, 1.0).min(closing.clamp(0.0, 1.0)));
        }
        grip
    }

    /// The spinner's speed, where the error bar usually is.
    fn draw_spin_readout(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let Some(font) = self.skin.font.as_ref() else {
            return;
        };
        // Whichever spinner is nearest to now: at the seam between two, the one
        // being read should be the one on screen.
        let Some(object) = self
            .state
            .timeline()
            .objects
            .iter()
            .filter(|object| object.is_spinner())
            .min_by(|a, b| {
                let near = |o: &TimedObject| {
                    if time_ms < o.start_ms {
                        o.start_ms - time_ms
                    } else if time_ms > o.end_ms {
                        time_ms - o.end_ms
                    } else {
                        0.0
                    }
                };
                near(a).total_cmp(&near(b))
            })
        else {
            return;
        };
        let rpm = dossier_sim::spinner_rpm(
            self.state.cursor_track(),
            object.start_ms,
            time_ms.clamp(object.start_ms, object.end_ms),
        );
        let height = f64::from(layout.height);
        let size = (height * SPIN_READOUT_SIZE) as f32;
        font.draw(
            pixmap,
            Label {
                text: &format!("RPM: {rpm:.0}"),
                x: layout.width as f32 * 0.5,
                y: (height * 0.962) as f32,
                size,
                colour: with_alpha(self.skin.spinner, presence),
                align: Align::Centre,
            },
        );
    }

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

    /// The two opacities, for tests that need to compare them.
    #[doc(hidden)]
    pub fn alpha_for_test(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_of(index, time_ms)
    }

    #[doc(hidden)]
    pub fn head_alpha_for_test(&self, index: usize, time_ms: f64) -> f32 {
        self.head_alpha(index, time_ms)
    }

    /// Opacity of an object: zero before it spawns and after it has faded.
    fn alpha_of(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::Own)
    }

    fn alpha_at(&self, index: usize, time_ms: f64, hidden: HiddenFade) -> f32 {
        let annotation = &self.annotations[index];
        if time_ms < annotation.spawn_ms || time_ms > annotation.gone_ms {
            return 0.0;
        }
        // A slider stays whole until its own end even if the head was judged
        // long before; only then does the fade start.
        let leaves = annotation.gone_ms - HIT_FADE_MS;
        let fade_in = if self.hidden {
            self.state.difficulty().preempt_ms() * HIDDEN_FADE_IN
        } else {
            self.state.difficulty().fade_in_ms()
        }
        .max(1.0);
        let appearing = ((time_ms - annotation.spawn_ms) / fade_in).clamp(0.0, 1.0) as f32;
        let leaving = fade((((time_ms - leaves) / HIT_FADE_MS).clamp(0.0, 1.0)) as f32);

        // Hidden takes the note away again the moment it has finished
        // arriving. The fade starts where the fade-in ended and runs for three
        // tenths of preempt, so the note is gone three tenths of preempt
        // before it is due — and a slider instead dissolves gradually across
        // its whole length.
        //
        // ```csharp
        // double fadeOutStartTime = hitObject.StartTime - hitObject.TimePreempt + hitObject.TimeFadeIn;
        // double fadeOutDuration = hitObject.TimePreempt * FADE_OUT_DURATION_MULTIPLIER;
        // double longFadeDuration = hitObject.GetEndTime() - fadeOutStartTime;
        // ```
        // A spinner is not in that switch either, and it must not be: Hidden
        // takes away what you would otherwise read ahead, and a spinner has
        // nothing to read ahead — it is a thing you are already doing. Fading it
        // like a note left the whole spinner section as a black screen with a
        // cursor circling in it, which is what a bug looks like rather than what
        // a mod looks like.
        let object = &self.state.timeline().objects[index];
        if self.hidden && hidden != HiddenFade::Untouched && !object.is_spinner() {
            let starts = annotation.spawn_ms + fade_in;
            let duration = if object.is_slider() && hidden == HiddenFade::Own {
                (object.end_ms - starts).max(1.0)
            } else {
                self.state.difficulty().preempt_ms() * HIDDEN_FADE_OUT
            };
            let hiding = 1.0 - (((time_ms - starts) / duration).clamp(0.0, 1.0) as f32);
            return appearing * leaving * hiding;
        }
        appearing * leaving
    }

    /// The opacity of the parts of a slider Hidden does not touch.
    ///
    /// The mod fades the body, the ticks and the head, and nothing else. Its
    /// own source says so of the arrows outright:
    ///
    /// ```csharp
    /// case DrawableSliderRepeat sliderRepeat:
    ///     // only apply to circle piece – reverse arrow is not affected by hidden.
    ///     sliderRepeat.CirclePiece.FadeOut(fadeDuration);
    /// ```
    ///
    /// and the ball and its follow circle appear in the switch not at all. It
    /// has to be that way round to be playable: the body is what the mod takes
    /// away, and the ball is what is left to follow once it has gone.
    fn alpha_through_hidden(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::Untouched)
    }

    /// The opacity of a slider's *head*, which Hidden treats as a circle.
    ///
    /// A slider's body dissolves across its whole length; its head goes on the
    /// ordinary short fade, like any note. lazer says so by handling the two in
    /// separate cases:
    ///
    /// ```csharp
    /// case DrawableSlider slider:
    ///     slider.Body.FadeOut(longFadeDuration, Easing.Out);
    /// ```
    ///
    /// Sharing one opacity between them dimmed the head on the body's schedule,
    /// so on a long slider the note you are about to click was already half
    /// gone — which is the wrong half of the object to take away, and reads as
    /// the head fading strangely rather than as the body dissolving.
    fn head_alpha(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::AsANote)
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
                let slide = object.slide_duration_ms().unwrap_or(0.0);
                for &tick in &annotation.ticks_ms {
                    // A tick belongs to the body, so it cannot precede it. It
                    // used to be drawn as soon as the note appeared, which put
                    // dots in empty space ahead of a slider that had not grown
                    // that far — and a dot with no line under it does not read
                    // as sitting on the line.
                    let on_body =
                        path_fraction(object, tick).is_some_and(|frac| frac >= from && frac <= to);
                    if tick <= time_ms || !on_body {
                        continue;
                    }
                    let Some(at) = object.ball_at(tick) else {
                        continue;
                    };
                    // Each tick arrives on its own schedule rather than the
                    // whole row appearing at once, so they light up in front
                    // of the ball as it travels.
                    //
                    // ```csharp
                    // if (SpanIndex > 0)
                    //     offset = 200;              // repeats
                    // else
                    //     offset = TimePreempt * 0.66f;
                    // TimePreempt = (StartTime - SpanStartTime) / 2 + offset;
                    // ```
                    //
                    // Half the distance it sits into its own slide, plus two
                    // thirds of the object's preempt on the way out and a flat
                    // two hundred milliseconds on every slide back — the game
                    // gives less warning on a repeat because the player has
                    // already seen where the ticks are.
                    let span = if slide > 0.0 {
                        ((tick - object.start_ms) / slide).floor()
                    } else {
                        0.0
                    };
                    let offset = if span > 0.0 {
                        TICK_REPEAT_LEAD_MS
                    } else {
                        self.state.difficulty().preempt_ms() * TICK_FIRST_LEAD
                    };
                    let live = tick - ((tick - (object.start_ms + span * slide)) / 2.0 + offset);
                    let arriving = (((time_ms - live) / TICK_FADE_MS).clamp(0.0, 1.0)) as f32;
                    if arriving <= 0.0 {
                        continue;
                    }
                    // …and grows into place as it arrives. The game uses an
                    // elastic overshoot over four times the fade; this is the
                    // same movement without the bounce, which at a dot of six
                    // pixels would be a flicker rather than a flourish.
                    let grown = 0.5 + 0.5 * fade((((time_ms - live) / (TICK_FADE_MS * 4.0)).clamp(0.0, 1.0)) as f32);
                    self.dot(
                        pixmap,
                        at,
                        radius * 0.14 * grown,
                        lighten(self.skin.circle_border, 0.5),
                        alpha * arriving,
                        layout,
                    );
                }
                // Hidden fades the body out from under the ball; the ball and
                // its follow circle stay, and so do the arrows.
                let carried = self.alpha_through_hidden(index, time_ms);
                if let Some(ball) = object.ball_at(time_ms) {
                    self.ring(
                        pixmap,
                        ball,
                        radius * 2.4,
                        radius * 0.06,
                        self.skin.circle_border,
                        carried * 0.5,
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
                    self.dot(pixmap, ball, radius, colour, carried, layout);
                    self.dot(
                        pixmap,
                        ball,
                        radius * (BALL_CORE_SCALE + (1.0 - BALL_CORE_SCALE) * done),
                        lighten(colour, 0.45),
                        carried,
                        layout,
                    );
                }
                self.draw_reverse_arrow(
                    pixmap,
                    object,
                    annotation,
                    time_ms,
                    radius,
                    carried,
                    (from, to),
                    layout,
                );
                // The head leaves on its own click rather than with the rest of
                // the slider — but it leaves, it does not vanish. Popping out of
                // existence mid-slide was the most artificial thing on screen.
                let exit = self.exit_progress(annotation.head_ms, time_ms);
                if exit < 1.0 {
                    let leaving = self.head_alpha(index, time_ms) * fade(exit);
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

        // The approach circle only exists while the note is still coming — and
        // not at all under Hidden, which is the half of the mod a player
        // actually feels. `OsuModHidden` implements `IHidesApproachCircles`
        // and hides them outright.
        if !object.is_spinner() && time_ms < object.start_ms && !self.hidden {
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

        self.draw_spin_bonus(pixmap, object, time_ms, alpha, layout);
    }

    /// The bonus so far, below the centre, and what it does when it grows.
    ///
    /// Each award arrives lit and oversized, then settles: it shrinks inward to
    /// its resting size and fades to grey, and stays there holding the running
    /// total until the next one lands and lights it again. So the number itself
    /// is the history — a spinner that keeps paying keeps flashing white, one
    /// that has stopped sits grey at whatever it reached.
    ///
    /// The step is a thousand, not the eleven hundred the score gets. osu!
    /// displays and pays different numbers here — `hitSpinner.Bonus(1000)`
    /// beside a `SpinnerBonus` worth 1100 — and copying the score's figure onto
    /// the screen would be a plausible, wrong number.
    fn draw_spin_bonus(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        time_ms: f64,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(font) = self.skin.font.as_ref() else {
            return;
        };
        let Some(judge) = self.state.judge() else {
            return;
        };
        // Every bonus this spinner has paid by now, and when the last one came.
        let mut awarded = 0u32;
        let mut latest = f64::NEG_INFINITY;
        for event in judge.events() {
            if event.part != dossier_sim::Part::SpinnerBonus || event.time_ms > time_ms {
                continue;
            }
            if event.time_ms < object.start_ms || event.time_ms > object.end_ms {
                continue;
            }
            awarded += 1;
            latest = latest.max(event.time_ms);
        }
        if awarded == 0 {
            return;
        }

        let age = time_ms - latest;
        // One pulse per award: lit and large at the moment it lands, settling to
        // grey and smaller over a fifth of a second. Cubed on the way out so
        // the flash is a flash rather than a slow dim.
        let flash = (1.0 - (age / SPINNER_BONUS_PULSE_MS).clamp(0.0, 1.0)) as f32;
        let eased = flash * flash * flash;
        let size = layout.length(SPINNER_BONUS_SIZE) * (1.0 + SPINNER_BONUS_SWELL * eased);
        // Lifted toward white rather than swapped for it, so the resting state
        // is the spinner's own colour dimmed rather than a second palette.
        let colour = lighten(darken(self.skin.spinner, SPINNER_BONUS_REST), eased);
        let at = layout.map(Point {
            x: Point::CENTRE.x,
            y: Point::CENTRE.y + SPINNER_BONUS_BELOW,
        });
        font.draw(
            pixmap,
            Label {
                text: &format!("{}", awarded * SPINNER_BONUS_STEP),
                x: at.0,
                y: at.1 + size * 0.35,
                size,
                colour: with_alpha(colour, alpha),
                align: Align::Centre,
            },
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

/// A rectangle with its corners taken off.
///
/// tiny-skia has no rounded rectangle, and a scoreboard of square cards over a
/// round playfield looks like a debug overlay — which is what this renderer spent
/// its first month looking like.
fn rounded_rect(x: f32, y: f32, width: f32, height: f32, radius: f32) -> Option<tiny_skia::Path> {
    if width <= 0.0 || height <= 0.0 {
        return None;
    }
    let r = radius.min(width / 2.0).min(height / 2.0).max(0.0);
    let (right, bottom) = (x + width, y + height);
    let mut path = PathBuilder::new();
    path.move_to(x + r, y);
    path.line_to(right - r, y);
    path.quad_to(right, y, right, y + r);
    path.line_to(right, bottom - r);
    path.quad_to(right, bottom, right - r, bottom);
    path.line_to(x + r, bottom);
    path.quad_to(x, bottom, x, bottom - r);
    path.line_to(x, y + r);
    path.quad_to(x, y, x + r, y);
    path.close();
    path.finish()
}

/// The engine's score track, as the scoreboard's `ScoreAt`.
///
/// A newtype rather than an `impl` on `ScoreTrack` itself, so the trait stays a
/// statement about what a scoreboard needs rather than a method the simulator has
/// to carry for the renderer's benefit.
struct ScoreCurve<'a>(&'a dossier_sim::ScoreTrack);

impl crate::leaderboard::ScoreAt for ScoreCurve<'_> {
    fn at(&self, time_ms: f64) -> u64 {
        self.0.at(time_ms)
    }

    fn reached(&self, score: u64) -> f64 {
        self.0.reached(score)
    }
}

/// Which of Hidden's two fades an object's part takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HiddenFade {
    /// The object's own: a long dissolve for a slider body, the short one for
    /// anything else.
    Own,
    /// The short one whatever the object is — a slider's head is a note.
    AsANote,
    /// None at all: the ball, the follow circle and the reverse arrows are not
    /// in the mod's switch.
    Untouched,
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
/// The longest name the scoreboard will draw in full.
///
/// A real one, chosen because it is the longest that still reads at a glance on
/// a card this size. Names go past twice this — osu! allows fifteen characters
/// and people use all of them — and a long one either ran into the rank number
/// or shrank the row's type until the whole board was set in two sizes.
///
/// Measured as a *width* rather than a count of characters, which is the only
/// way it can mean anything: `WWWWWWWWWWWW` is nearly twice `iiiiiiiiiiii` in
/// any proportional face, and cutting both at twelve characters would keep one
/// inside the card and leave the other hanging out of it.
const NAME_YARDSTICK: &str = "-legusshhka-";

/// The size to set a name at so it stays inside the yardstick's width.
///
/// Set smaller rather than cut short. A name is somebody's, and `entxrth3vxi…`
/// is not their name — where a shrunk one still is, and osu! caps names at
/// fifteen characters so the worst case is a fifth off the size rather than
/// something unreadable.
///
/// The same treatment the line beneath already gets, so a card that has to
/// give ground gives it the same way twice.
fn name_size(name: &str, font: &crate::text::Font, size: f32) -> f32 {
    let room = font.width(NAME_YARDSTICK, size);
    let measured = font.width(name, size);
    if room <= 0.0 || measured <= room {
        return size;
    }
    size * room / measured
}

/// A score in as few characters as it can be said in.
///
/// Three significant figures and a suffix. The board carries totals from two
/// scoring systems three orders of magnitude apart — lazer's standardised
/// million and ScoreV1's hundreds of millions — and the second, grouped in
/// threes, is eleven characters before the accuracy and the mods are appended
/// to it. That line was already being shrunk to fit; this is what it was being
/// shrunk *from*.
///
/// Nothing under ten thousand is touched: a four-figure score is short already,
/// and "9.99k" for 9 994 is longer than the number it replaces.
fn compact(value: u64) -> String {
    const STEPS: [(u64, char); 3] = [(1_000_000_000, 'b'), (1_000_000, 'm'), (1_000, 'k')];
    if value < 10_000 {
        return grouped(value);
    }
    for (unit, suffix) in STEPS {
        if value >= unit {
            let scaled = value as f64 / unit as f64;
            // Three significant figures, so the width is the same whichever
            // side of ten or a hundred the number falls on.
            let text = match scaled {
                s if s < 10.0 => format!("{s:.2}"),
                s if s < 100.0 => format!("{s:.1}"),
                s => format!("{s:.0}"),
            };
            return format!("{text}{suffix}");
        }
    }
    grouped(value)
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
/// How long a stretch of tapping the trail shows, in milliseconds of map time.
///
/// Two seconds. Long enough to hold a whole burst and read its shape — a stream
/// alternates, a doubletap comes in pairs, a held slider is one long block —
/// and short enough that individual taps in a 200 BPM stream are still separate
/// marks rather than a solid bar.
const KEYS_TRAIL_MS: f64 = 2_000.0;

/// How far the trail reaches left of the boxes, as a share of the frame width.
const KEYS_TRAIL_REACH: f64 = 0.12;

/// How far a box shrinks while its button is down.
///
/// Small. The counter jumping is what says a press happened; this is what says
/// it is still happening, and a box that visibly leapt about would pull the eye
/// off the play every time somebody tapped.
const KEYS_PRESS_SHRINK: f32 = 0.12;

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

        let bar = height * 0.44;
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
            // reads as a mark. The floor is in pixels rather than in
            // milliseconds because what is at stake is whether it can be seen.
            let width = width.max(reach * 0.006);
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

        for (index, name) in KEY_NAMES.iter().enumerate() {
            let held = self.keys.held(index, time_ms);
            self.draw_key_trail(pixmap, index, time_ms, layout, presence, {
                let y = top + step * index as f32;
                (right - box_wide, y, box_side)
            });
            let count = self.keys.count(index, time_ms);
            let shrink = if held { KEYS_PRESS_SHRINK } else { 0.0 };
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
            let body = if held {
                with_alpha(self.skin.verdict_miss, 0.85 * presence)
            } else {
                with_alpha(self.skin.background, 0.55 * presence)
            };
            let ink = self.skin.hud;
            fill.set_color(body);
            pixmap.fill_path(&card, &fill, FillRule::Winding, Transform::identity(), None);

            let mut edge = Paint {
                anti_alias: true,
                ..Default::default()
            };
            edge.set_color(with_alpha(ink, if held { 0.9 } else { 0.35 } * presence));
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
mod names {
    use super::{name_size, NAME_YARDSTICK};

    fn font() -> crate::text::Font {
        let path = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../assets/fonts/TorusNotched-Bold.ttf"
        );
        let bytes = std::fs::read(path).expect("the repo ships this font");
        crate::text::Font::from_bytes(&bytes).expect("and it parses")
    }

    /// The yardstick itself, and anything narrower, is set at full size.
    #[test]
    fn a_name_that_fits_is_left_alone() {
        let font = font();
        assert_eq!(name_size(NAME_YARDSTICK, &font, 20.0), 20.0);
        assert_eq!(name_size("sw1t", &font, 20.0), 20.0);
    }

    /// A longer one is set smaller — never cut. A name is somebody's, and
    /// `entxrth3vxi…` is not their name, where a shrunk one still is.
    #[test]
    fn a_long_name_is_set_smaller_until_it_fits() {
        let font = font();
        let room = font.width(NAME_YARDSTICK, 20.0);
        for name in ["WWWWWWWWWWWWWWW", "Sakiko Togawa the second", "entxrth3vxid_2026"] {
            let size = name_size(name, &font, 20.0);
            assert!(size < 20.0, "{name:?} was not shrunk");
            assert!(
                font.width(name, size) <= room + 1e-3,
                "{name:?} at {size} is still wider than the yardstick"
            );
        }
    }

    /// The whole case for measuring rather than counting characters. Fifteen
    /// `i`s are *narrower* than the twelve-character yardstick and fifteen `W`s
    /// are more than twice as wide; a rule counting characters would shrink a
    /// name that already fitted.
    #[test]
    fn width_is_not_a_count_of_characters() {
        let font = font();
        let narrow = "iiiiiiiiiiiiiii";
        let wide = "WWWWWWWWWWWWWWW";
        assert_eq!(narrow.chars().count(), wide.chars().count());
        assert_eq!(name_size(narrow, &font, 20.0), 20.0);
        assert!(name_size(wide, &font, 20.0) < 20.0);
    }

    /// There is no floor on the shrinking, and there does not need to be one.
    ///
    /// osu! caps a name at fifteen characters, and a real one that long comes
    /// out about a fifth smaller than the rest of the board — measured, 0.79 —
    /// which reads perfectly well. The bound on the pathological case is the
    /// card itself: a name of fifteen `W`s lands at 0.46 and is still inside
    /// its row, which is what the rule is for. A floor would trade that for an
    /// overflow, and an overflow is the thing being fixed.
    #[test]
    fn a_real_long_name_barely_shrinks() {
        let font = font();
        for name in ["Sakiko Togawa t", "entxrth3vxid_20"] {
            let factor = name_size(name, &font, 20.0) / 20.0;
            assert!(
                factor > 0.7,
                "{name:?} came out at {factor:.2} of the size — too small to sit in a list"
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
        assert!(track.held(0, 120.0));
        assert!(!track.held(0, 170.0));
        assert!(track.held(0, 220.0));
        assert!(!track.held(0, 300.0));
    }

    /// A button still down when the recording stops was still pressed, and a
    /// finish is exactly where somebody would be looking at the counter.
    #[test]
    fn a_button_still_down_at_the_end_still_counts() {
        let track = track(&[(0, 0), (100, Keys::K2)]);
        assert_eq!(track.count(1, 200.0), 1);
        assert!(track.held(1, 100.0));
    }
}

#[cfg(test)]
mod compacting {
    use super::compact;

    /// The example that prompted it, and the shape either side of it.
    #[test]
    fn a_score_is_said_in_three_figures_and_a_suffix() {
        assert_eq!(compact(1_234_567), "1.23m");
        assert_eq!(compact(12_345_678), "12.3m");
        assert_eq!(compact(125_645_112), "126m");
        assert_eq!(compact(987_654), "988k");
        assert_eq!(compact(87_340), "87.3k");
    }

    /// A four-figure score is short already, and "9.99k" is longer than the
    /// number it would replace.
    #[test]
    fn small_scores_are_left_as_they_are() {
        assert_eq!(compact(0), "0");
        assert_eq!(compact(950), "950");
        assert_eq!(compact(9_994), "9 994");
        assert_eq!(compact(10_000), "10.0k");
    }

    /// Both scoring systems the board carries, side by side: the point is that
    /// they come out the same width despite being three orders apart.
    #[test]
    fn both_scoring_systems_come_out_the_same_width() {
        assert_eq!(compact(1_002_431).len(), compact(125_645_112).len() + 1);
        assert!(compact(125_645_112).len() <= 5);
        assert!(compact(1_002_431).len() <= 5);
    }
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
