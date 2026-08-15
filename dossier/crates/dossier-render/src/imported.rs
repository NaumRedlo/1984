//! A skin as the game's own files, read off a folder.
//!
//! The engine draws everything itself. This is the other way in: the files a
//! player already has, so a render can look like the game they actually play
//! rather than like a program with opinions.
//!
//! Nothing here draws. It answers one question — *does this skin have its own
//! picture for this element, and at what scale* — and the renderer decides what
//! to do with the answer. That split is deliberate: every real skin omits
//! things, and a loader that could not say "no" would force a caller to guess
//! whether an empty canvas was a design choice or a missing file.
//!
//! Four rules, each of them learned from a real skin rather than from the wiki:
//!
//! - **`@2x` wins.** A skin may ship high-resolution art for some elements and
//!   not others, so the choice is per element, not per skin.
//! - **Names are matched without case.** Skins are made on Windows, where
//!   `HitCircle.png` and `hitcircle.png` are the same file. On this side they
//!   are not, and a skin that renders for its author would half-load for us.
//! - **Only the top of the folder is read.** osu! never looks in subfolders,
//!   and skins carry them: the one this was written against has a `cursors/`
//!   directory holding a *different* cursor from the one in use. Walking the
//!   tree would draw something its author never sees.
//! - **A blank file is not a missing file.** A fully transparent PNG is how a
//!   skin turns an element off; the same element absent means "use the default".
//!   They look identical and mean opposite things, so they are kept apart.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

use tiny_skia::{Color, Pixmap, PremultipliedColorU8};

use crate::elements::Element;

/// One picture out of a skin folder.
pub struct Sprite {
    pub pixmap: Pixmap,
    /// How many file pixels the skin drew per osu! pixel: 2 for an `@2x` file,
    /// 1 otherwise. The drawing code divides by this rather than reading the
    /// pixmap's size, because a skin is free to ship art at whatever size it
    /// likes and only the `@2x` suffix says what that size *means*.
    pub scale: f32,
}

impl Sprite {
    /// The element's width in osu! pixels, whatever the file's own size is.
    pub fn width(&self) -> f32 {
        self.pixmap.width() as f32 / self.scale
    }

    pub fn height(&self) -> f32 {
        self.pixmap.height() as f32 / self.scale
    }

    /// Whether every pixel is fully transparent — the way a skin says "do not
    /// draw this at all".
    fn is_blank(&self) -> bool {
        self.pixmap.pixels().iter().all(|p| p.alpha() == 0)
    }

    /// The same picture with the combo colour multiplied through it.
    ///
    /// The game tints some elements and not others — `hitcircle` and
    /// `approachcircle` take the combo colour, `hitcircleoverlay` and
    /// `reversearrow` keep their own — so which of these gets called is not a
    /// choice this function makes. See `Element::is_tinted`.
    ///
    /// Multiplied straight through the *premultiplied* channels, which is
    /// exactly right and looks wrong at first glance: a premultiplied channel
    /// already holds `colour x alpha`, so scaling it by the tint gives
    /// `(colour x tint) x alpha` — the premultiplied form of the tinted pixel.
    /// Unpremultiplying to tint and premultiplying back would be the same
    /// arithmetic with two roundings added.
    fn tinted(&self, tint: Color) -> Pixmap {
        let mut out = self.pixmap.clone();
        let (r, g, b) = (tint.red(), tint.green(), tint.blue());
        for pixel in out.pixels_mut() {
            let (pr, pg, pb, pa) = (pixel.red(), pixel.green(), pixel.blue(), pixel.alpha());
            *pixel = PremultipliedColorU8::from_rgba(
                (f32::from(pr) * r) as u8,
                (f32::from(pg) * g) as u8,
                (f32::from(pb) * b) as u8,
                pa,
            )
            // Scaling each channel by a factor in 0..=1 cannot lift it above
            // the alpha it started under, so the result is still a valid
            // premultiplied pixel. The fallback keeps the original rather than
            // a hole, which is the harmless half of an impossible case.
            .unwrap_or(*pixel);
        }
        out
    }
}

/// What a skin folder had to say about the elements the renderer draws.
///
/// `Debug` is written out rather than derived: a derived one would print a
/// couple of hundred thousand pixels, which is not a thing anybody wants in a
/// panic message.
pub struct Sprites {
    have: HashMap<Element, Sprite>,
    /// Elements the skin ships as an empty picture. Held apart from the ones it
    /// does not ship at all, because only one of the two means "draw nothing".
    off: HashSet<Element>,
    /// One coloured copy per combo colour, for the elements the game tints.
    ///
    /// Made once, up front, because a `Scene` is built on one thread and drawn
    /// on several: anything worked out lazily while drawing would need a lock
    /// around it, and a lock on the hot path of every note is a worse price
    /// than a few hundred kilobytes held for the length of a render. A map has
    /// a handful of combo colours, so this is bounded by the map rather than by
    /// the play.
    tinted: HashMap<(Element, usize), Pixmap>,
}

