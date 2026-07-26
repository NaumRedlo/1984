//! The character of the hit sounds.
//!
//! A skin is not only a picture. Four unrelated noises don't become a kit just
//! by being played together — what makes a set of hit sounds feel like *one
//! instrument* is that they share a tonal centre, a decay character and a
//! balance between pitch and noise. Those are the knobs here, and every voice
//! is derived from them rather than tuned in isolation.

/// Named sound identities.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Kit {
    /// Tonal centre of the plain hit, in Hz. Everything else is an interval
    /// away from it, which is what keeps the set sounding related.
    pub root_hz: f32,
    /// How much of a hit is pitch and how much is noise, 0 to 1. High is a
    /// wooden knock; low is a dry tick.
    pub tone: f32,
    /// Multiplies every decay. Under one is drier and tighter.
    pub decay: f32,
    /// Cutoff character of the noisy parts, 0 to 1. High is airy and bright.
    pub brightness: f32,
    /// Overall level, applied on top of each voice's own balance.
    pub level: f32,
}

impl Kit {
    /// A neutral, bright kit close to what most players expect.
    pub fn plain() -> Self {
        Self {
            root_hz: 320.0,
            tone: 0.55,
            decay: 1.0,
            brightness: 0.55,
            level: 1.0,
        }
    }

    /// Dossier's own: dark, dry and low, to sit under music rather than on top
    /// of it.
    ///
    /// The root is a low G, and the accents are a fifth and two octaves above
    /// it — consonant intervals, so a dense stream reads as a rhythm being
    /// played rather than as a stream of separate clicks. Decays are cut
    /// short: on a 270bpm map anything that rings is still ringing when the
    /// next note lands.
    pub fn nineteen_eightyfour() -> Self {
        Self {
            root_hz: 196.0,
            tone: 0.72,
            decay: 0.62,
            brightness: 0.34,
            level: 1.0,
        }
    }
}

impl Default for Kit {
    fn default() -> Self {
        Self::plain()
    }
}

impl Kit {
    /// Frequency an interval above the root, in semitones.
    pub(crate) fn interval(&self, semitones: f32) -> f32 {
        self.root_hz * 2.0_f32.powf(semitones / 12.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Voice;

    #[test]
    fn the_house_kit_is_darker_and_drier_than_the_plain_one() {
        let plain = Kit::plain();
        let house = Kit::nineteen_eightyfour();
        assert!(house.root_hz < plain.root_hz, "lower");
        assert!(house.decay < plain.decay, "shorter");
        assert!(house.brightness < plain.brightness, "duller");
    }

    #[test]
    fn a_shorter_decay_makes_shorter_sounds() {
        // The knob has to do what it says, or a "dry" kit is dry in name only.
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
    fn every_voice_is_tuned_from_the_root() {
        // Retuning the kit has to move the whole set, or one sound ends up out
        // of key with the rest.
        let low = Kit {
            root_hz: 100.0,
            ..Kit::plain()
        };
        let high = Kit {
            root_hz: 400.0,
            ..Kit::plain()
        };
        for voice in [Voice::Normal, Voice::Whistle, Voice::Finish, Voice::Tick] {
            let a = zero_crossings(&voice.render(&low));
            let b = zero_crossings(&voice.render(&high));
            assert!(b > a, "{voice:?} ignored the root: {a} vs {b}");
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
