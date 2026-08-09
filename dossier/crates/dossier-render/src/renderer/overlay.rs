//! The things that happen over the play rather than being part of it: the fail
//! as the health empties and the screen reddens, the danger vignette on a play
//! near death, the verdicts that pop and shrink as notes are judged, and the
//! arrows that warn a break is about to end.
//!
//! These are reactions to the state of the play, not readouts about it — which
//! is why a bare render keeps the danger and the fail and drops the rest. Four
//! are `pub(super)` for the frame's passes to call: `compose_fail` from the
//! fail path, `draw_danger` from both the bare and full overlays,
//! `draw_verdicts` and `draw_break_warning` from the play pass. `beat_kick`,
//! which pulses the break arrows on the music's beat, is theirs alone.

use super::*;

use tiny_skia::{Color, Paint, Pixmap, Transform};

use crate::layout::Layout;
use crate::skin::{with_alpha, ArrowShape};

impl Scene<'_> {
    /// The fail.
    ///
    /// ```csharp
    /// private const float duration = 2500;
    /// ...
    /// drawableRuleset.Playfield.HitObjectContainer.FadeOut(duration / 2);
    /// redFlashLayer.FadeOutFromOne(1000);      // Color4.Red.Opacity(0.6f), additive
    /// Content.ScaleTo(0.85f, duration, Easing.OutQuart);
    /// Content.RotateTo(1, duration, Easing.OutQuart);
    /// Content.FadeColour(Color4.Gray, duration);
    /// ```
    ///
    /// The timing is lazer's — two and a half seconds, the notes gone by
    /// halfway, a red flash across the first second — and the movement is not.
    /// lazer tilts the frame a degree and drops it; this catches its breath
    /// instead. The whole screen pulls in — hard at first and then still
    /// closing, for as long as the music has left — and lets go in the last
    /// half second, back to full size with nothing on it, which is the field
    /// the play started from.
    ///
    /// Two reasons for the change. A tilt is a permanent state — the frame is
    /// left crooked and nothing puts it back — where a squeeze is a movement
    /// that completes, which is what a render wants at its end rather than in
    /// the middle of a stream. And these frames get cut together with others:
    /// a clip that finishes level can be followed by anything, and one that
    /// finishes at a slight angle cannot.
    pub(super) fn compose_fail(
        &self,
        out: &mut Pixmap,
        field: &Pixmap,
        overlay: &Pixmap,
        progress: f32,
        presence: f32,
        layout: &Layout,
    ) {
        out.fill(self.skin.background);

        // In hard and then still going, out all at once. The pull takes most
        // of its distance in the first moment — that is the death — and then
        // keeps creeping inward for as long as the music has left, so the
        // frame is still closing when the sound gives out. The release is the
        // last fifth, and it is meant to look like something let go rather
        // than like something eased.
        let scale = if progress <= FAIL_RELEASE_AT {
            let t = progress / FAIL_RELEASE_AT;
            1.0 - (1.0 - FAIL_SQUEEZE) * (1.0 - (1.0 - t).powi(3))
        } else {
            let t = (progress - FAIL_RELEASE_AT) / (1.0 - FAIL_RELEASE_AT);
            FAIL_SQUEEZE + (1.0 - FAIL_SQUEEZE) * (1.0 - (1.0 - t).powi(3))
        };

        // Around the middle of the frame, so it pulls into itself rather than
        // towards a corner.
        let (cx, cy) = (layout.width as f32 / 2.0, layout.height as f32 / 2.0);
        let transform = Transform::from_translate(cx, cy)
            .pre_scale(scale, scale)
            .pre_translate(-cx, -cy);

        let blit = |out: &mut Pixmap, src: &Pixmap, opacity: f32| {
            let paint = tiny_skia::PixmapPaint {
                opacity,
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            };
            out.draw_pixmap(0, 0, src.as_ref(), &paint, transform, None);
        };

        // Nothing fades while the frame is moving. lazer takes the notes away
        // over the first half and drains the colour out of the rest, and both
        // were here until the whole thing read as the render giving up rather
        // than the play ending. The frame keeps everything it had and springs
        // back to size; only once it is still does `presence` take it away.
        blit(out, field, presence);
        blit(out, overlay, presence);

        let wash = |out: &mut Pixmap, colour: Color, blend: tiny_skia::BlendMode| {
            let mut paint = Paint::default();
            paint.set_color(colour);
            paint.anti_alias = false;
            paint.blend_mode = blend;
            if let Some(rect) =
                Rect::from_xywh(0.0, 0.0, layout.width as f32, layout.height as f32)
            {
                out.fill_rect(rect, &paint, Transform::identity(), None);
            }
        };

        // The red flash is additive and gone within the first second, so it is
        // a blow rather than a tint — but squared on the way out, and at a
        // fraction of lazer's opacity.
        //
        // The constant is lazer's; the surface it lands on is not. There the
        // red goes over a dimmed beatmap background with a lit playfield on
        // top, and 0.6 additive reads as a flash across a picture. Here the
        // field is very nearly black, so the same 0.6 has nothing to compete
        // with and floods the frame into a flat red card for a full second.
        let linear =
            1.0 - (progress * FAIL_ANIMATION_MS as f32 / FAIL_FLASH_MS as f32).clamp(0.0, 1.0);
        let flash = linear * linear;
        if flash > 0.0 {
            wash(
                out,
                with_alpha(self.skin.verdict_miss, flash * FAIL_FLASH_ALPHA),
                tiny_skia::BlendMode::Plus,
            );
        }
    }

    /// A failed play dims out rather than stopping mid-frame.
    ///
    /// The render already ends where the play did; without this it ends on a
    /// hard cut, which reads as the file having been trimmed rather than as
    /// the run having ended. Paired with the slow-down in `video.rs`, the last
    /// second becomes the play giving out.
    ///
    /// Only for a play that actually failed — a run that saw the map out
    /// finishes on its last note, and fading that would be inventing a defeat.
    /// Red creeping in from the edges as the health runs down.
    ///
    /// The health bar already says the number, and a number in a corner is not
    /// something anyone reads while watching a stream being played. This is the
    /// same fact put where it cannot be missed: the field itself starts to go
    /// red, and by the time it is obvious the play is nearly over.
    ///
    /// It only speaks in the last third of the bar. Above that it is silent,
    /// because a warning that is always on is not a warning — and it is drawn
    /// as four bands rather than one wash so the middle of the playfield, where
    /// the notes are, stays clean.
    pub(super) fn draw_danger(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        if self.cannot_die() {
            return;
        }
        let Some(health) = self.state.health_at(time_ms) else {
            return;
        };
        if health >= DANGER_FROM {
            return;
        }
        // Squared, so it stays faint through most of the range and only takes
        // over when the bar is genuinely nearly out.
        let closeness = ((DANGER_FROM - health) / DANGER_FROM).clamp(0.0, 1.0);
        let strength = closeness * closeness * DANGER_MAX;

        let (w, h) = (layout.width as f32, layout.height as f32);
        let reach = h * DANGER_REACH;
        // A hand-made gradient: bands of falling opacity marching inwards. A
        // real one would be a shader per edge, four of them, rebuilt every
        // frame — this costs a few dozen rectangles and looks the same.
        for step in 0..DANGER_BANDS {
            let t = step as f32 / DANGER_BANDS as f32;
            let alpha = strength * (1.0 - t) * (1.0 - t) / DANGER_BANDS as f32 * 3.0;
            let colour = with_alpha(self.skin.verdict_miss, alpha);
            let band = reach / DANGER_BANDS as f32;
            let inset = t * reach;
            for rect in [
                Rect::from_xywh(0.0, inset, w, band),
                Rect::from_xywh(0.0, h - inset - band, w, band),
                Rect::from_xywh(inset, 0.0, band, h),
                Rect::from_xywh(w - inset - band, 0.0, band, h),
            ]
            .into_iter()
            .flatten()
            {
                let mut paint = Paint::default();
                paint.set_color(colour);
                paint.anti_alias = false;
                pixmap.fill_rect(rect, &paint, Transform::identity(), None);
            }
        }
    }


    /// How far into the current beat we are, as a kick that decays across it.
    ///
    /// Zero when the map states no timing at all, which leaves anything built
    /// on it sitting still rather than guessing at a tempo.
    pub(super) fn beat_kick(&self, time_ms: f64) -> f32 {
        let Some(point) = self.state.timeline().timing.timing_point_at(time_ms) else {
            return 0.0;
        };
        if point.beat_length <= 0.0 {
            return 0.0;
        }
        let phase = ((time_ms - point.time_ms) / point.beat_length).rem_euclid(1.0) as f32;
        (1.0 - phase) * (1.0 - phase)
    }

    /// Arrows down both sides while a break is running out.
    ///
    /// Drawn under the cursor and over the field: they are a message to the
    /// player, not part of the map, and nothing about the play should be
    /// hidden behind them.
    /// The verdict each note earned, flashed where the note was.
    ///
    /// osu! does this with a sprite per judgement; here it is the score itself
    /// in the skin's own colours, rising a little and fading out. It answers
    /// the question a viewer actually has watching a replay — *what did that
    /// one give?* — which the combo counter only answers when it breaks.
    ///
    /// A 300 is deliberately the quietest of the four. A clean play should not
    /// be covered in confirmations of its own cleanliness; the eye should be
    /// drawn to the note that went wrong.
    pub(super) fn draw_verdicts(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(font) = &self.skin.font else {
            return;
        };
        let radius = self.state.difficulty().circle_radius();

        for index in self.candidates(time_ms) {
            let annotation = &self.annotations[index];
            let Some(verdict) = annotation.verdict else {
                continue;
            };
            let age = time_ms - annotation.resolved_ms;
            if !(0.0..VERDICT_MS).contains(&age) {
                continue;
            }
            if verdict == Judgement::Great && !self.skin.show_300 {
                continue;
            }
            let progress = (age / VERDICT_MS) as f32;
            // Out quickly at first, then linger: the flash is read in its
            // first fifty milliseconds and the rest is it leaving.
            let alpha = (1.0 - progress).powf(0.6);
            let (text, colour, scale) = match verdict {
                Judgement::Great => ("300", self.skin.verdict_300, 0.42),
                Judgement::Ok => ("100", self.skin.verdict_100, 0.42),
                Judgement::Meh => ("50", self.skin.verdict_50, 0.46),
                // The miss stays the largest of the four: it is the thing the
                // viewer is here to see.
                Judgement::Miss => ("×", self.skin.verdict_miss, 0.85),
            };
            // Still stepped, but far less: the colours already separate them,
            // so this only keeps a wall of 300s from shouting on the classic
            // skin.
            let presence = match verdict {
                Judgement::Great => 0.70,
                Judgement::Ok => 0.85,
                Judgement::Meh => 0.92,
                Judgement::Miss => 1.0,
            };

            let object = &self.state.timeline().objects[index];
            let at = layout.map(object.pos);
            // Collapsing: it arrives oversized and settles onto the note. A
            // miss collapses less, so it is still legible when it goes.
            let settle = if verdict == Judgement::Miss {
                1.0 + (VERDICT_SHRINK - 1.0) * 0.4 * (1.0 - progress)
            } else {
                1.0 + (VERDICT_SHRINK - 1.0) * (1.0 - progress)
            };
            let size = layout.length(radius * scale) * settle;
            font.draw(
                pixmap,
                Label {
                    text,
                    x: at.0,
                    y: at.1 + size * 0.35,
                    size,
                    colour: with_alpha(colour, alpha * presence),
                    align: Align::Centre,
                },
            );
        }
    }

    pub(super) fn draw_break_warning(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(ends) = self
            .state
            .timeline()
            .breaks
            .iter()
            .find(|(starts, ends)| time_ms >= *starts && time_ms < *ends + WARNING_EXIT_MS)
            .map(|&(_, ends)| ends)
        else {
            return;
        };

        let (alpha, scale) = if time_ms < ends {
            let left = ends - time_ms;
            if left > WARNING_MS {
                return;
            }
            // Full strength across the window, with only a short entry so they
            // do not simply appear. Dimming most of the window made the cue
            // arrive late — the window itself is the warning.
            let entering = ((WARNING_MS - left) / WARNING_ENTRY_MS).clamp(0.0, 1.0) as f32;
            let kick = self.beat_kick(time_ms);
            // Never fully dark between beats: an arrow that disappears reads as
            // a rendering fault rather than as a signal.
            (
                (WARNING_REST + WARNING_BEAT * kick) * entering,
                1.0 + WARNING_SWELL * kick,
            )
        } else {
            // Gone: quickly, and shrinking as it goes so the exit is a
            // movement rather than a dimming.
            let leaving = ((time_ms - ends) / WARNING_EXIT_MS).clamp(0.0, 1.0);
            let left = 1.0 - leaving;
            ((left * left) as f32, (1.0 - 0.45 * leaving) as f32)
        };
        if alpha <= 0.01 {
            return;
        }

        // Placed so the tip just touches the field's edge, which puts the body
        // of the arrow wholly outside it. Derived from the arrow's own size
        // rather than fixed: the size follows the circle radius, so a constant
        // inset would have them overlapping the field on a small-circle map and
        // floating away from it on a large-circle one.
        let arrow = self.state.difficulty().circle_radius() * WARNING_SIZE;
        // The tip of the *drawn* shape, not of the geometry: the rounding
        // stroke reaches half its width past the outline, and an arrow placed
        // without allowing for that pokes into the field.
        let reach = arrow * (1.0 + f64::from(ARROW_ROUNDING) / 2.0);
        let size = layout.length(arrow) * scale;
        for y in WARNING_ROWS {
            for (x, dir) in [
                (-reach, (1.0, 0.0)),
                (dossier_beatmap::PLAYFIELD_WIDTH + reach, (-1.0, 0.0)),
            ] {
                self.draw_chevron(
                    pixmap,
                    Turn {
                        at: Point { x, y },
                        dir,
                    },
                    size,
                    alpha,
                    ArrowShape::Rounded,
                    layout,
                );
            }
        }
    }

}
