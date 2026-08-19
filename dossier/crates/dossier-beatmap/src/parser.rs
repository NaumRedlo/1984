//! `.osu` parsing.
//!
//! The file is INI-ish: a version header, then `[Section]` blocks that are
//! either `Key:Value` lines or comma-separated records. Real files are messier
//! than the spec — CRLF endings, stray whitespace, `//` comments, and fields
//! that simply don't exist in older format versions — so parsing leans
//! tolerant wherever the game itself is.

use crate::difficulty::Difficulty;
use crate::error::{BeatmapError, Result};
use crate::hitobject::{parse_curve, type_bits, HitObject, HitSample, ObjectKind, Point, Slider};
use crate::timing::{SamplePoint, SampleSet, Timing, TimingPoint, VelocityPoint};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Metadata {
    pub title: String,
    pub artist: String,
    pub creator: String,
    /// Difficulty name — `Version` in the file, confusingly.
    pub version: String,
    pub beatmap_id: Option<i64>,
    pub beatmapset_id: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Beatmap {
    pub format_version: u32,
    /// 0 = osu!, 1 = taiko, 2 = catch, 3 = mania. Stays a raw byte until the
    /// simulator needs a shared enum across crates.
    pub mode: u8,
    pub metadata: Metadata,
    pub difficulty: Difficulty,
    pub timing: Timing,
    pub objects: Vec<HitObject>,
    pub audio_filename: String,
    /// The bank a timing point falls back to when it names none.
    ///
    /// `[General] SampleSet`. A timing point writing `0` in its sample-set
    /// field is not asking for `normal`; it is asking for whatever the map
    /// declared up here, and reading it as `normal` plays a different set of
    /// files from the one the mapper heard.
    pub sample_set: SampleSet,
    /// Background image from `[Events]`, when the map names one.
    pub background: Option<String>,
    /// Pauses the map declares, as (start, end) in milliseconds.
    pub breaks: Vec<(f64, f64)>,
    pub stack_leniency: f64,
    /// Combo colours as authored. Empty when the map doesn't override them —
    /// see [`Beatmap::combo_colours`], which fills in osu!'s defaults.
    pub colours: Vec<Colour>,
}

impl Default for Beatmap {
    fn default() -> Self {
        Self {
            format_version: 0,
            mode: 0,
            metadata: Metadata::default(),
            difficulty: Difficulty::default(),
            timing: Timing::default(),
            objects: Vec::new(),
            audio_filename: String::new(),
            sample_set: SampleSet::Normal,
            background: None,
            breaks: Vec::new(),
            stack_leniency: 0.7,
            colours: Vec::new(),
        }
    }
}

impl Beatmap {
    /// The palette to draw with: the map's own, or osu!'s defaults when it
    /// doesn't state any.
    pub fn combo_colours(&self) -> &[Colour] {
        if self.colours.is_empty() {
            DEFAULT_COMBO_COLOURS
        } else {
            &self.colours
        }
    }

    /// Objects that count toward combo and accuracy.
    pub fn object_count(&self) -> usize {
        self.objects.len()
    }

    /// Playable span: first object to last object end.
    pub fn drain_time_ms(&self) -> f64 {
        match (self.objects.first(), self.objects.last()) {
            (Some(first), Some(last)) => last.end_time_ms() - first.time_ms,
            _ => 0.0,
        }
    }

    pub fn parse(text: &str) -> Result<Self> {
        let mut map = Beatmap::default();
        let mut section = String::new();
        let mut saw_header = false;
        // AR only exists from format v8 on; before that the game reuses OD.
        // Tracked explicitly because "absent" and "authored as 5" differ.
        let mut approach_rate: Option<f64> = None;

        for (idx, raw) in text.lines().enumerate() {
            let line = strip_comment(raw);
            if line.is_empty() {
                continue;
            }

            if !saw_header {
                if let Some(v) = parse_header(line) {
                    map.format_version = v;
                    saw_header = true;
                    continue;
                }
                return Err(BeatmapError::MissingHeader);
            }

            if let Some(name) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
                section = name.to_ascii_lowercase();
                continue;
            }

            let line_no = idx + 1;
            match section.as_str() {
                "general" => {
                    if let Some((k, v)) = split_kv(line) {
                        match k.as_str() {
                            "mode" => map.mode = v.parse().unwrap_or(0),
                            "audiofilename" => map.audio_filename = v.to_owned(),
                            "sampleset" => {
                                map.sample_set = match v.to_ascii_lowercase().as_str() {
                                    "soft" => SampleSet::Soft,
                                    "drum" => SampleSet::Drum,
                                    _ => SampleSet::Normal,
                                }
                            }
                            "stackleniency" => {
                                map.stack_leniency = v.parse().unwrap_or(0.7);
                            }
                            _ => {}
                        }
                    }
                }
                "colours" => {
                    // `Combo1 : 255,192,0`. Other keys in this section colour
                    // the slider border and track; they aren't per-combo and
                    // are left alone.
                    if let Some((k, v)) = split_kv(line) {
                        if k.starts_with("combo") {
                            if let Some(colour) = Colour::parse(v) {
                                map.colours.push(colour);
                            }
                        }
                    }
                }
                "metadata" => {
                    if let Some((k, v)) = split_kv(line) {
                        let m = &mut map.metadata;
                        match k.as_str() {
                            "title" => m.title = v.to_owned(),
                            "artist" => m.artist = v.to_owned(),
                            "creator" => m.creator = v.to_owned(),
                            "version" => m.version = v.to_owned(),
                            "beatmapid" => m.beatmap_id = v.parse().ok(),
                            "beatmapsetid" => m.beatmapset_id = v.parse().ok(),
                            _ => {}
                        }
                    }
                }
                "difficulty" => {
                    if let Some((k, v)) = split_kv(line) {
                        let d = &mut map.difficulty;
                        let num = v.parse::<f64>();
                        match (k.as_str(), num) {
                            ("hpdrainrate", Ok(n)) => d.hp_drain = n,
                            ("circlesize", Ok(n)) => d.circle_size = n,
                            ("overalldifficulty", Ok(n)) => d.overall_difficulty = n,
                            ("approachrate", Ok(n)) => approach_rate = Some(n),
                            ("slidermultiplier", Ok(n)) => d.slider_multiplier = n,
                            ("slidertickrate", Ok(n)) => d.slider_tick_rate = n,
                            _ => {}
                        }
                    }
                }
                "events" => {
                    if map.background.is_none() {
                        map.background = parse_background(line);
                    }
                    if let Some(gap) = parse_break(line) {
                        map.breaks.push(gap);
                    }
                }
                "timingpoints" => parse_timing_point(line, line_no, &mut map.timing)?,
                "hitobjects" => map.objects.push(parse_hit_object(line, line_no)?),
                _ => {}
            }
        }

        if !saw_header {
            return Err(BeatmapError::MissingHeader);
        }

        map.difficulty.approach_rate = approach_rate.unwrap_or(map.difficulty.overall_difficulty);
        // Objects and timing points are authored in order, but not every editor
        // has honoured that; lookups here assume sorted input.
        map.timing
            .uninherited
            .sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));
        map.timing
            .inherited
            .sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));
        map.timing
            .samples
            .sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));
        map.objects.sort_by(|a, b| a.time_ms.total_cmp(&b.time_ms));

        Ok(map)
    }
}

