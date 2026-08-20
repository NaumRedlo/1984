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
    /// The skin's, keyed by bank and voice. No index, because no client ever
    /// shows a skin one: the suffix is stripped on the way in.
    ///
    /// ```csharp
    /// // LegacySkin.getLegacyLookupNames, with UseCustomSampleBanks false
    /// lookupNames = lookupNames.Where(name => !name.EndsWith(hitSample.Suffix));
    /// ```
    ///
    /// danser is the same shape — `Samples[set][voice]` against
    /// `MapSamples[set][voice][index]` — so a skin's `soft-hitwhistle2.wav` is
    /// dead weight in all three. It was read here for a while, on the grounds
    /// that a skin shipping one plainly meant it to be heard; that was one row
    /// out of thirty on a real play and not worth being the only place this
    /// engine disagrees with every client at once.
    skin: HashMap<(SampleSet, Voice), Vec<f32>>,
    /// Audio the folder holds that no voice is filed under.
    ///
    /// Most of it is a skin's menu — `menuhit`, `key-press-1`, `applause` —
    /// which never sounds during a play and which osu! would not reach either.
    /// The rest is names the game has no meaning for, and those are worth
    /// saying out loud: a skin whose `normal-hitwistle.wav` is a typo has a
    /// sound its author expected to hear and nobody ever will.
    unused: Vec<String>,
    /// The beatmap's own, keyed by bank, voice and index. Unbounded — a map may
    /// number its banks as high as it likes, and the folder is scanned rather
    /// than probed so it does not matter how high.
    beatmap: HashMap<(SampleSet, Voice, u32), Vec<f32>>,
    /// osu!'s own sounds, which is where the game's lookup ends.
    ///
    /// Keyed like the skin's and read exactly the same way, because it *is* a
    /// skin — the one the game ships. A skin that leaves a sound out does not
    /// go quiet in osu! and does not borrow another bank's: the chain runs
    /// beatmap, then skin, then this, each asked for the same name.
    ///
    /// Empty unless somebody points at a folder. The files are ppy's and are
    /// not this engine's to carry, so they come from whoever runs it — see
    /// `tools/stable.py assets`, which pulls all twenty-one banked voices out
    /// of `osu!gameplay.dll` as plain WAVs.
    game: HashMap<(SampleSet, Voice), Vec<f32>>,
}

/// Where a sound came from, once the lookup has run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Found {
    /// The map's own file at the index that was asked for.
    Beatmap(u32),
    /// The skin's unnumbered file, the index having none of its own — which is
    /// what the game does, since it never shows a skin an index at all.
    SkinPlain,
    /// osu!'s own, the skin having no such file — which is where the game's
    /// own lookup ends.
    Game,
    /// The skin's `normal` bank, the asked-for one carrying nothing.
    SkinNormalBank,
    /// A file that is there and holds nothing: somebody removed the sound.
    Blank,
    /// Nothing anywhere, so the engine invents one. Where the game would part
    /// company with this is [`Self::Game`] — with no such folder to point at,
    /// a sound nobody supplied is invented instead.
    Synthesised,
}

