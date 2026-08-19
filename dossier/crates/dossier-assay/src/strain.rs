//! Strain summed over sections of variable length — the model aim uses.
//!
//! Ported from `VariableLengthStrainSkill`. [`crate::speed`] sums differently,
//! harmonically over objects, and the two are not interchangeable.
//!
//! # What it is doing
//!
//! A map is cut into sections and only the hardest moment of each is kept. The
//! sections are not a fixed grid: a section ends early when a harder moment
//! arrives, so a spike starts its own section rather than being averaged into
//! the calm around it. The peaks are then sorted hardest first and summed with
//! a weight that decays down the list, so the map's hardest half-second counts
//! for far more than its hundredth-hardest.
//!
//! The queue is the part that repays reading twice. When a section runs out of
//! objects — a break, a long slider — the next section does not start from
//! nothing, because the strain a player was carrying does not vanish. It starts
//! from whatever strain was queued behind the peak, and only from the current
//! object once the queue is spent. Without it a gap would show up as a cliff.

/// The hardest moment of one section, and how long the section ran.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct StrainPeak {
    pub value: f64,
    /// Rounded, as ppy round it — `SectionLength = Math.Round(sectionLength)`.
    pub section_length: f64,
}

impl StrainPeak {
    pub fn new(value: f64, section_length: f64) -> Self {
        Self { value, section_length: section_length.round() }
    }
}

/// How far down the sorted peaks the weight is still worth carrying.
///
/// ```csharp
/// maxStoredLength = 11 / (1 - DecayWeight);
/// ```
///
/// At a decay of 0.9 that is a hundred and ten sections, which ppy note keeps
/// "at least 99.999% of the difficulty value". Everything past it is dropped so
/// a long map does not carry thousands of peaks that change no answer.
fn max_stored_length(decay_weight: f64) -> f64 {
    11.0 / (1.0 - decay_weight)
}

/// The peaks of one skill, as they are collected.
pub struct Sections {
    pub decay_weight: f64,
    pub max_section_length: f64,
    peak: f64,
    begin: f64,
    end: f64,
    /// Sorted hardest first, which is what lets the tail be dropped.
    peaks: Vec<StrainPeak>,
    total_length: f64,
    /// Strains that were not peaks but are still carried, oldest first.
    queued: Vec<(f64, f64)>,
    /// The section still open, once it has been added for reading.
    final_peak: Option<StrainPeak>,
}

impl Sections {
    pub fn new(decay_weight: f64, max_section_length: f64) -> Self {
        Self {
            decay_weight,
            max_section_length,
            peak: 0.0,
            begin: 0.0,
            end: 0.0,
            peaks: Vec::new(),
            total_length: 0.0,
            queued: Vec::new(),
            final_peak: None,
        }
    }

    /// Open the first section around the first object.
    pub fn begin_at(&mut self, start_time: f64, strain: f64) {
        self.begin = start_time;
        self.end = start_time + self.max_section_length;
        self.peak = strain;
    }

    /// Take one object's strain, having already carried the sections forward
    /// to reach it.
    ///
    /// `initial_strain` is what the strain decays to at the start of a new
    /// section — the skill knows how to work that out and this does not.
    pub fn take(&mut self, start_time: f64, strain: f64, initial_strain: &dyn Fn(f64) -> f64) {
        self.backfill(start_time, initial_strain);

        if strain > self.peak {
            // A harder moment: nothing queued behind the old peak can matter
            // any more, and the old section ends here rather than on the grid.
            self.queued.clear();
            self.save(start_time - self.begin);
            self.begin = start_time;
            self.end = start_time + self.max_section_length;
            self.peak = strain;
        } else {
            // Anything smaller than this is now unreachable behind it.
            while self.queued.last().is_some_and(|(value, _)| *value < strain) {
                self.queued.pop();
            }
            self.queued.push((strain, start_time));
        }
    }

    /// Carry the sections forward until the current object falls inside one.
    fn backfill(&mut self, start_time: f64, initial_strain: &dyn Fn(f64) -> f64) {
        while start_time > self.end {
            self.save(self.end - self.begin);
            self.begin = self.end;

            if self.queued.is_empty() {
                // Nothing carried over, so the new section is an ordinary one.
                self.end = self.begin + self.max_section_length;
                self.peak = initial_strain(self.begin);
            } else {
                let (strain, at) = self.queued.remove(0);
                // Ended a section's length after the strain being leaned on,
                // not after the section began, so a queued strain sits in a
                // section of its own when the gap is long enough. Without that
                // two sections either side of a gap differ harshly.
                self.end = at + self.max_section_length;
                self.peak = initial_strain(self.begin);
                self.peak = self.peak.max(strain);
            }
        }
    }

