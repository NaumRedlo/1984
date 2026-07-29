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

use std::collections::BTreeMap;
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
    /// What lazer appends after everything stable knows about. Absent on every
    /// stable replay, and on lazer replays older than the version that
    /// introduced it.
    pub score_info: Option<ScoreInfo>,
}

/// One mod as lazer records it.
///
/// The legacy mod field in the header is a bitmask stable's mods fit into, and
/// lazer has mods that do not — Classic above all, which changes how sliders
/// are scored and which note lock is in force. Without this block a Classic
/// score is indistinguishable from an ordinary one.
#[derive(Debug, Clone, PartialEq)]
pub struct LazerMod {
    pub acronym: String,
    /// Settings the player changed from their defaults. Absent keys mean the
    /// default, which is *not* the same as false — Classic's switches are all
    /// on unless someone turned one off.
    pub settings: BTreeMap<String, Setting>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Setting {
    Bool(bool),
    Number(f64),
    Text(String),
}

impl LazerMod {
    /// A boolean setting, or `default` when the player left it alone.
    pub fn switch(&self, name: &str, default: bool) -> bool {
        match self.settings.get(name) {
            Some(Setting::Bool(b)) => *b,
            _ => default,
        }
    }
}

/// lazer's own account of the play, from the block it appends to the replay.
///
/// Worth far more than the mods it was opened for. `statistics` is a count per
/// judgement *type* — how many slider tails were caught, how many large ticks,
/// how many were ignored — where the legacy header has only four numbers with
/// sliders folded into them. It is the closest thing to a per-object answer any
/// replay carries.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ScoreInfo {
    /// The build that recorded it, like `2026.417.0-lazer`.
    pub client_version: Option<String>,
    pub rank: Option<String>,
    pub mods: Vec<LazerMod>,
    pub statistics: BTreeMap<String, i64>,
    pub maximum_statistics: BTreeMap<String, i64>,
}

impl ScoreInfo {
    pub fn mod_named(&self, acronym: &str) -> Option<&LazerMod> {
        self.mods.iter().find(|m| m.acronym == acronym)
    }
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

        // Everything above is stable's format. What follows is lazer's, and
        // only lazer's — a stable replay simply ends here.
        let score_info = read_score_info(&mut r);

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
            score_info,
        })
    }

    /// The mods lazer recorded, which is not the same list as [`Replay::mods`].
    pub fn lazer_mods(&self) -> &[LazerMod] {
        self.score_info.as_ref().map_or(&[], |info| &info.mods)
    }

    /// The build that recorded this, as a human would name it.
    ///
    /// lazer knows its own version and says so; stable's header carries a date
    /// stamp instead, which is rendered here the way the game writes it.
    pub fn client_version(&self) -> String {
        match self.score_info.as_ref().and_then(|i| i.client_version.clone()) {
            Some(version) => version,
            None => {
                let v = self.game_version;
                let (y, m, d) = (v / 10_000, (v / 100) % 100, v % 100);
                if (2000..2100).contains(&y) && (1..=12).contains(&m) && (1..=31).contains(&d) {
                    format!("{y}.{m}.{d}")
                } else {
                    v.to_string()
                }
            }
        }
    }
}

/// Read lazer's trailing score-info block, if there is one.
///
/// `LegacyScoreEncoder` writes it as a length-prefixed byte array holding the
/// same LZMA-alone stream the frames use, of an ASCII JSON document. Anything
/// unexpected means "no block": a replay is an untrusted file, and a reader
/// that fails loudly on a format it has not met yet is a reader that refuses
/// perfectly good replays.
fn read_score_info(r: &mut Reader) -> Option<ScoreInfo> {
    let length = r.i32().ok()?;
    if length <= 0 {
        return None;
    }
    let blob = r.bytes(length as usize).ok()?;
    let text = decompress(blob).ok()?;
    let root = crate::json::parse(&text)?;

    let mods = root
        .get("mods")
        .and_then(crate::json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let acronym = item.get("acronym")?.as_str()?.to_owned();
                    let settings = item
                        .get("settings")
                        .and_then(crate::json::Value::as_object)
                        .map(|map| {
                            map.iter()
                                .filter_map(|(k, v)| Some((k.clone(), setting(v)?)))
                                .collect()
                        })
                        .unwrap_or_default();
                    Some(LazerMod { acronym, settings })
                })
                .collect()
        })
        .unwrap_or_default();

    let counts = |key: &str| {
        root.get(key)
            .and_then(crate::json::Value::as_object)
            .map(|map| {
                map.iter()
                    .filter_map(|(k, v)| Some((k.clone(), v.as_i64()?)))
                    .collect()
            })
            .unwrap_or_default()
    };

    Some(ScoreInfo {
        client_version: root
            .get("client_version")
            .and_then(crate::json::Value::as_str)
            .map(str::to_owned),
        rank: root
            .get("rank")
            .and_then(crate::json::Value::as_str)
            .map(str::to_owned),
        mods,
        statistics: counts("statistics"),
        maximum_statistics: counts("maximum_statistics"),
    })
}

fn setting(value: &crate::json::Value) -> Option<Setting> {
    if let Some(b) = value.as_bool() {
        return Some(Setting::Bool(b));
    }
    if let Some(s) = value.as_str() {
        return Some(Setting::Text(s.to_owned()));
    }
    match value {
        crate::json::Value::Number(n) => Some(Setting::Number(*n)),
        _ => None,
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

/// The health graph osu! writes into the header, parsed.
///
/// A comma-separated list of `time|value`, where value runs 0 to 1. Sampled
/// every couple of seconds and at every moment the bar moves sharply, which is
/// enough to draw it and far cheaper than modelling HP drain — this is the
/// game's own answer rather than a reconstruction of it.
///
/// Not every replay carries one: it is empty on a good half of the corpus, and
/// a renderer has to cope with having no health to show rather than inventing
/// some.
pub fn life_points(life_bar: &str) -> Vec<(f64, f32)> {
    let mut out: Vec<(f64, f32)> = life_bar
        .split(',')
        .filter_map(|entry| {
            let (time, value) = entry.trim().split_once('|')?;
            Some((time.parse().ok()?, value.parse::<f32>().ok()?.clamp(0.0, 1.0)))
        })
        .collect();
    out.sort_by(|a, b| a.0.total_cmp(&b.0));
    out
}

#[cfg(test)]
mod life_tests {
    use super::life_points;

    #[test]
    fn a_health_graph_is_read_as_time_and_value() {
        let points = life_points("38005|1,40250|0.5,42426|0");
        assert_eq!(points.len(), 3);
        assert_eq!(points[0], (38005.0, 1.0));
        assert_eq!(points[2], (42426.0, 0.0));
    }

    #[test]
    fn a_replay_without_a_graph_gives_nothing() {
        // Half the corpus is like this, and a renderer must not fill the gap
        // with a health bar it made up.
        assert!(life_points("").is_empty());
        assert!(life_points("   ").is_empty());
    }
}
