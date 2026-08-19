//! How hard the map is to *point at* — the three readings of aim, and the one
//! that gets used.
//!
//! osu! no longer has a single aim skill. It has three evaluators, and the map
//! decides between them object by object:
//!
//! - [`snap_difficulty_of`] — stopping the cursor on each note. Rewards
//!   velocity, sharp turns, sudden changes of speed, and sliders.
//! - [`flow_difficulty_of`] — never stopping, riding through the notes in one
//!   motion. Rewards the same velocity differently and punishes erratic angles.
//! - [`agility_difficulty_of`] — plain speed of hand over short distances,
//!   which is what makes snapping a stream unreasonable.
//!
//! Snap and agility are added as a p-norm, and that sum is weighed against flow
//! by a logistic on their ratio: a player is assumed to do whichever is easier,
//! and the two probabilities sum to one. The comment in ppy's source explains
//! why agility is on the snap side — snapping every circle of a stream demands
//! so much of it that flowing wins, which is the answer you want.
//!
//! Ported from `SnapAimEvaluator`, `FlowAimEvaluator` and `AgilityEvaluator`,
//! with the `Aim` skill that consumes them. It sums its strains differently
//! from [`crate::speed`] — over sections of variable length rather than
//! harmonically — and that model lives in [`crate::strain`].

use std::f64::consts::PI;

use crate::preprocessing::{DiffObject, NORMALISED_DIAMETER, NORMALISED_RADIUS};
use crate::utils::{
    milliseconds_to_bpm_at, reverse_lerp, smoothstep, smootherstep,
};

fn radians(degrees: f64) -> f64 {
    degrees * PI / 180.0
}

/// How sharply the player has to turn, from nothing at a straight line to one
/// at a hairpin.
pub fn angle_acuteness(angle: f64) -> f64 {
    smoothstep(angle, radians(140.0), radians(40.0))
}

/// The opposite reading of the same corner.
fn angle_wideness(angle: f64) -> f64 {
    smoothstep(angle, radians(40.0), radians(140.0))
}

/// Plain speed of hand: how far the cursor has to go, over how little time.
///
/// Capped at a little over one circle apart, because beyond that it stops being
/// a question of agility and becomes one of aim.
pub fn agility_difficulty_of(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    if current.is_spinner {
        return 0.0;
    }
    const DISTANCE_CAP: f64 = NORMALISED_DIAMETER * 1.2;

    let travel = if at > 0 { objects[at - 1].lazy_travel_distance } else { 0.0 };
    let distance = travel + current.lazy_jump_distance;
    let scaled = distance.min(DISTANCE_CAP) / DISTANCE_CAP;

    let mut difficulty = scaled * 1000.0 / current.adjusted_delta_time;
    difficulty *= current.small_circle_bonus().powf(1.5);
    difficulty *= 1.0 / (1.0 - 0.2f64.powf(current.adjusted_delta_time / 1000.0));
    difficulty
}

/// How much two objects' circles overlap, from nothing to one.
///
/// Measured in playfield pixels rather than normalised ones, because it is
/// about whether the two circles literally cover each other.
fn overlap_factor(first: &DiffObject, second: &DiffObject) -> f64 {
    let radius = first.radius;
    let distance = (first.pos.x - second.pos.x).hypot(first.pos.y - second.pos.y);
    (1.0 - ((distance - radius).max(0.0) / radius).powi(2)).clamp(0.0, 1.0)
}

