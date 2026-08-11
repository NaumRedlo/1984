//! The shapes an object is made of, in pixels.
//!
//! Everything here takes a position already in screen pixels and knows nothing
//! about the playfield, the layout or the play. That is the whole point: the
//! renderer maps a playfield point and calls these, and the skin exporter calls
//! the same ones onto a small transparent canvas to write a `.png` osu! can
//! wear. One set of shapes, two places that draw them — a second copy would be
//! a skin that slowly stopped looking like the renders.

use tiny_skia::{
    Color, FillRule, GradientStop, LineCap, LineJoin, Paint, PathBuilder, Pixmap, Point,
    RadialGradient, Shader, SpreadMode, Stroke, Transform,
};

use crate::skin::{darken, lighten, with_alpha, ArrowShape};

/// A filled circle.
pub(crate) fn dot(pixmap: &mut Pixmap, x: f32, y: f32, radius: f32, colour: Color, alpha: f32) {
    if radius <= 0.0 || alpha <= 0.0 {
        return;
    }
    let Some(path) = PathBuilder::from_circle(x, y, radius) else {
        return;
    };
    let paint = Paint {
        shader: Shader::SolidColor(with_alpha(colour, alpha)),
        anti_alias: true,
        ..Default::default()
    };
    pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
}

/// A stroked circle.
pub(crate) fn ring(
    pixmap: &mut Pixmap,
    x: f32,
    y: f32,
    radius: f32,
    width: f32,
    colour: Color,
    alpha: f32,
) {
    if radius <= 0.0 || alpha <= 0.0 {
        return;
    }
    let Some(path) = PathBuilder::from_circle(x, y, radius) else {
        return;
    };
    let paint = Paint {
        shader: Shader::SolidColor(with_alpha(colour, alpha)),
        anti_alias: true,
        ..Default::default()
    };
    let stroke = Stroke {
        width: width.max(0.5),
        ..Default::default()
    };
    pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
}

/// A filled circle with the light on it, so the disc reads as an object rather
/// than as a sticker. `relief` of zero is the flat fill exactly.
pub(crate) fn lit_dot(
    pixmap: &mut Pixmap,
    x: f32,
    y: f32,
    radius: f32,
    colour: Color,
    alpha: f32,
    relief: f32,
) {
    if relief <= 0.0 {
        dot(pixmap, x, y, radius, colour, alpha);
        return;
    }
    if radius <= 0.0 || alpha <= 0.0 {
        return;
    }
    let Some(path) = PathBuilder::from_circle(x, y, radius) else {
        return;
    };
    // Off-centre and high, the way a lit sphere reads. Kept well inside the
    // disc so the highlight never clips against the rim.
    let light = Point::from_xy(x - radius * 0.22, y - radius * 0.30);
    let stops = vec![
        GradientStop::new(0.0, with_alpha(lighten(colour, relief), alpha)),
        GradientStop::new(0.55, with_alpha(colour, alpha)),
        GradientStop::new(1.0, with_alpha(darken(colour, relief * 0.5), alpha)),
    ];
    let shader = RadialGradient::new(
        light,
        Point::from_xy(x, y),
        radius * 1.15,
        stops,
        SpreadMode::Pad,
        Transform::identity(),
    );
    let Some(shader) = shader else {
        // Degenerate geometry — a radius the gradient cannot describe. The flat
        // fill is the right answer rather than nothing at all.
        dot(pixmap, x, y, radius, colour, alpha);
        return;
    };
    let paint = Paint {
        shader,
        anti_alias: true,
        ..Default::default()
    };
    pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
}

/// A halo of the note's own colour, falling off to nothing by its rim.
pub(crate) fn glow(
    pixmap: &mut Pixmap,
    x: f32,
    y: f32,
    radius: f32,
    colour: Color,
    alpha: f32,
    reach: f32,
) {
    if reach <= 0.0 || radius <= 0.0 || alpha <= 0.0 {
        return;
    }
    let outer = radius * (1.0 + reach);
    let Some(path) = PathBuilder::from_circle(x, y, outer) else {
        return;
    };
    // Where the note's own edge falls inside this disc: the glow is at strength
    // up to there and gone by the rim.
    let edge = (radius / outer).clamp(0.0, 1.0);
    let strength = alpha * 0.22;
    let stops = vec![
        GradientStop::new(0.0, with_alpha(colour, strength)),
        GradientStop::new(edge, with_alpha(colour, strength * 0.7)),
        GradientStop::new(1.0, with_alpha(colour, 0.0)),
    ];
    let shader = RadialGradient::new(
        Point::from_xy(x, y),
        Point::from_xy(x, y),
        outer,
        stops,
        SpreadMode::Pad,
        Transform::identity(),
    );
    let Some(shader) = shader else {
        return;
    };
    let paint = Paint {
        shader,
        anti_alias: true,
        ..Default::default()
    };
    pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
}

