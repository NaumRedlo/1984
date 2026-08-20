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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
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
    /// The flash left where a note was struck. Additive, and tinted — the wiki
    /// says "tinting depends on the hit circle's combo colour" — so it is the
    /// note's own colour thrown back off the field.
    ///
    /// Only on a hit. A miss has nothing to light up, and osu! agrees: this
    /// lives in `ApplyHitAnimations` and nowhere else.
    Lighting,
    /// The key overlay down the right edge: the plate behind it, and one
    /// button per key.
    ///
    /// The plate is rotated a quarter turn — the file is drawn lying down and
    /// the game stands it up — and the button is tinted while it is held. Not
    /// by the combo colour: osu! lights the first two keys `#ffde00` and the
    /// rest `#f8009e`, which are the game's own and have nothing to do with
    /// the map.
    InputOverlayBackground,
    InputOverlayKey,
    /// The trail of marks osu! lays between one note and the next.
    ///
    /// > If an arrow-like figure is used, it should point towards the right.
    ///
    /// So the skin draws it pointing right and the gap between the two notes
    /// says which way right is, exactly as the reverse arrow works. Untinted:
    /// it belongs to the map's shape rather than to a combo.
    FollowPoint,
    /// A slider's own ends, which osu! lets a skin draw differently from a
    /// note: `sliderstartcircle` and `sliderendcircle`, each with an overlay.
    ///
    /// > Overrides `hitcircle.png` for the start of the slider, if skinned.
    ///
    /// "If skinned" is the whole rule, and it binds the pair: the wiki says an
    /// overlay *requires* its own base to function, so a skin shipping
    /// `sliderstartcircleoverlay` alone gets neither and falls back to the
    /// note's pair. They are two ways of saying one thing — a slider end is
    /// drawn from the note's pictures or from its own, never half of each.
    ///
    /// Tinted like the note they override, and for the same reason: they are
    /// the disc, and the disc is what the combo colour lands on.
    SliderHead,
    SliderHeadOverlay,
    SliderTail,
    SliderTailOverlay,
    /// The ball that runs along a slider, white — the game tints it.
    SliderBall,
    /// The ring around the ball that shows how far the cursor may stray.
    /// Untinted: it is the game speaking about tracking rather than the map
    /// speaking about a combo.
    SliderFollowCircle,
    /// The cursor's own disc. osu! rotates and expands this one on a click —
    /// rotation is invisible on a circle, and the expansion is exactly what our
    /// cursor does under the hand anyway.
    Cursor,
    /// The still centre, which osu! draws *above* the cursor and never expands.
    /// Our white middle, so it stays a crisp point while the disc swells.
    CursorMiddle,
    /// What the cursor leaves behind it.
    CursorTrail,
    /// A judgement, as the game flashes it at a note: `hit300`, `hit100`,
    /// `hit50`, `hit0`, and the variants shown at the end of a combo section.
    Verdict(Verdict),
    /// The disc a spinner turns around, in whichever style the skin is drawn
    /// in.
    ///
    /// osu! has two and they are not mixable. A skin shipping
    /// `spinner-background` is drawn in the old one, where the middle is
    /// `spinner-circle`; without it the skin is new-style and the middle is
    /// `spinner-middle`. Both are read because a skin exported from lazer
    /// carries both sets, and only the style it declares decides which is
    /// actually its own.
    SpinnerCircle,
    SpinnerMiddle,
    /// The new style's second middle, which *turns*. `spinner-middle` is the
    /// still part and this is the part that reports the spin — a skin drawing
    /// a needle or a mark puts it here, and drawn without its rotation it says
    /// the opposite of what it is for.
    SpinnerMiddle2,
    /// The old style's backdrop. Its presence is also how a skin says it is
    /// drawn in that style at all.
    SpinnerBackground,
    /// The old style's gauge: a picture revealed from the bottom up as the
    /// spinner is turned, rather than one placed. It is the only element in a
    /// skin whose *height* carries a reading.
    SpinnerMetre,
    /// The new style's layers, under and over the middle.
    SpinnerBottom,
    SpinnerGlow,
    SpinnerTop,
    /// The `RPM` label beside the count of turns.
    SpinnerRpm,
    /// The banner a break ends on: whether the play is passing at that moment.
    ///
    /// Shown over the middle of the screen towards the end of a long enough
    /// break — the game's own word for how the play is going, said once and
    /// taken away again. Which of the two appears is decided on health alone.
    SectionPass,
    SectionFail,
    /// The ring that closes in on a spinner.
    ///
    /// The only part of the new-style spinner written. The rest of its layers
    /// — the bottom, the top, the two middles, the glow — have neither
    /// documented sizes nor a documented stacking order, and a spinner guessed
    /// at wrong looks worse than the game's own, which is what those fall back
    /// to. This one has both: 384 square, and it plainly does what our ring
    /// does.
    SpinnerApproachCircle,
    /// The health bar's own pieces: the frame behind it, the fill that runs
    /// along it, and the mark at the fill's end that changes as health falls.
    ///
    /// Four files rather than one because the game animates them separately —
    /// the fill slides, the mark swaps between three pictures — and because a
    /// skin turns any of them off on its own. The one this was read against
    /// blanks the frame and the marker and keeps only the fill and the mark.
    ScoreBarBackground,
    ScoreBarFill,
    ScoreBarMark(Health),
    /// One glyph of the skin's own HUD lettering: `score-0`..`score-9`,
    /// `score-comma`, `score-dot`, `score-percent`, `score-x`.
    ///
    /// A separate set from the combo digits on purpose — osu! skins them
    /// separately, and they usually look nothing alike: the note digits are
    /// large and decorative, these are small and meant to be read at a glance
    /// in a corner.
    Score(char),
    /// A combo number, `0` to `9`.
    ///
    /// Unlike everything else here the canvas is not square and not fixed: the
    /// game lays multi-digit numbers out side by side from the sprites' own
    /// widths, so a digit padded into a square would be spaced across the note
    /// with holes between the figures. Each is cut to its own glyph, and the
    /// padding that remains is given back through `HitCircleOverlap`.
    Digit(u8),
}