fn strip_comment(raw: &str) -> &str {
    let line = raw.trim();
    match line.find("//") {
        Some(0) => "",
        Some(i) => line[..i].trim_end(),
        None => line,
    }
}

fn parse_header(line: &str) -> Option<u32> {
    let idx = line.find("osu file format v")?;
    line[idx + "osu file format v".len()..].trim().parse().ok()
}

/// `Key: Value` — the key is matched case-insensitively, the value kept as-is.
fn split_kv(line: &str) -> Option<(String, &str)> {
    let (k, v) = line.split_once(':')?;
    Some((k.trim().to_ascii_lowercase(), v.trim()))
}

/// `[Events]` break line: `2,start,end`. Old maps spell the type `Break`.
///
/// Breaks are the map telling the player they may stop. What comes after one
/// arrives with no warning from the rhythm, which is why the game puts arrows
/// up before it — so the pause has to be known to draw them.
fn parse_break(line: &str) -> Option<(f64, f64)> {
    let mut parts = line.split(',');
    let kind = parts.next()?.trim();
    if kind != "2" && !kind.eq_ignore_ascii_case("break") {
        return None;
    }
    let start: f64 = parts.next()?.trim().parse().ok()?;
    let end: f64 = parts.next()?.trim().parse().ok()?;
    (end > start).then_some((start, end))
}

