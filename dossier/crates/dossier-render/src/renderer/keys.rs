//! The two key counters and their tap trail, low on the right of the frame.
//!
//! osu! shows four buttons; this shows the two that are almost always the live
//! ones, and folds a mouse press into the keyboard box beside it so every press
//! is counted once. The count and the depth of a box are read from a table built
//! at the start — `KeyTrack` — so a frame answers "how many presses by now" with
//! a binary search rather than a walk, and answers it from the instant alone,
//! which is what lets every frame be drawn in parallel.
//!
//! `KeyTrack` and its `build` are `pub(super)`: the `Scene` holds one as a field
//! and builds it once when the scene is made. `draw_keys` is `pub(super)` for the
//! overlay pass; `draw_key_trail` is its own.

use super::*;

use tiny_skia::{Pixmap, Transform};

use crate::elements::Element;
use crate::layout::Layout;
use crate::skin::{blend, with_alpha};
use crate::text::{Align, Label};

/// Cubic ease-out: fast away from zero, settling toward one.
///
/// The shape a key has. A press is an impact and its motion belongs at the
/// start; linear motion on something this small reads as a slide.
fn ease_out(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    1.0 - (1.0 - x).powi(3)
}

/// The two buttons the overlay draws a counter for.
///
/// osu! shows four — K1, K2, M1, M2 — and two of them are almost always zero.
/// Measured on a real replay: 719, 695, 41, 0. Two empty plates every frame is
/// two plates of nothing, and what the element is *for* is showing how the
/// player is holding the map, which the two live ones say on their own.
///
/// A press made with the mouse falls into the same box as the keyboard button
/// beside it rather than disappearing. The label is then not literally true for
/// a player who drags with the mouse — but "K1 0, K2 0" all game would be worse
/// than a label that is approximate, and it keeps every press counted once.
const KEY_NAMES: [&str; 2] = ["K1", "K2"];

/// When each button was held, and for how long.
///
/// A table built once rather than a walk per frame. A frame has to answer *how
/// many times has this been pressed by now*, which is a walk over thirty
/// thousand input samples asked of every one of a hundred thousand frames — and
/// the answer never changes, so it is a binary search over a table built at the
/// start.
///
/// That is not only about speed. Every frame must be drawable without its
/// predecessors or they cannot be drawn in parallel, and a counter that
/// incremented as the render walked forwards would be exactly the kind of state
/// that rules out.
///
/// The reading of the key bitmask — which is where the subtlety is — belongs to
/// [`dossier_sim::CursorTrack::holds`], because Exhibit reads the same presses
/// to find where the tapping is hardest and two copies of that rule would be
/// one copy and a future bug.
#[derive(Debug, Default)]
pub(super) struct KeyTrack {
    /// `(pressed_at, released_at)` per button, in time order.
    holds: [Vec<(f64, f64)>; 2],
}

impl KeyTrack {
    /// How far down this button is at `time_ms`, from 0 to 1.
    ///
    /// Not the bare "is it held": a box that switched between two states on one
    /// frame flickered through a stream, because a tap is shorter than the gap
    /// between two frames at 60fps and half of them landed between samples.
    /// Eased, the same taps read as taps.
    ///
    /// Worked out from the press table alone, so it is still a function of the
    /// instant and nothing before it — an animation that accumulated frame by
    /// frame is exactly the state that would stop frames being drawn in
    /// parallel.
    ///
    /// A press shorter than the fall never reaches the bottom, and the release
    /// then starts from wherever it got to. Without that a fast stream would
    /// pump the box to full depth on every tap, which is louder than the tap.
    fn pressed(&self, key: usize, time_ms: f64, rate: f64) -> f32 {
        let (down_ms, up_ms) = (KEYS_PRESS_DOWN_MS * rate, KEYS_PRESS_UP_MS * rate);
        let holds = &self.holds[key];
        let index = holds.partition_point(|(from, _)| *from <= time_ms);
        let Some(&(down, up)) = index.checked_sub(1).and_then(|i| holds.get(i)) else {
            return 0.0;
        };
        let fell = |elapsed: f64, over: f64| ((elapsed / over.max(1e-6)).clamp(0.0, 1.0)) as f32;
        if time_ms < up {
            // Going down: quick, and easing out so it settles rather than stops.
            return ease_out(fell(time_ms - down, down_ms));
        }
        let reached = ease_out(fell(up - down, down_ms));
        reached * (1.0 - ease_out(fell(time_ms - up, up_ms)))
    }

