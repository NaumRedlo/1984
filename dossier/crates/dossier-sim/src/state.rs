//! `GameState` — what the map and the player were doing at a given instant.

use dossier_beatmap::{Beatmap, Difficulty, Point};
use dossier_replay::{HitCounts, Mods, Replay};

use crate::cursor::{Cursor, CursorTrack};
use crate::judge::{Event, Judge, Judgement, Part, ScoreState, Verdict};
use crate::ruleset::Ruleset;
use crate::timeline::{TimedObject, Timeline};

/// One object as it appears at the queried instant.
#[derive(Debug, Clone, Copy)]
pub struct ActiveObject<'a> {
    pub object: &'a TimedObject,
    /// 0 when the object spawns, 1 when it's due. Past 1 it is being played
    /// (a slider being tracked) rather than approached.
    pub approach: f64,
    /// Slider ball position, when this is a slider currently under way.
    pub ball: Option<Point>,
}

/// Everything the renderer needs for one instant.
#[derive(Debug, Clone)]
pub struct Snapshot<'a> {
    pub time_ms: f64,
    /// `None` before the replay's first frame or after its last — the recording
    /// simply doesn't cover that moment.
    pub cursor: Option<Cursor>,
    pub objects: Vec<ActiveObject<'a>>,
    /// Combo, accuracy and hit counts as of this instant. `None` when there is
    /// no replay to judge.
    pub score: Option<ScoreState>,
}

/// How our judgement compares with the totals the replay carries in its header.
///
/// The header is ground truth — osu! wrote it — so this is the honest way to
/// check the simulation against a real score.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Verification {
    pub ours: HitCounts,
    pub theirs: HitCounts,
    pub our_max_combo: u32,
    pub their_max_combo: u32,
    /// Objects in the map.
    pub objects: usize,
    /// Objects the play reached, which the header knows because its four
    /// counts name one object each. Short of [`objects`](Self::objects) when
    /// the player died partway and osu! stopped judging there.
    pub judged: usize,
}

impl Verification {
    /// Whether the play reached the end of the map.
    ///
    /// When it didn't, both sides here are counted over the objects it did
    /// reach — the rest of the map was never presented to the player, and
    /// judging it would compare our invented misses against nothing.
    pub fn finished(&self) -> bool {
        self.judged >= self.objects
    }

    /// Only the four judgements are compared.
    ///
    /// Geki and katu are per-combo-section awards, not judgements, and we don't
    /// compute them — comparing the whole struct would mark every replay that
    /// carries them as a mismatch and hide the numbers that do matter.
    pub fn counts_match(&self) -> bool {
        let ours = self.ours;
        let theirs = self.theirs;
        ours.count_300 == theirs.count_300
            && ours.count_100 == theirs.count_100
            && ours.count_50 == theirs.count_50
            && ours.count_miss == theirs.count_miss
    }

    pub fn combo_matches(&self) -> bool {
        self.our_max_combo == self.their_max_combo
    }

    pub fn is_exact(&self) -> bool {
        self.counts_match() && self.combo_matches()
    }
}

/// One of our misses, and what the input had to say near it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MissContext {
    pub object_index: usize,
    pub kind: &'static str,
    pub time_ms: f64,
    /// Signed offset of the nearest click, negative for early. `None` when no
    /// click landed anywhere near this object.
    pub press_dt_ms: Option<f64>,
    /// How far that click was from the object's centre.
    pub press_distance_px: Option<f64>,
    /// What it needed to be inside.
    pub radius_px: f64,
    /// Spinners only: turns the player actually swept, and the number the
    /// difficulty demanded. A failed spinner says nothing about clicks, so
    /// these are the only numbers that can explain one.
    pub spin_rotations: Option<f64>,
    pub spin_required: Option<f64>,
}

