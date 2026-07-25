//! Playback state — phase 2 of Dossier.
//!
//! Ties a parsed beatmap to a parsed replay and answers, for any instant in map
//! time: where the cursor was, which objects are on screen, how far into their
//! approach they are, and where a slider's ball is.
//!
//! What it does **not** do yet is judge. Combo and accuracy need hit windows,
//! notelock, slider ticks and spinner spins — a body of rules with its own
//! edge cases, and mixing it into the drawing path would make both harder to
//! get right. The replay's final totals are available from `Replay::hits` in
//! the meantime.
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
//! println!("{} object(s) on screen", frame.objects.len());
//! # Ok(())
//! # }
//! ```

mod cursor;
mod state;
mod timeline;

pub use cursor::{Cursor, CursorTrack};
pub use state::{ActiveObject, GameState, Snapshot};
pub use timeline::{TimedKind, TimedObject, Timeline};
