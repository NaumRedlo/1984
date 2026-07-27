//! Judgement — deciding what the player actually hit.
//!
//! The replay stores where the cursor was and which buttons were down; it does
//! *not* store which object each click landed on. That has to be re-derived,
//! and getting it right is what separates a replay renderer from an animation.
//!
//! ## The rules being modelled
//!
//! * **Notelock.** Only the earliest un-judged object can be clicked. A click
//!   while an earlier object is still live does nothing — it doesn't leak
//!   through to the object behind.
//! * **Circles** are judged by timing error against the 300/100/50 windows, and
//!   only when the cursor is inside the circle at the moment of the press.
//! * **Slider heads** are pass/fail: inside the 50 window and on the circle
//!   counts, and the exact error doesn't change the verdict. The error is
//!   recorded anyway, because it's worth showing.
//! * **Slider bodies** are judged by tracking — a button held with the cursor
//!   inside the follow circle — sampled at each tick, each repeat, and at the
//!   tail. The slider's verdict is the fraction of its parts that landed:
//!   all → 300, half → 100, at least one → 50, none → miss.
//! * **Spinners** accumulate swept angle around the playfield centre and are
//!   judged against the rotations the difficulty demands. No button needed —
//!   osu!standard spinners are spun, not clicked.
//! * **Combo** advances on every *part*: the head, each tick, each repeat, the
//!   tail. Dropping one tick of a long slider costs the combo but still leaves
//!   a 300. The tail is the exception — it *adds* combo when it lands and
//!   doesn't take it away when it doesn't, which is why a map can end with a
//!   pile of 100s and an intact combo.
//!
//! ## What is deliberately not modelled
//!
//! HP drain and failing, and osu!'s score number — score needs combo scaling,
//! spinner bonus and per-mod multipliers, none of which a renderer needs to
//! draw a frame. Geki and katu counts are left at zero: they're
//! per-combo-section awards, not judgements.
//!
//! The early-click "shake" used to be on this list and no longer is: a click
//! that lands on a note it cannot hit is recorded, and the renderer nudges the
//! note. What such a click *does* to that note is modelled too — inside 400ms
//! and outside the 50 window it takes the note with it.
//!
//! ## The known weak spot
//!
//! Notelock is modelled as osu! documents it — only the frontmost object can be
//! clicked — and on ordinary plays that reproduces the game exactly. It breaks
//! down on a *desynced stream*: once a few notes in a row go unhit, the pointer
//! trails the player by one note, every following click is tested against the
//! wrong object and rejected, and the run never recovers. One replay in the
//! local corpus (a 180bpm stream trainer) turns 9 real misses into 232 that way.
//!
//! Four looser rules were measured against the whole corpus — reach forward to
//! any object under the cursor, reach only past notes already due, reach past a
//! fraction of the 50 window, attribute each click to the nearest note in time.
//! Every one of them fixed that replay and cost more elsewhere, worst of all on
//! a mashed 37%-accuracy run where the loose reach invented 550 hits.
//!
//! There is a reference now. `dossier/docs/stable-fidelity.md` sets this engine
//! against danser's stable ruleset and lazer's Classic mod rule by rule: eleven
//! agree, and the three that do not are all notelock. Naming the rule stable
//! uses is not the same as having shown it helps here, so strict stays until a
//! restructure is measured over the corpus the way the four guesses were.

use std::f64::consts::{PI, TAU};

use dossier_beatmap::Point;
use dossier_replay::{HitCounts, Keys, ReplayFrame};

use crate::cursor::CursorTrack;
use crate::timeline::{TimedKind, TimedObject, Timeline};

/// The follow circle is this much wider than the hit circle.
pub const FOLLOW_CIRCLE_SCALE: f64 = 2.4;

/// osu! stops requiring tracking slightly before a slider's true end, which is
/// why letting go a hair early doesn't drop the tail.
pub const TAIL_LENIENCE_MS: f64 = 36.0;

/// How far from a note a click can be and still be an attempt at it.
///
/// Stable's `HittableRange`. Inside it a click is judged — and judged a miss if
/// it falls outside the 50 window, which takes the note with it. Outside it the
/// note is not accepting input at all, and the game answers by shaking rather
/// than by consuming anything.
pub const HITTABLE_RANGE_MS: f64 = 400.0;

