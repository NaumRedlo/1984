//! The osu! mod bitfield.
//!
//! Kept as a thin wrapper over the raw `u32` rather than an enum set: the field
//! carries bits we don't model yet (per-key mania mods, ScoreV2), and throwing
//! them away on parse would make a re-serialised replay differ from the input.

use std::fmt;

/// Mod bits, in the order osu! assigns them.
pub mod bits {
    pub const NO_FAIL: u32 = 1 << 0;
    pub const EASY: u32 = 1 << 1;
    pub const TOUCH_DEVICE: u32 = 1 << 2;
    pub const HIDDEN: u32 = 1 << 3;
    pub const HARD_ROCK: u32 = 1 << 4;
    pub const SUDDEN_DEATH: u32 = 1 << 5;
    pub const DOUBLE_TIME: u32 = 1 << 6;
    pub const RELAX: u32 = 1 << 7;
    pub const HALF_TIME: u32 = 1 << 8;
    pub const NIGHTCORE: u32 = 1 << 9;
    pub const FLASHLIGHT: u32 = 1 << 10;
    pub const AUTOPLAY: u32 = 1 << 11;
    pub const SPUN_OUT: u32 = 1 << 12;
    pub const AUTOPILOT: u32 = 1 << 13;
    pub const PERFECT: u32 = 1 << 14;
    pub const KEY4: u32 = 1 << 15;
    pub const KEY5: u32 = 1 << 16;
    pub const KEY6: u32 = 1 << 17;
    pub const KEY7: u32 = 1 << 18;
    pub const KEY8: u32 = 1 << 19;
    pub const FADE_IN: u32 = 1 << 20;
    pub const RANDOM: u32 = 1 << 21;
    pub const CINEMA: u32 = 1 << 22;
    pub const TARGET: u32 = 1 << 23;
    pub const KEY9: u32 = 1 << 24;
    pub const KEY_COOP: u32 = 1 << 25;
    pub const KEY1: u32 = 1 << 26;
    pub const KEY3: u32 = 1 << 27;
    pub const KEY2: u32 = 1 << 28;
    pub const SCORE_V2: u32 = 1 << 29;
    pub const MIRROR: u32 = 1 << 30;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Mods(pub u32);

impl Mods {
    pub fn new(raw: u32) -> Self {
        Self(raw)
    }

    pub fn raw(self) -> u32 {
        self.0
    }

    /// The same mods as lazer would name them, with no settings.
    ///
    /// For a replay that predates the block lazer appends — or one from
    /// stable, read under lazer's rules — the bitmask is all there is. Every
    /// mod comes out as left-as-default, because the bitmask cannot say
    /// otherwise.
    pub fn as_lazer_mods(self) -> Vec<crate::LazerMod> {
        [
            (bits::NO_FAIL, "NF"),
            (bits::EASY, "EZ"),
            (bits::TOUCH_DEVICE, "TD"),
            (bits::HIDDEN, "HD"),
            (bits::HARD_ROCK, "HR"),
            (bits::SUDDEN_DEATH, "SD"),
            (bits::DOUBLE_TIME, "DT"),
            (bits::RELAX, "RX"),
            (bits::HALF_TIME, "HT"),
            (bits::NIGHTCORE, "NC"),
            (bits::FLASHLIGHT, "FL"),
            (bits::SPUN_OUT, "SO"),
            (bits::AUTOPILOT, "AP"),
            (bits::PERFECT, "PF"),
            (bits::TARGET, "TP"),
            (bits::MIRROR, "MR"),
        ]
        .into_iter()
        // Nightcore and Perfect set their weaker partner's bit as well, and
        // naming both would multiply the same thing twice.
        .filter(|&(bit, _)| match bit {
            bits::DOUBLE_TIME => !self.contains(bits::NIGHTCORE),
            bits::SUDDEN_DEATH => !self.contains(bits::PERFECT),
            _ => true,
        })
        .filter(|&(bit, _)| self.contains(bit))
        .map(|(_, acronym)| crate::LazerMod::plain(acronym))
        .collect()
    }

    pub fn contains(self, bit: u32) -> bool {
        self.0 & bit != 0
    }

    pub fn is_empty(self) -> bool {
        self.0 == 0
    }

    /// Playback rate the mods impose. Nightcore sets DoubleTime's bit too, so
    /// checking DT alone covers both; the two never apply together.
    pub fn speed_multiplier(self) -> f64 {
        if self.contains(bits::DOUBLE_TIME) {
            1.5
        } else if self.contains(bits::HALF_TIME) {
            0.75
        } else {
            1.0
        }
    }

    /// Acronyms in the order osu! displays them, e.g. `HDDT`.
    ///
    /// Nightcore and Perfect are rendered as themselves even though they carry
    /// DoubleTime/SuddenDeath alongside — showing "DTNC" would be wrong.
    pub fn acronyms(self) -> Vec<&'static str> {
        use bits as b;
        let mut out = Vec::new();
        let has = |bit: u32| self.0 & bit != 0;

        if has(b::EASY) {
            out.push("EZ");
        }
        if has(b::NO_FAIL) {
            out.push("NF");
        }
        if has(b::HALF_TIME) {
            out.push("HT");
        }
        if has(b::HIDDEN) {
            out.push("HD");
        }
        if has(b::HARD_ROCK) {
            out.push("HR");
        }
        if has(b::PERFECT) {
            out.push("PF");
        } else if has(b::SUDDEN_DEATH) {
            out.push("SD");
        }
        if has(b::NIGHTCORE) {
            out.push("NC");
        } else if has(b::DOUBLE_TIME) {
            out.push("DT");
        }
        if has(b::FLASHLIGHT) {
            out.push("FL");
        }
        if has(b::RELAX) {
            out.push("RX");
        }
        if has(b::AUTOPILOT) {
            out.push("AP");
        }
        if has(b::SPUN_OUT) {
            out.push("SO");
        }
        if has(b::TOUCH_DEVICE) {
            out.push("TD");
        }
        if has(b::MIRROR) {
            out.push("MR");
        }
        if has(b::SCORE_V2) {
            out.push("V2");
        }
        if has(b::AUTOPLAY) {
            out.push("AT");
        }
        if has(b::CINEMA) {
            out.push("CN");
        }
        if has(b::TARGET) {
            out.push("TP");
        }
        out
    }
}

impl fmt::Display for Mods {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let acronyms = self.acronyms();
        if acronyms.is_empty() {
            write!(f, "NM")
        } else {
            write!(f, "{}", acronyms.concat())
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GameMode {
    Standard,
    Taiko,
    Catch,
    Mania,
}

impl GameMode {
    pub(crate) fn from_byte(value: u8) -> Option<Self> {
        Some(match value {
            0 => Self::Standard,
            1 => Self::Taiko,
            2 => Self::Catch,
            3 => Self::Mania,
            _ => return None,
        })
    }
}
