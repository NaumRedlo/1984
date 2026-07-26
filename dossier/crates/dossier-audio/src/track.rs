//! Stamping sounds onto a timeline.

use std::collections::HashMap;

use crate::kit::Kit;
use crate::synth::Voice;
use crate::SAMPLE_RATE;

/// A stretch of silence that hits get written into.
///
/// Mono while it's being built — hit sounds are centred, and carrying two
/// identical channels through the mixing would double the work for nothing.
/// The split to stereo happens on the way out.
pub struct Track {
    samples: Vec<f32>,
    voices: HashMap<Voice, Vec<f32>>,
    kit: Kit,
}

impl Track {
    pub fn new(seconds: f64, kit: Kit) -> Self {
        Self {
            samples: vec![0.0; (seconds.max(0.0) * f64::from(SAMPLE_RATE)) as usize],
            voices: HashMap::new(),
            kit,
        }
    }

    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    pub fn seconds(&self) -> f64 {
        self.samples.len() as f64 / f64::from(SAMPLE_RATE)
    }

    /// Add one hit at `at_seconds`.
    ///
    /// A hit past the end is dropped rather than extending the track: the
    /// track is exactly as long as the video, and audio beyond the last frame
    /// would either be cut by the encoder or stretch the clip.
    pub fn strike(&mut self, voice: Voice, at_seconds: f64) {
        if at_seconds < 0.0 {
            return;
        }
        let start = (at_seconds * f64::from(SAMPLE_RATE)) as usize;
        if start >= self.samples.len() {
            return;
        }

        let gain = voice.gain(&self.kit);
        let kit = self.kit;
        let rendered = self
            .voices
            .entry(voice)
            .or_insert_with(|| voice.render(&kit));
        for (offset, value) in rendered.iter().enumerate() {
            match self.samples.get_mut(start + offset) {
                Some(slot) => *slot += value * gain,
                None => break,
            }
        }
    }

    /// Interleaved stereo 16-bit PCM, little-endian — what ffmpeg is handed.
    ///
    /// Peaks are tamed rather than clipped: a dense stream lands several hits
    /// inside a few milliseconds, and letting those sum past full scale turns
    /// the busiest, most interesting moments into distortion.
    pub fn to_pcm(&self) -> Vec<u8> {
        let peak = self
            .samples
            .iter()
            .fold(0.0f32, |worst, s| worst.max(s.abs()));
        let scale = if peak > 0.95 { 0.95 / peak } else { 1.0 };

        let mut out = Vec::with_capacity(self.samples.len() * 4);
        for sample in &self.samples {
            let value = (sample * scale * f32::from(i16::MAX)) as i16;
            out.extend_from_slice(&value.to_le_bytes());
            out.extend_from_slice(&value.to_le_bytes());
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_empty_track_is_silence_of_the_right_length() {
        let track = Track::new(2.0, Kit::default());
        assert!((track.seconds() - 2.0).abs() < 1e-9);
        // Stereo, two bytes a channel.
        assert_eq!(track.to_pcm().len(), 2 * 44_100 * 2 * 2);
        assert!(track.to_pcm().iter().all(|&b| b == 0));
    }

    #[test]
    fn a_hit_lands_where_it_was_asked_to() {
        let mut track = Track::new(1.0, Kit::default());
        track.strike(Voice::Normal, 0.5);
        let pcm = track.to_pcm();

        let loudness_around = |seconds: f64| {
            let frame = (seconds * 44_100.0) as usize * 4;
            pcm[frame..frame + 400]
                .chunks_exact(2)
                .map(|b| i16::from_le_bytes([b[0], b[1]]).unsigned_abs() as u32)
                .sum::<u32>()
        };
        assert_eq!(loudness_around(0.2), 0, "silent before the hit");
        assert!(loudness_around(0.5) > 0, "and not at it");
    }

    #[test]
    fn hits_outside_the_track_are_dropped_not_stretched_onto_it() {
        // The track is exactly as long as the video; a sound after the last
        // frame has nowhere to go.
        let mut track = Track::new(1.0, Kit::default());
        track.strike(Voice::Normal, 5.0);
        track.strike(Voice::Normal, -1.0);
        assert!(track.to_pcm().iter().all(|&b| b == 0));
        assert!((track.seconds() - 1.0).abs() < 1e-9);
    }

    #[test]
    fn a_pile_of_hits_at_once_is_turned_down_rather_than_clipped() {
        // A dense stream lands several notes within milliseconds. Summing them
        // past full scale would distort exactly the busiest moments.
        let mut track = Track::new(1.0, Kit::default());
        for i in 0..12 {
            track.strike(Voice::Finish, 0.3 + f64::from(i) * 0.001);
        }
        let loudest = track
            .to_pcm()
            .chunks_exact(2)
            .map(|b| i16::from_le_bytes([b[0], b[1]]).unsigned_abs())
            .max()
            .unwrap();
        assert!(loudest <= (0.96 * f32::from(i16::MAX)) as u16, "{loudest}");
    }

    #[test]
    fn every_voice_makes_a_sound_and_stays_in_range() {
        for voice in [
            Voice::Normal,
            Voice::Whistle,
            Voice::Finish,
            Voice::Clap,
            Voice::Tick,
        ] {
            let rendered = voice.render(&Kit::default());
            assert!(!rendered.is_empty(), "{voice:?} is silent");
            assert!(
                rendered.iter().all(|s| s.abs() <= 1.5),
                "{voice:?} runs far out of range"
            );
            assert!(
                rendered.iter().any(|s| s.abs() > 0.05),
                "{voice:?} is inaudible"
            );
        }
    }

    #[test]
    fn a_voice_starts_and_ends_quietly() {
        // A buffer that begins or ends mid-swing clicks every time it plays.
        let rendered = Voice::Normal.render(&Kit::default());
        assert!(rendered[0].abs() < 0.05, "starts with a step");
        assert!(rendered[rendered.len() - 1].abs() < 0.05, "ends with one");
    }
}

#[cfg(test)]
mod levels {
    use super::*;

    /// A hit sound has one job: to be heard over the music. This pins that.
    ///
    /// Measured from a real map: the music sits near 9600 RMS on the i16 scale
    /// and is mastered to the ceiling. A hit is a few tens of milliseconds
    /// long, so to be heard at all it has to reach a comparable level — the
    /// first version of this kit peaked around 12000 and could not be heard.
    #[test]
    fn every_kit_is_loud_enough_to_hear_over_music() {
        for (name, kit) in [
            ("plain", Kit::plain()),
            ("1984", Kit::nineteen_eightyfour()),
        ] {
            for voice in [Voice::Normal, Voice::Whistle, Voice::Finish, Voice::Clap] {
                let mut track = Track::new(0.5, kit);
                track.strike(voice, 0.1);
                let peak = track
                    .to_pcm()
                    .chunks_exact(2)
                    .map(|b| i16::from_le_bytes([b[0], b[1]]).unsigned_abs())
                    .max()
                    .unwrap_or(0);
                assert!(
                    peak > 20_000,
                    "{name}/{voice:?} peaks at {peak}, which the music will bury"
                );
            }
        }
    }
}