    pub(super) fn build(cursor: &dossier_sim::CursorTrack) -> Self {
        Self {
            holds: cursor.holds(),
        }
    }

    /// How many times this button had gone down by `time_ms`.
    fn count(&self, key: usize, time_ms: f64) -> usize {
        self.holds[key].partition_point(|(from, _)| *from <= time_ms)
    }
}


/// How far in from the right edge the key column sits, as a share of the width.
const KEYS_INSET: f64 = 0.018;
/// One button's box, as a share of the frame height.
const KEYS_BOX: f64 = 0.052;
/// How much wider than tall a box is.
///
/// A tap counter reaches four figures on a marathon and the box has to hold
/// them without the type shrinking away — square, "1234" filled the plate edge
/// to edge and read as a smear at a glance.
const KEYS_WIDTH: f32 = 1.35;
/// Gap between boxes, as a share of one box.
const KEYS_GAP: f32 = 0.18;
/// How long a stretch of tapping the trail shows, in milliseconds of watching.
///
/// A second and a bit. Two seconds held more of the play and moved at a crawl:
/// the marks barely travelled between frames, which reads as a static pattern
/// rather than as tapping happening. Shorter is faster over the same reach, and
/// faster is what makes the trail look like an instrument.
///
/// The floor on it is legibility at speed: a 200 BPM stream is about thirteen
/// presses a second, so this window holds seventeen of them, which is a pattern
/// the eye can still resolve.
const KEYS_TRAIL_MS: f64 = 1_300.0;

/// How far the trail reaches left of the boxes, as a share of the frame width.
const KEYS_TRAIL_REACH: f64 = 0.135;

/// The shortest a mark may be drawn, as a share of the trail's reach.
///
/// A tap is twenty-odd milliseconds, which over any window worth showing is a
/// hairline — literally one pixel at 1280 wide. Drawn at its true length the
/// trail was a row of scratches; given a floor, each press is a block with a
/// shape, and the shape is the whole point. The length still grows with a long
/// hold, so a dragged slider is visibly one bar and not a tap.
const KEYS_MARK_MIN: f32 = 0.03;

/// How tall a mark is against its box.
const KEYS_MARK_HEIGHT: f32 = 0.6;

/// How far a box shrinks while its button is down.
///
/// Small. The counter jumping is what says a press happened; this is what says
/// it is still happening, and a box that visibly leapt about would pull the eye
/// off the play every time somebody tapped.
const KEYS_PRESS_SHRINK: f32 = 0.14;

/// How long a box takes to go down, and to come back up, in milliseconds of
/// watching.
///
/// Down fast and up slower. A press that eased in would read as late — the
/// sound and the note have already happened — while a release that snapped back
/// made a stream look like a strobe. Coming up over twice the time turns the
/// same taps into something the eye can follow.
const KEYS_PRESS_DOWN_MS: f64 = 45.0;
const KEYS_PRESS_UP_MS: f64 = 110.0;

