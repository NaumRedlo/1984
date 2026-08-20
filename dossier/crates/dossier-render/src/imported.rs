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
//! - **An animation's first frame counts as the element.** osu! reads
//!   `hit300-0.png`, `hit300-1.png` and so on as an animation and prefers it to
//!   the static `hit300.png`. Skins use the numbered form for things that never
//!   move — the one this was written against turns its 300s off by shipping a
//!   blank `hit300-0.png` and no `hit300.png` at all, so looking only for the
//!   static name found nothing, called it missing, and drew our own 300 over
//!   somebody else's skin.
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

/// The handful of `skin.ini` settings the renderer can act on.
///
/// A skin's ini has a hundred keys and most of them are about menus, song
/// select and the other rulesets. These are the ones that change what a play
/// looks like, and each is here because a real skin uses it.
#[derive(Debug, Clone)]
pub struct Ini {
    /// How far consecutive combo digits are pulled together, in skin pixels.
    ///
    /// Read literally, including the values that look absurd. The skin this
    /// was written against sets 160 against 160-pixel digits, which leaves no
    /// advance at all and stacks them exactly — and that is the point: each of
    /// its digits carries a whole note ring, so a two-figure combo has to come
    /// out as one ring rather than two side by side. Clamping this to
    /// something "sensible" would break the skin it was measured from.
    pub hit_circle_overlap: f32,
    /// The same, for the smaller lettering in the corners. A separate key
    /// because osu! skins the two sets separately and they are rarely drawn to
    /// the same metrics.
    pub score_overlap: f32,
    /// The same again, for the combo counter, which osu! skins apart from both.
    pub combo_overlap: f32,
    /// What a skin calls the three sets of digits it ships.
    ///
    /// A skin is free to keep them under any name — `HitCirclePrefix:
    /// numbers/hit` is a real thing — and a skin that does was, until this was
    /// read, a skin whose combo numbers we simply could not find. We looked for
    /// `default-0.png`, found nothing, and drew our own lettering instead.
    ///
    /// The defaults are osu!'s, and the third one is not a slip: the combo
    /// counter falls back to the *score* font, not to a font of its own. lazer
    /// and danser agree on all three.
    ///
    /// ```csharp
    /// case LegacyFont.HitCircle: return ...HitCirclePrefix)?.Value ?? "default";
    /// case LegacyFont.Score:     return ...ScorePrefix)?.Value ?? "score";
    /// case LegacyFont.Combo:     return ...ComboPrefix)?.Value ?? "score";
    /// ```
    pub hit_circle_prefix: String,
    pub score_prefix: String,
    pub combo_prefix: String,
    /// Combo colours the skin states for itself, which override the map's.
    ///
    /// osu! numbers these from 1 and shows `Combo2` first; they are stored
    /// here in the order they are shown, so the renderer can use them exactly
    /// as it uses a map's own.
    pub combo_colours: Vec<Color>,
    /// The rim a skin draws round its slider bodies.
    /// Frames per second for every animation in the skin.
    ///
    /// > A positive integer or `-1` to make osu! play all frames of the
    /// > animation in one second.
    ///
    /// Default `-1`, which is why it is held as the sentinel rather than
    /// resolved here: what "all frames in one second" comes to depends on how
    /// many frames the element has, which this does not know.
    pub animation_framerate: f32,
    /// > Should the cursor expand when clicked?
    ///
    /// Default `1`. Both skins to hand turn it off, which is a thing a skin is
    /// entitled to say and which this used to ignore.
    pub cursor_expand: bool,
    /// > Should the slider combo colour tint the slider ball?
    ///
    /// Default **`0`** — the ball keeps its own colours unless a skin asks
    /// otherwise. Ours tinted it always, which is the one element where the
    /// game's default is "leave it alone" and we had it the other way round.
    pub slider_ball_tint: bool,
    pub slider_border: Option<Color>,
    /// A flat colour for the body itself, in place of the combo colour.
    ///
    /// The skin this was written against asks for black, which is not a shade
    /// of any combo colour and cannot be reached by darkening one — so this has
    /// to replace the derivation rather than adjust it.
    pub slider_track: Option<Color>,
}