impl Found {
    /// A few words for a report.
    pub fn describe(self) -> String {
        match self {
            Self::Beatmap(at) => format!("the map's, index {at}"),
            Self::SkinPlain => "the skin's, unnumbered".to_owned(),
            Self::Game => "osu!'s own".to_owned(),
            Self::SkinNormalBank => "the skin's normal bank".to_owned(),
            Self::Blank => "blank — the skin removed it".to_owned(),
            Self::Synthesised => "nothing anywhere — synthesised".to_owned(),
        }
    }
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
    /// Listed rather than probed, and numbered files are kept — see the field.
    /// Missing ones are not an error: skins routinely leave out sounds they
    /// don't change, and a skin with only a clap should give you its clap and
    /// the engine's everything else.
    pub fn load(folder: &Path) -> Self {
        // Everything below asks this rather than the filesystem, so a skin that
        // capitalises a name is read the way osu! reads it.
        let files = index_of(folder);
        let mut skin = HashMap::new();
        let mut numbered = Vec::new();
        for ((set, voice, index), samples) in banked_in(folder) {
            if index == 1 {
                skin.insert((set, voice), samples);
            } else {
                // Not a skin's to offer — no client shows one an index. Named
                // in the report rather than dropped in silence, because a skin
                // that ships a whole numbered set plainly expected it to play.
                numbered.push(format!("{}-{}{index}", set.name(), voice.file_name()));
            }
        }
        for (voice, name) in BANKLESS {
            let found = files
                .get(&format!("{name}.wav"))
                .and_then(|path| Self::read(path));
            if let Some(samples) = found {
                skin.insert((SampleSet::Normal, voice), samples);
            }
        }

        // Then the near misses, and only into slots that would otherwise make
        // no sound. Skins are hand-made folders and they carry slips of the
        // finger — `normal-hitwistle`, `softl-hitfinish`, a doubled dash — and
        // each one is a sound its author expected to hear.
        //
        // An exact name always wins, which is the whole safety of this:
        // `drum--hitwhistle.wav` in `azr8` is a *blank* lying beside a real
        // `drum-hitwhistle.wav`, and taking the typo for the name would
        // silence the sound next to it.
        //
        // A guess may only fill a slot the skin has **nothing** under — blank
        // included. A blank is not a sound that went missing; it is a sound its
        // author deleted, and the two want opposite treatment. The guess is for
        // a folder that has `normal-hitwistle` and no `normal-hitwhistle` at
        // all; a folder that has an empty one has already said what it wants.
        //
        // `azr8` is why this is not a hypothetical. It stubs every whistle it
        // has — `normal-hitwhistle`, `soft-hitwhistle`, both slider whistles —
        // at forty-four bytes apiece, and parks its real recordings under names
        // the game does not read: `normal-hitwistle`, `soft-hitwhistle2`. In
        // osu! that skin has no whistles, which was checked in the client
        // against a replay. Filling the blank from the typo put a sound into
        // every render that the game is silent for.
        let mut guessed = Vec::new();
        for name in unfiled_in(folder) {
            let Some((set, voice, index)) = guess_sample_name(&name) else {
                guessed.push(name);
                continue;
            };
            let key = (set, voice);
            if index != 1 || skin.contains_key(&key) {
                guessed.push(name);
                continue;
            }
            match files
                .get(&format!("{name}.wav"))
                .and_then(|path| Self::read(path))
            {
                Some(samples) if !samples.is_empty() => {
                    skin.insert(key, samples);
                }
                _ => guessed.push(name),
            }
        }

        guessed.extend(numbered);
        guessed.sort();
        Self {
            unused: guessed,
            skin,
            beatmap: HashMap::new(),
            game: HashMap::new(),
        }
    }

    /// Audio in the skin that no voice was filed under, by name.
    ///
    /// For a caller that wants to say so. A skin is somebody's folder and the
    /// only honest answer to "where did my hit sound go" is the list.
    pub fn unused(&self) -> &[String] {
        &self.unused
    }

    /// Add the beatmap's own sounds, which is where a custom index resolves.
    ///
    /// The folder is listed rather than probed. A map may number its banks as
    /// high as it likes — the one this was written against goes to six, and
    /// nothing stops it going to sixty — so guessing an upper bound would be
    /// guessing at somebody else's file names.
    #[must_use]
    pub fn with_beatmap(mut self, folder: &Path) -> Self {
        for (name, samples) in banked_in(folder) {
            self.beatmap.insert(name, samples);
        }
        self
    }

    /// osu!'s own sounds, from a folder laid out the way a skin is.
    ///
    /// Read by exactly the code that reads a skin, because that is what it is:
    /// `normal-hitwhistle.wav` beside `soft-hitclap.wav`, under whatever
    /// capitalisation. `tools/stable.py assets` writes such a folder out of a
    /// client's own `osu!gameplay.dll`; the twenty-one banked voices are plain
    /// WAVs there and need nothing converting.
    ///
    /// Only the banked sounds move across. The near-miss guessing that
    /// [`Self::load`] does for a hand-made skin is left behind with it — a
    /// typo is a thing a skinner does, and the game's own folder has none.
    pub fn with_game_sounds(mut self, folder: &Path) -> Self {
        self.game = Self::load(folder).skin;
        self
    }

