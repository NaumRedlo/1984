//! What a play is worth — the other half, and the one that is about a person
//! rather than about a map.
//!
//! Ported from `OsuPerformanceCalculator`. The difficulty side asks what a map
//! demands; this asks how much of that demand a particular play met.
//!
//! It is graded against `corpus/scores.json` — plays put through ppy's own
//! `simulate`, which prints not just a pp figure but the pieces it was built
//! from. That matters as much here as the attributes did there: one wrong
//! number tells you something is broken, eight tell you which.

use crate::preprocessing::MIN_DELTA_TIME;
use crate::utils::{logistic, smoothstep};
use crate::Attributes;

/// What a play did, as the calculator needs to hear it.
#[derive(Debug, Clone, Default)]
pub struct Score {
    pub max_combo: u32,
    pub great: u32,
    pub ok: u32,
    pub meh: u32,
    pub miss: u32,
    /// Slider ends caught. Lazer counts these; a classic score cannot know.
    pub slider_tail_hit: u32,
    /// Ticks and reverse arrows missed. Lazer only, again.
    pub large_tick_miss: u32,
    /// Whether the play was scored the old way, where a slider's head carries
    /// no accuracy and a dropped tail is invisible.
    ///
    /// This is the Classic mod, and almost everything below branches on it: a
    /// classic score has to be *guessed at* where a lazer score can simply be
    /// read.
    pub classic: bool,
    /// Set only for a classic score that has one, which is what lets the
    /// calculator work misses out from the score rather than from the combo.
    pub legacy_total_score: Option<u64>,
    /// The play's accuracy, from nothing to one, as the *game* computed it.
    ///
    /// Given rather than worked out, because under lazer's rules it is not
    /// derivable from the four judgements: slider tails and large ticks count
    /// towards it too. A play graded 1356 greats, 288 oks and 8 mehs is 87.97%
    /// by the old arithmetic and 89.23% by the game's, and that gap is over
    /// eight per cent of the accuracy component once it is raised to the
    /// twenty-fourth power.
    ///
    /// `None` falls back to the four judgements, which is exactly right for a
    /// classic score and the best available for anything that did not say.
    pub accuracy: Option<f64>,
}

impl Score {
    pub fn total_hits(&self) -> u32 {
        self.great + self.ok + self.meh + self.miss
    }

    pub fn total_successful_hits(&self) -> u32 {
        self.great + self.ok + self.meh
    }

    pub fn total_imperfect_hits(&self) -> u32 {
        self.ok + self.meh + self.miss
    }

    /// The play's accuracy, from nothing to one.
    pub fn accuracy(&self) -> f64 {
        if let Some(given) = self.accuracy {
            return given.clamp(0.0, 1.0);
        }
        let total = self.total_hits();
        if total == 0 {
            return 0.0;
        }
        let weighted = 300.0 * f64::from(self.great)
            + 100.0 * f64::from(self.ok)
            + 50.0 * f64::from(self.meh);
        (weighted / (300.0 * f64::from(total))).clamp(0.0, 1.0)
    }
}

/// How many combo breaks the play really had, misses and slider breaks alike.
///
/// Ported from `calculateComboBasedEstimatedMissCount`. A miss is not the only
/// way to break combo — dropping a slider does it too, and on a classic score
/// nothing records that it happened. So the count is inferred from how far short
/// of the map's maximum the combo fell, and then held down by what the
/// judgements make possible.
///
/// The two branches are worth reading together. A lazer score knows exactly
/// which slider tails it dropped, so its threshold is exact and its ceiling is
/// the tick misses it also recorded. A classic score knows neither, so it
/// *estimates* how many tails a player of this map would likely have dropped —
/// from how demanding the map's sliders are — and then bounds the answer by the
/// fact that breaking a slider costs at least two combo, so a play one combo
/// short cannot have broken one at all.
pub fn combo_based_miss_count(score: &Score, attributes: &Attributes) -> f64 {
    let misses = f64::from(score.miss);
    if attributes.slider_count == 0 {
        return misses;
    }
    let max_combo = f64::from(attributes.max_combo);
    let score_combo = f64::from(score.max_combo);
    let mut count = misses;

    if score.classic {
        // Hard sliders get dropped at the end; easy ones get broken in the
        // middle. Which of the two a map invites is what its top-weighted
        // slider factor says.
        let likely_dropped = 0.04 + 0.06 * attributes.aim_top_weighted_slider_factor.min(1.0).powi(2);
        let sliders = f64::from(attributes.slider_count);
        // A dropped tail costs no combo and breaks none, so a full combo is the
        // maximum less however many were let go.
        let threshold = max_combo - (4.0 + likely_dropped * sliders).min(sliders);
        if score_combo < threshold {
            count = threshold / score_combo.max(1.0);
        }
        // There cannot be more misses than there were imperfect judgements.
        count = count.min(f64::from(score.total_imperfect_hits()));

        // Every slider is worth at least two combo in classic scoring — the
        // head and the tail — so a play one combo short of maximum did not
        // break a slider, it merely dropped an end.
        let max_breaks = (attributes.slider_count as i64)
            .min(((attributes.max_combo as i64) - (score.max_combo as i64)) / 2)
            .max(0) as f64;
        if count - misses > max_breaks {
            count = misses + max_breaks;
        }
    } else {
        let dropped = f64::from(attributes.slider_count.saturating_sub(score.slider_tail_hit));
        let threshold = max_combo - dropped;
        if score_combo < threshold {
            count = threshold / score_combo.max(1.0);
        }
        // A missed tick breaks combo too, so the two together bound this.
        count = count.min(f64::from(score.large_tick_miss + score.miss));
    }
    count
}

