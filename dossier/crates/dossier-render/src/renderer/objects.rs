//! Everything on the playfield: the notes, sliders, spinners and cursor, and
//! the fade maths that decides how visible each is at a given instant.
//!
//! This is the largest of the renderer's parts and the most self-contained. It
//! reads the play — where an object is, when it was hit, whether Hidden was on
//! — and turns one object into pixels. It borrows the frame's shared vocabulary
//! (`fade`, `unit`, the `Turn` and `Annotation` types, every timing constant)
//! from the parent module rather than restating it, which is what `use
//! super::*` is for.
//!
//! Four methods are `pub(super)` because the frame's orchestration calls them:
//! `draw_object` and `draw_cursor` from the play pass, `alpha_of` to decide
//! whether an object is worth drawing at all, and `draw_chevron` from the break
//! warning, which draws the same arrow a reverse does.

use super::*;

use dossier_beatmap::Point;
use dossier_sim::{GameState, TimedKind, TimedObject};
use tiny_skia::{
    Color, LineCap, LineJoin, Paint, PathBuilder, Pixmap, PixmapPaint, Shader, Stroke, Transform,
};

use crate::elements::Element;
use crate::layout::Layout;
use crate::skin::{darken, with_alpha, ArrowShape};

/// The hit circle every skin is drawn against, in the format's own pixels.
/// Not a number this renderer chose — it is the size osu! itself works to, and
/// every other element in a skin is proportioned by it.
const SKIN_CIRCLE_PIXELS: f32 = 128.0;

/// What the game multiplies a hit circle's own lettering by, and the largest
/// glyph it will draw. From `ppy/osu`,
/// `osu.Game.Rulesets.Osu/Skinning/Legacy/OsuLegacySkinTransformer.cs`:
///
/// ```csharp
/// const float hitcircle_text_scale = 0.8f;
/// // stable applies a blanket 0.8x scale to hitcircle fonts
/// Scale = new Vector2(hitcircle_text_scale),
/// MaxSizePerGlyph = OsuHitObject.OBJECT_DIMENSIONS * 2 / hitcircle_text_scale,
/// ```
///
/// Missing it made every skinned note a quarter larger than the slider bodies
/// beside it — visible on the skin this was read against, whose digits are
/// 160px and are drawn to be exactly a note once this factor is applied.
const DIGIT_SCALE: f32 = 0.8;
const DIGIT_MAX_PIXELS: f32 = 64.0 * 2.0 / DIGIT_SCALE;

/// The cross-section of a slider body, in fractions of its half-width, as
/// danser's own fragment shader states them — `assets/shaders/slidercolor.fsh`:
///
/// ```glsl
/// #define borderStart 0.06640625f      // 34/512
/// #define baseBorderWidth 0.126953125f // 65/512
/// #define blend 0.01f
/// ```
///
/// Measured from the outer edge inwards: a soft shadow, then the border, then
/// the body all the way to the centreline, with a hair of crossfade at each
/// join so no boundary reads as a line.
const SHADOW_PORTION: f32 = 1.0 - 59.0 / 64.0;
const BORDER_PORTION: f32 = 0.1875;
/// How dark the shadow gets at its inner end.
const SHADOW_ALPHA: f32 = 0.25;

/// The colour of a slider body at `towards`, where 0 is its outer edge and 1
/// its centreline.
///
/// stable's own, by way of lazer's legacy body, which cites the stable source
/// it was copied from:
///
/// ```csharp
/// Color4 shadow = new Color4(0, 0, 0, 0.25f);
/// Color4 outerColour = AccentColour.Darken(0.1f);
/// Color4 innerColour = lighten(AccentColour, 0.5f);
///
/// // https://github.com/peppy/osu-stable-reference/…/MmSliderRendererGL.cs#L59-L70
/// const float shadow_portion = 1 - (OsuLegacySkinTransformer.LEGACY_CIRCLE_RADIUS
///                                   / OsuHitObject.OBJECT_RADIUS);
/// const float border_portion = 0.1875f;
///
/// if (position <= shadow_portion)
///     return InterpolateNonLinear(position, Black.Opacity(0f), shadow, 0, shadow_portion);
/// if (position <= border_portion)
///     return BorderColour;
/// return InterpolateNonLinear(position, outerColour, innerColour, border_portion, 1);
/// ```
///
/// `LEGACY_CIRCLE_RADIUS` is `OBJECT_RADIUS - 5` and `OBJECT_RADIUS` is 64, so
/// the shadow is the outermost five sixty-fourths. `InterpolateNonLinear` with
/// no easing is a plain mix; what is non-linear about it is that it happens in
/// sRGB rather than in linear light, which is what mixing two `Color`s here
/// does too.
///
/// This followed danser's shader before — its zones are close (0.066 and 0.193
/// against 0.078 and 0.1875) but three other things were not. The shadow went
/// twice as dark. The ramp from the border to the centreline was squared rather
/// than straight, on the reasoning that a linear one "reads as a wide pale
/// core" — a preference, and one a side-by-side against the client overrules.
/// And the border was crossfaded into its neighbours over a hundredth of the
/// radius, which is exactly the crisp line that comparison showed missing.
fn tube_shade(
    towards: f32,
    border: Color,
    body_outer: Color,
    body_inner: Color,
    body_alpha: f32,
) -> Color {
    if towards <= SHADOW_PORTION {
        // Black coming up from nothing at the very edge. It is what seats a
        // slider on the field instead of pasting it on.
        return with_alpha(
            Color::from_rgba8(0, 0, 0, 255),
            SHADOW_ALPHA * towards / SHADOW_PORTION,
        );
    }
    if towards <= BORDER_PORTION {
        // Solid, with no crossfade at either edge. The hard boundary is the
        // point of it.
        return border;
    }
    let along = ((towards - BORDER_PORTION) / (1.0 - BORDER_PORTION)).clamp(0.0, 1.0);
    with_alpha(blend(body_outer, body_inner, along), body_alpha)
}

/// Which of a map's three circles is being drawn.
///
/// osu! lets a skin draw a slider's two ends differently from a note, so the
/// three are not interchangeable even though they are the same shape. Named
/// rather than passed as a pair of flags: the call sites read as what they
/// are, and there is no fourth case to invent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Face {
    Note,
    Head,
    Tail,
}