/// Riding through the notes without stopping.
pub fn flow_difficulty_of(objects: &[DiffObject], at: usize, with_sliders: bool) -> f64 {
    if at <= 1 {
        return 0.0;
    }
    let current = &objects[at];
    let last = &objects[at - 1];
    if current.is_spinner || last.is_spinner {
        return 0.0;
    }
    const VELOCITY_CHANGE_MULTIPLIER: f64 = 0.52;
    let last_last = &objects[at - 2];

    let curr_distance = if with_sliders { current.lazy_jump_distance } else { current.jump_distance };
    let prev_distance = if with_sliders { last.lazy_jump_distance } else { last.jump_distance };
    let mut curr_velocity = curr_distance / current.adjusted_delta_time;

    if last.is_slider && with_sliders {
        // Coming off a slider the cursor is already moving, so its travel
        // carries into this jump.
        let slider_distance = last.lazy_travel_distance + current.lazy_jump_distance;
        curr_velocity = curr_velocity.max(slider_distance / current.adjusted_delta_time);
    }
    let prev_velocity = prev_distance / last.adjusted_delta_time;

    let mut difficulty = curr_velocity;
    // A reduced circle-size bonus: the full one was tuned for a different
    // scaling of distance over time.
    difficulty *= current.small_circle_bonus().sqrt();

    // A change of rhythm is harder to ride through than to snap.
    let slower = current.adjusted_delta_time.max(last.adjusted_delta_time);
    let faster = current.adjusted_delta_time.min(last.adjusted_delta_time);
    difficulty *= 1.0 + (((slower - faster) / 50.0).powi(4)).min(0.25);

    if let (Some(curr_angle), Some(last_angle)) = (current.angle, last.angle) {
        let difference = (curr_angle - last_angle).abs();
        let adjusted = (difference / 2.0).sin() * 180.0;
        let angular_velocity = adjusted / (current.adjusted_delta_time * 0.1);
        // Consistent angles are easier to follow than erratic ones.
        difficulty *= 0.8 + (angular_velocity / 270.0).sqrt();
    }

    // Three notes stacked on each other ask for no movement, so they earn no
    // bonuses either.
    let mut overlapped_weight = 1.0;
    if at > 2 {
        overlapped_weight = 1.0
            - overlap_factor(current, last)
                * overlap_factor(current, last_last)
                * overlap_factor(last, last_last);
    }

    if let Some(angle) = current.angle {
        difficulty += curr_velocity * angle_acuteness(angle) * overlapped_weight;
    }

    if prev_velocity.max(curr_velocity) != 0.0 {
        let curr_velocity = if with_sliders {
            // The jump alone, without the slider's travel, when rewarding a
            // change of speed.
            curr_distance / current.adjusted_delta_time
        } else {
            curr_velocity
        };
        let ratio = smoothstep(
            (prev_velocity - curr_velocity).abs() / prev_velocity.max(curr_velocity),
            0.0,
            1.0,
        );
        let buff = (NORMALISED_DIAMETER * 1.25
            / current.adjusted_delta_time.min(last.adjusted_delta_time))
        .min((prev_velocity - curr_velocity).abs());
        difficulty += buff * ratio * overlapped_weight * VELOCITY_CHANGE_MULTIPLIER;
    }

    if current.is_slider && with_sliders {
        difficulty += current.travel_distance / current.travel_time;
    }

    // Raised to a power because flowing gets harder with distance and with time
    // together rather than with either alone.
    difficulty = difficulty.powf(1.45);
    // Anything closer than a radius is going to be flowed whatever else is true.
    difficulty * smootherstep(curr_distance, 0.0, NORMALISED_RADIUS)
}

