//! Loading a real skin's hit sounds.
//!
//! Synthesis was never the goal — it was the answer to not being allowed to
//! ship osu!'s own samples. Pointed at a skin folder the engine uses what's
//! there, and falls back to synthesis only for sounds the skin doesn't have.
//! Nothing is copied into the repository: the samples stay someone else's work
//! on someone else's disk, and the engine is given a path to them.
//!
//! osu! names them `{set}-hit{sound}.wav`, where the set is normal, soft or
//! drum. Only the loading is here; which set a given note uses is a property of
//! the beatmap, and belongs upstairs.

use std::collections::HashMap;
use std::path::Path;

use crate::synth::Voice;
use crate::SAMPLE_RATE;

/// osu!'s three sample sets.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SampleSet {
    Normal,
    Soft,
    Drum,
}

impl SampleSet {
    pub const ALL: [Self; 3] = [Self::Normal, Self::Soft, Self::Drum];

    pub fn name(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::Soft => "soft",
            Self::Drum => "drum",
        }
    }
}

/// The sounds a render can play, in the two places osu! looks for them.
///
/// Not one store but two, because the game does not treat them alike. The
/// beatmap's own folder is asked first and is the **only** place a custom
/// sample index resolves; a user skin is never even shown the index.
///
/// ```csharp
/// // LegacySkin.getLegacyLookupNames
/// // - if the skin can use custom sample banks, it MUST use the custom sample
/// //   bank suffix. it is not allowed to fall back to a non-custom sound.
/// // - if the skin cannot use custom sample banks, it MUST NOT use the custom
/// //   sample bank suffix.
/// if (UseCustomSampleBanks)
///     lookupNames = lookupNames.Where(name => name.EndsWith(hitSample.Suffix));
/// else
///     lookupNames = lookupNames.Where(name => !name.EndsWith(hitSample.Suffix));
/// ```
///
/// `UseCustomSampleBanks` is `false` on `LegacySkin` and `true` on
/// `LegacyBeatmapSkin`, with the reason written beside it: "in stable, only the
/// beatmap skin could use samples with a custom sample bank". So a skin's
/// `soft-hitwhistle2.wav` is dead weight in the game, and a map that asks for
/// index 4 gets `soft-hitwhistle4` from its own folder or the plain
/// `soft-hitwhistle` from the skin — never the skin's numbered one.
#[derive(Debug, Clone, Default)]
pub struct SamplePack {
    /// The skin's, keyed by bank and voice. No index: the skin is never shown
    /// one, so there is nothing for a second file of the same voice to be.
    skin: HashMap<(SampleSet, Voice), Vec<f32>>,
    /// The beatmap's own, keyed by bank, voice and index. Unbounded — a map may
    /// number its banks as high as it likes, and the folder is scanned rather
    /// than probed so it does not matter how high.
    beatmap: HashMap<(SampleSet, Voice, u32), Vec<f32>>,
}

/// Every voice a skin or a map files under a bank, with the name it uses.
const BANKED: [(Voice, &str); 7] = [
    (Voice::Normal, "hitnormal"),
    (Voice::Whistle, "hitwhistle"),
    (Voice::Finish, "hitfinish"),
    (Voice::Clap, "hitclap"),
    (Voice::Tick, "slidertick"),
    (Voice::Slide, "sliderslide"),
    (Voice::SlideWhistle, "sliderwhistle"),
];

/// And the three that belong to no bank: osu! ships one apiece for a whole
/// skin, with no prefix and no index.
const BANKLESS: [(Voice, &str); 3] = [
    (Voice::Bonus, "spinnerbonus"),
    (Voice::Spin, "spinnerspin"),
    (Voice::Miss, "combobreak"),
];

impl SamplePack {
    /// A file that is there but holds nothing is a skin silencing an element on
    /// purpose, and it is not the same as a file that is not there. osu! reads
    /// it the same way: `ResourceStore.Get` hands back the first result that is
    /// not null, and a blank file is `byte[0]` rather than null — so the blank
    /// wins, and nothing is heard.
    ///
    /// Read as an empty sample rather than as an absence, or the fallbacks
    /// would go looking elsewhere and, failing that, synthesise the very sound
    /// somebody took the trouble to remove.
    fn read(path: &Path) -> Option<Vec<f32>> {
        let bytes = std::fs::read(path).ok()?;
        if bytes.is_empty() {
            return Some(Vec::new());
        }
        decode_wav(&bytes)
    }