impl Scene<'_> {
    /// The two opacities, for tests that need to compare them.
    #[doc(hidden)]
    pub fn alpha_for_test(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_of(index, time_ms)
    }

    #[doc(hidden)]
    pub fn head_alpha_for_test(&self, index: usize, time_ms: f64) -> f32 {
        self.head_alpha(index, time_ms)
    }

    /// Opacity of an object: zero before it spawns and after it has faded.
    pub(super) fn alpha_of(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::Own)
    }

    fn alpha_at(&self, index: usize, time_ms: f64, hidden: HiddenFade) -> f32 {
        let annotation = &self.annotations[index];
        if time_ms < annotation.spawn_ms || time_ms > annotation.gone_ms {
            return 0.0;
        }
        // A slider stays whole until its own end even if the head was judged
        // long before; only then does the fade start.
        let leaves = annotation.gone_ms - HIT_FADE_MS;
        let fade_in = if self.hidden {
            self.state.difficulty().preempt_ms() * HIDDEN_FADE_IN
        } else {
            self.state.difficulty().fade_in_ms()
        }
        .max(1.0);
        let appearing = ((time_ms - annotation.spawn_ms) / fade_in).clamp(0.0, 1.0) as f32;
        // Straight, not eased. `this.FadeOut(240)` with no easing is a linear
        // ramp; ours squared it, so a note was at a quarter of its strength by
        // the time it was half way through swelling and all but invisible while
        // the animation still had a third to run. Reported as notes going
        // before their animation does.
        let leaving = 1.0 - (((time_ms - leaves) / HIT_FADE_MS).clamp(0.0, 1.0)) as f32;

        // Hidden takes the note away again the moment it has finished
        // arriving. The fade starts where the fade-in ended and runs for three
        // tenths of preempt, so the note is gone three tenths of preempt
        // before it is due — and a slider instead dissolves gradually across
        // its whole length.
        //
        // ```csharp
        // double fadeOutStartTime = hitObject.StartTime - hitObject.TimePreempt + hitObject.TimeFadeIn;
        // double fadeOutDuration = hitObject.TimePreempt * FADE_OUT_DURATION_MULTIPLIER;
        // double longFadeDuration = hitObject.GetEndTime() - fadeOutStartTime;
        // ```
        // A spinner is not in that switch either, and it must not be: Hidden
        // takes away what you would otherwise read ahead, and a spinner has
        // nothing to read ahead — it is a thing you are already doing. Fading it
        // like a note left the whole spinner section as a black screen with a
        // cursor circling in it, which is what a bug looks like rather than what
        // a mod looks like.
        let object = &self.state.timeline().objects[index];
        if self.hidden && hidden != HiddenFade::Untouched && !object.is_spinner() {
            let starts = annotation.spawn_ms + fade_in;
            let duration = if object.is_slider() && hidden == HiddenFade::Own {
                (object.end_ms - starts).max(1.0)
            } else {
                self.state.difficulty().preempt_ms() * HIDDEN_FADE_OUT
            };
            let hiding = 1.0 - (((time_ms - starts) / duration).clamp(0.0, 1.0) as f32);
            return appearing * leaving * hiding;
        }
        appearing * leaving
    }

    /// The opacity of the parts of a slider Hidden does not touch.
    ///
    /// The mod fades the body, the ticks and the head, and nothing else. Its
    /// own source says so of the arrows outright:
    ///
    /// ```csharp
    /// case DrawableSliderRepeat sliderRepeat:
    ///     // only apply to circle piece – reverse arrow is not affected by hidden.
    ///     sliderRepeat.CirclePiece.FadeOut(fadeDuration);
    /// ```
    ///
    /// and the ball and its follow circle appear in the switch not at all. It
    /// has to be that way round to be playable: the body is what the mod takes
    /// away, and the ball is what is left to follow once it has gone.
    fn alpha_through_hidden(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::Untouched)
    }

    /// The opacity of a slider's *head*, which Hidden treats as a circle.
    ///
    /// A slider's body dissolves across its whole length; its head goes on the
    /// ordinary short fade, like any note. lazer says so by handling the two in
    /// separate cases:
    ///
    /// ```csharp
    /// case DrawableSlider slider:
    ///     slider.Body.FadeOut(longFadeDuration, Easing.Out);
    /// ```
    ///
    /// Sharing one opacity between them dimmed the head on the body's schedule,
    /// so on a long slider the note you are about to click was already half
    /// gone — which is the wrong half of the object to take away, and reads as
    /// the head fading strangely rather than as the body dissolving.
    fn head_alpha(&self, index: usize, time_ms: f64) -> f32 {
        self.alpha_at(index, time_ms, HiddenFade::AsANote)
    }

    /// How far through leaving the screen a resolved note is: 0 while it is
    /// still a target, 1 once it has finished going.
    ///
    /// Separate from the alpha because the two are not the same curve on a
    /// slider — the body holds full opacity until the slider ends, while its
    /// head left the moment it was clicked.
    fn exit_progress(&self, from_ms: f64, time_ms: f64, missed: bool) -> f32 {
        let over = if missed { MISS_FADE_MS } else { HIT_FADE_MS };
        (((time_ms - from_ms) / over).clamp(0.0, 1.0)) as f32
    }

    /// How solid the number on a note still is, as it goes.
    ///
    /// Its own clock, four times the circle's: the circle has a quarter of a
    /// second to swell and fade and the digit has sixty milliseconds to be
    /// gone. It used to vanish on the frame the note was judged, which is the
    /// half of "notes leave too fast" that is about the number.
    ///
    /// ```csharp
    /// if (legacyVersion > 1.0m)
    /// {
    ///     // legacy skins of version 2.0 and newer only apply very short fade
    ///     // out to the number piece.
    ///     hitCircleText.FadeOut(legacy_fade_duration / 4);
    ///     hitCircleText.ScaleTo(1f);
    /// }
    /// else
    /// {
    ///     hitCircleText.FadeOut(legacy_fade_duration);
    ///     hitCircleText.ScaleTo(1.4f, legacy_fade_duration, Easing.Out);
    /// }
    /// ```
    ///
    /// An old skin's digit goes with the circle instead, at the circle's pace
    /// and swelling with it — see [`Scene::number_swells`], which is the other
    /// half of the same branch.
    fn number_alpha(&self, from_ms: f64, time_ms: f64) -> f32 {
        let over = if self.number_swells() { HIT_FADE_MS } else { NUMBER_FADE_MS };
        (1.0 - ((time_ms - from_ms) / over).clamp(0.0, 1.0)) as f32
    }

    /// Whether the number grows with the circle it sits on as the note leaves.
    ///
    /// Only on a version 1 skin. Everything newer holds the digit at the size
    /// it always was: it is a label on a target, and once the target has been
    /// taken it is answering a question nobody is asking any more — stretched
    /// to 1.4 while fading it would just smear.
    fn number_swells(&self) -> bool {
        self.skin_version() <= 1.0
    }

    /// The stretch of a slider's path that is drawn right now, as fractions.
    ///
    /// Two things move. Coming in, the body grows from the head — a slider that
    /// appears whole tells the player nothing about which way it goes, and the
    /// growth is the cue. Going out, the body retracts behind the ball, so the
    /// part already played stops competing for attention with the part still to
    /// play.
    ///
    /// A slider with repeats only retracts on its final pass: while there is
    /// still a turn ahead, the whole body is the target.
    ///
    /// # How fast it grows
    ///
    /// A third of the approach, finishing two thirds of it before the note is
    /// due. Taken from danser — `app/beatmap/objects/slider.go`, `initSnake`:
    ///
    /// ```text
    /// slSnInS := slider.StartTime - slider.diff.Preempt
    /// slSnInE := slider.StartTime - slider.diff.Preempt*2/3
    /// ```
    ///
    /// with its shipped defaults, `Snaking{DurationMultiplier: 0,
    /// FadeMultiplier: 0}`. Its two knobs are what the ends of that range mean:
    /// `FadeMultiplier` is documented as "how close to slider's start time
    /// snake in should end", and at 100% the snake finishes exactly at the
    /// start time.
    ///
    /// This number went through both wrong answers before the reference was
    /// read. It grew over the *fade-in* first, which is two thirds of the
    /// approach — half danser's speed, finishing a third of the way early. Then
    /// it grew over the whole approach, which is the far end of danser's own
    /// range and slower still.
    ///
    /// The lesson is the one this engine is otherwise built on and this corner
    /// of it had skipped: the number comes from an implementation, not from an
    /// argument about what a cue is for. Two versions of that argument were
    /// written down convincingly and both were wrong.
    ///
    /// The object unfurls quickly on arrival and is then a stable target for
    /// the rest of its approach, which is also what it looks like.
    fn snake(&self, object: &TimedObject, index: usize, time_ms: f64) -> (f64, f64) {
        // Both halves are settings — see `Effects` — and both are off unless
        // asked for. Each is a cue aimed at somebody who has to *play* the
        // slider: growth says where it goes, in the half second before it must
        // be hit, and retraction says how much is left. A viewer has neither
        // job. Somebody may want one and not the other, so they are two.
        let TimedKind::Slider { slides, .. } = &object.kind else {
            return (0.0, 1.0);
        };
        let annotation = &self.annotations[index];

        if time_ms < object.start_ms {
            if !self.skin.snake_in {
                return (0.0, 1.0);
            }
            // danser's window — `initSnake`, `StartTime - Preempt` to
            // `StartTime - Preempt*2/3` — so the object unfurls quickly on
            // arrival and is a stable target for the rest of its approach.
            let approach = (object.start_ms - annotation.spawn_ms).max(1.0);
            let window = approach * SNAKE_SHARE_OF_APPROACH;
            return (0.0, ((time_ms - annotation.spawn_ms) / window).clamp(0.0, 1.0));
        }
        if !self.skin.snake_out {
            return (0.0, 1.0);
        }

        // Clamped to the last slide so that once the slider is over the body
        // holds its retracted shape through the fade, instead of springing back
        // to full length for the final few frames.
        let slides = (*slides).max(1);
        let span = (object.end_ms - object.start_ms).max(1.0);
        let travelled =
            ((time_ms - object.start_ms) / span * f64::from(slides)).clamp(0.0, f64::from(slides));
        let last = f64::from(slides - 1);
        if travelled < last {
            return (0.0, 1.0);
        }

        let local = (travelled - last).clamp(0.0, 1.0);
        if slides % 2 == 1 {
            (local, 1.0) // the final pass runs forwards, so the start retreats
        } else {
            (0.0, 1.0 - local) // …and backwards, so the far end does
        }
    }

    /// Just the tube of a slider, for the pass that goes under everything.
    ///
    /// Slider bodies are a layer of their own, beneath every hit object on the
    /// field — that is how stable renders them, into a buffer of their own, and
    /// how danser does it after. Drawn in time order with the rest, a slider
    /// starting a moment earlier covers the note you are about to hit, which
    /// is the one thing on screen that must never be covered.
    pub(super) fn draw_object_body(
        &self,
        pixmap: &mut Pixmap,
        index: usize,
        time_ms: f64,
        layout: &Layout,
    ) {
        let object = &self.state.timeline().objects[index];
        if !matches!(object.kind, TimedKind::Slider { .. }) {
            return;
        }
        let annotation = &self.annotations[index];
        let colour = self.skin.combo_colour(annotation.colour);
        let (from, to) = self.snake(object, index, time_ms);
        self.draw_slider_body(
            pixmap,
            object,
            (from, to),
            colour,
            self.alpha_of(index, time_ms),
            layout,
        );
    }

    /// The ring closing in on a note, drawn in a pass of its own above
    /// everything else on the field.
    ///
    /// Its own pass because that is where the game keeps it. `OsuPlayfield`
    /// builds its layers in order and this one is last, filled by proxy:
    ///
    /// ```csharp
    /// borderContainer, Smoke, spinnerProxies, FollowPoints, judgementLayer,
    /// HitObjectContainer, judgementAboveHitObjectLayer, approachCircles
    /// ...
    /// approachCircles.Add(hitCircle.ProxiedLayer.CreateProxy());  // ProxiedLayer => ApproachCircle
    /// ```
    ///
    /// Drawn in its object's own place instead, a ring belonging to a note
    /// later than the slider being played passes under that slider's track and
    /// is darkened by it. It is the one thing on the field whose whole job is
    /// to be read at a glance while everything else is happening, which is
    /// presumably why the game lifts it clear.
    pub(super) fn draw_approach(
        &self,
        pixmap: &mut Pixmap,
        index: usize,
        time_ms: f64,
        layout: &Layout,
    ) {
        let object = &self.state.timeline().objects[index];
        // Only while the note is still coming — and not at all under Hidden,
        // which is the half of the mod a player actually feels.
        // `OsuModHidden` implements `IHidesApproachCircles` and hides them
        // outright.
        if object.is_spinner() || time_ms >= object.start_ms || self.hidden {
            return;
        }
        let alpha = self.alpha_of(index, time_ms);
        if alpha <= 0.0 {
            return;
        }
        let annotation = &self.annotations[index];
        let radius = layout.length(self.state.difficulty().circle_radius());
        let progress = self.state.timeline().approach_progress(object, time_ms);
        let scale = 1.0 + 3.0 * (1.0 - progress.clamp(0.0, 1.0)) as f32;
        // The ring closes in by growing the size it is drawn at, so the skin's
        // picture takes the same treatment as our own circle: one radius,
        // already scaled.
        if self.skin_speaks_for(Element::ApproachCircle) {
            self.draw_sprite(
                pixmap,
                Element::ApproachCircle,
                annotation.colour,
                object.pos,
                radius * scale,
                alpha,
                layout,
            );
        } else {
            self.ring(
                pixmap,
                object.pos,
                radius * scale,
                (radius * 0.09).max(1.0),
                self.skin.combo_colour(annotation.colour),
                alpha,
                layout,
            );
        }
    }

    pub(super) fn draw_object(&self, pixmap: &mut Pixmap, index: usize, time_ms: f64, layout: &Layout) {
        let object = &self.state.timeline().objects[index];
        let annotation = &self.annotations[index];
        let alpha = self.alpha_of(index, time_ms);
        let colour = self.skin.combo_colour(annotation.colour);
        let radius = layout.length(self.state.difficulty().circle_radius());

        match &object.kind {
            TimedKind::Spinner => self.draw_spinner(pixmap, object, time_ms, alpha, layout),
            TimedKind::Slider { .. } => {
                // The body first, under the rest of its own slider and under
                // everything drawn after it.
                self.draw_object_body(pixmap, index, time_ms, layout);
                let (from, to) = self.snake(object, index, time_ms);
                let slide = object.slide_duration_ms().unwrap_or(0.0);
                // The far end of the path, which osu! draws a circle on for as
                // long as the slider is up. Under everything else here: the
                // ball passes over it, and on a repeating slider the arrow
                // sits on it. Only once the body has actually reached it —
                // a circle at the end of a tube that has not grown that far
                // is a note floating in space, the same mistake the ticks and
                // the arrows each had to be taught not to make.
                if to >= 1.0 {
                    if let Some(end) = object.ball_at(object.start_ms + slide) {
                        let at = shaken(end, annotation, time_ms, self.state);
                        self.draw_circle(
                            pixmap, at, radius, colour, alpha, layout, annotation.colour,
                            Face::Tail,
                        );
                    }
                }
                for &tick in &annotation.ticks_ms {
                    // A tick belongs to the body, so it cannot precede it. It
                    // used to be drawn as soon as the note appeared, which put
                    // dots in empty space ahead of a slider that had not grown
                    // that far — and a dot with no line under it does not read
                    // as sitting on the line.
                    let on_body =
                        path_fraction(object, tick).is_some_and(|frac| frac >= from && frac <= to);
                    if tick <= time_ms || !on_body {
                        continue;
                    }
                    let Some(at) = object.ball_at(tick) else {
                        continue;
                    };
                    // Each tick arrives on its own schedule rather than the
                    // whole row appearing at once, so they light up in front
                    // of the ball as it travels.
                    //
                    // ```csharp
                    // if (SpanIndex > 0)
                    //     offset = 200;              // repeats
                    // else
                    //     offset = TimePreempt * 0.66f;
                    // TimePreempt = (StartTime - SpanStartTime) / 2 + offset;
                    // ```
                    //
                    // Half the distance it sits into its own slide, plus two
                    // thirds of the object's preempt on the way out and a flat
                    // two hundred milliseconds on every slide back — the game
                    // gives less warning on a repeat because the player has
                    // already seen where the ticks are.
                    let span = if slide > 0.0 {
                        ((tick - object.start_ms) / slide).floor()
                    } else {
                        0.0
                    };
                    let offset = if span > 0.0 {
                        TICK_REPEAT_LEAD_MS
                    } else {
                        self.state.difficulty().preempt_ms() * TICK_FIRST_LEAD
                    };
                    let live = tick - ((tick - (object.start_ms + span * slide)) / 2.0 + offset);
                    let arriving = (((time_ms - live) / TICK_FADE_MS).clamp(0.0, 1.0)) as f32;
                    if arriving <= 0.0 {
                        continue;
                    }
                    // …and grows into place as it arrives. The game uses an
                    // elastic overshoot over four times the fade; this is the
                    // same movement without the bounce, which at a dot of six
                    // pixels would be a flicker rather than a flourish.
                    let grown = 0.5 + 0.5 * fade((((time_ms - live) / (TICK_FADE_MS * 4.0)).clamp(0.0, 1.0)) as f32);
                    // The skin's own dot, where it has one. `sliderscorepoint`
                    // is what osu! draws here, and a skin that redrew every
                    // other part of a slider and had this borrowed back from us
                    // looked like two sliders overlaid.
                    if self.skin_speaks_for(Element::SliderScorePoint) {
                        self.draw_sprite(
                            pixmap,
                            Element::SliderScorePoint,
                            annotation.colour,
                            at,
                            // Against the note, like every other playfield
                            // sprite: a skin draws this to its own scale and
                            // `draw_sprite` reads that from the picture.
                            radius,
                            alpha * arriving * grown,
                            layout,
                        );
                    } else {
                        self.dot(
                            pixmap,
                            at,
                            radius * 0.14 * grown,
                            lighten(self.skin.circle_border, 0.5),
                            alpha * arriving,
                            layout,
                        );
                    }
                }
                // Hidden fades the body out from under the ball; the ball and
                // its follow circle stay, and so do the arrows.
                let carried = self.alpha_through_hidden(index, time_ms);
                if let Some(ball) = object.ball_at(time_ms) {
                    if self.skin_speaks_for(Element::SliderFollowCircle) {
                        self.draw_sprite(
                            pixmap,
                            Element::SliderFollowCircle,
                            annotation.colour,
                            ball,
                            radius,
                            carried,
                            layout,
                        );
                    } else {
                        self.ring(
                            pixmap,
                            ball,
                            radius * 2.4,
                            radius * 0.06,
                            self.skin.circle_border,
                            carried * 0.5,
                            layout,
                        );
                    }
                    // Two balls, one inside the other. The outer one is the
                    // full-size ball the game draws; the inner one grows to
                    // meet it as the slider runs out, so how far through you
                    // are is readable from the ball itself instead of only
                    // from where it sits on the body.
                    //
                    // The inner one is lifted toward white rather than made
                    // translucent: a paler combo colour still says which combo
                    // this is, where a see-through one would just take on the
                    // body underneath it.
                    let done = ((time_ms - object.start_ms)
                        / (object.end_ms - object.start_ms).max(1.0))
                    .clamp(0.0, 1.0) as f32;
                    if self.skin_speaks_for(Element::SliderBall) {
                        // One picture, and no inner disc: the second ball is
                        // ours for reading progress off, and painting it over
                        // somebody's artwork would be drawing on their skin.
                        let _ = done;
                        self.draw_sprite(
                            pixmap,
                            Element::SliderBall,
                            annotation.colour,
                            ball,
                            radius,
                            carried,
                            layout,
                        );
                    } else {
                        self.dot(pixmap, ball, radius, colour, carried, layout);
                        self.dot(
                            pixmap,
                            ball,
                            radius * (BALL_CORE_SCALE + (1.0 - BALL_CORE_SCALE) * done),
                            lighten(colour, 0.45),
                            carried,
                            layout,
                        );
                    }
                }
                self.draw_reverse_arrow(
                    pixmap,
                    object,
                    annotation,
                    time_ms,
                    radius,
                    carried,
                    (from, to),
                    layout,
                );
                // The head leaves on its own click rather than with the rest of
                // the slider — but it leaves, it does not vanish. Popping out of
                // existence mid-slide was the most artificial thing on screen.
                let exit = self.exit_progress(annotation.head_ms, time_ms, annotation.head_missed);
                if exit < 1.0 {
                    let leaving = self.head_alpha(index, time_ms) * (1.0 - exit);
                    let grown = radius * hit_expansion(exit, annotation.head_missed);
                    let at = shaken(object.pos, annotation, time_ms, self.state);
                    self.draw_circle(
                        pixmap, at, grown, colour, leaving, layout, annotation.colour, Face::Head,
                    );
                    // Four times faster than the circle and at the size it
                    // always was, unless the skin is old enough to want it
                    // going with the circle — see `number_alpha`.
                    let showing = leaving * self.number_alpha(annotation.head_ms, time_ms);
                    if showing > 0.0 {
                        let worn = if self.number_swells() { grown } else { radius };
                        self.draw_number(pixmap, at, worn, annotation.number, showing, layout);
                    }
                }
            }
            TimedKind::Circle => {
                // A hit circle swells as it goes; a missed one only fades. The
                // difference is the whole point — it says which happened without
                // waiting for the combo counter to drop.
                let exit = self.exit_progress(annotation.resolved_ms, time_ms, annotation.missed);
                let grown = radius * hit_expansion(exit, annotation.missed);
                let at = shaken(object.pos, annotation, time_ms, self.state);
                self.draw_circle(
                    pixmap, at, grown, colour, alpha, layout, annotation.colour, Face::Note,
                );
                let showing = alpha * self.number_alpha(annotation.resolved_ms, time_ms);
                if showing > 0.0 {
                    let worn = if self.number_swells() { grown } else { radius };
                    self.draw_number(pixmap, at, worn, annotation.number, showing, layout);
                }
            }
        }

        if annotation.missed && time_ms > annotation.resolved_ms {
            // A miss is worth seeing: the note stops being a target and turns
            // into a mark of what went wrong.
            self.ring(
                pixmap,
                object.pos,
                radius,
                radius * 0.18,
                self.skin.spinner,
                alpha * 0.7,
                layout,
            );
        }
    }

    /// Which pair of pictures a skin draws this circle from, if any.
    ///
    /// The pairing is the rule rather than a convenience: osu!'s wiki says an
    /// overlay requires its own base to function, so a skin shipping
    /// `sliderstartcircleoverlay` and no `sliderstartcircle` gets the note's
    /// pair for both halves rather than one of each.
    fn face_of(&self, face: Face) -> Option<(Element, Element)> {
        let own = match face {
            Face::Note => None,
            Face::Head => Some((Element::SliderHead, Element::SliderHeadOverlay)),
            Face::Tail => Some((Element::SliderTail, Element::SliderTailOverlay)),
        };
        if let Some(pair) = own {
            if self.skin_speaks_for(pair.0) {
                return Some(pair);
            }
        }
        // "Overrides `hitcircle.png` … if skinned" — so an end the skin says
        // nothing about is the note, which is also what a skin with no slider
        // ends at all gets from the game.
        self.skin_speaks_for(Element::HitCircle)
            .then_some((Element::HitCircle, Element::HitCircleOverlay))
    }

    #[allow(clippy::too_many_arguments)]
    fn draw_circle(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
        combo: usize,
        face: Face,
    ) {
        // A skin the player brought has the last word on what a note looks
        // like, including the word "nothing": the disc and its rim are two
        // separate elements, and a skin is free to ship one, both or neither.
        // Whatever it does not speak for falls back to the drawing below.
        if let Some((disc, overlay)) = self.face_of(face) {
            self.draw_sprite(pixmap, disc, combo, centre, radius, alpha, layout);
            if self.skin_speaks_for(overlay) {
                self.draw_sprite(pixmap, overlay, combo, centre, radius, alpha, layout);
            }
            return;
        }
        if face == Face::Tail {
            // Our own look ends a slider on the body's own cap and has been
            // tuned that way. A tail circle is something a skin brings.
            return;
        }

        let border = radius * self.skin.border_ratio;
        // A halo of the note's own colour, thrown onto the field before the
        // note is drawn over it — and it has to *fall off*, or it is not a glow
        // but a second, muddier ring drawn round every note. So it is one disc
        // filled with a gradient that fades to fully transparent by its rim.
        self.glow(pixmap, centre, radius, colour, alpha, layout);
        self.dot(pixmap, centre, radius, darken(colour, 0.25), alpha, layout);
        self.lit_dot(pixmap, centre, radius - border, colour, alpha, layout);
        self.ring(
            pixmap,
            centre,
            radius - border / 2.0,
            border,
            self.skin.circle_border,
            alpha,
            layout,
        );
    }

    /// Which generation of skinning rules to read this skin by.
    ///
    /// ```csharp
    /// skin.GetConfig<SkinConfiguration.LegacySetting, decimal>(...Version)?.Value
    /// ```
    ///
    /// Ours is a skin nobody imported, so it is read by the newest rules — the
    /// same answer osu! gives a folder that ships no `skin.ini` at all. See
    /// [`Ini::version`](crate::imported::Ini::version) for why the two cases
    /// that look alike are not.
    pub(super) fn skin_version(&self) -> f32 {
        self.skin
            .sprites
            .as_ref()
            .map_or(crate::imported::LATEST_SKIN_VERSION, |s| s.ini().version)
    }

    /// Whether the player's skin has an opinion about this element — either a
    /// picture of its own or a deliberate blank.
    ///
    /// The two are one question here on purpose: both mean "not ours to draw",
    /// and `draw_sprite` already draws nothing for the blank case. Splitting
    /// them at every call site would put the same two-line dance in six places.
    pub(super) fn skin_speaks_for(&self, element: Element) -> bool {
        self.skin
            .sprites
            .as_ref()
            .is_some_and(|s| !s.draw_ourselves(element))
    }

    /// One of the skin's own pictures, centred on a playfield point.
    ///
    /// Sized against the note rather than against the frame. Skins are drawn to
    /// a 128-pixel hit circle whatever else they contain, so that is the ruler:
    /// an element twice that wide in its own file is drawn twice as wide as the
    /// note. It is why the skin this was written against works at all — its
    /// `hitcircleoverlay` is 320 against a 128 circle, and reading either file's
    /// size as "the size of a note" would put one of them badly wrong.
    pub(super) fn draw_sprite(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        combo: usize,
        centre: Point,
        radius: f32,
        alpha: f32,
        layout: &Layout,
    ) {
        self.draw_sprite_turned(pixmap, element, combo, centre, radius, alpha, layout, 0.0);
    }

    /// A sprite at the size the skin drew it, in the space stable states its
    /// interface in.
    ///
    /// osu! lays the interface out in a frame 768 units tall and scales that to
    /// the screen, so a 55-pixel cursor is 55 of those units — about 51 screen
    /// pixels at 720p. Elements that are not part of the playfield are sized
    /// this way and not against a note: a cursor does not shrink when the
    /// circles do, and a health bar is as long as its picture.
    pub(super) fn skin_pixels(&self, layout: &Layout, own: f32) -> f32 {
        own * layout.height as f32 / 768.0
    }

    /// A sprite drawn to a width in screen pixels, rather than to a note.
    ///
    /// For everything on the playfield the note is the ruler, because that is
    /// what osu! proportions a skin against. The cursor is the exception and
    /// has to be: it is not part of the playfield and does not grow when the
    /// circles shrink. Sized by the note's ruler it came out a four-pixel dot
    /// on a small-circle map — drawn, and invisible.
    pub(super) fn draw_sprite_wide(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        centre: Point,
        width: f32,
        alpha: f32,
        layout: &Layout,
    ) {
        self.draw_wide(pixmap, element, centre, width, alpha, layout, 0.0);
    }

    /// The same, turned by `degrees` about its own centre.
    #[allow(clippy::too_many_arguments)]
    fn draw_wide(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        centre: Point,
        width: f32,
        alpha: f32,
        layout: &Layout,
        degrees: f32,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, per_osu_pixel)) = sprites.coloured(element, 0) else {
            return;
        };
        if alpha <= 0.0 || width <= 0.0 {
            return;
        }
        let own = (art.width() as f32 / per_osu_pixel).max(1.0);
        let scale = width / (own * per_osu_pixel);
        let (x, y) = layout.map(centre);
        let transform = Transform::from_translate(x, y)
            .pre_rotate(degrees)
            .pre_scale(scale, scale)
            .pre_translate(-(art.width() as f32) / 2.0, -(art.height() as f32) / 2.0);
        pixmap.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            transform,
            None,
        );
    }

    /// The same, turned by `degrees` about its own centre.
    ///
    /// Only the reverse arrow needs this: it is the one element in a skin that
    /// is drawn pointing somewhere rather than simply placed. A skin draws it
    /// pointing right, and the slider says where right is.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_sprite_turned(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        combo: usize,
        centre: Point,
        radius: f32,
        alpha: f32,
        layout: &Layout,
        degrees: f32,
    ) {
        self.draw_sprite_blended(
            pixmap, element, combo, centre, radius, alpha, layout, degrees,
            tiny_skia::BlendMode::SourceOver,
        );
    }

    /// The same, laid down some other way than over what is there.
    ///
    /// Only the hit flash wants this — the wiki gives `lighting.png` a blend
    /// mode of "Additive", which is the whole character of it: light thrown
    /// back off the field, not a sticker placed on it.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_sprite_blended(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        combo: usize,
        centre: Point,
        radius: f32,
        alpha: f32,
        layout: &Layout,
        degrees: f32,
        blend_mode: tiny_skia::BlendMode,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, per_osu_pixel)) = sprites.coloured(element, combo) else {
            return;
        };
        if alpha <= 0.0 {
            return;
        }
        // How many screen pixels one of the skin's own pixels covers: the note
        // is `2 * radius` across and stands for 128 of the skin's, and an `@2x`
        // file holds two file pixels per skin pixel.
        let scale = (radius * 2.0) / (SKIN_CIRCLE_PIXELS * per_osu_pixel);
        let (x, y) = layout.map(centre);
        // Built outwards from where it lands: move to the point, turn about
        // it, scale, then step back by half the picture so the middle of the
        // sprite is what sits on the point.
        let transform = Transform::from_translate(x, y)
            .pre_rotate(degrees)
            .pre_scale(scale, scale)
            .pre_translate(-(art.width() as f32) / 2.0, -(art.height() as f32) / 2.0);
        pixmap.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Bilinear,
                blend_mode,
                ..Default::default()
            },
            transform,
            None,
        );
    }

    /// The combo number out of the skin's own `default-N` pictures.
    ///
    /// False when the skin cannot supply it, and the lettering takes over.
    ///
    /// Worth more care than a decoration deserves, because for some skins this
    /// *is* the note. A skin can blank its hit circle and draw the whole object
    /// inside the digits, and the reason that works is timing: the number is
    /// taken away the instant a note is judged while the circle goes on
    /// swelling, so a note drawn as a number vanishes on the click. That is
    /// what "instafade" skins are, and the one this was written against is one
    /// — its `hitcircle` and `hitcircleoverlay` are both blank and each of its
    /// ten digits carries a complete ring.
    ///
    /// All ten or none. A skin missing `default-7` would otherwise draw every
    /// combo but the sevens, which reads as the renderer dropping notes.
    fn draw_number_from_skin(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        number: u32,
        alpha: f32,
        layout: &Layout,
    ) -> bool {
        let Some(sprites) = &self.skin.sprites else {
            return false;
        };
        let digits: Vec<u8> = number
            .to_string()
            .bytes()
            .map(|byte| byte - b'0')
            .collect();
        if digits.iter().any(|&d| sprites.silenced(Element::Digit(d))) {
            // Blanked on purpose: the skin wants no number, and drawing our own
            // lettering instead would put back what it deleted.
            return true;
        }
        let mut art = Vec::with_capacity(digits.len());
        for &digit in &digits {
            let Some(found) = sprites.coloured(Element::Digit(digit), 0) else {
                return false;
            };
            art.push(found);
        }

        // Laid out in the skin's own pixels and scaled once at the end, which
        // is the only way the overlap means what the skin says it means.
        let overlap = sprites.ini().hit_circle_overlap;
        // Capped, as the game caps it: a skin shipping figures larger than this
        // is drawing them at this size anyway.
        let widths: Vec<f32> = art
            .iter()
            .map(|(pixmap, per)| (pixmap.width() as f32 / per).min(DIGIT_MAX_PIXELS))
            .collect();
        let total: f32 = widths.iter().sum::<f32>() - overlap * (digits.len() as f32 - 1.0);

        let scale = (radius * 2.0) / SKIN_CIRCLE_PIXELS * DIGIT_SCALE;
        let (cx, cy) = layout.map(centre);
        let mut pen = cx - total * scale / 2.0;
        for ((pixmap_of, per), width) in art.into_iter().zip(widths) {
            let each = scale / per;
            let height = pixmap_of.height() as f32 * each;
            let transform = Transform::from_translate(pen, cy - height / 2.0).pre_scale(each, each);
            pixmap.draw_pixmap(
                0,
                0,
                pixmap_of.as_ref(),
                &PixmapPaint {
                    opacity: alpha.clamp(0.0, 1.0),
                    quality: tiny_skia::FilterQuality::Bilinear,
                    ..Default::default()
                },
                transform,
                None,
            );
            pen += (width - overlap) * scale;
        }
        true
    }

    /// The combo number, centred on a note.
    ///
    /// Centred on the *ink*, not on the baseline: digits sit above the baseline
    /// by their own height, and hanging them off it would leave every number
    /// riding high in its circle.
    fn draw_number(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        number: u32,
        alpha: f32,
        layout: &Layout,
    ) {
        if self.draw_number_from_skin(pixmap, centre, radius, number, alpha, layout) {
            return;
        }
        let Some(font) = &self.skin.font else {
            return;
        };
        let size = radius * 0.9;
        let (x, y) = layout.map(centre);
        font.draw(
            pixmap,
            Label {
                text: &number.to_string(),
                x,
                y: y + font.digit_height(size) / 2.0,
                size,
                colour: with_alpha(self.skin.circle_border, alpha),
                align: Align::Centre,
            },
        );
    }

    /// The slider track: a wide white stroke with a darker one inside it.
    ///
    /// The outline is in playfield coordinates and the transform does the
    /// scaling, so the stroke width is stated in osu!pixels and comes out right
    /// at any output size.
    /// The arrow telling the player they'll be coming back.
    ///
    /// Only one shows at a time, at the end the ball is heading for, and only
    /// while a turn is still to come. Without it a repeating slider is drawn
    /// exactly like one that ends where it stops — the map is being
    /// misrepresented, not merely under-decorated.
    #[allow(clippy::too_many_arguments)]
    fn draw_reverse_arrow(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        annotation: &Annotation,
        time_ms: f64,
        radius: f32,
        alpha: f32,
        (from, to): (f64, f64),
        layout: &Layout,
    ) {
        let (
            Some((head, tail)),
            TimedKind::Slider {
                slides,
                slide_duration_ms,
                ..
            },
        ) = (annotation.turns, &object.kind)
        else {
            return;
        };

        if *slide_duration_ms <= 0.0 {
            return;
        }

        // Turns happen at the slide boundaries: the first is at the tail, the
        // next at the head, alternating. Both ends carry an arrow while both
        // still have a turn coming — showing only the nearest one made the
        // far end's arrow vanish the moment the near one appeared, which reads
        // as the slider changing its mind about where it goes.
        for (at_tail, turn) in [(true, tail), (false, head)] {
            // Each turn with the moment it becomes the next one at this end:
            // the start of the slide that ends on it.
            let turns = (1..*slides)
                .filter(|k| k.is_multiple_of(2) != at_tail)
                .map(|k| {
                    (
                        object.start_ms + f64::from(k) * slide_duration_ms,
                        object.start_ms + f64::from(k - 1) * slide_duration_ms,
                    )
                });

            let turns: Vec<(f64, f64)> = turns.collect();
            // Read from when the ball sets off, not from now, so the first
            // turn's arrow is up while the slider is still approaching: a
            // player has to know a slider comes back before they start it.
            let (leaving, pulse) =
                arrow_life(&turns, time_ms, time_ms.max(object.start_ms), object.start_ms,
                           *slide_duration_ms);
            // An arrow cannot sit on a part of the body that has not grown
            // yet, for the same reason a tick cannot — and it arrives with the
            // body rather than appearing whole on top of it.
            let arriving = if at_tail {
                ((to - (1.0 - ARROW_REACH)) / ARROW_REACH).clamp(0.0, 1.0) as f32
            } else {
                ((ARROW_REACH - from) / ARROW_REACH).clamp(0.0, 1.0) as f32
            };

            let showing = alpha * leaving * arriving;
            if showing <= 0.0 {
                continue;
            }
            // Two movements on one mark, and they do different jobs: `pulse` is
            // the kick when the ball actually strikes this turn, and the beat is
            // the arrow breathing on the map's clock while it waits to be
            // struck. Added rather than blended — the kick should still read as
            // a kick when it lands on a beat.
            if self.skin_speaks_for(Element::ReverseArrow) {
                // The skin draws it pointing right; the turn says which way
                // right is on this slider.
                let mut degrees = turn.dir.1.atan2(turn.dir.0).to_degrees() as f32;
                // An old skin's arrows rock while they wait — see `arrow_rock`.
                // Added to the direction rather than replacing it: the rock is
                // about the arrow's own centre and the direction is where the
                // slider goes next.
                if self.skin_version() <= 1.0 {
                    degrees += arrow_rock(time_ms, object.start_ms);
                }
                self.draw_sprite_turned(
                    pixmap,
                    Element::ReverseArrow,
                    annotation.colour,
                    turn.at,
                    radius * pulse,
                    showing,
                    layout,
                    degrees,
                );
                continue;
            }
            self.draw_chevron(
                pixmap,
                turn,
                radius * ARROW_SCALE * pulse,
                showing,
                self.skin.arrow,
                layout,
            );
        }
    }

    /// A filled triangle pointing along `turn.dir`.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_chevron(
        &self,
        pixmap: &mut Pixmap,
        turn: Turn,
        size: f32,
        alpha: f32,
        shape: ArrowShape,
        layout: &Layout,
    ) {
        let (x, y) = layout.map(turn.at);
        crate::elements::chevron(
            pixmap,
            x,
            y,
            turn.dir,
            size,
            self.skin.circle_border,
            alpha,
            shape,
            ARROW_ROUNDING,
        );
    }

    fn draw_slider_body(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        snake: (f64, f64),
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(path) = body_path(object, snake) else {
            return;
        };
        // Two widths, and they are not interchangeable. The stroke is applied
        // through the layout's transform, so its width must be stated in
        // playfield units — given screen pixels it comes out scaled twice, and
        // the body was drawn as many times too wide as the field is stretched.
        let radius = self.state.difficulty().circle_radius() as f32;
        let half = layout.length(self.state.difficulty().circle_radius());
        if half < 0.5 || alpha <= 0.0 {
            return;
        }

        // Where the tube lands on screen, with room for its own width and a
        // pixel of anti-aliasing. Drawn into a buffer this size rather than a
        // frame-sized one: a slider covers a fraction of the frame and this
        // runs once per slider per frame.
        let bounds = path.bounds();
        let (x0, y0) = layout.map(Point {
            x: f64::from(bounds.left()),
            y: f64::from(bounds.top()),
        });
        let (x1, y1) = layout.map(Point {
            x: f64::from(bounds.right()),
            y: f64::from(bounds.bottom()),
        });
        let margin = half * 2.0 + 4.0;
        let (left, top) = (x0.min(x1) - margin, y0.min(y1) - margin);
        let width = ((x1 - x0).abs() + margin * 2.0).ceil() as u32;
        let height = ((y1 - y0).abs() + margin * 2.0).ceil() as u32;
        let Some(mut tube) = Pixmap::new(width.max(1), height.max(1)) else {
            return;
        };
        let into_tube = Transform::from_translate(-left, -top).pre_concat(layout.transform());

        // One band per screen pixel of half-width, drawn from the centreline
        // outwards. `DestinationOver` puts each behind what is already there,
        // so a band paints only the ring the narrower ones left uncovered —
        // which is what makes this a gradient rather than a stack. Overlapping
        // strokes drawn the usual way accumulate opacity, and that is what the
        // rings in the old version were.
        // One band per two screen pixels of half-width. At one per pixel the
        // gradient is no smoother to look at — it is shallow, and neighbouring
        // bands differ by a fraction of a level — but it costs twice the
        // strokes, and strokes are what a render spends its time on: measured
        // at 18.9ms of drawing per frame against 10.4 at this rate.
        let steps = ((half / 2.0).ceil() as usize).clamp(8, 48);
        let body = self.skin.slider_body.unwrap_or(colour);
        let (body_outer, body_inner) = (
            crate::skin::body_outer(body),
            crate::skin::body_inner(body),
        );
        // Widest band first, narrowest last, each one *replacing* what it
        // covers. Drawn the other way round with `DestinationOver` — "paint
        // behind what is already there" — the colours came out right and the
        // opacity did not: that blend is `dst + src·(1 - dst.a)`, not "only
        // where nothing is", so every one of the wider bands still added three
        // tenths of itself on top. Nested three or four deep the tube reached
        // full opacity, and a body at full opacity cannot dim what it passes
        // over — it covers it, which is exactly what was reported three times.
        //
        // `Source` keeps each pixel at the alpha of the narrowest band over it,
        // which is what the shading function says it should be: a quarter for
        // the shadow, opaque for the border, seven tenths for the track. Its
        // anti-aliased edges still mix with the band beneath, so nesting them
        // stays smooth.
        for step in (0..=steps).rev() {
            // 1 at the centreline, 0 at the outer edge — danser's own
            // `distance_inv`, which every threshold below is stated in.
            let towards = 1.0 - step as f32 / steps as f32;
            let shade = tube_shade(
                towards,
                self.skin.slider_border,
                body_outer,
                body_inner,
                self.skin.slider_body_alpha,
            );
            let paint = Paint {
                shader: Shader::SolidColor(shade),
                anti_alias: true,
                blend_mode: tiny_skia::BlendMode::Source,
                ..Default::default()
            };
            let stroke = Stroke {
                width: (radius * 2.0 * (1.0 - towards)).max(0.01),
                line_cap: LineCap::Round,
                line_join: LineJoin::Round,
                ..Default::default()
            };
            tube.stroke_path(&path, &paint, &stroke, into_tube, None);
        }

        pixmap.draw_pixmap(
            left.floor() as i32,
            top.floor() as i32,
            tube.as_ref(),
            &PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Nearest,
                ..Default::default()
            },
            Transform::identity(),
            None,
        );
    }

    fn draw_spinner(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        time_ms: f64,
        alpha: f32,
        layout: &Layout,
    ) {
        // The ring closes in as the spinner runs, which is how the player sees
        // time left rather than progress made. It closes onto the centre dot
        // rather than onto empty space: a ring shrinking towards nothing says
        // only that it is shrinking, while one arriving at a mark says how far
        // it still has to go.
        let progress =
            ((time_ms - object.start_ms) / object.duration_ms().max(1.0)).clamp(0.0, 1.0);
        let closing = SPINNER_RADIUS + (SPINNER_DOT - SPINNER_RADIUS) * progress;
        // The skin's own ring where it has one — and *nothing* where it has
        // deliberately blanked it, which is what the skin read against here
        // does to all but one of its spinner's parts. Ours was drawn over the
        // top of that regardless, which is the same mistake the verdicts had:
        // an element the skin turned off is not an element it left to us.
        if self.skin_speaks_for(Element::SpinnerApproachCircle) {
            self.draw_sprite_wide(
                pixmap,
                Element::SpinnerApproachCircle,
                Point::CENTRE,
                layout.length(closing) * 2.0,
                alpha,
                layout,
            );
        } else {
            self.ring(
                pixmap,
                Point::CENTRE,
                layout.length(closing),
                layout.length(4.0),
                self.skin.spinner,
                alpha,
                layout,
            );
        }

        // The mark at the middle, from the skin when it has one. Which file
        // that is depends on the style the skin is drawn in — see
        // `spinner_middle` — and a skin that ships neither leaves it to the
        // rings below.
        // The skin's own layers, each answering for itself. They used to hang
        // together behind "does it have a middle", which meant a skin with a
        // backdrop and a gauge and no `spinner-circle` — an ordinary shape for
        // one to be — drew none of the three.
        let old_style = self.spinner_is_old_style();
        if old_style {
            self.draw_spinner_layer(pixmap, Element::SpinnerBackground, alpha, layout);
            self.draw_spinner_metre(pixmap, object, time_ms, alpha, layout);
        } else {
            for layer in [Element::SpinnerBottom, Element::SpinnerGlow] {
                self.draw_spinner_layer(pixmap, layer, alpha, layout);
            }
        }

        let middle = self.spinner_middle();
        if let Some(middle) = middle {
            self.draw_spinner_layer(pixmap, middle, alpha, layout);
        }
        if !old_style {
            // `spinner-middle2` is the half of the middle that *turns*. A skin
            // drawing a needle or a mark puts it here, and placed without its
            // rotation it says the opposite of what it is for — a spinner that
            // reports nothing while being spun.
            self.draw_spinner_layer_turned(
                pixmap,
                Element::SpinnerMiddle2,
                alpha,
                layout,
                self.spun_degrees(object, time_ms),
            );
            self.draw_spinner_layer(pixmap, Element::SpinnerTop, alpha, layout);
        }
        if middle.is_some() {
            self.draw_spin_bonus(pixmap, object, time_ms, alpha, layout);
            return;
        }

        // The mark at the middle: a ring with a lit core inside it, drawn after
        // the closing ring so nothing crosses it at the end.
        let band = SPINNER_DOT - SPINNER_CORE;
        self.ring(
            pixmap,
            Point::CENTRE,
            layout.length(SPINNER_DOT - band / 2.0),
            layout.length(band),
            self.skin.spinner,
            alpha,
            layout,
        );
        self.dot(
            pixmap,
            Point::CENTRE,
            layout.length(SPINNER_CORE),
            lighten(self.skin.spinner, 0.55),
            alpha,
            layout,
        );

        self.draw_spin_bonus(pixmap, object, time_ms, alpha, layout);
    }

    /// How wide a skin drew this element, on screen.
    fn own_width(&self, layout: &Layout, element: Element) -> f32 {
        self.skin
            .sprites
            .as_ref()
            .and_then(|s| s.get(element))
            .map_or(0.0, |sprite| self.skin_pixels(layout, sprite.width()))
    }

    /// The old style's gauge, revealed from the bottom as the spinner fills.
    ///
    /// Cut rather than scaled, the same way a health bar is: squashing it would
    /// turn a gauge into a picture that changes shape, and what it is meant to
    /// say is *how far up it has got*. Its reading is rotations against the
    /// rotations the difficulty asks for — the same figure the judge scores it
    /// by, rather than time elapsed, which would fill even while nobody spun.
    fn draw_spinner_metre(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        time_ms: f64,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, per)) = sprites.coloured(Element::SpinnerMetre, 0) else {
            return;
        };
        let required = dossier_sim::required_spins(self.state.difficulty(), object.duration_ms());
        if required <= 0.0 || alpha <= 0.0 {
            return;
        }
        let turned = dossier_sim::spinner_rotations(
            self.state.cursor_track(),
            object.start_ms,
            time_ms.min(object.end_ms),
        );
        let filled = ((turned / required) as f32).clamp(0.0, 1.0);
        if filled <= 0.0 {
            return;
        }

        let scale = layout.height as f32 / 768.0 / per;
        let (w, h) = (art.width() as f32 * scale, art.height() as f32 * scale);
        let shown = (h * filled).ceil().max(1.0) as u32;
        let Some(mut strip) = Pixmap::new(w.ceil().max(1.0) as u32, shown) else {
            return;
        };
        // Drawn shifted up by the part that is still hidden, so what lands in
        // the strip is the bottom of the picture — a gauge fills upwards.
        strip.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            Transform::from_translate(0.0, -(h - shown as f32)).pre_scale(scale, scale),
            None,
        );
        let (cx, cy) = layout.map(Point::CENTRE);
        pixmap.draw_pixmap(
            (cx - w / 2.0) as i32,
            (cy + h / 2.0 - shown as f32) as i32,
            strip.as_ref(),
            &PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                ..Default::default()
            },
            Transform::identity(),
            None,
        );
    }

    /// Whether a skin is drawn in osu!'s old spinner style.
    ///
    /// The two are not mixable, and a skin exported from lazer carries both
    /// sets of files — so asking which exist answers the wrong question. What
    /// decides it is `spinner-background`: a skin that mentions it at all, even
    /// to blank it, is the old kind.
    fn spinner_is_old_style(&self) -> bool {
        self.skin
            .sprites
            .as_ref()
            .is_some_and(|s| !s.draw_ourselves(Element::SpinnerBackground))
    }

    /// Which of a skin's two spinner middles is its own, if either.
    fn spinner_middle(&self) -> Option<Element> {
        let sprites = self.skin.sprites.as_ref()?;
        let wanted = if self.spinner_is_old_style() {
            Element::SpinnerCircle
        } else {
            Element::SpinnerMiddle
        };
        (!sprites.draw_ourselves(wanted)).then_some(wanted)
    }

    /// One spinner layer at the size its picture was drawn, or nothing when the
    /// skin has no such file — or blanked the one it had.
    fn draw_spinner_layer(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        alpha: f32,
        layout: &Layout,
    ) {
        self.draw_spinner_layer_turned(pixmap, element, alpha, layout, 0.0);
    }

    /// The same, turned about the middle of the field.
    fn draw_spinner_layer_turned(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        alpha: f32,
        layout: &Layout,
        degrees: f32,
    ) {
        let own = self.own_width(layout, element);
        if own > 0.0 {
            self.draw_wide(pixmap, element, Point::CENTRE, own, alpha, layout, degrees);
        }
    }

    /// How far this spinner has been turned by now, in degrees.
    ///
    /// Rotations rather than time, which is the same figure the gauge fills by
    /// and the same one the judge scores a spinner on: a middle that turned on
    /// the clock would keep spinning while the cursor sat still.
    fn spun_degrees(&self, object: &TimedObject, time_ms: f64) -> f32 {
        let turned = dossier_sim::spinner_rotations(
            self.state.cursor_track(),
            object.start_ms,
            time_ms.min(object.end_ms),
        );
        (turned * 360.0) as f32
    }

    /// The bonus so far, below the centre, and what it does when it grows.
    ///
    /// Each award arrives lit and oversized, then settles: it shrinks inward to
    /// its resting size and fades to grey, and stays there holding the running
    /// total until the next one lands and lights it again. So the number itself
    /// is the history — a spinner that keeps paying keeps flashing white, one
    /// that has stopped sits grey at whatever it reached.
    ///
    /// The step is a thousand, not the eleven hundred the score gets. osu!
    /// displays and pays different numbers here — `hitSpinner.Bonus(1000)`
    /// beside a `SpinnerBonus` worth 1100 — and copying the score's figure onto
    /// the screen would be a plausible, wrong number.
    fn draw_spin_bonus(
        &self,
        pixmap: &mut Pixmap,
        object: &TimedObject,
        time_ms: f64,
        alpha: f32,
        layout: &Layout,
    ) {
        let Some(font) = self.skin.font.as_ref() else {
            return;
        };
        let Some(judge) = self.state.judge() else {
            return;
        };
        // Every bonus this spinner has paid by now, and when the last one came.
        let mut awarded = 0u32;
        let mut latest = f64::NEG_INFINITY;
        for event in judge.events() {
            if event.part != dossier_sim::Part::SpinnerBonus || event.time_ms > time_ms {
                continue;
            }
            if event.time_ms < object.start_ms || event.time_ms > object.end_ms {
                continue;
            }
            awarded += 1;
            latest = latest.max(event.time_ms);
        }
        if awarded == 0 {
            return;
        }

        let age = time_ms - latest;
        // One pulse per award: lit and large at the moment it lands, settling to
        // grey and smaller over a fifth of a second. Cubed on the way out so
        // the flash is a flash rather than a slow dim.
        let flash = (1.0 - (age / SPINNER_BONUS_PULSE_MS).clamp(0.0, 1.0)) as f32;
        let eased = flash * flash * flash;
        let size = layout.length(SPINNER_BONUS_SIZE) * (1.0 + SPINNER_BONUS_SWELL * eased);
        // Lifted toward white rather than swapped for it, so the resting state
        // is the spinner's own colour dimmed rather than a second palette.
        let colour = lighten(darken(self.skin.spinner, SPINNER_BONUS_REST), eased);
        let at = layout.map(Point {
            x: Point::CENTRE.x,
            y: Point::CENTRE.y + SPINNER_BONUS_BELOW,
        });
        font.draw(
            pixmap,
            Label {
                text: &format!("{}", awarded * SPINNER_BONUS_STEP),
                x: at.0,
                y: at.1 + size * 0.35,
                size,
                colour: with_alpha(colour, alpha),
                align: Align::Centre,
            },
        );
    }

    /// The marks the cursor leaves behind it.
    ///
    /// osu! has two trails and picks between them on a file the skin does *not*
    /// have:
    ///
    /// ```csharp
    /// DisjointTrail = cursorProvider?.GetTexture("cursormiddle") == null;
    /// …
    /// protected override double FadeDuration => DisjointTrail ? 150 : 500;
    /// protected override bool InterpolateMovements => !DisjointTrail;
    /// protected override bool AvoidDrawingNearCursor => !DisjointTrail;
    /// ```
    ///
    /// Without a middle it is a row of separate dots, one dropped every
    /// sixtieth of a second wherever the cursor is, each gone in 150ms. With
    /// one it is a ribbon: marks laid along the path by *distance* rather than
    /// by time, added together rather than drawn over one another, lasting half
    /// a second and leaving a gap by the cursor so the ribbon appears to come
    /// out from under it.
    ///
    /// What this used to do was neither. Fourteen marks over 110ms, each shrunk
    /// as it aged and none above a third opacity, which is a smear where the
    /// game draws a trail.
    fn draw_trail(&self, pixmap: &mut Pixmap, time_ms: f64, radius: f32, layout: &Layout) {
        let track = self.state.cursor_track();
        // A skin with a `cursormiddle` gets the ribbon. Blank counts as having
        // one, the way it does everywhere: the game asks whether the texture is
        // there, and a blank file is a texture.
        let disjoint = !self.skin_speaks_for(Element::CursorMiddle);

        // Blanking `cursortrail` is how a skin turns the trail off, and several
        // do — a trail is the first thing a player removes to see the field.
        // `draw_sprite_wide` draws nothing for a blank, so the same branch
        // covers both having a picture and having deleted one.
        let skinned = self.skin_speaks_for(Element::CursorTrail);
        let own = self
            .skin
            .sprites
            .as_ref()
            .and_then(|s| s.get(Element::CursorTrail))
            .map_or(radius * 2.0, |sprite| {
                self.skin_pixels(layout, sprite.width() / TRAIL_STABLE_SCALE)
            });

        let mut mark = |at: dossier_beatmap::Point, alpha: f32| {
            if alpha <= 0.0 {
                return;
            }
            if skinned {
                self.draw_sprite_wide(pixmap, Element::CursorTrail, at, own, alpha, layout);
            } else {
                self.dot(pixmap, at, radius * 0.8, self.skin.trail_colour, alpha, layout);
            }
        };

        if disjoint {
            let mut age = TRAIL_STEP_MS;
            while age <= TRAIL_DISJOINT_MS {
                if let Some(sample) = track.sample(time_ms - age) {
                    // `FadeExponent = 1`: straight down, not eased.
                    mark(sample.pos, 1.0 - (age / TRAIL_DISJOINT_MS) as f32);
                }
                age += TRAIL_STEP_MS;
            }
            return;
        }

        // The ribbon. Walked backwards along the path, dropping a mark every
        // `interval` of travel rather than every so many milliseconds, so a
        // fast sweep is a continuous line and a still cursor lays down nothing
        // new. The first interval is skipped — that is `AvoidDrawingNearCursor`
        // — which is what makes the ribbon appear from under the cursor rather
        // than through it.
        let interval = (own * TRAIL_INTERVAL_SHARE / layout.length(1.0).max(0.001)) as f64;
        if interval <= 0.0 {
            return;
        }
        let Some(head) = track.sample(time_ms) else {
            return;
        };
        let (mut last, mut walked) = (head.pos, 0.0f64);
        let mut age = 0.0f64;
        while age < TRAIL_CONTINUOUS_MS {
            age += TRAIL_STEP_MS / 4.0;
            let Some(sample) = track.sample(time_ms - age) else {
                break;
            };
            let step = f64::from((sample.pos.x - last.x).hypot(sample.pos.y - last.y));
            last = sample.pos;
            walked += step;
            if walked < interval {
                continue;
            }
            walked = 0.0;
            mark(sample.pos, 1.0 - (age / TRAIL_CONTINUOUS_MS) as f32);
        }
    }

    pub(super) fn draw_cursor(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let track = self.state.cursor_track();
        let radius = layout.length(9.0);

        // The trail is a setting of its own — somebody may want the cursor and
        // not the smear behind it.
        if self.skin.cursor_trail {
            self.draw_trail(pixmap, time_ms, radius, layout);
        }

        if let Some(sample) = track.sample(time_ms) {
            // > Should the cursor expand when clicked?  Default `1`.
            //
            // A skin is entitled to say no, and both of the ones this was
            // written against do. It used to be ignored with a comment saying
            // so; it is read now.
            // Both have to allow it: the setting is the viewer's and
            // `CursorExpand: 0` is the skin's, and a skin that refuses still
            // refuses when the setting is on.
            let expands = self.skin.cursor_expand
                && self
                    .skin
                    .sprites
                    .as_ref()
                    .is_none_or(|sprites| sprites.ini().cursor_expand);
            let held = expands && sample.keys.is_pressed();
            if self.skin_speaks_for(Element::Cursor) {
                // The skin's cursor swells under a click the way ours does.
                // osu! has a `CursorExpand` flag for exactly this and defaults
                // it on; the skin read here turns it off, which is a setting
                // this renderer does not carry yet — noted rather than guessed
                // at, because inventing the answer would be worse than being
                // consistent with our own cursor.
                // At the size the skin drew it, not at ours. Sized against the
                // note it came out a third too small — a 55-pixel cursor is 55
                // units of a 768-tall interface, whatever the circles are doing.
                let own = self
                    .skin
                    .sprites
                    .as_ref()
                    .and_then(|s| s.get(Element::Cursor))
                    .map_or(radius * 2.0, |sprite| {
                        self.skin_pixels(layout, sprite.width())
                    });
                let wide = own * if held { 1.25 } else { 1.0 };
                self.draw_sprite_wide(pixmap, Element::Cursor, sample.pos, wide, 1.0, layout);
                if self.skin_speaks_for(Element::CursorMiddle) {
                    // Drawn over the top and never expanded — that part is the
                    // game's own behaviour rather than a choice.
                    let middle = self
                        .skin
                        .sprites
                        .as_ref()
                        .and_then(|s| s.get(Element::CursorMiddle))
                        .map_or(radius * 2.0, |sprite| {
                            self.skin_pixels(layout, sprite.width())
                        });
                    self.draw_sprite_wide(
                        pixmap,
                        Element::CursorMiddle,
                        sample.pos,
                        middle,
                        1.0,
                        layout,
                    );
                }
                return;
            }
            self.dot(
                pixmap,
                sample.pos,
                radius * 1.25,
                self.skin.trail_colour,
                0.5,
                layout,
            );
            self.dot(
                pixmap,
                sample.pos,
                radius * if held { 0.95 } else { 0.75 },
                self.skin.cursor,
                1.0,
                layout,
            );
        }
    }

    fn dot(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let (x, y) = layout.map(centre);
        crate::elements::dot(pixmap, x, y, radius, colour, alpha);
    }

    /// The soft halo a note sits in, falling off to nothing past its rim.
    ///
    /// One disc reaching `note_glow` past the note, filled with a gradient that
    /// holds a low opacity out to the note's own edge and then fades to fully
    /// transparent. The falloff is the whole point: a halo of flat colour is
    /// just a wider, muddier note, which is what the first attempt drew.
    fn glow(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let (x, y) = layout.map(centre);
        crate::elements::glow(pixmap, x, y, radius, colour, alpha, self.skin.note_glow);
    }

    /// A disc with the light coming from a little above its centre.
    ///
    /// The same circle `dot` draws, filled with a radial gradient instead of a
    /// flat colour: lifted towards white at the centre, the plain colour by the
    /// rim. It is what turns a sticker into an object, and it is the whole of
    /// the skin's "depth" — no blur, no second pass, one shader on a fill that
    /// was happening anyway.
    ///
    /// With `note_relief` at zero this is `dot`, so the flat skins pay nothing
    /// and draw exactly what they drew before.
    fn lit_dot(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let (x, y) = layout.map(centre);
        crate::elements::lit_dot(pixmap, x, y, radius, colour, alpha, self.skin.note_relief);
    }

    #[allow(clippy::too_many_arguments)]
    fn ring(
        &self,
        pixmap: &mut Pixmap,
        centre: Point,
        radius: f32,
        width: f32,
        colour: tiny_skia::Color,
        alpha: f32,
        layout: &Layout,
    ) {
        let (x, y) = layout.map(centre);
        crate::elements::ring(pixmap, x, y, radius, width, colour, alpha);
    }
}

