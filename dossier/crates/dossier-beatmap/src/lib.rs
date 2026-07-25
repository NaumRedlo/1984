//! osu! `.osu` beatmap parsing — phase 1.2 of Dossier.
//!
//! Reads what the file says and stops there. Deriving a slider's path from its
//! control points, stacking overlapping objects, applying mods — all of that is
//! simulation, and lives above this layer.
//!
//! ```no_run
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let text = std::fs::read_to_string("map.osu")?;
//! let map = dossier_beatmap::Beatmap::parse(&text)?;
//! println!(
//!     "{} [{}] — {} objects, AR{} preempt {:.0}ms",
//!     map.metadata.title,
//!     map.metadata.version,
//!     map.object_count(),
//!     map.difficulty.approach_rate,
//!     map.difficulty.preempt_ms(),
//! );
//! # Ok(())
//! # }
//! ```

mod difficulty;
mod error;
mod hitobject;
mod parser;
mod timing;

pub use difficulty::Difficulty;
pub use error::{BeatmapError, Result};
pub use hitobject::{CurveType, HitObject, ObjectKind, Point, Slider};
pub use parser::{Beatmap, Metadata};
pub use timing::{Timing, TimingPoint, VelocityPoint};