    fn save(&mut self, section_length: f64) {
        if let Some(open) = self.final_peak.take() {
            if let Some(at) = self.peaks.iter().position(|peak| *peak == open) {
                self.peaks.remove(at);
            }
        }

        let peak = StrainPeak::new(self.peak, section_length);
        let at = self
            .peaks
            .partition_point(|other| other.value > peak.value);
        self.peaks.insert(at, peak);
        self.total_length += peak.section_length;

        // Drop from the easy end once there is more here than can matter.
        while self.total_length > max_stored_length(self.decay_weight) * self.max_section_length {
            if let Some(dropped) = self.peaks.pop() {
                self.total_length -= dropped.section_length;
            } else {
                break;
            }
        }
    }

    /// Every peak, hardest first, including the section still open.
    pub fn peaks(&mut self) -> &[StrainPeak] {
        if self.final_peak.is_none() {
            let peak = StrainPeak::new(self.peak, self.end - self.begin);
            let at = self
                .peaks
                .partition_point(|other| other.value > peak.value);
            self.peaks.insert(at, peak);
            self.final_peak = Some(peak);
        }
        &self.peaks
    }
}

fn lerp(from: f64, to: f64, at: f64) -> f64 {
    from + (to - from) * at
}

/// The peaks with the hardest ones brought down, sorted hardest first.
///
/// Ported from `Aim.getReducedStrainPeaks`. The first four seconds' worth of
/// peaks are replaced by twenty-millisecond chunks of themselves, each scaled
/// up from a baseline towards its full value along a logarithm — so the very
/// hardest moment of a map counts for about three quarters of what it is worth
/// and the fourth second onward counts fully.
///
/// Splitting into chunks rather than scaling each peak whole is ppy's own
/// answer to sections having different lengths: without it, how much a spike
/// was reduced would depend on how long the section it happened to fall in was.
pub fn reduced_peaks(peaks: &[StrainPeak]) -> Vec<StrainPeak> {
    const REDUCED_SECTION_TIME: f64 = 4000.0;
    const REDUCED_STRAIN_BASELINE: f64 = 0.727;
    const CHUNK_SIZE: f64 = 20.0;

    let mut strains: Vec<StrainPeak> =
        peaks.iter().copied().filter(|peak| peak.value > 0.0).collect();

    let mut time = 0.0;
    let mut skip = 0usize;
    while strains.len() > skip && time < REDUCED_SECTION_TIME {
        let strain = strains[skip];
        let mut added = 0.0;
        while added < strain.section_length {
            let scale =
                lerp(1.0, 10.0, ((time + added) / REDUCED_SECTION_TIME).clamp(0.0, 1.0)).log10();
            // Added at the end and sorted afterwards, which ppy note is cheaper.
            strains.push(StrainPeak::new(
                strain.value * lerp(REDUCED_STRAIN_BASELINE, 1.0, scale),
                CHUNK_SIZE.min(strain.section_length - added),
            ));
            added += CHUNK_SIZE;
        }
        time += strain.section_length;
        skip += 1;
    }

    let mut out: Vec<StrainPeak> = strains.split_off(skip.min(strains.len()));
    out.sort_by(|a, b| b.value.partial_cmp(&a.value).unwrap_or(std::cmp::Ordering::Equal));
    out
}

/// The weighted sum of the peaks.
///
/// Ported from `Aim.DifficultyValue`. The weight is the area under
/// `decay^x` across the section, so a long hard stretch counts for more than a
/// short one at the same strain — which is the whole reason sections have
/// lengths rather than being counted one apiece.
pub fn difficulty_value(peaks: &[StrainPeak], decay_weight: f64, max_section_length: f64) -> f64 {
    let mut difficulty = 0.0;
    let mut time = 0.0;
    for peak in peaks {
        let start = time;
        let end = time + peak.section_length / max_section_length;
        difficulty += peak.value * (decay_weight.powf(start) - decay_weight.powf(end));
        time = end;
    }
    difficulty / (1.0 - decay_weight)
}
