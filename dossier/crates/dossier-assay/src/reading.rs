//! How hard the map is to *read* — the newest skill, and the reason Hidden
//! moves the star rating.
//!
//! Aim and speed ask what the hand has to do. This asks what the eye has to
//! take in before the hand can start: how many objects are on screen at once,
//! how little warning each gets, and how much of that is hidden.
//!
//! Ported from `Reading` and `ReadingEvaluator`. Three difficulties are worked
//! out and combined as a p-norm — the approach rate on its own, the density of
//! what is visible, and Hidden where it applies — then everything is nerfed by
//! how repetitive the angles have been, on the grounds that a pattern you have
//! already read once is not read again.
//!
//! # The corpus had to be extended for this
//!
//! `reading_difficulty` is one of the four figures the public attributes
//! endpoint does not return, which is a strange gap: the endpoint serves star
//! ratings computed *with* this skill while keeping its attributes to itself.
//! The numbers it is graded against here came from ppy's own osu-tools, merged
//! by `scripts/pp_corpus_tools.py`.

use crate::preprocessing::{DiffObject, NORMALISED_DIAMETER, NORMALISED_RADIUS};
use crate::utils::{logistic, norm, reverse_lerp, smootherstep};

/// How far ahead the eye is assumed to read.
const READING_WINDOW_SIZE: f64 = 3000.0;

/// Past this far apart, one object stops confusing the reading of another.
const DISTANCE_INFLUENCE_THRESHOLD: f64 = NORMALISED_DIAMETER * 1.5;

fn radians(degrees: f64) -> f64 {
    degrees * std::f64::consts::PI / 180.0
}

/// How much an object that far away in time still counts.
fn time_nerf_factor(delta: f64) -> f64 {
    (2.0 - delta / (READING_WINDOW_SIZE / 2.0)).clamp(0.0, 1.0)
}

/// Everything already on screen when this object appears, and how much each
/// muddies it.
fn past_object_influence(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    let mut influence = 0.0;
    for back in 0..at {
        let object = &objects[at - 1 - back];
        if current.start_time - object.start_time > READING_WINDOW_SIZE
            // Not on screen yet when this one has to be clicked.
            || object.start_time < current.start_time - current.preempt
        {
            break;
        }
        let mut difficulty = current.opacity_at(object.raw_start_time, false);
        // A note the cursor barely has to move to is one whose placement can be
        // cheesed, so how confusingly it was arranged stops mattering.
        difficulty *= smootherstep(object.lazy_jump_distance, 15.0, DISTANCE_INFLUENCE_THRESHOLD);
        difficulty *= time_nerf_factor(current.start_time - object.start_time);
        influence += difficulty;
    }
    influence
}

/// How much is on screen at the moment this object has to be clicked.
fn visible_object_density(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    let mut count = 0.0;
    for ahead in objects.iter().skip(at + 1) {
        if ahead.start_time - current.start_time > READING_WINDOW_SIZE
            || current.start_time < ahead.start_time - ahead.preempt
        {
            break;
        }
        count += ahead.opacity_at(current.raw_start_time, false)
            * time_nerf_factor(ahead.start_time - current.start_time);
    }
    count
}

/// How much to hold back for the angles having been seen before.
///
/// A shape read once is not read again, so a run of objects turning the same
/// way — or alternating the same two ways — is worth less than its density
/// suggests. Bottoms out at a fifth: repetition makes reading easier, never
/// free.
fn constant_angle_nerf_factor(objects: &[DiffObject], at: usize) -> f64 {
    const MINIMUM_ANGLE_RELEVANCY_TIME: f64 = 2000.0;
    const MAXIMUM_ANGLE_RELEVANCY_TIME: f64 = 200.0;

    let current = &objects[at];
    let mut count = 0.0;
    let mut index = 0usize;
    let mut gap = 0.0;

    // The three objects behind the one being looked at, for the alternating
    // case: a zigzag repeats every other object rather than every one.
    let mut prev0 = current;
    let mut prev1: Option<&DiffObject> = None;
    let mut prev2: Option<&DiffObject> = None;

    while gap < MINIMUM_ANGLE_RELEVANCY_TIME {
        let Some(object) = at.checked_sub(index + 1).map(|i| &objects[i]) else { break };

        // An object far enough back in time is barely part of the same reading.
        let long_interval = 1.0
            - reverse_lerp(
                object.adjusted_delta_time,
                MAXIMUM_ANGLE_RELEVANCY_TIME,
                MINIMUM_ANGLE_RELEVANCY_TIME,
            );

        if let (Some(here), Some(there)) = (current.angle, object.angle) {
            let difference = (here - there).abs();
            let mut alternating = std::f64::consts::PI;

            if let (Some(one), Some(two)) = (prev1, prev2) {
                if let (Some(a0), Some(a1), Some(a2), Some(ao)) =
                    (prev0.angle, one.angle, two.angle, object.angle)
                {
                    alternating = (a1 - ao).abs() + (a2 - a0).abs();
                    // Only a genuine zigzag counts: one of the pair sharp while
                    // the other is wide.
                    let mut weight = 1.0;
                    weight *= reverse_lerp(ao.min(a0) * 180.0 / std::f64::consts::PI, 20.0, 5.0);
                    weight *= reverse_lerp(ao.max(a0) * 180.0 / std::f64::consts::PI, 60.0, 120.0);
                    alternating = std::f64::consts::PI
                        + (0.1 * alternating - std::f64::consts::PI) * weight;
                }
            }

            // A stack is not a shape, so it cannot be a repeated one.
            let stack = smootherstep(object.lazy_jump_distance, 0.0, NORMALISED_RADIUS);
            count += (3.0 * radians(30.0).min(difference.min(alternating) * stack)).cos()
                * long_interval;
        }

        gap = current.start_time - object.start_time;
        index += 1;
        prev2 = prev1;
        prev1 = Some(prev0);
        prev0 = object;
    }

    (2.0 / count).clamp(0.2, 1.0)
}

