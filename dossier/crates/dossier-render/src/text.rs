//! Text, drawn glyph by glyph.
//!
//! [`tiny_skia`] rasterises paths and nothing else, so glyphs come from
//! [`fontdue`] as coverage bitmaps and are blended in by hand. That's the whole
//! of it: no shaping, no bidi, no fallback chain. A HUD is digits, a percent
//! sign and the occasional Latin word, and every one of those is one codepoint
//! to one glyph.

use tiny_skia::{Color, Pixmap, PremultipliedColorU8};

/// A loaded typeface.
#[derive(Clone)]
pub struct Font {
    inner: std::sync::Arc<fontdue::Font>,
}

impl std::fmt::Debug for Font {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The glyph tables are megabytes of no interest to anyone reading a
        // debug dump of a skin.
        f.write_str("Font(..)")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Centre,
    Right,
}

/// One run of text and everything about how to place it.
#[derive(Debug, Clone, Copy)]
pub struct Label<'a> {
    pub text: &'a str,
    pub x: f32,
    /// The baseline, not the top.
    pub y: f32,
    pub size: f32,
    pub colour: Color,
    pub align: Align,
}

impl Font {
    /// Load from the bytes of a `.ttf` or `.otf`.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, String> {
        let inner = fontdue::Font::from_bytes(bytes, fontdue::FontSettings::default())?;
        Ok(Self {
            inner: std::sync::Arc::new(inner),
        })
    }

    /// Width of `text` at `size`, in pixels.
    pub fn width(&self, text: &str, size: f32) -> f32 {
        text.chars()
            .map(|c| self.inner.metrics(c, size).advance_width)
            .sum()
    }

    /// Draw a label with its baseline at `label.y`.
    pub fn draw(&self, pixmap: &mut Pixmap, label: Label<'_>) {
        let Label {
            text,
            x,
            y,
            size,
            colour,
            align,
        } = label;

        let mut pen = match align {
            Align::Left => x,
            Align::Centre => x - self.width(text, size) / 2.0,
            Align::Right => x - self.width(text, size),
        };

        for ch in text.chars() {
            let (metrics, coverage) = self.inner.rasterize(ch, size);
            // `ymin` is the distance from the baseline to the *bottom* of the
            // bitmap, so the top edge is that far above it plus the height.
            let left = (pen + metrics.xmin as f32).round() as i32;
            let top = (y - (metrics.height as i32 + metrics.ymin) as f32).round() as i32;
            blit(pixmap, &coverage, metrics.width, left, top, colour);
            pen += metrics.advance_width;
        }
    }

    /// Distance from the baseline to the top of a digit, at `size`. Used to
    /// centre numbers on things rather than hanging them off a baseline.
    pub fn digit_height(&self, size: f32) -> f32 {
        self.inner.metrics('0', size).height as f32
    }
}

/// Blend an 8-bit coverage bitmap into the pixmap, source-over.
fn blit(pixmap: &mut Pixmap, coverage: &[u8], width: usize, left: i32, top: i32, colour: Color) {
    if width == 0 || coverage.is_empty() {
        return;
    }
    let (frame_w, frame_h) = (pixmap.width() as i32, pixmap.height() as i32);
    let height = coverage.len() / width;
    let (sr, sg, sb, sa) = (colour.red(), colour.green(), colour.blue(), colour.alpha());
    let pixels = pixmap.pixels_mut();

    for row in 0..height {
        let y = top + row as i32;
        if y < 0 || y >= frame_h {
            continue;
        }
        for column in 0..width {
            let x = left + column as i32;
            if x < 0 || x >= frame_w {
                continue;
            }
            let alpha = f32::from(coverage[row * width + column]) / 255.0 * sa;
            if alpha <= 0.0 {
                continue;
            }
            let index = (y * frame_w + x) as usize;
            let dst = pixels[index];
            // The pixmap is premultiplied, so the source has to be too before
            // the two are mixed.
            let keep = 1.0 - alpha;
            let mix = |src: f32, dst: u8| {
                ((src * alpha + f32::from(dst) / 255.0 * keep) * 255.0)
                    .round()
                    .clamp(0.0, 255.0) as u8
            };
            let (r, g, b) = (
                mix(sr, dst.red()),
                mix(sg, dst.green()),
                mix(sb, dst.blue()),
            );
            let a = ((alpha + f32::from(dst.alpha()) / 255.0 * keep) * 255.0)
                .round()
                .clamp(0.0, 255.0) as u8;
            // Rounding can leave a channel a hair above the alpha, which the
            // premultiplied invariant forbids.
            pixels[index] =
                PremultipliedColorU8::from_rgba(r.min(a), g.min(a), b.min(a), a).unwrap_or(dst);
        }
    }
}
