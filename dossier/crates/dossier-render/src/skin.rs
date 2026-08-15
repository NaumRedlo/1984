//! Colours and proportions.
//!
//! Everything a skin would decide lives here, so the drawing code never picks a
//! colour of its own. Image skins come later; this is the "default look" in
//! numeric form.

use dossier_beatmap::Colour;
use tiny_skia::Color;

use crate::imported::Sprites;
use crate::text::Font;

/// The silhouette of a reverse arrow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ArrowShape {
    /// A plain triangle: unmistakable, and about as interesting as a road sign.
    Triangle,
    /// The plain triangle with its corners taken off, after Roundicons on
    /// Flaticon. Used for the arrows that warn a break is ending: those speak
    /// for the game rather than for the map, so they keep their own shape
    /// whatever skin is on.
    Rounded,
}

#[derive(Debug, Clone)]
pub struct Skin {
    /// The player's own skin, when a render was given one.
    ///
    /// Held behind an `Arc` because a `Skin` is cloned freely and this is a
    /// few hundred kilobytes of decoded pictures; and shared rather than owned
    /// per thread because a scene is built once and drawn on several, so the
    /// textures have to be immutable by the time drawing starts.
    ///
    /// `None` is the ordinary case and means every element is ours to draw.
    pub sprites: Option<std::sync::Arc<Sprites>>,
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
    /// Gold, silver and bronze, for the first three places on the scoreboard.
    ///
    /// The bot's own `TOP_COLORS`, so a podium in a render and a podium in a
    /// leaderboard card are the same three colours rather than two people's
    /// separate idea of gold.
    pub podium: [Color; 3],
    /// Colours for the verdict that flashes at a note as it resolves.
    ///
    /// osu!'s own: a light blue 300, a green 100, an amber 50 and a red miss.
    /// Players read these without thinking, and inventing a palette for them
    /// would make the render harder to follow than the game it is showing.
    pub verdict_300: Color,
    pub verdict_100: Color,
    pub verdict_50: Color,
    pub verdict_miss: Color,
    /// How much a note's fill lifts towards the light at its centre, 0 to 1.
    ///
    /// A flat disc is what osu! draws and what `classic` keeps. Given a little
    /// depth the same disc reads as a lit object rather than as a sticker: the
    /// centre comes up towards white, the rim falls away, and the combo colour
    /// is still the colour of the note. Zero is the flat fill exactly, so this
    /// costs nothing where it is not asked for.
    pub note_relief: f32,
    /// How much a reverse arrow swells on the map's beat, as a fraction of its
    /// size.
    ///
    /// A reverse arrow is the one mark on the field that says *this is coming
    /// back*, and it is read while the ball is still on its way to it — so it
    /// is worth making it move. It beats on the map's own clock, the way the
    /// break warnings already do: the music is what the player is reading, and
    /// a pulse that rides it says something they can already feel.
    ///
    /// Zero for the skins imitating osu!, where an arrow sits still.
    pub arrow_beat: f32,
    /// How far a note's glow reaches past its rim, as a fraction of the radius.
    ///
    /// The note's own colour, thrown softly onto the near-black field so the
    /// object sits *in* the frame instead of on top of it. Zero draws none.
    pub note_glow: f32,
    /// How far the map's own artwork is taken towards [`Skin::background`],
    /// 0 to 1, and how hard it is blurred as a share of the frame's height.
    ///
    /// Heavy on both counts, and deliberately. A background is the map's, and
    /// showing it is worth doing — but the render exists to show a *play*, and
    /// a picture that keeps any detail worth reading takes the eye off the
    /// notes. Blurred and washed towards the field's own near-black it becomes
    /// what it should be: a colour and a mood behind the play rather than a
    /// second thing to look at.
    pub background_dim: f32,
    pub background_blur: f32,
    /// Whether to flash a 300 at all.
    ///
    /// On a clean play nearly every note is a 300, and marking each one buries
    /// the two that were not. Off in the bot's own skin, where the point is to
    /// show what went wrong; on in the classic one, which is imitating the
    /// game.
    pub show_300: bool,
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
            sprites: None,
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
            podium: [rgb(255, 215, 0), rgb(192, 192, 210), rgb(205, 150, 80)],
            verdict_300: rgb(102, 204, 255),
            verdict_100: rgb(136, 221, 68),
            verdict_50: rgb(255, 204, 34),
            verdict_miss: rgb(237, 84, 84),
            // Flat and still, like the game: `classic` imitates osu!, where a
            // disc is flat and a reverse arrow does not move.
            note_relief: 0.0,
            arrow_beat: 0.0,
            note_glow: 0.0,
            background_dim: 0.82,
            background_blur: 0.022,
            show_300: true,
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

/// Mix two colours, `amount` of the way from the first to the second.
///
/// Kept beside [`lighten`] and [`darken`], which are this against white and
/// black. A crossfade between two states wants the hue to travel rather than
/// one colour to fade out from under another: over a bright background the
/// second way shows whatever is behind, and the plate stops being a plate
/// halfway through.
pub fn blend(from: Color, to: Color, amount: f32) -> Color {
    let k = amount.clamp(0.0, 1.0);
    let mix = |a: f32, b: f32| a + (b - a) * k;
    Color::from_rgba(
        mix(from.red(), to.red()),
        mix(from.green(), to.green()),
        mix(from.blue(), to.blue()),
        mix(from.alpha(), to.alpha()),
    )
    .unwrap_or(from)
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
    fn blending_travels_the_whole_way_and_stops_at_both_ends() {
        let a = Color::from_rgba8(20, 20, 24, 255);
        let b = Color::from_rgba8(226, 72, 72, 255);
        assert_eq!(blend(a, b, 0.0), a);
        assert_eq!(blend(a, b, 1.0), b);
        // Halfway is halfway on every channel — a crossfade that moved the hue
        // faster than the value would show a colour neither state has.
        let half = blend(a, b, 0.5);
        assert!((half.red() - (a.red() + b.red()) / 2.0).abs() < 1e-6);
        assert!((half.green() - (a.green() + b.green()) / 2.0).abs() < 1e-6);
        // Out of range is clamped rather than extrapolated into nonsense.
        assert_eq!(blend(a, b, -1.0), a);
        assert_eq!(blend(a, b, 2.0), b);
    }

    #[test]
    fn lightening_and_darkening_pull_opposite_ways() {
        let coral = rgb(226, 72, 72);
        assert!(lighten(coral, 0.5).green() > coral.green());
        assert!(darken(coral, 0.5).green() < coral.green());
    }
}