/// A slider's centre line as a path in playfield coordinates.
/// The note's drawn position, shaken if it has just refused a click.
fn shaken(pos: Point, annotation: &Annotation, time_ms: f64, state: &GameState) -> Point {
    let radius = state.difficulty().circle_radius();
    let dx = shake_offset(&annotation.shakes_ms, time_ms, radius);
    Point {
        x: pos.x + dx,
        y: pos.y,
    }
}

/// Sideways offset of a note that has just refused a click, in osu!pixels.
///
/// A decaying sine: it starts at full swing on the frame the click landed and
/// settles inside a tenth of a second, so a note being clicked at repeatedly
/// shakes on each one rather than blurring into a single long wobble.
fn shake_offset(shakes: &[f64], time_ms: f64, radius: f64) -> f64 {
    let Some(last) = shakes
        .iter()
        .copied()
        .filter(|&at| at <= time_ms && time_ms - at < SHAKE_MS)
        .fold(None::<f64>, |best, at| {
            Some(best.map_or(at, |b: f64| b.max(at)))
        })
    else {
        return 0.0;
    };
    let progress = (time_ms - last) / SHAKE_MS;
    let swing = (progress * SHAKE_CYCLES * std::f64::consts::TAU).sin();
    swing * (1.0 - progress) * radius * SHAKE_WIDTH
}