    /// Read a skin's sounds from `folder`.
    ///
    /// Only the unsuffixed files, because those are the only ones the game will
    /// ever ask a skin for. Missing ones are not an error: skins routinely
    /// leave out sounds they don't change, and a skin with only a clap should
    /// give you its clap and the engine's everything else.
    pub fn load(folder: &Path) -> Self {
        let mut skin = HashMap::new();
        for set in SampleSet::ALL {
            for (voice, name) in BANKED {
                if let Some(samples) = Self::read(&folder.join(format!("{}-{name}.wav", set.name())))
                {
                    // Struck sounds are levelled; held ones are not. The others
                    // have to land at a comparable level whatever a skin
                    // recorded them at, while these run underneath for seconds,
                    // and pushing a quiet loop to full scale is the one way to
                    // make a background noise into a foreground one.
                    let held = matches!(voice, Voice::Slide | Voice::SlideWhistle);
                    skin.insert(
                        (set, voice),
                        if held { samples } else { normalise(samples) },
                    );
                }
            }
        }
        for (voice, name) in BANKLESS {
            if let Some(samples) = Self::read(&folder.join(format!("{name}.wav"))) {
                let held = voice == Voice::Spin;
                skin.insert(
                    (SampleSet::Normal, voice),
                    if held { samples } else { normalise(samples) },
                );
            }
        }
        Self {
            skin,
            beatmap: HashMap::new(),
        }
    }

    /// Add the beatmap's own sounds, which is where a custom index resolves.
    ///
    /// The folder is listed rather than probed. A map may number its banks as
    /// high as it likes — the one this was written against goes to six, and
    /// nothing stops it going to sixty — so guessing an upper bound would be
    /// guessing at somebody else's file names.
    #[must_use]
    pub fn with_beatmap(mut self, folder: &Path) -> Self {
        let Ok(entries) = std::fs::read_dir(folder) else {
            return self;
        };
        for entry in entries.flatten() {
            let leaf = entry.file_name();
            let Some(name) = leaf.to_str() else { continue };
            let Some(stem) = name.strip_suffix(".wav").map(str::to_ascii_lowercase) else {
                continue;
            };
            let Some((set, voice, index)) = parse_sample_name(&stem) else {
                continue;
            };
            if let Some(samples) = Self::read(&entry.path()) {
                let held = matches!(voice, Voice::Slide | Voice::SlideWhistle | Voice::Spin);
                self.beatmap.insert(
                    (set, voice, index),
                    if held { samples } else { normalise(samples) },
                );
            }
        }
        self
    }

    pub fn is_empty(&self) -> bool {
        self.skin.is_empty() && self.beatmap.is_empty()
    }

    /// How many sounds came from the skin, which is what a caller reports when
    /// it names the skin folder it read.
    pub fn len(&self) -> usize {
        self.skin.len()
    }

    /// And how many came from the map.
    pub fn from_beatmap(&self) -> usize {
        self.beatmap.len()
    }

    /// The sound for this voice, or `None` to fall back to synthesis.
    ///
    /// The order is the game's. The beatmap is asked first and is the only
    /// place the index means anything; a custom index may not fall back to the
    /// plain name *within* the beatmap, because
    /// `lookupNames.Where(name => name.EndsWith(suffix))` leaves nothing else
    /// to try. Then the skin, which is asked for the plain name whatever the
    /// index was.
    ///
    /// The one liberty taken is the last step: a bank the skin does not carry
    /// defers to `Normal` rather than to the game's own default sounds, which
    /// this engine does not have. Handing back the skin's `normal-hitwhistle`
    /// is closer to what somebody chose the skin for than a synthesised one.
    pub fn get(&self, set: SampleSet, voice: Voice, index: u32) -> Option<&[f32]> {
        let index = index.max(1);
        if let Some(sound) = self.beatmap.get(&(set, voice, index)) {
            return Some(sound);
        }
        self.skin
            .get(&(set, voice))
            .or_else(|| self.skin.get(&(SampleSet::Normal, voice)))
            .map(Vec::as_slice)
    }
}

