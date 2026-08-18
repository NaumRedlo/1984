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

/// A skin's sounds, as loaded from disk.
#[derive(Debug, Clone, Default)]
pub struct SamplePack {
    /// Keyed by the bank, the voice, and the *custom sample index* a map can
    /// switch to on a timing point. osu! writes the first set without a suffix
    /// and every one after it numbered — `soft-hitnormal.wav`,
    /// `soft-hitnormal2.wav` — and a map that asks for index 2 and is given
    /// index 1 plays the wrong sound rather than none, which is the kind of
    /// wrong nobody notices until they hear the map in the game.
    sounds: HashMap<(SampleSet, Voice, u32), Vec<f32>>,
}

/// The highest custom index worth looking for.
///
/// osu! puts no limit on it, and a folder scan would be the honest way to find
/// them — but a skin with a hundred banks is not a thing that exists, and this
/// keeps the load to a bounded number of files that are usually absent.
const MAX_INDEX: u32 = 9;

impl SamplePack {
    /// Read every `{set}-hit{sound}.wav` in `folder`.
    ///
    /// Missing files are not an error. Skins routinely leave out sounds they
    /// don't change, and a skin with only a clap should give you its clap and
    /// the engine's everything else.
    pub fn load(folder: &Path) -> Self {
        let mut sounds = HashMap::new();
        // A file that is there but holds nothing is a skin silencing an element
        // on purpose, and it is not the same as a file that is not there. osu!
        // reads it the same way: `ResourceStore.Get` hands back the first
        // result that is not null, and a blank file is `byte[0]` rather than
        // null — so the blank wins, and nothing is heard.
        //
        // Read as an empty sample rather than as an absence, or the fallback
        // below would go looking for another bank and, failing that, synthesise
        // the very sound the skin took the trouble to remove.
        let read = |path: &Path| -> Option<Vec<f32>> {
            let bytes = std::fs::read(path).ok()?;
            if bytes.is_empty() {
                return Some(Vec::new());
            }
            decode_wav(&bytes)
        };
        for set in SampleSet::ALL {
            for index in 1..=MAX_INDEX {
                // Index 1 is the unsuffixed file; everything after carries its
                // number. `soft-hitnormal.wav` and `soft-hitnormal1.wav` are
                // the same bank, and osu! writes the former.
                let suffix = if index == 1 {
                    String::new()
                } else {
                    index.to_string()
                };
                for (voice, name) in [
                    (Voice::Normal, "normal"),
                    (Voice::Whistle, "whistle"),
                    (Voice::Finish, "finish"),
                    (Voice::Clap, "clap"),
                ] {
                    let path =
                        folder.join(format!("{}-hit{name}{suffix}.wav", set.name()));
                    if let Some(samples) = read(&path) {
                        sounds.insert((set, voice, index), normalise(samples));
                    }
                }
                // Slider ticks have their own name and no `hit` in it, and so
                // do the two sounds a slider *holds*.
                for (voice, name) in [
                    (Voice::Tick, "slidertick"),
                    (Voice::Slide, "sliderslide"),
                    (Voice::SlideWhistle, "sliderwhistle"),
                ] {
                    let path = folder.join(format!("{}-{name}{suffix}.wav", set.name()));
                    if let Some(samples) = read(&path) {
                        // Not normalised. The others are struck and have to
                        // land at a comparable level whatever a skin recorded
                        // them at; these run underneath for seconds, and
                        // pushing a quiet loop up to full scale is the one way
                        // to make a background noise into a foreground one.
                        sounds.insert((set, voice, index), samples);
                    }
                }
            }
        }
        // The spinner's bonus chime belongs to no bank: osu! ships one
        // `spinnerbonus.wav` for the whole skin, with no set prefix and no
        // index. Filed under `Normal` so the lookup finds it whatever bank the
        // map was in when the spinner came round.
        if let Some(samples) = read(&folder.join("spinnerbonus.wav")) {
            sounds.insert((SampleSet::Normal, Voice::Bonus, 1), normalise(samples));
        }
        // And the spinner's own loop, which likewise belongs to no bank.
        if let Some(samples) = read(&folder.join("spinnerspin.wav")) {
            sounds.insert((SampleSet::Normal, Voice::Spin, 1), samples);
        }

        Self { sounds }
    }

    pub fn is_empty(&self) -> bool {
        self.sounds.is_empty()
    }

    pub fn len(&self) -> usize {
        self.sounds.len()
    }

    /// The sound for this voice, or `None` to fall back to synthesis.
    ///
    /// A set the skin doesn't carry defers to `Normal`, which is what osu!
    /// does: the normal set is the one a skin is guaranteed to define.
    /// A missing index falls back to the skin's first bank before the set
    /// falls back to `Normal` — asking for `soft-hitnormal7` and being handed
    /// `normal-hitnormal` skips over `soft-hitnormal`, which is much closer to
    /// what the mapper asked for.
    pub fn get(&self, set: SampleSet, voice: Voice, index: u32) -> Option<&[f32]> {
        let index = index.max(1);
        self.sounds
            .get(&(set, voice, index))
            .or_else(|| self.sounds.get(&(set, voice, 1)))
            .or_else(|| self.sounds.get(&(SampleSet::Normal, voice, index)))
            .or_else(|| self.sounds.get(&(SampleSet::Normal, voice, 1)))
            .map(Vec::as_slice)
    }
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
        // that is always there.
        let mut pack = SamplePack::default();
        pack.sounds
            .insert((SampleSet::Normal, Voice::Clap, 1), vec![0.5; 10]);
        assert!(pack.get(SampleSet::Drum, Voice::Clap, 1).is_some());
        assert!(pack.get(SampleSet::Drum, Voice::Finish, 1).is_none());
    }

    #[test]
    fn a_map_that_switches_banks_gets_the_bank_it_asked_for() {
        // The whole point of a custom index. A map can hold two sets of the
        // same voice and move between them on a timing point, and playing the
        // first for both is not silence — it is the wrong sound, which nobody
        // notices until they hear the map in the game.
        let mut pack = SamplePack::default();
        pack.sounds
            .insert((SampleSet::Soft, Voice::Normal, 1), vec![0.1; 4]);
        pack.sounds
            .insert((SampleSet::Soft, Voice::Normal, 2), vec![0.9; 4]);

        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 1).unwrap()[0], 0.1);
        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 2).unwrap()[0], 0.9);
        // Index 0 means "whatever the skin's first is", which osu! writes
        // without a suffix at all.
        assert_eq!(pack.get(SampleSet::Soft, Voice::Normal, 0).unwrap()[0], 0.1);
    }

    #[test]
    fn an_index_the_skin_lacks_stays_in_its_own_bank() {
        // Asking for `soft-hitnormal7` and being handed `normal-hitnormal`
        // skips over `soft-hitnormal`, which is much closer to what the mapper
        // asked for. The bank matters more than the number.
        let mut pack = SamplePack::default();
        pack.sounds
            .insert((SampleSet::Soft, Voice::Normal, 1), vec![0.1; 4]);
        pack.sounds
            .insert((SampleSet::Normal, Voice::Normal, 7), vec![0.9; 4]);

        assert_eq!(
            pack.get(SampleSet::Soft, Voice::Normal, 7).unwrap()[0],
            0.1,
            "its own bank first, whatever the index"
        );
    }
}