/// How an arrow at one end of a slider presents itself: how bright, and how
/// much bigger than its resting size.
///
/// `turns` is every moment the ball turns around at *that* end, and `span_ms`
/// is how long one traversal takes. The arrow is full while a turn is coming
/// within one traversal — arriving as the ball sets off towards it, the way
/// lazer brings a repeat in — then goes out over its own window rather than
/// blinking off on the frame the ball touches it. Landing gives it a kick,
/// which is the cue that the direction just changed; it decays quadratically so
/// the kick is over well before the fade is.
///
/// Both ends can therefore be lit at once, which is the point: at a turn the
/// arrow just struck is still fading while the far end's is already up.
///
/// Split out from the drawing because it cannot be measured through pixels:
/// the ball and the ticks pass through the same few square pixels at exactly
/// the moment in question, and there is no telling their brightness from the
/// arrow's.
/// How far a waiting arrow has rocked, in degrees, on a skin old enough to.
///
/// ```csharp
/// const float rotation = 5.625f;
/// arrow.Rotation = ValueAt(loopCurrentTime, rotation, -rotation, 0, duration);
/// ```
///
/// Only for `Version <= 1`. Newer skins hold theirs still and ease the scale
/// instead, which is the same loop wearing different clothes — and both are
/// what osu! does, so which one a skin gets is decided by a line in its own
/// `skin.ini` rather than by us.
fn arrow_rock(time_ms: f64, started_ms: f64) -> f32 {
    const ROTATION: f32 = 5.625;
    let phase = ((time_ms - started_ms).rem_euclid(ARROW_LOOP_MS) / ARROW_LOOP_MS) as f32;
    // Linear across the loop, from one side to the other.
    ROTATION - 2.0 * ROTATION * phase
}