/// How much a jump is punished for repeating the one before it.
///
/// A hand that has made the same movement six times running is not being asked
/// for anything new. Both readings of "the same" count: the same corner, and
/// the same direction of travel.
fn vector_angle_repetition(objects: &[DiffObject], at: usize) -> f64 {
    let current = &objects[at];
    let previous = &objects[at - 1];
    let (Some(curr_angle), Some(last_angle)) = (current.angle, previous.angle) else {
        return 1.0;
    };

    const NOTE_LIMIT: usize = 6;
    const MAXIMUM_REPETITION_NERF: f64 = 0.15;
    const MAXIMUM_VECTOR_INFLUENCE: f64 = 0.5;

    let mut constant_angle_count = 0.0;
    for index in 0..NOTE_LIMIT {
        let Some(prev) = at.checked_sub(index + 1).map(|i| &objects[i]) else { break };
        // Only vectors in the same run count: stopping to change rhythm breaks
        // the momentum that makes repetition easy.
        if current.adjusted_delta_time.max(prev.adjusted_delta_time)
            > 1.1 * current.adjusted_delta_time.min(prev.adjusted_delta_time)
        {
            break;
        }
        if let (Some(a), Some(b)) = (current.normalised_vector_angle, prev.normalised_vector_angle) {
            let difference = (a - b).abs();
            constant_angle_count += (8.0 * radians(11.25).min(difference)).cos();
        }
    }

    let vector_repetition = (0.5 / constant_angle_count).min(1.0).powi(2);
    // A jump shorter than a diameter is a stack, and stacks are not repetition.
    let stack_factor = smootherstep(current.lazy_jump_distance, 0.0, NORMALISED_DIAMETER);
    let adjusted =
        (2.0 * radians(45.0).min((curr_angle - last_angle).abs() * stack_factor)).cos();
    let base = 1.0 - MAXIMUM_REPETITION_NERF * angle_acuteness(last_angle) * adjusted;

    (base + (1.0 - base) * vector_repetition * MAXIMUM_VECTOR_INFLUENCE * stack_factor).powi(2)
}

