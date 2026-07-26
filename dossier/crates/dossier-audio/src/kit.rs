//! Sample packs, and the knobs that shift them.
//!
//! Two different things live here and it's worth keeping them apart. A
//! [`Timbre`] is a *choice* — which recipe the synthesiser follows, and what
//! the pack fundamentally sounds like. The three numbers alongside it are
//! *tuning* — how high, how long, how loud that recipe is played.
//!
//! An earlier version had no timbres and five tuning knobs, which meant every
//! pack was the same sound wearing a different hat. Character comes from the
//! recipe; the knobs only move it.

/// What a pack is made of.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Timbre {
    /// Noise through a tight band-pass: the ordinary bright skin click.
    Click,
    /// The same, rounded — lower, slower to start, no edge on it.
    Soft,
    /// Percussion: a pitched body that drops as it decays, noise on the front.
    Drum,
    /// Struck glass. Tuned partials, no noise, a long ring.
    Glass,
    /// A woodblock knock: very short, very tight, a hint of pitch.
    Wood,
}

impl Timbre {
    pub const ALL: [Self; 5] = [Self::Click, Self::Soft, Self::Drum, Self::Glass, Self::Wood];

    pub fn name(self) -> &'static str {
        match self {
            Self::Click => "click",
            Self::Soft => "soft",
            Self::Drum => "drum",
            Self::Glass => "glass",
            Self::Wood => "wood",
        }
    }

    /// The numbers the synthesiser actually reads.
    pub(crate) fn recipe(self) -> Recipe {
        match self {
            // Bright, dry, and out of the way — what most skins use.
            Self::Click => Recipe {
                centre: 1_100.0,
                resonance: 2.6,
                body: 0.0,
                droop: 0.0,
                length: 1.0,
                attack_ms: 0.5,
                partials: 1,
            },
            // Lower and slower to start. The soft attack is what takes the
            // click off it: the ear reads a fast rise as a snap regardless of
            // frequency.
            Self::Soft => Recipe {
                centre: 620.0,
                resonance: 1.5,
                body: 0.20,
                droop: 0.0,
                length: 1.6,
                attack_ms: 4.0,
                partials: 1,
            },
            // A pitched body sliding downward is the whole of drum synthesis,
            // and the reason this pack sounds like an instrument rather than a
            // marker.
            Self::Drum => Recipe {
                centre: 900.0,
                resonance: 1.8,
                body: 0.75,
                droop: 0.55,
                length: 2.2,
                attack_ms: 0.4,
                partials: 1,
            },
            // No noise at all. Stacked partials ringing on is what makes glass
            // read as struck rather than hit.
            Self::Glass => Recipe {
                centre: 1_800.0,
                resonance: 4.0,
                body: 0.95,
                droop: 0.0,
                length: 2.8,
                attack_ms: 1.0,
                partials: 3,
            },
            // Shorter than everything else on purpose: a knock is defined by
            // how fast it stops.
            Self::Wood => Recipe {
                centre: 2_300.0,
                resonance: 6.0,
                body: 0.45,
                droop: 0.40,
                length: 0.55,
                attack_ms: 0.3,
                partials: 1,
            },
        }
    }
}

/// The synthesiser's settings for one pack.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Recipe {
    /// Where the band-pass sits for noisy voices, in Hz.
    pub centre: f32,
    /// How tight that band is. High is pitched, low is a hiss.
    pub resonance: f32,
    /// How much pitched body sits under the noise, 0 to 1.
    pub body: f32,
    /// How far the body's pitch falls over its decay, 0 to 1.
    pub droop: f32,
    /// Multiplies every voice's length.
    pub length: f32,
    /// How long the rise takes. Anything under a millisecond reads as a snap.
    pub attack_ms: f32,
    /// Partials on the tonal voices. One is a sine; more is a bell.
    pub partials: usize,
}

/// A pack: a recipe, and how it's played.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Kit {
    pub timbre: Timbre,
    /// Multiplies every frequency, so the whole pack moves together.
    pub pitch: f32,
    /// Multiplies every decay. Under one is tighter.
    pub decay: f32,
    /// Overall level, on top of each voice's own balance.
    pub level: f32,
}

impl Kit {
    /// A pack at its intended tuning.
    pub fn of(timbre: Timbre) -> Self {
        Self {
            timbre,
            pitch: 1.0,
            decay: 1.0,
            level: 2.0,
        }
    }

    /// The ordinary bright click, and the default.
    pub fn plain() -> Self {
        Self::of(Timbre::Click)
    }

