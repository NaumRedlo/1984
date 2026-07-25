//! `.osr` parsing.
//!
//! Layout (all little-endian), per the osu! file-format documentation:
//!
//! ```text
//! byte    game mode
//! int     game version
//! string  beatmap MD5
//! string  player name
//! string  replay MD5
//! short   300s / 100s / 50s / gekis / katus / misses   (six shorts)
//! int     total score
//! short   max combo
//! byte    perfect combo flag
//! int     mods
//! string  life bar graph
//! long    timestamp (Windows ticks)
//! int     length of the compressed frame block
//! bytes   LZMA-compressed frames
//! long    online score id          (absent in very old replays)
//! double  target-practice accuracy (only when the Target mod is set)
//! ```

use std::io::{BufReader, Cursor};

use crate::error::{ReplayError, Result};
use crate::mods::{bits, GameMode, Mods};
use crate::reader::Reader;

/// Windows tick epoch (0001-01-01) to Unix epoch, in seconds.
const TICKS_EPOCH_OFFSET_SECS: i64 = 62_135_596_800;
const TICKS_PER_SEC: i64 = 10_000_000;

/// Sentinel in place of a frame time, marking the trailing RNG-seed record.
const SEED_FRAME_TIME: i64 = -12345;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Keys(pub u8);

impl Keys {
    pub const M1: u8 = 1 << 0;
    pub const M2: u8 = 1 << 1;
    pub const K1: u8 = 1 << 2;
    pub const K2: u8 = 1 << 3;
    pub const SMOKE: u8 = 1 << 4;

    pub fn is_pressed(self) -> bool {
        self.0 & (Self::M1 | Self::M2 | Self::K1 | Self::K2) != 0
    }

    pub fn contains(self, key: u8) -> bool {
        self.0 & key != 0
    }
}

/// One cursor sample. `time_ms` is absolute (milliseconds from the audio's
/// zero), already accumulated from the per-frame deltas stored in the file.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ReplayFrame {
    pub time_ms: i64,
    pub x: f32,
    pub y: f32,
    pub keys: Keys,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HitCounts {
    pub count_300: u16,
    pub count_100: u16,
    pub count_50: u16,
    pub count_geki: u16,
    pub count_katu: u16,
    pub count_miss: u16,
}

impl HitCounts {
    /// Judged objects — the denominator for osu!std accuracy.
    pub fn total_hits(self) -> u32 {
        u32::from(self.count_300)
            + u32::from(self.count_100)
            + u32::from(self.count_50)
            + u32::from(self.count_miss)
    }

    /// osu!std accuracy in percent. Returns 100.0 for an empty replay, matching
    /// how the client displays a score with nothing judged yet.
    pub fn accuracy_std(self) -> f64 {
        let total = self.total_hits();
        if total == 0 {
            return 100.0;
        }
        let weighted = 300.0 * f64::from(self.count_300)
            + 100.0 * f64::from(self.count_100)
            + 50.0 * f64::from(self.count_50);
        weighted / (300.0 * f64::from(total)) * 100.0
    }
}

#[derive(Debug, Clone)]
pub struct Replay {
    pub mode: GameMode,
    pub game_version: i32,
    pub beatmap_hash: String,
    pub player: String,
    pub replay_hash: String,
    pub hits: HitCounts,
    pub score: i32,
    pub max_combo: u16,
    pub perfect_combo: bool,
    pub mods: Mods,
    /// Raw life-bar string (`ms|life` pairs); parsed on demand, not here.
    pub life_bar: String,
    /// Timestamp in Windows ticks, exactly as stored.
    pub timestamp_ticks: i64,
    pub online_score_id: i64,
    /// Only present when the Target mod is set.
    pub target_practice_accuracy: Option<f64>,
    pub frames: Vec<ReplayFrame>,
    /// Seed from the trailing `-12345` record, when the replay carries one.
    pub rng_seed: Option<i64>,
}

impl Replay {
    /// Unix timestamp (seconds) of when the replay was played.
    pub fn played_at_unix(&self) -> i64 {
        self.timestamp_ticks / TICKS_PER_SEC - TICKS_EPOCH_OFFSET_SECS
    }