impl MissContext {
    /// A click close in time that landed just outside the circle — the
    /// signature of the object being in the wrong place, not of a bad player.
    pub fn looks_like_a_geometry_error(&self) -> bool {
        matches!(
            (self.press_dt_ms, self.press_distance_px),
            (Some(dt), Some(distance))
                if dt.abs() <= 100.0 && distance > self.radius_px && distance < self.radius_px * 2.0
        )
    }
}

/// One press, and the object the judgement had in front of it at the time.
///
/// The bare trace says a click was refused; this says refused *by what*, how
/// late it was and how far off the note it landed. Every judgement question so
/// far has come down to those three numbers for one click — tokken's was a
/// press 1.8px outside a 45.4px circle — and reconstructing them by hand each
/// time is how the same instrumentation got written and deleted twice.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PressDetail {
    pub time_ms: f64,
    pub verdict: Verdict,
    /// The object the press was tested against, when there was one.
    pub object_index: Option<usize>,
    pub object_ms: Option<f64>,
    /// Press minus object time — negative is early.
    pub error_ms: Option<f64>,
    /// Where the cursor was, against the object's centre.
    pub distance_px: Option<f64>,
    pub radius_px: f64,
    /// On a refusal, the unjudged object that did the blocking.
    pub blocked_by: Option<usize>,
}

/// Clicks more than this far from an object are about some other object.
const NEAR_PRESS_WINDOW_MS: f64 = 400.0;

fn kind_name(object: &TimedObject) -> &'static str {
    if object.is_spinner() {
        "spinner"
    } else if object.is_slider() {
        "slider"
    } else {
        "circle"
    }
}

fn nearest_press(presses: &[crate::judge::Press], time_ms: f64) -> Option<&crate::judge::Press> {
    presses
        .iter()
        .filter(|p| (p.time_ms - time_ms).abs() <= NEAR_PRESS_WINDOW_MS)
        .min_by(|a, b| {
            (a.time_ms - time_ms)
                .abs()
                .total_cmp(&(b.time_ms - time_ms).abs())
        })
}

/// The map, the replay, and the arithmetic that ties them to a clock.
#[derive(Debug, Clone)]
pub struct GameState {
    timeline: Timeline,
    cursor: CursorTrack,
    judge: Option<Judge>,
    /// How many of the map's objects the play actually reached. Everything
    /// this engine is *answerable* for stops here; the timeline does not, so
    /// a video of a failed run still has a map to draw.
    played: usize,
    /// Where a play that ended early ended, and the score it ended on.
    /// `None` when the play saw the whole map out.
    ending: Option<PlayEnd>,
    /// The running score, in the arithmetic of whichever client recorded the
    /// play. Absent when there is no play.
    score: Option<crate::ScoreTrack>,
    /// The bar as this engine computes it, for the replays that arrived
    /// without osu!'s own record of it. Absent when the graph made it
    /// unnecessary.
    modelled: Option<crate::HealthTrack>,
    /// The health graph the replay carries, as `(time, 0..1)`. Empty when the
    /// replay does not carry one, which about half do not.
    health: Vec<(f64, f32)>,
}

/// The last moment a play was still being judged, and its score there.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PlayEnd {
    /// The last judgement the play produced. Not the last replay frame:
    /// stable records a frame only when the input changes, so a player who
    /// has given up stops producing frames while the health bar drains, and
    /// the recording can run out over a second before the judging does.
    pub time_ms: f64,
    /// Score at that moment — the same totals `verify` compares, so the HUD
    /// and the report cannot end a failed play on different numbers.
    pub score: ScoreState,
}

/// How far a play got, read off the header's own counts.
///
/// osu! stops judging where the player died, and its four counts name one
/// object each — so their sum is the number of objects the play reached. A
/// header carrying no counts at all is not a play that ended instantly: some
/// replays arrive that way, and for those the whole map stands.
fn objects_played(replay: &Replay, objects: usize) -> usize {
    match replay.hits.total_hits() as usize {
        0 => objects,
        judged => judged.min(objects),
    }
}