impl Default for Ini {
    fn default() -> Self {
        Self {
            // Minus two, which is osu!'s own default and not zero — digits
            // are drawn to overlap slightly by default, and a skin that says
            // nothing expects that. Both lazer and danser ship -2.
            hit_circle_overlap: -2.0,
            score_overlap: 0.0,
            combo_overlap: 0.0,
            hit_circle_prefix: "default".to_owned(),
            score_prefix: "score".to_owned(),
            combo_prefix: "score".to_owned(),
            combo_colours: Vec::new(),
            animation_framerate: -1.0,
            cursor_expand: true,
            slider_ball_tint: false,
            slider_border: None,
            slider_track: None,
        }
    }
}

impl Ini {
    /// Read `skin.ini` out of a skin folder. A skin without one is not an
    /// error — the defaults are what the game would have used anyway.
    pub fn read(root: &Path) -> Self {
        let index = index_of(root);
        let Some(path) = index.get("skin.ini") else {
            return Self::default();
        };
        fs::read(path)
            .ok()
            .map(|bytes| Self::parse(&String::from_utf8_lossy(&bytes)))
            .unwrap_or_default()
    }

    pub fn parse(text: &str) -> Self {
        let mut out = Self::default();
        let mut numbered: Vec<(usize, Color)> = Vec::new();
        let mut section = String::new();

        for line in text.lines() {
            let line = line.split("//").next().unwrap_or("").trim();
            if line.is_empty() {
                continue;
            }
            if let Some(name) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
                section = name.to_ascii_lowercase();
                continue;
            }
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let (key, value) = (key.trim().to_ascii_lowercase(), value.trim());

            match (section.as_str(), key.as_str()) {
                ("fonts", "hitcircleoverlap") => {
                    if let Ok(n) = value.parse::<f32>() {
                        out.hit_circle_overlap = n;
                    }
                }
                ("fonts", "scoreoverlap") => {
                    if let Ok(n) = value.parse::<f32>() {
                        out.score_overlap = n;
                    }
                }
                ("fonts", "combooverlap") => {
                    if let Ok(n) = value.parse::<f32>() {
                        out.combo_overlap = n;
                    }
                }
                // A skin naming its digit sets, kept without whatever folder it
                // named them in.
                //
                // Prefixes may carry a path — `HitCirclePrefix: numbers/hit` is
                // a real thing — and this engine cannot follow one, because a
                // skin arrives here already flattened: the importer keeps every
                // file by its bare name, since most archives wrap themselves in
                // a folder and would otherwise unpack into one the engine finds
                // empty. So both ends agree to ignore folders, and the skin's
                // digits are found under the name the file actually has.
                //
                // The price is a skin that ships two different `hit-0.png` in
                // two folders, where one overwrites the other on the way in.
                // Nothing in the store does; a skin that did would lose the
                // same file to the importer with or without this.
                ("fonts", "hitcircleprefix") => out.hit_circle_prefix = leaf_of(value),
                ("fonts", "scoreprefix") => out.score_prefix = leaf_of(value),
                ("fonts", "comboprefix") => out.combo_prefix = leaf_of(value),
                // Only osu!standard's own combo colours. `[Mania]` has a
                // `Colour1..N` of its own meaning something else entirely, and
                // reading those as combo colours would repaint every note on a
                // map from a section about a ruleset we do not draw.
                ("general", "cursorexpand") => out.cursor_expand = value != "0",
                ("general", "allowsliderballtint") => out.slider_ball_tint = value == "1",
                ("general", "animationframerate") => {
                    if let Ok(n) = value.parse::<f32>() {
                        out.animation_framerate = n;
                    }
                }
                ("colours", "sliderborder") => out.slider_border = rgb_of(value),
                ("colours", "slidertrackoverride") => out.slider_track = rgb_of(value),
                ("colours", _) if key.starts_with("combo") => {
                    if let (Ok(n), Some(colour)) =
                        (key.trim_start_matches("combo").parse::<usize>(), rgb_of(value))
                    {
                        numbered.push((n, colour));
                    }
                }
                _ => {}
            }
        }

