//! The standings down the left of the frame, climbing to the map's best score.
//!
//! Read upwards: the worst kept score at the top, the leader at the bottom. A
//! board with the leader on top is a table; one that climbs to them is a story,
//! and the player's row rising through it is the only thing on screen that
//! changes place. Every position is worked out from the score curve the engine
//! already computes, so a frame stands alone and the whole reel can be drawn in
//! parallel.
//!
//! `draw_leaderboard` is `pub(super)` for the overlay pass to call; everything
//! else — the row, and the `ScoreCurve` that feeds the engine's track to the
//! board — is private to it.

use super::format::{compact, name_size};
use super::paint::rounded_rect;
use super::*;

use tiny_skia::{Color, FillRule, Paint, Pixmap, Transform};

use crate::layout::Layout;
use crate::skin::{darken, lighten, with_alpha};
use crate::text::{Align, Label};

impl Scene<'_> {
    /// The standings, down the left, climbing to the best score on the map.
    ///
    /// Read upwards: the worst kept score at the top, the leader at the bottom.
    /// A board with the leader on top is a table; one that climbs to them is a
    /// story, and the player's row rising through it is the only thing on screen
    /// that changes place.
    ///
    /// Drawn from the score the engine is already computing, so the row moves at
    /// the moment it actually passes somebody — and the move is worked out from
    /// the score curve rather than from the frame before, because a frame here
    /// has to stand alone or they cannot be drawn in parallel.
    pub(super) fn draw_leaderboard(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout) {
        let (Some(font), false) = (&self.skin.font, self.leaderboard.is_empty()) else {
            return;
        };
        let Some(track) = self.state.score_track() else {
            return;
        };
        let rows = self
            .leaderboard
            .standings_at(&ScoreCurve(track), time_ms, BOARD_ROWS);

        let height = f64::from(layout.height);
        let size = (height * BOARD_TEXT) as f32;
        let step = (height * BOARD_STEP) as f32;
        let left = (height * BOARD_LEFT) as f32;
        let width = (height * BOARD_WIDTH) as f32;
        let card_height = step * BOARD_CARD_FILL;
        // Anchored across the middle of the left edge, which is where the
        // playfield is emptiest whatever the aspect ratio. The block is as tall
        // as the window is long, whatever places happen to be in it — sizing it
        // from the places themselves put the leader three thousand pixels below
        // the frame on a map forty people had played.
        let drawn = BOARD_ROWS as f32;
        let top = pixmap.height() as f32 / 2.0 + (drawn / 2.0 - 1.0) * step;

        for row in &rows {
            let eased = {
                // Ease out, so it leaves briskly and settles rather than
                // arriving at speed.
                let t = row.moving.clamp(0.0, 1.0);
                1.0 - (1.0 - t) * (1.0 - t) * (1.0 - t)
            };
            // Slot zero is the worst score kept and it is drawn at the *bottom*,
            // so the block reads best-first downwards and the player climbs it
            // from below. Drawn the other way round — worst at the top,
            // descending to the leader — was tried and looked wrong: the eye
            // starts at the top of a list, and starting it on the row that
            // matters least buries the one that matters most.
            let slot = row.from_slot + (row.slot - row.from_slot) * eased;
            let y = top - slot * step + size * 1.15;
            // Three states, three shapes. A row on its way out shrinks and fades
            // as it travels into the row that overtook it; one arriving at the
            // top grows into place from nothing; one merely changing slot stays
            // whole and slides. Sliding all three would make the board look like
            // a list being sorted, which is what it is and not what it is *for*.
            let t = row.moving.clamp(0.0, 1.0);
            // A leaver has to still be *there* while it travels, or it is not
            // flying into anything — it is a row dissolving where it stood. So it
            // holds its size and its colour for most of the trip and gives them
            // up at the end, on top of the row that took its place. Fading with
            // the same ease-out that carries it made it invisible before it
            // arrived, which is why the first attempt looked like no change at
            // all: the movement was right and nobody could see it.
            let late = 1.0 - t * t * t;
            let settling = if row.leaving {
                late
            } else if row.entering {
                eased
            } else if (row.slot - row.from_slot).abs() < f32::EPSILON {
                1.0
            } else {
                eased
            };
            let presence = if row.leaving || row.entering {
                settling
            } else {
                0.45 + 0.55 * settling
            };
            let shrink = if row.leaving || row.entering {
                BOARD_GROW + (1.0 - BOARD_GROW) * settling
            } else {
                0.94 + 0.06 * settling
            };

            // The card shrinks with the text. Scaling only the letters is what
            // made a collapsing row read as a fading one — the panel stayed its
            // full size underneath and nothing appeared to shrink at all.
            let card_w = width * shrink;
            let card_h = card_height * shrink;
            self.draw_board_row(
                pixmap,
                font,
                row,
                left + (width - card_w) / 2.0,
                y - (card_height - card_h) / 2.0,
                card_w,
                card_h,
                size * shrink,
                presence,
            );
        }
    }

    /// One card: the cover behind it, the avatar, the place, and the numbers.
    #[allow(clippy::too_many_arguments)]
    fn draw_board_row(
        &self,
        pixmap: &mut Pixmap,
        font: &crate::text::Font,
        row: &crate::leaderboard::Row,
        left: f32,
        baseline: f32,
        width: f32,
        card_height: f32,
        size: f32,
        presence: f32,
    ) {
        let top = baseline - size * 1.15;
        let Some(card) = rounded_rect(left, top, width, card_height, card_height * BOARD_RADIUS)
        else {
            return;
        };

        // The cover first, clipped to the card, then two washes over it: heavy on
        // the left where the avatar and the name sit, lighter on the right. One
        // flat dim would either drown the picture or lose the text; the point of
        // a cover is to be seen behind the half of the row that has fewer words
        // in it.
        let mut paint = Paint {
            anti_alias: true,
            ..Default::default()
        };
        let has_cover = row
            .entry
            .cover
            .as_deref()
            .is_some_and(|p| self.pictures.contains_key(p));
        if let Some(cover) = row
            .entry
            .cover
            .as_deref()
            .and_then(|p| self.pictures.get(p))
        {
            let scale = (width / cover.width() as f32).max(card_height / cover.height() as f32);
            let shader = tiny_skia::Pattern::new(
                cover.as_ref(),
                tiny_skia::SpreadMode::Pad,
                tiny_skia::FilterQuality::Bilinear,
                presence,
                Transform::from_translate(left, top).pre_scale(scale, scale),
            );
            paint.shader = shader;
            pixmap.fill_path(
                &card,
                &paint,
                FillRule::Winding,
                Transform::identity(),
                None,
            );
            paint.shader = Shader::SolidColor(Color::BLACK);
        }

        let base = if row.is_player {
            lighten(self.skin.background, BOARD_CARD_LIFT)
        } else {
            self.skin.background
        };
        // A gradient across the card rather than two flat bands.
        //
        // Bands were tried and are wrong twice over. They leave a seam where
        // they meet — one card reads as two — and the heavy one has to be heavy
        // enough for text over the *worst* cover, which on the left of the card
        // meant ninety per cent of near-black: the cover simply was not there,
        // and half of every row was a black rectangle. A ramp puts the weight
        // where the words are and lets go of it where they stop, so the picture
        // survives the half of the row that has fewer of them.
        let (heavy, light) = if has_cover {
            (BOARD_DARK_LEFT_COVER, BOARD_DARK_RIGHT_COVER)
        } else {
            (BOARD_DARK_LEFT, BOARD_DARK_RIGHT)
        };
        if let Some(shade) = tiny_skia::LinearGradient::new(
            tiny_skia::Point::from_xy(left, top),
            tiny_skia::Point::from_xy(left + width, top),
            vec![
                tiny_skia::GradientStop::new(0.0, with_alpha(base, heavy * presence)),
                tiny_skia::GradientStop::new(
                    BOARD_DARK_SPLIT,
                    with_alpha(base, heavy * BOARD_DARK_KNEE * presence),
                ),
                tiny_skia::GradientStop::new(1.0, with_alpha(base, light * presence)),
            ],
            tiny_skia::SpreadMode::Pad,
            Transform::identity(),
        ) {
            let wash = Paint {
                shader: shade,
                anti_alias: true,
                ..Default::default()
            };
            pixmap.fill_path(&card, &wash, FillRule::Winding, Transform::identity(), None);
        }

        let colour = if row.is_player {
            self.skin.hud
        } else {
            darken(self.skin.hud, BOARD_RIVAL_DIM)
        };

        // The avatar, square and inside a ring that glows a little. Red because
        // it is the house colour and because on a board of grey rows one warm
        // edge is enough to find your own line without reading it.
        let face = card_height * BOARD_FACE;
        let face_x = left + card_height * 0.16;
        let face_y = top + (card_height - face) / 2.0;
        if let Some(avatar) = row
            .entry
            .avatar
            .as_deref()
            .and_then(|p| self.pictures.get(p))
        {
            if let Some(clip) = rounded_rect(face_x, face_y, face, face, face * 0.28) {
                let scale = face / avatar.width().max(1) as f32;
                let mut art = Paint {
                    anti_alias: true,
                    ..Default::default()
                };
                art.shader = tiny_skia::Pattern::new(
                    avatar.as_ref(),
                    tiny_skia::SpreadMode::Pad,
                    tiny_skia::FilterQuality::Bilinear,
                    presence,
                    Transform::from_translate(face_x, face_y).pre_scale(scale, scale),
                );
                pixmap.fill_path(&clip, &art, FillRule::Winding, Transform::identity(), None);
            }
        }
        // The ring is drawn whether or not there is a face behind it: an empty
        // frame still says which row is which, where a missing one would leave
        // the layout jumping between players who have an avatar and players who
        // do not.
        for (grow, alpha) in [(BOARD_GLOW, 0.22), (0.0, 0.95)] {
            let Some(ring) = rounded_rect(
                face_x - face * grow,
                face_y - face * grow,
                face * (1.0 + grow * 2.0),
                face * (1.0 + grow * 2.0),
                face * 0.28,
            ) else {
                continue;
            };
            let mut edge = Paint {
                anti_alias: true,
                ..Default::default()
            };
            edge.set_color(with_alpha(self.skin.verdict_miss, alpha * presence));
            pixmap.stroke_path(
                &ring,
                &edge,
                &Stroke {
                    width: face * BOARD_RING,
                    ..Default::default()
                },
                Transform::identity(),
                None,
            );
        }

        // The place, large and lit, in a column of its own at the right edge with
        // the text stopping short of it.
        //
        // It was a dim watermark, on the reasoning that the order already says
        // the place so the number is optional. That reasoning holds for a
        // scoreboard you are reading and not for one you are watching: a row goes
        // past in a second and a half and the number is the only part of it that
        // says *where in the field* this is happening. Lit, it is the first thing
        // the eye finds on the card; dim, it was the last.
        //
        // The first three carry the bot's own gold, silver and bronze, so a
        // podium here and a podium on a leaderboard card are the same three
        // colours rather than two people's separate idea of gold.
        let rank_column = card_height * BOARD_RANK_COLUMN;
        let rank_colour = match row.place {
            0..=2 => self.skin.podium[row.place],
            _ => lighten(colour, BOARD_RANK_LIFT),
        };
        font.draw(
            pixmap,
            Label {
                text: &format!("{}", row.place + 1),
                x: left + width - card_height * 0.18,
                y: baseline + size * 0.62,
                size: size * 1.75,
                colour: with_alpha(rank_colour, 0.95 * presence),
                align: Align::Right,
            },
        );

        let text_x = face_x + face + card_height * 0.2;
        let text_room = left + width - rank_column - text_x;
        font.draw(
            pixmap,
            Label {
                text: &row.entry.name,
                x: text_x,
                y: baseline,
                size: name_size(&row.entry.name, font, size),
                colour: with_alpha(colour, 0.95 * presence),
                align: Align::Left,
            },
        );
        let mut under = compact(row.entry.score);
        if let Some(accuracy) = row.entry.accuracy {
            under.push_str(&format!("  {accuracy:.2}%"));
        }
        if !row.entry.mods.is_empty() {
            under.push_str(&format!("  {}", row.entry.mods));
        }
        // Shrunk to fit rather than allowed past the card. A ScoreV1 total with
        // an accuracy and mods after it is the widest line the board ever draws,
        // and sizing for the average left it hanging into the playfield.
        let mut under_size = size * 0.78;
        let measured = font.width(&under, under_size);
        if measured > text_room && measured > 0.0 {
            under_size *= text_room / measured;
        }
        font.draw(
            pixmap,
            Label {
                text: &under,
                x: text_x,
                y: baseline + size * 1.05,
                size: under_size,
                colour: with_alpha(darken(colour, 0.22), 0.9 * presence),
                align: Align::Left,
            },
        );
    }
}

/// A rectangle with its corners taken off.
///
/// tiny-skia has no rounded rectangle, and a scoreboard of square cards over a
/// round playfield looks like a debug overlay — which is what this renderer spent
/// its first month looking like.
/// The engine's score track, as the scoreboard's `ScoreAt`.
///
/// A newtype rather than an `impl` on `ScoreTrack` itself, so the trait stays a
/// statement about what a scoreboard needs rather than a method the simulator has
/// to carry for the renderer's benefit.
struct ScoreCurve<'a>(&'a dossier_sim::ScoreTrack);

impl crate::leaderboard::ScoreAt for ScoreCurve<'_> {
    fn at(&self, time_ms: f64) -> u64 {
        self.0.at(time_ms)
    }

    fn reached(&self, score: u64) -> f64 {
        self.0.reached(score)
    }
}
