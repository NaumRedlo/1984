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

/// The field sits slightly below centre, which is where osu! puts it — the
/// score and combo readouts need more room at the top than at the bottom.
const VERTICAL_BIAS: f64 = 0.02;

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
            origin_y: (h - PLAYFIELD_HEIGHT * scale) / 2.0 + h * VERTICAL_BIAS,
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
        assert!((f64::from(x) - 640.0).abs() < 0.01);
        assert!((f64::from(y) - 360.0).abs() < 720.0 * VERTICAL_BIAS + 0.01);
    }
}
