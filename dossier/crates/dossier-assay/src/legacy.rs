//! What a play was worth under the old scoring, and what that says about it.
//!
//! Stable never recorded how a play broke — only what it scored. But score
//! under ScoreV1 is mostly the combo portion, which grows with the square of
//! combo, so a total that falls short of what the combo alone implies is a total
//! that was interrupted. Reading the interruptions back out of the number is
//! what this is for, and it is the one path by which a stable score can be
//! priced as precisely as a lazer one.
//!
//! Ported from `LegacyScoreUtils` and `OsuLegacyScoreMissCalculator`.

use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};
use dossier_sim::{TimedKind, Timeline};

use crate::slider::Nested;

/// What a slider's big and small ticks are worth outside the combo portion.
const BIG_TICK_SCORE: f64 = 30.0;
const SMALL_TICK_SCORE: f64 = 10.0;

/// The score a map's nested objects carry, spread over its objects.
///
/// Ported from `CalculateNestedScorePerObject`. Slider heads, tails and repeats
/// are "big" ticks and its ticks are "small" ones; spinners are worth whatever
/// a determined player could spin out of them.
pub fn nested_score_per_object(beatmap: &Beatmap, mods: Mods, object_count: u32) -> f64 {
    if object_count == 0 {
        return 0.0;
    }
    let timeline = Timeline::build(beatmap, mods);
    let (mut big, mut small) = (0.0, 0.0);
    let mut spinner_score = 0.0;

    for object in &timeline.objects {
        match &object.kind {
            TimedKind::Slider { slides, .. } => {
                // One for the head, one for the tail, and one per repeat.
                big += 2.0 + f64::from(slides.saturating_sub(1));
                small += crate::slider_parts(beatmap, object)
                    .iter()
                    .filter(|part| part.kind == Nested::Tick)
                    .count() as f64;
            }
            TimedKind::Spinner => spinner_score += spinner_score_of(object.duration_ms()),
            TimedKind::Circle => {}
        }
    }

    (big * BIG_TICK_SCORE + small * SMALL_TICK_SCORE + spinner_score) / f64::from(object_count)
}

/// What a spinner of this length is worth, as stable would have counted it.
///
/// Ported from `calculateSpinnerScore`, including the two approximations ppy
/// name: the required spin rate is taken at its easiest rather than from the
/// map's overall difficulty, and the bonus spins are cut back because the aim
/// is an average play rather than the best possible one.
fn spinner_score_of(duration_ms: f64) -> f64 {
    const SPIN_SCORE: f64 = 100.0;
    const BONUS_SPIN_SCORE: f64 = 1000.0;
    // Stable's own ceiling, not lazer's leniency.
    const MAXIMUM_ROTATIONS_PER_SECOND: f64 = 477.0 / 60.0;
    const MINIMUM_ROTATIONS_PER_SECOND: f64 = 3.0;

    let seconds = duration_ms / 1000.0;
    let total_half_spins = (seconds * MAXIMUM_ROTATIONS_PER_SECOND * 2.0) as i64;
    let required_for_completion = (seconds * MINIMUM_ROTATIONS_PER_SECOND) as i64;
    // Another turn and a half before any bonus is paid.
    let required_before_bonus = required_for_completion + 3;

    let full_spins = total_half_spins / 2;
    let mut score = SPIN_SCORE * full_spins as f64;
    let bonus_spins = ((total_half_spins - required_before_bonus) / 2 - full_spins / 2).max(0);
    score += BONUS_SPIN_SCORE * bonus_spins as f64;
    score
}

/// The map's ScoreV1 difficulty multiplier — its "peppy stars".
///
/// Ported from `CalculateDifficultyPeppyStars`, and taken from the map
/// **without mods**, which is not an oversight of this port: the difficulty
/// calculator asks `WorkingBeatmap.Beatmap` for this one while asking the
/// mod-applied beatmap for everything around it.
///
/// ppy compute it in `decimal` and put a warning above it in capitals. The
/// reason is that stable did this arithmetic in x87 registers, which are eighty
/// bits wide, and .NET's own doubles round differently — so on a fair number of
/// maps the result comes out one star apart. This port is in `f64` and so is
/// exposed to exactly that: the corpus agrees on every map in it, and a map that
/// disagrees would be off by a whole multiplier rather than by a fraction.
pub fn difficulty_peppy_stars(beatmap: &Beatmap) -> i32 {
    let objects = beatmap.objects.len();
    let mut drain_length = 0i64;

    if objects > 0 {
        let timeline = Timeline::build(beatmap, Mods::new(0));
        let breaks: i64 = timeline
            .breaks
            .iter()
            .map(|(start, end)| end.round() as i64 - start.round() as i64)
            .sum();
        let first = beatmap.objects.first().map_or(0.0, |o| o.time_ms).round() as i64;
        let last = beatmap.objects.last().map_or(0.0, |o| o.time_ms).round() as i64;
        drain_length = (last - first - breaks) / 1000;
    }

    let ratio = if drain_length != 0 {
        (objects as f64 / drain_length as f64 * 8.0).clamp(0.0, 16.0)
    } else {
        16.0
    };
    let difficulty = &beatmap.difficulty;
    let sum = f64::from(difficulty.hp_drain as f32)
        + f64::from(difficulty.overall_difficulty as f32)
        + f64::from(difficulty.circle_size as f32)
        + ratio;
    (sum / 38.0 * 5.0).round() as i32
}