/// `[Events]` background line: `0,0,"bg.jpg",0,0`. Video lines share the shape
/// but use type `1`/`Video`, so the type field is checked rather than assumed.
fn parse_background(line: &str) -> Option<String> {
    let mut parts = line.split(',');
    let kind = parts.next()?.trim();
    if kind != "0" {
        return None;
    }
    parts.next()?; // start time, always 0 for a background
    let name = parts.next()?.trim().trim_matches('"');
    (!name.is_empty()).then(|| name.to_owned())
}

fn parse_timing_point(line: &str, line_no: usize, timing: &mut Timing) -> Result<()> {
    let bad = || BeatmapError::BadTimingPoint {
        line: line_no,
        text: line.to_owned(),
    };
    let parts: Vec<&str> = line.split(',').map(str::trim).collect();
    if parts.len() < 2 {
        return Err(bad());
    }

    let time: f64 = parts[0].parse().map_err(|_| bad())?;
    let beat_length: f64 = parts[1].parse().map_err(|_| bad())?;
    let meter: u32 = parts.get(2).and_then(|s| s.parse().ok()).unwrap_or(4);
    // Very old maps omit the flag entirely; a positive beat length means red.
    let uninherited = parts
        .get(6)
        .and_then(|s| s.parse::<i32>().ok())
        .map_or(beat_length > 0.0, |v| v != 0);
    let kiai = parts
        .get(7)
        .and_then(|s| s.parse::<i32>().ok())
        .is_some_and(|v| v & 1 != 0);

    // Sound settings ride on every timing point, red or green, so they're
    // recorded before the line is sorted into one bucket or the other.
    timing.samples.push(SamplePoint {
        time_ms: time,
        set: SampleSet::from_code(parts.get(3).and_then(|s| s.parse().ok()).unwrap_or(0)),
        set_given: parts
            .get(3)
            .and_then(|s| s.trim().parse::<u8>().ok())
            .is_some_and(|code| code != 0),
        index: parts.get(4).and_then(|s| s.parse().ok()).unwrap_or(0),
        volume: parts.get(5).and_then(|s| s.parse().ok()).unwrap_or(100),
    });

    if uninherited {
        timing.uninherited.push(TimingPoint {
            time_ms: time,
            beat_length: beat_length.abs(),
            meter,
            kiai,
        });
    } else {
        // Green lines store -100/SV, so SV = -100/value. Guard the degenerate
        // zero rather than producing an infinity that poisons later maths.
        //
        // Then clamped to the range the game allows, which is not decoration:
        // `DifficultyControlPoint.SliderVelocityBindable` is a
        // `BindableDouble(1) { MinValue = 0.1, MaxValue = 10 }`, so a line
        // asking for anything outside that gets the nearest end of it. Maps do
        // ask — a `-10000` appears in the wild, meaning 0.01, and the game
        // plays it at 0.1.
        //
        // Without the clamp the slider that line governs is ten times too slow
        // and so ten times too long: one measured at thirty seconds is three in
        // the game. That is a wrong duration for the renderer to draw, a wrong
        // end for the judge to hold the player to, and eighty-one slider ticks
        // that do not exist.
        let velocity = if beat_length < 0.0 {
            (-100.0 / beat_length).clamp(0.1, 10.0)
        } else {
            1.0
        };
        timing.inherited.push(VelocityPoint {
            time_ms: time,
            velocity,
            kiai,
        });
    }
    Ok(())
}