/// Which judgement a `hit*.png` carries.
///
/// The `k` and `g` variants are what the game shows when a combo section ends
/// perfectly; they are the same mark as the plain one here, so a section ending
/// does not suddenly change typeface.
/// How close a play is to ending, as the health bar's mark shows it.
///
/// osu! ships three: the ordinary one, and two that say a play is nearly over.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Health {
    Fine,
    Low,
    Critical,
}

impl Health {
    /// Which mark a fraction of health calls for. The thresholds are osu!'s
    /// own: the first warning at half, the second at a fifth.
    pub fn of(fraction: f32) -> Self {
        if fraction < 0.2 {
            Self::Critical
        } else if fraction < 0.5 {
            Self::Low
        } else {
            Self::Fine
        }
    }

    fn stem(self) -> &'static str {
        match self {
            Self::Fine => "scorebar-ki",
            Self::Low => "scorebar-kidanger",
            Self::Critical => "scorebar-kidanger2",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Verdict {
    Miss,
    Fifty,
    Hundred,
    HundredKatu,
    Three,
    ThreeKatu,
    ThreeGeki,
}

impl Verdict {
    pub const ALL: [Self; 7] = [
        Self::Miss,
        Self::Fifty,
        Self::Hundred,
        Self::HundredKatu,
        Self::Three,
        Self::ThreeKatu,
        Self::ThreeGeki,
    ];

    fn stem(self) -> &'static str {
        match self {
            Self::Miss => "hit0",
            Self::Fifty => "hit50",
            Self::Hundred => "hit100",
            Self::HundredKatu => "hit100k",
            Self::Three => "hit300",
            Self::ThreeKatu => "hit300k",
            Self::ThreeGeki => "hit300g",
        }
    }

    /// The mark, and how big it is against the note's radius — the same text
    /// and the same proportions the renderer flashes.
    fn mark(self) -> (&'static str, f32) {
        match self {
            Self::Miss => ("×", 0.85),
            Self::Fifty => ("50", 0.46),
            Self::Hundred | Self::HundredKatu => ("100", 0.42),
            Self::Three | Self::ThreeKatu | Self::ThreeGeki => ("300", 0.42),
        }
    }
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
            Self::InputOverlayBackground => "inputoverlay-background".to_owned(),
            Self::InputOverlayKey => "inputoverlay-key".to_owned(),
            Self::FollowPoint => "followpoint".to_owned(),
            Self::Lighting => "lighting".to_owned(),
            Self::SliderHead => "sliderstartcircle".to_owned(),
            Self::SliderHeadOverlay => "sliderstartcircleoverlay".to_owned(),
            Self::SliderTail => "sliderendcircle".to_owned(),
            Self::SliderTailOverlay => "sliderendcircleoverlay".to_owned(),
            Self::SliderBall => "sliderb".to_owned(),
            Self::SliderFollowCircle => "sliderfollowcircle".to_owned(),
            Self::Cursor => "cursor".to_owned(),
            Self::CursorMiddle => "cursormiddle".to_owned(),
            Self::CursorTrail => "cursortrail".to_owned(),
            Self::Verdict(v) => v.stem().to_owned(),
            Self::SpinnerApproachCircle => "spinner-approachcircle".to_owned(),
            Self::SpinnerCircle => "spinner-circle".to_owned(),
            Self::SpinnerMiddle => "spinner-middle".to_owned(),
            Self::SpinnerMiddle2 => "spinner-middle2".to_owned(),
            Self::SpinnerBackground => "spinner-background".to_owned(),
            Self::SpinnerMetre => "spinner-metre".to_owned(),
            Self::SpinnerBottom => "spinner-bottom".to_owned(),
            Self::SpinnerGlow => "spinner-glow".to_owned(),
            Self::SpinnerTop => "spinner-top".to_owned(),
            Self::SpinnerRpm => "spinner-rpm".to_owned(),
            Self::SectionPass => "section-pass".to_owned(),
            Self::SectionFail => "section-fail".to_owned(),
            Self::ScoreBarBackground => "scorebar-bg".to_owned(),
            Self::ScoreBarFill => "scorebar-colour".to_owned(),
            Self::ScoreBarMark(state) => state.stem().to_owned(),
            Self::Score(c) => match c {
                ',' => "score-comma".to_owned(),
                '.' => "score-dot".to_owned(),
                '%' => "score-percent".to_owned(),
                'x' => "score-x".to_owned(),
                other => format!("score-{other}"),
            },
            Self::Digit(n) => format!("default-{n}"),
        }
    }

    /// The same, under the names *this* skin gives its digit sets.
    ///
    /// Everything but the digits is named by the game and comes back unchanged.
    /// The digits are named by the skin — `[Fonts] HitCirclePrefix` and
    /// `ScorePrefix` — and a skin that renames them is a skin whose numbers
    /// were invisible to us before this existed.
    pub fn stem_with(self, ini: &crate::imported::Ini) -> String {
        match self {
            Self::Digit(n) => format!("{}-{n}", ini.hit_circle_prefix),
            Self::Score(c) => {
                // The score face, and the combo counter's too.
                //
                // osu! lets a skin name them apart — `ComboPrefix` against
                // `ScorePrefix` — and this does not, because the sprite store
                // is keyed by element and one `Score(c)` cannot hold two
                // pictures. Skins that name them apart exist and are rare; the
                // one this was written against sets both to `num\berlin`. What
                // it *does* honour is the two overlaps, which that skin sets to
                // 0 and 5 and which are visible on every frame.
                let prefix = &ini.score_prefix;
                match c {
                    ',' => format!("{prefix}-comma"),
                    '.' => format!("{prefix}-dot"),
                    '%' => format!("{prefix}-percent"),
                    'x' => format!("{prefix}-x"),
                    other => format!("{prefix}-{other}"),
                }
            }
            other => other.stem(),
        }
    }


    /// Whether the game multiplies the combo colour through this element.
    ///
    /// The same split the exporter works to, read the other way: the tinted
    /// ones are written white so the game can colour them, so on the way *in*
    /// they are white and have to be coloured here. Getting this wrong is not
    /// subtle — an untinted element run through the palette comes out muddy,
    /// and a tinted one left white stays white through every combo.
    pub fn is_tinted(self) -> bool {
        matches!(
            self,
            Self::HitCircle
                | Self::ApproachCircle
                | Self::SliderBall
                | Self::SliderHead
                | Self::SliderTail
                | Self::Lighting
        )
    }

    /// The size osu! draws this element at, in the format's own pixels. The
    /// high-resolution `@2x` file is exactly twice this on each side.
    ///
    /// For a digit this is the hit circle it is sized against rather than the
    /// canvas, which is cut to the glyph.
    pub fn size(self) -> u32 {
        match self {
            Self::HitCircle | Self::HitCircleOverlay | Self::ReverseArrow => 128,
            // The wiki gives a slider's own ends the same 128 square as the
            // note they stand in for, which is the point of them.
            Self::SliderHead
            | Self::SliderHeadOverlay
            | Self::SliderTail
            | Self::SliderTailOverlay => 128,
            Self::ApproachCircle => 126,
            Self::SliderScorePoint => 16,
            Self::FollowPoint => 64,
            // lazer's own: `Height = Width = 46`, "matching the default skin
            // asset". The plate is read at whatever size it was drawn.
            Self::InputOverlayKey => 46,
            Self::InputOverlayBackground => 64,
            // The wiki's own suggested size for it.
            Self::Lighting => 100,
            Self::SliderBall => 128,
            Self::SliderFollowCircle => 256,
            // Never exported, and read at whatever size the skin drew it —
            // the interface is scaled to the frame rather than to a note.
            Self::Score(_) => 64,
            Self::ScoreBarBackground | Self::ScoreBarFill => 640,
            Self::SpinnerCircle | Self::SpinnerMiddle | Self::SpinnerMiddle2 => 666,
            Self::SpinnerBackground => 640,
            Self::SpinnerBottom | Self::SpinnerGlow | Self::SpinnerTop => 666,
            Self::SpinnerMetre => 1024,
            Self::SpinnerRpm => 256,
            Self::SectionPass | Self::SectionFail => 800,
            Self::ScoreBarMark(_) => 160,
            Self::Cursor | Self::CursorMiddle => 128,
            Self::CursorTrail => 64,
            Self::SpinnerApproachCircle => 384,
            Self::Verdict(_) | Self::Digit(_) => DIGIT_REFERENCE as u32,
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
            glow(&mut pixmap, half, half, radius, skin.trail_colour, 1.0, 0.9);
            dot(&mut pixmap, half, half, radius, skin.trail_colour, 1.0);
        }
        Element::CursorMiddle => {
            // Held or not, this one keeps its size: the game never expands it.
            dot(&mut pixmap, half, half, half * 0.25, skin.cursor, 1.0);
        }
        Element::CursorTrail => {
            dot(&mut pixmap, half, half, half * 0.5, skin.trail_colour, 0.55);
        }
        Element::SpinnerApproachCircle => {
            let width = (size as f32 * 0.02).max(2.0);
            ring(&mut pixmap, half, half, half - width, width, skin.spinner, 1.0);
        }
        // Read but not written. A skin can hand us a ball and a follow circle
        // and the renderer will use them; going the other way would mean
        // exporting shapes drawn nowhere else, and an exported skin is meant to
        // be what our renders look like rather than a fuller set than we draw.
        Element::SliderBall
        | Element::SliderFollowCircle
        // Neither has ever been part of our own look, and inventing shapes for
        // them here would put a dotted trail and a flash on every render made
        // without a skin — a redecoration, not a fix.
        | Element::FollowPoint
        | Element::Lighting
        // The key overlay is ours end to end — a column of plates with the
        // counts on them — and nothing in it is shaped like osu!'s two files.
        | Element::InputOverlayBackground
        | Element::InputOverlayKey
        // Our own look draws a slider's ends from the note's pictures, which
        // is what a skin without these gets from osu! too. Exporting them
        // would be exporting a copy under another name.
        | Element::SliderHead
        | Element::SliderHeadOverlay
        | Element::SliderTail
        | Element::SliderTailOverlay
        | Element::Score(_)
        | Element::ScoreBarBackground
        | Element::ScoreBarFill
        | Element::ScoreBarMark(_)
        | Element::SpinnerCircle
        | Element::SpinnerMiddle
        | Element::SpinnerMiddle2
        | Element::SpinnerBackground
        | Element::SpinnerMetre
        | Element::SpinnerBottom
        | Element::SpinnerGlow
        | Element::SpinnerTop
        | Element::SpinnerRpm
        // A banner is a piece of lettering the skin either has or has not.
        // Ours would be a design decision rather than a fallback, so a skin
        // without one simply shows nothing at its breaks.
        | Element::SectionPass
        | Element::SectionFail => return None,
        Element::Verdict(_) | Element::Digit(_) => return lettered(skin, element, size),
    }
    Some(pixmap)
}

