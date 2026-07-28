//! Colours and proportions.
//!
//! Everything a skin would decide lives here, so the drawing code never picks a
//! colour of its own. Image skins come later; this is the "default look" in
//! numeric form.

use dossier_beatmap::Colour;
use tiny_skia::Color;

use crate::text::Font;

/// The silhouette of a reverse arrow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArrowShape {
    /// A plain triangle: unmistakable, and about as interesting as a road sign.
    Triangle,
    /// Swept back, with a notch cut into its tail — the shape a paper plane
    /// makes. The notch is what does the work: it turns a static wedge into
    /// something that reads as already moving, which is the right thing for a
    /// mark that means "come back this way".
    ///
    /// After the arrow BizzBox drew for Flaticon, which is where the shape
    /// came from — redrawn as a path rather than bundled, so it stays sharp at
    /// any size and can take the skin's colour.
    Swept,
    /// The plain triangle with its corners taken off, after Roundicons on
    /// Flaticon. Used for the arrows that warn a break is ending: those speak
    /// for the game rather than for the map, so they keep their own shape
    /// whatever skin is on.
    Rounded,
}

#[derive(Debug, Clone)]
pub struct Skin {
    /// Cycled per combo, straight from the beatmap.
    pub combo_colours: Vec<Color>,
    pub background: Color,
    pub circle_border: Color,
    pub approach_circle: Color,
    pub slider_border: Color,
    /// The slider body is the combo colour darkened by this much.
    pub slider_body_dim: f32,
    pub slider_body_alpha: f32,
    /// Border thickness as a fraction of the circle radius.
    pub border_ratio: f32,
    /// Which reverse arrow to draw.
    pub arrow: ArrowShape,
    pub cursor: Color,
    pub cursor_trail: Color,
    pub spinner: Color,
    /// Typeface for combo numbers and the HUD. Without one the renderer draws
    /// the play and stays silent about the score — better than inventing a
    /// bitmap font nobody asked for.
    pub font: Option<Font>,
    pub hud: Color,
    /// Colours for the verdict that flashes at a note as it resolves —
    /// 300, 100, 50 and miss. The first three step down in presence so a
    /// clean play stays quiet and a dropped note stands out.
    pub verdict_300: Color,
    pub verdict_100: Color,
    pub verdict_50: Color,
    pub verdict_miss: Color,
}

impl Skin {
    pub fn with_combo_colours(colours: &[Colour]) -> Self {
        Self {
            combo_colours: colours.iter().map(|c| rgb(c.r, c.g, c.b)).collect(),
            ..Self::default()
        }
    }

    pub fn with_font(mut self, font: Font) -> Self {
        self.font = Some(font);
        self
    }

    /// Dossier's own look, on the palette the bot already ships.
    ///
    /// Note what this gives up: the combo colours stop being the map's. That is
    /// a real loss of fidelity and the reason it isn't the default — a mapper
    /// chose those colours. It's offered as a named skin because a house style
    /// is a legitimate thing to want, not because the map's own palette is
    /// wrong.
    ///
    /// The cycle is two colours: the bot's coral accent and the warm sand it
    /// alternates with. It used to run through all three medal metals as well,
    /// which made the skin restless — gold and silver are cold and bright next
    /// to the coral, so every fourth combo jumped out of the palette instead of
    /// belonging to it. Two warm colours read as one deliberate scheme, and a
    /// combo change is still unmistakable because they alternate every time.
    pub fn nineteen_eightyfour() -> Self {
        Self {
            combo_colours: vec![
                rgb(226, 72, 72),  // ACCENT — the bot's coral
                rgb(205, 150, 80), // the bronze of the medal set, warm sand here
            ],
            background: rgb(14, 12, 16), // BG
            // Off-white rather than white: pure white on a near-black field is
            // harsher than anything else in the bot's design.
            circle_border: rgb(236, 234, 238), // TEXT_PRIMARY
            approach_circle: rgb(236, 234, 238),
            slider_border: rgb(236, 234, 238),
            // Darker, flatter slider bodies keep the coral heads legible on top
            // of them.
            slider_body_dim: 0.55,
            slider_body_alpha: 0.62,
            border_ratio: 0.10,
            arrow: ArrowShape::Swept,
            cursor: rgb(255, 255, 255),
            cursor_trail: rgb(240, 104, 104), // ACCENT_PP
            spinner: rgb(156, 144, 150),      // TEXT_MUTED
            font: None,
            hud: rgb(236, 234, 238),
            // The bot's two colours doing the work: sand for a clean hit,
            // stepping down through the muted tones, and coral for a miss.
            // Nothing new is introduced — the palette is the point.
            verdict_300: rgb(236, 234, 238),  // TEXT_PRIMARY
            verdict_100: rgb(206, 186, 160),  // sand
            verdict_50: rgb(156, 144, 150),   // TEXT_MUTED
            verdict_miss: rgb(240, 104, 104), // ACCENT_PP
        }
    }