/// Split `soft-hitwhistle4` into its bank, its voice and its index.
///
/// `None` for anything that is not a sample name — a folder holds a song, a
/// background and whatever else besides.
fn parse_sample_name(stem: &str) -> Option<(SampleSet, Voice, u32)> {
    let (bank, rest) = stem.split_once('-')?;
    let set = SampleSet::ALL.into_iter().find(|s| s.name() == bank)?;
    let (voice, name) = BANKED.into_iter().find(|(_, n)| rest.starts_with(n))?;
    let digits = &rest[name.len()..];
    // The unsuffixed file is index 1: osu! writes the first set without a
    // number and every one after it numbered.
    let index = if digits.is_empty() {
        1
    } else {
        digits.parse().ok()?
    };
    Some((set, voice, index))
}

/// Bring a sample to a known peak.
///
/// Skins are mastered to no particular standard — one may sit at half scale
/// and the next at the ceiling. Levelling them here means the mix balance that
/// was tuned once holds for every skin, instead of every skin needing its own.
fn normalise(mut samples: Vec<f32>) -> Vec<f32> {
    const TARGET: f32 = 0.9;
    let peak = samples.iter().fold(0.0f32, |worst, s| worst.max(s.abs()));
    if peak > 1e-6 {
        let scale = TARGET / peak;
        for sample in &mut samples {
            *sample *= scale;
        }
    }
    samples
}

/// Decode a PCM `.wav` to mono `f32`.
///
/// Handles what skins actually contain: 8-bit unsigned and 16-bit signed, mono
/// or stereo. Anything else — compressed, 24-bit, float — returns `None` and
/// falls back to synthesis, which beats playing noise at someone.
pub fn decode_wav(bytes: &[u8]) -> Option<Vec<f32>> {
    if bytes.len() < 12 || &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return None;
    }

    let mut channels = 0u16;
    let mut rate = 0u32;
    let mut bits = 0u16;
    let mut format = 0u16;
    let mut data: Option<&[u8]> = None;

    // Chunks are not in a guaranteed order and there may be extras between
    // them, so walk rather than assume the usual layout.
    let mut cursor = 12usize;
    while cursor + 8 <= bytes.len() {
        let id = &bytes[cursor..cursor + 4];
        let size = u32::from_le_bytes(bytes[cursor + 4..cursor + 8].try_into().ok()?) as usize;
        let body = bytes.get(cursor + 8..cursor + 8 + size)?;

        match id {
            b"fmt " if size >= 16 => {
                format = u16::from_le_bytes(body[0..2].try_into().ok()?);
                channels = u16::from_le_bytes(body[2..4].try_into().ok()?);
                rate = u32::from_le_bytes(body[4..8].try_into().ok()?);
                bits = u16::from_le_bytes(body[14..16].try_into().ok()?);
            }
            b"data" => data = Some(body),
            _ => {}
        }
        // Chunks are word-aligned; an odd size is followed by a pad byte.
        cursor += 8 + size + (size & 1);
    }

    let data = data?;
    if format != 1 || channels == 0 {
        return None;
    }

    let mono: Vec<f32> = match bits {
        8 => data
            .chunks_exact(channels as usize)
            // 8-bit wav is unsigned, centred on 128.
            .map(|frame| {
                frame
                    .iter()
                    .map(|&s| (f32::from(s) - 128.0) / 128.0)
                    .sum::<f32>()
                    / f32::from(channels)
            })
            .collect(),
        16 => data
            .chunks_exact(2 * channels as usize)
            .map(|frame| {
                frame
                    .chunks_exact(2)
                    .map(|s| f32::from(i16::from_le_bytes([s[0], s[1]])) / 32_768.0)
                    .sum::<f32>()
                    / f32::from(channels)
            })
            .collect(),
        _ => return None,
    };

    Some(resample(mono, rate))
}

