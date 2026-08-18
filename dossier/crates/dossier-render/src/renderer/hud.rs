//! The readouts laid over the play: score, accuracy and combo along the top,
//! the health and progress bars, the spinner's read-out, the hit-error meter at
//! the foot, and the provenance line in the corner.
//!
//! All of it is meant to be glanced at, not read — a render is watched for the
//! play — so every piece here fades with `hud_presence` and draws in the quiet
//! colours the skin gives it. The shapes come from `paint`, the number and name
//! formatting from `format`, and the rest of the frame's vocabulary from the
//! parent through `use super::*`.
//!
//! Three methods are `pub(super)`: `draw_hud` and `draw_signature`, which the
//! overlay pass calls, and `hud_presence`, which it reads to fade the key
//! counters in step with everything else here.

use super::*;
use super::format::grouped;
use super::paint::{draw_bar, draw_pill, pie};

/// The interface's proportions, as fractions of the frame's height.
///
/// stable states its own in a space 768 units tall and scales that to the
/// screen, and danser follows it; each of these is one of those numbers over
/// 768. Keeping them together, and named, is the difference between a HUD that
/// matches the game and one that was nudged until it looked about right.
const SCORE_SIZE: f64 = 0.050;
const ACCURACY_OF_SCORE: f32 = 0.6;
const COMBO_OF_SCORE: f32 = 1.28 / 0.96;
const PROGRESS_RADIUS: f64 = 16.0 / 768.0;
const EDGE_MARGIN: f64 = 12.8 / 768.0;
/// How far the dial's centre sits left of the accuracy's own slot. danser
/// subtracts `38.4*scale` and then a further `9.6*scale` of right offset:
///
/// ```go
/// rightOffset := -9.6 * scoreScale
/// accOffset := overlay.ScaledWidth - ...GetWidthMonospaced(accSize, "99.99%")
///     + accOverlap - 38.4*scoreScale + rightOffset
/// ```
const PROGRESS_GAP: f64 = 48.0 / 768.0;
/// How much shorter the health bar is drawn than the game would draw it.
///
/// Not one of stable's numbers. Its own length comes from the skin's picture,
/// which stable shows in a 4:3 frame and we show in a 16:9 one — at full length
/// it crosses most of the width and reads as a loading bar rather than as a
/// reading.
/// Cut where the skin's own sentence reaches "emilia type", which is where it
/// was asked to stop.
const HEALTH_BAR_TRIM: f32 = 0.65;

use tiny_skia::{Pixmap, PixmapPaint, Transform};

use crate::layout::Layout;
use crate::skin::with_alpha;
use crate::text::{Align, Label};