    /// How many of osu!'s own it holds.
    pub fn from_game(&self) -> usize {
        self.game.len()
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

    /// Where a lookup landed, for a caller that wants to say so.
    ///
    /// The same order as [`Self::get`], step for step. Kept beside it rather
    /// than inferred afterwards: the whole point is to answer "why is this note
    /// silent", and an answer worked out by a second, similar-looking function
    /// is an answer about that function.
    pub fn trace(&self, set: SampleSet, voice: Voice, index: u32) -> Found {
        let index = index.max(1);
        if let Some(sound) = self.beatmap.get(&(set, voice, index)) {
            return if sound.is_empty() {
                Found::Blank
            } else {
                Found::Beatmap(index)
            };
        }
        for (store, at, step) in [
            (&self.skin, (set, voice), Found::SkinPlain),
            (&self.game, (set, voice), Found::Game),
            (&self.skin, (SampleSet::Normal, voice), Found::SkinNormalBank),
        ] {
            if let Some(sound) = store.get(&at) {
                return if sound.is_empty() { Found::Blank } else { step };
            }
        }
        Found::Synthesised
    }

    /// The sound for this voice, or `None` to fall back to synthesis.
    ///
    /// The order is the game's. The beatmap is asked first and is the only
    /// place the index means anything; a custom index may not fall back to the
    /// plain name *within* the beatmap, because
    /// `lookupNames.Where(name => name.EndsWith(suffix))` leaves nothing else
    /// to try. Then the skin, which is asked for the plain name whatever the
    /// index was, because that is the only name a skin is ever shown. Then
    /// osu!'s own sounds, which is where the game stops looking.
    ///
    /// A blank at any step wins and the search ends there. That is not a quirk
    /// of this function but the whole grammar of a skin: a file that exists and
    /// holds nothing is somebody removing a sound, and a search that carried on
    /// past it would put back what they removed.
    ///
    /// The last step is the one liberty, and it only fires when nobody has
    /// supplied osu!'s own folder: a bank the skin does not carry defers to
    /// `Normal`, because handing back the skin's `normal-hitwhistle` is closer
    /// to what somebody chose the skin for than a synthesised one. With the
    /// game's sounds in place the step above it always answers first, and this
    /// engine agrees with osu! rather than approximating it.
    pub fn get(&self, set: SampleSet, voice: Voice, index: u32) -> Option<&[f32]> {
        let index = index.max(1);
        if let Some(sound) = self.beatmap.get(&(set, voice, index)) {
            return Some(sound);
        }
        self.skin
            .get(&(set, voice))
            .or_else(|| self.game.get(&(set, voice)))
            .or_else(|| self.skin.get(&(SampleSet::Normal, voice)))
            .map(Vec::as_slice)
    }
}

/// Every `{bank}-{voice}{index}.wav` in a folder, by the key it is filed under.
///
/// Listed rather than probed. A map may number its banks as high as it likes —
/// the one this was written against goes to six — so guessing an upper bound
/// would be guessing at somebody else's file names.
/// Every file in `folder`, keyed by its name in lower case.
///
/// osu! grew up on Windows, where the filesystem does not care about case, and
/// skinners and mappers lean on that: `Soft-HitClap.WAV` and
/// `normal-hitnormal.Wav` are both real things to find in a folder. A lookup
/// that asks for the exact lower-case name finds neither on a filesystem that
/// does care — which is to say, on the server, while the machine this was
/// written on finds them both and says nothing.
///
/// Built once per folder rather than asked per name: a skin is a few hundred
/// files and a pack is loaded once per render.
fn index_of(folder: &Path) -> std::collections::HashMap<String, std::path::PathBuf> {
    let mut index = std::collections::HashMap::new();
    let Ok(entries) = std::fs::read_dir(folder) else {
        return index;
    };
    for entry in entries.flatten() {
        if entry.file_type().is_ok_and(|kind| kind.is_file()) {
            let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
            index.insert(name, entry.path());
        }
    }
    index
}

fn unfiled_in(folder: &Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return Vec::new();
    };
    let mut out: Vec<String> = entries
        .flatten()
        .filter_map(|entry| {
            // Lowered *before* the suffix is taken off, not after. A skin
            // shipping `Soft-HitClap.WAV` is ordinary, and stripping `.wav`
            // from it first fails and drops the file without a word.
            let leaf = entry.file_name().to_string_lossy().to_ascii_lowercase();
            let stem = leaf.strip_suffix(".wav")?.to_owned();
            let bankless = BANKLESS.iter().any(|(_, name)| *name == stem);
            (parse_sample_name(&stem).is_none() && !bankless).then_some(stem)
        })
        .collect();
    out.sort();
    out
}

