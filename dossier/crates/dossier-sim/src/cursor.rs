//! Cursor position between replay frames.
//!
//! Frames arrive at whatever rate the client recorded them — around 60 Hz, but
//! irregular, and sparser when the cursor is still. Video wants a position at
//! an arbitrary instant, so samples are interpolated.
//!
//! Keys are *not* interpolated: a button is down or it isn't, so the state of
//! the frame at or before the query time is what holds.

use dossier_beatmap::Point;
use dossier_replay::{Keys, ReplayFrame};

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Cursor {
    pub pos: Point,
    pub keys: Keys,
}

/// Frames plus a sequential-access hint.
///
/// Rendering walks time forwards, so the last index is remembered and the next
/// lookup usually costs a step or two instead of a binary search over a track
/// with tens of thousands of frames.
#[derive(Debug, Clone)]
pub struct CursorTrack {
    frames: Vec<ReplayFrame>,
    hint: std::cell::Cell<usize>,
}

impl CursorTrack {
    pub fn new(frames: Vec<ReplayFrame>) -> Self {
        Self {
            frames,
            hint: std::cell::Cell::new(0),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.frames.is_empty()
    }

    pub fn len(&self) -> usize {
        self.frames.len()
    }

    /// First and last recorded times, if there are any frames.
    pub fn span_ms(&self) -> Option<(f64, f64)> {
        Some((
            self.frames.first()?.time_ms as f64,
            self.frames.last()?.time_ms as f64,
        ))
    }

    /// Cursor state at `time_ms`.
    ///
    /// Outside the recorded span the nearest end is held rather than
    /// extrapolated — the cursor sat still before the first frame and after the
    /// last, which is exactly what the player's did.
    pub fn sample(&self, time_ms: f64) -> Option<Cursor> {
        if self.frames.is_empty() {
            return None;
        }
        let idx = self.index_at(time_ms);
        let current = &self.frames[idx];

        let Some(next) = self.frames.get(idx + 1) else {
            return Some(Cursor {
                pos: Point {
                    x: f64::from(current.x),
                    y: f64::from(current.y),
                },
                keys: current.keys,
            });
        };

        let span = (next.time_ms - current.time_ms) as f64;
        let t = if span > 0.0 {
            ((time_ms - current.time_ms as f64) / span).clamp(0.0, 1.0)
        } else {
            0.0
        };

        Some(Cursor {
            pos: Point {
                x: lerp(f64::from(current.x), f64::from(next.x), t),
                y: lerp(f64::from(current.y), f64::from(next.y), t),
            },
            // Held, not blended: a key is pressed or it isn't.
            keys: current.keys,
        })
    }

    /// Index of the last frame at or before `time_ms`, clamped into range.
    fn index_at(&self, time_ms: f64) -> usize {
        let last = self.frames.len() - 1;
        if time_ms <= self.frames[0].time_ms as f64 {
            self.hint.set(0);
            return 0;
        }
        if time_ms >= self.frames[last].time_ms as f64 {
            self.hint.set(last);
            return last;
        }

        // Playback is sequential, so try walking from where we left off before
        // falling back to a search.
        let hint = self.hint.get().min(last);
        if self.frames[hint].time_ms as f64 <= time_ms {
            let mut i = hint;
            for _ in 0..8 {
                match self.frames.get(i + 1) {
                    Some(next) if (next.time_ms as f64) <= time_ms => i += 1,
                    _ => break,
                }
            }
            if self
                .frames
                .get(i + 1)
                .is_some_and(|n| (n.time_ms as f64) > time_ms)
            {
                self.hint.set(i);
                return i;
            }
        }

        let idx = self
            .frames
            .partition_point(|f| (f.time_ms as f64) <= time_ms)
            .saturating_sub(1);
        self.hint.set(idx);
        idx
    }
}

fn lerp(a: f64, b: f64, t: f64) -> f64 {
    a + (b - a) * t
}
