//! Stamping sounds onto a timeline.

use std::collections::HashMap;

use crate::kit::Kit;
use crate::samples::{SamplePack, SampleSet};
use crate::synth::Voice;
use crate::SAMPLE_RATE;

/// A stretch of silence that hits get written into.
///
/// Mono while it's being built — hit sounds are centred, and carrying two
/// identical channels through the mixing would double the work for nothing.
/// The split to stereo happens on the way out.
pub struct Track {
    samples: Vec<f32>,
    voices: HashMap<(SampleSet, Voice, u32), Vec<f32>>,
    kit: Kit,
    /// A skin's own sounds, used ahead of synthesis wherever it has one.
    pack: SamplePack,
}

impl Track {
    pub fn new(seconds: f64, kit: Kit) -> Self {
        Self {
            samples: vec![0.0; (seconds.max(0.0) * f64::from(SAMPLE_RATE)) as usize],
            voices: HashMap::new(),
            kit,
            pack: SamplePack::default(),
        }
    }

    /// Play a real skin's sounds instead of the synthesised ones.
    ///
    /// Per voice, not all or nothing: a skin that only defines a clap keeps
    /// its clap and borrows the rest.
    pub fn with_samples(mut self, pack: SamplePack) -> Self {
        self.pack = pack;
        self
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
        self.strike_with(voice, at_seconds, SampleSet::Normal, 1.0);
    }

    /// Add one hit, saying which bank it comes from and how loud it is.
    ///
    /// Both are properties of the map rather than of the skin: a section can
    /// switch to the soft bank or drop to a third of the volume, and ignoring
    /// that flattens exactly the dynamics the mapper wrote in.
    pub fn strike_with(&mut self, voice: Voice, at_seconds: f64, set: SampleSet, volume: f32) {
        self.strike_indexed(voice, at_seconds, set, 1, volume);
    }

    /// The same, naming the custom sample bank a map switched to.
    ///
    /// A separate entry point rather than another argument on `strike_with`
    /// because most callers have no index to give and `1` is not a number they
    /// should have to know.
    pub fn strike_indexed(
        &mut self,
        voice: Voice,
        at_seconds: f64,
        set: SampleSet,
        index: u32,
        volume: f32,
    ) {
        if at_seconds < 0.0 {
            return;
        }
        let start = (at_seconds * f64::from(SAMPLE_RATE)) as usize;
        if start >= self.samples.len() {
            return;
        }

        let gain = voice.gain(&self.kit) * volume.clamp(0.0, 1.0);
        let kit = self.kit;
        let pack = &self.pack;
        let rendered = self.voices.entry((set, voice, index)).or_insert_with(|| {
            // A skin's own sound wins; synthesis fills whatever it lacks.
            pack.get(set, voice, index)
                .map(<[f32]>::to_vec)
                .unwrap_or_else(|| voice.render(&kit))
        });
        for (offset, value) in rendered.iter().enumerate() {
            match self.samples.get_mut(start + offset) {
                Some(slot) => *slot += value * gain,
                None => break,
            }
        }
    }