/// Buttons that count as a click. Smoke doesn't.
const CLICK_KEYS: u8 = Keys::M1 | Keys::M2 | Keys::K1 | Keys::K2;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Judgement {
    /// 300.
    Great,
    /// 100.
    Ok,
    /// 50.
    Meh,
    Miss,
}

impl Judgement {
    /// Accuracy weight: 300, 100, 50 or 0.
    pub fn value(self) -> u32 {
        match self {
            Self::Great => 300,
            Self::Ok => 100,
            Self::Meh => 50,
            Self::Miss => 0,
        }
    }

    pub fn is_miss(self) -> bool {
        self == Self::Miss
    }

    fn from_flag(hit: bool) -> Self {
        if hit {
            Self::Great
        } else {
            Self::Miss
        }
    }
}

/// Which piece of an object an event belongs to.
///
/// A slider produces several: its head, its ticks and repeats, its tail, and
/// then one [`Part::Slider`] carrying the verdict for the whole thing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Part {
    Circle,
    SliderHead,
    SliderTick,
    SliderRepeat,
    SliderTail,
    /// The slider's overall verdict, assembled from its parts.
    Slider,
    Spinner,
}

impl Part {
    /// Only whole objects count toward accuracy — otherwise a slider with ten
    /// ticks would weigh ten times a circle.
    pub fn counts_for_accuracy(self) -> bool {
        matches!(self, Self::Circle | Self::Slider | Self::Spinner)
    }

    /// Every part advances the combo counter when it lands — which is why a
    /// slider is worth more combo than a circle. The exception is the slider's
    /// own summary, whose pieces already moved the counter as they happened.
    pub fn adds_combo(self) -> bool {
        !matches!(self, Self::Slider)
    }

    /// ...but the tail doesn't take the combo away when it's dropped.
    ///
    /// This asymmetry is real osu!, not an oversight: letting go a moment early
    /// costs the 300 and nothing else. It's why players finish maps with a
    /// handful of 100s and the combo still intact, and modelling the tail like
    /// a tick turns every such map into a shredded combo.
    pub fn breaks_combo(self) -> bool {
        !matches!(self, Self::Slider | Self::SliderTail)
    }
}

/// One judged thing, at the moment it was judged.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Event {
    pub time_ms: f64,
    /// Index into [`Timeline::objects`].
    pub object_index: usize,
    pub part: Part,
    pub result: Judgement,
    /// Signed timing error in milliseconds — negative is early. Present only
    /// for clicked parts; tracking and spinners have no single instant.
    pub error_ms: Option<f64>,
    pub combo_after: u32,
}

/// The counters as of some instant.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ScoreState {
    pub combo: u32,
    pub max_combo: u32,
    pub counts: HitCounts,
}

impl ScoreState {
    /// osu!standard accuracy in percent; 100 before anything is judged.
    pub fn accuracy(&self) -> f64 {
        self.counts.accuracy_std()
    }
}

/// A replay judged against a map.
#[derive(Debug, Clone)]
pub struct Judge {
    events: Vec<Event>,
    /// Clicks the game refused, as (object, when). A press that arrives before
    /// a note's window has opened hits nothing, and stable answers it by
    /// shaking the note rather than by ignoring it — which is the only thing
    /// that tells the player they were early rather than that the game missed
    /// their input.
    shakes: Vec<(usize, f64)>,
    /// `states[i]` is the score after `events[i]`, so a lookup is a binary
    /// search rather than a replay of everything before it.
    states: Vec<ScoreState>,
}

