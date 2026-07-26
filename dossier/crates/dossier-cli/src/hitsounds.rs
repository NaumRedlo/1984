//! Turning a judged play into a hit-sound track.
//!
//! The sounds follow the *judgement*, not the map: a note makes its noise when
//! the player struck it, and a note nobody struck makes none. That is what osu!
//! does, and it's why the track can't be built from the beatmap alone — a
//! missed note in a rendered replay should be conspicuous by its silence.

use dossier_audio::{Kit, SamplePack, SampleSet, Track, Voice};
use dossier_beatmap::{sound_bits, Beatmap, HitObject, SampleSet as MapSet};
use dossier_sim::{GameState, Part};

/// Build the track for the span being rendered.
///
/// `from_ms` and `rate` are the same numbers the video uses, so a hit at map
/// time T lands at video time `(T - from) / rate` — under DoubleTime the sounds
/// compress along with everything else.
pub fn build(
    state: &GameState,
    beatmap: &Beatmap,
    from_ms: f64,
    rate: f64,
    video_seconds: f64,
    kit: Kit,
    pack: SamplePack,
) -> Track {
    let mut track = Track::new(video_seconds, kit).with_samples(pack);
    let Some(judge) = state.judge() else {
        return track;
    };

    for event in judge.events() {
        if event.result.is_miss() {
            continue;
        }
        let Some(object) = beatmap.objects.get(event.object_index) else {
            continue;
        };
        let Some(voice) = voice_for(event.part, object) else {
            continue;
        };
        let (set, volume) = bank_for(beatmap, object, voice);
        track.strike_with(
            voice,
            (event.time_ms - from_ms) / 1000.0 / rate,
            set,
            volume,
        );
    }
    track
}

/// Which sound a part of an object makes.
fn voice_for(part: Part, object: &HitObject) -> Option<Voice> {
    match part {
        // The slider's overall verdict is a score, not a strike, and a spinner
        // has no single moment to sound at.
        Part::Slider | Part::Spinner => None,
        Part::SliderTick => Some(Voice::Tick),
        _ => Some(loudest(object.hit_sound)),
    }
}

/// Which bank the sound comes from, and how loud.
///
/// osu! resolves this in layers, and each one only speaks if it has something
/// to say. The note's own field wins; a zero there means the timing point
/// decides; and additions (whistle, finish, clap) have a bank of their own that
/// falls back to the plain one. Collapsing any of that loses a distinction the
/// mapper made on purpose.
fn bank_for(beatmap: &Beatmap, object: &HitObject, voice: Voice) -> (SampleSet, f32) {
    let point = beatmap.timing.sample_point_at(object.time_ms);
    let inherited = point.map_or(MapSet::Normal, |p| p.set);

    let sample = object.hit_sample;
    let code = match voice {
        // The plain hit and the slider tick follow the note's own bank.
        Voice::Normal | Voice::Tick => sample.normal_set,
        // Decorations have their own, falling back to the plain one.
        _ => {
            if sample.addition_set != 0 {
                sample.addition_set
            } else {
                sample.normal_set
            }
        }
    };
    let set = if code == 0 {
        inherited
    } else {
        MapSet::from_code(code)
    };

    let volume = if sample.volume > 0 {
        f32::from(sample.volume)
    } else {
        point.map_or(100.0, |p| f32::from(p.volume))
    } / 100.0;

    (convert(set), volume)
}

/// The beatmap's notion of a bank and the audio crate's are the same three
/// names held by two crates that have no business depending on each other.
fn convert(set: MapSet) -> SampleSet {
    match set {
        MapSet::Normal => SampleSet::Normal,
        MapSet::Soft => SampleSet::Soft,
        MapSet::Drum => SampleSet::Drum,
    }
}

/// osu! lets a note carry several sounds at once. Layering them all turns a
/// busy map into mush, so the most prominent one wins — which is also the one
/// the mapper put there to be noticed.
fn loudest(bits: u8) -> Voice {
    if bits & sound_bits::FINISH != 0 {
        Voice::Finish
    } else if bits & sound_bits::CLAP != 0 {
        Voice::Clap
    } else if bits & sound_bits::WHISTLE != 0 {
        Voice::Whistle
    } else {
        Voice::Normal
    }
}