/// Bring a sample to the engine's rate.
///
/// Linear interpolation. For a percussive one-shot of a few hundred
/// milliseconds the difference against anything fancier is inaudible, and most
/// skins are 44.1kHz already, so this usually does nothing at all.
fn resample(samples: Vec<f32>, from_rate: u32) -> Vec<f32> {
    if from_rate == SAMPLE_RATE || from_rate == 0 || samples.is_empty() {
        return samples;
    }
    let ratio = f64::from(SAMPLE_RATE) / f64::from(from_rate);
    let out_len = (samples.len() as f64 * ratio) as usize;

    (0..out_len)
        .map(|i| {
            let source = i as f64 / ratio;
            let index = source as usize;
            let fraction = (source - index as f64) as f32;
            let a = samples.get(index).copied().unwrap_or(0.0);
            let b = samples.get(index + 1).copied().unwrap_or(a);
            a + (b - a) * fraction
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a WAV in memory so the decoder can be tested without files.
    fn wav(channels: u16, bits: u16, rate: u32, frames: &[i16]) -> Vec<u8> {
        let mut data = Vec::new();
        for &frame in frames {
            for _ in 0..channels {
                match bits {
                    8 => data.push(((frame >> 8) as i32 + 128).clamp(0, 255) as u8),
                    _ => data.extend_from_slice(&frame.to_le_bytes()),
                }
            }
        }

        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data.len() as u32).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16u32.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&channels.to_le_bytes());
        out.extend_from_slice(&rate.to_le_bytes());
        out.extend_from_slice(&(rate * u32::from(channels) * u32::from(bits / 8)).to_le_bytes());
        out.extend_from_slice(&(channels * bits / 8).to_le_bytes());
        out.extend_from_slice(&bits.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&data);
        out
    }

    /// A folder of samples, named as osu! names them.
    fn skin(files: &[(&str, Option<&[i16]>)]) -> std::path::PathBuf {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let dir = std::env::temp_dir().join(format!(
            "dossier-pack-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");
        for (leaf, frames) in files {
            // `None` is the blank a skin uses to remove an element.
            let body = frames.map_or_else(Vec::new, |f| wav(1, 16, SAMPLE_RATE, f));
            std::fs::write(dir.join(format!("{leaf}.wav")), body).expect("a file");
        }
        dir
    }

    const LOUD: &[i16] = &[16_384, -16_384, 16_384];

    #[test]
    fn sixteen_bit_mono_decodes_to_the_samples_that_went_in() {
        let decoded = decode_wav(&wav(1, 16, SAMPLE_RATE, &[0, 16_384, -16_384])).unwrap();
        assert_eq!(decoded.len(), 3);
        assert!((decoded[1] - 0.5).abs() < 0.001);
        assert!((decoded[2] + 0.5).abs() < 0.001);
    }

    #[test]
    fn stereo_is_folded_to_mono() {
        // Hit sounds are centred; carrying two identical channels through the
        // mixing would double the work for nothing.
        let decoded = decode_wav(&wav(2, 16, SAMPLE_RATE, &[16_384])).unwrap();
        assert_eq!(decoded.len(), 1);
        assert!((decoded[0] - 0.5).abs() < 0.001);
    }

    #[test]
    fn eight_bit_is_read_as_unsigned() {
        // The one format detail that silently produces garbage if missed:
        // 8-bit wav is centred on 128, not on zero.
        let decoded = decode_wav(&wav(1, 8, SAMPLE_RATE, &[0])).unwrap();
        assert!(
            decoded[0].abs() < 0.01,
            "silence came out as {}",
            decoded[0]
        );
    }

    #[test]
    fn a_different_rate_is_resampled_to_ours() {
        let decoded = decode_wav(&wav(1, 16, SAMPLE_RATE / 2, &[0; 100])).unwrap();
        assert!(
            (decoded.len() as i32 - 200).abs() <= 1,
            "got {} samples",
            decoded.len()
        );
    }

    #[test]
    fn nonsense_is_refused_rather_than_played() {
        assert!(decode_wav(b"not a wav at all").is_none());
        assert!(decode_wav(&[]).is_none());
        // 24-bit is valid wav but not something we read.
        assert!(decode_wav(&wav(1, 24, SAMPLE_RATE, &[0])).is_none());
    }

    #[test]
    fn loading_a_folder_that_is_not_there_gives_an_empty_pack() {
        // A wrong path should mean "no samples", not a crash halfway through a
        // render.
        let pack = SamplePack::load(Path::new("/nowhere/at/all"));
        assert!(pack.is_empty());
        assert!(pack.get(SampleSet::Normal, Voice::Normal, 1).is_none());
    }

    #[test]
    fn a_missing_set_falls_back_to_normal() {
        // Skins commonly define only the normal set; osu! treats it as the one
        // that is always there. The liberty here is where it defers *to* — the
        // game would reach its own default sounds, which this engine does not
        // have, and the skin's normal bank is closer to what somebody chose the
        // skin for than something synthesised.
        let pack = SamplePack::load(&skin(&[("normal-hitclap", Some(LOUD))]));
        assert!(pack.get(SampleSet::Drum, Voice::Clap, 1).is_some());
        assert!(pack.get(SampleSet::Drum, Voice::Finish, 1).is_none());
    }

    #[test]
    fn a_map_that_switches_banks_gets_the_bank_it_asked_for() {
        // The whole point of a custom index, and the whole reason a map ships
        // sounds at all. A map holds two sets of the same voice and moves
        // between them on a timing point; playing the first for both is not
        // silence, it is the wrong sound.
        let dir = skin(&[
            ("soft-hitnormal", Some(&[4_000])),
            ("soft-hitnormal2", Some(&[4_000, 4_000])),
            ("soft-hitnormal6", Some(&[4_000, 4_000, 4_000, 4_000, 4_000, 4_000])),
        ]);
        let pack = SamplePack::default().with_beatmap(&dir);

        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 1).unwrap().len(), 1);
        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 2).unwrap().len(), 2);
        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 6).unwrap().len(), 6);
        // Index 0 means "whatever the first is", which osu! writes without a
        // suffix at all.
        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 0).unwrap().len(), 1);
    }

    #[test]
    fn a_skins_numbered_file_is_never_reached() {
        // `soft-hitwhistle2.wav` in a *skin* is dead weight, and this is the
        // thing that took longest to find. `UseCustomSampleBanks` is false on
        // `LegacySkin` and true on `LegacyBeatmapSkin` — "in stable, only the
        // beatmap skin could use samples with a custom sample bank" — so the
        // suffix is stripped before a skin is ever asked, and what answers is
        // the plain name. Here that is a blank, and a blank means silence.
        let dir = skin(&[
            ("soft-hitwhistle", None),
            ("soft-hitwhistle2", Some(LOUD)),
        ]);
        let pack = SamplePack::load(&dir);
        for asked in [1, 2, 4] {
            let got = pack.get(SampleSet::Soft, Voice::Whistle, asked);
            assert!(got.is_some(), "silence, not synthesis, at index {asked}");
            assert!(
                got.unwrap().is_empty(),
                "index {asked} reached the skin's numbered file"
            );
        }
    }

    #[test]
    fn the_map_is_asked_before_the_skin() {
        let map = skin(&[("soft-hitwhistle4", Some(&[4_000, 4_000, 4_000]))]);
        let dressed = skin(&[("soft-hitwhistle", Some(&[4_000]))]);
        let pack = SamplePack::load(&dressed).with_beatmap(&map);

        assert_eq!(
            pack.get(SampleSet::Soft, Voice::Whistle, 4).unwrap().len(),
            3,
            "the map's own numbered sound"
        );
        // And an index the map does not carry falls to the skin's plain name,
        // which is exactly what the game does: the suffix is stripped on the
        // way to a skin, so there is nothing else left to try.
        assert_eq!(
            pack.get(SampleSet::Soft, Voice::Whistle, 5).unwrap().len(),
            1,
            "the skin's plain sound"
        );
    }

    #[test]
    fn a_voice_nobody_carries_is_left_to_synthesis() {
        let pack = SamplePack::load(&skin(&[("soft-hitclap", Some(LOUD))]));
        assert!(pack.get(SampleSet::Soft, Voice::Whistle, 1).is_none());
    }

    #[test]
    fn a_name_that_is_not_a_sample_is_not_read_as_one() {
        assert_eq!(
            parse_sample_name("soft-hitwhistle4"),
            Some((SampleSet::Soft, Voice::Whistle, 4))
        );
        assert_eq!(
            parse_sample_name("drum-sliderslide"),
            Some((SampleSet::Drum, Voice::Slide, 1))
        );
        // A folder holds a song and a background besides.
        assert_eq!(parse_sample_name("audio"), None);
        assert_eq!(parse_sample_name("bg"), None);
        assert_eq!(parse_sample_name("taiko-normal-hitclap"), None);
    }

}