impl Scene<'_> {
    /// The stretch of tapping behind each counter, running off to the left.
    ///
    /// The counter says how much; this says *how*. A stream alternates in even
    /// pairs, a doubletap comes in twos with a gap, a dragged slider is one long
    /// block, and somebody struggling taps unevenly — none of which a number
    /// climbing by one can show, and all of which are the whole reason to watch
    /// somebody else's replay.
    ///
    /// Time runs right to left, newest against the box, so the marks flow away
    /// the way the play has just gone. Older ones fade out rather than stopping
    /// at a hard edge, which would read as a wall the taps are hitting.
    fn draw_key_trail(
        &self,
        pixmap: &mut Pixmap,
        key: usize,
        time_ms: f64,
        layout: &Layout,
        presence: f32,
        place: (f32, f32, f32),
    ) {
        let (right, top, height) = place;
        let reach = (f64::from(layout.width) * KEYS_TRAIL_REACH) as f32;
        let rate = self.state.playback_rate().max(0.001);
        // The window is map time, so under a rate mod it covers more of the map
        // — which is right: two seconds of *watching* is what the eye is given,
        // whatever the map is doing underneath.
        let window = KEYS_TRAIL_MS * rate;
        let from = time_ms - window;

        let bar = height * KEYS_MARK_HEIGHT;
        let bar_top = top + (height - bar) / 2.0;
        let x_of = |at: f64| right - ((time_ms - at) / window) as f32 * reach;

        for &(down, up) in self.keys.holds[key]
            .iter()
            .rev()
            .take_while(|(_, up)| *up >= from)
        {
            let (a, b) = (down.max(from), up.min(time_ms));
            if b <= a {
                continue;
            }
            let (left, width) = (x_of(a), (x_of(b) - x_of(a)).max(1.0));
            // A tap is an instant and would be a hairline; given a floor it
            // reads as a block. The floor is a share of the reach rather than a
            // number of milliseconds because what is at stake is whether it can
            // be seen, and that is a question about pixels.
            let width = width.max(reach * KEYS_MARK_MIN);
            let Some(mark) = rounded_rect(left, bar_top, width, bar, bar * 0.35) else {
                continue;
            };
            // Fading with age, so the trail thins into the frame instead of
            // ending at a line.
            let age = ((time_ms - b) / window).clamp(0.0, 1.0) as f32;
            let mut paint = Paint {
                anti_alias: true,
                ..Default::default()
            };
            paint.set_color(with_alpha(
                self.skin.verdict_miss,
                0.75 * (1.0 - age) * presence,
            ));
            pixmap.fill_path(&mark, &paint, FillRule::Winding, Transform::identity(), None);
        }
    }

    /// The button counters down the right edge.
    ///
    /// osu!'s key overlay, which is the one part of its HUD that says something
    /// about the *player* rather than about the play: how they are holding the
    /// map — alternating, single-tapping, dragging with the mouse — is legible
    /// here and nowhere else on the screen.
    ///
    /// The right edge because that is where osu! puts it and because it is the
    /// only free side: the scoreboard has the left, and the score and accuracy
    /// have the top.
    pub(super) fn draw_keys(&self, pixmap: &mut Pixmap, time_ms: f64, layout: &Layout, presence: f32) {
        if presence <= 0.01 {
            return;
        }
        if self.skin_speaks_for(Element::InputOverlayKey) {
            self.draw_skin_keys(pixmap, time_ms, layout, presence);
            return;
        }
        let Some(font) = &self.skin.font else {
            return;
        };
        let (width, height) = (f64::from(layout.width), f64::from(layout.height));
        let box_side = (height * KEYS_BOX) as f32;
        let box_wide = box_side * KEYS_WIDTH;
        let step = box_side * (1.0 + KEYS_GAP);
        let right = (width * (1.0 - KEYS_INSET)) as f32;
        // Centred on the frame, so the column is a fixed landmark rather than
        // something that moves with whatever is above or below it.
        let top = (height as f32 - (step * 4.0 - box_side * KEYS_GAP)) / 2.0;

        let rate = self.state.playback_rate().max(0.001);
        for (index, name) in KEY_NAMES.iter().enumerate() {
            // How far down, not whether down: every part of the box follows the
            // same number, so the shrink, the fill and the border move together
            // instead of one snapping while the others slide.
            let down = self.keys.pressed(index, time_ms, rate);
            self.draw_key_trail(pixmap, index, time_ms, layout, presence, {
                let y = top + step * index as f32;
                (right - box_wide, y, box_side)
            });
            let count = self.keys.count(index, time_ms);
            let shrink = KEYS_PRESS_SHRINK * down;
            let side = box_side * (1.0 - shrink);
            let wide = box_wide * (1.0 - shrink);
            let x = right - box_wide + (box_wide - wide) / 2.0;
            let y = top + step * index as f32 + (box_side - side) / 2.0;

            let Some(card) = rounded_rect(x, y, wide, side, side * 0.3) else {
                continue;
            };
            let mut fill = Paint {
                anti_alias: true,
                ..Default::default()
            };
            // Held, the box fills with the same red the engine uses for
            // everything that is happening *now*; loose, it is a dark plate
            // that keeps the column readable over a bright background.
            // The plate crossfades to the engine's red rather than switching to
            // it, which is what makes a fast stream read as a pulse instead of
            // a strobe.
            let body = with_alpha(
                blend(self.skin.background, self.skin.verdict_miss, down),
                (0.55 + 0.30 * down) * presence,
            );
            let ink = self.skin.hud;
            fill.set_color(body);
            pixmap.fill_path(&card, &fill, FillRule::Winding, Transform::identity(), None);

            let mut edge = Paint {
                anti_alias: true,
                ..Default::default()
            };
            edge.set_color(with_alpha(ink, (0.35 + 0.55 * down) * presence));
            pixmap.stroke_path(
                &card,
                &edge,
                &Stroke {
                    width: (side * 0.05).max(1.0),
                    ..Default::default()
                },
                Transform::identity(),
                None,
            );

            // The name above the count and much smaller: which button this is
            // never changes, and the number does.
            font.draw(
                pixmap,
                Label {
                    text: name,
                    x: x + wide / 2.0,
                    y: y + side * 0.34,
                    size: side * 0.26,
                    colour: with_alpha(ink, 0.7 * presence),
                    align: Align::Centre,
                },
            );
            // The count in the skin's own figures when it has them. The label
            // above it stays ours: `K1` is lettering, and a skin's HUD set is
            // digits and four signs — there is no `K` in it to borrow.
            let count = count.to_string();
            if !self.draw_hud_text(
                pixmap,
                &count,
                x + wide / 2.0,
                y + side * 0.78,
                side * 0.42,
                Align::Centre,
                0.95 * presence,
            ) {
                font.draw(
                    pixmap,
                    Label {
                        text: &count,
                        x: x + wide / 2.0,
                        y: y + side * 0.78,
                        size: side * 0.42,
                        colour: with_alpha(ink, 0.95 * presence),
                        align: Align::Centre,
                    },
                );
            }
        }
    }
}

