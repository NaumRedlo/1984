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
//! Not modelling the drain does not mean ignoring a play that ended on it. The
//! replay header says how many objects were judged, and
//! [`Judge::state_up_to_object`] scores exactly that many — see
//! [`crate::GameState::verify`]. What is missing is the ability to work out
//! *where* a player died without being told.
//!
//! The early-click "shake" used to be on this list and no longer is: a click
//! that lands on a note it cannot hit is recorded, and the renderer nudges the
//! note. What such a click *does* to that note is modelled too — inside 400ms
//! and outside the 50 window it takes the note with it.
//!
//! ## The weak spot that was, and what is left
//!
//! Notelock is modelled as osu! documents it — only the frontmost object can be
//! clicked. That used to break down badly on a *desynced stream*: once a few
//! notes in a row went unhit the pointer trailed the player by one note, every
//! following click was tested against the wrong object, and the run never
//! recovered. One replay in the corpus turned 9 real misses into 232.
//!
//! Four looser rules were measured against the whole corpus and every one of
//! them fixed that replay and cost more elsewhere — worst of all on a mashed
//! 37%-accuracy run where a loose reach invented 550 hits. None of them was the
//! answer, because none of them was the rule.
//!
//! The rule was `LegacyHitPolicy`'s strict comparison, with clicks processed
//! before the miss sweep — the two-millisecond difference recorded in
//! `dossier/docs/stable-fidelity.md`. Those replays are now exact, and the worst
//! row in the corpus is eighteen counts on a map of nine hundred objects.
//!
//! What remains is not this. Every disagreement left in the corpus is bounded by
//! the population of hits sitting within two milliseconds of a window boundary,
//! and splits evenly either side of it: the replay records whole milliseconds
//! and the game judged against an audio clock that does not. That is a property
//! of the file format, not a rule waiting to be found.

use std::f64::consts::{PI, TAU};

use dossier_beatmap::Point;
use dossier_replay::{HitCounts, Keys, ReplayFrame};

use crate::cursor::CursorTrack;
use crate::ruleset::Ruleset;
use crate::timeline::{TimedKind, TimedObject, Timeline};

/// The follow circle is this much wider than the hit circle.
pub const FOLLOW_CIRCLE_SCALE: f64 = 2.4;

/// osu! stops requiring tracking slightly before a slider's true end, which is
/// why letting go a hair early doesn't drop the tail.
pub const TAIL_LENIENCE_MS: f64 = 36.0;

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
    /// A full turn of a spinner that pays nothing. osu! scores every *second*
    /// turn and this is the other one — it still moves the health bar and the
    /// counter on screen, which is why it is an event at all.
    SpinnerSpin,
    /// A turn that pays its hundred: every second one, from the second turn on.
    SpinnerPoints,
    /// A turn past `requirement + 3`, and again only every second one. Worth
    /// eleven hundred under ScoreV1 and five hundred under ScoreV2 — the only
    /// place the two tables differ.
    SpinnerBonus,
}

impl Part {
    /// Only whole objects count toward accuracy — otherwise a slider with ten
    /// ticks would weigh ten times a circle.
    pub fn counts_for_accuracy(self) -> bool {
        matches!(self, Self::Circle | Self::Slider | Self::Spinner)
    }

    /// Every part advances the combo counter when it lands — which is why a
    /// slider is worth more combo than a circle. The exceptions are the
    /// slider's own summary, whose pieces already moved the counter as they
    /// happened, and a spinner's turns, which are worth points and never combo.
    pub fn adds_combo(self) -> bool {
        !matches!(self, Self::Slider | Self::SpinnerSpin | Self::SpinnerPoints | Self::SpinnerBonus)
    }

    /// Whether this is bonus rather than part of the scored play.
    ///
    /// `IsBonus()` — a spinner's turns, and nothing else. Under ScoreV2 they
    /// are added on top of the million rather than counted inside it.
    pub fn is_bonus(self) -> bool {
        matches!(self, Self::SpinnerSpin | Self::SpinnerPoints | Self::SpinnerBonus)
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

/// What became of one press.
///
/// Every press ends in exactly one of these, which is what makes the trace
/// worth keeping: the six counts add up to the number of clicks in the replay,
/// so a play that scores badly can be asked *which* of the ways it went wrong.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Verdict {
    /// Hit the object it landed on.
    Landed { object: usize },
    /// On the object and close enough to be an attempt, but outside the 50
    /// window — so the note went with it and a second click cannot save it.
    TookItEarly { object: usize },
    /// The note lock refused it: an earlier object is still unjudged, and
    /// `blocked_by` is which one. That name is the whole of a cascade — each
    /// refusal points at the note behind it, and following the chain back
    /// reaches the one verdict that started it.
    Refused { object: usize, blocked_by: usize },
    /// Further than the hittable range from the object under the cursor.
    OutOfRange { object: usize },
    /// The object before this one is an unjudged stacked object, so the click
    /// passes through untouched.
    Ignored { object: usize },
    /// The cursor was on nothing that could be hit.
    FoundNothing,
}

impl Verdict {
    /// The object it concerned, when it concerned one.
    pub fn object(self) -> Option<usize> {
        match self {
            Self::Landed { object }
            | Self::TookItEarly { object }
            | Self::Refused { object, .. }
            | Self::OutOfRange { object }
            | Self::Ignored { object } => Some(object),
            Self::FoundNothing => None,
        }
    }