/// Where a play that ended early ended.
///
/// The moment is the last judgement it produced, which is not the same as the
/// last object's start: a slider is judged at its end, and a head nobody
/// touched is judged when its window shuts. Taking the maximum over the
/// played events is the only version of "when did this play stop" that cannot
/// land before something the player is still owed.
fn play_end(judge: &Judge, played: usize, objects: usize) -> Option<PlayEnd> {
    if played >= objects {
        return None;
    }
    let time_ms = judge
        .events()
        .iter()
        .filter(|event| event.object_index < played)
        .map(|event| event.time_ms)
        .fold(f64::NEG_INFINITY, f64::max);
    time_ms.is_finite().then(|| PlayEnd {
        time_ms,
        score: judge.state_up_to_object(played),
    })
}

/// How long the frame is held before the first note appears.
const LEAD_IN_MS: f64 = 800.0;

impl GameState {
    /// Build from a parsed map and replay, applying the replay's own mods.
    pub fn new(beatmap: &Beatmap, replay: &Replay) -> Self {
        Self::with_mods(beatmap, replay, replay.mods)
    }

    /// Same, but with the mods stated explicitly — useful for previewing a map
    /// under mods nobody has played it with.
    pub fn with_mods(beatmap: &Beatmap, replay: &Replay, mods: Mods) -> Self {
        let timeline = Timeline::build(beatmap, mods);
        let cursor = CursorTrack::new(replay.frames.clone());
        // Which client wrote this replay decides which rules judge it — the
        // header carries the version, and the two rulesets genuinely differ.
        let judge = Judge::run(
            &timeline,
            &cursor,
            Ruleset::of_replay(replay),
        );
        let played = objects_played(replay, timeline.objects.len());
        let ending = play_end(&judge, played, timeline.objects.len());
        let mut health = dossier_replay::life_points(&replay.life_bar);
        // A play that ended early ended because the bar emptied — that is what
        // ending early *is* — so the curve is truncated there and pinned to
        // zero. osu!'s graph carries about a hundred samples across a whole
        // map, two seconds apart, and nothing guarantees one of them lands on
        // the death; the only failed replay in the corpus happens to have one
        // that does, so this changes nothing today and is here so the bar
        // cannot be left reading half full over a play that is over.
        // Only worth solving for when osu! did not already say. The
        // calibration is a loop over the whole map and there is no sense
        // running it to reproduce a graph we have been handed.
        let score = crate::ScoreTrack::build(
            &judge,
            beatmap,
            mods,
            Ruleset::of_replay(replay),
        );
        if let Some(end) = ending {
            health.retain(|&(at, _)| at < end.time_ms);
            health.push((end.time_ms, 0.0));
        }
        let modelled = health.is_empty().then(|| {
            crate::HealthTrack::build(
                &judge,
                &timeline,
                &beatmap.breaks,
                beatmap.format_version,
                mods,
                Ruleset::of_replay(replay),
            )
        });
        Self {
            timeline,
            cursor,
            judge: Some(judge),
            played,
            ending,
            health,
            modelled,
            score: Some(score),
        }
    }

    /// Map with no replay behind it: object timings only, no cursor and no
    /// judgement. Nothing was played, so there is nothing to score — reporting
    /// a map-long miss streak would be worse than reporting nothing.
    pub fn from_beatmap(beatmap: &Beatmap, mods: Mods) -> Self {
        let timeline = Timeline::build(beatmap, mods);
        let played = timeline.objects.len();
        Self {
            timeline,
            cursor: CursorTrack::new(Vec::new()),
            judge: None,
            played,
            ending: None,
            health: Vec::new(),
            modelled: None,
            score: None,
        }
    }

    /// Objects the play reached, and so the ones this engine is answerable
    /// for. Short of the map's own count when the player died partway.
    pub fn objects_played(&self) -> usize {
        self.played
    }