#[cfg(test)]
mod keys {
    use super::KeyTrack;
    use dossier_replay::{Keys, ReplayFrame};

    fn track(script: &[(i64, u8)]) -> KeyTrack {
        let frames = script
            .iter()
            .map(|&(time_ms, keys)| ReplayFrame {
                time_ms,
                x: 0.0,
                y: 0.0,
                keys: Keys(keys),
            })
            .collect();
        KeyTrack::build(&dossier_sim::CursorTrack::new(frames))
    }

    /// The detail the whole element turns on. osu! sets the mouse bit as well
    /// when a keyboard button goes down, so K1 arrives as `M1 | K1` — and the
    /// two bits read together are one press, not two.
    #[test]
    fn a_keyboard_press_is_one_press_and_not_two() {
        let track = track(&[
            (0, 0),
            (10, Keys::M1 | Keys::K1),
            (20, 0),
            (30, Keys::M1 | Keys::K1),
            (40, 0),
            (50, Keys::M1 | Keys::K1),
            (60, 0),
        ]);
        assert_eq!(track.count(0, 100.0), 3);
    }

    /// …and a press made with the mouse alone lands in the same button rather
    /// than disappearing, which is what keeps a mouse player's counters from
    /// reading zero all game.
    #[test]
    fn a_mouse_press_lands_in_the_same_button() {
        let track = track(&[(0, 0), (10, Keys::M1), (20, 0), (30, Keys::M2), (40, 0)]);
        assert_eq!(track.count(0, 100.0), 1);
        assert_eq!(track.count(1, 100.0), 1);
    }