/// Stopping the cursor on each note.
pub fn snap_difficulty_of(objects: &[DiffObject], at: usize, with_sliders: bool) -> f64 {
    if at <= 1 {
        return 0.0;
    }
    let current = &objects[at];
    let last = &objects[at - 1];
    if current.is_spinner || last.is_spinner {
        return 0.0;
    }

    const WIDE_ANGLE_MULTIPLIER: f64 = 9.67;
    const ACUTE_ANGLE_MULTIPLIER: f64 = 2.41;
    const SLIDER_MULTIPLIER: f64 = 1.5;
    const VELOCITY_CHANGE_MULTIPLIER: f64 = 0.9;
    // ppy's warning: above 1.02 this starts *reducing* difficulty as distance
    // rises, which is not what a bonus is for.
    const WIGGLE_MULTIPLIER: f64 = 1.02;

    let last2 = at.checked_sub(3).map(|i| &objects[i]);

    let curr_distance = if with_sliders { current.lazy_jump_distance } else { current.jump_distance };
    let mut curr_velocity = curr_distance / current.adjusted_delta_time;
    if last.is_slider && with_sliders {
        let slider_distance = last.lazy_travel_distance + current.lazy_jump_distance;
        curr_velocity = curr_velocity.max(slider_distance / current.adjusted_delta_time);
    }
    let prev_distance = if with_sliders { last.lazy_jump_distance } else { last.jump_distance };
    let prev_velocity = prev_distance / last.adjusted_delta_time;

    let mut difficulty = curr_velocity;
    difficulty *= vector_angle_repetition(objects, at);

    if let (Some(curr_angle), Some(last_angle)) = (current.angle, last.angle) {
        // The slower of the two, so a bonus cannot be earned by one fast jump
        // alone.
        let velocity_influence = curr_velocity.min(prev_velocity);
        let mut acute_bonus = 0.0;

        if current.adjusted_delta_time.max(last.adjusted_delta_time)
            < 1.25 * current.adjusted_delta_time.min(last.adjusted_delta_time)
        {
            acute_bonus = angle_acuteness(curr_angle);
            // Compared raw, before anything multiplies it, so that repeating a
            // sharp corner is what gets punished rather than repeating a fast one.
            acute_bonus *= 0.08
                + 0.92 * (1.0 - acute_bonus.min(angle_acuteness(last_angle).powi(3)));
            acute_bonus *= velocity_influence
                * smootherstep(
                    milliseconds_to_bpm_at(current.adjusted_delta_time, 2.0),
                    300.0,
                    400.0,
                )
                * smootherstep(curr_distance, 0.0, NORMALISED_DIAMETER * 2.0);
        }

        let mut wide_bonus = angle_wideness(curr_angle);
        wide_bonus *= 0.25 + 0.75 * (1.0 - wide_bonus.min(angle_wideness(last_angle).powi(3)));

        const WIDE_ANGLE_TIME_SCALE: f64 = 1.45;
        let mut wide_curr = curr_distance / current.adjusted_delta_time.powf(WIDE_ANGLE_TIME_SCALE);
        let wide_prev = prev_distance / last.adjusted_delta_time.powf(WIDE_ANGLE_TIME_SCALE);
        if last.is_slider && with_sliders {
            let slider_distance = last.lazy_travel_distance + current.lazy_jump_distance;
            wide_curr = wide_curr
                .max(slider_distance / current.adjusted_delta_time.powf(WIDE_ANGLE_TIME_SCALE));
        }
        wide_bonus *= wide_curr.min(wide_prev);

        if let Some(last2) = last2 {
            // Back and forth through one point is not a wide angle in any sense
            // that costs the player anything.
            let distance = (last2.pos.x - last.pos.x).hypot(last2.pos.y - last.pos.y);
            if distance < 1.0 {
                wide_bonus *= 1.0 - 0.55 * (1.0 - distance);
            }
        }

        difficulty += (acute_bonus * ACUTE_ANGLE_MULTIPLIER).max(wide_bonus * WIDE_ANGLE_MULTIPLIER);

        // A wiggle: two short jumps in a row, both turning sharply.
        let wiggle = velocity_influence
            * smootherstep(curr_distance, NORMALISED_RADIUS, NORMALISED_DIAMETER)
            * reverse_lerp(curr_distance, NORMALISED_DIAMETER * 3.0, NORMALISED_DIAMETER).powf(1.8)
            * smootherstep(curr_angle, radians(110.0), radians(60.0))
            * smootherstep(prev_distance, NORMALISED_RADIUS, NORMALISED_DIAMETER)
            * reverse_lerp(prev_distance, NORMALISED_DIAMETER * 3.0, NORMALISED_DIAMETER).powf(1.8)
            * smootherstep(last_angle, radians(110.0), radians(60.0));
        difficulty += wiggle * WIGGLE_MULTIPLIER;
    }

    if prev_velocity.max(curr_velocity) != 0.0 {
        let curr_velocity = if with_sliders {
            curr_distance / current.adjusted_delta_time
        } else {
            curr_velocity
        };
        let ratio = smoothstep(
            (prev_velocity - curr_velocity).abs() / prev_velocity.max(curr_velocity),
            0.0,
            1.0,
        );
        let buff = (NORMALISED_DIAMETER * 1.25
            / current.adjusted_delta_time.min(last.adjusted_delta_time))
        .min((prev_velocity - curr_velocity).abs());
        let mut bonus = buff * ratio;
        // A change of speed that comes with a change of rhythm is expected, so
        // it earns less.
        bonus *= (current.adjusted_delta_time.min(last.adjusted_delta_time)
            / current.adjusted_delta_time.max(last.adjusted_delta_time))
        .powi(2);
        difficulty += bonus * VELOCITY_CHANGE_MULTIPLIER;
    }

    if current.is_slider && with_sliders {
        let slider = current.travel_distance / current.travel_time;
        difficulty += (if slider < 1.0 { slider } else { slider.powf(0.75) }) * SLIDER_MULTIPLIER;
    }

    difficulty *= current.small_circle_bonus();
    difficulty *= 1.0 / (1.0 - 0.03f64.powf((current.adjusted_delta_time / 1000.0).powf(0.65)));
    difficulty
}

/// The aim skill: the three readings combined, decayed into a strain, and
/// summed over sections.
///
/// Built twice per map — once counting slider travel and once not — because the
/// ratio of the two is what `slider_factor` reports.
pub struct Aim {
    pub sections: crate::strain::Sections,
    /// The strain at each slider, for the performance side.
    pub slider_strains: Vec<f64>,
}

fn strain_decay(ms: f64) -> f64 {
    0.2f64.powf(ms / 1000.0)
}