/// A short piece for listening to a kit on its own.
///
/// Each voice in isolation, then all of them in a stream at 180bpm. The
/// isolated hits say what a sound *is*; the stream says whether it survives
/// being played fast, which is where most hit sounds fall apart.
pub fn audition(kit: Kit, pack: SamplePack) -> Track {
    // The pack has to be in place before the first strike: a voice is rendered
    // once and cached, so attaching samples afterwards would be ignored.
    let mut track = Track::new(6.0, kit).with_samples(pack);
    let mut at = 0.3;

    for voice in [
        Voice::Normal,
        Voice::Whistle,
        Voice::Finish,
        Voice::Clap,
        Voice::Tick,
    ] {
        for _ in 0..3 {
            track.strike(voice, at);
            at += 0.28;
        }
        at += 0.35;
    }

    // 1/4 notes at 180bpm — 83ms apart, the density that exposes a sound with
    // too long a tail.
    at += 0.3;
    for i in 0..24 {
        let voice = match i % 8 {
            0 => Voice::Finish,
            4 => Voice::Clap,
            2 | 6 => Voice::Whistle,
            _ => Voice::Normal,
        };
        track.strike(voice, at + f64::from(i) * 0.0833);
    }
    track
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_undecorated_note_makes_the_plain_sound() {
        assert_eq!(loudest(0), Voice::Normal);
        assert_eq!(loudest(sound_bits::NORMAL), Voice::Normal);
    }

    #[test]
    fn each_decoration_has_its_own_voice() {
        assert_eq!(loudest(sound_bits::WHISTLE), Voice::Whistle);
        assert_eq!(loudest(sound_bits::FINISH), Voice::Finish);
        assert_eq!(loudest(sound_bits::CLAP), Voice::Clap);
    }

    #[test]
    fn stacked_sounds_pick_one_rather_than_pile_up() {
        // Layering every bit on a note that carries three of them is how a
        // dense map turns into noise.
        let all = sound_bits::WHISTLE | sound_bits::FINISH | sound_bits::CLAP;
        assert_eq!(loudest(all), Voice::Finish);
        assert_eq!(loudest(sound_bits::WHISTLE | sound_bits::CLAP), Voice::Clap);
    }
}

#[cfg(test)]
mod banks {
    use super::*;
    fn map(timing_points: &str, object: &str) -> Beatmap {
        Beatmap::parse(&format!(
            "osu file format v14\n\n[TimingPoints]\n{timing_points}\n\n[HitObjects]\n{object}\n"
        ))
        .expect("test map should parse")
    }

    /// `time,beatLength,meter,sampleSet,sampleIndex,volume,uninherited,effects`
    const SOFT_AT_HALF: &str = "0,500,4,2,0,50,1,0";
    const DRUM_LOUD: &str = "0,500,4,3,0,100,1,0";

    #[test]
    fn a_note_that_says_nothing_takes_the_timing_points_bank_and_volume() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0");
        let (set, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal);
        assert_eq!(set, SampleSet::Soft);
        assert!((volume - 0.5).abs() < 1e-6);
    }

    #[test]
    fn a_notes_own_bank_overrules_the_timing_point() {
        // `3:0:0:0:` — drum for the plain hit, everything else inherited.
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0,3:0:0:0:");
        let (set, _) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal);
        assert_eq!(set, SampleSet::Drum);
    }

    #[test]
    fn decorations_have_a_bank_of_their_own() {
        // Normal from drum, additions from soft: a mapper can put the clap in
        // a different bank from the hit under it, and often does.
        let beatmap = map(DRUM_LOUD, "100,100,1000,1,8,3:2:0:0:");
        let object = &beatmap.objects[0];
        assert_eq!(bank_for(&beatmap, object, Voice::Normal).0, SampleSet::Drum);
        assert_eq!(bank_for(&beatmap, object, Voice::Clap).0, SampleSet::Soft);
    }

    #[test]
    fn a_decoration_with_no_bank_of_its_own_follows_the_plain_hit() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,8,3:0:0:0:");
        let object = &beatmap.objects[0];
        assert_eq!(bank_for(&beatmap, object, Voice::Clap).0, SampleSet::Drum);
    }

    #[test]
    fn a_notes_own_volume_overrules_the_timing_points() {
        let beatmap = map(SOFT_AT_HALF, "100,100,1000,1,0,0:0:0:20:");
        let (_, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal);
        assert!((volume - 0.2).abs() < 1e-6, "got {volume}");
    }

    #[test]
    fn the_bank_follows_the_section_a_note_falls_in() {
        // Two sections, and a note in each. Reading only the first line would
        // play the whole map in one bank.
        let beatmap = map(
            "0,500,4,2,0,50,1,0\n5000,-100,4,3,0,90,0,0",
            "100,100,1000,1,0\n200,200,6000,1,0",
        );
        assert_eq!(
            bank_for(&beatmap, &beatmap.objects[0], Voice::Normal).0,
            SampleSet::Soft
        );
        assert_eq!(
            bank_for(&beatmap, &beatmap.objects[1], Voice::Normal).0,
            SampleSet::Drum,
            "a green line carries sound settings too"
        );
    }

    #[test]
    fn a_note_with_no_sample_field_at_all_still_resolves() {
        // Most notes on most maps say nothing, so this is the common path, not
        // the edge case.
        let beatmap = map(DRUM_LOUD, "100,100,1000,1,0");
        let (set, volume) = bank_for(&beatmap, &beatmap.objects[0], Voice::Normal);
        assert_eq!(set, SampleSet::Drum);
        assert!((volume - 1.0).abs() < 1e-6);
    }
}