    /// Length of the recorded input, in milliseconds.
    pub fn duration_ms(&self) -> i64 {
        match (self.frames.first(), self.frames.last()) {
            (Some(first), Some(last)) => last.time_ms - first.time_ms,
            _ => 0,
        }
    }

    pub fn parse(data: &[u8]) -> Result<Self> {
        let mut r = Reader::new(data);

        let mode_byte = r.u8()?;
        let mode = GameMode::from_byte(mode_byte).ok_or(ReplayError::UnknownMode(mode_byte))?;
        let game_version = r.i32()?;
        let beatmap_hash = r.string()?;
        let player = r.string()?;
        let replay_hash = r.string()?;

        let hits = HitCounts {
            count_300: r.u16()?,
            count_100: r.u16()?,
            count_50: r.u16()?,
            count_geki: r.u16()?,
            count_katu: r.u16()?,
            count_miss: r.u16()?,
        };

        let score = r.i32()?;
        let max_combo = r.u16()?;
        let perfect_combo = r.u8()? != 0;
        let mods = Mods::new(r.u32()?);
        let life_bar = r.string()?;
        let timestamp_ticks = r.i64()?;

        let compressed_len = r.i32()?;
        let compressed = if compressed_len > 0 {
            r.bytes(compressed_len as usize)?
        } else {
            &[][..]
        };

        // Both of these are optional tails: replays from before online ids
        // existed simply end after the frame block, and the target-practice
        // double is only written when that mod is on. Read defensively rather
        // than failing a valid old replay.
        let online_score_id = if r.remaining() >= 8 { r.i64()? } else { 0 };
        let target_practice_accuracy = if mods.contains(bits::TARGET) && r.remaining() >= 8 {
            Some(r.f64()?)
        } else {
            None
        };

        let (frames, rng_seed) = if compressed.is_empty() {
            (Vec::new(), None)
        } else {
            parse_frames(&decompress(compressed)?)?
        };

        Ok(Self {
            mode,
            game_version,
            beatmap_hash,
            player,
            replay_hash,
            hits,
            score,
            max_combo,
            perfect_combo,
            mods,
            life_bar,
            timestamp_ticks,
            online_score_id,
            target_practice_accuracy,
            frames,
            rng_seed,
        })
    }
}

fn decompress(compressed: &[u8]) -> Result<String> {
    let mut out = Vec::new();
    lzma_rs::lzma_decompress(&mut BufReader::new(Cursor::new(compressed)), &mut out)
        .map_err(|e| ReplayError::Lzma(e.to_string()))?;
    String::from_utf8(out).map_err(|_| ReplayError::Lzma("frame data is not UTF-8".into()))
}

/// Frames arrive as `w|x|y|z` records separated by commas, where `w` is the
/// delta since the previous record. Two details a naive split gets wrong:
///
/// * the last record is usually `-12345|0|0|<seed>` — an RNG seed, not a frame.
///   Left in place it becomes a sample at time −12345 that wrecks any timeline;
/// * deltas have to be accumulated, since everything downstream wants absolute
///   times. Early frames may still land before zero, which is legitimate: the
///   client records cursor movement during the lead-in.
fn parse_frames(text: &str) -> Result<(Vec<ReplayFrame>, Option<i64>)> {
    let mut frames = Vec::new();
    let mut seed = None;
    let mut clock: i64 = 0;

    for record in text.split(',') {
        if record.is_empty() {
            continue;
        }
        let mut parts = record.split('|');
        let (Some(w), Some(x), Some(y), Some(z), None) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
        ) else {
            return Err(ReplayError::BadFrame {
                frame: record.to_owned(),
            });
        };

        let bad = || ReplayError::BadFrame {
            frame: record.to_owned(),
        };
        let delta: i64 = w.parse().map_err(|_| bad())?;

        if delta == SEED_FRAME_TIME {
            seed = z.parse::<i64>().ok();
            continue;
        }

        clock += delta;
        frames.push(ReplayFrame {
            time_ms: clock,
            x: x.parse().map_err(|_| bad())?,
            y: y.parse().map_err(|_| bad())?,
            keys: Keys(z.parse::<u32>().map_err(|_| bad())? as u8),
        });
    }

    Ok((frames, seed))
}
