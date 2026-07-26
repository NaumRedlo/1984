//! The `[Difficulty]` section and the values the game derives from it.
//!
//! The raw numbers are stored as authored; the derived ones (approach preempt,
//! hit windows, circle radius) live here rather than in the simulator because
//! they're pure functions of this section and every consumer needs the same
//! answer.

/// Defaults are what osu! assumes when a field is absent, which happens a lot
/// in maps from the early file-format versions.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Difficulty {
    pub hp_drain: f64,
    pub circle_size: f64,
    pub overall_difficulty: f64,
    pub approach_rate: f64,
    pub slider_multiplier: f64,
    pub slider_tick_rate: f64,
}

impl Default for Difficulty {
    fn default() -> Self {
        Self {
            hp_drain: 5.0,
            circle_size: 5.0,
            overall_difficulty: 5.0,
            approach_rate: 5.0,
            slider_multiplier: 1.4,
            slider_tick_rate: 1.0,
        }
    }
}

/// Linear interpolation osu! uses for every difficulty-derived value: `mid` at
/// 5, `min` at 0, `max` at 10, with the two halves scaled separately.
fn difficulty_range(value: f64, min: f64, mid: f64, max: f64) -> f64 {
    if value > 5.0 {
        mid + (max - mid) * (value - 5.0) / 5.0
    } else if value < 5.0 {
        mid - (mid - min) * (5.0 - value) / 5.0
    } else {
        mid
    }
}

impl Difficulty {
    /// How long an object is visible before it must be hit, in milliseconds.
    pub fn preempt_ms(&self) -> f64 {
        difficulty_range(self.approach_rate, 1800.0, 1200.0, 450.0)
    }

    /// Fade-in duration, which osu! ties to preempt rather than to AR directly.
    pub fn fade_in_ms(&self) -> f64 {
        self.preempt_ms() * 0.66
    }

    /// Half-width of the 300/100/50 judgement windows, in milliseconds. A hit
    /// counts as a 300 while `|error| <= hit_window_300()`, and so on outward.
    pub fn hit_window_300(&self) -> f64 {
        difficulty_range(self.overall_difficulty, 80.0, 50.0, 20.0)
    }

    pub fn hit_window_100(&self) -> f64 {
        difficulty_range(self.overall_difficulty, 140.0, 100.0, 60.0)
    }

    pub fn hit_window_50(&self) -> f64 {
        difficulty_range(self.overall_difficulty, 200.0, 150.0, 100.0)
    }

    /// Circle radius in osu!pixels, on the 512×384 playfield.
    pub fn circle_radius(&self) -> f64 {
        54.4 - 4.48 * self.circle_size
    }

    /// Full rotations a spinner demands per second of its duration.
    ///
    /// osu! states this as revolutions per minute — `100 + 15 * OD`, so OD5
    /// asks for 175rpm and OD10 for 250. That is a rate ordinary players clear
    /// comfortably, which is the point: spinners are a formality for anyone who
    /// can play the map, not a second skill check.
    pub fn spins_per_second(&self) -> f64 {
        (100.0 + 15.0 * self.overall_difficulty) / 60.0
    }

    /// HardRock: every stat harder, capped at 10.
    ///
    /// CS scales less than the rest — 1.3 against 1.4 — which is the game's
    /// rule, not a rounding artefact.
    pub fn hard_rock(&self) -> Self {
        Self {
            hp_drain: (self.hp_drain * 1.4).min(10.0),
            circle_size: (self.circle_size * 1.3).min(10.0),
            overall_difficulty: (self.overall_difficulty * 1.4).min(10.0),
            approach_rate: (self.approach_rate * 1.4).min(10.0),
            ..*self
        }
    }

    /// Easy: every stat halved. No cap needed — halving can't exceed 10.
    pub fn easy(&self) -> Self {
        Self {
            hp_drain: self.hp_drain * 0.5,
            circle_size: self.circle_size * 0.5,
            overall_difficulty: self.overall_difficulty * 0.5,
            approach_rate: self.approach_rate * 0.5,
            ..*self
        }
    }
}
