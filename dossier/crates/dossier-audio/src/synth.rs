//! Making the sounds.
//!
//! One synthesiser, five settings. Every percussive voice is the same idea —
//! noise through a resonant band-pass, optionally over a pitched body that
//! falls as it decays — and the pack decides where the filter sits, how tight
//! it is, and how much body there is. Tonal voices skip the noise entirely and
//! stack partials instead.
//!
//! Each one-shot is generated once and stamped wherever it's needed. Generating
//! per hit would repeat identical arithmetic thousands of times over a map.

use crate::kit::{Kit, Recipe};
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
        let recipe = kit.timbre.recipe();
        let seconds = |base: f32| base * recipe.length * kit.decay;
        let hz = |multiple: f32| recipe.centre * multiple * kit.pitch;

        match self {
            Self::Normal => strike(seconds(0.038), hz(1.0), &recipe, 0x51ed_2701),
            // Tonal even in the noisy packs: an accent has to read as *pitched*
            // against a stream of clicks, or it isn't an accent.
            Self::Whistle => ring(seconds(0.075), hz(1.5), recipe.partials),
            Self::Finish => splash(seconds(0.30), hz(0.55), &recipe),
            Self::Clap => clap(seconds(0.055), hz(1.35), &recipe),
            // Higher and quieter than the plain hit: present, never in the way.
            Self::Tick => strike(seconds(0.018), hz(2.2), &recipe, 0x1234_5678),
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

/// The workhorse: band-passed noise over an optional falling body.
fn strike(seconds: f32, hz: f32, recipe: &Recipe, seed: u32) -> Vec<f32> {
    let mut rng = Noise::new(seed);
    let mut filter = Svf::new(hz, recipe.resonance);
    let noise_level = (1.0 - recipe.body) * 1.6;
    // A body an octave under the filter — the filter marks the attack, the
    // body carries the weight.
    let body_hz = hz * 0.5;
    let mut phase = 0.0f32;

    envelope(seconds, recipe.attack_ms, |t, envelope| {
        let noisy = filter.band(rng.next()) * noise_level;

        // The drop is what separates a drum from a beep. Advancing the phase
        // rather than evaluating sin(t * hz) is what makes a *changing*
        // frequency come out smooth instead of stepping.
        let progress = (t / seconds).min(1.0);
        let current = body_hz * (1.0 - recipe.droop * progress);
        phase += current / SAMPLE_RATE as f32 * std::f32::consts::TAU;
        let body = phase.sin() * recipe.body;

        (noisy + body) * envelope
    })
}

/// Stacked partials, no noise: struck rather than hit.
fn ring(seconds: f32, hz: f32, partials: usize) -> Vec<f32> {
    // Ratios of a struck bar rather than a harmonic series — whole-number
    // harmonics sound like an organ, not like something solid being hit.
    const RATIOS: [f32; 3] = [1.0, 2.76, 5.40];
    let count = partials.clamp(1, RATIOS.len());

    envelope(seconds, 1.0, |t, envelope| {
        let sum: f32 = RATIOS[..count]
            .iter()
            .enumerate()
            .map(|(i, ratio)| {
                // Upper partials die first, as they do on anything real. The
                // fundamental gets no extra decay of its own — the shared
                // envelope already ends the sound, and decaying it twice made
                // short packs inaudible.
                let decay = (-t / seconds.max(0.005) * 2.0 * i as f32).exp();
                (t * hz * ratio * std::f32::consts::TAU).sin() * decay
            })
            .sum();
        sum / count as f32 * envelope
    })
}

/// Wide bright noise with a long tail — a cymbal rather than a click.
fn splash(seconds: f32, hz: f32, recipe: &Recipe) -> Vec<f32> {
    let mut rng = Noise::new(0x9e37_79b9);
    let mut low = OnePole::new(0.72);
    let mut phase = 0.0f32;

    envelope(seconds, recipe.attack_ms, |_, envelope| {
        let raw = rng.next();
        // Subtracting a lowpass leaves the top end, which is all a splash is —
        // and only a fraction of the amplitude, hence the make-up gain.
        let shimmer = (raw - low.step(raw)) * 2.4 * (1.0 - recipe.body * 0.5);
        phase += hz / SAMPLE_RATE as f32 * std::f32::consts::TAU;
        (shimmer + phase.sin() * recipe.body * 0.6) * envelope
    })
}

/// Two bursts a few milliseconds apart. The doubling is what makes a clap read
/// as a clap rather than as a shorter, duller hit.
fn clap(seconds: f32, hz: f32, recipe: &Recipe) -> Vec<f32> {
    let gap = (0.007 * SAMPLE_RATE as f32) as usize;
    let mut out = strike(seconds, hz, recipe, 0xa5a5_1234);

    let first = out.clone();
    for (i, value) in first.iter().enumerate() {
        if let Some(slot) = out.get_mut(i + gap) {
            *slot += value * 0.8;
        }
    }
    out
}

/// Run `voice` over `seconds` under the shared attack/decay shape.
fn envelope(seconds: f32, attack_ms: f32, mut voice: impl FnMut(f32, f32) -> f32) -> Vec<f32> {
    let samples = (seconds * SAMPLE_RATE as f32) as usize;
    let attack_rate = 1_000.0 / attack_ms.max(0.05);
    (0..samples)
        .map(|i| {
            let t = i as f32 / SAMPLE_RATE as f32;
            // The rise stops the discontinuity at sample zero from clicking,
            // and past about a millisecond it stops sounding like a snap —
            // which is the whole of the difference between a click and a tap.
            let attack = (t * attack_rate).min(1.0);
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