        // `Combo2` is shown first and `Combo1` last — osu!'s own ordering, the
        // same inversion the skin exporter writes. Sorted by number and then
        // rotated, so the first colour here is the first one seen in play.
        numbered.sort_by_key(|(n, _)| *n);
        if numbered.len() > 1 {
            numbered.rotate_left(1);
        }
        out.combo_colours = numbered.into_iter().map(|(_, c)| c).collect();
        out
    }
}

/// `r,g,b` as osu! writes a colour, with an optional alpha nobody uses.
fn rgb_of(value: &str) -> Option<Color> {
    let mut parts = value.split(',').map(|p| p.trim().parse::<u8>());
    let (r, g, b) = (parts.next()?.ok()?, parts.next()?.ok()?, parts.next()?.ok()?);
    Some(Color::from_rgba8(r, g, b, 255))
}

/// The same picture with a colour multiplied through it.
///
/// Multiplied straight through the *premultiplied* channels, which is exactly
/// right and looks wrong at first glance: a premultiplied channel already holds
/// `colour x alpha`, so scaling it by the tint gives `(colour x tint) x alpha`
/// — the premultiplied form of the tinted pixel. Unpremultiplying to tint and
/// premultiplying back would be the same arithmetic with two roundings added.
///
/// Free rather than a method because three callers want it and only one of them
/// has a `Sprite`: the combo tint made up front, a HUD figure put through a
/// verdict colour, and a held key put through the colour osu! lights it in.
pub fn tinted(pixmap: &Pixmap, tint: Color) -> Pixmap {
    let mut out = pixmap.clone();
    let (r, g, b) = (tint.red(), tint.green(), tint.blue());
    for pixel in out.pixels_mut() {
        let (pr, pg, pb, pa) = (pixel.red(), pixel.green(), pixel.blue(), pixel.alpha());
        *pixel = PremultipliedColorU8::from_rgba(
            (f32::from(pr) * r) as u8,
            (f32::from(pg) * g) as u8,
            (f32::from(pb) * b) as u8,
            pa,
        )
        // Scaling each channel by a factor in 0..=1 cannot lift it above the
        // alpha it started under, so the result is still a valid premultiplied
        // pixel. The fallback keeps the original rather than a hole, which is
        // the harmless half of an impossible case.
        .unwrap_or(*pixel);
    }
    out
}

/// One picture out of a skin folder.
///
/// `Clone` because an animated element is held twice: once as the whole strip
/// and once as the frame drawn when nothing is moving. That is one spare
/// pixmap per animation, paid at load, against threading a frame index through
/// every call site that only ever wants the still one.
#[derive(Clone)]
pub struct Sprite {
    pub pixmap: Pixmap,
    /// How many file pixels the skin drew per osu! pixel: 2 for an `@2x` file,
    /// 1 otherwise. The drawing code divides by this rather than reading the
    /// pixmap's size, because a skin is free to ship art at whatever size it
    /// likes and only the `@2x` suffix says what that size *means*.
    pub scale: f32,
    /// The size of the drawn part, ignoring transparent padding, in osu!
    /// pixels.
    ///
    /// Walked once, here, rather than at every frame that wants it: a judgement
    /// is a small figure in a large empty square — up to 256 pixels of square —
    /// and that is not a scan to repeat sixty times a second.
    pub ink_width: f32,
    pub ink_height: f32,
}

impl Sprite {
    fn new(pixmap: Pixmap, scale: f32) -> Self {
        let (mut left, mut right) = (pixmap.width(), 0u32);
        let (mut top, mut bottom) = (pixmap.height(), 0u32);
        for (index, pixel) in pixmap.pixels().iter().enumerate() {
            if pixel.alpha() == 0 {
                continue;
            }
            let (x, y) = (
                index as u32 % pixmap.width(),
                index as u32 / pixmap.width(),
            );
            left = left.min(x);
            right = right.max(x);
            top = top.min(y);
            bottom = bottom.max(y);
        }
        let span = |from: u32, to: u32| {
            if from > to {
                0.0
            } else {
                (to - from + 1) as f32 / scale
            }
        };
        Self {
            ink_width: span(left, right),
            ink_height: span(top, bottom),
            pixmap,
            scale,
        }
    }

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
        tinted(&self.pixmap, tint)
    }
}