/// How many of those breaks were sliders rather than misses.
///
/// Ported from `calculateEstimatedSliderBreaks`. Nothing at all on a lazer
/// score, which records what it dropped, and nothing on a play with no imperfect
/// judgements — a slider break leaves a trail of Oks and Mehs, so a spotless
/// play that lost combo lost it some other way.
pub fn estimated_slider_breaks(
    score: &Score,
    attributes: &Attributes,
    effective_misses: f64,
    top_weighted_slider_factor: f64,
) -> f64 {
    let non_miss_mistakes = f64::from(score.ok + score.meh);
    if !score.classic || non_miss_mistakes == 0.0 {
        return 0.0;
    }

    let missed_combo = 1.0 - f64::from(score.max_combo) / f64::from(attributes.max_combo);
    let mut breaks = non_miss_mistakes.min(effective_misses * top_weighted_slider_factor);

    // More Oks and Mehs make a slider break likelier. ppy's own note: the
    // arbitrary constants on both sides of the division keep this steady at the
    // extremes.
    let adjustment = (non_miss_mistakes - breaks + 4.5) / (non_miss_mistakes + 4.0);

    // Around a single effective miss there is little room for a hidden break:
    // the score-based estimate is good at telling one break from none.
    breaks *= smoothstep(effective_misses, 1.0, 2.0);

    breaks * adjustment * logistic(missed_combo, 0.33, 15.0, 1.0)
}

/// Everything the calculator works out about the play before it starts pricing
/// it.
#[derive(Debug, Clone, Default)]
pub struct Effective {
    pub miss_count: f64,
    pub combo_based_miss_count: f64,
    pub aim_slider_breaks: f64,
    pub speed_slider_breaks: f64,
}

/// The breaks a play really had, bounded by what is possible.
pub fn effective(score: &Score, attributes: &Attributes) -> Effective {
    let combo_based = combo_based_miss_count(score, attributes);

    // The score-based estimate belongs to classic scores that carry a total,
    // and needs the legacy score simulator, which is not ported yet. Until it
    // is, such a score falls back to the combo-based count — the same answer
    // the calculator gives when a classic score has no total to read.
    let mut miss_count = combo_based;

    miss_count = miss_count.max(f64::from(score.miss));
    miss_count = miss_count.min(f64::from(score.total_hits()));
    miss_count = miss_count.max(0.0);

    let (aim_breaks, speed_breaks) = if miss_count > 0.0 {
        (
            estimated_slider_breaks(score, attributes, miss_count,
                                    attributes.aim_top_weighted_slider_factor),
            estimated_slider_breaks(score, attributes, miss_count,
                                    attributes.speed_top_weighted_slider_factor),
        )
    } else {
        (0.0, 0.0)
    };

    Effective {
        miss_count,
        combo_based_miss_count: combo_based,
        aim_slider_breaks: aim_breaks,
        speed_slider_breaks: speed_breaks,
    }
}

/// The overall difficulty a play was judged at, from the window it was given.
pub fn overall_difficulty(great_hit_window: f64) -> f64 {
    (79.5 - great_hit_window / 2.0) / 6.0
}

/// Kept so the floor is stated once; the performance side inherits it.
pub const MINIMUM_DELTA_TIME: f64 = MIN_DELTA_TIME;