fn banked_in(folder: &Path) -> Vec<((SampleSet, Voice, u32), Vec<f32>)> {
    let Ok(entries) = std::fs::read_dir(folder) else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter_map(|entry| {
            // Lowered first, for the same reason as above.
            let leaf = entry.file_name().to_string_lossy().to_ascii_lowercase();
            let stem = leaf.strip_suffix(".wav")?;
            let key = parse_sample_name(stem)?;
            Some((key, SamplePack::read(&entry.path())?))
        })
        .collect()
}

/// The slot a *near* miss was probably meant for.
///
/// Only ever consulted after every exact name has been filed, and only allowed
/// to fill a slot that would otherwise be silent — see [`SamplePack::load`].
/// What it forgives is what hand-made folders actually contain: a doubled
/// separator, a letter dropped or added, and a trailing scribble on the end of
/// a voice (`soft-hitnormalh`, which is somebody's second take).
fn guess_sample_name(stem: &str) -> Option<(SampleSet, Voice, u32)> {
    // `drum--hitwhistle` and `drum-hitwhistle` are one typo apart, and the typo
    // is in the separator rather than in either word.
    let mut squeezed = String::with_capacity(stem.len());
    for ch in stem.chars() {
        if ch == '-' && squeezed.ends_with('-') {
            continue;
        }
        squeezed.push(ch);
    }
    let (bank, rest) = squeezed.split_once('-')?;
    let set = SampleSet::ALL
        .into_iter()
        .find(|s| bank == s.name() || bank.starts_with(s.name()) || one_edit_apart(bank, s.name()))?;

    // A trailing number is an index wherever it appears; anything else trailing
    // is scribble, and the voice in front of it is what was meant.
    let digits = rest.len() - rest.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    let (word, tail) = rest.split_at(rest.len() - digits);
    let index = if tail.is_empty() { 1 } else { tail.parse().ok()? };
    let (voice, _) = BANKED.into_iter().find(|(_, name)| {
        word == *name || word.starts_with(name) || one_edit_apart(word, name)
    })?;
    Some((set, voice, index))
}

/// Whether one insertion, deletion or substitution turns `a` into `b`.
fn one_edit_apart(a: &str, b: &str) -> bool {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    if a.len().abs_diff(b.len()) > 1 {
        return false;
    }
    // Walk both, and allow exactly one place where they disagree: on a
    // substitution both advance, on an insertion only the longer one does.
    let (long, short) = if a.len() >= b.len() { (&a, &b) } else { (&b, &a) };
    let mut skipped = false;
    let (mut i, mut j) = (0usize, 0usize);
    while i < long.len() && j < short.len() {
        if long[i] == short[j] {
            i += 1;
            j += 1;
            continue;
        }
        if skipped {
            return false;
        }
        skipped = true;
        i += 1;
        if long.len() == short.len() {
            j += 1;
        }
    }
    true
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
    // The unsuffixed file is index 1, and a file written `…1` is not.
    //
    // ```csharp
    // suffix: customSampleBank >= 2 ? customSampleBank.ToString() : null,
    // ```
    //
    // Index 1 carries *no suffix at all*, so `soft-hitnormal1.wav` is a name
    // nothing ever asks for — not through a skin, which is never shown an
    // index, and not through a beatmap, where index 1 resolves to the plain
    // name. Reading it as index 1 put it in the same slot as the real
    // `soft-hitnormal.wav` and let the directory walk decide which survived,
    // which is a coin toss that lands differently on different machines.
    // `vv_idke_trail` ships both, and they are not the same recording.
    //
    // Zero is the same story from the other end: `customSampleBank` 0 means
    // the map's own folder is not consulted at all, and it is never written
    // into a filename either.
    let index = if digits.is_empty() {
        1
    } else {
        match digits.parse().ok()? {
            0 | 1 => return None,
            n => n,
        }
    };
    Some((set, voice, index))
}

