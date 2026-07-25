//! `GameState` — what the map and the player were doing at a given instant.

use dossier_beatmap::{Beatmap, Difficulty};
use dossier_replay::{bits, Mods, Replay};

use crate::cursor::{Cursor, CursorTrack};
use crate::timeline::{TimedObject, Timeline};

/// One object as it appears at the queried instant.
#[derive(Debug, Clone, Copy)]
pub struct ActiveObject<'a> {
    pub object: &'a TimedObject,
    /// 0 when the object spawns, 1 when it's due. Past 1 it is being played
    /// (a slider being tracked) rather than approached.
    pub approach: f64,
    /// Slider ball position, when this is a slider currently under way.
    pub ball: Option<dossier_beatmap::Point>,
}

/// Everything the renderer needs for one instant.
#[derive(Debug, Clone)]
pub struct Snapshot<'a> {
    pub time_ms: f64,
    /// `None` before the replay's first frame or after its last — the recording
    /// simply doesn't cover that moment.
    pub cursor: Option<Cursor>,
    pub objects: Vec<ActiveObject<'a>>,
}

/// The map, the replay, and the arithmetic that ties them to a clock.
///
/// Judgement — deciding what was hit and what was missed, and so combo and
/// accuracy — is deliberately not here yet. This layer answers "what is on
/// screen and where is the cursor", which is what drawing needs; scoring is a
/// separate pass with its own rules (hit windows, notelock, slider ticks).
#[derive(Debug, Clone)]
pub struct GameState {
    timeline: Timeline,
    cursor: CursorTrack,
    mods: Mods,
}

impl GameState {
    /// Build from a parsed map and replay, applying the replay's own mods to
    /// the difficulty.
    pub fn new(beatmap: &Beatmap, replay: &Replay) -> Self {
        Self::with_mods(beatmap, replay, replay.mods)
    }

    /// Same, but with the mods stated explicitly — useful for previewing a map
    /// under mods nobody has played it with.
    pub fn with_mods(beatmap: &Beatmap, replay: &Replay, mods: Mods) -> Self {
        Self {
            timeline: Timeline::build(beatmap, apply_mods(beatmap.difficulty, mods)),
            cursor: CursorTrack::new(replay.frames.clone()),
            mods,
        }
    }

    /// Map with no replay behind it: object timings only, no cursor.
    pub fn from_beatmap(beatmap: &Beatmap, mods: Mods) -> Self {
        Self {
            timeline: Timeline::build(beatmap, apply_mods(beatmap.difficulty, mods)),
            cursor: CursorTrack::new(Vec::new()),
            mods,
        }
    }

    pub fn timeline(&self) -> &Timeline {
        &self.timeline
    }

    pub fn mods(&self) -> Mods {
        self.mods
    }

    /// Difficulty in force, after mods.
    pub fn difficulty(&self) -> &Difficulty {
        &self.timeline.difficulty
    }

    /// Rate the audio and video run at. The timeline itself stays in map time —
    /// DoubleTime doesn't move the notes, it plays the same map faster — so
    /// this is for the encoder's clock, not for object lookups.
    pub fn playback_rate(&self) -> f64 {
        self.mods.speed_multiplier()
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
        }
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

/// HardRock and Easy rewrite the difficulty; they're mutually exclusive, and
/// osu! rejects a replay carrying both.
fn apply_mods(difficulty: Difficulty, mods: Mods) -> Difficulty {
    if mods.contains(bits::HARD_ROCK) {
        difficulty.hard_rock()
    } else if mods.contains(bits::EASY) {
        difficulty.easy()
    } else {
        difficulty
    }
}
