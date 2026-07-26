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

/// Which bank of sounds a note is played from.
///
/// Deliberately a beatmap concept and not an audio one: the file says "soft",
/// and what a "soft" sound *is* belongs to whoever is making noise. The audio
/// crate has its own idea of the same three names and the two meet at the
/// caller, which keeps a beatmap parser from depending on a synthesiser.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SampleSet {
    /// `0` in the file — inherit, which at the top level means normal.
    #[default]
    Normal,
    Soft,
    Drum,
}

impl SampleSet {
    /// `1`, `2`, `3`; anything else, including the `0` that means "inherit",
    /// resolves to normal.
    pub fn from_code(code: u8) -> Self {
        match code {
            2 => Self::Soft,
            3 => Self::Drum,
            _ => Self::Normal,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::Soft => "soft",
            Self::Drum => "drum",
        }
    }
}

/// The sample bank and volume in force from `time_ms` on.
///
/// Every timing point carries these, red or green alike — which is why they
/// live in a list of their own rather than on either kind. Splitting the file's
/// one line type into tempo and velocity was right; splitting sound along with
/// it would have lost the ordering between them.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SamplePoint {
    pub time_ms: f64,
    pub set: SampleSet,
    /// Custom sample index; `0` means the skin's default files.
    pub index: u32,
    /// 0–100.
    pub volume: u8,
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
    /// One per timing point of either kind, in time order.
    pub samples: Vec<SamplePoint>,
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

impl Timing {
    /// The sample bank and volume in force at `time_ms`.
    ///
    /// Before the first timing point there is nothing to inherit, so the
    /// caller gets `None` and can fall back to the defaults rather than being
    /// handed an invented point.
    pub fn sample_point_at(&self, time_ms: f64) -> Option<&SamplePoint> {
        let index = self.samples.partition_point(|p| p.time_ms <= time_ms);
        if index == 0 {
            // A note a hair before the first line still belongs to it.
            self.samples.first()
        } else {
            self.samples.get(index - 1)
        }
    }
}
