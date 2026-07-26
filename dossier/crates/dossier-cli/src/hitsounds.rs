//! Turning a judged play into a hit-sound track.
//!
//! The sounds follow the *judgement*, not the map: a note makes its noise when
//! the player struck it, and a note nobody struck makes none. That is what osu!
//! does, and it's why the track can't be built from the beatmap alone — a
//! missed note in a rendered replay should be conspicuous by its silence.

use dossier_audio::{Kit, SamplePack, Track, Voice};
use dossier_beatmap::{sound_bits, Beatmap};
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
        let Some(voice) = voice_for(event.part, beatmap, event.object_index) else {
            continue;
        };
        track.strike(voice, (event.time_ms - from_ms) / 1000.0 / rate);
    }
    track
}

/// Which sound a part of an object makes.
fn voice_for(part: Part, beatmap: &Beatmap, object_index: usize) -> Option<Voice> {
    match part {
        // The slider's overall verdict is a score, not a strike, and a spinner
        // has no single moment to sound at.
        Part::Slider | Part::Spinner => None,
        Part::SliderTick => Some(Voice::Tick),
        _ => {
            let bits = beatmap.objects.get(object_index)?.hit_sound;
            Some(loudest(bits))
        }
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
