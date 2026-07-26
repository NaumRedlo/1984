//! Making the sounds.
//!
//! Every voice is the same idea: noise through a resonant band-pass, with an
//! envelope on it. That one recipe covers the plain hit, the clap and the tick
//! — they differ only in where the filter sits and how fast the envelope
//! closes. Tuned noise is what a skin's click actually is; the earlier attempt
//! here built pitched bodies with noise transients layered on, which is a
//! drum-synthesis technique and sounded like one.
//!
//! Each one-shot is generated once and stamped wherever it's needed. Generating
//! per hit would repeat identical arithmetic thousands of times over a map.

use crate::kit::Kit;
use crate::SAMPLE_RATE;

/// The sounds a note can make.
///
/// The four osu! hitsounds plus the slider tick, which is not a hitsound bit
/// but is the only other thing that makes a noise while a slider runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Voice {
    /// The plain hit.
    Normal,
    /// A bright ping, used for accents.
    Whistle,
    /// The crash on a big landing.
    Finish,
    /// A sharp snap.
    Clap,
    /// The small blip a slider makes as it passes a tick.
    Tick,
}

impl Voice {
    /// The one-shot for this voice under `kit`: mono, roughly [-1, 1].
    pub fn render(self, kit: &Kit) -> Vec<f32> {
        match self {
            // A short, dry tick around 1.1kHz — the ordinary skin click.
            Self::Normal => tuned_noise(0.034 * kit.decay, 1_100.0 * kit.pitch, 2.6, 0x51ed_2701),
            // A clean tone rather than noise, so accents read as *pitched*
            // against a stream of clicks.
            Self::Whistle => tone(0.055 * kit.decay, 1_600.0 * kit.pitch),
            Self::Finish => splash(0.26 * kit.decay, kit.pitch),
            Self::Clap => clap(kit),
            // Higher and quieter than the plain hit: present, never in the way.
            Self::Tick => tuned_noise(0.016 * kit.decay, 2_600.0 * kit.pitch, 3.2, 0x1234_5678),
        }
    }

    /// How loud this voice sits in the mix. Ticks are frequent and incidental;
    /// a finish is meant to land.
    pub fn gain(self, kit: &Kit) -> f32 {
        let base = match self {
            Self::Normal => 0.62,
            Self::Whistle => 0.48,
            Self::Finish => 0.70,
            Self::Clap => 0.60,
            Self::Tick => 0.24,
        };
        base * kit.level
    }
}

/// Noise through a resonant band-pass: the click every skin is built on.
fn tuned_noise(seconds: f32, hz: f32, resonance: f32, seed: u32) -> Vec<f32> {
    let mut rng = Noise::new(seed);
    let mut filter = Svf::new(hz, resonance);
    generate(seconds, |_, envelope| {
        filter.band(rng.next()) * envelope * 1.6
    })
}

/// A plain decaying sine.
fn tone(seconds: f32, hz: f32) -> Vec<f32> {
    generate(seconds, |t, envelope| {
        (t * hz * std::f32::consts::TAU).sin() * envelope
    })
}

/// Bright, wide noise with a long tail — a cymbal rather than a click.
fn splash(seconds: f32, pitch: f32) -> Vec<f32> {
    let mut rng = Noise::new(0x9e37_79b9);
    let mut low = OnePole::new(0.72);
    generate(seconds, |_, envelope| {
        let raw = rng.next();
        // Subtracting a lowpass leaves the top end, which is all a splash is —
        // and only a fraction of the original amplitude, hence the make-up.
        (raw - low.step(raw)) * 2.4 * envelope * (0.8 + pitch * 0.2)
    })
}

/// Two bursts a few milliseconds apart. The doubling is what makes a clap read
/// as a clap rather than as a shorter, duller hit.
fn clap(kit: &Kit) -> Vec<f32> {
    let gap = (0.007 * SAMPLE_RATE as f32) as usize;
    let mut out = tuned_noise(0.055 * kit.decay, 1_500.0 * kit.pitch, 2.2, 0xa5a5_1234);

    let first = out.clone();
    for (i, value) in first.iter().enumerate() {
        if let Some(slot) = out.get_mut(i + gap) {
            *slot += value * 0.8;
        }
    }
    out
}

/// Run `voice` over `seconds`, applying the shared attack/decay envelope.
fn generate(seconds: f32, mut voice: impl FnMut(f32, f32) -> f32) -> Vec<f32> {
    let samples = (seconds * SAMPLE_RATE as f32) as usize;
    (0..samples)
        .map(|i| {
            let t = i as f32 / SAMPLE_RATE as f32;
            // A sub-millisecond fade in stops the discontinuity at sample zero
            // from clicking; the exponential decay does the rest.
            let attack = (t * 2_000.0).min(1.0);
            let decay = (-t / (seconds * 0.30)).exp();
            voice(t, attack * decay)
        })
        .collect()
}

/// Deterministic white noise. Reproducible sounds matter: a test that asserts
/// on a waveform has to get the same waveform every run.
struct Noise(u32);

impl Noise {
    fn new(seed: u32) -> Self {
        Self(seed)
    }

    fn next(&mut self) -> f32 {
        // xorshift32 — plenty of randomness for noise, and no dependency.
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 17;
        self.0 ^= self.0 << 5;
        (self.0 as f32 / u32::MAX as f32) * 2.0 - 1.0
    }
}

/// Chamberlin state-variable filter, band-pass output.
///
/// A resonant band-pass is what turns white noise into a *pitched* click. A
/// plain one-pole can only dull the noise; it can't give it a centre, which is
/// the difference between a hiss and a tap.
struct Svf {
    f: f32,
    damping: f32,
    low: f32,
    band: f32,
}

impl Svf {
    fn new(hz: f32, resonance: f32) -> Self {
        let hz = hz.clamp(20.0, SAMPLE_RATE as f32 * 0.45);
        Self {
            f: 2.0 * (std::f32::consts::PI * hz / SAMPLE_RATE as f32).sin(),
            damping: 1.0 / resonance.max(0.5),
            low: 0.0,
            band: 0.0,
        }
    }

    fn band(&mut self, input: f32) -> f32 {
        let high = input - self.low - self.damping * self.band;
        self.band += self.f * high;
        self.low += self.f * self.band;
        self.band
    }
}

/// One-pole lowpass, used to split a splash's top end off its body.
struct OnePole {
    coefficient: f32,
    state: f32,
}

impl OnePole {
    fn new(coefficient: f32) -> Self {
        Self {
            coefficient,
            state: 0.0,
        }
    }

    fn step(&mut self, input: f32) -> f32 {
        self.state += (input - self.state) * self.coefficient;
        self.state
    }
}