impl Judge {
    pub fn run(timeline: &Timeline, cursor: &CursorTrack) -> Self {
        let (heads, shakes) = judge_heads(timeline, cursor);
        let mut events = Vec::new();
        for (index, object) in timeline.objects.iter().enumerate() {
            build_events(timeline, cursor, index, object, heads[index], &mut events);
        }

        // Ties keep object order, which a stable sort preserves.
        events.sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));

        let mut state = ScoreState::default();
        let mut states = Vec::with_capacity(events.len());
        for event in &mut events {
            if event.result.is_miss() {
                if event.part.breaks_combo() {
                    state.combo = 0;
                }
            } else if event.part.adds_combo() {
                state.combo += 1;
                state.max_combo = state.max_combo.max(state.combo);
            }
            if event.part.counts_for_accuracy() {
                match event.result {
                    Judgement::Great => state.counts.count_300 += 1,
                    Judgement::Ok => state.counts.count_100 += 1,
                    Judgement::Meh => state.counts.count_50 += 1,
                    Judgement::Miss => state.counts.count_miss += 1,
                }
            }
            event.combo_after = state.combo;
            states.push(state);
        }

        Self {
            events,
            shakes,
            states,
        }
    }

    /// Clicks the game refused, as (object, when).
    pub fn shakes(&self) -> &[(usize, f64)] {
        &self.shakes
    }

    pub fn events(&self) -> &[Event] {
        &self.events
    }

    /// Score as of `time_ms`, counting everything judged at or before it.
    pub fn state_at(&self, time_ms: f64) -> ScoreState {
        let i = self.events.partition_point(|e| e.time_ms <= time_ms);
        if i == 0 {
            ScoreState::default()
        } else {
            self.states[i - 1]
        }
    }

    /// Score at the end of the map.
    pub fn final_state(&self) -> ScoreState {
        self.states.last().copied().unwrap_or_default()
    }

    /// Every event belonging to one object, in time order.
    pub fn events_for(&self, object_index: usize) -> impl Iterator<Item = &Event> {
        self.events
            .iter()
            .filter(move |e| e.object_index == object_index)
    }

    /// Unstable timing errors of the clicked parts, for a hit-error graph.
    pub fn errors_ms(&self) -> impl Iterator<Item = (f64, f64)> + '_ {
        self.events
            .iter()
            .filter_map(|e| e.error_ms.map(|err| (e.time_ms, err)))
    }
}

/// Whether and when an object's head was clicked.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Head {
    Hit { time_ms: f64, error_ms: f64 },
    Missed,
}

pub(crate) struct Press {
    pub time_ms: f64,
    pub pos: Point,
}

/// Newly-pressed buttons, in order.
///
/// Only the rising edge counts: holding a button through several frames is one
/// click. Two buttons going down on the same frame is also one click — osu!
/// sets M1 alongside K1 for a keyboard press, and counting both would double
/// every hit.
pub(crate) fn presses(frames: &[ReplayFrame]) -> Vec<Press> {
    let mut out = Vec::new();
    let mut previous = 0u8;
    for frame in frames {
        let held = frame.keys.0 & CLICK_KEYS;
        if held & !previous != 0 {
            out.push(Press {
                time_ms: frame.time_ms as f64,
                pos: Point {
                    x: f64::from(frame.x),
                    y: f64::from(frame.y),
                },
            });
        }
        previous = held;
    }
    out
}

/// Walk the clicks against the object list, honouring notelock.
///
/// How this compares with stable, rule by rule, is written up in
/// `dossier/docs/stable-fidelity.md` — including the three notelock rules this
/// does not model and why they cannot be expressed while a press is offered to
/// the earliest unjudged object rather than to the one under the cursor.
fn judge_heads(timeline: &Timeline, cursor: &CursorTrack) -> (Vec<Head>, Vec<(usize, f64)>) {
    let objects = &timeline.objects;
    let mut heads = vec![Head::Missed; objects.len()];
    let mut shakes = Vec::new();

    let window = timeline.difficulty.hit_window_50();
    let radius = timeline.difficulty.circle_radius();
    let mut next = 0usize;

    for press in presses(cursor.frames()) {
        // Anything whose window has closed is out of the way — it stays a miss.
        while next < objects.len() && past_it(&objects[next], press.time_ms, window) {
            next += 1;
        }
        if next >= objects.len() {
            break;
        }

        let object = &objects[next];

        // Spinners aren't clicked, and they hold the lock until they end, so a
        // click during one is simply swallowed.
        if object.is_spinner() {
            continue;
        }

        // Position decides first, as it does in the game: a click that does not
        // land on the circle neither hits it, nor misses it, nor shakes it. The
        // click also doesn't reach past this object to the one behind it — that
        // is the lock, and it costs a real play very little, but see the note on
        // desynced streams in this module's docs.
        if press.pos.distance_to(object.pos) > radius {
            continue;
        }

        let error_ms = press.time_ms - object.start_ms;
        if error_ms.abs() >= HITTABLE_RANGE_MS {
            // Far too early to be an attempt at this note. Nothing is consumed;
            // the game shakes the note to say so, and silence here would look
            // like dropped input rather than like a player who jumped the gun.
            //
            // Only once it is on screen, though. The game can only shake what
            // it is drawing, and a note still waiting to appear has nothing to
            // shake — our object list has no such notion, so it is stated here.
            if press.time_ms >= object.start_ms - timeline.difficulty.preempt_ms() {
                shakes.push((next, press.time_ms));
            }
            continue;
        }
        if error_ms <= -window {
            // On the note, close enough to be aimed at it, but before the window
            // opened. Stable judges that a miss and takes the note with it — a
            // second click cannot save it. We used to swallow the press and let
            // the note time out instead, which is more forgiving than the game.
            next += 1;
            continue;
        }

        heads[next] = Head::Hit {
            time_ms: press.time_ms,
            error_ms,
        };
        next += 1;
    }

    (heads, shakes)
}

