//! Writing our look out as a skin osu! can actually wear.
//!
//! A skin is a folder: a `skin.ini` and, optionally, the images and sounds that
//! replace the game's own. Anything left out falls back to the default skin, so
//! a partial skin is a legal skin — which is what makes this worth doing in
//! steps rather than all at once.
//!
//! This writes the part we already have exactly: the palette, and the hit
//! sounds, which have been named `{set}-hit{sound}.wav` from the day they were
//! recorded because that is what the engine reads them by. The graphics come
//! later; until then the game draws its own over our colours.

use std::path::{Path, PathBuf};

use dossier_render::Skin;
use tiny_skia::Color;

/// The skin format version written into every file we produce.
///
/// Pinned deliberately, and the reason is a trap rather than a preference: a
/// `skin.ini` that exists *without* a `Version` is read as `1.0` — the 2007
/// format, which has no `@2x` high-resolution support at all. `latest` is worse
/// for anything distributed, since a future release can change what the skin
/// means. 2.7 is the newest documented version; everything past 2.0 shares the
/// HD support and the modern spinner, and the versions between only move
/// things in modes we do not draw.
const SKIN_VERSION: &str = "2.7";

/// The hit-sound files a skin carries, in osu!'s own naming.
const SAMPLE_SETS: [&str; 3] = ["normal", "soft", "drum"];
const SAMPLE_SOUNDS: [&str; 4] = ["normal", "whistle", "finish", "clap"];

pub struct Written {
    pub folder: PathBuf,
    pub sounds: usize,
}

/// Write `skin` into `folder` as an osu! skin, with `samples` copied in if a
/// folder of them was found.
pub fn write(skin: &Skin, name: &str, folder: &Path, samples: Option<&Path>) -> Result<Written, String> {
    std::fs::create_dir_all(folder).map_err(|e| format!("{}: {e}", folder.display()))?;
    let ini = folder.join("skin.ini");
    std::fs::write(&ini, ini_text(skin, name)).map_err(|e| format!("{}: {e}", ini.display()))?;

    let mut sounds = 0;
    if let Some(from) = samples {
        for set in SAMPLE_SETS {
            for sound in SAMPLE_SOUNDS {
                let file = format!("{set}-hit{sound}.wav");
                let source = from.join(&file);
                if source.is_file() && std::fs::copy(&source, folder.join(&file)).is_ok() {
                    sounds += 1;
                }
            }
            // The slider tick, which the engine reads by the same rule.
            let tick = format!("{set}-slidertick.wav");
            let source = from.join(&tick);
            if source.is_file() && std::fs::copy(&source, folder.join(&tick)).is_ok() {
                sounds += 1;
            }
        }
    }

    Ok(Written {
        folder: folder.to_path_buf(),
        sounds,
    })
}

/// The `skin.ini`, as text.
fn ini_text(skin: &Skin, name: &str) -> String {
    let mut out = String::new();
    out.push_str("// Written by `dossier skin`. The colours are the engine's own,\n");
    out.push_str("// so a render and a play in this skin agree about the palette.\n\n");

    out.push_str("[General]\n");
    out.push_str(&format!("Name: {name}\n"));
    out.push_str("Author: dossier\n");
    out.push_str(&format!("Version: {SKIN_VERSION}\n"));
    out.push('\n');

    out.push_str("[Colours]\n");
    // The one thing about this file that reads backwards: `Combo2` is the
    // colour shown *first* and `Combo1` the one shown *last*, so a two-colour
    // cycle is written bottom-up. Getting it the obvious way round swaps every
    // combo in the game against every combo in our renders.
    out.push_str("// Combo2 is shown first and Combo1 last — osu!'s own ordering.\n");
    let colours = &skin.combo_colours;
    for (index, colour) in colours.iter().enumerate() {
        // The first colour of ours is the game's Combo2, the second Combo3, and
        // the last of ours wraps around to Combo1.
        let slot = if index + 1 == colours.len() {
            1
        } else {
            index + 2
        };
        out.push_str(&format!("Combo{slot}: {}\n", rgb(*colour)));
    }
    out.push_str(&format!("SliderBorder: {}\n", rgb(skin.slider_border)));
    out.push('\n');

    out
}

/// A colour as osu! writes one: `r,g,b`, each 0–255.
fn rgb(colour: Color) -> String {
    let channel = |v: f32| (v * 255.0).round().clamp(0.0, 255.0) as u8;
    format!(
        "{},{},{}",
        channel(colour.red()),
        channel(colour.green()),
        channel(colour.blue())
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_combo_cycle_is_written_in_osus_backwards_order() {
        // Ours is [coral, sand] — coral first. osu! shows Combo2 first and
        // Combo1 last, so coral has to land in Combo2 and sand in Combo1. The
        // obvious mapping would swap every combo colour in the game against
        // every combo colour in our own renders.
        let text = ini_text(&Skin::nineteen_eightyfour(), "1984");
        let combo2 = text.lines().find(|l| l.starts_with("Combo2:")).unwrap();
        let combo1 = text.lines().find(|l| l.starts_with("Combo1:")).unwrap();
        assert_eq!(combo2, "Combo2: 226,72,72", "the coral is shown first");
        assert_eq!(combo1, "Combo1: 205,150,80", "the sand is shown last");
    }

    #[test]
    fn the_version_is_pinned_rather_than_left_to_the_game() {
        // A `skin.ini` with no Version at all is read as 1.0 — the format from
        // before high-resolution elements existed — and `latest` lets a future
        // release change what this skin means. Neither is a thing to ship.
        let text = ini_text(&Skin::nineteen_eightyfour(), "1984");
        assert!(text.contains("Version: 2.7"), "{text}");
        assert!(!text.contains("latest"));
    }

    #[test]
    fn a_colour_is_written_as_three_bytes() {
        assert_eq!(rgb(Color::from_rgba8(226, 72, 72, 255)), "226,72,72");
        assert_eq!(rgb(Color::from_rgba8(0, 0, 0, 255)), "0,0,0");
        assert_eq!(rgb(Color::from_rgba8(255, 255, 255, 255)), "255,255,255");
    }
}