fn arrow_life(
    turns: &[(f64, f64)],
    time_ms: f64,
    reading_ms: f64,
    started_ms: f64,
    span_ms: f64,
) -> (f32, f32) {
    // A turn is due once the ball is on the slide that ends at it — `due` is
    // when that slide begins. Stated as a moment rather than as "within one
    // traversal", because the two are the same in arithmetic and not in
    // floating point: `start + span - start` comes out an ulp above `span`, so
    // the comparison failed at exactly the boundary and the first turn's arrow
    // stayed dark for the whole approach.
    let ahead = turns
        .iter()
        .any(|&(at, due)| at > time_ms && reading_ms >= due);
    let behind = turns
        .iter()
        .map(|&(at, _)| at)
        .filter(|&at| at <= time_ms)
        .fold(None::<f64>, |best, at| {
            Some(best.map_or(at, |b: f64| b.max(at)))
        });

    // How far into its arrival the next turn's arrow is.
    //
    // Only for an arrow that becomes due *during* the slide. The first one is
    // due before the slider has even started and arrives with the body as it
    // snakes out, which is its animation; giving it a second one would fade it
    // in over a slider that is already there. A later arrow had none at all
    // and snapped on at full brightness, which reads as a second slider
    // materialising out of nothing.
    let arriving = turns
        .iter()
        .filter(|&&(at, due)| at > time_ms && reading_ms >= due)
        .map(|&(_, due)| {
            if due <= started_ms {
                1.0
            } else {
                ((reading_ms - due) / ARROW_FADE_MS).clamp(0.0, 1.0) as f32
            }
        })
        .fold(0.0f32, f32::max);

    let leaving = match (ahead, behind) {
        (true, _) => arriving,
        (false, Some(last)) => 1.0 - ((time_ms - last) / ARROW_FADE_MS).clamp(0.0, 1.0) as f32,
        (false, None) => 0.0,
    };
    // `Easing.Out` — the quadratic, quick away and slow to arrive.
    let ease = |t: f32| 1.0 - (1.0 - t.clamp(0.0, 1.0)) * (1.0 - t.clamp(0.0, 1.0));
    let scale = match behind {
        // Struck: it grows into the turn it just marked, over its own slide or
        // three tenths of a second, whichever is shorter.
        Some(last) => {
            let over = span_ms.min(ARROW_LOOP_MS).max(1.0);
            1.0 + (ARROW_STRUCK_TO - 1.0) * ease(((time_ms - last) / over) as f32)
        }
        // Waiting: a three-hundred-millisecond loop, large to small, from the
        // slider's own beginning so every arrow on one slider breathes together.
        None => {
            let phase = ((time_ms - started_ms).rem_euclid(ARROW_LOOP_MS) / ARROW_LOOP_MS) as f32;
            ARROW_LOOP_FROM + (1.0 - ARROW_LOOP_FROM) * ease(phase)
        }
    };
    (leaving, scale)
}