impl Scene<'_> {
    /// The skin's glyphs for one line, with the scale they are drawn at and
    /// the width the line comes to on screen.
    ///
    /// osu! skins the numbers in the corners separately from the ones on the
    /// notes, and they usually look nothing alike: the note digits are large
    /// and decorative, these are small and meant to be read at a glance. So
    /// this is its own set of files and its own layout.
    ///
    /// All the glyphs or none — where "none" means the skin has no such file.
    /// A skin *missing* `score-percent` would otherwise draw the accuracy
    /// without its sign, which reads as a broken number rather than as a skin
    /// that left a file out. A skin that ships an empty one has said something
    /// different and is obeyed: that glyph is simply not drawn.
    ///
    /// Split out from the drawing because the *placing* needs the same answer.
    /// The dial beside the accuracy used to be positioned off our typeface
    /// while the accuracy itself was drawn in the skin's — so a skin with
    /// narrower figures left the dial marooned in the gap, which is what it
    /// looked like: an element from a different interface.
    #[allow(clippy::type_complexity)]
    fn hud_glyphs(
        &self,
        text: &str,
        height: f32,
    ) -> Option<(Vec<Option<(&tiny_skia::Pixmap, f32)>>, f32, f32)> {
        let sprites = self.skin.sprites.as_ref()?;
        let mut art = Vec::with_capacity(text.len());
        for glyph in text.chars() {
            // Spaces are not a file. `grouped()` writes them into a score, and
            // a skin has nothing to say about them beyond leaving a gap.
            if glyph == ' ' {
                art.push(None);
                continue;
            }
            let element = crate::elements::Element::Score(glyph);
            if sprites.silenced(element) {
                // Blanked on purpose, which is not the same as missing. The
                // skin this was written against ships an empty `score-x`,
                // hiding the sign after the combo — and treating that as "this
                // skin cannot draw the line" put the whole combo back in our
                // own typeface beside a score in the skin's.
                continue;
            }
            art.push(Some(sprites.coloured(element, 0)?));
        }
        // Sized off the tallest glyph so a line of mixed figures and signs sits
        // on one baseline whatever the skin drew them at.
        let tallest = art
            .iter()
            .flatten()
            .map(|(pixmap, per)| pixmap.height() as f32 / per)
            .fold(0.0f32, f32::max)
            .max(1.0);
        let scale = height / tallest;
        let overlap = sprites.ini().score_overlap;
        let width: f32 = art
            .iter()
            .map(|glyph| match glyph {
                Some((pixmap, per)) => pixmap.width() as f32 / per - overlap,
                // A space is a third of the line's height, which is about what
                // a digit's own advance comes to in these faces.
                None => height / scale / 3.0,
            })
            .sum::<f32>()
            + overlap;
        Some((art, scale, width * scale))
    }

    /// How wide a line of HUD text comes out, in the glyphs it will be drawn
    /// in — the skin's when it has them, ours when it does not.
    fn hud_width(&self, font: &crate::text::Font, text: &str, height: f32) -> f32 {
        self.hud_glyphs(text, height)
            .filter(|(art, _, _)| !art.is_empty())
            .map_or_else(|| font.width(text, height), |(_, _, width)| width)
    }

    /// One line of the skin's own HUD lettering, or `false` if it cannot draw
    /// it and the typeface should.
    pub(super) fn draw_hud_text(
        &self,
        pixmap: &mut Pixmap,
        text: &str,
        right_x: f32,
        baseline_y: f32,
        height: f32,
        align: Align,
        alpha: f32,
    ) -> bool {
        self.draw_hud_glyphs(pixmap, text, right_x, baseline_y, height, align, alpha, None)
    }

    /// The same, with the skin's figures put through a colour.
    ///
    /// Only for the tally, and only because the tally is ours: osu! has no
    /// such readout, so there is no skin decision to override. What the four
    /// colours carry is which row is which, and a skin ships one set of
    /// glyphs — so without this, asking for the skin's figures would have
    /// meant four identical white numbers stacked in a corner.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn draw_hud_text_in(
        &self,
        pixmap: &mut Pixmap,
        text: &str,
        right_x: f32,
        baseline_y: f32,
        height: f32,
        align: Align,
        alpha: f32,
        colour: tiny_skia::Color,
    ) -> bool {
        self.draw_hud_glyphs(
            pixmap, text, right_x, baseline_y, height, align, alpha, Some(colour),
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn draw_hud_glyphs(
        &self,
        pixmap: &mut Pixmap,
        text: &str,
        right_x: f32,
        baseline_y: f32,
        height: f32,
        align: Align,
        alpha: f32,
        tint: Option<tiny_skia::Color>,
    ) -> bool {
        let Some((art, scale, width)) = self.hud_glyphs(text, height) else {
            return false;
        };
        if art.is_empty() {
            return true;
        }
        let overlap = self.skin.sprites.as_ref().map_or(0.0, |s| s.ini().score_overlap);
        let mut pen = match align {
            Align::Right => right_x - width,
            Align::Centre => right_x - width / 2.0,
            Align::Left => right_x,
        };
        for glyph in art {
            let Some((art_pixmap, per)) = glyph else {
                pen += height / 3.0;
                continue;
            };
            let each = scale / per;
            let drawn_height = art_pixmap.height() as f32 * each;
            let transform =
                Transform::from_translate(pen, baseline_y - drawn_height).pre_scale(each, each);
            // The alpha is untouched, so the glyph keeps its own shape and its
            // anti-aliased edge and only changes colour.
            let painted = tint.map(|colour| crate::imported::tinted(art_pixmap, colour));
            pixmap.draw_pixmap(
                0,
                0,
                painted.as_ref().unwrap_or(art_pixmap).as_ref(),
                &PixmapPaint {
                    opacity: alpha.clamp(0.0, 1.0),
                    quality: tiny_skia::FilterQuality::Bilinear,
                    ..Default::default()
                },
                transform,
                None,
            );
            pen += (art_pixmap.width() as f32 / per - overlap) * scale;
        }
        true
    }

    pub(super) fn draw_hud(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let (Some(font), Some(judge)) = (&self.skin.font, self.state.judge()) else {
            return;
        };
        // A break thins the interface rather than clearing it. The timeline,
        // the accuracy and the combo stay — a viewer still wants to know where
        // they are and how the play stands — while the health bar and the
        // error meter go, because neither says anything while nobody is
        // playing.
        let presence = self.hud_presence(time_ms);
        let score = judge.state_at(time_ms);
        let height = f64::from(layout.height);
        let margin = (height * EDGE_MARGIN) as f32;

        // The score sits above the accuracy and is drawn larger, because it
        // is the number the play is finally judged on. Which arithmetic it is
        // follows the client that recorded the replay: stable's climbs into
        // the hundreds of millions on a long map, lazer's is capped at a
        // million on every map. Grouping the digits is not decoration — nine
        // unbroken figures cannot be read at a glance in motion.
        // Sized as stable sizes them. danser states its interface in a space
        // 768 units tall and scales that to the frame, so every number here is
        // one of its own divided by 768 — `app/states/components/overlays/`,
        // `scoreoverlay.go` and `play/combocounter.go`:
        //
        //     scoreSize := overlay.scoreFont.GetSize() * scoreScale * 0.96
        //     accSize := scoreSize * 0.6
        //     scl := settings.Gameplay.ComboCounter.Scale * 1.28
        //
        // Ours were each a little larger and the ratios between them were
        // wrong — the accuracy stood at 0.78 of the score where the game puts
        // it at 0.6, which is most of why the interface read as oversized.
        let score_size = (height * SCORE_SIZE) as f32;
        let accuracy_size = score_size * ACCURACY_OF_SCORE;
        // The first line is centred on the band the health bar and the
        // timeline share, so the three read as one row across the top rather
        // than as a bar with a paragraph hanging beside it.
        let leads = if self.state.score_at(time_ms).is_some() {
            score_size
        } else {
            accuracy_size
        };
        let mut top = self.top_band(layout) + font.digit_height(leads) / 2.0 - leads;
        if let Some(value) = self.state.score_at(time_ms) {
            let text = grouped(value);
            let right = layout.width as f32 - margin;
            if !self.draw_hud_text(
                pixmap, &text, right, top + score_size, score_size, Align::Right, 1.0,
            ) {
                font.draw(
                    pixmap,
                    Label {
                        text: &text,
                        x: right,
                        y: top + score_size,
                        size: score_size,
                        colour: self.skin.hud,
                        align: Align::Right,
                    },
                );
            }
            top += score_size * 1.15;
        }
        let accuracy = format!("{:.2}%", score.accuracy());
        let right = layout.width as f32 - margin;
        if !self.draw_hud_text(
            pixmap, &accuracy, right, top + accuracy_size, accuracy_size, Align::Right, 1.0,
        ) {
            font.draw(
                pixmap,
                Label {
                    text: &accuracy,
                    x: right,
                    y: top + accuracy_size,
                    size: accuracy_size,
                    colour: self.skin.hud,
                    align: Align::Right,
                },
            );
        }
        // Left of the accuracy, as stable places it. Measured off the text
        // rather than guessed at a fraction of the frame, so it stays put when
        // the accuracy is 100.00% and when it is 9.99%.
        // Radius and place both from danser: `DrawCircleProgressS(..., 16 *
        // scale, 40, progress)` at an offset measured off a *monospaced*
        // "99.99%" rather than off the live text — so the dial holds still
        // while the accuracy's digits change under it.
        let radius = (height * PROGRESS_RADIUS) as f32;
        self.draw_progress(
            pixmap,
            time_ms,
            right - self.hud_width(font, "99.99%", accuracy_size) - (height * PROGRESS_GAP) as f32,
            top + accuracy_size - font.digit_height(accuracy_size) / 2.0,
            radius,
            1.0,
        );

        // Bigger than the accuracy, and pulsing: it is the number a viewer
        // actually follows.
        let combo_size = score_size * COMBO_OF_SCORE * self.combo_pulse(time_ms);
        let combo = format!("{}x", score.combo);
        let bottom = layout.height as f32 - margin;
        if !self.draw_hud_text(
            pixmap, &combo, margin, bottom, combo_size, Align::Left, 1.0,
        ) {
            font.draw(
                pixmap,
                Label {
                    text: &combo,
                    x: margin,
                    y: bottom,
                    size: combo_size,
                    colour: self.skin.hud,
                    align: Align::Left,
                },
            );
        }

        // The tally, stacked under the accuracy in the verdict colours. A
        // viewer watching a replay wants the shape of the play, and "two
        // hundreds and a miss" is a different play from "three hundreds" at
        // the same percentage.
        //
        // Vertical, because a row of four spread four hundred pixels across
        // the top of the frame is not a corner — it is a banner, and it reads
        // as one. A column right-aligned on the same edge as the score keeps
        // the whole block the shape of the corner it is in, and takes the
        // question of the numbers moving with it: a right edge cannot shift.
        let tally_size = (height * 0.030) as f32;
        let counts = score.counts;
        let tally = [
            (u32::from(counts.count_300), self.skin.verdict_300),
            (u32::from(counts.count_100), self.skin.verdict_100),
            (u32::from(counts.count_50), self.skin.verdict_50),
            (u32::from(counts.count_miss), self.skin.verdict_miss),
        ];
        let mut y = top + accuracy_size + tally_size * 1.6;
        let right_edge = layout.width as f32 - margin;
        for (value, colour) in tally {
            // The skin's own figures when it brought some, like every other
            // number in this corner — put through the row's colour, which is
            // what says which row it is.
            let text = format!("{value}");
            if !self.draw_hud_text_in(
                pixmap, &text, right_edge, y, tally_size, Align::Right, presence, colour,
            ) {
                font.draw(
                    pixmap,
                    Label {
                        text: &text,
                        x: right_edge,
                        y,
                        size: tally_size,
                        colour: with_alpha(colour, presence),
                        align: Align::Right,
                    },
                );
            }
            y += tally_size * 1.25;
        }

        // Always on: they orient rather than report.
        self.draw_health(pixmap, time_ms, layout, presence);
        // The error bar and the spinner's speed share one place, because during
        // a spinner the bar has nothing to say: there are no clicks to time, so
        // it would sit there showing the last note before the spinner for as
        // long as the spinner lasts — a stale reading, which is worse than an
        // empty space and much worse than a live one.
        let spinning = self.spinner_grip(time_ms);
        if spinning < 1.0 {
            self.draw_error_bar(pixmap, time_ms, layout, presence * (1.0 - spinning));
        }
        if spinning > 0.0 {
            self.draw_spin_readout(pixmap, time_ms, layout, presence * spinning);
        }
    }

    pub(super) fn draw_signature(&self, pixmap: &mut Pixmap, layout: &Layout) {
        let (Some(font), Some(signature)) = (&self.skin.font, &self.signature) else {
            return;
        };
        let height = f64::from(layout.height);
        let margin = (height * EDGE_MARGIN) as f32;
        let client_size = (height * 0.028) as f32;
        let version_size = (height * 0.015) as f32;

        // Faint, and the version fainter still: the client is the part worth
        // catching at a glance, the build only matters to whoever goes looking.
        let bottom = layout.height as f32 - margin;
        font.draw(
            pixmap,
            Label {
                text: &signature.version,
                x: layout.width as f32 - margin,
                y: bottom,
                size: version_size,
                colour: with_alpha(self.skin.hud, 0.20),
                align: Align::Right,
            },
        );
        font.draw(
            pixmap,
            Label {
                text: &signature.client,
                x: layout.width as f32 - margin,
                y: bottom - version_size * 1.15,
                size: client_size,
                colour: with_alpha(self.skin.hud, 0.30),
                align: Align::Right,
            },
        );
        if !signature.mods.is_empty() {
            font.draw(
                pixmap,
                Label {
                    text: &signature.mods,
                    x: layout.width as f32 - margin,
                    y: bottom - version_size * 1.15 - client_size * 1.35,
                    size: client_size * 1.35,
                    colour: with_alpha(self.skin.hud, 0.80),
                    align: Align::Right,
                },
            );
        }
    }

    /// How present the interface should be: one during play, nothing in the
    /// middle of a break, easing across the edges.
    pub(super) fn hud_presence(&self, time_ms: f64) -> f32 {
        let mut presence = 1.0f32;
        for &(from, to) in &self.state.timeline().breaks {
            if to - from < BREAK_HUD_FADE_MS * 2.0 {
                continue;
            }
            if time_ms < from || time_ms > to {
                continue;
            }
            let into = ((time_ms - from) / BREAK_HUD_FADE_MS).clamp(0.0, 1.0) as f32;
            let out_of = ((to - time_ms) / BREAK_HUD_FADE_MS).clamp(0.0, 1.0) as f32;
            presence = presence.min(1.0 - into.min(out_of));
        }
        presence
    }

    /// The three bars: health at the very top, progress under it, and the
    /// hit-error meter at the foot of the screen.
    ///
    /// Where the two bars live: a centred strip, inset from the edges.
    ///
    /// Full-width bars pinned to the very top read as a browser's loading
    /// indicator — they belong to the window rather than to the play. Pulled
    /// in and given room, they become part of the piece.
    /// The line everything along the top of the frame is centred on.
    ///
    /// One band, three things: the health bar in the left corner, the timeline
    /// in the middle, the score in the right. The timeline used to run nearly
    /// the full width, which left the corners to be stacked underneath it — so
    /// the bar sat below the strip rather than beside it and the top of the
    /// frame was three rows deep for no reason.
    fn top_band(&self, layout: &Layout) -> f32 {
        layout.height as f32 * 0.042
    }

    /// How far through the song, as stable draws it: a small disc that fills
    /// clockwise, with a point at its centre.
    ///
    /// It replaced a bar across the middle of the top. That bar could show the
    /// breaks on it, which this cannot, and it is still the right trade: the
    /// strip took the width the corners wanted and read as a browser loading
    /// the page rather than as part of a play.
    ///
    /// Placed left of the accuracy, where the game puts it, so the top-right
    /// reads as one block: score, then accuracy, then how far in you are.
    fn draw_progress(
        &self,
        pixmap: &mut Pixmap,
        time_ms: f64,
        cx: f32,
        cy: f32,
        radius: f32,
        presence: f32,
    ) {
        let (from, to) = self.state.span_ms();
        if to <= from || radius <= 0.5 {
            return;
        }
        let played = (((time_ms - from) / (to - from)).clamp(0.0, 1.0)) as f32;

        // A disc that fills, and a hairline saying how far there is to go.
        //
        // It used to have a heavy rim and a bright point at the centre, and
        // together with a wedge that is thin for most of a map those two made
        // it a clock face: a hand on a dial, drawn in more ink than anything
        // else in the corner, next to numbers that are all thin lettering.
        // Reported as looking like it came from a different interface, which
        // is exactly what it was — neither the rim nor the point is in the
        // game. danser fills a plain circle and stops:
        //
        // ```go
        // DrawCircleProgressS(batch, position, 16*scale, 40, progress)
        // ```
        crate::elements::ring(
            pixmap,
            cx,
            cy,
            radius,
            (radius * 0.07).max(1.0),
            self.skin.hud,
            0.22 * presence,
        );
        pie(
            pixmap,
            cx,
            cy,
            radius * 0.88,
            played,
            with_alpha(self.skin.hud, 0.45 * presence),
        );
    }

    /// Health, as a thick bar in the top-left.
    ///
    /// Given weight and its own corner rather than tucked under the timeline:
    /// it is the one reading that decides whether the play survives, and on a
    /// failed run it is the thing the viewer watches. Everything else on
    /// screen is a record of what happened; this is the only part that says
    /// what is *about* to.
    fn draw_health(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        if self.cannot_die() {
            return;
        }
        let Some(health) = self.state.health_at(time_ms) else {
            return;
        };
        let height = f64::from(layout.height);
        let margin = (height * EDGE_MARGIN) as f32;
        // Across the top, the way stable runs its scorebar. Its length is the
        // skin's own — danser draws the bar at `healthBar.Texture.Width` in the
        // 768-tall interface space, so a 695-pixel bar is 695 of those units —
        // and ours is half the frame when there is no skin to ask. Sixty-two
        // hundredths was a guess and read as a bar that would not end.
        // Its length is the skin's own — danser draws the bar at
        // `healthBar.Texture.Width` in the 768-tall interface space, so a
        // 695-pixel bar is 695 of those units — taken down by a quarter, which
        // is ours and not the game's: at full length it runs most of the way
        // across a 16:9 frame, which stable never has to sit in.
        let width = self
            .skin
            .sprites
            .as_ref()
            .and_then(|s| {
                s.get(crate::elements::Element::ScoreBarFill)
                    .or_else(|| s.get(crate::elements::Element::ScoreBarBackground))
            })
            .map_or(layout.width as f32 * 0.5, |sprite| {
                self.skin_pixels(layout, sprite.width())
            })
            * HEALTH_BAR_TRIM;
        let thickness = (height * 0.018).max(5.0) as f32;
        let y = self.top_band(layout) - thickness / 2.0;

        if self.draw_skin_health(pixmap, health, margin, y, width, presence, layout.height) {
            return;
        }
        draw_pill(
            pixmap,
            margin,
            y,
            width,
            thickness,
            with_alpha(self.skin.hud, 0.13 * presence),
        );
        // Below a third it turns the miss colour: a play about to end should
        // say so before it does.
        let (colour, alpha) = if health < 0.33 {
            (self.skin.verdict_miss, 0.95)
        } else {
            (self.skin.hud, 0.62)
        };
        draw_pill(
            pixmap,
            margin,
            y,
            width * health,
            thickness,
            with_alpha(colour, alpha * presence),
        );
    }

    /// The health bar out of the skin's own pieces, or `false` for ours.
    ///
    /// Three pieces, each optional and each meaning something different when
    /// it is absent. The frame goes down first, the fill is clipped to the
    /// health and laid over it, and the mark sits at the fill's end and swaps
    /// picture as the play nears its end.
    ///
    /// A skin that ships none of them leaves the bar to us. A skin that ships
    /// only some — and the one this was read against blanks its frame and its
    /// marker deliberately — gets the ones it has and nothing invented for the
    /// rest: drawing our own pill behind somebody's fill would put back the
    /// frame they removed.
    #[allow(clippy::too_many_arguments)]
    fn draw_skin_health(
        &self,
        pixmap: &mut Pixmap,
        health: f32,
        x: f32,
        y: f32,
        width: f32,
        presence: f32,
        height: u32,
    ) -> bool {
        use crate::elements::Element;
        let fill = Element::ScoreBarFill;
        // Every piece answers for itself, including by being blank. A skin that
        // ships an empty `scorebar-colour` has removed its fill, and putting
        // ours there instead would be drawing back what it deleted — the same
        // mistake the verdicts and the spinner's ring both had.
        let speaks = [fill, Element::ScoreBarBackground]
            .iter()
            .any(|&piece| self.skin_speaks_for(piece));
        if !speaks {
            return false;
        }
        let alpha = presence.clamp(0.0, 1.0);

        // At the fill's own proportions rather than ours. osu! draws this
        // across the width of the play area, so a skin's strip is long and
        // thin; squeezed into the pill we draw it comes out compressed by four
        // times, which on the skin this was read against turned a line of
        // lettering into a smear. The bar keeps its place and its length and
        // takes its thickness from the picture.
        // Proportioned by whichever piece the skin actually drew: a skin with
        // only a frame has no fill to take the shape from, and one with only a
        // fill has no frame.
        let Some(sprites) = &self.skin.sprites else {
            return false;
        };
        let shape = sprites
            .get(fill)
            .or_else(|| sprites.get(Element::ScoreBarBackground));
        let thickness = match shape {
            // The picture's own height, not one derived from the trimmed
            // length — cutting a bar short must not also make it thinner.
            Some(sprite) => sprite.height() * height as f32 / 768.0,
            // Both pieces blank: the skin wants no bar at all, which is an
            // answer and not a gap.
            None => return true,
        };

        // The frame, at the same size as the fill will be.
        self.blit_bar(
            pixmap,
            Element::ScoreBarBackground,
            x,
            y,
            width,
            thickness,
            1.0,
            alpha,
            height,
        );
        // The fill, cut to the health rather than squashed to it: a bar at half
        // health is half a bar, not a whole bar drawn narrow.
        self.blit_bar(pixmap, fill, x, y, width, thickness, health, alpha, height);

        let mark = crate::elements::Element::ScoreBarMark(crate::elements::Health::of(health));
        if self.skin_speaks_for(mark) {
            self.blit_mark(pixmap, mark, x + width * health, y + thickness / 2.0, thickness, alpha);
        }
        true
    }

    /// One piece of the health bar, stretched along it and cut at `share`.
    #[allow(clippy::too_many_arguments)]
    #[allow(clippy::too_many_arguments)]
    fn blit_bar(
        &self,
        pixmap: &mut Pixmap,
        element: crate::elements::Element,
        x: f32,
        y: f32,
        width: f32,
        thickness: f32,
        share: f32,
        alpha: f32,
        layout_height: u32,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, _)) = sprites.coloured(element, 0) else {
            return;
        };
        let share = share.clamp(0.0, 1.0);
        if share <= 0.0 || alpha <= 0.0 {
            return;
        }
        // At the picture's own scale, and *cut* rather than squashed. Squashing
        // it to the bar's length was wrong twice over: osu! draws this at its
        // natural size and clips it to the health, and shortening a squashed
        // bar only compresses whatever is drawn on it — which on a skin that
        // writes a sentence along its own health bar is very visible.
        let scale = layout_height as f32 / 768.0;
        let visible = (width * share).ceil().max(1.0) as u32;
        let Some(mut strip) = Pixmap::new(visible, thickness.ceil().max(1.0) as u32) else {
            return;
        };
        strip.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            Transform::from_scale(scale, scale),
            None,
        );
        pixmap.draw_pixmap(
            x as i32,
            y as i32,
            strip.as_ref(),
            &PixmapPaint {
                opacity: alpha,
                ..Default::default()
            },
            Transform::identity(),
            None,
        );
    }

    /// The mark at the end of the fill, centred on it and sized by the bar.
    fn blit_mark(
        &self,
        pixmap: &mut Pixmap,
        element: crate::elements::Element,
        x: f32,
        y: f32,
        thickness: f32,
        alpha: f32,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, _)) = sprites.coloured(element, 0) else {
            return;
        };
        // Twice the bar's thickness, which is about how far it stands proud of
        // it in the game.
        let scale = (thickness * 2.0) / art.height() as f32;
        let (w, h) = (art.width() as f32 * scale, art.height() as f32 * scale);
        pixmap.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &PixmapPaint {
                opacity: alpha,
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            Transform::from_translate(x - w / 2.0, y - h / 2.0).pre_scale(scale, scale),
            None,
        );
    }

    /// Recent timing errors, as osu!'s hit-error bar.
    ///
    /// A tick per recent hit, placed by how early or late it was, over three
    /// bands standing for the 300, 100 and 50 windows. It is the one part of
    /// the interface that says *how* a player is playing rather than how well:
    /// a cloud sitting left of centre is somebody rushing, and no total shows
    /// that.
    /// How much a spinner owns the bottom of the frame, 0 to 1.
    ///
    /// Faded rather than switched. A bar that vanishes and a number that appears
    /// on the same frame reads as a glitch; a quarter of a second of one giving
    /// way to the other reads as the display changing its mind, which is what it
    /// is doing.
    fn spinner_grip(&self, time_ms: f64) -> f32 {
        let mut grip: f32 = 0.0;
        for object in &self.state.timeline().objects {
            if !object.is_spinner() {
                continue;
            }
            // Open a little before it starts and shut a little after it ends, so
            // the swap has happened by the time the ring appears and is undone
            // by the time the next note is due.
            let opening = ((time_ms - (object.start_ms - SPIN_SWAP_MS)) / SPIN_SWAP_MS) as f32;
            let closing = (((object.end_ms + SPIN_SWAP_MS) - time_ms) / SPIN_SWAP_MS) as f32;
            grip = grip.max(opening.clamp(0.0, 1.0).min(closing.clamp(0.0, 1.0)));
        }
        grip
    }

    /// The spinner's speed, where the error bar usually is.
    ///
    /// osu! draws this as a `spinner-rpm` label with the figure beside it, and
    /// a skin that ships that label blank has said it wants no read-out — so
    /// ours goes too. Writing "RPM" in our own letters over a skin that
    /// deleted the label would be the same mistake the verdicts had.
    fn draw_spin_readout(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        if self
            .skin
            .sprites
            .as_ref()
            .is_some_and(|s| s.silenced(crate::elements::Element::SpinnerRpm))
        {
            return;
        }
        let Some(font) = self.skin.font.as_ref() else {
            return;
        };
        // Whichever spinner is nearest to now: at the seam between two, the one
        // being read should be the one on screen.
        let Some(object) = self
            .state
            .timeline()
            .objects
            .iter()
            .filter(|object| object.is_spinner())
            .min_by(|a, b| {
                let near = |o: &TimedObject| {
                    if time_ms < o.start_ms {
                        o.start_ms - time_ms
                    } else if time_ms > o.end_ms {
                        time_ms - o.end_ms
                    } else {
                        0.0
                    }
                };
                near(a).total_cmp(&near(b))
            })
        else {
            return;
        };
        let rpm = dossier_sim::spinner_rpm(
            self.state.cursor_track(),
            object.start_ms,
            time_ms.clamp(object.start_ms, object.end_ms),
        );
        let height = f64::from(layout.height);
        let size = (height * SPIN_READOUT_SIZE) as f32;
        let baseline = (height * 0.962) as f32;
        let figure = format!("{rpm:.0}");

        // The skin's own label with the figure beside it, which is how osu!
        // states this: `spinner-rpm` is a picture of the word, not a caption we
        // are meant to letter ourselves. Drawn as a pair and centred as one, so
        // a skin with a wide label does not push its figure off the middle.
        if self.skin_speaks_for(crate::elements::Element::SpinnerRpm) {
            let sprite = self
                .skin
                .sprites
                .as_ref()
                .and_then(|s| s.coloured(crate::elements::Element::SpinnerRpm, 0));
            if let Some((art, per)) = sprite {
                // Against the interface's own 768-unit frame, like every other
                // element that is not on the playfield.
                let scale = layout.height as f32 / 768.0 / per;
                let label_w = art.width() as f32 * scale;
                let label_h = art.height() as f32 * scale;
                let gap = size * 0.35;
                let figure_w = self.hud_width(font, &figure, size);
                let left = layout.width as f32 * 0.5 - (label_w + gap + figure_w) / 2.0;

                // The label sits on the same baseline as the figure rather than
                // on its own centre: they are one line, and a picture of a word
                // hung from the middle reads as two.
                pixmap.draw_pixmap(
                    0,
                    0,
                    art.as_ref(),
                    &PixmapPaint {
                        opacity: presence.clamp(0.0, 1.0),
                        quality: tiny_skia::FilterQuality::Bilinear,
                        ..Default::default()
                    },
                    Transform::from_translate(left, baseline - label_h).pre_scale(scale, scale),
                    None,
                );
                let at = left + label_w + gap;
                if !self.draw_hud_text(pixmap, &figure, at, baseline, size, Align::Left, presence) {
                    font.draw(
                        pixmap,
                        Label {
                            text: &figure,
                            x: at,
                            y: baseline,
                            size,
                            colour: with_alpha(self.skin.spinner, presence),
                            align: Align::Left,
                        },
                    );
                }
                return;
            }
        }

        // No label of its own, so ours — and ours has to say what the number
        // is, since there is no picture to say it.
        font.draw(
            pixmap,
            Label {
                text: &format!("RPM: {rpm:.0}"),
                x: layout.width as f32 * 0.5,
                y: baseline,
                size,
                colour: with_alpha(self.skin.spinner, presence),
                align: Align::Centre,
            },
        );
    }

    fn draw_error_bar(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        let Some(judge) = self.state.judge() else {
            return;
        };
        let difficulty = self.state.difficulty();
        let (w300, w100, w50) = (
            difficulty.hit_window_300(),
            difficulty.hit_window_100(),
            difficulty.hit_window_50(),
        );
        if w50 <= 0.0 {
            return;
        }

        let height = f64::from(layout.height);
        let full_width = (layout.width as f64 * 0.22) as f32;
        let centre_x = layout.width as f32 * 0.5;
        let y = (height * 0.955) as f32;
        let band = (height * 0.006).max(2.0) as f32;
        let span = w50 * ERROR_BAR_SPAN;
        let half = |window: f64| (window / span) as f32 * full_width * 0.5;

        // The windows themselves, widest first so the narrow ones sit on top.
        for (window, colour) in [
            (w50, self.skin.verdict_50),
            (w100, self.skin.verdict_100),
            (w300, self.skin.verdict_300),
        ] {
            let w = half(window);
            draw_bar(
                pixmap,
                centre_x - w,
                y,
                w * 2.0,
                band,
                with_alpha(colour, 0.30 * presence),
            );
        }

        // The last few hits, the most recent brightest.
        let mut recent: Vec<(f64, f64)> = judge
            .errors_ms()
            .filter(|&(at, _)| at <= time_ms)
            .collect();
        // Most recent first, so the brightest tick is the newest.
        recent.reverse();
        recent.truncate(ERROR_BAR_TICKS);
        let tick_w = (height * 0.0035).max(1.0) as f32;
        for (i, (_, error)) in recent.iter().enumerate() {
            let age = i as f32 / ERROR_BAR_TICKS as f32;
            let offset = (*error / span).clamp(-1.0, 1.0) as f32 * full_width * 0.5;
            let colour = if error.abs() < w300 {
                self.skin.verdict_300
            } else if error.abs() < w100 {
                self.skin.verdict_100
            } else {
                self.skin.verdict_50
            };
            draw_bar(
                pixmap,
                centre_x + offset - tick_w * 0.5,
                y - band * 1.6,
                tick_w,
                band * 4.2,
                with_alpha(colour, (1.0 - age) * 0.9 * presence),
            );
        }

        // Dead centre, so early and late read at a glance.
        let centre_top = y - band * 2.4;
        draw_bar(
            pixmap,
            centre_x - tick_w * 0.5,
            centre_top,
            tick_w,
            band * 5.8,
            with_alpha(self.skin.hud, 0.75 * presence),
        );

        // And the figure that meter is a picture of, sitting on top of its own
        // centre line. The ticks say where the errors fell; this says how far
        // apart they were, which is the one number a viewer wants from the bar
        // and cannot read off it.
        if let Some(rate) = judge.unstable_rate(time_ms).filter(|_| self.skin.unstable_rate) {
            let size = (height * ERROR_BAR_UR_SIZE) as f32;
            let baseline = centre_top - size * ERROR_BAR_UR_GAP;
            // The figure alone. What it measures is said by the meter it sits
            // on, and a caption on a number that is already over its own scale
            // is a word the reader has to skip.
            let text = format!("{rate:.0}");
            if !self.draw_hud_text(
                pixmap,
                &text,
                centre_x,
                baseline,
                size,
                Align::Centre,
                0.75 * presence,
            ) {
                if let Some(font) = self.skin.font.as_ref() {
                    font.draw(
                        pixmap,
                        Label {
                            text: &text,
                            x: centre_x,
                            y: baseline,
                            size,
                            colour: with_alpha(self.skin.hud, 0.75 * presence),
                            align: Align::Centre,
                        },
                    );
                }
            }
        }
    }

}