impl std::fmt::Debug for Sprites {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Sprites")
            .field("pictures", &self.have.len())
            .field("turned off", &self.off.len())
            .field("tinted copies", &self.tinted.len())
            .finish()
    }
}

impl Sprites {
    /// Read every element the renderer knows about, out of `root`.
    ///
    /// Missing files are not errors and neither is an unreadable one: a skin is
    /// somebody else's folder, and the engine's own drawing is a complete
    /// answer for anything it cannot use. What comes back is what was found.
    pub fn read(root: &Path, wanted: &[Element]) -> Self {
        let index = index_of(root);
        let mut have = HashMap::new();
        let mut off = HashSet::new();

        for &element in wanted {
            let stem = element.stem().to_ascii_lowercase();
            // `@2x` first: a skin that ships both means the plain one for the
            // game's low-resolution mode, which we are not in.
            let found = index
                .get(&format!("{stem}@2x.png"))
                .map(|p| (p, 2.0))
                .or_else(|| index.get(&format!("{stem}.png")).map(|p| (p, 1.0)));
            let Some((path, scale)) = found else { continue };
            let Some(pixmap) = fs::read(path).ok().and_then(|b| Pixmap::decode_png(&b).ok())
            else {
                continue;
            };
            let sprite = Sprite { pixmap, scale };
            if sprite.is_blank() {
                off.insert(element);
            } else {
                have.insert(element, sprite);
            }
        }
        Self {
            have,
            off,
            tinted: HashMap::new(),
        }
    }

    /// Colour the tinted elements for each of the map's combo colours.
    ///
    /// Separate from reading because the two know different things: the folder
    /// knows what pictures exist, the beatmap knows what colours they are worn
    /// in. Called once, when the skin is assembled against a map.
    pub fn tint_for(mut self, colours: &[Color]) -> Self {
        for (&element, sprite) in &self.have {
            if !element.is_tinted() {
                continue;
            }
            for (index, &colour) in colours.iter().enumerate() {
                self.tinted
                    .insert((element, index), sprite.tinted(colour));
            }
        }
        self
    }

    /// The element as it should be drawn for this combo, colour and all.
    ///
    /// An untinted element ignores the index and hands back its own picture,
    /// so the caller does not have to know which is which.
    /// Returns the picture and how many file pixels it holds per osu! pixel,
    /// because the caller needs both to put it on the field at the right size.
    pub fn coloured(&self, element: Element, combo: usize) -> Option<(&Pixmap, f32)> {
        let scale = self.have.get(&element)?.scale;
        if element.is_tinted() {
            // No entry means the colours were never worked out, which is a
            // mistake in assembling the skin rather than something to paper
            // over with an untinted picture at render time.
            return self.tinted.get(&(element, combo)).map(|p| (p, scale));
        }
        self.have.get(&element).map(|s| (&s.pixmap, scale))
    }

    /// This skin's picture for the element, if it has a usable one.
    pub fn get(&self, element: Element) -> Option<&Sprite> {
        self.have.get(&element)
    }

    /// Whether the skin deliberately turned this element off.
    pub fn silenced(&self, element: Element) -> bool {
        self.off.contains(&element)
    }

    /// Whether the renderer should draw this element itself.
    ///
    /// The whole point of keeping blank and absent apart: absent falls back to
    /// our own drawing, blank does not fall back to anything.
    pub fn draw_ourselves(&self, element: Element) -> bool {
        !self.have.contains_key(&element) && !self.off.contains(&element)
    }

    pub fn len(&self) -> usize {
        self.have.len()
    }

    pub fn is_empty(&self) -> bool {
        self.have.is_empty()
    }
}