/// Where along the path a moment of a slider falls, as a fraction.
///
/// Reversed slides walk the path backwards, so their local progress is
/// mirrored — which is what makes this the right thing to compare against the
/// grown stretch of the body rather than raw elapsed time.
fn path_fraction(object: &TimedObject, time_ms: f64) -> Option<f64> {
    let TimedKind::Slider {
        slides,
        slide_duration_ms,
        ..
    } = &object.kind
    else {
        return None;
    };
    if *slide_duration_ms <= 0.0 {
        return None;
    }
    let travelled = (time_ms - object.start_ms) / slide_duration_ms;
    let last = f64::from(slides.saturating_sub(1));
    let slide = travelled.floor().clamp(0.0, last);
    let local = (travelled - slide).clamp(0.0, 1.0);
    Some(if (slide as u32).is_multiple_of(2) {
        local
    } else {
        1.0 - local
    })
}

/// How much a note swells as it leaves, as a multiple of its radius.
///
/// A hit expands while it fades — the note is being taken away, and the growth
/// reads as the taking. A miss does not: it stays the size it was and simply
/// stops being there, which is what missing looks like. Making both expand
/// would throw away the only difference between them a still frame can show.
fn hit_expansion(exit: f32, missed: bool) -> f32 {
    if missed {
        1.0
    } else {
        // Eased out, so nearly all the growth is over in the first third. The
        // note has to read as struck, and a strike is not a linear ramp — a
        // linear one looks like the note is being inflated.
        1.0 + 0.4 * (1.0 - (1.0 - exit) * (1.0 - exit))
    }
}

