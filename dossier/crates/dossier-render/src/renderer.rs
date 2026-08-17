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
use crate::skin::{blend, lighten, Skin};
use crate::text::{Align, Label};

mod format;

mod paint;
use paint::rounded_rect;

mod objects;
mod hud;
mod scoreboard;
mod overlay;
mod keys;
use keys::KeyTrack;

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

/// How long a struck note takes to leave, how long a missed one takes, and how
/// long the number on it lasts.
///
/// ```csharp
/// const double legacy_fade_duration = 240;
///
/// CircleSprite.FadeOut(legacy_fade_duration);
/// CircleSprite.ScaleTo(1.4f, legacy_fade_duration, Easing.Out);
/// OverlaySprite.FadeOut(legacy_fade_duration);
/// OverlaySprite.ScaleTo(1.4f, legacy_fade_duration, Easing.Out);
/// ...
/// // legacy skins of version 2.0 and newer only apply very short fade out to
/// // the number piece.
/// hitCircleText.FadeOut(legacy_fade_duration / 4);
/// ...
/// case ArmedState.Miss:
///     this.FadeOut(100);
/// ```
///
/// This was 140, and before that 220 — lowered by hand because 220 "read as
/// sluggish". The number it was being nudged towards and away from is 240, and
/// reported as exactly that: notes leaving faster than the game lets them.
/// Guessing at it twice cost more than reading it once would have.
///
/// A miss goes quicker and does not swell, which is the difference that says
/// which happened without waiting for the combo counter.
const HIT_FADE_MS: f64 = 240.0;
const MISS_FADE_MS: f64 = 100.0;
/// The number goes four times faster than the circle under it, and does not
/// grow with it. A digit stretched to 1.4 while fading is a smear; osu! stopped
/// doing that for skins of version 2.0 and later, and every skin anybody sends
/// is later.
const NUMBER_FADE_MS: f64 = HIT_FADE_MS / 4.0;

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

/// How a verdict arrives and goes, on stable's own clock.
///
/// ```csharp
/// const double fade_in_length = 120;
/// const double fade_out_delay = 500;
/// const double fade_out_length = 600;
///
/// this.FadeInFromZero(fade_in_length);
/// this.Delay(fade_out_delay).FadeOut(fade_out_length);
/// ```
///
/// Both transforms start together, so the hold is measured from the mark
/// appearing rather than from the fade-in ending: full for half a second, then
/// six tenths of a second going. Eleven hundred milliseconds in all.
///
/// This used to be 240ms flat, on the reasoning that a verdict is a receipt
/// and a stream at 200bpm brings the next note in 75ms. That reasoning was
/// about the wrong thing — stable has the same problem and answers it by
/// *stacking* marks, not by cutting them short. A quarter of a second reads as
/// a flicker, and a viewer watching a replay to see what a note gave has to
/// catch it in the time it takes to look.
const VERDICT_FADE_IN_MS: f64 = 120.0;
const VERDICT_HOLD_MS: f64 = 500.0;
const VERDICT_FADE_OUT_MS: f64 = 600.0;
const VERDICT_MS: f64 = VERDICT_HOLD_MS + VERDICT_FADE_OUT_MS;

/// The flash a struck note leaves behind, on lazer's clock.
///
/// ```csharp
/// bool hitLightingEnabled = config.Get<bool>(OsuSetting.HitLighting);
/// ...
/// Lighting.ScaleTo(0.8f).ScaleTo(1.2f, 600, Easing.Out);
/// Lighting.FadeIn(200).Then().Delay(200).FadeOut(1000);
/// ```
///
/// `Then()` chains, so the hold runs from the end of the fade-in: two tenths
/// of a second coming up, two holding, a full second going.
///
/// Off by default here — see [`Skin::hit_lighting`]. It is a setting in the
/// game for the same reason it is one here.
const LIGHTING_FADE_IN_MS: f64 = 200.0;
const LIGHTING_HOLD_MS: f64 = 400.0;
const LIGHTING_FADE_OUT_MS: f64 = 1000.0;
const LIGHTING_MS: f64 = LIGHTING_HOLD_MS + LIGHTING_FADE_OUT_MS;
const LIGHTING_GROWTH_MS: f64 = 600.0;
const LIGHTING_FROM: f32 = 0.8;
const LIGHTING_TO: f32 = 1.2;