    /// Where the play ended, when it ended before the map did.
    pub fn ending(&self) -> Option<PlayEnd> {
        self.ending
    }

    /// The score as of `time_ms`.
    ///
    /// stable's and lazer's are different numbers on different scales, not two
    /// renderings of one — see [`crate::score`]. Which one this is follows the
    /// replay's own header.
    pub fn score_at(&self, time_ms: f64) -> Option<u64> {
        self.score.as_ref().map(|track| track.at(time_ms))
    }

    /// Health at `time_ms`.
    ///
    /// osu!'s own graph when the replay carries one, because that is what
    /// actually happened and no model beats a record of it; otherwise the
    /// computed one, which is why roughly half the corpus can have a bar at
    /// all. `None` only when there is no play to speak of.
    ///
    /// The two are not interchangeable and the difference shows: the graph is
    /// a little over a hundred samples across a whole map, so it ramps where
    /// the model steps. See `dossier health` for how far apart they run.
    pub fn health_at(&self, time_ms: f64) -> Option<f32> {
        if self.health.is_empty() {
            return self.modelled.as_ref().map(|track| track.at(time_ms));
        }
        let i = self.health.partition_point(|(t, _)| *t <= time_ms);
        if i == 0 {
            return Some(self.health[0].1);
        }
        let (t0, v0) = self.health[i - 1];
        let Some(&(t1, v1)) = self.health.get(i) else {
            return Some(v0);
        };
        let span = t1 - t0;
        if span <= 0.0 {
            return Some(v1);
        }
        let f = ((time_ms - t0) / span).clamp(0.0, 1.0) as f32;
        Some(v0 + (v1 - v0) * f)
    }

    /// Whether `time_ms` falls inside one of the map's breaks.
    pub fn in_break(&self, time_ms: f64) -> bool {
        self.timeline
            .breaks
            .iter()
            .any(|&(from, to)| time_ms >= from && time_ms <= to)
    }

    /// Every press with the object it was tested against, in order.
    ///
    /// The trace and the presses come from the same walk, one entry each, so
    /// they line up — which is what lets a verdict be shown next to the click
    /// that earned it.
    pub fn press_detail(&self) -> Vec<PressDetail> {
        let Some(judge) = &self.judge else {
            return Vec::new();
        };
        let presses = crate::judge::presses(self.cursor.frames());
        let radius_px = self.timeline.difficulty.circle_radius();

        judge
            .trace()
            .iter()
            .zip(&presses)
            .map(|(entry, press)| {
                let object = entry
                    .verdict
                    .object()
                    .and_then(|index| self.timeline.objects.get(index));
                PressDetail {
                    time_ms: entry.time_ms,
                    verdict: entry.verdict,
                    object_index: entry.verdict.object(),
                    object_ms: object.map(|o| o.start_ms),
                    error_ms: object.map(|o| entry.time_ms - o.start_ms),
                    distance_px: object.map(|o| {
                        let (dx, dy) = (press.pos.x - o.pos.x, press.pos.y - o.pos.y);
                        (dx * dx + dy * dy).sqrt()
                    }),
                    radius_px,
                    blocked_by: match entry.verdict {
                        Verdict::Refused { blocked_by, .. } => Some(blocked_by),
                        _ => None,
                    },
                }
            })
            .collect()
    }

    /// Judgements from the part of the map that was actually played.
    ///
    /// Every account of what the player did goes through here, so that a run
    /// that ended at 77 seconds is never explained with misses from the two
    /// minutes it never saw.
    fn played_events<'a>(&'a self, judge: &'a Judge) -> impl Iterator<Item = &'a Event> {
        judge
            .events()
            .iter()
            .filter(move |event| event.object_index < self.played)
    }

    pub fn timeline(&self) -> &Timeline {
        &self.timeline
    }

    pub fn cursor_track(&self) -> &CursorTrack {
        &self.cursor
    }

    pub fn judge(&self) -> Option<&Judge> {
        self.judge.as_ref()
    }