    /// The counter is what it was at that instant, not what it ends at — a
    /// frame has to be drawable without the frames before it, which is what
    /// lets them be drawn in parallel.
    #[test]
    fn a_count_is_as_of_the_instant_asked_for() {
        let track = track(&[
            (0, 0),
            (100, Keys::K1),
            (150, 0),
            (200, Keys::K1),
            (250, 0),
        ]);
        assert_eq!(track.count(0, 50.0), 0);
        assert_eq!(track.count(0, 120.0), 1);
        assert_eq!(track.count(0, 220.0), 2);
    }

    /// The box follows a number between nought and one, not a switch. A tap is
    /// shorter than the gap between two frames at 60fps, so half of them land
    /// between samples — switched, a stream flickers; eased, the same taps read
    /// as taps.
    #[test]
    fn a_press_goes_down_over_time_rather_than_at_once() {
        let track = track(&[(0, 0), (100, Keys::K1), (400, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);

        assert_eq!(at(99.0), 0.0, "nothing before the press");
        assert!(at(100.0) < 0.05, "the press starts at the top");
        assert!(at(115.0) > at(105.0), "and travels");
        assert!(at(200.0) > 0.99, "arriving well inside the hold");
        assert!(at(390.0) > 0.99, "and staying there");
    }

    /// Coming back up takes longer than going down, and long enough after a
    /// release the box is at rest.
    #[test]
    fn a_release_comes_back_slower_than_the_press_went_down() {
        let track = track(&[(0, 0), (100, Keys::K1), (400, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);
        assert!(at(430.0) > 0.2, "still visibly down a frame or two after");
        assert!(at(520.0) < 0.01, "and at rest well after");

        // The claim stated properly: thirty milliseconds after each edge, the
        // press has travelled further down than the release has travelled back
        // up. Down in 45ms and up in 110ms is what makes that true.
        let gone_down = at(130.0);
        let come_up = 1.0 - at(430.0);
        assert!(
            come_up < gone_down,
            "the release ({come_up:.3}) kept up with the press ({gone_down:.3})"
        );
    }

    /// A tap shorter than the fall never reaches the bottom, and the release
    /// starts from wherever it got to. Without that a fast stream pumps the box
    /// to full depth on every tap, which is louder than the tap.
    #[test]
    fn a_tap_too_short_to_land_starts_back_from_where_it_reached() {
        let track = track(&[(0, 0), (100, Keys::K1), (110, 0)]);
        let at = |t: f64| track.pressed(0, t, 1.0);
        let peak = at(110.0);
        assert!(peak > 0.0 && peak < 0.7, "a 10ms tap reached {peak}");
        assert!(at(140.0) < peak, "and comes back from there");
    }

    /// The animation is in seconds of watching, so a rate mod does not make it
    /// twice as quick: a press should feel the same whatever the map is doing.
    #[test]
    fn the_animation_keeps_its_pace_under_a_rate_mod() {
        let track = track(&[(0, 0), (100, Keys::K1), (900, 0)]);
        let plain = track.pressed(0, 130.0, 1.0);
        // Under DoubleTime the same instant of watching is half again as much
        // map time, so the same point in the animation is further along it.
        let fast = track.pressed(0, 100.0 + 30.0 * 1.5, 1.5);
        assert!((plain - fast).abs() < 1e-6, "{plain} against {fast}");
    }

    /// A button still down when the recording stops was still pressed, and a
    /// finish is exactly where somebody would be looking at the counter.
    #[test]
    fn a_button_still_down_at_the_end_still_counts() {
        let track = track(&[(0, 0), (100, Keys::K2)]);
        assert_eq!(track.count(1, 200.0), 1);
        // …and the box is down at that last instant rather than at rest,
        // which is what the one-millisecond close is for.
        assert!(track.pressed(1, 100.5, 1.0) > 0.0);
    }
}

/// The overlay's own measurements, in the 768-unit space stable states its
/// interface in.
///
/// ```csharp
/// // LegacyKeyCounterDisplay
/// Scale = new Vector2(1.05f, 1);   Rotation = 90;     // the plate
/// X = -1.5f, Y = 7;  Spacing = new Vector2(1.8f);     // the row of keys
/// static readonly Colour4 active_colour_top    = Colour4.FromHex(@"#ffde00");
/// static readonly Colour4 active_colour_bottom = Colour4.FromHex(@"#f8009e");
///
/// // LegacyKeyCounter
/// private const float transition_duration = 160;
/// Height = Width = 46;
/// keyContainer.ScaleTo(0.75f, transition_duration, Easing.Out);
/// keySprite.Colour = ActiveColour;
/// ```
const OVERLAY_KEY: f32 = 46.0;
const OVERLAY_SPACING: f32 = 1.8;
const OVERLAY_PRESSED: f32 = 0.75;
/// How much longer the plate is than the run of keys it holds.
///
/// stable's plate carries four keys — `4*46 + 3*1.8`, against `199 * 1.05` of
/// plate — and we show two, for the reason [`KEY_NAMES`] gives. Kept as the
/// ratio rather than the length so a plate drawn for four keys and used for two
/// keeps its proportions instead of trailing off into empty artwork.
const OVERLAY_SLACK: f32 = 199.0 * 1.05 / (4.0 * OVERLAY_KEY + 3.0 * OVERLAY_SPACING);

impl Scene<'_> {
    /// The key overlay as the skin draws it: a plate stood on its end, a button
    /// per key, and the count on the button.
    ///
    /// Ours is a column of rounded cards with a trail behind each — a good
    /// readout and not this one. When a skin brings the two files osu! draws
    /// this from, they win, the same way they do everywhere else.
    fn draw_skin_keys(
        &self,
        pixmap: &mut Pixmap,
        time_ms: f64,
        layout: &Layout,
        presence: f32,
    ) {
        let key = self.skin_pixels(layout, OVERLAY_KEY);
        let gap = self.skin_pixels(layout, OVERLAY_SPACING);
        let keys = KEY_NAMES.len() as f32;
        let run = key * keys + gap * (keys - 1.0);
        let right = (f64::from(layout.width) * (1.0 - KEYS_INSET)) as f32;
        // Centred on the frame, where ours has always sat. What a skin decides
        // here is what the overlay is made of, not where the render puts it.
        let top = (layout.height as f32 - run) / 2.0;

        // The plate first, standing on its end. Its own file is drawn lying
        // down and the game turns it a quarter turn to stand it up, so the
        // width of the strip is the *height* the skin drew.
        if let Some(plate) = self.plate_width(layout) {
            let length = run * OVERLAY_SLACK;
            self.draw_upright(
                pixmap,
                Element::InputOverlayBackground,
                (right - plate, top - (length - run) / 2.0, plate, length),
                presence,
            );
        }

        let rate = self.state.playback_rate().max(0.001);
        for index in 0..KEY_NAMES.len() {
            let down = self.keys.pressed(index, time_ms, rate);
            // Held, the button shrinks and lights. Both follow the one number,
            // so a fast stream reads as a pulse rather than a strobe.
            let side = key * (1.0 + (OVERLAY_PRESSED - 1.0) * down);
            let centre_x = right - self.plate_width(layout).unwrap_or(key) / 2.0;
            let centre_y = top + (key + gap) * index as f32 + key / 2.0;

            let lit = blend(tiny_skia::Color::WHITE, active_colour(index), down);
            self.draw_key_sprite(
                pixmap,
                (centre_x - side / 2.0, centre_y - side / 2.0),
                side,
                lit,
                presence,
            );

            // The count sits on the button in the skin's own HUD figures —
            // `LegacySpriteText(LegacyFont.ScoreEntry)`, which is the `score-`
            // set. Sized off the button rather than the frame so it stays on
            // it whatever the skin drew.
            let count = self.keys.count(index, time_ms).to_string();
            let text = key * 0.42;
            if !self.draw_hud_text(
                pixmap,
                &count,
                centre_x,
                centre_y + text * 0.5,
                text,
                Align::Centre,
                presence,
            ) {
                if let Some(font) = &self.skin.font {
                    font.draw(
                        pixmap,
                        Label {
                            text: &count,
                            x: centre_x,
                            y: centre_y + text * 0.5,
                            size: text,
                            colour: with_alpha(self.skin.hud, presence),
                            align: Align::Centre,
                        },
                    );
                }
            }
        }
    }

    /// How wide the plate stands, or `None` when the skin brought none.
    fn plate_width(&self, layout: &Layout) -> Option<f32> {
        let sprites = self.skin.sprites.as_ref()?;
        let (art, per) = sprites.coloured(Element::InputOverlayBackground, 0)?;
        Some(self.skin_pixels(layout, art.height() as f32 / per))
    }

    /// A sprite turned a quarter turn and stretched into a box.
    fn draw_upright(
        &self,
        pixmap: &mut Pixmap,
        element: Element,
        (x, y, wide, tall): (f32, f32, f32, f32),
        alpha: f32,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, _)) = sprites.coloured(element, 0) else {
            return;
        };
        if alpha <= 0.0 || wide <= 0.0 || tall <= 0.0 {
            return;
        }
        // Turned about the top-left corner and then walked back into place,
        // which is what `Origin = TopLeft, Rotation = 90` comes to.
        let transform = Transform::from_translate(x + wide, y)
            .pre_rotate(90.0)
            .pre_scale(tall / art.width() as f32, wide / art.height() as f32);
        pixmap.draw_pixmap(
            0,
            0,
            art.as_ref(),
            &tiny_skia::PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            transform,
            None,
        );
    }

    /// One button, in the colour it is lit.
    fn draw_key_sprite(
        &self,
        pixmap: &mut Pixmap,
        (x, y): (f32, f32),
        side: f32,
        colour: tiny_skia::Color,
        alpha: f32,
    ) {
        let Some(sprites) = &self.skin.sprites else {
            return;
        };
        let Some((art, _)) = sprites.coloured(Element::InputOverlayKey, 0) else {
            return;
        };
        if alpha <= 0.0 || side <= 0.0 {
            return;
        }
        let painted = crate::imported::tinted(art, colour);
        let scale = side / art.width() as f32;
        pixmap.draw_pixmap(
            0,
            0,
            painted.as_ref(),
            &tiny_skia::PixmapPaint {
                opacity: alpha.clamp(0.0, 1.0),
                quality: tiny_skia::FilterQuality::Bilinear,
                ..Default::default()
            },
            Transform::from_translate(x, y).pre_scale(scale, scale),
            None,
        );
    }
}

/// The two colours osu! lights a held key in — the game's own, nothing to do
/// with the map's palette. The first two keys take the top one.
fn active_colour(key: usize) -> tiny_skia::Color {
    if key < 2 {
        tiny_skia::Color::from_rgba8(0xff, 0xde, 0x00, 0xff)
    } else {
        tiny_skia::Color::from_rgba8(0xf8, 0x00, 0x9e, 0xff)
    }
}
