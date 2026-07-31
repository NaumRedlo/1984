//! Playback state — phase 2 of Dossier.
//!
//! Ties a parsed beatmap to a parsed replay and answers, for any instant in map
//! time: where the cursor was, which objects are on screen, how far into their
//! approach they are, and where a slider's ball is.
//!
//! It also judges: which click landed on which object, which slider ticks were
//! tracked, how far a spinner got — and from those, combo and accuracy at any
//! instant. See [`judge`] for the rules that are modelled and the ones that
//! aren't.
//!
//! ```no_run
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let map = dossier_beatmap::Beatmap::parse(&std::fs::read_to_string("map.osu")?)?;
//! let replay = dossier_replay::Replay::parse(&std::fs::read("replay.osr")?)?;
//!
//! let state = dossier_sim::GameState::new(&map, &replay);
//! let frame = state.update(31_450.0);
//! if let Some(cursor) = frame.cursor {
//!     println!("cursor at ({:.0}, {:.0})", cursor.pos.x, cursor.pos.y);
//! }
//! if let Some(score) = frame.score {
//!     println!("{}x — {:.2}%", score.combo, score.accuracy());
//! }
//! # Ok(())
//! # }
//! ```

mod cursor;
pub mod judge;
mod ruleset;
pub mod health;
pub mod multiplier;
pub mod score;
mod stacking;
mod state;
mod timeline;

pub use cursor::{Cursor, CursorTrack};
pub use judge::{
    required_spins, spinner_rotations, spinner_rpm, tail_check_ms, Event, Judge, Judgement, Part, PressTrace,
    ScoreState, Verdict,
};
pub use ruleset::{Client, Ruleset};
pub use health::{HealthTrack, DANGER_LEVEL};
pub use multiplier::{lazer_multiplier, Generation};
pub use score::ScoreTrack;
pub use state::{
    ActiveObject, ComboChain, GameState, MissContext, PlayEnd, PressDetail, PressSummary, Snapshot,
    Suspect, Verification,
};
pub use timeline::{TimedKind, TimedObject, Timeline};
