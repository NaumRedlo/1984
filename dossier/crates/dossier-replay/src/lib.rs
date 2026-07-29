//! osu! `.osr` replay parsing — phase 1.1 of Dossier.
//!
//! Deliberately does nothing but read the format: no judgement, no simulation,
//! no rendering. Those sit on top of this, and keeping the boundary sharp means
//! the parser can be tested against byte-exact fixtures without dragging a game
//! engine along.
//!
//! ```no_run
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let bytes = std::fs::read("replay.osr")?;
//! let replay = dossier_replay::Replay::parse(&bytes)?;
//! println!("{} — {} ({:.2}%)", replay.player, replay.mods, replay.hits.accuracy_std());
//! # Ok(())
//! # }
//! ```

mod error;
mod json;
mod mods;
mod reader;
mod replay;

pub use error::{ReplayError, Result};
pub use mods::{bits, GameMode, Mods};
pub use replay::{life_points, HitCounts, Keys, LazerMod, Replay, ReplayFrame, ScoreInfo, Setting};
