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
    /// A flat colour for the slider body, when a skin states one.
    ///
    /// `None` derives it from the combo colour, which is what osu! does and
    /// what every skin without a `SliderTrackOverride` wants. Set, it replaces
    /// the derivation outright — a skin asking for black is not asking for a
    /// darker shade of the combo.
    /// Whether a struck note throws light back off the field.
    ///
    /// osu! makes this a setting rather than a fact about a skin —
    /// `config.Get<bool>(OsuSetting.HitLighting)` — and so does this. Off,
    /// because a render is watched: on a dense map the flashes last more than
    /// a second apiece, so a dozen of them are up at once and the play is
    /// behind them. The skin's `lighting.png` is read either way, so turning
    /// this on is the whole of what it takes.
    pub hit_lighting: bool,
    /// The movements a viewer may want and a player needs.
    ///
    /// Each one is a thing osu! either does or offers as a setting, and each is
    /// off or on here to suit *watching* rather than playing — see
    /// [`Effects`], which is where the defaults and their reasons live. They
    /// are separate fields rather than a set so the drawing code asks a
    /// question with a name rather than looking something up by string on the
    /// hot path of every frame.
    pub snake_in: bool,
    pub snake_out: bool,
    pub cursor_expand: bool,
    pub cursor_trail: bool,
    pub keypad: bool,
    pub key_bars: bool,
    pub unstable_rate: bool,
    pub slider_body: Option<Color>,
    /// The slider body is the combo colour darkened by this much.
    pub slider_body_dim: f32,
    pub slider_body_alpha: f32,
    /// Border thickness as a fraction of the circle radius.
    pub border_ratio: f32,
    /// Which reverse arrow to draw.
    pub arrow: ArrowShape,
    pub cursor: Color,
    pub trail_colour: Color,
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
    /// How big the hit-error meter is drawn, as a multiple of its own size.
    ///
    /// The whole meter moves together — the coloured bands, the ticks on them,
    /// the centre line and the unstable rate over it — so at 2.0 it is twice
    /// the thing it was and not a wider bar with the same fine ticks on it.
    /// It grows from its baseline and from its centre, which is what keeps it
    /// where it was on the frame at every setting.
    ///
    /// This is nobody's rule. osu! has the same knob — `Options_ScoreMeterScale`
    /// is in the client's own strings — but it is a preference of whoever was
    /// sitting at the machine, and a replay does not record it, so there is no
    /// value here that a play can be said to *have*. It is the viewer's, and
    /// the range this engine accepts is its own choice rather than something
    /// read out of stable.
    /// How big the cursor is drawn, as a multiple of the size the skin drew it.
    ///
    /// osu! has the same knob and calls it `Cursor size`. It moves the cursor,
    /// its middle and its trail together, because they are one thing — and the
    /// trail's spacing follows, since the game lays its marks one every
    /// `DisplayWidth * CursorScale / 2.5` and this is that `CursorScale`.
    pub cursor_scale: f32,
    pub meter_scale: f32,
    /// Whether to date an imported skin the way osu! does, rather than drawing
    /// it by the newest rules — see [`effective_version`](crate::imported::effective_version).
    pub skin_version_as_written: bool,
    /// Whether the slider ball wears the combo's colour, overruling the skin —
    /// see [`Effects`].
    pub slider_ball_tint: bool,
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
            hit_lighting: false,
            snake_in: false,
            snake_out: false,
            cursor_expand: false,
            cursor_trail: true,
            keypad: true,
            key_bars: true,
            unstable_rate: true,
            slider_body: None,
            slider_body_dim: 0.35,
            slider_body_alpha: 0.70,
            border_ratio: 0.11,
            arrow: ArrowShape::Triangle,
            cursor: rgb(255, 255, 255),
            trail_colour: rgb(255, 190, 190),
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
            cursor_scale: 1.0,
            meter_scale: 1.0,
            skin_version_as_written: false,
            slider_ball_tint: false,
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
/// The two shades a slider body is drawn between, as danser computes them.
///
/// Ours were invented and looked it: the rim was darkened by a third and the
/// centre lifted a little, which made a body that reads as a dark stripe. The
/// game's is nearly the opposite — the rim is barely darker than the track and
/// the centre is lifted a long way, which is what makes a slider look like a
/// tube with light down it.
///
/// From `danser-go`, `app/beatmap/objects/slider.go`:
///
/// ```go
/// bodyOuter = baseTrack.Shade2(-0.1)
/// bodyInner = baseTrack.Shade2(0.5)
/// ```
///
/// with `Shade2` in `framework/math/color/color.go` resolving to `Darken(0.1)`
/// and `Lighten2(0.5)`:
///
/// ```go
/// func (c Color) Darken(amount float32) Color {
///     scale := max(1.0, 1.0+amount)
///     return NewRGBA(c.R/scale, c.G/scale, c.B/scale, c.A)
/// }
///
/// func (c Color) Lighten2(amount float32) Color {
///     amount *= 0.5
///     scale := 1.0 + 0.5*amount
///     return NewRGBA(min(1.0, c.R*scale+amount), ..., c.A)
/// }
/// ```
///
/// Written out rather than expressed through this file's own `darken` and
/// `lighten`, which are a different arithmetic: those interpolate towards
/// black and white, these scale and offset. Reusing them would have been the
/// same mistake in a new place.
pub fn body_outer(track: Color) -> Color {
    let scale = 1.1;
    Color::from_rgba(
        track.red() / scale,
        track.green() / scale,
        track.blue() / scale,
        track.alpha(),
    )
    .unwrap_or(track)
}