/// One skin element, drawn on its own transparent canvas at `size` pixels
/// square — the form osu! wants a `.png` in.
///
/// Which colour each is drawn in is not a free choice: the game **tints**
/// `hitcircle`, `approachcircle` and the slider ball by the combo colour, and
/// leaves `hitcircleoverlay`, `reversearrow` and `sliderscorepoint` alone. So
/// the tinted ones are drawn in white here and take their colour in the game,
/// while the untinted ones carry the skin's own. Drawing a tinted element in
/// its colour would apply the palette twice and come out muddy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Element {
    /// The disc, white — the game tints it.
    HitCircle,
    /// The rim that sits over the disc, in the skin's own border colour.
    HitCircleOverlay,
    /// The ring that closes in on a note, white — tinted in the game.
    ApproachCircle,
    /// The mark that says a slider comes back, in the skin's own colour.
    ReverseArrow,
    /// The dot a slider's ticks are drawn as.
    SliderScorePoint,
    /// The cursor's own disc. osu! rotates and expands this one on a click —
    /// rotation is invisible on a circle, and the expansion is exactly what our
    /// cursor does under the hand anyway.
    Cursor,
    /// The still centre, which osu! draws *above* the cursor and never expands.
    /// Our white middle, so it stays a crisp point while the disc swells.
    CursorMiddle,
    /// What the cursor leaves behind it.
    CursorTrail,
    /// A combo number, `0` to `9`.
    ///
    /// Unlike everything else here the canvas is not square and not fixed: the
    /// game lays multi-digit numbers out side by side from the sprites' own
    /// widths, so a digit padded into a square would be spaced across the note
    /// with holes between the figures. Each is cut to its own glyph, and the
    /// padding that remains is given back through `HitCircleOverlap`.
    Digit(u8),
}

/// The margin left around a digit's glyph, in the format's own pixels.
///
/// Small, and it exists because a glyph that ends exactly on the canvas edge
/// loses its anti-aliased rim. `HitCircleOverlap` in the `skin.ini` has to
/// cancel it or every multi-digit combo reads a little wide.
pub const DIGIT_PADDING: f32 = 4.0;

/// The side of the hit circle a digit is sized against.
const DIGIT_REFERENCE: f32 = 128.0;

impl Element {
    /// The filename osu! reads this element by, without the extension.
    pub fn stem(self) -> String {
        match self {
            Self::HitCircle => "hitcircle".to_owned(),
            Self::HitCircleOverlay => "hitcircleoverlay".to_owned(),
            Self::ApproachCircle => "approachcircle".to_owned(),
            Self::ReverseArrow => "reversearrow".to_owned(),
            Self::SliderScorePoint => "sliderscorepoint".to_owned(),
            Self::Cursor => "cursor".to_owned(),
            Self::CursorMiddle => "cursormiddle".to_owned(),
            Self::CursorTrail => "cursortrail".to_owned(),
            Self::Digit(n) => format!("default-{n}"),
        }
    }

    /// The size osu! draws this element at, in the format's own pixels. The
    /// high-resolution `@2x` file is exactly twice this on each side.
    ///
    /// For a digit this is the hit circle it is sized against rather than the
    /// canvas, which is cut to the glyph.
    pub fn size(self) -> u32 {
        match self {
            Self::HitCircle | Self::HitCircleOverlay | Self::ReverseArrow => 128,
            Self::ApproachCircle => 126,
            Self::SliderScorePoint => 16,
            Self::Cursor | Self::CursorMiddle => 128,
            Self::CursorTrail => 64,
            Self::Digit(_) => DIGIT_REFERENCE as u32,
        }
    }
}

/// Draw `element` for `skin` at `size` pixels square.
///
/// The same functions the renderer draws a frame with, onto a small canvas
/// instead of a video frame — so the skin a player installs and the skin a
/// render wears cannot drift apart.
pub fn element(skin: &crate::skin::Skin, element: Element, size: u32) -> Option<Pixmap> {
    let mut pixmap = Pixmap::new(size, size)?;
    let half = size as f32 / 2.0;
    // Everything is drawn inset by a pixel or two: an anti-aliased edge that
    // ends exactly on the canvas boundary is a hard edge, and the glow needs
    // somewhere to fall off into.
    let white = Color::from_rgba8(255, 255, 255, 255);
    match element {
        Element::HitCircle => {
            let radius = half * 0.94;
            let border = radius * skin.border_ratio;
            // White, because the game multiplies the combo colour through it.
            // The relief still reads: it is a light and a shade on the white,
            // and a tint keeps both.
            lit_dot(&mut pixmap, half, half, radius - border, white, 1.0, skin.note_relief);
        }
        Element::HitCircleOverlay => {
            let radius = half * 0.94;
            let border = radius * skin.border_ratio;
            ring(
                &mut pixmap,
                half,
                half,
                radius - border / 2.0,
                border,
                skin.circle_border,
                1.0,
            );
        }
        Element::ApproachCircle => {
            // Sized off the canvas rather than off the hit circle: osu! scales
            // this one against the note itself, and its own file is 126 square.
            let width = (size as f32 * 0.035).max(2.0);
            ring(&mut pixmap, half, half, half - width, width, white, 1.0);
        }
        Element::ReverseArrow => {
            chevron(
                &mut pixmap,
                half,
                half,
                (1.0, 0.0),
                half * 0.62,
                skin.circle_border,
                1.0,
                skin.arrow,
                0.22,
            );
        }
        Element::SliderScorePoint => {
            dot(&mut pixmap, half, half, half * 0.75, skin.circle_border, 1.0);
        }
        Element::Cursor => {
            // The disc, with its own halo — the same reading the notes get, so
            // the hand belongs to the same frame as what it is aiming at.
            let radius = half * 0.42;
            glow(&mut pixmap, half, half, radius, skin.cursor_trail, 1.0, 0.9);
            dot(&mut pixmap, half, half, radius, skin.cursor_trail, 1.0);
        }
        Element::CursorMiddle => {
            // Held or not, this one keeps its size: the game never expands it.
            dot(&mut pixmap, half, half, half * 0.25, skin.cursor, 1.0);
        }
        Element::CursorTrail => {
            dot(&mut pixmap, half, half, half * 0.5, skin.cursor_trail, 0.55);
        }
        Element::Digit(_) => return digit(skin, element, size),
    }
    Some(pixmap)
}