/// How hard this object is to read.
pub fn reading_difficulty_of(objects: &[DiffObject], at: usize, hidden: bool) -> f64 {
    let current = &objects[at];
    if current.is_spinner || at == 0 {
        return 0.0;
    }

    // Only ever a bonus: reading a slow map is not made easier by it being slow.
    let velocity = (current.lazy_jump_distance / current.adjusted_delta_time).max(1.0);
    let density = visible_object_density(objects, at);
    let past = past_object_influence(objects, at);
    let nerf = constant_angle_nerf_factor(objects, at);

    let note_density = density_difficulty(objects.get(at + 1), velocity, nerf, past, density);
    let hidden_difficulty = if hidden {
        hidden_difficulty(current, past, density, velocity, nerf, objects.get(at - 1))
    } else {
        0.0
    };
    let preempt = preempt_difficulty(velocity, nerf, current.preempt);

    let difficulty = norm(1.5, &[preempt, hidden_difficulty, note_density]);
    // Less time to take it in is harder.
    difficulty * (1.0 / (1.0 - 0.8f64.powf(current.adjusted_delta_time / 1000.0)))
}

/// How hard it is to pick this object out of what else is on screen.
fn density_difficulty(
    next: Option<&DiffObject>,
    velocity: f64,
    nerf: f64,
    past: f64,
    density: f64,
) -> f64 {
    const DENSITY_MULTIPLIER: f64 = 2.4;
    const DENSITY_DIFFICULTY_BASE: f64 = 2.5;

    // What is still to come counts too, because it muddies which way the cursor
    // is going next.
    let mut future = density.sqrt();
    if let Some(next) = next {
        future *= smootherstep(next.lazy_jump_distance, 15.0, DISTANCE_INFLUENCE_THRESHOLD);
    }
    let mut difficulty = (past + future).powf(1.7) * 0.4 * nerf * velocity;
    // Only maps denser than ordinary earn anything at all.
    difficulty = (difficulty - DENSITY_DIFFICULTY_BASE).max(0.0);
    // Softened, because a dense map is partly memorised rather than read.
    difficulty.powf(0.45) * DENSITY_MULTIPLIER
}

/// How hard it is to read on the approach rate alone.
fn preempt_difficulty(velocity: f64, nerf: f64, preempt: f64) -> f64 {
    const PREEMPT_BALANCING_FACTOR: f64 = 140_000.0;
    // AR 9.66, where this starts costing anything.
    const PREEMPT_STARTING_POINT: f64 = 500.0;

    // `(a - x + |x - a|) / 2` is `max(0, a - x)` written without a branch.
    let over = ((PREEMPT_STARTING_POINT - preempt) + (preempt - PREEMPT_STARTING_POINT).abs()) / 2.0;
    over.powf(2.5) / PREEMPT_BALANCING_FACTOR * nerf * velocity
}

/// What Hidden adds.
fn hidden_difficulty(
    current: &DiffObject,
    past: f64,
    density: f64,
    velocity: f64,
    nerf: f64,
    previous: Option<&DiffObject>,
) -> f64 {
    const HIDDEN_MULTIPLIER: f64 = 0.28;

    // A longer preempt means longer spent invisible, which is the whole cost.
    let preempt_factor = current.preempt.powf(2.2) * 0.01;
    let density_factor = (density + past).powf(3.3) * 3.0;
    let mut difficulty = (preempt_factor + density_factor) * nerf * velocity * 0.01;
    difficulty = difficulty.powf(0.4) * HIDDEN_MULTIPLIER;

    // A perfect stack under Hidden is guesswork rather than reading, but only
    // when the next note is genuinely invisible as the previous one is clicked.
    if let Some(previous) = previous {
        if current.lazy_jump_distance == 0.0
            && current.opacity_at(previous.raw_start_time, true) == 0.0
            && previous.start_time > current.start_time - current.preempt
        {
            // Harder the less time there is between them.
            difficulty += HIDDEN_MULTIPLIER * 2500.0 / current.adjusted_delta_time.powf(1.5);
        }
    }
    difficulty
}

