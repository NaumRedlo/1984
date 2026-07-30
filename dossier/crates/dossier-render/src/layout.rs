//! Getting from osu!pixels to screen pixels.
//!
//! Maps are authored on a fixed 512×384 field and never mention resolution.
//! osu! fits that field to a fraction of the window height and centres it, so
//! the same map looks the same at every size — and so this transform is the
//! only place in the renderer that needs to know how big the output is.

use dossier_beatmap::{Point, PLAYFIELD_HEIGHT, PLAYFIELD_WIDTH};

/// Share of the frame height the playfield occupies. osu! leaves the rest for
/// the HUD above and below.
const PLAYFIELD_HEIGHT_RATIO: f64 = 0.8;

/// How far below centre the field sits, **in osu!pixels**.
///
/// danser's `SetOsuViewport`, emulating stable:
///
/// ```go
/// scl := baseScale * 0.8 * scale
/// if osuOffset { shiftY = 8 }
/// camera.positionV = vector.NewVec2d(shiftX, shiftY).Scl(scl)
/// ```
///
/// Eight osu!pixels, scaled with everything else — not a fraction of the frame.
/// This was written as 2% of the frame height, which is the same thing only at
/// 16:9 and diverges everywhere else: 20% too low on a widescreen frame, 80% on
/// a tall one, and nearly triple on a portrait render. The field is measured in
/// osu!pixels from end to end and its offset has to be too, or the layout stops
/// being a property of the game and becomes a property of the window.
const VERTICAL_SHIFT_OSU: f64 = 8.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Layout {
    pub width: u32,
    pub height: u32,
    scale: f64,
    origin_x: f64,
    origin_y: f64,
}

impl Layout {
    pub fn new(width: u32, height: u32) -> Self {
        let (w, h) = (f64::from(width), f64::from(height));
        // Fit by height, then check the width still holds it — a narrow window
        // would otherwise push the field off both sides.
        let scale = (h * PLAYFIELD_HEIGHT_RATIO / PLAYFIELD_HEIGHT)
            .min(w * PLAYFIELD_HEIGHT_RATIO / PLAYFIELD_WIDTH);
        Self {
            width,
            height,
            scale,
            origin_x: (w - PLAYFIELD_WIDTH * scale) / 2.0,
            origin_y: (h - PLAYFIELD_HEIGHT * scale) / 2.0 + VERTICAL_SHIFT_OSU * scale,
        }
    }

    /// Screen position of a point on the playfield.
    pub fn map(&self, point: Point) -> (f32, f32) {
        (
            (self.origin_x + point.x * self.scale) as f32,
            (self.origin_y + point.y * self.scale) as f32,
        )
    }

    /// A length in osu!pixels, in screen pixels.
    pub fn length(&self, osu_pixels: f64) -> f32 {
        (osu_pixels * self.scale) as f32
    }

    pub fn scale(&self) -> f64 {
        self.scale
    }

    /// The same mapping as [`Layout::map`], as a matrix.
    ///
    /// Lets geometry be built once in playfield coordinates and drawn at any
    /// size — which is what keeps slider paths out of the per-frame work.
    pub fn transform(&self) -> tiny_skia::Transform {
        tiny_skia::Transform::from_row(
            self.scale as f32,
            0.0,
            0.0,
            self.scale as f32,
            self.origin_x as f32,
            self.origin_y as f32,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_field_is_centred_horizontally() {
        let layout = Layout::new(1920, 1080);
        let (left, _) = layout.map(Point { x: 0.0, y: 0.0 });
        let (right, _) = layout.map(Point {
            x: PLAYFIELD_WIDTH,
            y: 0.0,
        });
        assert!(
            (left - (1920.0 - right)).abs() < 0.01,
            "margins differ: {left} vs {}",
            1920.0 - right
        );
    }

    #[test]
    fn the_field_keeps_its_aspect_ratio() {
        let layout = Layout::new(1920, 1080);
        let width = layout.length(PLAYFIELD_WIDTH);
        let height = layout.length(PLAYFIELD_HEIGHT);
        assert!((f64::from(width / height) - 4.0 / 3.0).abs() < 1e-6);
    }

    #[test]
    fn a_narrow_frame_fits_by_width_instead() {
        // 400×1000: fitting by height would need 1066px of width.
        let layout = Layout::new(400, 1000);
        assert!(layout.length(PLAYFIELD_WIDTH) <= 400.0);
    }

    #[test]
    fn the_centre_of_the_field_lands_near_the_centre_of_the_frame() {
        let layout = Layout::new(1280, 720);
        let (x, y) = layout.map(Point::CENTRE);
        // Horizontally exact, and eight osu!pixels below vertically.
        assert!((f64::from(x) - 640.0).abs() < 0.01);
        let below = f64::from(y) - 360.0;
        assert!(
            (below - f64::from(layout.length(VERTICAL_SHIFT_OSU))).abs() < 0.01,
            "{below}px below centre"
        );
    }
}