/// What a skin folder had to say about the elements the renderer draws.
///
/// `Debug` is written out rather than derived: a derived one would print a
/// couple of hundred thousand pixels, which is not a thing anybody wants in a
/// panic message.
pub struct Sprites {
    have: HashMap<Element, Sprite>,
    /// Every frame, for the elements that ship more than one.
    frames: HashMap<Element, Vec<Sprite>>,
    /// Elements the skin ships as an empty picture. Held apart from the ones it
    /// does not ship at all, because only one of the two means "draw nothing".
    off: HashSet<Element>,
    /// What the folder's `skin.ini` asked for.
    ini: Ini,
    /// How many combo colours were tinted for, so an index can be wrapped the
    /// same way the palette wraps.
    palette: usize,
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
            .field("animated", &self.frames.len())
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
        // Read here rather than by the caller: the folder walk is already done,
        // and a skin's pictures and its settings are two halves of one answer.
        let ini = index
            .get("skin.ini")
            .and_then(|p| fs::read(p).ok())
            .map(|b| Ini::parse(&String::from_utf8_lossy(&b)))
            .unwrap_or_default();
        let mut have = HashMap::new();
        let mut frames: HashMap<Element, Vec<Sprite>> = HashMap::new();
        let mut off = HashSet::new();

        for &element in wanted {
            let stem = element.stem_with(&ini).to_ascii_lowercase();
            // Animation first, then the static name, and `@2x` ahead of the
            // plain file within each — the order osu! resolves them in. Only
            // frame zero is read: nothing here animates yet, and a skin's
            // first frame is what it looks like at rest.
            let found = index
                .get(&format!("{stem}-0@2x.png"))
                .map(|p| (p, 2.0))
                .or_else(|| index.get(&format!("{stem}-0.png")).map(|p| (p, 1.0)))
                // And without the dash. osu! is not consistent about this and
                // the slider ball is the one that matters: its frames are
                // `sliderb0.png`, `sliderb1.png`, with no separator at all, so
                // a skin exported from lazer ships `sliderb0@2x.png` and the
                // dashed search above finds nothing.
                .or_else(|| index.get(&format!("{stem}0@2x.png")).map(|p| (p, 2.0)))
                .or_else(|| index.get(&format!("{stem}0.png")).map(|p| (p, 1.0)))
                .or_else(|| index.get(&format!("{stem}@2x.png")).map(|p| (p, 2.0)))
                .or_else(|| index.get(&format!("{stem}.png")).map(|p| (p, 1.0)));
            let Some((path, scale)) = found else { continue };
            let Some(pixmap) = fs::read(path).ok().and_then(|b| Pixmap::decode_png(&b).ok())
            else {
                continue;
            };
            let first = Sprite::new(pixmap, scale);

            // Every frame after the first, while they keep coming. An element
            // named `x-0.png` is frame zero of an animation, and osu! plays the
            // lot; reading only the first was fine while nothing here moved.
            //
            // It stopped being fine the moment it decided anything. A skin
            // whose follow points animate from nothing ships a *blank* frame
            // zero — 61 frames, the first of them empty — and reading that one
            // file alone said "this skin turned follow points off", which is
            // the opposite of what shipping 61 frames means. So the question
            // "did the skin blank this" is asked of the strip, not of a frame.
            let mut strip = vec![first];
            for n in 1.. {
                let next = index
                    .get(&format!("{stem}-{n}@2x.png"))
                    .map(|p| (p, 2.0))
                    .or_else(|| index.get(&format!("{stem}-{n}.png")).map(|p| (p, 1.0)))
                    .or_else(|| index.get(&format!("{stem}{n}@2x.png")).map(|p| (p, 2.0)))
                    .or_else(|| index.get(&format!("{stem}{n}.png")).map(|p| (p, 1.0)));
                let Some((path, scale)) = next else { break };
                match fs::read(path).ok().and_then(|b| Pixmap::decode_png(&b).ok()) {
                    Some(pixmap) => strip.push(Sprite::new(pixmap, scale)),
                    None => break,
                }
            }

            if strip.iter().all(Sprite::is_blank) {
                off.insert(element);
                continue;
            }
            // The still picture is the first frame with anything in it. For
            // everything that does not animate that is frame zero, unchanged;
            // for an animation it is what the element looks like rather than
            // what it starts from.
            let resting = strip.iter().position(|s| !s.is_blank()).unwrap_or(0);
            if strip.len() > 1 {
                frames.insert(element, strip.clone());
            }
            have.insert(element, strip.swap_remove(resting));
        }
        Self {
            have,
            frames,
            off,
            ini,
            palette: 0,
            tinted: HashMap::new(),
        }
    }

    /// How many frames this element animates over. One for a still picture.
    pub fn frame_count(&self, element: Element) -> usize {
        self.frames.get(&element).map_or(1, Vec::len)
    }

    /// One frame of an animation, by index, wrapping round the strip.
    ///
    /// Untinted only, which is every animated element the renderer draws so
    /// far. A tinted animation would want a coloured copy per frame per combo
    /// colour, and nothing has asked for one.
    pub fn frame(&self, element: Element, frame: usize) -> Option<(&Pixmap, f32)> {
        let strip = self.frames.get(&element)?;
        let sprite = strip.get(frame % strip.len())?;
        Some((&sprite.pixmap, sprite.scale))
    }

    /// What the folder's `skin.ini` asked for.
    pub fn ini(&self) -> &Ini {
        &self.ini
    }

    /// Colour the tinted elements for each of the map's combo colours.
    ///
    /// Separate from reading because the two know different things: the folder
    /// knows what pictures exist, the beatmap knows what colours they are worn
    /// in. Called once, when the skin is assembled against a map.
    pub fn tint_for(mut self, colours: &[Color]) -> Self {
        self.palette = colours.len();
        for (&element, sprite) in &self.have {
            if !element.is_tinted() {
                continue;
            }
            // `AllowSliderBallTint` defaults to off: the ball keeps whatever
            // colours the skin drew unless the skin asks for the combo colour.
            if element == Element::SliderBall && !self.ini.slider_ball_tint {
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
            // Wrapped, because a combo number counts up across the whole map
            // and a palette has a handful of colours: the fifth combo on a
            // four-colour map is the first colour again. Looking this up
            // unwrapped returned nothing past the end of the palette, and
            // nothing is what got drawn — approach circles present for the
            // first few combos of a map and gone for the rest of it.

            if self.palette == 0 {
                // Never coloured at all: a `Sprites` no map has dressed
                // yet. Drawing the white art it was authored in would be worse
                // than drawing nothing.
                return None;
            }
            if let Some(painted) = self.tinted.get(&(element, combo % self.palette)) {
                return Some((painted, scale));
            }
            // No coloured copy: an element the game tints in general but this
            // skin asked to be left alone. Its own picture, unpainted.
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
/// A skin's own name for something, with any folder in front of it dropped.
///
/// Written with forward slashes whatever the machine, since a skin.ini is a
/// Windows file read everywhere; backslashes are taken too, because skins in
/// the wild carry both.
fn leaf_of(value: &str) -> String {
    value
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(value)
        .trim()
        .to_owned()
}

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
    fn an_animations_first_frame_is_the_element() {
        // osu! prefers `hit300-0.png` to `hit300.png`, and skins use the
        // numbered form for things that never move. Reading only the static
        // name called the element missing.
        let dir = folder("anim");
        write(&dir, "hitcircle-0.png", 128, 255);
        let sprites = Sprites::read(&dir, WANTED);
        assert!(sprites.get(Element::HitCircle).is_some());
    }

    #[test]
    fn the_slider_balls_frames_carry_no_dash() {
        // osu! is not consistent about animation names, and this is the one
        // that matters: `sliderb0.png`, not `sliderb-0.png`. A skin exported
        // from lazer ships `sliderb0@2x.png`, and searching only the dashed
        // form found nothing — the ball came out ours on somebody else's skin.
        let dir = folder("sliderb");
        write(&dir, "sliderb0@2x.png", 256, 255);
        let sprites = Sprites::read(&dir, &[Element::SliderBall]);
        let ball = sprites.get(Element::SliderBall).expect("found");
        assert_eq!(ball.scale, 2.0, "the suffix still counts");
    }

    #[test]
    fn a_blank_first_frame_turns_the_element_off_like_any_other_blank() {
        // The bug this was found by. The skin read against here ships a blank
        // `hit300-0.png` and no `hit300.png`, which is how it hides its 300s —
        // and being unable to see it, we drew our own over the top.
        let dir = folder("anim-blank");
        write(&dir, "hitcircle-0.png", 1, 0);
        let sprites = Sprites::read(&dir, WANTED);
        assert!(sprites.silenced(Element::HitCircle));
        assert!(!sprites.draw_ourselves(Element::HitCircle));
    }

    #[test]
    fn an_animation_is_preferred_to_the_still_beside_it() {
        let dir = folder("anim-both");
        write(&dir, "hitcircle.png", 128, 255);
        write(&dir, "hitcircle-0.png", 1, 0);
        let sprites = Sprites::read(&dir, WANTED);
        assert!(
            sprites.silenced(Element::HitCircle),
            "the numbered frame is the one osu! reads"
        );
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
    fn a_combo_past_the_end_of_the_palette_wraps_round_it() {
        // A combo number counts up across the whole map; a palette has a
        // handful of colours. The fifth combo on a four-colour map is the first
        // colour again — and an unwrapped lookup returned nothing, which drew
        // nothing: approach circles for the first few combos of a map and none
        // for the rest of it. Reported from a real render.
        let dir = folder("wrap");
        write(&dir, "hitcircle.png", 8, 255);
        let sprites = Sprites::read(&dir, WANTED).tint_for(&[
            Color::from_rgba8(255, 0, 0, 255),
            Color::from_rgba8(0, 255, 0, 255),
        ]);

        let first = sprites.coloured(Element::HitCircle, 0).expect("combo 0");
        for combo in [2usize, 4, 100] {
            let later = sprites
                .coloured(Element::HitCircle, combo)
                .unwrap_or_else(|| panic!("combo {combo} drew nothing"));
            assert_eq!(later.0.pixels()[0], first.0.pixels()[0], "combo {combo}");
        }
        assert!(sprites.coloured(Element::HitCircle, 3).is_some(), "odd combos too");
    }

    #[test]
    fn a_blanked_hud_glyph_reads_as_off_rather_than_as_missing() {
        // The corner lettering is drawn all-or-nothing: one glyph the skin has
        // no file for and the whole line goes back to our typeface, because
        // half a number is worse than a different face. A glyph the skin ships
        // *empty* has to answer differently — the skin this was written against
        // blanks `score-x` to hide the sign after the combo, and reading that
        // as "cannot draw" put the combo in our face beside a score in the
        // skin's. Two faces in one corner.
        let dir = folder("hud-blank");
        write(&dir, "score-4.png", 8, 255);
        write(&dir, "score-x.png", 8, 0);
        let wanted = [Element::Score('4'), Element::Score('x'), Element::Score('7')];
        let sprites = Sprites::read(&dir, &wanted);

        assert!(sprites.get(Element::Score('4')).is_some(), "a figure it drew");
        assert!(sprites.silenced(Element::Score('x')), "a sign it deleted");
        assert!(
            sprites.draw_ourselves(Element::Score('7')),
            "and one it never mentioned, which is the only case worth a fallback"
        );
    }

    #[test]
    fn a_skin_that_was_never_tinted_draws_nothing_tinted() {
        // Rather than handing back the white picture the skin ships for the
        // game to colour, which would put an uncoloured note on the field.
        let dir = folder("untinted-skin");
        write(&dir, "hitcircle.png", 8, 255);
        let sprites = Sprites::read(&dir, WANTED);
        assert!(sprites.coloured(Element::HitCircle, 0).is_none());
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
    fn the_ini_is_read_for_the_settings_that_change_a_play() {
        let ini = Ini::parse(
            "[General]\nName: doki dt mix v3\n\n[Colours]\nCombo1: 255,255,255\n             Combo2: 10,20,30\n\n[Fonts]\nHitCirclePrefix: default\nHitCircleOverlap: 160\n",
        );
        assert_eq!(ini.hit_circle_overlap, 160.0);
        // Shown-first order: `Combo2` leads and `Combo1` comes last.
        assert_eq!(ini.combo_colours.len(), 2);
        assert_eq!(ini.combo_colours[0], Color::from_rgba8(10, 20, 30, 255));
        assert_eq!(ini.combo_colours[1], Color::from_rgba8(255, 255, 255, 255));
    }

    #[test]
    fn an_absurd_overlap_is_taken_at_its_word() {
        // 160 against 160-pixel digits leaves no advance at all. That is not a
        // mistake in the skin — its digits each carry a whole note ring, and
        // stacking them is how a two-figure combo stays one ring. Clamping this
        // would break the skin it was measured from.
        assert_eq!(
            Ini::parse("[Fonts]\nHitCircleOverlap: 160\n").hit_circle_overlap,
            160.0
        );
    }

    #[test]
    fn the_mania_section_does_not_repaint_the_notes() {
        // `[Mania]` has colour keys of its own meaning something else. Reading
        // them as combo colours would repaint a map from a ruleset we do not
        // draw.
        let ini = Ini::parse("[Mania]\nColour1: 255,0,0\nCombo1: 0,255,0\n");
        assert!(ini.combo_colours.is_empty(), "{:?}", ini.combo_colours);
    }

    #[test]
    fn comments_and_a_missing_ini_are_both_ordinary() {
        let ini = Ini::parse("// a note to nobody\n[Fonts]\nHitCircleOverlap: 7 // why not\n");
        assert_eq!(ini.hit_circle_overlap, 7.0);
        // Minus two rather than nothing, which is osu!'s own default and what
        // both lazer and danser ship. A skin that says nothing about its digit
        // spacing means them to overlap slightly, and reading that as zero drew
        // every combo number two pixels wider than the skin intended.
        assert_eq!(Ini::read(Path::new("/no/such/skin")).hit_circle_overlap, -2.0);
    }

    #[test]
    fn a_skin_that_renames_its_digits_is_still_found() {
        // `[Fonts] HitCirclePrefix` is a real thing skins use, and a skin that
        // uses it had no combo numbers at all here: we looked for
        // `default-0.png`, found nothing, and drew our own lettering instead.
        //
        // The combo counter's fallback is the *score* font rather than one of
        // its own, which is osu!'s rule and looks like a typo until you check.
        let ini = Ini::parse(
            "[Fonts]\nHitCirclePrefix: numbers/hit\nScorePrefix: ui\\score\n",
        );
        // Without the folder, because the importer flattens a skin on the way
        // in and this engine's index is flat to match. Both slashes are taken:
        // a skin.ini is a Windows file that gets read everywhere.
        assert_eq!(Element::Digit(4).stem_with(&ini), "hit-4");
        assert_eq!(Element::Score('x').stem_with(&ini), "score-x");

        // And a skin that says nothing keeps the names the game gives.
        let plain = Ini::default();
        assert_eq!(Element::Digit(4).stem_with(&plain), "default-4");
        assert_eq!(Element::Score(',').stem_with(&plain), "score-comma");
        assert_eq!(plain.combo_prefix, "score");
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

    #[test]
    fn a_blank_first_frame_is_not_a_skin_turning_the_element_off() {
        // The one that got away. A skin whose follow points animate up from
        // nothing ships a *blank* frame zero — sixty-one frames, the first of
        // them empty — and reading that one file alone said "this skin turned
        // follow points off", which is the opposite of what shipping sixty-one
        // frames means. Whether a skin blanked something is a question about
        // the strip.
        let dir = folder("animated");
        write(&dir, "followpoint-0.png", 32, 0);
        write(&dir, "followpoint-1.png", 32, 200);
        write(&dir, "followpoint-2.png", 32, 255);
        let sprites = Sprites::read(&dir, &[Element::FollowPoint]);

        assert!(!sprites.draw_ourselves(Element::FollowPoint), "it is skinned");
        assert_eq!(sprites.frame_count(Element::FollowPoint), 3);
        assert!(
            sprites.get(Element::FollowPoint).is_some_and(|s| !s.is_blank()),
            "and the still picture is a frame with something in it"
        );
    }

    #[test]
    fn a_strip_of_nothing_but_blanks_is_still_a_skin_saying_no() {
        // The other half. Blanking every frame is how a skin turns off an
        // element it does not want, and the count of files does not change
        // that.
        let dir = folder("animated-off");
        write(&dir, "followpoint-0.png", 32, 0);
        write(&dir, "followpoint-1.png", 32, 0);
        let sprites = Sprites::read(&dir, &[Element::FollowPoint]);

        assert!(!sprites.draw_ourselves(Element::FollowPoint), "still spoken for");
        assert!(sprites.get(Element::FollowPoint).is_none(), "and drawn as nothing");
    }

    #[test]
    fn a_single_picture_has_one_frame() {
        let dir = folder("still");
        write(&dir, "hitcircle.png", 128, 255);
        let sprites = Sprites::read(&dir, &[Element::HitCircle]);
        assert_eq!(sprites.frame_count(Element::HitCircle), 1);
        assert!(sprites.frame(Element::HitCircle, 0).is_none(), "no strip to index");
    }

    #[test]
    fn the_rate_a_strip_plays_at_defaults_to_the_whole_thing_in_a_second() {
        // > A positive integer or `-1` to make osu! play all frames of the
        // > animation in one second.
        assert_eq!(Ini::default().animation_framerate, -1.0);
        assert_eq!(
            Ini::parse("[General]\nAnimationFramerate: 24\n").animation_framerate,
            24.0
        );
    }

    #[test]
    fn a_skin_may_ask_for_its_cursor_not_to_swell() {
        // > Should the cursor expand when clicked?  Default `1`.
        //
        // Both skins this was written against say no, and it used to be
        // ignored with a comment saying so.
        assert!(Ini::default().cursor_expand, "on unless a skin says otherwise");
        assert!(!Ini::parse("[General]\nCursorExpand: 0\n").cursor_expand);
        assert!(Ini::parse("[General]\nCursorExpand: 1\n").cursor_expand);
    }

    #[test]
    fn the_slider_ball_keeps_its_own_colours_unless_asked() {
        // > Should the slider combo colour tint the slider ball?  Default `0`.
        //
        // The one element whose default is "leave it alone", and the one this
        // had the wrong way round: it was tinted always.
        assert!(!Ini::default().slider_ball_tint, "off unless a skin says otherwise");
        assert!(Ini::parse("[General]\nAllowSliderBallTint: 1\n").slider_ball_tint);
    }

    #[test]
    fn a_ball_left_alone_is_still_drawn() {
        // Skipping the coloured copies must not skip the element: the lookup
        // falls back to the picture the skin drew, which is the whole point of
        // leaving it alone.
        let dir = folder("ball");
        write(&dir, "sliderb0.png", 128, 255);
        let sprites = Sprites::read(&dir, &[Element::SliderBall])
            .tint_for(&[Color::from_rgba8(255, 0, 0, 255)]);
        assert!(
            sprites.coloured(Element::SliderBall, 0).is_some(),
            "the ball vanished instead of keeping its own colours"
        );
    }

    #[test]
    fn a_ball_a_skin_asks_to_tint_is_tinted() {
        let dir = folder("ball-tinted");
        write(&dir, "sliderb0.png", 128, 255);
        std::fs::write(
            dir.join("skin.ini"),
            "[General]\nAllowSliderBallTint: 1\n",
        )
        .expect("written");
        let sprites = Sprites::read(&dir, &[Element::SliderBall])
            .tint_for(&[Color::from_rgba8(255, 0, 0, 255)]);
        let (art, _) = sprites.coloured(Element::SliderBall, 0).expect("drawn");
        let pixel = art.pixels()[0];
        assert!(pixel.red() > 0 && pixel.green() == 0, "{pixel:?}");
    }
}
