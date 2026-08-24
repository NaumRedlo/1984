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
    /// Two buttons rather than four because that is what a *click* is in osu!
    /// standard: a left and a right, whichever device they were struck on. For
    /// the four the key overlay shows, see [`Self::holds_each`].
    pub fn holds(&self) -> [Vec<(f64, f64)>; 2] {
        let [k1, k2] = self.spans([
            |keys: Keys| keys.contains(Keys::K1) || keys.contains(Keys::M1),
            |keys: Keys| keys.contains(Keys::K2) || keys.contains(Keys::M2),
        ]);
        [k1, k2]
    }

    /// The same, split by the device it was struck on: `K1, K2, M1, M2`, which
    /// is the order osu! shows them in.
    ///
    /// The mouse bit cannot simply be read on its own, for the reason above: it
    /// is set whenever a key is down, so `M1` read straight off the bitmask
    /// counts every keyboard press a second time. A press is *the mouse's* when
    /// the mouse bit is set and the keyboard bit beside it is not — which is
    /// also how osu! decides which of its four counters to move.
    ///
    /// Kept apart from [`Self::holds`] rather than replacing it. That one
    /// answers "was a click being held", which is what judging a slider and
    /// finding the hardest tapping in a play both need, and neither cares which
    /// finger did it.
    pub fn holds_each(&self, lazer: bool) -> [Vec<(f64, f64)>; 4] {
        if lazer {
            // lazer's own input has two actions and no idea which finger made
            // them, so its replays carry the mouse bits alone — never a
            // keyboard bit, on any frame, in any play. Read by stable's rule
            // that is *every press attributed to the mouse*, which is not
            // "unknown" but a false statement about how somebody played.
            //
            // So the two actions go where lazer itself shows them, in the two
            // key lanes, and the mouse lanes stay empty. Empty is the honest
            // answer for a column the file cannot fill.
            //
            // Asked of the client rather than of the frames. A stable play made
            // entirely with the mouse has no keyboard bits either, and reading
            // the frames alone would move a real mouse player's presses into
            // the keyboard's lanes — the same false statement, the other way
            // round.
            return self.spans([
                |keys: Keys| keys.contains(Keys::M1),
                |keys: Keys| keys.contains(Keys::M2),
                |_: Keys| false,
                |_: Keys| false,
            ]);
        }
        self.spans([
            |keys: Keys| keys.contains(Keys::K1),
            |keys: Keys| keys.contains(Keys::K2),
            |keys: Keys| keys.contains(Keys::M1) && !keys.contains(Keys::K1),
            |keys: Keys| keys.contains(Keys::M2) && !keys.contains(Keys::K2),
        ])
    }



    /// When each of `N` lanes was held, in time order.
    ///
    /// A button still down when the recording stops is closed a millisecond
    /// past the press at the earliest. A press *on* the final frame would
    /// otherwise be an interval of no length and read as never held, and a
    /// millisecond is the finest the format distinguishes — frame times are
    /// whole ones — so this claims exactly "for the instant it was recorded in".
    fn spans<const N: usize>(
        &self,
        lanes: [fn(Keys) -> bool; N],
    ) -> [Vec<(f64, f64)>; N] {
        let mut out: [Vec<(f64, f64)>; N] = std::array::from_fn(|_| Vec::new());
        let mut down = [None::<f64>; N];
        for frame in &self.frames {
            let at = frame.time_ms as f64;
            for (index, lane) in lanes.iter().enumerate() {
                match (down[index], lane(frame.keys)) {
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