    pub fn mods(&self) -> Mods {
        self.timeline.mods
    }

    /// Difficulty in force, after mods.
    pub fn difficulty(&self) -> &Difficulty {
        &self.timeline.difficulty
    }

    /// Rate the audio and video run at. The timeline itself stays in map time —
    /// DoubleTime doesn't move the notes, it plays the same map faster — so
    /// this is for the encoder's clock, not for object lookups.
    pub fn playback_rate(&self) -> f64 {
        self.timeline.mods.speed_multiplier()
    }

    /// Everything on screen at `time_ms`, in map time.
    pub fn update(&self, time_ms: f64) -> Snapshot<'_> {
        let objects = self
            .timeline
            .visible_at(time_ms)
            .map(|object| ActiveObject {
                object,
                approach: self.timeline.approach_progress(object, time_ms),
                ball: object.ball_at(time_ms),
            })
            .collect();

        // Past the end of a play there is nothing left to score. The judge
        // still has verdicts out there — it walks the whole map — but they
        // belong to notes the player never saw, and letting them into the HUD
        // draws a combo and an accuracy collapsing after the player was
        // already dead.
        let score = self.judge.as_ref().map(|judge| match self.ending {
            Some(end) if time_ms >= end.time_ms => end.score,
            _ => judge.state_at(time_ms),
        });

        Snapshot {
            time_ms,
            cursor: self.cursor.sample(time_ms),
            objects,
            score,
        }
    }

    /// Every object we called a miss, with whatever the input says about it.
    ///
    /// A disagreement with the replay header says *that* the simulation is
    /// wrong; this says *how*. A miss with a click right next to it, a hair
    /// outside the circle, is a geometry problem. A miss with a click on top of
    /// it is an attribution problem — notelock, or the window. A miss with no
    /// click anywhere near it is the player's, and ours to leave alone.
    pub fn explain_misses(&self) -> Vec<MissContext> {
        let Some(judge) = &self.judge else {
            return Vec::new();
        };
        let presses = crate::judge::presses(self.cursor.frames());
        let radius = self.timeline.difficulty.circle_radius();

        self.played_events(judge)
            .filter(|e| e.part.counts_for_accuracy() && e.result.is_miss())
            .map(|event| {
                let object = &self.timeline.objects[event.object_index];
                let spinning = object.is_spinner();
                let nearest = if spinning {
                    None
                } else {
                    nearest_press(&presses, object.start_ms)
                };
                MissContext {
                    object_index: event.object_index,
                    kind: kind_name(object),
                    time_ms: object.start_ms,
                    press_dt_ms: nearest.map(|p| p.time_ms - object.start_ms),
                    press_distance_px: nearest.map(|p| p.pos.distance_to(object.pos)),
                    radius_px: radius,
                    spin_rotations: spinning.then(|| {
                        crate::judge::spinner_rotations(
                            &self.cursor,
                            object.start_ms,
                            object.end_ms,
                        )
                    }),
                    spin_required: spinning.then(|| {
                        crate::judge::required_spins(
                            &self.timeline.difficulty,
                            object.duration_ms(),
                        )
                    }),
                }
            })
            .collect()
    }

    /// Clicks we found in the replay.
    ///
    /// Worth checking against how many objects were hit: if a play landed more
    /// notes than we counted presses, the fault is in reading the input, and
    /// nothing downstream of that can be right.
    pub fn press_count(&self) -> usize {
        crate::judge::presses(self.cursor.frames()).len()
    }

    /// Combo a flawless play would reach: every part that advances the counter.
    ///
    /// This depends on nothing but the map — no replay, no judgement — so it
    /// can be checked against the figure osu! publishes for the beatmap. When
    /// the two disagree we are building sliders out of the wrong number of
    /// pieces, and no amount of tuning the tracking rules will fix that.
    pub fn max_possible_combo(&self) -> u32 {
        self.timeline
            .objects
            .iter()
            .map(|object| {
                if object.is_slider() {
                    // Head and tail, plus everything in between.
                    2 + object.tick_times().len() as u32 + object.repeat_times().len() as u32
                } else {
                    1
                }
            })
            .sum()
    }

    /// Sliders whose tail we credited *only* because of the lenience window —
    /// the player was tracking 36ms before the end but not at the end itself.
    ///
    /// These are the sliders the lenience is deciding. If we hand out more 300s
    /// than the replay says, this number is the size of the pool that could
    /// explain it; if it's far smaller than the disagreement, the lenience is
    /// innocent and the cause is elsewhere.
    pub fn lenient_tails(&self) -> usize {
        let radius = self.timeline.difficulty.circle_radius() * crate::judge::FOLLOW_CIRCLE_SCALE;
        self.timeline
            .objects
            .iter()
            .filter(|object| object.is_slider())
            .filter(|object| {
                let check = crate::judge::tail_check_ms(object);
                crate::judge::is_tracking(&self.cursor, object, check, radius)
                    && !crate::judge::is_tracking(&self.cursor, object, object.end_ms, radius)
            })
            .count()
    }

    /// Tails we credited with the cursor out near the rim of the follow circle
    /// — past 2.0 radii but inside the 2.4 we allow.
    ///
    /// The other way a tail can be credited too easily. If the disagreement
    /// with the replay is this size, the follow circle is too wide; if this is
    /// far larger, narrowing it would break more verdicts than it fixes.
    pub fn tails_near_the_rim(&self) -> usize {
        let inner = self.timeline.difficulty.circle_radius() * 2.0;
        let outer = self.timeline.difficulty.circle_radius() * crate::judge::FOLLOW_CIRCLE_SCALE;
        self.timeline
            .objects
            .iter()
            .filter(|object| object.is_slider())
            .filter(|object| {
                let check = crate::judge::tail_check_ms(object);
                crate::judge::is_tracking(&self.cursor, object, check, outer)
                    && !crate::judge::is_tracking(&self.cursor, object, check, inner)
            })
            .count()
    }
}

