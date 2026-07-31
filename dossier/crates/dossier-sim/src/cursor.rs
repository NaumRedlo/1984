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
///
/// The hint is atomic rather than a `Cell` so that a track can be read from
/// several threads at once — which is what lets frames be rendered in
/// parallel. Relaxed ordering is enough: the hint is only a guess, every use
/// of it is checked against the frames themselves, and a stale one costs a
/// binary search rather than a wrong answer.
#[derive(Debug)]
pub struct CursorTrack {
    frames: Vec<ReplayFrame>,
    hint: std::sync::atomic::AtomicUsize,
}

impl Clone for CursorTrack {
    /// A copy starts its own hint rather than inheriting one. The hint is a
    /// guess about where the *last* lookup landed, and a fresh track has not
    /// looked anywhere yet.
    fn clone(&self) -> Self {
        Self::new(self.frames.clone())
    }
}

impl CursorTrack {
    pub fn new(frames: Vec<ReplayFrame>) -> Self {
        Self {
            frames,
            hint: std::sync::atomic::AtomicUsize::new(0),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.frames.is_empty()
    }

    pub fn len(&self) -> usize {
        self.frames.len()
    }

    /// The recorded frames themselves.
    ///
    /// Judgement works off these rather than off samples: a click happens on
    /// exactly one frame, and re-sampling would either invent presses or lose
    /// them.
    /// When each of the two buttons was down, as `(pressed_at, released_at)`.
    ///
    /// The one place the replay's key bitmask is read, and it needs to be one
    /// place: osu! sets the mouse bit *as well* when a keyboard button goes
    /// down, so pressing K1 arrives as `M1 | K1`. Counting the bits separately
    /// tallies every keyboard press twice; counting them together tallies it
    /// once and picks up a genuine mouse press — which arrives as the mouse bit
    /// alone — in the same button rather than losing it.
    ///
    /// Two buttons rather than four because that is what osu! standard has: a
    /// left and a right, whichever device they were struck on.
    ///
    /// A button still down when the recording stops is closed a millisecond
    /// past the press at the earliest. A press *on* the final frame would
    /// otherwise be an interval of no length and read as never held, and a
    /// millisecond is the finest the format distinguishes — frame times are
    /// whole ones — so this claims exactly "for the instant it was recorded in".
    pub fn holds(&self) -> [Vec<(f64, f64)>; 2] {
        let mut out: [Vec<(f64, f64)>; 2] = Default::default();
        let mut down = [None::<f64>; 2];
        for frame in &self.frames {
            let at = frame.time_ms as f64;
            let keys = frame.keys;
            let now = [
                keys.contains(Keys::K1) || keys.contains(Keys::M1),
                keys.contains(Keys::K2) || keys.contains(Keys::M2),
            ];
            for (index, held) in now.into_iter().enumerate() {
                match (down[index], held) {
                    (None, true) => down[index] = Some(at),
                    (Some(from), false) => {
                        out[index].push((from, at));
                        down[index] = None;
                    }
                    _ => {}
                }
            }
        }
        let last = self.frames.last().map_or(0.0, |f| f.time_ms as f64);
        for (index, from) in down.into_iter().enumerate() {
            if let Some(from) = from {
                out[index].push((from, last.max(from + 1.0)));
            }
        }
        out
    }

    pub fn frames(&self) -> &[ReplayFrame] {
        &self.frames
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
            self.hint.store(0, std::sync::atomic::Ordering::Relaxed);
            return 0;
        }
        if time_ms >= self.frames[last].time_ms as f64 {
            self.hint.store(last, std::sync::atomic::Ordering::Relaxed);
            return last;
        }

        // Playback is sequential, so try walking from where we left off before
        // falling back to a search.
        let hint = self
            .hint
            .load(std::sync::atomic::Ordering::Relaxed)
            .min(last);
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
                self.hint.store(i, std::sync::atomic::Ordering::Relaxed);
                return i;
            }
        }

        let idx = self
            .frames
            .partition_point(|f| (f.time_ms as f64) <= time_ms)
            .saturating_sub(1);
        self.hint.store(idx, std::sync::atomic::Ordering::Relaxed);
        idx
    }
}

fn lerp(a: f64, b: f64, t: f64) -> f64 {
    a + (b - a) * t
}