    /// A short name, for tables.
    pub fn name(self) -> &'static str {
        match self {
            Self::Landed { .. } => "landed",
            Self::TookItEarly { .. } => "took a note early",
            Self::Refused { .. } => "refused by the lock",
            Self::OutOfRange { .. } => "out of range",
            Self::Ignored { .. } => "ignored, stacked",
            Self::FoundNothing => "found nothing",
        }
    }
}

/// One press and what became of it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PressTrace {
    pub time_ms: f64,
    pub verdict: Verdict,
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
    /// What became of every press, in order.
    trace: Vec<PressTrace>,
    /// `states[i]` is the score after `events[i]`, so a lookup is a binary
    /// search rather than a replay of everything before it.
    states: Vec<ScoreState>,
}

impl Judge {
    pub fn run(timeline: &Timeline, cursor: &CursorTrack, ruleset: Ruleset) -> Self {
        let Heads {
            heads,
            shakes,
            trace,
        } = judge_heads(timeline, cursor, ruleset);
        let mut events = Vec::new();
        for (index, object) in timeline.objects.iter().enumerate() {
            build_events(timeline, cursor, index, object, heads[index], ruleset, &mut events);
        }

        // Ties keep object order, which a stable sort preserves.
        events.sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));

        let mut state = ScoreState::default();
        let mut states = Vec::with_capacity(events.len());
        for event in &mut events {
            accrue(&mut state, event);
            event.combo_after = state.combo;
            states.push(state);
        }

        Self {
            events,
            shakes,
            trace,
            states,
        }
    }

    /// Clicks the game refused, as (object, when).
    pub fn shakes(&self) -> &[(usize, f64)] {
        &self.shakes
    }

    /// What became of every press, in order.
    ///
    /// Kept always rather than behind a flag: it is a few dozen bytes per click
    /// and it is the only account of *why* a play scored what it did. Twice now
    /// the same numbers have been obtained by instrumenting this file by hand
    /// and then deleting the instrumentation.
    pub fn trace(&self) -> &[PressTrace] {
        &self.trace
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

    /// Score counting only the map's first `objects` objects.
    ///
    /// A play can end before the map does: the player's health runs out and
    /// osu! stops judging where they died. Everything after that was never
    /// presented to them, and counting it invents misses by the hundred — a
    /// failed run at 77 seconds of a three-minute map came out 869 misses
    /// worse than its own header until this existed.
    ///
    /// The cut is by object rather than by time because that is what the
    /// header can be asked about: it says how many objects were judged, not
    /// when the play stopped. Events are filtered rather than truncated,
    /// since a slider's tail can be judged after a later circle's head.
    pub fn state_up_to_object(&self, objects: usize) -> ScoreState {
        let mut state = ScoreState::default();
        for event in self.events.iter().filter(|e| e.object_index < objects) {
            accrue(&mut state, event);
        }
        state
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

    /// Unstable rate as of `time_ms`: ten times the standard deviation of the
    /// timing errors so far.
    ///
    /// Ten times because the figure is quoted in tenths of a millisecond, which
    /// is the convention everywhere it appears and not a scaling anybody chose
    /// for its own sake.
    ///
    /// The *population* deviation, dividing by n rather than n-1: the errors
    /// are the whole play rather than a sample of a larger one, and every
    /// client that shows this figure does the same. `None` until there are two
    /// of them — a single hit has no spread, and quoting zero would read as a
    /// perfect play rather than as an unanswered question.
    pub fn unstable_rate(&self, time_ms: f64) -> Option<f64> {
        let errors: Vec<f64> = self
            .errors_ms()
            .filter(|&(at, _)| at <= time_ms)
            .map(|(_, err)| err)
            .collect();
        if errors.len() < 2 {
            return None;
        }
        let mean = errors.iter().sum::<f64>() / errors.len() as f64;
        let variance = errors
            .iter()
            .map(|err| (err - mean) * (err - mean))
            .sum::<f64>()
            / errors.len() as f64;
        Some(variance.sqrt() * 10.0)
    }
}

/// Fold one event into a running score.
///
/// The only place these rules live, so that a score over part of a play and a
/// score over all of it cannot disagree about what a dropped tail costs.
fn accrue(state: &mut ScoreState, event: &Event) {
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
}

/// Whether and when an object's head was clicked.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Head {
    Hit {
        time_ms: f64,
        error_ms: f64,
    },
    /// `at_ms` is set when the note was killed early — a click landed on a
    /// later note while this one was still unjudged, and osu! writes the miss
    /// off there and then rather than waiting for the window to shut. `None`
    /// is the ordinary case: nobody came, and the window ran out.
    Missed {
        at_ms: Option<f64>,
    },
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
/// The presses osu! makes for a player under Relax.
///
/// A Relax replay records the cursor and nothing else: the game does the
/// clicking, and it does not write those clicks into the file. On the replay
/// that showed this up — 2861 objects — there is exactly **one** press in the
/// whole recording, against 550 in an ordinary replay of similar length. Judged
/// as written, every note on the map misses.
///
/// So they have to be made here, and danser's stable path says how:
///
/// ```go
/// const leniency = 12
/// for _, o := range processed {
///     if spinner || alreadyHit { continue }
///     if time > obj.GetStartTime()-leniency { click = true }
/// }
/// cursor.LeftButton  = click && !wasLeft
/// cursor.RightButton = click &&  wasLeft
/// if click { wasLeft = !wasLeft }
/// ```
///
/// Two things in that are easy to get wrong. There is **no geometry**: the
/// cursor's position is not consulted at all, because whether the click lands
/// is the judging path's question and not this one's. And the alternation is
/// not decoration — a held button raises one edge, so swapping hands every
/// frame is what makes *every* frame a fresh press for as long as something is
/// due. Both are reproduced here by emitting one press per frame that
/// qualifies.
///
/// Twelve milliseconds of lead, and it is danser's number rather than one this
/// engine measured.
const RELAX_LEAD_MS: f64 = 12.0;

fn relax_presses(
    frames: &[ReplayFrame],
    objects: &[TimedObject],
    window_50: f64,
    radius: f64,
    lazer: bool,
) -> Vec<Press> {
    let mut out = Vec::new();
    // The earliest object that could still be due. Objects are in time order,
    // so this only ever moves forward — a linear walk rather than a search per
    // frame, on replays that run to tens of thousands of frames.
    //
    // danser's condition is "not yet hit", which is not knowable out here: the
    // judging has not run. The upper bound used instead is the one the judging
    // itself uses to give up on a note — `past_it`, the fifty window — which is
    // when the object leaves the live list danser is iterating in the first
    // place. What that costs is a few extra presses on a note already hit,
    // between the hit and the end of its window.
    let mut first = 0usize;
    for frame in frames {
        let now = f64::from(frame.time_ms as i32);
        while first < objects.len() && past_it(&objects[first], now, window_50) {
            first += 1;
        }
        let at = Point {
            x: f64::from(frame.x),
            y: f64::from(frame.y),
        };
        // The two clients ask different questions, and danser writes both out:
        //
        // ```go
        // if isLazer {
        //     if (!c2 || time <= obj.GetEndTime()) &&
        //         time >= obj.GetStartTime()-leniency &&
        //         pos.Dst(cursor.RawPosition) <= CircleRadiusL &&
        //         time-obj.GetStartTime() <= Hit50U { click = true }
        // } else if time > obj.GetStartTime()-leniency { click = true }
        // ```
        //
        // stable clicks on time alone and lets the judging decide whether it
        // landed. lazer will not click unless the cursor is already on the note
        // and the note is inside its own fifty window — so a lazer Relax play
        // clicks less and clicks later, and reading one by stable's rule hands
        // it presses the game never made.
        let due = objects[first..]
            .iter()
            .take_while(|object| object.start_ms - RELAX_LEAD_MS < now)
            .any(|object| {
                if object.is_spinner() {
                    return false;
                }
                if !lazer {
                    return true;
                }
                (!object.is_slider() || now <= object.end_ms)
                    && now >= object.start_ms - RELAX_LEAD_MS
                    && at.distance_to(object.pos) <= radius
                    && now - object.start_ms <= window_50
            });
        if due {
            out.push(Press { time_ms: now, pos: at });
        }
    }
    out
}

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
/// Modelled on stable's own rule, as `LegacyHitPolicy.CheckHittable` restores
/// it in lazer and as danser's `CanBeHitStable` implements it. The shape that
/// matters: a press is offered to **the object under the cursor**, and the lock
/// is then consulted about that object. Offering it to the earliest unjudged
/// one instead — which is what this did — cannot express the lock's own
/// exceptions, because the objects they talk about are judged by construction.
///
/// See `dossier/docs/stable-fidelity.md` for the rule-by-rule comparison.
fn judge_heads(timeline: &Timeline, cursor: &CursorTrack, ruleset: Ruleset) -> Heads {
    let objects = &timeline.objects;
    let mut heads = vec![Head::Missed { at_ms: None }; objects.len()];
    let mut judged = vec![false; objects.len()];
    let mut shakes = Vec::new();
    let mut trace = Vec::new();

    let window = timeline.difficulty.hit_window_50();
    let radius = timeline.difficulty.circle_radius();
    let preempt = timeline.difficulty.preempt_ms();
    // Everything before this has been judged, so the searches below start here
    // rather than at the beginning of the map.
    let mut first = 0usize;
    // …and everything before *this* has finished playing. A slider goes on
    // swallowing clicks after its head is judged, so `first` steps over it
    // while it is still on the playfield and a second cursor is needed.
    let mut first_live = 0usize;

    // Under Relax the game does the clicking and does not record it, so the
    // presses are made here instead — see `relax_presses`.
    let made = ruleset
        .relax
        .then(|| {
            relax_presses(
                cursor.frames(),
                objects,
                window,
                radius,
                ruleset.client() == crate::ruleset::Client::Lazer,
            )
        });
    let clicks = made.unwrap_or_else(|| presses(cursor.frames()));
    for press in clicks {
        // Anything the game had already swept up by the moment it last looked
        // is judged — a miss — and stops blocking. Not "anything whose window
        // has shut": see [`past_it`], where the difference is two milliseconds
        // and most of what this engine used to get wrong about mashed streams.
        for (index, object) in objects.iter().enumerate().skip(first) {
            if object.start_ms - preempt > press.time_ms {
                break;
            }
            if !judged[index] && past_it(object, press.time_ms, window) {
                judged[index] = true;
            }
        }
        while first < objects.len() && judged[first] {
            first += 1;
        }
        while first_live < objects.len() && objects[first_live].end_ms <= press.time_ms {
            first_live += 1;
        }
        if first >= objects.len() {
            // Everything is judged, so this click reached nothing — but it is
            // still a click, and the trace has to account for it or the counts
            // stop adding up to the number the player made.
            trace.push(PressTrace {
                time_ms: press.time_ms,
                verdict: Verdict::FoundNothing,
            });
            continue;
        }

        // The object the click landed on. Two passes, and the order matters.
        //
        // First, among the notes that would actually *take* this click — under
        // the cursor and inside their own fifty window — the nearest one. On a
        // dense stream the circles overlap almost entirely, and a player who
        // is a little late has already moved the cursor onto the next note by
        // the time they press: the click is inside both circles, 34px into the
        // one behind and 19px into the one ahead, and osu! gives it to the one
        // ahead. Taking the earlier one instead strands a note nobody will
        // ever click again, and the lock then refuses everything that follows.
        //
        // The window is what keeps this honest. Ranking every spawned note by
        // distance hands the click to whatever happens to be nearest, which on
        // a fast map is a note half a second away — 439ms, in the case that
        // first showed this up. A note only competes for a click it could be
        // judged by.
        let candidates = || {
            objects
                .iter()
                .enumerate()
                .skip(first)
                .take_while(|(_, object)| object.start_ms - preempt <= press.time_ms)
                .filter(|(index, object)| {
                    !judged[*index]
                        && !object.is_spinner()
                        && press.pos.distance_to(object.pos) <= radius
                })
        };
        let target = candidates().next().map(|(index, _)| index);
        let Some(target) = target else {
            trace.push(PressTrace {
                time_ms: press.time_ms,
                verdict: Verdict::FoundNothing,
            });
            continue;
        };

        // Stacks are exempt from the lock. A click whose predecessor is an
        // unjudged stacked object passes through untouched — neither hitting
        // nor shaking — which is stable's way of not rattling a whole pile
        // when the player is early on one of them.
        if target > 0 && objects[target - 1].stack_height > 0 && !judged[target - 1] {
            trace.push(PressTrace {
                time_ms: press.time_ms,
                verdict: Verdict::Ignored { object: target },
            });
            continue;
        }

        // A slider keeps its head's hit area alive until the whole slider is
        // judged, which is at its end:
        //
        // ```csharp
        // slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
        // ```
        //
        // The area is live for as long as the object is — from the moment it
        // spawns, not from the moment it is due. That distinction is the whole
        // rule: a slider whose head has been clicked early counts as judged to
        // the note lock, yet it sits on the playfield with a live hit area
        // swallowing whatever lands on it.
        //
        // On `yax03 - down` that is one click 362ms ahead of the next note. We
        // handed it to that note, which took it as an early miss and cost a
        // 2687-link run 352 of its links. The click never reached that note:
        // it went into the slider the player had just started.
        let swallowed = ruleset.slider_swallows_notes_beneath()
            && objects
                .iter()
                .skip(first_live)
                .take(target.saturating_sub(first_live))
                .any(|object| {
                    object.is_slider()
                        && object.start_ms - preempt <= press.time_ms
                        && object.end_ms > press.time_ms
                        && press.pos.distance_to(object.pos) <= radius
                });
        // And a spinner swallows one wherever the cursor is. Its hittability
        // test in the client is the base's time gates with the geometry taken
        // out — it uses neither the position nor the radius — so a live spinner
        // earlier in the list answers yes to any press and takes it before
        // anything behind it can. See `docs/stable-client.md`.
        let spun = ruleset.spinner_swallows_presses()
            && objects
                .iter()
                .skip(first_live)
                .take(target.saturating_sub(first_live))
                .any(|object| {
                    object.is_spinner()
                        && object.start_ms - preempt <= press.time_ms
                        && object.end_ms > press.time_ms
                });
        let swallowed = swallowed || spun;
        if swallowed {
            trace.push(PressTrace {
                time_ms: press.time_ms,
                verdict: Verdict::Ignored { object: target },
            });
            continue;
        }

        // The lock proper: an earlier unjudged object blocks only if it *ended*
        // before this one started. Objects that overlap in time do not block
        // each other, which is the part a "frontmost object only" rule cannot
        // say. Three milliseconds of slack for objects that are a hair unsnapped.
        // Which object it is, not merely that there is one: a refusal names
        // its blocker, because a cascade is read backwards from the click that
        // was refused to the note that was never judged.
        // Which earlier note, if any, stands in the way. The two clients
        // answer this very differently — see `ruleset.rs`.
        let locked = objects
            .iter()
            .enumerate()
            .skip(first)
            .take_while(|(index, _)| *index < target)
            .find(|(index, object)| {
                !judged[*index]
                    && ruleset.blocks(
                        object.end_ms,
                        object.start_ms,
                        objects[target].start_ms,
                        press.time_ms,
                    )
            })
            .map(|(index, _)| index);

        let object = &objects[target];
        let error_ms = press.time_ms - object.start_ms;
        if locked.is_some() || error_ms.abs() >= ruleset.hittable_range_ms() {
            // Refused: the note shakes and nothing is consumed. Only once it is
            // on screen, since the game can only shake what it is drawing.
            if press.time_ms >= object.start_ms - preempt {
                shakes.push((target, press.time_ms));
            }
            trace.push(PressTrace {
                time_ms: press.time_ms,
                verdict: match locked {
                    Some(blocked_by) => Verdict::Refused {
                        object: target,
                        blocked_by,
                    },
                    None => Verdict::OutOfRange { object: target },
                },
            });
            continue;
        }

        // Landing on a note writes off everything still unjudged behind it.
        //
        // osu! does not wait for those windows to shut: the combo breaks the
        // instant the player moves past a note they never hit. The difference
        // is only ever in *when*, never in what — but when is what a combo is
        // made of. On the stream trainer two notes clicked after the abandoned
        // one and before its window ran out counted into the run first, and
        // the maximum came out 66 against the header's 64.
        if ruleset.writes_off_stranded_notes() {
            for index in first..target {
                if !judged[index] && !objects[index].is_spinner() {
                    judged[index] = true;
                    heads[index] = Head::Missed {
                        at_ms: Some(press.time_ms),
                    };
                }
            }
        }
        judged[target] = true;
        trace.push(PressTrace {
            time_ms: press.time_ms,
            verdict: if error_ms > -window {
                Verdict::Landed { object: target }
            } else {
                Verdict::TookItEarly { object: target }
            },
        });
        if error_ms > -window {
            heads[target] = Head::Hit {
                time_ms: press.time_ms,
                error_ms,
            };
        }
        // …and when it is not, the note is taken anyway: within the hittable
        // range but outside the 50 window is a miss, and a second click cannot
        // save it.
    }

    Heads {
        heads,
        shakes,
        trace,
    }
}

/// Everything the click walk produced.
struct Heads {
    heads: Vec<Head>,
    shakes: Vec<(usize, f64)>,
    trace: Vec<PressTrace>,
}

/// Whether an object's window had already shut *by the time the game last
/// looked* — which is the frame before the click, not the click itself.
///
/// The distinction is the whole of the cascade. osu! runs a frame at a time,
/// and within a frame it offers the click to the objects first and only then
/// sweeps up whatever has run out of window:
///
/// ```go
/// g.UpdateClickFor(player, time)   // ← the click, against the old state
/// ...
/// g.UpdatePostFor(player, time, _) // ← and only now the misses
/// ```
///
/// So a note whose window shuts at 71057ms is still blocking a click on the
/// frame at 71060: the game has not yet been round to write it off. Testing
/// against the click's own instant frees the note early, and every press in a
/// cascade that stable refuses becomes one this engine credits.
///
/// The comparison is strict for the same reason it is in the game
/// (`time > GetEndTime() + Hit50`): a frame landing exactly on the boundary
/// has not passed it.
/// Whether the game had already written this object off **by the time it last
/// looked**, which is not the same instant as the click.
///
/// Two millisecond-sized facts, each with its own reason, and together they
/// were the whole of the Chambarising disagreement — 23 circles credited that
/// osu! called misses, and the same error on four more replays of that map.
///
/// The first is that the game's own comparison is strict:
///
/// ```go
/// if time > int64(circle.hitCircle.GetEndTime())+player.diff.Hit50 && !state.isHit {
/// ```
///
/// so the earliest millisecond at which a note can be written off is
/// `start + window50 + 1`, not `start + window50`.
///
/// The second is the order of business inside one update. Clicks are offered
/// to the objects first, and only afterwards is anything swept up:
///
/// ```go
/// controller.ruleset.UpdateClickFor(controller.cursors[i], replayTime)
/// controller.ruleset.UpdateNormalFor(controller.cursors[i], replayTime, processAhead)
/// controller.ruleset.UpdatePostFor(controller.cursors[i], replayTime, processAhead)
/// ```
///
/// — in that order at every call site. So a click is tested against the world
/// as the previous update left it, one millisecond earlier, and a note whose
/// window shut a moment ago is still standing in the way.
///
/// Neither of these is a tunable. The corpus says so plainly: at one
/// millisecond of grace the error is 114, at two it is 70, and at three it is
/// 246 — a knife edge rather than a basin, which is what a real rule looks
/// like and a fitted constant does not. The whole-frame reading, that the game
/// only sweeps when a replay frame arrives, is wrong for the same test: 16ms
/// of grace scores 1678. osu! updates far faster than a replay records.
fn past_it(object: &TimedObject, time_ms: f64, window_50: f64) -> bool {
    if object.is_spinner() {
        time_ms > object.end_ms
    } else {
        // `- 1` for the update the click did not wait for, `>` for the game's
        // own strict comparison.
        time_ms - 1.0 > object.start_ms + window_50
    }
}

fn build_events(
    timeline: &Timeline,
    cursor: &CursorTrack,
    index: usize,
    object: &TimedObject,
    head: Head,
    ruleset: Ruleset,
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
                Head::Missed { at_ms } => (
                    at_ms.unwrap_or(object.start_ms + difficulty.hit_window_50()),
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

        TimedKind::Slider { .. } => {
            build_slider_events(timeline, cursor, index, object, head, ruleset, out)
        }

        TimedKind::Spinner => {
            let turns = spinner_spin_times(cursor, object.start_ms, object.end_ms);
            let rotations = spinner_rotations(cursor, object.start_ms, object.end_ms);
            let required = required_spins(difficulty, object.duration_ms());
            // Each turn as it lands, and the ones past the requirement as
            // bonus. osu! pays for these separately from the spinner's own
            // verdict, and pays for them *while the spinner runs* — which is
            // what lets a spinner pull a dying play back from nothing.
            for (turn, at) in turns.iter().enumerate() {
                out.push(Event {
                    time_ms: *at,
                    object_index: index,
                    part: spinner_turn(turn as i64 + 1, required as i64),
                    result: Judgement::Great,
                    error_ms: None,
                    combo_after: 0,
                });
            }
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
    ruleset: Ruleset,
    out: &mut Vec<Event>,
) {
    let difficulty = &timeline.difficulty;

    let (head_time, head_error) = match head {
        Head::Hit { time_ms, error_ms } => (time_ms, Some(error_ms)),
        // A miss is only certain once the window shuts, which on a slider
        // shorter than the window is past the slider's own end.
        Head::Missed { at_ms } => (
            at_ms.unwrap_or(object.start_ms + difficulty.hit_window_50()),
            None,
        ),
    };
    let head_hit = matches!(head, Head::Hit { .. });
    // Only lazer hands the slide over from a landed head this way; stable's
    // head sits at the ball's own starting position, so the question does not
    // arise there.
    let head_time_for_tracking = match head {
        Head::Hit { time_ms, .. } if ruleset.slider_is_scored_by_its_head() => Some(time_ms),
        _ => None,
    };

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

    for (time_ms, part, hit) in track_slider(
        cursor,
        object,
        difficulty,
        &parts,
        head_time_for_tracking,
        ruleset.slider_is_scored_by_its_head(),
    )
    {
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

    // What the slider is *worth*, and the two clients do not agree on the
    // question. stable assembles it from the pieces: everything tracked is a
    // 300, half is a 100. lazer has no such judgement at all — its slider is
    // scored piece by piece, and the 300/100/50 that lands in the score is the
    // head's, judged on the ordinary windows like any circle.
    let result = if ruleset.slider_verdict_from_head() {
        let from_head = match head {
            Head::Hit { error_ms, .. } => window_judgement(error_ms, difficulty),
            Head::Missed { .. } => Judgement::Miss,
        };
        if ruleset.slider_verdict_also_needs_its_pieces() {
            score_v2_slider(slider_judgement(parts_hit, parts_total), from_head)
        } else {
            from_head
        }
    } else {
        slider_judgement(parts_hit, parts_total)
    };

    // *When* the verdict happens, which is not always the slider's end.
    //
    // Where the verdict is the head's — lazer, and stable under ScoreV2 — it is
    // decided the instant the head is struck, and lazer shows it there. Holding
    // it to the slider's end made a hundred appear seconds after the click that
    // earned it, on a slider the player had already stopped thinking about.
    //
    // Where it is assembled from the pieces, the end is right: it cannot be
    // known before the last piece has been tracked.
    let verdict_at = match head {
        Head::Hit { time_ms, .. } if ruleset.slider_verdict_from_head() => time_ms,
        _ => object.end_ms,
    };
    out.push(Event {
        time_ms: verdict_at,
        object_index: index,
        part: Part::Slider,
        result,
        error_ms: None,
        combo_after: 0,
    });
}

/// stable's ScoreV2 slider verdict, from the pieces and the head together.
///
/// `scoreV2Processor.ModifyResult`, which danser implements as osu!'s:
///
/// ```go
/// if result&Hit300 > 0 && startResult&Hit300 > 0 {
///     return Hit300
/// } else if result&(Hit300|Hit100) > 0 && startResult&(Hit300|Hit100) > 0 {
///     return Hit100
/// } else if result != Miss {
///     return Hit50
/// }
/// ```
///
/// The first two branches are taken as written. The third is not, and the
/// corpus is why.
///
/// Read literally, `result != Miss` gives a **50** to a slider whose head was
/// missed and whose body was then tracked to the end: `result` is the pieces'
/// verdict, which is high, and only `startResult` is the miss. Implemented that
/// way this replay went from eight counts out to sixteen, turning five of the
/// game's misses into fifties.
///
/// So the departure is one condition: a missed head takes the slider with it.
/// The likeliest reading is that danser's `result` is already a miss in that
/// case and the branch is unreachable rather than wrong — our pieces' verdict
/// is assembled differently and reaches it. Either way the rule below is what
/// the replays say, and the quote above is what the source says; where those
/// two disagree this file follows the replays and says so.
fn score_v2_slider(from_pieces: Judgement, from_head: Judgement) -> Judgement {
    use Judgement::{Great, Meh, Miss, Ok};
    let at_least_ok = |j: Judgement| matches!(j, Great | Ok);
    if from_pieces == Great && from_head == Great {
        Great
    } else if at_least_ok(from_pieces) && at_least_ok(from_head) {
        Ok
    } else if from_pieces != Miss && from_head != Miss {
        Meh
    } else {
        Miss
    }
}

/// Windows are exclusive: an error of exactly 20ms on a 20ms window is a 100,
/// not a 300.
///
/// osu! compares whole milliseconds with a strict `<`, and both frame times and
/// object times are integers, so the boundary is a real, populated value rather
/// than a measure-zero edge case. On a dense map dozens of hits land exactly on
/// it — enough to move the accuracy in the second decimal place, and invisible
/// to any test that doesn't probe the boundary itself.
pub(crate) fn window_judgement(error_ms: f64, difficulty: &dossier_beatmap::Difficulty) -> Judgement {
    let error = error_ms.abs();
    if error < difficulty.hit_window_300() {
        Judgement::Great
    } else if error < difficulty.hit_window_100() {
        Judgement::Ok
    } else if error < difficulty.hit_window_50() {
        Judgement::Meh
    } else {
        // A click can land on a note whose window has already shut, because the
        // game has not yet been round to write the note off — see [`past_it`].
        // When it does, the note is spent there and then:
        //
        // ```go
        // } else if int64(delta) < player.diff.Hit50 {
        //     return Hit50
        // }
        // return Miss
        // ```
        //
        // and the miss is dated to the click rather than to the end of the
        // window, which is where the player will see it.
        Judgement::Miss
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
pub fn tail_check_ms(object: &TimedObject) -> f64 {
    let half_slide = object
        .slide_duration_ms()
        .map_or(0.0, |duration| object.start_ms + duration / 2.0);
    (object.end_ms - TAIL_LENIENCE_MS)
        .max(half_slide)
        .max(object.start_ms)
}

/// Whole turns a spinner asks for. osu! truncates, so a spinner that works out
/// to 4.9 turns is cleared by four.
pub fn required_spins(difficulty: &dossier_beatmap::Difficulty, duration_ms: f64) -> f64 {
    (difficulty.spins_per_second() * duration_ms / 1000.0).floor()
}

/// What the nth turn of a spinner is worth.
///
/// ```go
/// if state.scoringRotationCount > state.requirement+3 &&
///    (state.scoringRotationCount-(state.requirement+3))%2 == 0 {
///     SpinnerBonus
/// } else if state.scoringRotationCount > 1 && state.scoringRotationCount%2 == 0 {
///     SpinnerPoints
/// } else if state.scoringRotationCount > 1 {
///     SpinnerSpin
/// }
/// ```
///
/// Far stingier than it looks from the game. Only every *second* turn pays its
/// hundred, the first pays nothing at all, and the bonus does not begin the
/// moment the requirement is met — it waits three turns more and then also
/// comes every second turn. Paying every turn instead put a whole corpus of
/// scores a fraction of a per cent over, which on a map with one spinner is
/// entirely that spinner.
///
/// The transcription is deliberate rather than derived, because danser's own
/// counter is ambiguous about its unit: `rotationCountF` accumulates
/// `|addition| / π`, which is half-turns, while `requirement` is stated in
/// whole spins. Reading it as half-turns — a hundred points on every full turn
/// — was measured against the corpus and is worse: eight replays over their
/// pinned score instead of six. So the rule is taken at face value in turns,
/// which is both what it reads like and what the replays agree with.
fn spinner_turn(turn: i64, required: i64) -> Part {
    let bonus_from = required + 3;
    if turn > bonus_from && (turn - bonus_from) % 2 == 0 {
        Part::SpinnerBonus
    } else if turn > 1 && turn % 2 == 0 {
        Part::SpinnerPoints
    } else {
        Part::SpinnerSpin
    }
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
    head_hit_ms: Option<f64>,
    tail_window: bool,
) -> Vec<(f64, Part, bool)> {
    let radius = difficulty.circle_radius();
    let follow = radius * FOLLOW_CIRCLE_SCALE;

    let mut sliding = false;
    let mut slide_start = f64::INFINITY;
    let mut judged = 0usize;
    let mut out = Vec::with_capacity(parts.len());

    // The tail's grace, under lazer only. Everything else is decided at one
    // instant; the tail is decided over a window, and lands if the player was
    // tracking at any point in it.
    //
    // ```csharp
    // case DrawableSliderTail:
    //     if (timeOffset < SliderEventGenerator.TAIL_LENIENCY) return;
    // ...
    // if (Tracking) nestedObject.HitForcefully();
    // else if (timeOffset >= 0) nestedObject.MissForcefully();
    // ```
    //
    // The miss is only written at `timeOffset >= 0`, so every frame from
    // thirty-six milliseconds early to the slider's own end is another chance.
    // Checking the first of them and no others drops a tail whose player let
    // go a moment before the end, which they are entitled to do.
    let mut tail_pending: Option<f64> = None;
    let mut tail_hit = false;

    // Every millisecond inside the slider, plus the part times themselves so a
    // part that falls after the last frame still gets an answer. Finer than
    // the game's own frames on purpose — sampling on frames instead was tried,
    // and the corpus cannot tell the two apart.
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
        // Landing the head starts the slide from the *expanded* area, not from
        // the ball itself. `SliderInputManager.PostProcessHeadJudgement`:
        //
        // ```csharp
        // if (!head.Judged || !head.Result.IsHit) return;
        // if (!IsMouseInFollowArea(true)) return;
        // ...
        // updateTracking(allTicksInRange || IsMouseInFollowArea(false));
        // ```
        //
        // It matters on a short slider hit late: by the time the click is
        // judged the ball has already travelled, and requiring the cursor to
        // be back on top of it drops a slider the player is plainly holding.
        let head_landing = head_hit_ms.is_some_and(|at| now >= at) && !sliding;
        let allowable = match (object.ball_at(now), cursor.sample(now)) {
            (Some(ball), Some(sample)) => {
                let needed = if sliding || head_landing { follow } else { radius };
                sample.keys.is_pressed() && sample.pos.distance_to(ball) <= needed
            }
            _ => false,
        };
        if allowable && !sliding {
            sliding = true;
            slide_start = now;
        }

        // One part per instant, exactly as the game retires them — except a
        // lazer tail, which is held open until the slider's own end.
        if let Some(&(time_ms, part)) = parts.get(judged) {
            if time_ms <= now {
                let landed = allowable && slide_start <= time_ms;
                if tail_window && part == Part::SliderTail {
                    tail_pending = Some(time_ms);
                    tail_hit = landed;
                } else {
                    out.push((time_ms, part, landed));
                }
                judged += 1;
            }
        }
        if let Some(at) = tail_pending {
            if now > at && now <= object.end_ms {
                tail_hit |= allowable && slide_start <= now;
            }
        }

        if !allowable {
            sliding = false;
        }
    }

    if let Some(at) = tail_pending {
        out.push((at, Part::SliderTail, tail_hit));
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
pub fn spinner_rotations(cursor: &CursorTrack, start_ms: f64, end_ms: f64) -> f64 {
    spinner_sweep(cursor, start_ms, end_ms).0
}

/// How fast a spinner is being turned, in revolutions per minute.
///
/// Measured over a window ending at `time_ms` rather than smoothed frame by
/// frame. danser carries a decaying average:
///
/// ```go
/// decay1 := math.Pow(0.9, timeDiff/FrameTime)
/// state.rpm = state.rpm*decay1 + (1.0-decay1)*(math.Abs(state.currentVelocity)*1000)/(math.Pi*2)*60
/// ```
///
/// That needs the per-frame state a live game has and a renderer does not: any
/// frame here can be drawn without the ones before it, which is what lets them
/// be drawn in parallel. A trailing window is the same quantity — turns over
/// time — read from the replay instead of accumulated, and at a fifth of a
/// second it settles about as fast as the decay does.
pub fn spinner_rpm(cursor: &CursorTrack, start_ms: f64, time_ms: f64) -> f64 {
    const WINDOW_MS: f64 = 200.0;
    let from = (time_ms - WINDOW_MS).max(start_ms);
    let span = time_ms - from;
    if span < 1.0 {
        return 0.0;
    }
    spinner_rotations(cursor, from, time_ms) / span * 60_000.0
}

/// When each full turn of a spinner was completed.
///
/// The same sweep, kept in time rather than summed away. A turn is worth a
/// hundred points and a little health at the moment it lands, and "at the
/// moment" is the whole difficulty: a spinner that carries a play back from the
/// edge does so over its four seconds, not at its end. Summing first and
/// awarding at the end would put the health where the graph is not.
pub(crate) fn spinner_spin_times(cursor: &CursorTrack, start_ms: f64, end_ms: f64) -> Vec<f64> {
    spinner_sweep(cursor, start_ms, end_ms).1
}

/// Total turns, and the instant each of them completed.
fn spinner_sweep(cursor: &CursorTrack, start_ms: f64, end_ms: f64) -> (f64, Vec<f64>) {
    if end_ms <= start_ms || cursor.is_empty() {
        return (0.0, Vec::new());
    }

    let mut samples: Vec<(f64, Point)> = Vec::new();
    samples.extend(cursor.sample(start_ms).map(|c| (start_ms, c.pos)));
    samples.extend(
        cursor
            .frames()
            .iter()
            .filter(|f| (f.time_ms as f64) > start_ms && (f.time_ms as f64) < end_ms)
            .map(|f| {
                (
                    f.time_ms as f64,
                    Point {
                        x: f64::from(f.x),
                        y: f64::from(f.y),
                    },
                )
            }),
    );
    samples.extend(cursor.sample(end_ms).map(|c| (end_ms, c.pos)));

    let centre = Point::CENTRE;
    let mut swept = 0.0;
    let mut previous: Option<(f64, f64)> = None;
    let mut turns = Vec::new();
    for (time_ms, pos) in samples {
        let (dx, dy) = (pos.x - centre.x, pos.y - centre.y);
        if dx.hypot(dy) < 1e-9 {
            // Dead on the centre there is no angle to speak of.
            continue;
        }
        let angle = dy.atan2(dx);
        if let Some((was_at, before)) = previous {
            let mut step = angle - before;
            while step > PI {
                step -= TAU;
            }
            while step < -PI {
                step += TAU;
            }
            let after = swept + step.abs();
            // Every whole turn crossed inside this step, placed where it
            // actually fell rather than at the sample that noticed it.
            let mut crossed = (swept / TAU).floor() + 1.0;
            while crossed * TAU <= after {
                let share = if after > swept {
                    (crossed * TAU - swept) / (after - swept)
                } else {
                    0.0
                };
                turns.push(was_at + (time_ms - was_at) * share);
                crossed += 1.0;
            }
            swept = after;
        }
        previous = Some((time_ms, angle));
    }

    (swept / TAU, turns)
}
