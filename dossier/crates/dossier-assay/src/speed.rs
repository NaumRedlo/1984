//! How hard the map is to *press* — the first skill, and the first thing here
//! that ppy will grade.
//!
//! `speed_difficulty` comes back from the attributes endpoint for every map and
//! mod set in the corpus, so unlike the preprocessing underneath it this can be
//! marked right or wrong outright. It is also what marks the preprocessing:
//! rhythm reads the gaps, double-tapping reads the jump distances, and a
//! mistake in either shows up here as a number that is simply not ppy's.
//!
//! Three pieces, in the order they feed each other:
//!
//! 1. [`speed_difficulty_of`] — how hard one press is, from the gap before it.
//! 2. [`rhythm_multiplier_of`] — how much harder that press is for coming in an
//!    awkward rhythm, judged against the five seconds of play behind it.
//! 3. [`Speed`] — the two combined into a strain that decays between objects,
//!    and the strains summed with the hardest counting most.

use crate::preprocessing::{DiffObject, MIN_DELTA_TIME};
use crate::utils::{
    bpm_to_milliseconds, logistic, milliseconds_to_bpm, reverse_lerp, smoothstep_bell_curve,
};

/// How fast a map has to be before speed alone starts earning a bonus.
const MIN_SPEED_BONUS_BPM: f64 = 200.0;

/// The strain left after a second of nothing.
const STRAIN_DECAY_BASE: f64 = 0.3;

fn strain_decay(ms: f64) -> f64 {
    STRAIN_DECAY_BASE.powf(ms / 1000.0)
}

/// How hard this object is to press, before rhythm.
///
/// Ported from `SpeedEvaluator.EvaluateDifficultyOf`.
pub fn speed_difficulty_of(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    if current.is_spinner {
        return 0.0;
    }

    const SPEED_BALANCING_FACTOR: f64 = 40.0;

    let mut strain_time = current.adjusted_delta_time;
    let feasibility = 1.0 - current.double_tap_feasibility(objects.get(at + 1));

    // A gap shorter than the window a Great is given cannot really be pressed
    // more precisely, so it stops counting as faster.
    //
    // ppy's note on the two constants: "0.93 is derived from making sure 260bpm
    // OD8 streams aren't nerfed harshly, whilst 0.92 limits the effect of the
    // cap."
    strain_time /= ((strain_time / current.hit_window_great) / 0.93).clamp(0.92, 1.0);

    let mut speed_bonus = 0.0;
    if milliseconds_to_bpm(strain_time) > MIN_SPEED_BONUS_BPM {
        speed_bonus = 0.75
            * ((bpm_to_milliseconds(MIN_SPEED_BONUS_BPM) - strain_time) / SPEED_BALANCING_FACTOR)
                .powi(2);
    }

    let mut difficulty = (1.0 + speed_bonus) * 1000.0 / strain_time;
    // Undoes the strain decay for very fast objects, so a stream is not held
    // back by the decay that is about to be applied to it.
    difficulty *= 1.0 / (1.0 - strain_decay(current.adjusted_delta_time));
    difficulty * feasibility
}

/// A run of objects sharing one gap — a stream, a triple, a single note.
///
/// Rhythm is judged by how these fall against each other rather than by the
/// gaps alone: two triples in a row are less interesting than a triple after a
/// double, however fast either is.
#[derive(Debug, Clone, Copy)]
struct Island {
    delta: i64,
    count: i64,
    occurrences: i64,
    /// Whether a delta has been put in yet. ppy uses `int.MaxValue` as the
    /// stand-in; a flag says the same thing without the sentinel arithmetic.
    empty: bool,
}

impl Island {
    fn unset() -> Self {
        Self { delta: i64::MAX, count: 1, occurrences: 1, empty: true }
    }

    fn new(delta: i64) -> Self {
        Self {
            delta: delta.max(MIN_DELTA_TIME as i64),
            count: 1,
            occurrences: 1,
            empty: false,
        }
    }