/// Whether a click at `time_ms` arrives too late to touch this object.
///
/// The 50 window is exclusive at both ends, matching [`window_judgement`]: an
/// error of exactly the window width is outside it.
fn past_it(object: &TimedObject, time_ms: f64, window_50: f64) -> bool {
    if object.is_spinner() {
        time_ms > object.end_ms
    } else {
        time_ms >= object.start_ms + window_50
    }
}

fn build_events(
    timeline: &Timeline,
    cursor: &CursorTrack,
    index: usize,
    object: &TimedObject,
    head: Head,
    out: &mut Vec<Event>,
) {
    let difficulty = &timeline.difficulty;

    match &object.kind {
        TimedKind::Circle => {
            let (time_ms, result, error_ms) = match head {
                Head::Hit { time_ms, error_ms } => (
                    time_ms,
                    window_judgement(error_ms, difficulty),
                    Some(error_ms),
                ),
                Head::Missed => (
                    object.start_ms + difficulty.hit_window_50(),
                    Judgement::Miss,
                    None,
                ),
            };
            out.push(Event {
                time_ms,
                object_index: index,
                part: Part::Circle,
                result,
                error_ms,
                combo_after: 0,
            });
        }

        TimedKind::Slider { .. } => build_slider_events(timeline, cursor, index, object, head, out),

        TimedKind::Spinner => {
            let rotations = spinner_rotations(cursor, object.start_ms, object.end_ms);
            let required = required_spins(difficulty, object.duration_ms());
            out.push(Event {
                time_ms: object.end_ms,
                object_index: index,
                part: Part::Spinner,
                result: spinner_judgement(rotations, required),
                error_ms: None,
                combo_after: 0,
            });
        }
    }
}

fn build_slider_events(
    timeline: &Timeline,
    cursor: &CursorTrack,
    index: usize,
    object: &TimedObject,
    head: Head,
    out: &mut Vec<Event>,
) {
    let difficulty = &timeline.difficulty;

    let (head_time, head_error) = match head {
        Head::Hit { time_ms, error_ms } => (time_ms, Some(error_ms)),
        // A miss is only certain once the window shuts — but on a very short
        // slider that lands past the object itself, so clamp it to the end.
        Head::Missed => (
            (object.start_ms + difficulty.hit_window_50()).min(object.end_ms),
            None,
        ),
    };
    let head_hit = matches!(head, Head::Hit { .. });

    out.push(Event {
        time_ms: head_time,
        object_index: index,
        part: Part::SliderHead,
        result: Judgement::from_flag(head_hit),
        error_ms: head_error,
        combo_after: 0,
    });

    let mut parts_total = 1u32;
    let mut parts_hit = u32::from(head_hit);

    // The parts in the order the game meets them: ticks and reverses
    // interleaved by time, then the tail at its own leniency point.
    let mut parts: Vec<(f64, Part)> = object
        .tick_times()
        .into_iter()
        .map(|t| (t, Part::SliderTick))
        .chain(
            object
                .repeat_times()
                .into_iter()
                .map(|t| (t, Part::SliderRepeat)),
        )
        .collect();
    parts.sort_by(|a, b| a.0.total_cmp(&b.0));
    parts.push((tail_check_ms(object), Part::SliderTail));

    for (time_ms, part, hit) in track_slider(cursor, object, difficulty, &parts) {
        parts_total += 1;
        parts_hit += u32::from(hit);
        out.push(Event {
            // The tail is reported at the slider's real end, not at the
            // leniency point it was tested on: that is where it happens.
            time_ms: if part == Part::SliderTail {
                object.end_ms
            } else {
                time_ms
            },
            object_index: index,
            part,
            result: Judgement::from_flag(hit),
            error_ms: None,
            combo_after: 0,
        });
    }

    out.push(Event {
        time_ms: object.end_ms,
        object_index: index,
        part: Part::Slider,
        result: slider_judgement(parts_hit, parts_total),
        error_ms: None,
        combo_after: 0,
    });
}