pub fn body_inner(track: Color) -> Color {
    // `Lighten2(0.5)`: the amount halves to 0.25, the scale becomes 1.125, and
    // the offset is what lifts a black track off black at all.
    let (amount, scale) = (0.25f32, 1.125f32);
    let lift = |c: f32| (c * scale + amount).min(1.0);
    Color::from_rgba(
        lift(track.red()),
        lift(track.green()),
        lift(track.blue()),
        track.alpha(),
    )
    .unwrap_or(track)
}

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

#[cfg(test)]
mod body_shades {
    use super::*;

    #[test]
    fn a_black_track_still_has_light_down_the_middle() {
        // The case that exposed the old arithmetic. Our shades interpolated
        // towards black and white, so a black track stayed black at both ends
        // and the body came out as a flat stripe. The game's centre shade adds
        // an offset, which lifts black off black.
        let black = Color::from_rgba8(0, 0, 0, 255);
        assert_eq!(body_outer(black).red(), 0.0);
        assert!(
            (body_inner(black).red() - 0.25).abs() < 1e-6,
            "{}",
            body_inner(black).red()
        );
    }

    #[test]
    fn the_rim_is_barely_darker_than_the_track() {
        // A tenth, not a third. Ours took a third off and made every body read
        // as a dark stripe rather than as a lit tube.
        let blue = Color::from_rgba8(100, 150, 250, 255);
        let rim = body_outer(blue);
        assert!((rim.red() - blue.red() / 1.1).abs() < 1e-6);
        assert!(rim.red() > blue.red() * 0.85, "only slightly darker");
    }

    #[test]
    fn the_centre_is_lifted_a_long_way_and_never_past_white() {
        let blue = Color::from_rgba8(100, 150, 250, 255);
        let core = body_inner(blue);
        assert!(core.green() > blue.green(), "lighter than the track");
        for channel in [core.red(), core.green(), core.blue()] {
            assert!((0.0..=1.0).contains(&channel), "{channel}");
        }
        // A track already near white cannot overflow.
        let pale = Color::from_rgba8(250, 250, 250, 255);
        assert!(body_inner(pale).red() <= 1.0);
    }

    #[test]
    fn the_middle_is_always_lighter_than_the_rim() {
        // The whole point of the pair. Whatever the track, the tube has to read
        // as lit from within rather than as two arbitrary shades.
        for (r, g, b) in [(0, 0, 0), (255, 255, 255), (12, 200, 40), (200, 30, 90)] {
            let track = Color::from_rgba8(r, g, b, 255);
            let (rim, core) = (body_outer(track), body_inner(track));
            assert!(
                core.red() >= rim.red()
                    && core.green() >= rim.green()
                    && core.blue() >= rim.blue(),
                "{r},{g},{b}"
            );
        }
    }
}


