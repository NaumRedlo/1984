//! Making the sounds.
//!
//! Each voice is a one-shot: a short buffer generated once and stamped into the
//! track wherever it's needed. Generating per hit would mean thousands of
//! identical computations for a map that strikes the same note a thousand times.

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
    /// The one-shot for this voice: mono, [-1, 1].
    pub fn render(self) -> Vec<f32> {
        match self {
            Self::Normal => click(0.045, 320.0, 0.55, 0.45),
            Self::Whistle => chime(0.075, &[1_400.0, 2_100.0]),
            Self::Finish => crash(0.32),
            Self::Clap => clap(),
            Self::Tick => click(0.022, 1_900.0, 0.25, 0.30),
        }
    }

    /// How loud this voice sits in the mix. Ticks are frequent and incidental;
    /// a finish is meant to land.
    pub fn gain(self) -> f32 {
        match self {
            Self::Normal => 0.55,
            Self::Whistle => 0.45,
            Self::Finish => 0.70,
            Self::Clap => 0.55,
            Self::Tick => 0.22,
        }
    }
}

/// A percussive click: a decaying sine with a noise transient on the front.
fn click(seconds: f32, hz: f32, tone: f32, noise: f32) -> Vec<f32> {
    let mut rng = Noise::new(0x51ed_2701);
    let mut lowpass = OnePole::new(0.35);
    generate(seconds, |t, envelope| {
        let body = (t * hz * std::f32::consts::TAU).sin() * tone;
        // The noise is only in the attack — past a few milliseconds a click is
        // just a pitched decay, and leaving the hiss in makes it sound broken.
        let transient = lowpass.step(rng.next()) * noise * (-t * 260.0).exp();
        (body + transient) * envelope
    })
}

/// Stacked sines, no noise: a clean bell-like ping.
fn chime(seconds: f32, partials: &[f32]) -> Vec<f32> {
    generate(seconds, |t, envelope| {
        let sum: f32 = partials
            .iter()
            .enumerate()
            .map(|(i, hz)| {
                // Upper partials fade faster, which is what stops a stack of
                // sines sounding like a synthesiser preset.
                (t * hz * std::f32::consts::TAU).sin() * (-t * 26.0 * (i + 1) as f32).exp()
            })
            .sum();
        sum / partials.len() as f32 * envelope
    })
}

/// Bright noise over a low thump.
fn crash(seconds: f32) -> Vec<f32> {
    let mut rng = Noise::new(0x9e37_79b9);
    let mut highpass = OnePole::new(0.85);
    generate(seconds, |t, envelope| {
        let raw = rng.next();
        let shimmer = raw - highpass.step(raw);
        let body = (t * 120.0 * std::f32::consts::TAU).sin() * (-t * 14.0).exp() * 0.5;
        (shimmer * 0.7 + body) * envelope
    })
}

/// Two bursts a few milliseconds apart — the doubling is what makes a clap read
/// as a clap rather than as a shorter crash.
fn clap() -> Vec<f32> {
    let mut rng = Noise::new(0x1234_5678);
    let mut band = OnePole::new(0.55);
    let gap = (0.008 * SAMPLE_RATE as f32) as usize;
    let mut out = generate(0.09, |t, envelope| {
        band.step(rng.next()) * envelope * (-t * 40.0).exp()
    });

    let first = out.clone();
    for (i, value) in first.iter().enumerate() {
        if let Some(slot) = out.get_mut(i + gap) {
            *slot += value * 0.75;
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
            // A 1ms fade in stops the discontinuity at sample zero from
            // clicking; the exponential decay does the rest.
            let attack = (t * 1_000.0).min(1.0);
            let decay = (-t / (seconds * 0.32)).exp();
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

/// One-pole filter, used to take the edge off raw noise.
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