    fn add_delta(&mut self, delta: i64) {
        if self.empty {
            self.delta = delta.max(MIN_DELTA_TIME as i64);
            self.empty = false;
        }
        self.count += 1;
    }

    /// Same gap, and an odd run against an odd one or an even against an even.
    ///
    /// Two runs of the same parity are tapped with the same hand alternation,
    /// which is what makes the second of them easier than it looks.
    fn is_similar_polarity(&self, other: &Island, epsilon: f64) -> bool {
        if self.count <= 1 || other.count <= 1 {
            return false;
        }
        ((self.delta - other.delta).abs() as f64) < epsilon && self.count % 2 == other.count % 2
    }

    fn almost_equals(&self, other: &Island, epsilon: f64) -> bool {
        ((self.delta - other.delta).abs() as f64) < epsilon && self.count == other.count
    }
}

/// How much of a bonus a change of gap deserves.
///
/// Gaps that are neat multiples of each other — a hundred into two hundred —
/// get almost nothing, because halving or doubling a rhythm is the easiest
/// change there is. The bell peaks where the ratio sits furthest from whole.
fn effective_difficulty(ratio: f64) -> f64 {
    const RHYTHM_RATIO_DIFFICULTY_MULTIPLIER: f64 = 26.0;
    let fraction = ratio - ratio.trunc();
    1.0 + RHYTHM_RATIO_DIFFICULTY_MULTIPLIER * smoothstep_bell_curve(fraction).min(0.5)
}