/// How much a play's breaks cost it.
///
/// ```csharp
/// private double calculateMissPenalty(double missCount, double difficultStrainCount)
///     => 0.93 / (missCount / (4 * Math.Log(Math.Max(1, difficultStrainCount))) + 1);
/// ```
///
/// Divided by how much of the map was difficult, not by how long it was: a
/// break in a map with one hard section costs far more of that map than the
/// same break in one that is hard throughout. That is what the strain counts
/// were collected for.
pub fn miss_penalty(miss_count: f64, difficult_strain_count: f64) -> f64 {
    0.93 / (miss_count / (4.0 * difficult_strain_count.max(1.0).ln()) + 1.0)
}

/// The bonus a longer map earns.
///
/// Holding concentration is itself a skill, so two thousand objects are worth
/// more than one thousand — and past two thousand the bonus keeps growing, but
/// only logarithmically.
fn length_bonus(total_hits: u32) -> f64 {
    let hits = f64::from(total_hits);
    0.95
        + 0.35 * (hits / 2000.0).min(1.0)
        + if hits > 2000.0 { (hits / 2000.0).log10() * 0.5 } else { 0.0 }
}

/// What the aim was worth.
///
/// Ported from `computeAimValue`.
pub fn aim_value(score: &Score, attributes: &Attributes, effective: &Effective) -> f64 {
    let mut difficulty = attributes.aim_difficulty;

    if attributes.slider_count > 0 && attributes.aim_difficult_slider_count > 0.0 {
        // How many of the map's demanding sliders went unfollowed. A lazer score
        // can say; a classic one is bounded by how much combo went missing and
        // how many judgements were imperfect.
        let dropped = if score.classic {
            f64::from(score.total_imperfect_hits())
                .min(f64::from(attributes.max_combo.saturating_sub(score.max_combo)))
                .clamp(0.0, attributes.aim_difficult_slider_count)
        } else {
            let ends = attributes.slider_count.saturating_sub(score.slider_tail_hit);
            f64::from(ends + score.large_tick_miss)
                .clamp(0.0, attributes.aim_difficult_slider_count)
        };
        // Only the part of aim that rests on sliders is at stake, and it falls
        // away cubically — letting a few go is nearly free, letting most go is
        // not.
        let nerf = (1.0 - attributes.slider_factor)
            * (1.0 - dropped / attributes.aim_difficult_slider_count).powi(3)
            + attributes.slider_factor;
        difficulty *= nerf;
    }

    let mut value = crate::aim::difficulty_to_performance(difficulty);
    value *= length_bonus(score.total_hits());

    if effective.miss_count > 0.0 {
        let relevant = (effective.miss_count + effective.aim_slider_breaks)
            .min(f64::from(score.total_imperfect_hits() + score.large_tick_miss));
        value *= miss_penalty(relevant, attributes.aim_difficult_strain_count);
    }

    // Aiming well and hitting badly is not aiming well.
    value * score.accuracy()
}

/// What the accuracy was worth.
///
/// Ported from `computeAccuracyValue`. Measured against only the objects that
/// *have* accuracy, which is where the two scoring models differ again: under
/// classic rules a slider's head carries none, so only circles count, and under
/// lazer's rules sliders count too.
///
/// Raised to the twenty-fourth power, which is why the figure is so unforgiving
/// — the step from 98% to 99% is most of this component.
pub fn accuracy_value(score: &Score, attributes: &Attributes, overall_difficulty: f64) -> f64 {
    let mut with_accuracy = attributes.hit_circle_count;
    if !score.classic {
        with_accuracy += attributes.slider_count;
    }
    if with_accuracy == 0 {
        return 0.0;
    }

    // Everything that is not one of those is assumed to have been a Great, so
    // it neither helps nor hurts.
    let others = f64::from(score.total_hits().saturating_sub(with_accuracy));
    let better = (((f64::from(score.great) - others) * 6.0
        + f64::from(score.ok) * 2.0
        + f64::from(score.meh))
        / (f64::from(with_accuracy) * 6.0))
        .max(0.0);

    let mut value = 1.52163f64.powf(overall_difficulty) * better.powi(24) * 2.83;
    // A map with more objects to be accurate on is a longer test of it.
    let share = f64::from(with_accuracy) / 1000.0;
    value *= if with_accuracy < 1000 { share.powf(0.3) } else { share.powf(0.1) };
    value
}
