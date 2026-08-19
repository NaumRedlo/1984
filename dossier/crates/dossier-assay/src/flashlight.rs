//! How hard the map is to play with the lights off.
//!
//! Ported from `Flashlight` and `FlashlightEvaluator`. Zero without the mod, so
//! this earns its keep on two of the corpus's fifteen mod sets and nothing on
//! the other thirteen — but the star rating cannot be assembled without it.
//!
//! What it measures is memory rather than sight. Flashlight lights a circle
//! around the cursor and nothing else, so a jump costs in proportion to how far
//! outside that circle its target sits and how long ago the player last saw the
//! ground they are crossing. Every object walks up to ten objects back, adding
//! what each contributes divided by how long ago it was.
//!
//! It sums its sections differently again — not harmonically like speed, not by
//! the area under a decay like aim, but plainly, every section counting fully.
//! What it measures accumulates across a map rather than peaking in it.

use crate::preprocessing::DiffObject;

/// Sections are a fixed grid here, unlike aim's.
const SECTION_LENGTH: f64 = 400.0;

fn strain_decay(ms: f64) -> f64 {
    0.15f64.powf(ms / 1000.0)
}

/// How hard one object is to reach in the dark.
pub fn flashlight_difficulty_of(objects: &[DiffObject], at: usize, hidden: bool) -> f64 {
    let current = &objects[at];
    if current.is_spinner {
        return 0.0;
    }

    const MAX_OPACITY_BONUS: f64 = 0.4;
    const HIDDEN_BONUS: f64 = 0.2;
    const MIN_VELOCITY: f64 = 0.5;
    const SLIDER_MULTIPLIER: f64 = 1.3;
    const MIN_ANGLE_MULTIPLIER: f64 = 0.2;

    // Fifty-two, not the fifty everything else normalises to. It is ppy's
    // number and not a slip of theirs or of this port.
    let scaling = 52.0 / current.radius;
    let mut small_distance_nerf = 1.0;
    let mut cumulative_strain_time = 0.0;
    let mut difficulty = 0.0;
    let mut angle_repeats = 0.0;
    let mut last = current;

    for back in 0..at.min(10) {
        let object = &objects[at - 1 - back];
        cumulative_strain_time += last.adjusted_delta_time;

        if !object.is_spinner {
            // To where that object *ended*: a slider leaves the cursor at its
            // tail, not at its head.
            let jump = (current.pos.x - object.end_pos.x).hypot(current.pos.y - object.end_pos.y);

            if back == 0 {
                // Anything inside the lit circle is seen rather than remembered.
                small_distance_nerf = (jump / 75.0).min(1.0);
            }
            // Only the first object of a stack is worth remembering.
            let stack_nerf = ((object.lazy_jump_distance / scaling) / 25.0).min(1.0);
            let opacity_bonus = 1.0
                + MAX_OPACITY_BONUS * (1.0 - current.opacity_at(object.raw_start_time, hidden));
            difficulty += stack_nerf * opacity_bonus * scaling * jump / cumulative_strain_time;

            if let (Some(here), Some(there)) = (current.angle, object.angle) {
                if (there - here).abs() < 0.02 {
                    // Further back in time counts for less.
                    angle_repeats += (1.0 - 0.1 * back as f64).max(0.0);
                }
            }
        }
        last = object;
    }

    difficulty = (small_distance_nerf * difficulty).powi(2);
    if hidden {
        // No approach circles to give the timing away.
        difficulty *= 1.0 + HIDDEN_BONUS;
    }
    difficulty *= MIN_ANGLE_MULTIPLIER + (1.0 - MIN_ANGLE_MULTIPLIER) / (angle_repeats + 1.0);

    let mut slider_bonus = 0.0;
    if current.is_slider {
        // Undone, to get the distance the cursor really covers.
        let pixels = current.lazy_travel_distance / scaling;
        slider_bonus = (pixels / current.travel_time - MIN_VELOCITY).max(0.0).sqrt();
        // A longer slider is more to memorise.
        slider_bonus *= pixels;
        if current.repeat_count > 0 {
            // One that doubles back shows the same ground twice.
            slider_bonus /= f64::from(current.repeat_count + 1);
        }
    }
    difficulty + slider_bonus * SLIDER_MULTIPLIER
}