    /// Hold a sound across a span, looping it, at a playback rate that may
    /// change as it goes.
    ///
    /// The other entry points stamp a finished one-shot. These three sounds are
    /// not one-shots: a `sliderslide` is a second of recorded noise that osu!
    /// runs on a loop for as long as the ball is under the cursor, and a
    /// `spinnerspin` does the same while climbing in pitch. Stamping either one
    /// per frame would be a machine gun; stamping it once would stop before the
    /// slider did.
    ///
    /// `rate` is read per output sample, as a multiple of the recording's own
    /// speed, and it is what carries the spinner's rise:
    ///
    /// ```csharp
    /// private const float spinning_sample_modulated_base_frequency = 20_000f / 44_100;
    /// private const float spinning_sample_modulaton_ratio = 40_000f / 44_100;
    /// private const float spinning_sample_modulated_max_frequency = 100_000f / 44_100;
    ///
    /// spinningSample.Frequency.Value = Math.Min(
    ///     spinning_sample_modulated_max_frequency,
    ///     spinning_sample_modulated_base_frequency
    ///         + progressUnclamped * spinning_sample_modulaton_ratio);
    /// ```
    ///
    /// Read with linear interpolation between neighbouring samples rather than
    /// by rounding to the nearest. At the base rate — well under one, so the
    /// recording is stretched — rounding holds each source sample for two or
    /// three outputs in a row, and a staircase in a waveform is audible as a
    /// buzz sitting on top of the note.
    ///
    /// Nothing is synthesised for these: a skin that brought no file gets
    /// silence, which is what the render already had.
    #[allow(clippy::too_many_arguments)]
    pub fn sustain(
        &mut self,
        voice: Voice,
        (from_seconds, to_seconds): (f64, f64),
        set: SampleSet,
        index: u32,
        volume: f32,
        rate: impl Fn(f64) -> f32,
    ) {
        let Some(source) = self.pack.get(set, voice, index) else {
            return;
        };
        if source.len() < 2 || to_seconds <= from_seconds {
            return;
        }
        let rate_hz = f64::from(SAMPLE_RATE);
        let start = (from_seconds.max(0.0) * rate_hz) as usize;
        let end = ((to_seconds * rate_hz) as usize).min(self.samples.len());
        if start >= end {
            return;
        }

        let gain = voice.gain(&self.kit) * volume.clamp(0.0, 1.0);
        // Long enough not to click, short enough not to eat a slider that only
        // lasts a moment: an eighth of the span, capped at fifteen
        // milliseconds. A loop that starts at full level pops, because the
        // recording does not begin at silence.
        let ramp = (((end - start) / 8).min((0.015 * rate_hz) as usize)).max(1);
        let mut read = 0.0f64;
        for (step, slot) in (start..end).enumerate() {
            let held = step as f64 / rate_hz;
            let fade = (step as f32 / ramp as f32)
                .min((end - start - step) as f32 / ramp as f32)
                .clamp(0.0, 1.0);

            // Between the two samples it falls between, and round the loop
            // rather than off the end of it.
            let whole = read as usize % source.len();
            let next = (whole + 1) % source.len();
            let fraction = (read - read.floor()) as f32;
            let value = source[whole] + (source[next] - source[whole]) * fraction;
            self.samples[slot] += value * gain * fade;

            read += f64::from(rate(held).max(0.01));
            if read >= source.len() as f64 {
                read -= source.len() as f64;
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

    /// The same samples wrapped in a WAV header.
    ///
    /// Auditioning a kit shouldn't need an encoder, a map or a replay — it
    /// should be one command and a file you can double-click. A WAV header is
    /// forty-four bytes of arithmetic, which is a smaller price than a
    /// dependency.
    pub fn to_wav(&self) -> Vec<u8> {
        const CHANNELS: u16 = 2;
        const BITS: u16 = 16;
        let pcm = self.to_pcm();
        let byte_rate = SAMPLE_RATE * u32::from(CHANNELS) * u32::from(BITS / 8);

        let mut out = Vec::with_capacity(pcm.len() + 44);
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + pcm.len() as u32).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16u32.to_le_bytes()); // PCM header size
        out.extend_from_slice(&1u16.to_le_bytes()); // uncompressed
        out.extend_from_slice(&CHANNELS.to_le_bytes());
        out.extend_from_slice(&SAMPLE_RATE.to_le_bytes());
        out.extend_from_slice(&byte_rate.to_le_bytes());
        out.extend_from_slice(&(CHANNELS * BITS / 8).to_le_bytes()); // block align
        out.extend_from_slice(&BITS.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
        out.extend_from_slice(&pcm);
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
        // The kits a render can be given without asking for one. Broadening
        // this to every timbre looked like free coverage and is not: `soft`
        // peaks at 17249 against a bar of 20000, so it would turn a test about
        // the default into a failing claim about a pack nobody defaults to.
        for (name, kit) in [("plain", Kit::plain())] {
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

    // ── the sounds osu! holds rather than strikes ────────────────────────

    /// A folder holding one `{name}.wav`: a sine at `hz`, `seconds` long.
    ///
    /// A sine rather than noise because two of these tests measure *pitch*, and
    /// the only honest way to measure a pitch is to put a known one in.
    fn folder_with(name: &str, hz: f32, seconds: f32) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "dossier-loop-{name}-{}-{hz}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");

        let frames = (seconds * SAMPLE_RATE as f32) as usize;
        let mut data = Vec::with_capacity(frames * 2);
        for n in 0..frames {
            let phase = n as f32 / SAMPLE_RATE as f32 * hz * std::f32::consts::TAU;
            data.extend_from_slice(&((phase.sin() * 12_000.0) as i16).to_le_bytes());
        }
        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data.len() as u32).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16u32.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&1u16.to_le_bytes());
        out.extend_from_slice(&SAMPLE_RATE.to_le_bytes());
        out.extend_from_slice(&(SAMPLE_RATE * 2).to_le_bytes());
        out.extend_from_slice(&2u16.to_le_bytes());
        out.extend_from_slice(&16u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
        out.extend_from_slice(&data);
        std::fs::write(dir.join(format!("{name}.wav")), out).expect("written");
        dir
    }

    /// How loud the track is over a stretch of it, in map seconds.
    fn loudness(track: &Track, from: f64, to: f64) -> f32 {
        let rate = f64::from(SAMPLE_RATE);
        let (a, b) = ((from * rate) as usize, (to * rate) as usize);
        track.samples[a..b.min(track.samples.len())]
            .iter()
            .fold(0.0f32, |worst, s| worst.max(s.abs()))
    }

    #[test]
    fn a_held_sound_runs_for_its_whole_span_and_not_past_it() {
        // A `sliderslide` is a second of recorded noise osu! runs on a loop for
        // as long as the ball is held. Stamped once it would stop before the
        // slider did; this is the whole reason `sustain` exists.
        let dir = folder_with("normal-sliderslide", 440.0, 0.05);
        let mut track = Track::new(1.0, Kit::default()).with_samples(SamplePack::load(&dir));
        track.sustain(Voice::Slide, (0.2, 0.8), SampleSet::Normal, 1, 1.0, |_| 1.0);

        assert!(loudness(&track, 0.0, 0.19) < 1e-6, "silent before it starts");
        assert!(loudness(&track, 0.3, 0.4) > 0.01, "sounding at the start");
        // The source is fifty milliseconds and the span is six hundred, so
        // anything audible here got there by looping.
        assert!(loudness(&track, 0.7, 0.78) > 0.01, "still sounding at the end");
        assert!(loudness(&track, 0.81, 1.0) < 1e-6, "silent after it stops");
    }

    #[test]
    fn a_held_sound_starts_and_ends_at_nothing() {
        // A loop that begins at full level pops: a recording does not start at
        // silence, so the first sample is a step from zero to wherever the
        // waveform happened to be.
        let dir = folder_with("normal-sliderslide", 200.0, 0.05);
        let mut track = Track::new(1.0, Kit::default()).with_samples(SamplePack::load(&dir));
        track.sustain(Voice::Slide, (0.1, 0.9), SampleSet::Normal, 1, 1.0, |_| 1.0);

        let middle = loudness(&track, 0.4, 0.6);
        assert!(loudness(&track, 0.1, 0.105) < middle * 0.6, "it fades in");
        assert!(loudness(&track, 0.895, 0.9) < middle * 0.6, "and out");
    }

    #[test]
    fn a_spinner_climbs_in_pitch_as_it_is_turned() {
        // ```csharp
        // spinningSample.Frequency.Value = Math.Min(
        //     spinning_sample_modulated_max_frequency,
        //     spinning_sample_modulated_base_frequency
        //         + progressUnclamped * spinning_sample_modulaton_ratio);
        // ```
        //
        // Measured as zero crossings, which is what pitch is: the source is one
        // sine, so twice as many crossings in the same stretch is an octave up.
        let dir = folder_with("spinnerspin", 300.0, 0.2);
        let mut track = Track::new(2.0, Kit::default()).with_samples(SamplePack::load(&dir));
        // Base at the start, four times it by the end.
        track.sustain(Voice::Spin, (0.1, 1.9), SampleSet::Normal, 1, 1.0, |held| {
            0.5 + 1.5 * (held / 1.8) as f32
        });

        let crossings = |from: f64, to: f64| {
            let rate = f64::from(SAMPLE_RATE);
            let (a, b) = ((from * rate) as usize, (to * rate) as usize);
            track.samples[a..b]
                .windows(2)
                .filter(|pair| (pair[0] < 0.0) != (pair[1] < 0.0))
                .count()
        };
        let early = crossings(0.3, 0.5);
        let late = crossings(1.5, 1.7);
        assert!(early > 0, "it is sounding at all: {early}");
        assert!(late > early * 2, "the pitch did not climb: {early} then {late}");
    }

    #[test]
    fn a_skin_that_brought_no_loop_gets_silence() {
        // Nothing is synthesised for these. Our own kit is a set of struck
        // sounds, and putting an invented noise under every slider of every
        // render made without a skin is not a thing to do quietly.
        let mut track = Track::new(1.0, Kit::default());
        track.sustain(Voice::Slide, (0.2, 0.8), SampleSet::Normal, 1, 1.0, |_| 1.0);
        assert!(track.to_pcm().iter().all(|&b| b == 0));
    }
}
