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
        self.ground(out);

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
        // A skin's own `hit0`/`hit50`/`hit100`/`hit300` replace the lettering,
        // so a render with no typeface still shows judgements when the skin
        // brought pictures of them. Only the fallback needs a font.
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
            // A skin that ships a blank `hit300` has turned it off, the same
            // way `show_300` does — and it says so per judgement, so a skin can
            // hide the 300s and keep the misses.
            let element = crate::elements::Element::Verdict(match verdict {
                Judgement::Great => crate::elements::Verdict::Three,
                Judgement::Ok => crate::elements::Verdict::Hundred,
                Judgement::Meh => crate::elements::Verdict::Fifty,
                Judgement::Miss => crate::elements::Verdict::Miss,
            });
            let alpha = verdict_alpha(age);
            // One size for all four. They used to be 0.42, 0.42, 0.46 and
            // 0.85, which is the same disagreement the skinned marks had — a 50
            // larger than a 100 and a miss twice a 300 — and it was fixed there
            // and left here.
            //
            // The figure is chosen so a skin with pictures and one without look
            // alike: on the skin this was settled on, its `hit100` comes out 42
            // pixels tall and ours came out 16, so the size goes up by the same
            // ratio it was short by.
            let (text, colour) = match verdict {
                Judgement::Great => ("300", self.skin.verdict_300),
                Judgement::Ok => ("100", self.skin.verdict_100),
                Judgement::Meh => ("50", self.skin.verdict_50),
                Judgement::Miss => ("×", self.skin.verdict_miss),
            };
            let scale = VERDICT_TEXT_SCALE;
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
            let settle = verdict_settle(age, verdict == Judgement::Miss);
            let size = layout.length(radius * scale) * settle;
            if self.skin_speaks_for(element) {
                // At the size the skin drew it, in the playfield's own units —
                // *not* against the note, which is what this used to do.
                //
                // Everything else a skin brings is a piece of a hit object and
                // takes the note as its ruler. A judgement is not: osu! hangs
                // it in the playfield beside the objects, so a `hit100` drawn
                // 75 pixels wide is 75 playfield pixels wide whatever the
                // circle size. Measured against the note it came out at the
                // note's radius over 128 — under a third of the size the game
                // draws it, and reported as exactly that.
                let own = self
                    .skin
                    .sprites
                    .as_ref()
                    .and_then(|sprites| sprites.get(element))
                    .map_or(0.0, |sprite| {
                        let full = layout.length(f64::from(sprite.width()));
                        // Every mark is held to a share of the note, measured
                        // on the ink's *height* rather than the canvas or its
                        // width — see the constant for both of those mistakes.
                        //
                        // A deliberate departure, asked for: at the size the
                        // game draws them a 300 on this skin is two thirds of a
                        // note, and a screen of them over a play reads as
                        // clutter rather than as a score. The game has a player
                        // watching the notes; a render has somebody watching
                        // the play.
                        //
                        // Only ever smaller. A skin that already draws a modest
                        // mark is left alone, so this cannot repeat the failure
                        // it replaces.
                        let ink = layout.length(f64::from(sprite.ink_height));
                        let allowed = layout.length(radius * 2.0 * VERDICT_INK_SHARE);
                        if ink > allowed && ink > 0.0 {
                            full * allowed / ink
                        } else {
                            full
                        }
                    });
                // At the size the skin drew it, with no cap. There was one —
                // the note's own diameter — and it was measuring the wrong
                // thing: a judgement is a small figure in the middle of a large
                // transparent canvas, and capping the *canvas* squeezes the
                // figure with it. The skin this was reported against draws its
                // `hit100` as fifty-two pixels of ink on a 256-pixel square, so
                // the cap took a mark that should be two thirds of a note and
                // made it a fifth — which is exactly what "the hits are
                // unusually small" meant. The cap had been put in to answer the
                // opposite complaint, on a skin whose canvases were tight.
                //
                // Neither osu! nor danser caps: `LegacyJudgementPieceOld` draws
                // the sprite at its texture's own size, and danser's
                // `sprite.NewAnimation(frames, …, vector.Centre)` does the
                // same. A skin that wants a modest mark draws a modest one.
                if own > 0.0 {
                    self.draw_sprite_wide(
                        pixmap,
                        element,
                        object.pos,
                        own * settle,
                        alpha * presence,
                        layout,
                    );
                }
                continue;
            }
            let Some(font) = &self.skin.font else {
                continue;
            };
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

    /// The banner a break ends on: whether the play is passing at that moment.
    ///
    /// danser's schedule, which is stable's:
    ///
    /// ```go
    /// if overlay.currentBreak.Length() < 2880 { return }
    /// pass := overlay.ruleset.GetHP(overlay.cursor) >= 0.5
    /// time := min(currentBreak.GetEndTime()-2880, currentBreak.GetEndTime()-currentBreak.Length()/2)
    /// // pass: on at +20, off at +100, on at +160, off at +230, on at +280,
    /// //       and out from +1280 to +1480
    /// // fail: on at +130, off at +230, on at +280, and the same fade out
    /// ```
    ///
    /// Two blinks and a hold, one blink fewer for a fail, and nothing at all on
    /// a break under three seconds — there is no room to say it and be read.
    /// Health decides which, and half is the line.
    ///
    /// Only the skin's picture. Lettering one ourselves would be a design
    /// decision rather than a fallback, so a skin without the file simply shows
    /// nothing at its breaks.
    pub(super) fn draw_section(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let Some(&(from, to)) = self
            .state
            .timeline()
            .breaks
            .iter()
            .find(|&&(from, to)| time_ms >= from && time_ms <= to)
        else {
            return;
        };
        let length = to - from;
        if length < SECTION_MIN_BREAK_MS {
            return;
        }
        let at = (to - SECTION_MIN_BREAK_MS).min(to - length / 2.0);

        // Health at the moment the banner is decided, not at the moment it is
        // drawn: the two are a second apart and the answer must not change
        // under the blink.
        let passing = self
            .state
            .health_at(at)
            .is_none_or(|health| health >= SECTION_PASS_HEALTH);
        let element = if passing {
            crate::elements::Element::SectionPass
        } else {
            crate::elements::Element::SectionFail
        };
        if !self.skin_speaks_for(element) {
            return;
        }

        // The blink, as a series of steps. Each pair is "from this moment, at
        // this opacity", and the last runs the fade out.
        let steps: &[(f64, f32)] = if passing {
            &[(20.0, 1.0), (100.0, 0.0), (160.0, 1.0), (230.0, 0.0), (280.0, 1.0)]
        } else {
            &[(130.0, 1.0), (230.0, 0.0), (280.0, 1.0)]
        };
        let since = time_ms - at;
        if since < steps[0].0 {
            return;
        }
        let mut alpha = 0.0;
        for &(offset, level) in steps {
            if since >= offset {
                alpha = level;
            }
        }
        if since >= SECTION_FADE_FROM_MS {
            let out = ((since - SECTION_FADE_FROM_MS)
                / (SECTION_FADE_TO_MS - SECTION_FADE_FROM_MS))
                .clamp(0.0, 1.0) as f32;
            alpha = 1.0 - out;
        }
        if alpha <= 0.0 {
            return;
        }

        let own = self
            .skin
            .sprites
            .as_ref()
            .and_then(|sprites| sprites.get(element))
            .map_or(0.0, |sprite| self.skin_pixels(layout, sprite.width()));
        if own <= 0.0 {
            return;
        }
        self.draw_sprite_wide(
            pixmap,
            element,
            dossier_beatmap::Point::CENTRE,
            own,
            alpha,
            layout,
        );
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

/// How solid a verdict is at `age` milliseconds old, on stable's envelope.
fn verdict_alpha(age: f64) -> f32 {
    if age < VERDICT_FADE_IN_MS {
        (age / VERDICT_FADE_IN_MS) as f32
    } else if age < VERDICT_HOLD_MS {
        1.0
    } else {
        (1.0 - (age - VERDICT_HOLD_MS) / VERDICT_FADE_OUT_MS).clamp(0.0, 1.0) as f32
    }
}

/// How large it is drawn, likewise.
///
/// ```csharp
/// // a miss
/// this.ScaleTo(1.6f);
/// this.ScaleTo(1, 100, Easing.In);
///
/// // everything else
/// this.ScaleTo(0.6f)
///     .Then().ScaleTo(1.1f, fade_in_length * 0.8f)
///     .Then().Delay(fade_in_length * 0.2f)
///     .ScaleTo(0.9f, fade_in_length * 0.2f)
///     .Then().ScaleTo(1f, fade_in_length * 0.2f);
/// ```
///
/// The two go opposite ways, and that is the point of them: a miss lands large
/// and snaps down, a score springs up from small and settles with a small
/// bounce. Ours used to collapse from oversized in both cases — which read
/// well over 240 milliseconds and would read as a slow deflation stretched
/// across eleven hundred.
fn verdict_settle(age: f64, missed: bool) -> f32 {
    let step = VERDICT_FADE_IN_MS * 0.2;
    if missed {
        // `Easing.In` is the quadratic: slow to leave, quick to arrive.
        let t = (age / 100.0).clamp(0.0, 1.0) as f32;
        return 1.6 + (1.0 - 1.6) * t * t;
    }
    let ease = |from: f32, to: f32, at: f64, over: f64| {
        from + (to - from) * (at / over).clamp(0.0, 1.0) as f32
    };
    if age < step * 4.0 {
        ease(0.6, 1.1, age, step * 4.0)
    } else if age < step * 5.0 {
        1.1
    } else if age < step * 6.0 {
        ease(1.1, 0.9, age - step * 5.0, step)
    } else if age < step * 7.0 {
        ease(0.9, 1.0, age - step * 6.0, step)
    } else {
        1.0
    }
}


impl Scene<'_> {
    /// The flash a struck note leaves on the field.
    ///
    /// > Blend Mode: Additive … Tinting depends on the hit circle's combo
    /// > colour.
    ///
    /// Both halves matter. Laid over the field it would be a grey disc sitting
    /// on top of the play; added to it, it is light, which is what a flash is.
    /// And it carries the note's own colour, so a stream reads as the combo
    /// lighting up rather than as a row of identical white blooms.
    ///
    /// Only where a note was actually struck: osu! puts this in
    /// `ApplyHitAnimations` and gives a miss nothing. From the skin alone, and
    /// only when [`Skin::hit_lighting`] asks for it — which by default it does
    /// not.
    pub(super) fn draw_lighting(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        if !self.skin.hit_lighting
            || !self.skin_speaks_for(crate::elements::Element::Lighting)
        {
            return;
        }
        let radius = self.state.difficulty().circle_radius();
        for index in self.candidates(time_ms) {
            let annotation = &self.annotations[index];
            let Some(verdict) = annotation.verdict else {
                continue;
            };
            if verdict == Judgement::Miss {
                continue;
            }
            let age = time_ms - annotation.resolved_ms;
            if !(0.0..LIGHTING_MS).contains(&age) {
                continue;
            }
            let alpha = if age < LIGHTING_FADE_IN_MS {
                (age / LIGHTING_FADE_IN_MS) as f32
            } else if age < LIGHTING_HOLD_MS {
                1.0
            } else {
                (1.0 - (age - LIGHTING_HOLD_MS) / LIGHTING_FADE_OUT_MS).clamp(0.0, 1.0) as f32
            };
            if alpha <= 0.0 {
                continue;
            }
            // `Easing.Out` on the growth, so it opens fast and keeps drifting
            // wider for the whole second it is going.
            let t = (age / LIGHTING_GROWTH_MS).clamp(0.0, 1.0) as f32;
            let eased = 1.0 - (1.0 - t) * (1.0 - t);
            let scale = LIGHTING_FROM + (LIGHTING_TO - LIGHTING_FROM) * eased;
            let object = &self.state.timeline().objects[index];
            self.draw_sprite_blended(
                pixmap,
                crate::elements::Element::Lighting,
                annotation.colour,
                object.pos,
                layout.length(radius) * scale,
                alpha,
                layout,
                0.0,
                tiny_skia::BlendMode::Plus,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The envelope, straight off stable's own transforms:
    ///
    /// ```csharp
    /// this.FadeInFromZero(120);
    /// this.Delay(500).FadeOut(600);
    /// ```
    ///
    /// Checked here rather than by counting lit pixels in a frame. Two goes at
    /// the pixel version measured the cursor parked on top of the mark, and
    /// then a frame that was empty because the map had ended — both of which
    /// are facts about the fixture and neither about the thing that changed.
    #[test]
    fn a_verdict_holds_half_a_second_before_it_starts_leaving() {
        assert_eq!(verdict_alpha(0.0), 0.0, "it fades in from nothing");
        assert!((verdict_alpha(60.0) - 0.5).abs() < 0.01, "halfway in at 60ms");
        assert_eq!(verdict_alpha(120.0), 1.0);
        assert_eq!(verdict_alpha(499.0), 1.0, "full for the whole hold");
        assert!((verdict_alpha(800.0) - 0.5).abs() < 0.01, "halfway out at 800ms");
        assert_eq!(verdict_alpha(1100.0), 0.0, "and gone at eleven hundred");
    }

    #[test]
    fn it_outlasts_the_quarter_second_it_used_to_get() {
        // The report was that they went too fast to read. 240ms was the old
        // whole life; it is now barely past the hold.
        assert_eq!(verdict_alpha(240.0), 1.0);
        assert!(verdict_alpha(900.0) > 0.0, "still readable most of a second on");
    }

    #[test]
    fn a_miss_lands_large_and_snaps_down_while_a_score_springs_up() {
        // ```csharp
        // this.ScaleTo(1.6f); this.ScaleTo(1, 100, Easing.In);      // miss
        // this.ScaleTo(0.6f).Then().ScaleTo(1.1f, 96);              // the rest
        // ```
        //
        // Opposite directions, which is the whole point of them: the two are
        // told apart before either is read.
        assert!((verdict_settle(0.0, true) - 1.6).abs() < 0.001);
        assert!((verdict_settle(100.0, true) - 1.0).abs() < 0.001);
        assert!(verdict_settle(50.0, true) > 1.0, "on its way down, not up");

        assert!((verdict_settle(0.0, false) - 0.6).abs() < 0.001);
        assert!((verdict_settle(96.0, false) - 1.1).abs() < 0.001, "overshoots");
        assert!((verdict_settle(500.0, false) - 1.0).abs() < 0.001, "and settles");
    }

    #[test]
    fn both_are_at_rest_long_before_the_mark_goes() {
        // The size settles inside the first fifth of a second and the fade
        // runs for a second after that, so nothing is still moving while it
        // leaves. Ours used to shrink across the whole life, which read as a
        // deflation once the life was eleven hundred milliseconds long.
        for missed in [true, false] {
            assert!((verdict_settle(200.0, missed) - 1.0).abs() < 0.001, "{missed}");
            assert!((verdict_settle(1000.0, missed) - 1.0).abs() < 0.001, "{missed}");
        }
    }
}