/// Windows are exclusive: an error of exactly 20ms on a 20ms window is a 100,
/// not a 300.
///
/// osu! compares whole milliseconds with a strict `<`, and both frame times and
/// object times are integers, so the boundary is a real, populated value rather
/// than a measure-zero edge case. On a dense map dozens of hits land exactly on
/// it — enough to move the accuracy in the second decimal place, and invisible
/// to any test that doesn't probe the boundary itself.
fn window_judgement(error_ms: f64, difficulty: &dossier_beatmap::Difficulty) -> Judgement {
    let error = error_ms.abs();
    if error < difficulty.hit_window_300() {
        Judgement::Great
    } else if error < difficulty.hit_window_100() {
        Judgement::Ok
    } else {
        // Clicks outside the 50 window never reach here — they don't judge the
        // object at all.
        Judgement::Meh
    }
}

/// All parts → 300, half → 100, one → 50, none → miss.
fn slider_judgement(hit: u32, total: u32) -> Judgement {
    if total == 0 || hit == total {
        Judgement::Great
    } else if hit * 2 >= total {
        Judgement::Ok
    } else if hit > 0 {
        Judgement::Meh
    } else {
        Judgement::Miss
    }
}

/// When a slider's tail is decided.
///
/// Nominally 36ms before the end — but never earlier than halfway through the
/// final slide. That second clause is not a detail: on a fast map a slide can
/// be 50ms long, and a flat 36ms grace would hand the player two thirds of it
/// for free. Sliders that short are exactly where a tail is won or lost.
pub(crate) fn tail_check_ms(object: &TimedObject) -> f64 {
    let half_slide = object
        .slide_duration_ms()
        .map_or(0.0, |duration| object.start_ms + duration / 2.0);
    (object.end_ms - TAIL_LENIENCE_MS)
        .max(half_slide)
        .max(object.start_ms)
}

/// Whole turns a spinner asks for. osu! truncates, so a spinner that works out
/// to 4.9 turns is cleared by four.
pub(crate) fn required_spins(difficulty: &dossier_beatmap::Difficulty, duration_ms: f64) -> f64 {
    (difficulty.spins_per_second() * duration_ms / 1000.0).floor()
}

fn spinner_judgement(rotations: f64, required: f64) -> Judgement {
    if required <= 0.0 {
        return Judgement::Great;
    }
    let progress = rotations / required;
    if progress >= 1.0 {
        Judgement::Great
    } else if progress > 0.9 {
        Judgement::Ok
    } else if progress > 0.75 {
        Judgement::Meh
    } else {
        Judgement::Miss
    }
}