/// How much the old scoring multiplied a play by, for the mods it used.
///
/// Ported from `getLegacyScoreMultiplier`. Relax and Autopilot return nothing
/// at all, which is how a play under them is told apart from one merely worth
/// little.
pub fn score_multiplier(mods: Mods) -> f64 {
    if mods.contains(bits::RELAX) || mods.contains(bits::AUTOPILOT) {
        return 0.0;
    }
    let mut multiplier = 1.0;
    if mods.contains(bits::NO_FAIL) {
        multiplier *= 0.5;
    }
    if mods.contains(bits::EASY) {
        multiplier *= 0.5;
    }
    if mods.contains(bits::HALF_TIME) {
        multiplier *= 0.3;
    }
    if mods.contains(bits::HIDDEN) {
        multiplier *= 1.06;
    }
    if mods.contains(bits::HARD_ROCK) {
        multiplier *= 1.06;
    }
    if mods.contains(bits::DOUBLE_TIME) {
        multiplier *= 1.12;
    }
    if mods.contains(bits::FLASHLIGHT) {
        multiplier *= 1.12;
    }
    if mods.contains(bits::SPUN_OUT) {
        multiplier *= 0.9;
    }
    multiplier
}

/// The combo portion of the greatest ScoreV1 total this map allows.
///
/// Ported from `OsuLegacyScoreSimulator.Simulate`. Under the old scoring a hit
/// is worth its face value plus that value again multiplied by the combo
/// standing when it lands, so the combo portion grows with the square of the
/// map's length — and that is what makes a total readable. Knowing the maximum
/// is what turns a player's total into a statement about where they broke.
///
/// Two details here are stable's arithmetic rather than anyone's intent, and
/// both are load-bearing. `scoreIncrease / 25` is integer division between two
/// ints, so a slider tick worth ten contributes *nothing* to the combo portion
/// and a circle worth three hundred contributes twelve; ppy keep it with a note
/// that it is intentional. And the running total is truncated to an integer at
/// every object rather than at the end.
pub fn maximum_combo_score(beatmap: &Beatmap, mods: Mods) -> f64 {
    let multiplier = f64::from(difficulty_peppy_stars(beatmap));
    let timeline = Timeline::build(beatmap, mods);

    let mut combo: i64 = 0;
    let mut combo_score: i64 = 0;

    // Face value in, combo score out — the integer division is the whole point.
    let score_at = |value: i64, combo: i64| -> i64 {
        ((combo - 1).max(0) as f64 * ((value / 25) as f64 * multiplier)) as i64
    };

    for object in &timeline.objects {
        match &object.kind {
            TimedKind::Circle => {
                combo += 1;
                combo_score += score_at(300, combo);
            }
            TimedKind::Slider { .. } => {
                // Every piece first, each taking the combo up, and only then the
                // slider itself — which is why a long slider is worth more than
                // a circle in the same place.
                for _ in crate::slider_parts(beatmap, object) {
                    combo += 1;
                }
                // The slider does not raise the combo again; its head did.
                combo_score += score_at(300, combo);
            }
            TimedKind::Spinner => {
                // Its ticks are bonus score and raise no combo at all.
                combo += 1;
                combo_score += score_at(300, combo);
            }
        }
    }
    combo_score as f64
}