/// The elements that are a piece of text: the combo digits and the judgements.
///
/// Both are cut to their own glyph rather than padded into a square, because
/// the game lays a multi-digit number out from the sprites' widths — squared
/// off, `100` reads with holes between its figures.
fn lettered(skin: &crate::skin::Skin, element: Element, size: u32) -> Option<Pixmap> {
    let font = skin.font.as_ref()?;
    let (text, share, colour) = match element {
        Element::Digit(value) => (
            value.to_string(),
            // The proportion the renderer draws a combo number at — nine
            // tenths of the circle's radius — divided by the 0.8 the game
            // shrinks every digit by, so the figure arrives the size it was
            // drawn to be.
            0.47 / 0.8,
            skin.circle_border,
        ),
        Element::Verdict(verdict) => {
            // A 300 the skin does not flash is written as an empty canvas
            // rather than left out: absent, the game would fall back to its
            // own and mark every note in a clean play — which is the thing
            // this skin deliberately does not do.
            if matches!(
                verdict,
                Verdict::Three | Verdict::ThreeKatu | Verdict::ThreeGeki
            ) && !skin.show_300
            {
                return Pixmap::new(size / 4, size / 4);
            }
            let (text, scale) = verdict.mark();
            let colour = match verdict {
                Verdict::Miss => skin.verdict_miss,
                Verdict::Fifty => skin.verdict_50,
                Verdict::Hundred | Verdict::HundredKatu => skin.verdict_100,
                Verdict::Three | Verdict::ThreeKatu | Verdict::ThreeGeki => skin.verdict_300,
            };
            // Half, because the renderer's scale is against the note's radius
            // and this reference is its whole width.
            (text.to_owned(), scale / 2.0, colour)
        }
        _ => return None,
    };

    let scale = size as f32 / DIGIT_REFERENCE;
    // The canvas is the plain size rounded once and then multiplied, never
    // rounded twice. Sized independently at each resolution the two disagreed
    // by a pixel — 58×60 against 115×119 — and `@2x` means exactly twice, not
    // about twice.
    let plain = DIGIT_REFERENCE * share;
    let unit_width = (font.width(&text, plain) + DIGIT_PADDING * 2.0).ceil();
    let unit_height = (font.digit_height(plain) + DIGIT_PADDING * 2.0).ceil();
    let mut pixmap = Pixmap::new(
        (unit_width * scale).round() as u32,
        (unit_height * scale).round() as u32,
    )?;
    font.draw(
        &mut pixmap,
        crate::text::Label {
            text: &text,
            x: unit_width * scale / 2.0,
            y: DIGIT_PADDING * scale + font.digit_height(plain * scale),
            size: plain * scale,
            colour,
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

#[cfg(test)]
mod health_marks {
    use super::*;

    #[test]
    fn the_mark_changes_at_the_thresholds_the_game_uses() {
        // Three pictures, two lines. A skin draws them differently on purpose —
        // the one this was read against gets progressively more alarmed — so
        // picking the wrong one is a play that looks safe while it is ending.
        assert_eq!(Health::of(1.0), Health::Fine);
        assert_eq!(Health::of(0.5), Health::Fine);
        assert_eq!(Health::of(0.49), Health::Low);
        assert_eq!(Health::of(0.2), Health::Low);
        assert_eq!(Health::of(0.19), Health::Critical);
        assert_eq!(Health::of(0.0), Health::Critical);
    }

    #[test]
    fn each_mark_reads_its_own_file() {
        // Three names, and none of them is a suffix of another in a way that
        // would let a loose match pick the wrong one.
        let names: Vec<String> = [Health::Fine, Health::Low, Health::Critical]
            .map(|h| Element::ScoreBarMark(h).stem())
            .to_vec();
        assert_eq!(
            names,
            ["scorebar-ki", "scorebar-kidanger", "scorebar-kidanger2"]
        );
    }

    #[test]
    fn the_bar_is_three_separate_files() {
        // Each is optional and each means something different when absent: the
        // skin this was read against blanks its frame and keeps its fill.
        assert_eq!(Element::ScoreBarBackground.stem(), "scorebar-bg");
        assert_eq!(Element::ScoreBarFill.stem(), "scorebar-colour");
    }
}