/// Every file at the top of `root`, keyed by its lowercased name.
///
/// Built once rather than probing for each name in turn: a skin holds a couple
/// of hundred files and the renderer asks about a dozen elements, so one listing
/// beats two dozen case-insensitive searches. Subdirectories are skipped — see
/// this module's note about `cursors/`.
fn index_of(root: &Path) -> HashMap<String, PathBuf> {
    let mut index = HashMap::new();
    let Ok(entries) = fs::read_dir(root) else {
        return index;
    };
    for entry in entries.flatten() {
        if !entry.file_type().is_ok_and(|t| t.is_file()) {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
        index.insert(name, entry.path());
    }
    index
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::elements::Verdict;

    /// A square of flat white at `alpha` — which is what a skin ships for the
    /// elements the game tints, and the only source colour that can show a tint
    /// going wrong. A red fixture would come back black under a blue tint and
    /// look like a broken tint rather than a badly chosen test.
    fn write(dir: &Path, name: &str, size: u32, alpha: u8) {
        let mut pixmap = Pixmap::new(size, size).expect("a canvas");
        for pixel in pixmap.pixels_mut() {
            *pixel = tiny_skia::PremultipliedColorU8::from_rgba(alpha, alpha, alpha, alpha)
                .expect("a colour");
        }
        fs::write(dir.join(name), pixmap.encode_png().expect("png")).expect("written");
    }

    fn folder(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("dossier-skin-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("a folder");
        dir
    }

    const WANTED: &[Element] = &[
        Element::HitCircle,
        Element::ApproachCircle,
        Element::CursorTrail,
        Element::Verdict(Verdict::Three),
    ];

    #[test]
    fn a_skin_that_has_the_picture_hands_it_over() {
        let dir = folder("plain");
        write(&dir, "hitcircle.png", 128, 255);
        let sprites = Sprites::read(&dir, WANTED);

        let sprite = sprites.get(Element::HitCircle).expect("it was there");
        assert_eq!(sprite.scale, 1.0);
        assert_eq!(sprite.width(), 128.0);
        assert!(!sprites.draw_ourselves(Element::HitCircle));
    }

    #[test]
    fn the_high_resolution_file_wins_and_says_what_its_size_means() {
        // A 256px `@2x` file and a 128px plain one are the same element at the
        // same size; only the suffix says so. Reading the pixmap's own width
        // would draw this one twice as large as the skin intended.
        let dir = folder("at2x");
        write(&dir, "hitcircle.png", 128, 255);
        write(&dir, "hitcircle@2x.png", 256, 255);
        let sprites = Sprites::read(&dir, WANTED);

        let sprite = sprites.get(Element::HitCircle).expect("it was there");
        assert_eq!(sprite.scale, 2.0);
        assert_eq!(sprite.pixmap.width(), 256);
        assert_eq!(sprite.width(), 128.0, "the same element, at the same size");
    }

    #[test]
    fn a_skin_made_on_windows_still_loads() {
        // `HitCircle.png` and `hitcircle.png` are one file there and two here.
        // A skin that renders for its author must not half-load for us.
        let dir = folder("case");
        write(&dir, "HitCircle.png", 128, 255);
        write(&dir, "ApproachCircle@2X.png", 252, 255);
        let sprites = Sprites::read(&dir, WANTED);

        assert!(sprites.get(Element::HitCircle).is_some());
        let approach = sprites.get(Element::ApproachCircle).expect("found");
        assert_eq!(approach.scale, 2.0, "the suffix counts whatever its case");
    }

    #[test]
    fn a_blank_picture_means_off_and_a_missing_one_means_default() {
        // The distinction the whole loader exists for. The skin this was
        // written against ships `cursortrail.png` as one transparent pixel,
        // which is how a skin turns the trail off — while saying nothing at all
        // about `hit300` is how it leaves that to the game.
        let dir = folder("blank");
        write(&dir, "cursortrail.png", 1, 0);
        let sprites = Sprites::read(&dir, WANTED);

        assert!(sprites.silenced(Element::CursorTrail));
        assert!(sprites.get(Element::CursorTrail).is_none());
        assert!(
            !sprites.draw_ourselves(Element::CursorTrail),
            "it was turned off, not left out"
        );

        let missing = Element::Verdict(Verdict::Three);
        assert!(!sprites.silenced(missing));
        assert!(
            sprites.draw_ourselves(missing),
            "nothing was said about it, so it is ours to draw"
        );
    }

    #[test]
    fn a_picture_with_colour_under_a_zero_alpha_is_still_off() {
        // Not a hypothetical. The skin this was written against ships a
        // `hitcircle.png` whose colour channels run 119..255 and whose alpha is
        // zero throughout — an ordinary circle that was erased by its alpha
        // rather than by deleting the pixels. It draws nothing, and a check for
        // "every channel is zero" would have called it a picture and drawn an
        // invisible note over the top of the skin's real one.
        let dir = folder("ghost");
        let mut pixmap = Pixmap::new(128, 128).expect("a canvas");
        for pixel in pixmap.pixels_mut() {
            *pixel = tiny_skia::PremultipliedColorU8::from_rgba(0, 0, 0, 0).expect("a colour");
        }
        fs::write(
            dir.join("hitcircle.png"),
            pixmap.encode_png().expect("png"),
        )
        .expect("written");

        let sprites = Sprites::read(&dir, WANTED);
        assert!(sprites.silenced(Element::HitCircle));
        assert!(!sprites.draw_ourselves(Element::HitCircle));
    }

    #[test]
    fn nothing_below_the_top_of_the_folder_is_read() {
        // Real skins carry spare copies in subdirectories — the one this was
        // written against has a `cursors/` holding a different cursor. osu!
        // never looks there, so neither may we, or the render shows a picture
        // its author has never seen.
        let dir = folder("nested");
        let nested = dir.join("cursors");
        fs::create_dir_all(&nested).expect("a folder");
        write(&nested, "hitcircle.png", 128, 255);
        let sprites = Sprites::read(&dir, WANTED);

        assert!(sprites.get(Element::HitCircle).is_none());
        assert!(sprites.draw_ourselves(Element::HitCircle));
    }

    #[test]
    fn a_tinted_element_comes_back_wearing_the_combo_colour() {
        // The skin ships `hitcircle` white so the game can colour it. Handing
        // it over untouched would leave every combo the same white.
        let dir = folder("tint");
        write(&dir, "hitcircle.png", 8, 255);
        let sprites = Sprites::read(&dir, WANTED)
            .tint_for(&[Color::from_rgba8(255, 0, 0, 255), Color::from_rgba8(0, 0, 255, 255)]);

        let (first, _) = sprites.coloured(Element::HitCircle, 0).expect("combo 0");
        let (second, _) = sprites.coloured(Element::HitCircle, 1).expect("combo 1");
        let px = |p: &Pixmap| {
            let q = p.pixels()[0];
            (q.red(), q.green(), q.blue())
        };
        assert_eq!(px(first).0, 255, "the first combo is red");
        assert_eq!(px(first).2, 0);
        assert_eq!(px(second).2, 255, "the second is blue");
        assert_eq!(px(second).0, 0);
    }

    #[test]
    fn an_untinted_element_is_handed_over_as_the_skin_drew_it() {
        // Running the palette through an element the game leaves alone applies
        // the colour twice and comes out muddy.
        let dir = folder("untinted");
        write(&dir, "reversearrow.png", 8, 255);
        let sprites = Sprites::read(&dir, &[Element::ReverseArrow])
            .tint_for(&[Color::from_rgba8(0, 255, 0, 255)]);

        let (drawn, _) = sprites.coloured(Element::ReverseArrow, 0).expect("there");
        let original = sprites.get(Element::ReverseArrow).expect("there");
        assert_eq!(drawn.pixels()[0], original.pixmap.pixels()[0]);
    }

    #[test]
    fn tinting_keeps_the_shape_the_skin_drew() {
        // Only the colour may change. An edge softened by its alpha has to stay
        // exactly as soft, or every element grows a hard rim.
        let dir = folder("alpha");
        let mut pixmap = Pixmap::new(2, 1).expect("a canvas");
        pixmap.pixels_mut()[0] =
            PremultipliedColorU8::from_rgba(128, 128, 128, 128).expect("half-lit");
        pixmap.pixels_mut()[1] = PremultipliedColorU8::from_rgba(0, 0, 0, 0).expect("clear");
        fs::write(dir.join("hitcircle.png"), pixmap.encode_png().expect("png")).expect("written");

        let sprites = Sprites::read(&dir, WANTED)
            .tint_for(&[Color::from_rgba8(255, 255, 255, 255)]);
        let (out, _) = sprites.coloured(Element::HitCircle, 0).expect("there");
        assert_eq!(out.pixels()[0].alpha(), 128, "the soft edge stays soft");
        assert_eq!(out.pixels()[1].alpha(), 0, "and the clear part stays clear");
    }

    #[test]
    fn a_folder_that_is_not_there_is_not_a_failure() {
        // A skin is somebody else's folder and may be gone, unreadable or
        // empty. Every one of those has the same answer: draw it ourselves.
        let sprites = Sprites::read(Path::new("/no/such/skin"), WANTED);
        assert!(sprites.is_empty());
        assert!(WANTED.iter().all(|&e| sprites.draw_ourselves(e)));
    }

    #[test]
    fn a_file_that_is_not_a_png_is_skipped_rather_than_fatal() {
        let dir = folder("junk");
        fs::write(dir.join("hitcircle.png"), b"not a png at all").expect("written");
        let sprites = Sprites::read(&dir, WANTED);
        assert!(sprites.draw_ourselves(Element::HitCircle));
    }
}
