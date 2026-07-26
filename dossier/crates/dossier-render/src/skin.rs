//! Colours and proportions.
//!
//! Everything a skin would decide lives here, so the drawing code never picks a
//! colour of its own. Image skins come later; this is the "default look" in
//! numeric form.

use dossier_beatmap::Colour;
use tiny_skia::Color;

use crate::text::Font;

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
    pub cursor: Color,
    pub cursor_trail: Color,
    pub spinner: Color,
    /// Typeface for combo numbers and the HUD. Without one the renderer draws
    /// the play and stays silent about the score — better than inventing a
    /// bitmap font nobody asked for.
    pub font: Option<Font>,
    pub hud: Color,
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
            cursor: rgb(255, 255, 255),
            cursor_trail: rgb(255, 190, 190),
            spinner: rgb(190, 190, 200),
            font: None,
            hud: rgb(255, 255, 255),
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