/// How much harder this object is to press for the rhythm it arrives in.
///
/// Ported from `RhythmEvaluator.EvaluateDifficultyOf`. Walks up to five seconds
/// or thirty-two objects backwards, whichever runs out first, weighing each
/// change of gap and fading the older ones out.
pub fn rhythm_multiplier_of(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    if current.is_spinner {
        return 0.0;
    }

    const HISTORY_TIME_MAX: f64 = 5_000.0;
    const HISTORY_OBJECTS_MAX: usize = 32;
    const RHYTHM_OVERALL_MULTIPLIER: f64 = 0.95;
    // Small enough that a gap is genuinely zero rather than merely tiny.
    const DELTA_MIN_VALUE: f64 = 1e-7;

    let mut complexity_sum = 0.0;
    let epsilon = current.hit_window_great * 0.3;

    let mut island = Island::unset();
    let mut previous_island = Island::unset();
    let mut islands: Vec<Island> = Vec::new();

    // The difficulty this island opened with, kept so a tightening rhythm can
    // be rewarded against where it started.
    let mut start_difficulty = 0.0;
    let mut first_delta_switch = false;

    // `current.index` counts the object's place in the map; the difficulty list
    // starts one later, so its own position is what bounds the history.
    let historical_note_count = at.min(HISTORY_OBJECTS_MAX);

    let previous = |back: usize| -> Option<&DiffObject> { at.checked_sub(back + 1).map(|i| &objects[i]) };

    let mut rhythm_start = 0;
    while rhythm_start + 2 < historical_note_count
        && previous(rhythm_start)
            .is_some_and(|obj| current.start_time - obj.start_time < HISTORY_TIME_MAX)
    {
        rhythm_start += 1;
    }

    let Some(mut prev_obj) = previous(rhythm_start) else { return 1.0 };
    let mut prev_prev_obj = previous(rhythm_start + 1);

    // From the furthest object back towards this one.
    for i in (1..=rhythm_start).rev() {
        let Some(curr_obj) = previous(i - 1) else { continue };
        if curr_obj.is_spinner {
            continue;
        }

        // Nothing counts fully forever: whichever runs out first, time or
        // object count, fades this change away.
        let time_decay = (HISTORY_TIME_MAX - (current.start_time - curr_obj.start_time))
            / HISTORY_TIME_MAX;
        let note_decay = (historical_note_count - i) as f64 / historical_note_count as f64;
        let historical_decay = note_decay.min(time_decay);

        let curr_delta = curr_obj.delta_time.max(DELTA_MIN_VALUE);
        let prev_delta = prev_obj.delta_time.max(DELTA_MIN_VALUE);
        let delta_difference = (prev_delta - curr_delta).abs();

        if island.empty {
            island = Island::new(curr_delta as i64);
        }

        let ratio = prev_delta.max(curr_delta) / prev_delta.min(curr_delta);
        // A change too large to feel as a change of rhythm rather than as a
        // stop and a fresh start.
        let difference_multiplier = (2.0 - ratio / 8.0).clamp(0.0, 1.0);
        let window_penalty = ((delta_difference - epsilon) / epsilon).clamp(0.0, 1.0);

        let mut difficulty = effective_difficulty(ratio) * window_penalty * difference_multiplier;

        // Coming off a slider is easier than coming off a circle: the finger is
        // already up, so a slider-circle-circle reads as a plain triple rather
        // than as a single into a double.
        if prev_obj.is_slider {
            let lazy_end_delta = curr_obj.minimum_jump_time;
            let lazy_ratio =
                lazy_end_delta.max(curr_delta) / lazy_end_delta.min(curr_delta);
            let real_end_delta = curr_obj.last_object_end_delta_time;
            let real_ratio = real_end_delta.max(curr_delta) / real_end_delta.min(curr_delta);
            let slider_difficulty =
                effective_difficulty(lazy_ratio).min(effective_difficulty(real_ratio));
            difficulty = slider_difficulty.min(difficulty);
        }

        if delta_difference < epsilon {
            // The same gap again: the run goes on.
            island.add_delta(curr_delta as i64);
        }

        if first_delta_switch {
            if delta_difference > epsilon {
                // Into a slider, where the accuracy window is generous.
                if curr_obj.is_slider {
                    difficulty *= 0.5;
                }
                if island.is_similar_polarity(&previous_island, epsilon) {
                    difficulty *= 0.5;
                }
                // A rhythm that has been tightening for two changes running is
                // not surprising by the second one.
                if prev_prev_obj.map_or(false, |obj| {
                    obj.delta_time.max(DELTA_MIN_VALUE) > prev_delta + epsilon
                }) && prev_delta > curr_delta + epsilon
                {
                    difficulty *= 0.125;
                }
                // Triplet into triplet. ppy's own note: kept for balance
                // despite the ratio calculation it leans on being flawed.
                if previous_island.count == island.count {
                    difficulty *= 0.5;
                }
                if prev_delta > curr_delta + epsilon {
                    difficulty *= 0.65;
                }

                let mut found = false;
                for existing in islands.iter_mut() {
                    if existing.almost_equals(&island, epsilon) {
                        // Only a run that follows its own twin counts as a
                        // repeat; the same shape twice across the map does not.
                        if previous_island.almost_equals(&island, epsilon) {
                            existing.occurrences += 1;
                        }
                        let power = logistic(island.delta as f64, 58.33, 0.24, 2.75);
                        difficulty *= (3.0 / existing.occurrences as f64)
                            .min((1.0 / existing.occurrences as f64).powf(power));
                        found = true;
                        break;
                    }
                }
                if !found && island.count > 0 {
                    islands.push(island);
                }

                difficulty *= 1.0 - prev_obj.double_tap_feasibility(Some(curr_obj)) * 0.75;

                if island.count > 1 {
                    complexity_sum += (difficulty * start_difficulty).sqrt() * historical_decay;
                } else {
                    // One note on its own is worth a flat amount however it sits.
                    complexity_sum += 0.7 * historical_decay;
                }

                start_difficulty = difficulty;

                // Slowing down ends the run; speeding up keeps it going.
                if prev_delta + epsilon < curr_delta {
                    first_delta_switch = false;
                }

                previous_island = island;
                island = Island::new(curr_delta as i64);
            }
        } else if prev_delta > curr_delta + epsilon {
            // Speeding up: start counting a run.
            first_delta_switch = true;
            if curr_obj.is_slider {
                difficulty *= 0.6;
            }
            if prev_obj.is_slider {
                difficulty *= 0.6;
            }
            start_difficulty = difficulty;
            island = Island::new(curr_delta as i64);
        }

        prev_prev_obj = Some(prev_obj);
        prev_obj = curr_obj;
    }

    // A long run at the end matters less than the changes that led into it.
    complexity_sum *= reverse_lerp(island.count as f64, 22.0, 3.0);

    (4.0 + complexity_sum * RHYTHM_OVERALL_MULTIPLIER).sqrt() / 2.0
}