/// How many times a classic play broke, read out of its score.
///
/// Ported from `OsuLegacyScoreMissCalculator.Calculate`. The idea is neat: the
/// combo portion of a ScoreV1 total is an arithmetic progression in combo, so
/// the score a play *should* have if it never broke after its best combo can be
/// worked out — and how far the real total falls short of that says how many
/// times it did break.
///
/// It is bounded on both sides. Below one it defers to the combo-based count,
/// because a total is not precise enough to tell one break from none. Above, it
/// is capped by a harsher combo-based count than the ordinary one — the same
/// estimate raised to the power of 2.5 — so a strange total cannot invent
/// misses that the combo makes impossible.
pub fn score_based_miss_count(
    score: &crate::performance::Score,
    attributes: &crate::Attributes,
    mods: Mods,
) -> f64 {
    let Some(total) = score.legacy_total_score else { return 0.0 };
    if attributes.max_combo == 0 {
        return 0.0;
    }

    let v1_multiplier = attributes.legacy_score_base_multiplier * score_multiplier(mods);
    let combo_per_object = relevant_combo_per_object(attributes);
    let maximum = maximum_combo_based_miss_count(score, attributes);

    let during_max_combo =
        score_at_combo(score, attributes, f64::from(score.max_combo), combo_per_object, v1_multiplier);
    let remaining = total as f64 - during_max_combo;
    if remaining <= 0.0 {
        // The total is entirely accounted for by the best combo, so nothing can
        // be read from what is left.
        return maximum;
    }

    let remaining_combo = f64::from(attributes.max_combo.saturating_sub(score.max_combo));
    let expected = score_at_combo(score, attributes, remaining_combo, combo_per_object, v1_multiplier);

    // How many times the remainder had to be started over.
    (expected / remaining).max(1.0).min(maximum)
}

/// What a play of this accuracy would have scored by the time it reached
/// `combo`.
fn score_at_combo(
    score: &crate::performance::Score,
    attributes: &crate::Attributes,
    combo: f64,
    combo_per_object: f64,
    v1_multiplier: f64,
) -> f64 {
    let total_hits = f64::from(score.total_hits());
    let objects = combo / combo_per_object - 1.0;

    // The combo portion is an arithmetic progression, so its sum is closed-form
    // rather than walked.
    let combo_score = if combo_per_object > 0.0 {
        (2.0 * (combo_per_object - 1.0) + (objects - 1.0) * combo_per_object) * objects / 2.0
    } else {
        0.0
    };
    let combo_score = combo_score * score.accuracy() * 300.0 / 25.0 * v1_multiplier;

    let hit = (total_hits - f64::from(score.miss)) * combo / f64::from(attributes.max_combo);
    // And the portion that does not care about combo at all.
    let flat = (300.0 + attributes.nested_score_per_object) * score.accuracy() * hit;
    combo_score + flat
}

/// How much combo one object is worth on this map, on average.
///
/// Worked backwards out of the map's own maximum combo score by reversing the
/// arithmetic progression — which is cheaper than walking the map again and,
/// more to the point, is what ppy do.
fn relevant_combo_per_object(attributes: &crate::Attributes) -> f64 {
    let combo_score = attributes.maximum_legacy_combo_score
        / (300.0 / 25.0 * attributes.legacy_score_base_multiplier);
    let max_combo = f64::from(attributes.max_combo);
    (max_combo - 2.0) * max_combo / (max_combo + 2.0 * (combo_score - 1.0)).max(1.0)
}

/// The ceiling the score-based count is held under.
///
/// The same shape as the ordinary combo-based estimate with one difference that
/// matters: the shortfall is raised to the power of 2.5 rather than taken
/// plainly, which makes it a far harsher bound. It exists to stop a strange
/// total inventing misses, not to be the answer.
fn maximum_combo_based_miss_count(
    score: &crate::performance::Score,
    attributes: &crate::Attributes,
) -> f64 {
    let misses = f64::from(score.miss);
    if attributes.slider_count == 0 {
        return misses;
    }
    let likely_dropped =
        0.04 + 0.06 * attributes.aim_top_weighted_slider_factor.min(1.0).powi(2);
    let sliders = f64::from(attributes.slider_count);
    let threshold =
        f64::from(attributes.max_combo) - (4.0 + likely_dropped * sliders).min(sliders);

    let mut count = 0.0;
    if f64::from(score.max_combo) < threshold {
        count = (threshold / f64::from(score.max_combo).max(1.0)).powf(2.5);
    }
    count = count.min(f64::from(score.total_imperfect_hits()));

    let max_breaks = (attributes.slider_count as i64)
        .min(((attributes.max_combo as i64) - (score.max_combo as i64)) / 2)
        .max(0) as f64;
    if count - misses > max_breaks {
        count = misses + max_breaks;
    }
    count
}
