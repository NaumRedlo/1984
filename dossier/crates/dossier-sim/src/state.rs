//! `GameState` — what the map and the player were doing at a given instant.

use dossier_beatmap::{Beatmap, Difficulty, Point};
use dossier_replay::{HitCounts, Mods, Replay};

use crate::cursor::{Cursor, CursorTrack};
use crate::judge::{Judge, ScoreState};
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
}

impl Verification {
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
}

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
        let judge = Judge::run(&timeline, &cursor);
        Self {
            timeline,
            cursor,
            judge: Some(judge),
        }
    }

    /// Map with no replay behind it: object timings only, no cursor and no
    /// judgement. Nothing was played, so there is nothing to score — reporting
    /// a map-long miss streak would be worse than reporting nothing.
    pub fn from_beatmap(beatmap: &Beatmap, mods: Mods) -> Self {
        Self {
            timeline: Timeline::build(beatmap, mods),
            cursor: CursorTrack::new(Vec::new()),
            judge: None,
        }
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

        Snapshot {
            time_ms,
            cursor: self.cursor.sample(time_ms),
            objects,
            score: self.judge.as_ref().map(|j| j.state_at(time_ms)),
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

        judge
            .events()
            .iter()
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

    /// Our totals against the replay's own.
    pub fn verify(&self, replay: &Replay) -> Option<Verification> {
        let state = self.judge.as_ref()?.final_state();
        Some(Verification {
            ours: state.counts,
            theirs: replay.hits,
            our_max_combo: state.max_combo,
            their_max_combo: u32::from(replay.max_combo),
        })
    }

    /// Span worth rendering: from the first object's spawn to the last one's
    /// end, widened to cover the replay if it runs past either edge.
    pub fn span_ms(&self) -> (f64, f64) {
        let preempt = self.timeline.difficulty.preempt_ms();
        let map = match (self.timeline.objects.first(), self.timeline.objects.last()) {
            (Some(first), Some(last)) => (first.start_ms - preempt, last.end_ms),
            _ => (0.0, 0.0),
        };
        match self.cursor.span_ms() {
            Some((from, to)) => (map.0.min(from), map.1.max(to)),
            None => map,
        }
    }
}
