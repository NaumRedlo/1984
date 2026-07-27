//! Drawing osu! replays — phase 4 of Dossier.
//!
//! Rasterising is [`tiny_skia`]: Skia's raster backend ported to Rust, so the
//! path model, the anti-aliasing and the blending are Skia's, without a C++
//! toolchain in the build. Nothing here needs a GPU yet — a frame is a few
//! hundred filled circles and a handful of stroked paths.
//!
//! ```no_run
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! let map = dossier_beatmap::Beatmap::parse(&std::fs::read_to_string("map.osu")?)?;
//! let replay = dossier_replay::Replay::parse(&std::fs::read("replay.osr")?)?;
//! let state = dossier_sim::GameState::new(&map, &replay);
//!
//! let skin = dossier_render::Skin::with_combo_colours(map.combo_colours());
//! let scene = dossier_render::Scene::new(&state, skin);
//! let layout = dossier_render::Layout::new(1920, 1080);
//! std::fs::write("frame.png", scene.frame(31_450.0, &layout).encode_png()?)?;
//! # Ok(())
//! # }
//! ```

mod layout;
mod renderer;
mod skin;
mod text;

pub use layout::Layout;
pub use renderer::Scene;
pub use skin::{ArrowShape, Skin};
pub use text::{Align, Font, Label};