/// Which of Hidden's two fades an object's part takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HiddenFade {
    /// The object's own: a long dissolve for a slider body, the short one for
    /// anything else.
    Own,
    /// The short one whatever the object is — a slider's head is a note.
    AsANote,
    /// None at all: the ball, the follow circle and the reverse arrows are not
    /// in the mod's switch.
    Untouched,
}

/// The slider body between two progress fractions, ready to stroke.
///
/// Built per frame rather than once, because the stretch it covers changes
/// every frame while the slider is growing or retracting. The prebuilt path it
/// replaces was described in this file as the renderer's largest cost, which
/// turned out to be wrong: building a 240-point body measures at 0.0022ms
/// against 1.2441ms to stroke it once, and it is stroked twice. Under a fifth
/// of a percent. See the `path_building_against_stroking` benchmark below —
/// comparing two binaries end to end could not tell, the machine noise being
/// larger than the effect in both directions on successive runs.
fn body_path(object: &TimedObject, (from, to): (f64, f64)) -> Option<tiny_skia::Path> {
    let TimedKind::Slider { path, .. } = &object.kind else {
        return None;
    };
    let (start, interior, end) = path.segment(from, to)?;
    // Sized up front: the builder otherwise regrows both of its buffers a dozen
    // times over a path of a few hundred points, once per slider per frame.
    let mut builder = PathBuilder::with_capacity(interior.len() + 2, interior.len() + 2);
    builder.move_to(start.x as f32, start.y as f32);
    for point in interior {
        builder.line_to(point.x as f32, point.y as f32);
    }
    builder.line_to(end.x as f32, end.y as f32);
    builder.finish()
}

#[cfg(test)]
mod exits {
    use super::*;

    /// One traversal, for the tests that care how far ahead a turn is.
    const SPAN: f64 = 2000.0;

    /// A turn at `at`, due from one traversal before it.
    fn turn(at: f64) -> (f64, f64) {
        (at, at - SPAN)
    }

    #[test]
    fn an_arrow_waits_until_the_ball_sets_off_towards_it() {
        // The end of a slider is where its head circle sits, so an arrow that
        // stands from the start sits underneath the note for the whole first
        // slide. It is due when the slide that ends on it begins.
        let turns = [turn(5000.0)];
        assert_eq!(
            arrow_life(&turns, 2000.0, 2000.0, 0.0, 500.0).0,
            0.0,
            "two traversals out, nothing there yet"
        );
        // Exactly on the boundary — which is the case that broke. Written as
        // `at - now <= span` this failed, because `start + span - start` comes
        // out an ulp above `span` and the arrow stayed dark all approach.
        //
        // The arrow now *starts* arriving here rather than snapping on: a
        // later turn becomes due mid-slide, and appearing at full brightness
        // reads as a second slider materialising out of nothing.
        assert_eq!(
            arrow_life(&turns, 3000.0, 3000.0, 2500.0, 500.0).0,
            0.0,
            "one traversal out, to the millisecond: it begins arriving"
        );
        let midway = arrow_life(&turns, 3000.0 + ARROW_FADE_MS * 0.5, 3000.0 + ARROW_FADE_MS * 0.5, 2500.0, 500.0).0;
        assert!(
            (0.3..0.7).contains(&midway),
            "halfway through arriving: {midway}"
        );
        assert_eq!(
            arrow_life(&turns, 3000.0 + ARROW_FADE_MS, 3000.0 + ARROW_FADE_MS, 2500.0, 500.0).0,
            1.0,
            "and fully there once its fade is done"
        );
    }

    #[test]
    fn an_arrow_holds_while_a_turn_is_coming_and_then_goes_out() {
        let turns = [turn(1000.0), turn(3000.0)];
        assert_eq!(arrow_life(&turns, 500.0, 500.0, 0.0, 500.0).0, 1.0, "before the first");
        assert_eq!(
            arrow_life(&turns, 2500.0, 2500.0, 0.0, 500.0).0,
            1.0,
            "another is still coming, and has finished arriving"
        );

        // After the last one it decays rather than blinking off.
        let half = arrow_life(&turns, 3000.0 + ARROW_FADE_MS / 2.0, 3000.0 + ARROW_FADE_MS / 2.0, 0.0, 500.0)
        .0;
        assert!(half > 0.0 && half < 1.0, "{half}");
        assert_eq!(
            arrow_life(&turns, 3000.0 + ARROW_FADE_MS, 3000.0 + ARROW_FADE_MS, 2500.0, 500.0).0,
            0.0,
            "and is gone"
        );
    }

    #[test]
    fn an_arrow_waiting_breathes_on_a_fixed_loop() {
        // ```csharp
        // const double duration = 300;
        // double loopCurrentTime = (Time.Current - AnimationStartTime) % duration;
        // arrow.Scale = ValueAt(loopCurrentTime, 1.3f, 1, 0, duration, Easing.Out);
        // ```
        //
        // Three tenths of a second, large to small, and *not* the map's tempo —
        // breathing on the beat is the obvious guess and this carried a
        // coefficient for it, set to zero, so the arrow did not breathe at all.
        let turns = [turn(4000.0)];
        let at = |t: f64| arrow_life(&turns, t, t, 0.0, 500.0).1;
        assert!((at(0.0) - ARROW_LOOP_FROM).abs() < 1e-6, "largest at the start");
        assert!(at(ARROW_LOOP_MS - 1.0) < 1.02, "and smallest at the end");
        // And it comes round again.
        assert!((at(ARROW_LOOP_MS) - ARROW_LOOP_FROM).abs() < 1e-6);
        assert!((at(ARROW_LOOP_MS * 3.0) - ARROW_LOOP_FROM).abs() < 1e-6);
    }

    #[test]
    fn a_struck_arrow_grows_into_the_turn_it_marked() {
        // ```csharp
        // double animDuration = Math.Min(300, SpanDuration);
        // arrow.Scale = ValueAt(now, 1, 1.4f, hitTime, hitTime + animDuration, Easing.Out);
        // ```
        let turns = [turn(1000.0)];
        let at = |t: f64, span: f64| arrow_life(&turns, t, t, 0.0, span).1;
        assert!((at(1000.0, 500.0) - 1.0).abs() < 1e-6, "starts at its own size");
        assert!((at(1300.0, 500.0) - ARROW_STRUCK_TO).abs() < 1e-6, "and reaches 1.4");
        // A slide shorter than three tenths of a second finishes sooner.
        assert!((at(1120.0, 120.0) - ARROW_STRUCK_TO).abs() < 1e-6);
        assert!(at(1060.0, 120.0) < ARROW_STRUCK_TO, "part-way at half the span");
    }

    /// A version 1 skin's reverse arrow rocks as it breathes.
    ///
    /// ```csharp
    /// bool shouldRotate = skin.GetConfig<SkinConfiguration.LegacySetting, decimal>(
    ///     SkinConfiguration.LegacySetting.Version)?.Value <= 1;
    /// ...
    /// arrow.Rotation = ValueAt(loopCurrentTime, -5.625f, 5.625f, 0, duration);
    /// ```
    ///
    /// Read the other way round from ppy's `ValueAt`, which counts a rotation
    /// *down* from `+5.625`: the sign is the whole of the effect, so it is
    /// worth being explicit that the arrow leans right first.
    #[test]
    fn an_old_skins_arrow_leans_one_way_and_then_the_other() {
        assert!((arrow_rock(1000.0, 1000.0) - 5.625).abs() < 1e-4, "right, at the top");
        assert!(arrow_rock(1150.0, 1000.0).abs() < 1e-4, "level, halfway");
        assert!(arrow_rock(1290.0, 1000.0) < -5.0, "and left by the end");
        // It loops on its own three hundred milliseconds rather than the map's
        // tempo, so the next pass starts exactly where the first one did.
        assert!(
            (arrow_rock(1300.0, 1000.0) - arrow_rock(1000.0, 1000.0)).abs() < 1e-4,
            "the loop does not drift"
        );
    }

    #[test]
    fn an_end_that_never_turns_shows_nothing() {
        // Only the first half is a claim: with nothing to show, the scale it
        // would have been shown at is not a fact about anything.
        assert_eq!(arrow_life(&[], 1234.0, 1234.0, 0.0, 500.0).0, 0.0);
    }

    #[test]
    fn a_hit_swells_as_it_goes_and_a_miss_does_not() {
        // The two exits have to look different, or a still frame cannot say
        // which happened without waiting for the combo counter to drop.
        assert_eq!(hit_expansion(0.0, false), 1.0, "nothing has happened yet");
        assert!(hit_expansion(1.0, false) > hit_expansion(0.5, false));
        assert_eq!(hit_expansion(1.0, true), 1.0, "a miss keeps its size");
        assert_eq!(hit_expansion(0.5, true), 1.0);
    }
}

#[cfg(test)]
mod cost {
    use super::*;