/// One combo digit, on a canvas cut to its own glyph.
fn digit(skin: &crate::skin::Skin, element: Element, size: u32) -> Option<Pixmap> {
    let Element::Digit(value) = element else {
        return None;
    };
    let font = skin.font.as_ref()?;
    let scale = size as f32 / DIGIT_REFERENCE;
    // The proportion the renderer draws a combo number at — nine tenths of the
    // circle's radius — carried over so a note in game and a note in a render
    // wear the same figure. The game then downscales every digit by 0.8, so it
    // is drawn that much larger here to arrive at the same size.
    let glyph = DIGIT_REFERENCE * 0.47 * scale / 0.8;
    let pad = DIGIT_PADDING * scale;
    let text = value.to_string();
    // The canvas is the plain size rounded once and then multiplied, never
    // rounded twice. Sized independently at each resolution the two disagreed
    // by a pixel — 58×60 against 115×119 — and `@2x` means exactly twice, not
    // about twice.
    let plain = DIGIT_REFERENCE * 0.47 / 0.8;
    let unit_width = (font.width(&text, plain) + DIGIT_PADDING * 2.0).ceil();
    let unit_height = (font.digit_height(plain) + DIGIT_PADDING * 2.0).ceil();
    let width = unit_width * scale;
    let height = unit_height * scale;
    let mut pixmap = Pixmap::new(width.round() as u32, height.round() as u32)?;
    font.draw(
        &mut pixmap,
        crate::text::Label {
            text: &text,
            x: width / 2.0,
            y: pad + font.digit_height(glyph),
            size: glyph,
            colour: skin.circle_border,
            align: crate::text::Align::Centre,
        },
    );
    Some(pixmap)
}

/// A filled arrowhead pointing along `dir`, centred on `x, y`.
#[allow(clippy::too_many_arguments)]
pub(crate) fn chevron(
    pixmap: &mut Pixmap,
    x: f32,
    y: f32,
    dir: (f64, f64),
    size: f32,
    colour: Color,
    alpha: f32,
    shape: ArrowShape,
    rounding: f32,
) {
    let (dx, dy) = dir;
    let (px, py) = (-dy, dx); // perpendicular, for the base corners
    let point = |along: f64, across: f64| {
        (
            x + (dx * along + px * across) as f32 * size,
            y + (dy * along + py * across) as f32 * size,
        )
    };

    // The swept shape carries a notch in its tail, so it needs the extra
    // vertex; the plain triangle closes straight across.
    let outline: &[(f64, f64)] = match shape {
        ArrowShape::Triangle | ArrowShape::Rounded => &[(1.0, 0.0), (-0.55, 0.85), (-0.55, -0.85)],
        ArrowShape::Swept => &[(1.0, 0.0), (-0.78, 0.82), (-0.38, 0.0), (-0.78, -0.82)],
    };

    let mut builder = PathBuilder::with_capacity(outline.len() + 1, outline.len() + 1);
    let (first_x, first_y) = point(outline[0].0, outline[0].1);
    builder.move_to(first_x, first_y);
    for &(along, across) in &outline[1..] {
        let (px, py) = point(along, across);
        builder.line_to(px, py);
    }
    builder.close();
    let Some(path) = builder.finish() else {
        return;
    };

    let paint = Paint {
        shader: Shader::SolidColor(with_alpha(colour, alpha)),
        anti_alias: true,
        ..Default::default()
    };
    pixmap.fill_path(&path, &paint, FillRule::Winding, Transform::identity(), None);
    // Corners rounded by stroking the same outline over the fill. Sharp points
    // on a mark this small read as jagged rather than as crisp, and the drawn
    // shape this is after has generous rounding.
    if shape != ArrowShape::Triangle {
        let stroke = Stroke {
            width: size * rounding,
            line_cap: LineCap::Round,
            line_join: LineJoin::Round,
            ..Default::default()
        };
        pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
    }
}