/// How long anything about an object is still being drawn after the object
/// itself has gone.
///
/// `candidates` is the window every pass draws from, and it was measured from
/// the note's own fade alone — which was true while a verdict lasted a quarter
/// of a second and stopped being true the moment it lasted eleven hundred
/// milliseconds. An object dropped from the window takes its own verdict with
/// it, and the mark vanishes mid-fade.
const AFTERLIFE_MS: f64 = if VERDICT_MS > LIGHTING_MS { VERDICT_MS } else { LIGHTING_MS };

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
    /// The map's own artwork, already scaled, blurred and dimmed to the output
    /// size — see [`crate::background`]. Drawn under everything.
    backdrop: Option<Pixmap>,
}

/// How far the field is drawn in, and towards what.
///
/// The playfield objects and the cursor follow it; the verdicts, the break
/// arrows and the HUD do not — a readout that swelled with the zoom would read
/// as the interface come loose from the frame. `closeness` runs 0 to 1, and the
/// caller ramps it: [`Layout::focused`] turns the pair into the field's layout.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Camera {
    pub focus: Point,
    pub closeness: f64,
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
            .fold(0.0f64, f64::max)
            + AFTERLIFE_MS;

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
            backdrop: None,
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
    /// Put the map's artwork behind the play.
    ///
    /// The pixmap is expected to be the size of the frame and already prepared:
    /// preparing it is a blur over two million pixels, and doing that per frame
    /// would cost more than drawing the play does.
    pub fn with_backdrop(mut self, backdrop: Pixmap) -> Self {
        self.backdrop = Some(backdrop);
        self
    }

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
    /// A single frame, with no camera move — a still is never in the middle of
    /// a zoom.
    pub fn frame(&self, time_ms: f64, layout: &Layout) -> Pixmap {
        let mut pixmap = Pixmap::new(layout.width, layout.height)
            .expect("a frame with a zero dimension was requested");
        self.draw_into(&mut pixmap, time_ms, layout, None);
        pixmap
    }

    /// Draw into a buffer that already exists.
    ///
    /// Video wants this: a 1080p frame is eight megabytes, and allocating and
    /// dropping one per frame is several gigabytes of churn over a map for no
    /// gain — the previous frame is entirely overwritten anyway.
    pub fn draw_into(
        &self,
        pixmap: &mut Pixmap,
        time_ms: f64,
        layout: &Layout,
        camera: Option<Camera>,
    ) {
        // The camera draws the field in towards a moment; everything laid over
        // the play — the verdicts, the break arrows, the HUD — keeps the plain
        // layout, so a readout never swells with the zoom. `close` is the field
        // layout, and equals `layout` exactly when there is no move, so a render
        // without one is untouched.
        let focused = camera.map(|c| layout.focused(c.focus, c.closeness));
        let close = focused.as_ref().unwrap_or(layout);
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
                self.ground(pixmap);
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
            // A failed play never zooms — the fail has its own ending — so the
            // frozen field takes the plain layout.
            self.draw_field(&mut field, frozen, layout, layout);
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
            self.draw_play(&mut frame, time_ms, layout, close);
            pixmap.fill(self.skin.background);
            let paint = tiny_skia::PixmapPaint {
                opacity: intro,
                quality: tiny_skia::FilterQuality::Nearest,
                ..Default::default()
            };
            pixmap.draw_pixmap(0, 0, frame.as_ref(), &paint, Transform::identity(), None);
            return;
        }
        // While the camera draws in, the interface gets out of the way — it
        // fades as the field swells and comes back as the camera pulls out,
        // rather than sitting at a fixed size over a play that no longer fills
        // the frame the way it was placed against. The play itself — the notes
        // and the cursor — is all that is left on screen at the bottom of the
        // dip. Away from a dip this is the plain path, untouched to the byte.
        match camera {
            Some(camera) if camera.closeness > 0.0 => {
                self.draw_zoomed(pixmap, time_ms, layout, close, 1.0 - camera.closeness as f32);
            }
            _ => self.draw_play(pixmap, time_ms, layout, close),
        }
    }

    /// The play drawn in, with the interface fading out behind the camera.
    ///
    /// `interface` is how visible the interface is: 1 with the camera home, 0
    /// at the bottom of the dip. The notes and the cursor are drawn at full
    /// strength through the drawn-in `close` layout; everything laid over them
    /// — the verdicts, the break arrows and the whole HUD — is drawn once onto
    /// a layer at the plain layout and composited at `interface`, so it dims as
    /// one and at no point changes size.
    fn draw_zoomed(
        &self,
        pixmap: &mut Pixmap,
        time_ms: f64,
        layout: &Layout,
        close: &Layout,
        interface: f32,
    ) {
        self.ground(pixmap);
        for index in self.candidates(time_ms).rev() {
            if self.alpha_of(index, time_ms) > 0.0 {
                self.draw_object(pixmap, index, time_ms, close);
            }
        }
        self.draw_cursor(pixmap, time_ms, close);
        if interface <= 0.0 {
            return;
        }
        let mut over = Pixmap::new(layout.width, layout.height)
            .expect("a frame with a zero dimension was requested");
        self.draw_verdicts(&mut over, time_ms, layout);
        self.draw_break_warning(&mut over, time_ms, layout);
        self.draw_overlay(&mut over, time_ms, layout);
        let paint = tiny_skia::PixmapPaint {
            opacity: interface.min(1.0),
            quality: tiny_skia::FilterQuality::Nearest,
            ..Default::default()
        };
        pixmap.draw_pixmap(0, 0, over.as_ref(), &paint, Transform::identity(), None);
    }

    /// What a frame stands on: the map's artwork when there is one, and the
    /// skin's flat background when there is not.
    ///
    /// Everywhere the play is drawn, so the artwork does not appear and vanish
    /// between the ordinary frames and the fail's. The one place it is *not*
    /// used is the base of the opening and closing fades, which is the black
    /// the whole picture — artwork included — comes up from and returns to.
    pub(super) fn ground(&self, pixmap: &mut Pixmap) {
        let Some(backdrop) = &self.backdrop else {
            pixmap.fill(self.skin.background);
            return;
        };
        pixmap.fill(self.skin.background);
        pixmap.draw_pixmap(
            0,
            0,
            backdrop.as_ref(),
            &tiny_skia::PixmapPaint::default(),
            Transform::identity(),
            None,
        );
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
    ///
    /// `close` is the field's own layout — drawn in by the camera when there is
    /// one — and `layout` is the plain one everything laid over the play keeps.
    fn draw_play(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, close: &Layout) {
        self.ground(pixmap);
        self.draw_field(pixmap, time_ms, layout, close);
        self.draw_overlay(pixmap, time_ms, layout);
    }

    /// The playfield: what the player was aiming at, and where they were.
    ///
    /// The objects and the cursor are the play, and they take `close` — the
    /// camera's layout, drawn in towards the moment. The verdicts and the break
    /// arrows are readouts *about* the play, so they take the plain `layout` and
    /// hold their size and place while the field leans in behind them.
    fn draw_field(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, close: &Layout) {

        // Under even the slider bodies: they are the map's handwriting, not
        // part of any object, and anything they crossed over would read as
        // belonging to the note it covered.
        self.draw_follow_points(pixmap, time_ms, close);
        // Then the flashes, over the trail and under every note. That is where
        // the game puts them — `OsuPlayfield` builds its layers in order:
        //
        // ```csharp
        // borderContainer, Smoke, spinnerProxies, FollowPoints, judgementLayer,
        // HitObjectContainer, judgementAboveHitObjectLayer, approachCircles
        // ```
        //
        // The mark itself climbs back over the notes through
        // `ProxiedAboveHitObjectsContent`; the light does not go with it.
        self.draw_lighting(pixmap, time_ms, close);
        // Slider bodies next, all of them, under everything else. They are a
        // layer of their own in the game — stable renders them into their own
        // buffer — and the reason is that a slider beginning a moment after a
        // note would otherwise be drawn over it, hiding the very thing the
        // player is about to hit.
        for index in self.candidates(time_ms).rev() {
            if self.alpha_of(index, time_ms) > 0.0 {
                self.draw_object_body(pixmap, index, time_ms, close);
            }
        }
        // Then the objects themselves, back to front: later notes sit
        // underneath earlier ones, so the one due next is always on top. The
        // same order lazer sorts by — "put earlier hitobjects towards the end
        // of the list", `osu.Game/Rulesets/UI/HitObjectContainer.cs`.
        for index in self.candidates(time_ms).rev() {
            if self.alpha_of(index, time_ms) > 0.0 {
                self.draw_object(pixmap, index, time_ms, close);
            }
        }
        self.draw_verdicts(pixmap, time_ms, layout);
        self.draw_break_warning(pixmap, time_ms, layout);
        self.draw_cursor(pixmap, time_ms, close);
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