/// How the three readings become one.
///
/// Snap and agility are added as a p-norm, then weighed against flow by a
/// logistic on their ratio. ppy's note on why agility sits with snap: snapping
/// every circle of a stream demands so much of it that flowing wins, which is
/// the answer you want.
fn combine(snap: f64, agility: f64, flow: f64, relax: bool, touch: bool) -> f64 {
    const SKILL_MULTIPLIER_TOTAL: f64 = 1.12;
    const COMBINED_SNAP_NORM_EXPONENT: f64 = 1.2;
    // The one constant in the probability, tuned rather than derived.
    const K: f64 = 7.27;

    let mut snap = snap;
    let mut flow = flow;
    let mut combined = crate::utils::norm(COMBINED_SNAP_NORM_EXPONENT, &[snap, agility]);

    // P(snap) + P(flow) = 1, and f(x) + f(1/x) = 1, which this logistic
    // satisfies — the two readings are symmetric and reversible.
    let ratio = flow / combined;
    let p_snap = if ratio == 0.0 {
        0.0
    } else if ratio.is_nan() {
        1.0
    } else {
        crate::utils::logistic_of(-K * ratio.ln(), 1.0)
    };

    if touch {
        // Agility already reads touch difficulty well enough, so only snap is
        // adjusted.
        snap = snap.powf(0.89);
        combined = crate::utils::norm(COMBINED_SNAP_NORM_EXPONENT, &[snap, agility]);
    }
    if relax {
        combined *= 0.75;
        flow *= 0.6;
    }

    (combined * p_snap + flow * (1.0 - p_snap)) * SKILL_MULTIPLIER_TOTAL
}

impl Aim {
    /// Walk the map, collecting sections.
    pub fn of(objects: &[DiffObject], with_sliders: bool, relax: bool, touch: bool,
              autopilot: bool) -> Self {
        const SKILL_MULTIPLIER_SNAP: f64 = 70.9;
        const SKILL_MULTIPLIER_AGILITY: f64 = 2.35;
        const SKILL_MULTIPLIER_FLOW: f64 = 242.0;

        let mut sections = crate::strain::Sections::new(0.9, 400.0);
        let mut slider_strains = Vec::new();
        let mut current_strain = 0.0f64;

        for at in 0..objects.len() {
            let object = &objects[at];

            let mut difficulty = 0.0;
            if !autopilot {
                // Autopilot aims for the player, so there is no aim to grade.
                let snap = snap_difficulty_of(objects, at, with_sliders) * SKILL_MULTIPLIER_SNAP;
                let agility = agility_difficulty_of(objects, at) * SKILL_MULTIPLIER_AGILITY;
                let flow = flow_difficulty_of(objects, at, with_sliders) * SKILL_MULTIPLIER_FLOW;
                difficulty = combine(snap, agility, flow, relax, touch);
                // A steadier hand is asked for at higher overall difficulty.
                difficulty *= 0.985 + object.overall_difficulty().max(0.0).powi(2) / 4000.0;
            }

            let decay = strain_decay(object.adjusted_delta_time);
            let carried = current_strain * decay;
            current_strain = carried + difficulty * (1.0 - decay);

            if at == 0 {
                sections.begin_at(object.start_time, current_strain);
            } else {
                // What the strain decays to at a given moment, for a section
                // that opens in a gap rather than on an object.
                let previous = objects[at - 1].start_time;
                let before = carried / decay;
                let initial = move |time: f64| before * strain_decay(time - previous);
                sections.take(object.start_time, current_strain, &initial);
            }

            if object.is_slider {
                slider_strains.push(current_strain);
            }
        }

        Self { sections, slider_strains }
    }

    /// The weighted sum of this skill's sections.
    pub fn difficulty_value(&mut self) -> f64 {
        let decay = self.sections.decay_weight;
        let length = self.sections.max_section_length;
        let reduced = crate::strain::reduced_peaks(self.sections.peaks());
        crate::strain::difficulty_value(&reduced, decay, length)
    }
}

/// The figure the attributes endpoint calls `aim_difficulty`.
///
/// ```csharp
/// private double calculateAimDifficultyRating(double difficultyValue) => DiffUtils.Pow(difficultyValue, 0.63) * 0.02275;
/// ```
pub fn difficulty_rating(difficulty_value: f64) -> f64 {
    difficulty_value.powf(0.63) * 0.02275
}