    /// Colour for the `index`-th combo on the map, wrapping round the palette.
    pub fn combo_colour(&self, index: usize) -> Color {
        if self.combo_colours.is_empty() {
            return rgb(255, 192, 0);
        }
        self.combo_colours[index % self.combo_colours.len()]
    }
}

impl Default for Skin {
    fn default() -> Self {
        Self {
            combo_colours: dossier_beatmap::DEFAULT_COMBO_COLOURS
                .iter()
                .map(|c| rgb(c.r, c.g, c.b))
                .collect(),
            background: rgb(12, 12, 16),
            circle_border: rgb(255, 255, 255),
            approach_circle: rgb(255, 255, 255),
            slider_border: rgb(255, 255, 255),
            slider_body_dim: 0.35,
            slider_body_alpha: 0.72,
            border_ratio: 0.11,
            arrow: ArrowShape::Triangle,
            cursor: rgb(255, 255, 255),
            cursor_trail: rgb(255, 190, 190),
            spinner: rgb(190, 190, 200),
            font: None,
            hud: rgb(255, 255, 255),
            verdict_300: rgb(120, 200, 255),
            verdict_100: rgb(140, 230, 140),
            verdict_50: rgb(230, 200, 120),
            verdict_miss: rgb(240, 90, 90),
        }
    }
}

pub fn rgb(r: u8, g: u8, b: u8) -> Color {
    Color::from_rgba8(r, g, b, 255)
}

/// The same colour at a different opacity.
pub fn with_alpha(colour: Color, alpha: f32) -> Color {
    let mut out = colour;
    out.set_alpha(colour.alpha() * alpha.clamp(0.0, 1.0));
    out
}

/// Scale a colour toward white, the mirror of [`darken`].
///
/// Lifting toward white rather than raising the alpha keeps the hue: a paler
/// version of the combo colour still reads as belonging to this combo, where a
/// translucent one takes on whatever it happens to be sitting over.
pub fn lighten(colour: Color, amount: f32) -> Color {
    let k = amount.clamp(0.0, 1.0);
    Color::from_rgba(
        colour.red() + (1.0 - colour.red()) * k,
        colour.green() + (1.0 - colour.green()) * k,
        colour.blue() + (1.0 - colour.blue()) * k,
        colour.alpha(),
    )
    .unwrap_or(colour)
}

/// Scale a colour toward black. Used for slider bodies, which are the combo
/// colour with the life taken out of them so the border reads clearly.
pub fn darken(colour: Color, amount: f32) -> Color {
    let k = 1.0 - amount.clamp(0.0, 1.0);
    Color::from_rgba(
        colour.red() * k,
        colour.green() * k,
        colour.blue() * k,
        colour.alpha(),
    )
    .unwrap_or(colour)
}

#[cfg(test)]
mod shades {
    use super::*;

    #[test]
    fn lightening_moves_toward_white_without_losing_the_hue() {
        // The point of lifting toward white rather than dropping the alpha: a
        // pale combo colour still says which combo it belongs to, where a
        // translucent one takes on whatever it is drawn over.
        let coral = rgb(226, 72, 72);
        let pale = lighten(coral, 0.45);

        assert!(pale.red() > coral.red());
        assert!(pale.green() > coral.green());
        assert!(pale.blue() > coral.blue());
        assert_eq!(pale.alpha(), coral.alpha(), "opacity is not the lever here");
        // Still visibly red: the channel that dominated still dominates.
        assert!(pale.red() > pale.green() + 0.1, "{pale:?}");
    }

    #[test]
    fn the_two_ends_are_the_colour_itself_and_white() {
        let coral = rgb(226, 72, 72);
        assert_eq!(lighten(coral, 0.0), coral);
        let white = lighten(coral, 1.0);
        assert!(white.red() > 0.99 && white.green() > 0.99 && white.blue() > 0.99);
    }

    #[test]
    fn lightening_and_darkening_pull_opposite_ways() {
        let coral = rgb(226, 72, 72);
        assert!(lighten(coral, 0.5).green() > coral.green());
        assert!(darken(coral, 0.5).green() < coral.green());
    }
}