/// The whole map's pressing difficulty, and what it is made of.
pub struct Speed {
    /// One strain per object, in order.
    pub strains: Vec<f64>,
    /// The strains that belong to sliders, kept apart for the performance side.
    pub slider_strains: Vec<f64>,
    /// The sum of the weights the strains were summed with.
    pub weight_sum: f64,
}

/// How much the hardest objects count for against the rest.
const HARMONIC_SCALE: f64 = 20.0;
const DECAY_EXPONENT: f64 = 0.9;

impl Speed {
    /// Walk the map, leaving a strain at each object.
    ///
    /// Ported from `Speed.ObjectDifficultyOf`. The strain carries over from one
    /// object to the next, decayed by the gap between them, so a stream builds
    /// and a pause lets go.
    pub fn of(objects: &[DiffObject], relax: bool) -> Self {
        const SKILL_MULTIPLIER: f64 = 1.16;

        let mut strains = Vec::with_capacity(objects.len());
        let mut slider_strains = Vec::new();
        let mut current_strain = 0.0;

        for at in 0..objects.len() {
            if relax {
                // Relax presses nothing, so there is no speed to speak of.
                strains.push(0.0);
                continue;
            }
            let decay = strain_decay(objects[at].adjusted_delta_time);
            current_strain *= decay;
            current_strain += speed_difficulty_of(objects, at) * (1.0 - decay) * SKILL_MULTIPLIER;

            let total = current_strain * rhythm_multiplier_of(objects, at);
            if objects[at].is_slider {
                slider_strains.push(total);
            }
            strains.push(total);
        }

        Self { strains, slider_strains, weight_sum: 0.0 }
    }

    /// The strains summed with the hardest counting most.
    ///
    /// Ported from `HarmonicSkill.DifficultyValue`. Sorted hardest first, each
    /// given a weight that falls away as it goes down the list, so a map with
    /// one very hard section and a map with many moderate ones do not come out
    /// alike.
    ///
    /// Objects worth nothing are dropped rather than sorted, which ppy do for
    /// speed: a map can have thousands of them and they change no answer.
    pub fn difficulty_value(&mut self) -> f64 {
        self.weight_sum = 0.0;
        if self.strains.is_empty() {
            return 0.0;
        }

        let mut sorted: Vec<f64> = self.strains.iter().copied().filter(|v| *v > 0.0).collect();
        sorted.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));

        let mut difficulty = 0.0;
        for (index, value) in sorted.iter().enumerate() {
            let index = index as f64;
            let harmonic = HARMONIC_SCALE / (1.0 + index);
            let weight = (1.0 + harmonic) / (index.powf(DECAY_EXPONENT) + 1.0 + harmonic);
            self.weight_sum += weight;
            difficulty += value * weight;
        }
        difficulty
    }
}

/// The figure the attributes endpoint calls `speed_difficulty`.
///
/// ```csharp
/// private double calculateDifficultyRating(double difficultyValue) => Math.Sqrt(difficultyValue) * 0.0675;
/// ```
pub fn difficulty_rating(difficulty_value: f64) -> f64 {
    difficulty_value.sqrt() * 0.0675
}