/// The reading skill over a whole map.
pub struct Reading {
    /// One difficulty per object, in order.
    pub difficulties: Vec<f64>,
    /// How many objects fall in the first minute, which is how many get held
    /// back for being memorised.
    reduced_note_count: f64,
    pub weight_sum: f64,
}

impl Reading {
    pub fn of(objects: &[DiffObject], hidden: bool, relax: bool, touch: bool,
              autopilot: bool) -> Self {
        const SKILL_MULTIPLIER: f64 = 2.5;
        const REDUCED_DIFFICULTY_DURATION: f64 = 60.0 * 1000.0;

        let mut difficulties = Vec::with_capacity(objects.len());
        let mut current_strain = 0.0f64;
        let mut reduced_until: Option<f64> = None;
        let mut reduced_note_count = 0.0;

        for at in 0..objects.len() {
            let mut difficulty = reading_difficulty_of(objects, at, hidden);
            if touch {
                difficulty = difficulty.powf(0.89);
            }
            if relax {
                difficulty *= 0.4;
            }
            if autopilot {
                difficulty *= 0.1;
            }
            difficulty *= 0.825 + objects[at].overall_difficulty().max(0.0).powf(2.2) / 1125.0;

            // Decayed on the plain gap rather than the floored one, unlike every
            // other skill here.
            let decay = 0.8f64.powf(objects[at].delta_time / 1000.0);
            current_strain *= decay;
            current_strain += difficulty * (1.0 - decay) * SKILL_MULTIPLIER;

            let until = *reduced_until.get_or_insert(objects[at].start_time + REDUCED_DIFFICULTY_DURATION);
            if objects[at].start_time <= until {
                reduced_note_count += 1.0;
            }
            difficulties.push(current_strain);
        }

        Self { difficulties, reduced_note_count, weight_sum: 0.0 }
    }

    /// The difficulties with the first minute's held back.
    ///
    /// ppy's baseline here is zero, with the comment "assume the first seconds
    /// are completely memorised" — so the very first note of a map is worth
    /// nothing at all and the minute after it rises to full along a logarithm.
    /// Reading is the one skill where that makes sense: you only read a map
    /// once.
    ///
    /// Held back in the order the objects came, before anything is sorted,
    /// because it is about *when* they happened.
    fn transformed(&self) -> Vec<f64> {
        const REDUCED_DIFFICULTY_BASE_LINE: f64 = 0.0;
        let mut out: Vec<f64> = self.difficulties.iter().copied().filter(|v| *v > 0.0).collect();
        let count = self.reduced_note_count;
        let limit = (out.len() as f64).min(count) as usize;
        for (index, value) in out.iter_mut().enumerate().take(limit) {
            let at = if count > 0.0 { (index as f64 / count).clamp(0.0, 1.0) } else { 0.0 };
            let scale = (1.0 + 9.0 * at).log10();
            *value *= REDUCED_DIFFICULTY_BASE_LINE
                + (1.0 - REDUCED_DIFFICULTY_BASE_LINE) * scale;
        }
        out
    }

    /// The harmonic sum, at reading's own weights.
    pub fn difficulty_value(&mut self) -> f64 {
        let (value, weight_sum) = crate::speed::harmonic_sum(&self.transformed(), 1.0, 0.9);
        self.weight_sum = weight_sum;
        value
    }

    /// How many notes are difficult to read, against what the top one would be
    /// if they all were.
    ///
    /// Reading overrides the shared counter with its own constants — a midpoint
    /// of 1.15 against 0.88 and a growth of 5 against 10 — so a map has to be
    /// consistently hard to read before many of its notes count, where a map
    /// merely hard to hit does not.
    pub fn top_weighted_notes(&self, difficulty_value: f64) -> f64 {
        if self.difficulties.is_empty() || self.weight_sum == 0.0 {
            return 0.0;
        }
        let consistent_top = difficulty_value / self.weight_sum;
        if consistent_top == 0.0 {
            return 0.0;
        }
        self.difficulties
            .iter()
            .map(|value| logistic(value / consistent_top, 1.15, 5.0, 1.1))
            .sum()
    }
}

/// The figure the corpus calls `reading_difficulty`.
pub fn difficulty_rating(difficulty_value: f64) -> f64 {
    difficulty_value.sqrt() * 0.0675
}
