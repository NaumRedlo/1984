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
    pub fn counts_match(&self) -> bool {
        self.ours == self.theirs
    }

    pub fn combo_matches(&self) -> bool {
        self.our_max_combo == self.their_max_combo
    }

    pub fn is_exact(&self) -> bool {
        self.counts_match() && self.combo_matches()
    }
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