/// An object the game's extra combo break can have fallen on, with everything
/// needed to tell the two candidates apart: what we made of it, and whether
/// there was a click anywhere near.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Suspect {
    pub object_index: usize,
    pub kind: &'static str,
    pub time_ms: f64,
    /// Our verdict for the object as a whole. `None` if we never judged it.
    pub ours: Option<Judgement>,
    pub press_dt_ms: Option<f64>,
    pub press_distance_px: Option<f64>,
    pub radius_px: f64,
}

/// How many refusals in a row are worth calling a run rather than noise.
const REFUSAL_RUN: usize = 4;

/// Every press in the replay, counted by what became of it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct PressSummary {
    pub landed: usize,
    pub took_a_note_early: usize,
    pub refused: usize,
    pub out_of_range: usize,
    pub ignored: usize,
    pub found_nothing: usize,
    /// Where the lock refused several clicks in a row, as (when, how many).
    pub refusal_runs: Vec<(f64, usize)>,
}

impl PressSummary {
    pub fn total(&self) -> usize {
        self.landed
            + self.took_a_note_early
            + self.refused
            + self.out_of_range
            + self.ignored
            + self.found_nothing
    }
}

/// A run of combo that ended, and what ended it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ComboChain {
    pub length: u32,
    /// `INFINITY` for the run the play finished on — nothing broke that one.
    pub ended_at_ms: f64,
    pub object_index: usize,
    /// `None` for the final run, which no part ended.
    pub part: Option<Part>,
}