/// Walk a slider's parts the way the game does, and say which were collected.
///
/// The rule that matters, and the one a per-part check gets wrong: **the follow
/// circle only exists while a slide is already running**. To start one the
/// cursor has to come within the plain circle radius; only then does the
/// tolerance open out to 2.4 radii, and it snaps shut again the instant the
/// cursor leaves. Checking each part independently at 2.4 radii credits parts
/// the game never gives — a cursor that drifts past a slider without ever
/// touching it collects the lot.
///
/// The second rule: a part is only collected if the *current* slide began at or
/// before it. Re-entering the follow circle after a break does not retroactively
/// pick up the parts missed while outside.
///
/// Evaluation walks the slider a millisecond at a time rather than only at the
/// part times, because the game polls far faster than the parts arrive and a
/// slide can start and end between two of them. Stepping on the replay's own
/// frames instead was measured and is very slightly worse — it cost one replay
/// two verdicts and gained nothing — so the finer step stays.
fn track_slider(
    cursor: &CursorTrack,
    object: &TimedObject,
    difficulty: &dossier_beatmap::Difficulty,
    parts: &[(f64, Part)],
) -> Vec<(f64, Part, bool)> {
    let radius = difficulty.circle_radius();
    let follow = radius * FOLLOW_CIRCLE_SCALE;

    let mut sliding = false;
    let mut slide_start = f64::INFINITY;
    let mut judged = 0usize;
    let mut out = Vec::with_capacity(parts.len());

    // Every frame inside the slider, plus the part times themselves so a part
    // that falls after the last frame still gets an answer.
    let mut instants: Vec<f64> = {
        let mut v = Vec::new();
        let mut t = object.start_ms.ceil();
        while t <= object.end_ms + TAIL_LENIENCE_MS {
            v.push(t);
            t += 1.0;
        }
        v
    };
    instants.extend(parts.iter().map(|&(t, _)| t));
    instants.sort_by(f64::total_cmp);

    for now in instants {
        let allowable = match (object.ball_at(now), cursor.sample(now)) {
            (Some(ball), Some(sample)) => {
                let needed = if sliding { follow } else { radius };
                sample.keys.is_pressed() && sample.pos.distance_to(ball) <= needed
            }
            _ => false,
        };
        if allowable && !sliding {
            sliding = true;
            slide_start = now;
        }

        // One part per instant, exactly as the game retires them.
        if let Some(&(time_ms, part)) = parts.get(judged) {
            if time_ms <= now {
                out.push((time_ms, part, allowable && slide_start <= time_ms));
                judged += 1;
            }
        }

        if !allowable {
            sliding = false;
        }
    }

    // Anything left never came up: the replay stopped before the slider did.
    for &(time_ms, part) in &parts[judged.min(parts.len())..] {
        out.push((time_ms, part, false));
    }
    out
}

/// A button held with the cursor inside the follow circle.
pub(crate) fn is_tracking(
    cursor: &CursorTrack,
    object: &TimedObject,
    time_ms: f64,
    radius: f64,
) -> bool {
    let Some(ball) = object.ball_at(time_ms) else {
        return false;
    };
    let Some(sample) = cursor.sample(time_ms) else {
        return false;
    };
    sample.keys.is_pressed() && sample.pos.distance_to(ball) <= radius
}

/// Total turns swept around the playfield centre between two instants.
///
/// Angles are summed per recorded frame rather than at a fixed rate: the frames
/// *are* the resolution of the input, and each step is folded into `[-π, π]` so
/// a sample that skips more than half a turn is read the short way round rather
/// than as a huge jump.
pub(crate) fn spinner_rotations(cursor: &CursorTrack, start_ms: f64, end_ms: f64) -> f64 {
    if end_ms <= start_ms || cursor.is_empty() {
        return 0.0;
    }

    let mut positions = Vec::new();
    positions.extend(cursor.sample(start_ms).map(|c| c.pos));
    positions.extend(
        cursor
            .frames()
            .iter()
            .filter(|f| (f.time_ms as f64) > start_ms && (f.time_ms as f64) < end_ms)
            .map(|f| Point {
                x: f64::from(f.x),
                y: f64::from(f.y),
            }),
    );
    positions.extend(cursor.sample(end_ms).map(|c| c.pos));

    let centre = Point::CENTRE;
    let mut swept = 0.0;
    let mut previous: Option<f64> = None;
    for pos in positions {
        let (dx, dy) = (pos.x - centre.x, pos.y - centre.y);
        if dx.hypot(dy) < 1e-9 {
            // Dead on the centre there is no angle to speak of.
            continue;
        }
        let angle = dy.atan2(dx);
        if let Some(before) = previous {
            let mut step = angle - before;
            while step > PI {
                step -= TAU;
            }
            while step < -PI {
                step += TAU;
            }
            swept += step.abs();
        }
        previous = Some(angle);
    }

    swept / TAU
}