    /// Dossier's own: the wood knock, a little deeper and a little tighter.
    ///
    /// Austere and quick, to match a design that is mostly dark field and one
    /// accent colour. It is a tuning of a pack rather than a sixth pack —
    /// there is no point owning a sound nobody else can reach.
    pub fn nineteen_eightyfour() -> Self {
        Self {
            timbre: Timbre::Wood,
            pitch: 0.88,
            decay: 0.85,
            level: 2.1,
        }
    }

    /// Look a pack up by name, for the command line.
    pub fn by_name(name: &str) -> Option<Self> {
        let lower = name.to_ascii_lowercase();
        if lower == "1984" || lower == "dossier" {
            return Some(Self::nineteen_eightyfour());
        }
        Timbre::ALL
            .into_iter()
            .find(|t| t.name() == lower)
            .map(Self::of)
    }
}

impl Default for Kit {
    fn default() -> Self {
        Self::plain()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Voice;

    #[test]
    fn every_pack_can_be_named_and_found_again() {
        for timbre in Timbre::ALL {
            let found = Kit::by_name(timbre.name()).expect("named packs resolve");
            assert_eq!(found.timbre, timbre);
        }
        assert_eq!(Kit::by_name("1984").unwrap().timbre, Timbre::Wood);
        assert!(Kit::by_name("nonsense").is_none());
    }

    #[test]
    fn the_packs_actually_sound_different_from_each_other() {
        // The point of five packs is five sounds. Two that measure the same
        // are one pack with two names.
        let fingerprints: Vec<_> = Timbre::ALL
            .into_iter()
            .map(|t| {
                let rendered = Voice::Normal.render(&Kit::of(t));
                (rendered.len() / 100, zero_crossings(&rendered) / 20)
            })
            .collect();

        for (i, a) in fingerprints.iter().enumerate() {
            for (j, b) in fingerprints.iter().enumerate().skip(i + 1) {
                assert_ne!(
                    a,
                    b,
                    "{} and {} are the same sound",
                    Timbre::ALL[i].name(),
                    Timbre::ALL[j].name()
                );
            }
        }
    }

    #[test]
    fn glass_is_the_one_without_noise_in_it() {
        // A tuned voice crosses zero at a steady rate; noise does not. This is
        // the difference between the packs that ring and the packs that tick.
        let glass = Voice::Normal.render(&Kit::of(Timbre::Glass));
        let click = Voice::Normal.render(&Kit::of(Timbre::Click));
        assert!(
            regularity(&glass) < regularity(&click),
            "glass should be the steadier waveform"
        );
    }

    #[test]
    fn wood_is_the_shortest_pack_and_glass_the_longest() {
        let length = |t: Timbre| Voice::Normal.render(&Kit::of(t)).len();
        assert!(length(Timbre::Wood) < length(Timbre::Click));
        assert!(length(Timbre::Glass) > length(Timbre::Click));
    }

    #[test]
    fn a_shorter_decay_makes_shorter_sounds() {
        let long = Voice::Normal.render(&Kit::plain());
        let short = Voice::Normal.render(&Kit {
            decay: 0.5,
            ..Kit::plain()
        });
        assert!(short.len() < long.len());
    }

    #[test]
    fn pitch_moves_the_whole_pack_together() {
        // Shifting one voice and not the others is how a pack stops sounding
        // like one pack.
        for timbre in Timbre::ALL {
            let low = Kit {
                pitch: 0.5,
                ..Kit::of(timbre)
            };
            let high = Kit {
                pitch: 2.0,
                ..Kit::of(timbre)
            };
            for voice in [Voice::Normal, Voice::Whistle, Voice::Clap, Voice::Tick] {
                let a = zero_crossings(&voice.render(&low));
                let b = zero_crossings(&voice.render(&high));
                assert!(b > a, "{}/{voice:?} ignored the pitch", timbre.name());
            }
        }
    }

    fn zero_crossings(samples: &[f32]) -> usize {
        samples
            .windows(2)
            .filter(|w| (w[0] < 0.0) != (w[1] < 0.0))
            .count()
    }

    /// Spread of the gaps between zero crossings: low for a tone, high for
    /// noise.
    fn regularity(samples: &[f32]) -> f64 {
        let mut gaps = Vec::new();
        let mut last = 0usize;
        for (i, w) in samples.windows(2).enumerate() {
            if (w[0] < 0.0) != (w[1] < 0.0) {
                gaps.push((i - last) as f64);
                last = i;
            }
        }
        if gaps.len() < 3 {
            return f64::MAX;
        }
        let mean = gaps.iter().sum::<f64>() / gaps.len() as f64;
        (gaps.iter().map(|g| (g - mean).powi(2)).sum::<f64>() / gaps.len() as f64).sqrt()
    }
}
