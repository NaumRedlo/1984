//! Timing points.
//!
//! The format overloads one line type for two very different jobs, keyed by the
//! `uninherited` flag:
//!
//! * **uninherited** ("red") — sets the tempo. `beat_length` is milliseconds per
//!   beat, always positive.
//! * **inherited** ("green") — leaves tempo alone and scales slider velocity.
//!   The same field is then a *negative* number whose magnitude is `100 / SV`,
//!   so `-50` means double speed and `-200` means half.
//!
//! Storing them in one list the way the file does would push that sign trick
//! onto every caller, so they're split apart on parse.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TimingPoint {
    pub time_ms: f64,
    /// Milliseconds per beat. Always positive.
    pub beat_length: f64,
    pub meter: u32,
    /// Kiai time is bit 0 of the effects field.
    pub kiai: bool,
}

impl TimingPoint {
    pub fn bpm(&self) -> f64 {
        if self.beat_length > 0.0 {
            60_000.0 / self.beat_length
        } else {
            0.0
        }
    }
}

/// A green line: a slider-velocity multiplier that applies from `time_ms` on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VelocityPoint {
    pub time_ms: f64,
    /// Already converted from the file's `-100 / sv` encoding.
    pub velocity: f64,
    pub kiai: bool,
}

/// Both kinds, kept in the order they appeared, with lookups that mirror how
/// the game resolves them: the newest point at or before a given time wins.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Timing {
    pub uninherited: Vec<TimingPoint>,
    pub inherited: Vec<VelocityPoint>,
}

impl Timing {
    /// The tempo in force at `time_ms`.
    ///
    /// Before the first red line the game keeps using that first line rather
    /// than treating the map as having no tempo, so objects placed slightly
    /// early (which happens) still resolve.
    pub fn timing_point_at(&self, time_ms: f64) -> Option<&TimingPoint> {
        if self.uninherited.is_empty() {
            return None;
        }
        let idx = self
            .uninherited
            .partition_point(|p| p.time_ms <= time_ms)
            .saturating_sub(1);
        self.uninherited.get(idx).or(self.uninherited.first())
    }

    /// Slider-velocity multiplier at `time_ms`; 1.0 where no green line applies.
    pub fn velocity_at(&self, time_ms: f64) -> f64 {
        if self.inherited.is_empty() {
            return 1.0;
        }
        let idx = self.inherited.partition_point(|p| p.time_ms <= time_ms);
        if idx == 0 {
            1.0
        } else {
            self.inherited[idx - 1].velocity
        }
    }

    pub fn bpm_at(&self, time_ms: f64) -> f64 {
        self.timing_point_at(time_ms).map_or(0.0, TimingPoint::bpm)
    }
}
