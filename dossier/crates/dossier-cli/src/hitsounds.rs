//! Turning a judged play into a hit-sound track.
//!
//! The sounds follow the *judgement*, not the map: a note makes its noise when
//! the player struck it, and a note nobody struck makes none. That is what osu!
//! does, and it's why the track can't be built from the beatmap alone — a
//! missed note in a rendered replay should be conspicuous by its silence.

use dossier_audio::{Track, Voice};
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
) -> Track {
    let mut track = Track::new(video_seconds);
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