impl GameState {
    /// Where our combo chains end, longest chain first.
    ///
    /// A totals table says the combo disagrees; it never says where. When our
    /// chain is longer than the replay's, the game broke somewhere we did not,
    /// and the arithmetic points straight at the culprit: a break that splits
    /// our longest chain into `a` and `b` leaves the game with `max(a, b)`, so
    /// a chain we hold that is `their_max` long has its next part as the
    /// suspect. Without this the search is every object on the map.
    ///
    /// Each entry is the chain that just ended: how long it got, and the part
    /// that ended it.
    pub fn combo_chains(&self) -> Vec<ComboChain> {
        let Some(judge) = &self.judge else {
            return Vec::new();
        };
        let mut chains = Vec::new();
        let mut length = 0;
        for event in self.played_events(judge) {
            if event.result == Judgement::Miss {
                if event.part.breaks_combo() {
                    chains.push(ComboChain {
                        length,
                        ended_at_ms: event.time_ms,
                        object_index: event.object_index,
                        part: Some(event.part),
                    });
                    length = 0;
                }
            } else if event.part.adds_combo() {
                length += 1;
            }
        }
        // The run the play ended on: nothing broke it, so nothing is to blame.
        if length > 0 {
            chains.push(ComboChain {
                length,
                ended_at_ms: f64::INFINITY,
                object_index: usize::MAX,
                part: None,
            });
        }
        chains.sort_by_key(|chain| std::cmp::Reverse(chain.length));
        chains
    }

    /// The parts the game must have broken on, when our combo reads too high.
    ///
    /// One break splits our longest run of `n` into `a` and `b`, and the game
    /// is left holding `max(a, b) = their_max`. That leaves exactly two places
    /// it can have fallen — `n - their_max` parts in, or `their_max` parts in —
    /// so a disagreement over a whole map narrows to two objects to look at.
    ///
    /// Empty when our combo is not the higher one: then the game broke where we
    /// did and something else is wrong.
    pub fn combo_break_suspects(&self, their_max: u32) -> Vec<Suspect> {
        let Some(judge) = &self.judge else {
            return Vec::new();
        };
        let Some(longest) = self
            .combo_chains()
            .into_iter()
            .find(|c| c.length > their_max)
        else {
            return Vec::new();
        };

        // Walk the events again, collecting each run as it goes, and keep the
        // one that ended where the longest run did.
        let mut run: Vec<(usize, f64)> = Vec::new();
        let mut longest_run = Vec::new();
        for event in self.played_events(judge) {
            if event.result == Judgement::Miss && event.part.breaks_combo() {
                if event.object_index == longest.object_index {
                    longest_run = std::mem::take(&mut run);
                    break;
                }
                run.clear();
            } else if event.part.adds_combo() {
                run.push((event.object_index, event.time_ms));
            }
        }
        // The run that finished the map is never ended by a part, so it is only
        // reachable here as whatever is left over.
        if longest.object_index == usize::MAX {
            longest_run = run;
        }

        let presses = crate::judge::presses(self.cursor.frames());
        let radius = self.timeline.difficulty.circle_radius();
        let mut wanted = [longest.length - their_max, their_max];
        wanted.sort_unstable();
        wanted
            .iter()
            .filter_map(|&at| longest_run.get(at.saturating_sub(1) as usize).copied())
            .map(|(index, _)| {
                let object = &self.timeline.objects[index];
                let nearest = nearest_press(&presses, object.start_ms);
                Suspect {
                    object_index: index,
                    kind: kind_name(object),
                    time_ms: object.start_ms,
                    ours: judge
                        .events_for(index)
                        .find(|e| e.part.counts_for_accuracy())
                        .map(|e| e.result),
                    press_dt_ms: nearest.map(|p| p.time_ms - object.start_ms),
                    press_distance_px: nearest.map(|p| p.pos.distance_to(object.pos)),
                    radius_px: radius,
                }
            })
            .collect()
    }