fn parse_hit_object(line: &str, line_no: usize) -> Result<HitObject> {
    let bad = || BeatmapError::BadHitObject {
        line: line_no,
        text: line.to_owned(),
    };
    let parts: Vec<&str> = line.split(',').map(str::trim).collect();
    if parts.len() < 4 {
        return Err(bad());
    }

    let pos = Point {
        x: parts[0].parse().map_err(|_| bad())?,
        y: parts[1].parse().map_err(|_| bad())?,
    };
    let time_ms: f64 = parts[2].parse().map_err(|_| bad())?;
    let type_field: u32 = parts[3].parse().map_err(|_| bad())?;
    let new_combo = type_field & type_bits::NEW_COMBO != 0;
    // Absent or unreadable means the plain hit sound, which is what a note with
    // no decoration makes.
    let hit_sound: u8 = parts.get(4).and_then(|s| s.parse().ok()).unwrap_or(0);
    // The sample field is always last, and how far along that is depends on
    // the object kind: a circle has nothing between, a slider has four fields
    // of curve, and a spinner has its end time.
    let hit_sample = HitSample::parse(parts.last().filter(|s| s.contains(':')));

    let kind = if type_field & type_bits::SLIDER != 0 {
        // curve, slides, length — the two numbers are absent in a few very old
        // maps, where the game treats the slider as a single pass.
        let spec = parts.get(5).ok_or_else(bad)?;
        let (curve_type, mut points) = parse_curve(spec).ok_or_else(bad)?;
        points.insert(0, pos);
        Slider {
            curve_type,
            points,
            slides: parts
                .get(6)
                .and_then(|s| s.parse().ok())
                .unwrap_or(1)
                .max(1),
            length: parts.get(7).and_then(|s| s.parse().ok()).unwrap_or(0.0),
            // `2|0|8` — one bitmask per edge, pipe separated.
            edge_sounds: parts
                .get(8)
                .map(|f| f.split('|').filter_map(|v| v.parse().ok()).collect())
                .unwrap_or_default(),
            // `0:0|0:2|0:0` — normalSet:additionSet per edge.
            edge_sets: parts
                .get(9)
                .map(|f| {
                    f.split('|')
                        .filter_map(|v| {
                            let (normal, addition) = v.split_once(':')?;
                            Some((normal.parse().ok()?, addition.parse().ok()?))
                        })
                        .collect()
                })
                .unwrap_or_default(),
        }
        .into()
    } else if type_field & type_bits::SPINNER != 0 {
        ObjectKind::Spinner {
            end_time_ms: parts.get(5).and_then(|s| s.parse().ok()).ok_or_else(bad)?,
        }
    } else if type_field & type_bits::CIRCLE != 0 {
        ObjectKind::Circle
    } else if type_field & type_bits::MANIA_HOLD != 0 {
        // A mania hold degrades to its head note. Dossier renders osu!standard,
        // so the alternative would be failing the whole file — this at least
        // leaves a mania map parseable for metadata and timing.
        ObjectKind::Circle
    } else {
        return Err(BeatmapError::UnknownObjectType {
            line: line_no,
            type_bits: type_field,
        });
    };

    Ok(HitObject {
        pos,
        time_ms,
        new_combo,
        hit_sound,
        hit_sample,
        kind,
    })
}

impl From<Slider> for ObjectKind {
    fn from(s: Slider) -> Self {
        ObjectKind::Slider(s)
    }
}

/// A combo colour, as authored in `[Colours]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Colour {
    pub r: u8,
    pub g: u8,
    pub b: u8,
}

impl Colour {
    /// `255,192,0` — a trailing alpha is accepted and ignored, which some maps
    /// carry.
    fn parse(text: &str) -> Option<Self> {
        let mut parts = text.split(',').map(|p| p.trim().parse::<u8>());
        Some(Self {
            r: parts.next()?.ok()?,
            g: parts.next()?.ok()?,
            b: parts.next()?.ok()?,
        })
    }
}

/// What osu! uses when a map states no colours of its own.
pub const DEFAULT_COMBO_COLOURS: &[Colour] = &[
    Colour {
        r: 255,
        g: 192,
        b: 0,
    },
    Colour { r: 0, g: 202, b: 0 },
    Colour {
        r: 18,
        g: 124,
        b: 255,
    },
    Colour {
        r: 242,
        g: 24,
        b: 57,
    },
];