/// The optional movements, by the names a command line and a settings screen
/// both use.
///
/// One list rather than a flag apiece, because this is a set that grows: every
/// small thing somebody might want to switch off is a name here, an entry in a
/// menu and nothing else — no new argument, no new column in a database, no
/// second place to forget.
///
/// The defaults are what a render ships with, and each is chosen for watching
/// rather than for playing:
///
/// * `snake-in` — a body growing out of its head. osu! has it on: it says
///   *where a slider goes* to somebody who must read that in the half second
///   before they hit it. Off, for a viewer who has no such half second.
/// * `snake-out` — a body retracting behind the ball, which says how much is
///   left to play. Off, for the same reason.
/// * `cursor-expand` — the cursor swelling under a click. osu!'s own default is
///   on and a skin may refuse it with `CursorExpand: 0`; off here, and a skin
///   that refuses still refuses when it is on.
/// * `cursor-trail` — on by default.
/// * `keypad` — the column of keys in the corner, with its counts. On. osu!
///   has one and a viewer usually wants it, but a render made to be looked at
///   rather than read is entitled to a bare field.
/// * `key-bars` — the bars that run out of the keypad under a press. Ours, not
///   the game's: osu! has no such readout. On, and only drawn beside our own
///   keypad — a skin that brought a panel of its own gets that panel and not
///   two interfaces at once.
/// * `unstable-rate` — the spread of the timing errors, over the meter that is
///   a picture of it. On.
/// * `hit-lighting` — the flash a struck note throws. osu! makes it a setting
///   too; off, because on a dense map a dozen are up at once.
/// * `slider-ball-tint` — the ball wearing the combo's colour. osu! leaves this
///   to the skin, through `AllowSliderBallTint`, and almost every skin says
///   nothing, which means no. Unlike `cursor-expand` this *overrules* the skin
///   rather than granting it permission: a viewer asking for coloured balls is
///   asking about the video in front of them, not about what the skin's author
///   intended, and permission would mean the switch did nothing on nearly every
///   skin there is. Off by default, which is where the skins leave it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Effects;

impl Effects {
    /// Every name this understands, in the order a menu should show them.
    pub const ALL: [&'static str; 9] = [
        "snake-in",
        "snake-out",
        "cursor-expand",
        "cursor-trail",
        "keypad",
        "key-bars",
        "unstable-rate",
        "hit-lighting",
        "slider-ball-tint",
    ];

    /// Turn a comma-separated list into the flags it names, leaving everything
    /// it does not name switched off.
    ///
    /// Absent from a command line the skin keeps its own defaults, which is not
    /// the same as an empty list: an empty list is somebody having switched
    /// everything off, and it is obeyed.
    pub fn apply(skin: &mut Skin, list: &str) {
        let named: Vec<&str> = list
            .split(',')
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .collect();
        let on = |name: &str| named.contains(&name);
        skin.snake_in = on("snake-in");
        skin.snake_out = on("snake-out");
        skin.cursor_expand = on("cursor-expand");
        skin.cursor_trail = on("cursor-trail");
        skin.keypad = on("keypad");
        skin.key_bars = on("key-bars");
        skin.unstable_rate = on("unstable-rate");
        skin.hit_lighting = on("hit-lighting");
        skin.slider_ball_tint = on("slider-ball-tint");
    }

    /// Whether a list names one effect, without applying the rest.
    ///
    /// For the one decision that has to be made *before* a skin is assembled:
    /// the tinted pictures are built when the sprites are read, and a switch
    /// consulted afterwards would be consulted too late.
    pub fn asked_for(list: &str, name: &str) -> bool {
        list.split(',').map(str::trim).any(|named| named == name)
    }

    /// Which names a skin currently has switched on, for reporting back.
    pub fn of(skin: &Skin) -> Vec<&'static str> {
        let mut on = Vec::new();
        for (name, set) in [
            ("snake-in", skin.snake_in),
            ("snake-out", skin.snake_out),
            ("cursor-expand", skin.cursor_expand),
            ("cursor-trail", skin.cursor_trail),
            ("keypad", skin.keypad),
            ("key-bars", skin.key_bars),
            ("unstable-rate", skin.unstable_rate),
            ("hit-lighting", skin.hit_lighting),
            ("slider-ball-tint", skin.slider_ball_tint),
        ] {
            if set {
                on.push(name);
            }
        }
        on
    }
}