// Samples are played at the level they were recorded at, and this is where a
// function that changed that used to be.
//
// Everything a skin or a map ships was mixed by somebody against everything
// else they shipped: a clap two decibels under the plain hit is a decision,
// and so is a tick at a third of it. Levelling each one to a common peak, and
// then laying the synthesiser's own per-voice balance over the top, replaced
// that decision with ours — which is most of what "the sounds are completely
// different from the client" turned out to mean.
//
// osu! does neither. A sample plays as recorded, scaled by the volume the map
// asks for on the timing point or the note, and nothing else. So does this.
// The synthesised kit keeps its balance, because there it *is* the design —
// see [`Voice::gain`], which is now applied to nothing else.

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

    // ── osu!'s own sounds, which is where the game's lookup ends ────────
    //
    // Read out of `osu!gameplay.dll` — see `tools/stable.py` and
    // `docs/stable-client.md`. The client ships all three banks and all seven
    // voices with no gaps, so in the game a skin's omission is never heard as
    // an omission. This engine had no such folder and said so in a comment for
    // a long time; these four say what changes now that it can have one.

    /// A distinct waveform, so a test can say *which* file was heard rather
    /// than only that something was.
    const QUIET: &[i16] = &[2_048, -2_048, 2_048];

    #[test]
    fn a_sound_the_skin_leaves_out_comes_from_osu_rather_than_another_bank() {
        // The skin has a normal whistle and no soft one. Before this, a soft
        // whistle fetched the skin's *normal* whistle — a liberty taken because
        // there was nothing better to reach for. There is now.
        let dressed = skin(&[("normal-hitwhistle", Some(LOUD))]);
        let osu = skin(&[
            ("normal-hitwhistle", Some(LOUD)),
            ("soft-hitwhistle", Some(QUIET)),
        ]);
        let pack = SamplePack::load(&dressed).with_game_sounds(&osu);
        let heard = pack.get(SampleSet::Soft, Voice::Whistle, 1).expect("a sound");
        assert!(heard[0] < 0.2, "the skin's normal bank was used instead");
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Whistle, 1), Found::Game);
    }

    #[test]
    fn a_sound_the_skin_removed_stays_removed() {
        // The whole grammar of a skin: a file that is there and holds nothing
        // is somebody taking a sound away. Laying osu!'s own underneath must
        // not put back what they deleted — the search ends at the blank.
        let silenced = skin(&[("soft-hitwhistle", None)]);
        let osu = skin(&[("soft-hitwhistle", Some(LOUD))]);
        let pack = SamplePack::load(&silenced).with_game_sounds(&osu);
        assert_eq!(
            pack.get(SampleSet::Soft, Voice::Whistle, 1),
            Some(&[][..]),
            "a deliberate blank was overruled"
        );
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Whistle, 1), Found::Blank);
    }

    #[test]
    fn the_skin_and_the_map_both_still_come_first() {
        // Order, not preference: the map is asked before the skin and the skin
        // before osu!, and a folder laid underneath cannot reorder that.
        let dressed = skin(&[("soft-hitwhistle", Some(LOUD))]);
        let osu = skin(&[("soft-hitwhistle", Some(QUIET))]);
        let pack = SamplePack::load(&dressed).with_game_sounds(&osu);
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Whistle, 1), Found::SkinPlain);
        assert!(pack.get(SampleSet::Soft, Voice::Whistle, 1).expect("a sound")[0] > 0.4);

        let map = skin(&[("soft-hitwhistle4", Some(&[8_192, -8_192, 8_192]))]);
        let pack = pack.with_beatmap(&map);
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Whistle, 4), Found::Beatmap(4));
    }

    #[test]
    fn without_that_folder_the_old_liberty_is_still_taken() {
        // Nobody has to supply osu!'s files — they are ppy's, and a deployment
        // without them has to keep working. There the normal bank is still a
        // better answer than a synthesised sound.
        let pack = SamplePack::load(&skin(&[("normal-hitwhistle", Some(LOUD))]));
        assert_eq!(
            pack.trace(SampleSet::Soft, Voice::Whistle, 1),
            Found::SkinNormalBank
        );
    }

    #[test]
    fn a_file_written_with_a_one_is_not_the_plain_sound() {
        // ```csharp
        // suffix: customSampleBank >= 2 ? customSampleBank.ToString() : null,
        // ```
        //
        // Index 1 carries no suffix, so `soft-hitnormal1.wav` is a name nothing
        // asks for. Read as index 1 it landed in the same slot as the real
        // `soft-hitnormal.wav`, and which of the two survived was decided by
        // the order the directory happened to be walked in.
        //
        // `vv_idke_trail` ships both and they are different recordings. On the
        // machine this was found on the numbered one won, so every soft note in
        // every render was struck with a file the game never opens.
        let dir = skin(&[
            ("soft-hitnormal", Some(LOUD)),
            ("soft-hitnormal1", Some(&[512, -512, 512])),
        ]);
        let pack = SamplePack::load(&dir);
        let heard = pack.get(SampleSet::Soft, Voice::Normal, 1).expect("a sound");
        assert!(heard[0] > 0.4, "the numbered file was played as the plain one");
        assert!(
            pack.unused().contains(&"soft-hitnormal1".to_owned()),
            "a name the game never asks for should be reported, not used"
        );
    }

    #[test]
    fn the_plain_sound_still_answers_when_it_is_the_only_one() {
        // The other half: dropping the `1` file must not drop the real one,
        // and an index the map does ask for — two and up — still resolves.
        let pack = SamplePack::default().with_beatmap(&skin(&[
            ("soft-hitnormal", Some(LOUD)),
            ("soft-hitnormal2", Some(&[512, -512, 512])),
        ]));
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Normal, 1), Found::Beatmap(1));
        assert_eq!(pack.trace(SampleSet::Soft, Voice::Normal, 2), Found::Beatmap(2));
    }

    #[test]
    fn a_skin_that_capitalises_a_name_is_still_read() {
        // osu! grew up on Windows, where the filesystem does not care about
        // case, and skinners lean on it: `Soft-HitClap.WAV` is an ordinary
        // thing to find in a folder.
        //
        // This engine cared twice over. The scanner took `.wav` off the name
        // *before* lowering it, so a capitalised extension dropped the file
        // without a word; and the by-name lookups joined an exact lower-case
        // path, which finds nothing on a filesystem that distinguishes case.
        //
        // Neither showed on the machine this was written on, because macOS does
        // not distinguish either. Both would show on the server.
        let dir = std::env::temp_dir().join(format!(
            "dossier-case-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos())
        ));
        std::fs::create_dir_all(&dir).expect("a folder");
        for name in ["Soft-HitClap.WAV", "NORMAL-HitWhistle.Wav"] {
            std::fs::write(dir.join(name), wav(1, 16, 44_100, &[1000, -1000, 1000, -1000]))
                .expect("a file");
        }

        let pack = SamplePack::load(&dir);
        assert!(
            pack.get(SampleSet::Soft, Voice::Clap, 1).is_some(),
            "a capitalised clap was not found"
        );
        assert!(
            pack.get(SampleSet::Normal, Voice::Whistle, 1).is_some(),
            "a capitalised whistle was not found"
        );
        std::fs::remove_dir_all(&dir).ok();
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
        // No client shows a skin an index. lazer strips the suffix before the
        // lookup — `lookupNames.Where(name => !name.EndsWith(suffix))` when
        // `UseCustomSampleBanks` is false, which it is for every user skin —
        // and danser is the same shape, `Samples[set][voice]` against
        // `MapSamples[set][voice][index]`. So `soft-hitwhistle2.wav` in a skin
        // is dead weight in all three.
        //
        // Here the plain file is a blank, which is what makes it audible: every
        // index finds the blank and is silent, and the numbered file beside it
        // is never asked for.
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
        // And it is reported rather than quietly dropped.
        assert_eq!(pack.unused(), ["soft-hitwhistle2"]);
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
    fn a_slip_of_the_finger_fills_a_slot_nothing_else_speaks_for() {
        // Skins are hand-made folders and they carry typos. A folder with
        // `normal-hitwistle` and no `normal-hitwhistle` at all has a sound its
        // author expected to hear and the game never will, and that is what
        // this is for.
        let dir = skin(&[
            ("normal-hitwistle", Some(LOUD)),   // the only whistle, misspelt
            ("soft-hitfinish", Some(LOUD)),
            ("softl-hitfinish", Some(&[99])),   // a slip, but the slot is taken
        ]);
        let pack = SamplePack::load(&dir);

        assert!(
            pack.get(SampleSet::Normal, Voice::Whistle, 1)
                .is_some_and(|s| !s.is_empty()),
            "the misspelt whistle was left on the floor"
        );
        assert_eq!(
            pack.get(SampleSet::Soft, Voice::Finish, 1).unwrap().len(),
            LOUD.len(),
            "a guess overruled a name that was spelt right"
        );
        assert_eq!(pack.unused(), ["softl-hitfinish"]);
    }

    #[test]
    fn a_guess_does_not_undo_a_deliberate_blank() {
        // `azr8`, exactly: a stubbed whistle with the real recording beside it
        // under a name the game does not read. In osu! that skin has no
        // whistles — checked in the client against a replay — so neither has
        // this. A blank is a deletion, and a typo is not a licence to reverse
        // one.
        let dir = skin(&[
            ("normal-hitwhistle", None),        // forty-four bytes, on purpose
            ("normal-hitwistle", Some(LOUD)),   // the joke, parked out of reach
        ]);
        let pack = SamplePack::load(&dir);
        assert_eq!(
            pack.get(SampleSet::Normal, Voice::Whistle, 1),
            Some(&[][..]),
            "a guess put back a sound the skin removed"
        );
        assert_eq!(pack.trace(SampleSet::Normal, Voice::Whistle, 1), Found::Blank);
        assert_eq!(pack.unused(), ["normal-hitwistle"]);
    }

    #[test]
    fn a_guess_never_silences_a_sound_that_is_there() {
        // The whole safety of the above. `drum--hitwhistle.wav` in the skin
        // this was written against is a *blank*, and taking it for
        // `drum-hitwhistle` would silence the real one lying beside it.
        let dir = skin(&[
            ("drum-hitwhistle", Some(LOUD)),
            ("drum--hitwhistle", None),
        ]);
        let pack = SamplePack::load(&dir);
        assert_eq!(
            pack.get(SampleSet::Drum, Voice::Whistle, 1).unwrap().len(),
            LOUD.len(),
            "a blank with a doubled dash silenced the real file"
        );
    }

    #[test]
    fn a_name_no_amount_of_forgiveness_reaches_is_left_alone() {
        // `hitsoft` is not a misspelling of anything osu! plays; it is somebody
        // keeping a second take in the folder. Reported, not adopted.
        let dir = skin(&[("soft-hitnormal", Some(LOUD)), ("soft-hitsoft", Some(LOUD))]);
        let pack = SamplePack::load(&dir);
        assert_eq!(pack.unused(), ["soft-hitsoft"]);
    }

    #[test]
    fn what_no_voice_uses_is_remembered_so_it_can_be_named() {
        // A skin is somebody's folder, and the only honest answer to "where did
        // my hit sound go" is the list. Most of it is menu audio that never
        // sounds during a play; what matters is the file that looks like a hit
        // sound and is not one, because that is a sound its author expected to
        // hear and nobody ever will — here or in the game.
        let dir = skin(&[
            ("soft-hitnormal", Some(LOUD)),
            ("menu-play-click", Some(LOUD)),    // never heard during a play
            ("combobreak", Some(LOUD)),         // bankless, and filed
        ]);
        let pack = SamplePack::load(&dir);
        assert_eq!(
            pack.unused(),
            ["menu-play-click"],
            "the list is what it could neither file nor place"
        );
        assert!(pack.get(SampleSet::Normal, Voice::Miss, 1).is_some(), "combobreak is filed");
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