    /// What became of every press, counted by kind, plus where the refusals
    /// gather.
    ///
    /// The counts add up to the number of clicks in the replay, which is what
    /// makes them worth reading: a play that scores badly can be asked which of
    /// the ways it went wrong, rather than only how much.
    ///
    /// Runs matter more than totals. Scattered refusals are a player clicking
    /// early here and there; a run of them is the note lock having lost the
    /// thread, and the timestamp says where in the replay to look.
    pub fn press_verdicts(&self) -> PressSummary {
        let Some(judge) = &self.judge else {
            return PressSummary::default();
        };
        let mut summary = PressSummary::default();
        let mut run: Option<(f64, usize)> = None;

        for entry in judge.trace() {
            match entry.verdict {
                Verdict::Landed { .. } => summary.landed += 1,
                Verdict::TookItEarly { .. } => summary.took_a_note_early += 1,
                Verdict::Refused { .. } => summary.refused += 1,
                Verdict::OutOfRange { .. } => summary.out_of_range += 1,
                Verdict::Ignored { .. } => summary.ignored += 1,
                Verdict::FoundNothing => summary.found_nothing += 1,
            }

            match (&mut run, entry.verdict) {
                (None, Verdict::Refused { .. }) => run = Some((entry.time_ms, 1)),
                (Some((_, count)), Verdict::Refused { .. }) => *count += 1,
                (Some((at, count)), _) => {
                    if *count >= REFUSAL_RUN {
                        summary.refusal_runs.push((*at, *count));
                    }
                    run = None;
                }
                (None, _) => {}
            }
        }
        if let Some((at, count)) = run {
            if count >= REFUSAL_RUN {
                summary.refusal_runs.push((at, count));
            }
        }
        summary
    }

    /// Our totals against the replay's own.
    ///
    /// A play that ended early is compared over the part that happened. osu!
    /// judged as many objects as its four counts add up to; past that the
    /// player was already dead, so the rest of the map is left out of both
    /// sides rather than scored as a few hundred misses nobody made.
    pub fn verify(&self, replay: &Replay) -> Option<Verification> {
        let judge = self.judge.as_ref()?;
        let objects = self.timeline.objects.len();
        let judged = self.played.min(objects);
        let state = if judged < objects {
            judge.state_up_to_object(judged)
        } else {
            judge.final_state()
        };
        Some(Verification {
            ours: state.counts,
            theirs: replay.hits,
            our_max_combo: state.max_combo,
            their_max_combo: u32::from(replay.max_combo),
            objects,
            judged,
        })
    }

    /// Span worth rendering: from the first object's spawn to the last one's
    /// end, widened to cover the replay if it runs past either edge.
    ///
    /// A play that ended early ends the span with it. What follows is the map
    /// going on without a player — no cursor, no judgements, a HUD frozen on
    /// numbers nobody is changing — and on the run that prompted this, two
    /// minutes of it.
    /// When the play begins and ends, in map time.
    ///
    /// The start is where the first note becomes visible, less a beat so it is
    /// not already fading in on the opening frame — *not* where the replay
    /// started recording. Those can be a minute apart on a map with a long
    /// intro, and a minute of empty playfield is a minute nobody watches.
    pub fn span_ms(&self) -> (f64, f64) {
        let preempt = self.timeline.difficulty.preempt_ms();
        let map = match (self.timeline.objects.first(), self.timeline.objects.last()) {
            (Some(first), Some(last)) => (first.start_ms - preempt, last.end_ms),
            _ => (0.0, 0.0),
        };
        // The cursor is allowed to run past the end — a replay keeps recording
        // after the last note — but not to drag the start backwards. A replay
        // begins recording well before the first note, and on a map with a
        // long intro that is a minute of an empty playfield before anything
        // happens. The play starts where the first note becomes visible, plus
        // a beat to see it coming.
        let (from, to) = match self.cursor.span_ms() {
            Some((_, cursor_to)) => (map.0 - LEAD_IN_MS, map.1.max(cursor_to)),
            None => (map.0 - LEAD_IN_MS, map.1),
        };
        match self.ending {
            Some(end) => (from, to.min(end.time_ms)),
            None => (from, to),
        }
    }
}