    /// What building a slider body actually costs, against what stroking one
    /// costs. Run on demand:
    ///
    ///     cargo test --release -p dossier-render path_building -- --ignored --nocapture
    #[test]
    #[ignore = "a measurement, not an assertion"]
    fn path_building_against_stroking() {
        use std::time::Instant;

        // A slider body flattens to a few hundred points at a quarter-pixel.
        let points: Vec<(f32, f32)> = (0..240)
            .map(|i| (i as f32 * 1.7, (i as f32 * 0.11).sin() * 40.0 + 200.0))
            .collect();

        let rounds = 10_000;
        let mark = Instant::now();
        let mut kept = 0usize;
        for _ in 0..rounds {
            let mut builder = PathBuilder::with_capacity(points.len(), points.len());
            builder.move_to(points[0].0, points[0].1);
            for p in &points[1..] {
                builder.line_to(p.0, p.1);
            }
            kept += builder.finish().map_or(0, |p| p.len());
        }
        let building = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

        let mut pixmap = Pixmap::new(1920, 1080).unwrap();
        let mut builder = PathBuilder::with_capacity(points.len(), points.len());
        builder.move_to(points[0].0, points[0].1);
        for p in &points[1..] {
            builder.line_to(p.0, p.1);
        }
        let path = builder.finish().unwrap();
        let paint = Paint::default();
        let stroke = Stroke {
            width: 64.0,
            line_cap: LineCap::Round,
            line_join: LineJoin::Round,
            ..Default::default()
        };

        let strokes = 200;
        let mark = Instant::now();
        for _ in 0..strokes {
            pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
        }
        let stroking = mark.elapsed().as_secs_f64() / f64::from(strokes) * 1000.0;

        println!(
            "slider body: building {building:.4}ms, stroking {stroking:.4}ms \
             — building is {:.2}% of one stroke ({kept} verbs kept)",
            building / stroking * 100.0
        );
    }
}


/// How far apart osu! sets the marks between two notes, in playfield units,
/// and how long before its moment each one appears.
///
/// ```csharp
/// public const int SPACING = 32;
/// public const double PREEMPT = 800;
///
/// for (int d = (int)(SPACING * 1.5); d < distance - SPACING; d += SPACING)
/// {
///     float fraction = (float)d / distance;
///     Vector2 pointStartPosition = startPosition + (fraction - 0.1f) * distanceVector;
///     Vector2 pointEndPosition = startPosition + fraction * distanceVector;
///     ...
///     fp.FadeIn(end.TimeFadeIn);
///     fp.ScaleTo(end.Scale, end.TimeFadeIn, Easing.Out);
///     fp.MoveTo(pointEndPosition, end.TimeFadeIn, Easing.Out);
///     fp.Delay(fadeOutTime - fadeInTime).FadeOut(end.TimeFadeIn);
/// }
/// ```
///
/// The first mark sits a step and a half out and the last stops a step short,
/// so a trail never touches either note. Each slides the last tenth of the way
/// into its place as it appears, which is what makes the row read as running
/// towards the next note rather than as a row of dots switching on.
const FOLLOW_SPACING: f64 = 32.0;
const FOLLOW_PREEMPT_MS: f64 = 800.0;
/// What each mark starts at before it settles to its size, from `ScaleTo`.
const FOLLOW_ENTRY_SCALE: f32 = 1.5;
/// How much of the gap a mark travels as it arrives, from `pointStartPosition`.
const FOLLOW_APPROACH: f64 = 0.1;

/// `Easing.Out` — the quadratic, quick to leave and slow to arrive.
fn ease_out(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    1.0 - (1.0 - t) * (1.0 - t)
}

impl Scene<'_> {
    /// The marks osu! lays between one note and the next.
    ///
    /// Drawn only from a skin's own picture. Our look has never had them and
    /// giving it a set now would redecorate every render made without a skin,
    /// which is a change nobody asked for — where a skin that ships sixty
    /// frames of `followpoint` plainly did ask.
    ///
    /// Within a combo only, and never touching a spinner: a trail says "this
    /// one, then this one" about notes that belong together, and a new combo
    /// is the map saying they do not.
    pub(super) fn draw_follow_points(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        if !self.skin_speaks_for(Element::FollowPoint) {
            return;
        }
        let objects = &self.state.timeline().objects;
        let fade_in = self.state.difficulty().fade_in_ms().max(1.0);
        let radius = self.state.difficulty().circle_radius();

        for index in self.candidates(time_ms) {
            if index == 0 {
                continue;
            }
            let (from, to) = (&objects[index - 1], &objects[index]);
            // A new combo breaks the thread, and a spinner has no place on the
            // field to run to or from.
            if self.annotations[index].number == 1 || from.is_spinner() || to.is_spinner() {
                continue;
            }
            let start_ms = from.end_ms;
            let span = to.start_ms - start_ms;
            if span <= 0.0 {
                continue;
            }
            // Where the previous object leaves the player: the end of a slider,
            // or the note itself.
            let leaves = from.ball_at(from.end_ms).unwrap_or(from.pos);
            let (dx, dy) = (to.pos.x - leaves.x, to.pos.y - leaves.y);
            let distance = dx.hypot(dy);
            if distance <= FOLLOW_SPACING * 2.5 {
                continue;
            }

            let degrees = dy.atan2(dx).to_degrees() as f32;

            let mut walked = FOLLOW_SPACING * 1.5;
            while walked < distance - FOLLOW_SPACING {
                let fraction = walked / distance;
                walked += FOLLOW_SPACING;

                let leaves_at = start_ms + fraction * span;
                let arrives_at = leaves_at - FOLLOW_PREEMPT_MS;
                if time_ms < arrives_at {
                    continue;
                }
                let arriving = ((time_ms - arrives_at) / fade_in).clamp(0.0, 1.0) as f32;
                let leaving = if time_ms > leaves_at {
                    ((time_ms - leaves_at) / fade_in).clamp(0.0, 1.0) as f32
                } else {
                    0.0
                };
                let alpha = arriving * (1.0 - leaving);
                if alpha <= 0.0 {
                    continue;
                }
                // It comes in a tenth of the way behind its place and slides
                // up to it, growing down to size as it goes.
                let along = fraction - FOLLOW_APPROACH * f64::from(1.0 - ease_out(arriving));
                let at = dossier_beatmap::Point {
                    x: leaves.x + dx * along,
                    y: leaves.y + dy * along,
                };
                let scale = FOLLOW_ENTRY_SCALE
                    + (1.0 - FOLLOW_ENTRY_SCALE) * ease_out(arriving);
                self.draw_frame_turned(
                    pixmap,
                    Element::FollowPoint,
                    self.animation_frame(Element::FollowPoint, time_ms - arrives_at),
                    at,
                    layout.length(radius) * scale,
                    alpha,
                    layout,
                    degrees,
                );
            }
        }
    }
}

impl Scene<'_> {
    /// Which frame of an element's strip is showing, `elapsed_ms` after the
    /// thing wearing it appeared.
    ///
    /// > A positive integer or `-1` to make osu! play all frames of the
    /// > animation in one second.
    ///
    /// So the default is not a frame rate at all but a *duration*: however
    /// many frames the skin drew, they take a second between them. A rate
    /// stated in the ini is used as it stands.
    ///
    /// Counted from the element's own beginning rather than from map time,
    /// which is what `GetAnimation("followpoint", true, false)` asks for — the
    /// `false` is `startAtCurrentTime`. Off map time every mark on screen shows
    /// the *same* frame, so a strip whose frames fade in and out blinks the
    /// whole trail on and off together, and on a frame the skin drew empty the
    /// trail disappears outright. Measured on a 61-frame skin: every follow
    /// point missing at three moments out of three.
    fn animation_frame(&self, element: Element, elapsed_ms: f64) -> usize {
        let Some(sprites) = &self.skin.sprites else {
            return 0;
        };
        let count = sprites.frame_count(element);
        if count <= 1 {
            return 0;
        }
        let stated = sprites.ini().animation_framerate;
        let per_second = if stated > 0.0 {
            f64::from(stated)
        } else {
            count as f64
        };
        ((elapsed_ms.max(0.0) / 1000.0 * per_second) as usize) % count
    }

    /// One frame of an animated element, turned. Falls back to the still
    /// picture for anything that does not animate.
    #[allow(clippy::too_many_arguments)]
    fn draw_frame_turned(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        frame: usize,
        centre: Point,
        radius: f32,
        alpha: f32,
        layout: &Layout,
        degrees: f32,
    ) {
        let art = self
            .skin
            .sprites
            .as_ref()
            .and_then(|sprites| sprites.frame(element, frame));
        let Some((art, per_osu_pixel)) = art else {
            self.draw_sprite_turned(pixmap, element, 0, centre, radius, alpha, layout, degrees);
            return;
        };
        if alpha <= 0.0 {
            return;
        }
        let scale = (radius * 2.0) / (SKIN_CIRCLE_PIXELS * per_osu_pixel);
        let (x, y) = layout.map(centre);
        let transform = Transform::from_translate(x, y)
            .pre_rotate(degrees)
            .pre_scale(scale, scale)
            .pre_translate(-(art.width() as f32) / 2.0, -(art.height() as f32) / 2.0);
        pixmap.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            transform,
            None,
        );
    }
}

#[cfg(test)]
mod shading {
    use super::*;

    fn track() -> Color {
        Color::from_rgba8(0, 120, 255, 255)
    }

    fn shade(at: f32) -> Color {
        tube_shade(
            at,
            Color::from_rgba8(255, 255, 255, 255),
            crate::skin::body_outer(track()),
            crate::skin::body_inner(track()),
            0.7,
        )
    }

    /// The formula itself, away from the rasteriser. What changed here is which
    /// colour belongs at which distance from the edge; whether a band of bands
    /// reproduces it faithfully is a separate question with its own answers.
    #[test]
    fn the_outermost_sliver_is_a_shadow_coming_up_from_nothing() {
        // ```csharp
        // Color4 shadow = new Color4(0, 0, 0, 0.25f);
        // if (position <= shadow_portion)
        //     return InterpolateNonLinear(position, Black.Opacity(0f), shadow, 0, shadow_portion);
        // ```
        assert!(shade(0.0).alpha() < 0.001, "nothing at the very edge");
        let inner = shade(SHADOW_PORTION);
        assert!((inner.alpha() - SHADOW_ALPHA).abs() < 0.01, "{}", inner.alpha());
        assert!(inner.red() + inner.green() + inner.blue() < 0.01, "and it is black");
        // Half way along it is half as dark.
        assert!((shade(SHADOW_PORTION / 2.0).alpha() - SHADOW_ALPHA / 2.0).abs() < 0.01);
    }

    #[test]
    fn the_border_is_one_colour_across_its_whole_width() {
        // `if (position <= border_portion) return BorderColour;` — solid, with
        // no crossfade at either edge. Ours faded into its neighbours over a
        // hundredth of the radius, and that softness is what a side-by-side
        // against the client showed missing.
        let white = Color::from_rgba8(255, 255, 255, 255);
        for at in [SHADOW_PORTION + 0.001, 0.12, BORDER_PORTION] {
            let there = shade(at);
            assert!(
                (there.red() - white.red()).abs() < 0.001
                    && (there.alpha() - white.alpha()).abs() < 0.001,
                "the border is not solid at {at}: {there:?}"
            );
        }
    }

    #[test]
    fn the_body_mixes_straight_from_the_border_to_the_centreline() {
        // `InterpolateNonLinear(position, outerColour, innerColour, border_portion, 1)`
        // with no easing is a plain mix. Ours squared it, on the reasoning that
        // a linear ramp "reads as a wide pale core" — a preference, and one the
        // comparison overruled.
        let outer = crate::skin::body_outer(track());
        let inner = crate::skin::body_inner(track());
        let at_start = shade(BORDER_PORTION + 0.0001);
        assert!((at_start.green() - outer.green()).abs() < 0.01, "starts at the outer shade");
        let at_end = shade(1.0);
        assert!((at_end.green() - inner.green()).abs() < 0.01, "ends at the inner one");

        // Half way along the ramp is half way between the two, which is the
        // whole difference from a squared one.
        let half = shade(BORDER_PORTION + (1.0 - BORDER_PORTION) / 2.0);
        let expect = (outer.green() + inner.green()) / 2.0;
        assert!((half.green() - expect).abs() < 0.01, "{} against {expect}", half.green());
    }

    #[test]
    fn the_track_carries_the_alpha_the_game_gives_it() {
        // "legacy skins use a constant value for slider track alpha, regardless
        // of the source colour" — `.Opacity(0.7f)`.
        assert!((shade(0.5).alpha() - 0.7).abs() < 0.001);
        assert!((shade(1.0).alpha() - 0.7).abs() < 0.001);
    }
}