/// The flashlight skill over a map.
pub struct Flashlight {
    peaks: Vec<f64>,
    total_objects: usize,
}

impl Flashlight {
    #[allow(clippy::too_many_arguments)]
    pub fn of(
        objects: &[DiffObject],
        has_flashlight: bool,
        hidden: bool,
        relax: bool,
        touch: bool,
        autopilot: bool,
        total_objects: usize,
    ) -> Self {
        const SKILL_MULTIPLIER: f64 = 0.058;

        let mut peaks = Vec::new();
        let mut current_strain = 0.0f64;
        let mut section_end = 0.0;
        let mut section_peak = 0.0f64;

        for (at, object) in objects.iter().enumerate() {
            if at == 0 {
                // Aligned to the clock rather than to the first object, so the
                // opening section ends on the next boundary.
                section_end = (object.start_time / SECTION_LENGTH).ceil() * SECTION_LENGTH;
            }
            while object.start_time > section_end {
                peaks.push(section_peak);
                // A new section does not open empty: the strain the player was
                // carrying decays into it.
                let previous = objects[at.saturating_sub(1)].start_time;
                section_peak = current_strain * strain_decay(section_end - previous);
                section_end += SECTION_LENGTH;
            }

            let mut strain = 0.0;
            if has_flashlight {
                let mut difficulty = flashlight_difficulty_of(objects, at, hidden);
                if touch {
                    difficulty = difficulty.powf(0.9);
                }
                if relax {
                    difficulty *= 0.7;
                }
                if autopilot {
                    difficulty *= 0.4;
                }
                difficulty *= 0.985 + object.overall_difficulty().max(0.0).powi(2) / 4000.0;

                // Decayed on the plain gap, and the difficulty added whole
                // rather than weighted by what the decay left — unlike aim.
                current_strain *= strain_decay(object.delta_time);
                current_strain += difficulty * SKILL_MULTIPLIER;
                strain = current_strain;
            }
            section_peak = section_peak.max(strain);
        }
        peaks.push(section_peak);

        Self { peaks, total_objects }
    }

    /// The plain sum of the sections, held back on short maps.
    pub fn difficulty_value(&self) -> f64 {
        let sum: f64 = self.peaks.iter().filter(|value| **value > 0.0).sum();
        let objects = self.total_objects as f64;
        // A short map spends more of itself at the small radius a low combo
        // gives, which would otherwise flatter it.
        let length_factor = 0.7
            + 0.1 * (objects / 200.0).min(1.0)
            + if objects > 200.0 {
                0.2 * ((objects - 200.0) / 200.0).min(1.0)
            } else {
                0.0
            };
        sum * length_factor
    }
}

/// The figure the corpus calls `flashlight_difficulty`.
pub fn difficulty_rating(difficulty_value: f64) -> f64 {
    difficulty_value.sqrt() * 0.0675
}

/// What a flashlight rating is worth as performance.
pub fn difficulty_to_performance(difficulty: f64) -> f64 {
    25.0 * difficulty.powi(2)
}

/// Reading and flashlight added together, as one demand on the eye.
///
/// ```csharp
/// return DiffUtils.Norm(PERFORMANCE_NORM_EXPONENT, reading, flashlight * Math.Clamp(flashlight / reading, 0.25, 1.0));
/// ```
///
/// Flashlight is held back where reading already dominates: a map you cannot
/// read is not made much harder by also not seeing it.
pub fn sum_cognition(reading: f64, flashlight: f64, norm_exponent: f64) -> f64 {
    if reading <= 0.0 {
        return flashlight;
    }
    if flashlight <= 0.0 {
        return reading;
    }
    let held = flashlight * (flashlight / reading).clamp(0.25, 1.0);
    crate::utils::norm(norm_exponent, &[reading, held])
}
