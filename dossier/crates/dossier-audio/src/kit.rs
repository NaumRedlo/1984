//! The character of the hit sounds.
//!
//! Three knobs, deliberately. An earlier version had five — a tonal root, a
//! tone/noise balance, a brightness — and used them to build something with a
//! character of its own. A hit sound with a character of its own turns out to
//! be one you notice rather than one you *use*: what a skin is actually asked
//! for is a short, dry, bright click that marks the beat and gets out of the
//! way. The design does that now, and the knobs only shift it rather than
//! reinvent it.

/// Named sound identities.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Kit {
    /// Multiplies every frequency in the set, so the whole kit moves together.
    pub pitch: f32,
    /// Multiplies every decay. Under one is tighter.
    pub decay: f32,
    /// Overall level, on top of each voice's own balance.
    pub level: f32,
}

impl Kit {
    /// The ordinary click most skins use: short, dry and bright.
    pub fn plain() -> Self {
        Self {
            pitch: 1.0,
            decay: 1.0,
            level: 2.0,
        }
    }

    /// Dossier's own — the same click, a little deeper and a little tighter.
    ///
    /// A house style, not a different instrument. The first attempt at this
    /// *was* a different instrument: low, dark, long. It was unusable — masked
    /// by any loud master, and distracting when it wasn't.
    pub fn nineteen_eightyfour() -> Self {
        Self {
            pitch: 0.88,
            decay: 0.85,
            level: 2.1,
        }
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
    fn the_house_kit_is_deeper_and_tighter_than_the_plain_one() {
        let plain = Kit::plain();
        let house = Kit::nineteen_eightyfour();
        assert!(house.pitch < plain.pitch, "deeper");
        assert!(house.decay < plain.decay, "tighter");
    }

    #[test]
    fn a_shorter_decay_makes_shorter_sounds() {
        // The knob has to do what it says, or "tighter" is a label rather than
        // a property.
        let long = Voice::Normal.render(&Kit::plain());
        let short = Voice::Normal.render(&Kit::nineteen_eightyfour());
        assert!(
            short.len() < long.len(),
            "{} vs {}",
            short.len(),
            long.len()
        );
    }

    #[test]
    fn pitch_moves_the_whole_set_together() {
        // Shifting one voice and not the others is how a kit stops sounding
        // like one kit.
        let low = Kit {
            pitch: 0.5,
            ..Kit::plain()
        };
        let high = Kit {
            pitch: 2.0,
            ..Kit::plain()
        };
        for voice in [Voice::Normal, Voice::Whistle, Voice::Clap, Voice::Tick] {
            let a = zero_crossings(&voice.render(&low));
            let b = zero_crossings(&voice.render(&high));
            assert!(b > a, "{voice:?} ignored the pitch: {a} vs {b}");
        }
    }

    /// A rough pitch proxy: higher sounds cross zero more often.
    fn zero_crossings(samples: &[f32]) -> usize {
        samples
            .windows(2)
            .filter(|w| (w[0] < 0.0) != (w[1] < 0.0))
            .count()
    }
}
