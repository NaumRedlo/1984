//! lazer's mod score multiplier, in both of its generations.
//!
//! This used to be one number per mod, hanging off the mod itself. It is not
//! any more:
//!
//! ```csharp
//! [Obsolete("This property is no longer used to calculate the score multiplier.
//!            Use `Ruleset.CreateScoreMultiplierCalculator()` instead.")]
//! public virtual double ScoreMultiplier => 1;
//! ```
//!
//! It moved to a calculator belonging to the ruleset, and that calculator is
//! itself versioned — `OsuScoreMultiplierCalculatorV1` and `…V2`, with the
//! change landing at replay version 30000017, "Mod score multiplier rebalance".
//!
//! Which matters because a replay carries the score the client computed *at the
//! time*. Reading a 2025 replay with 2026's table is not a rounding error: an
//! Easy play in the corpus came out forty-two per cent under before this
//! existed, because Easy went from a flat half to a curve starting at 0.8.
//!
//! lazer recalculates every stored score when it upgrades, which is why it can
//! keep one table. Nothing recalculates a replay file, so this keeps both.

use dossier_beatmap::Difficulty;
use dossier_replay::LazerMod;

/// Which generation of the table a score was computed with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Generation {
    /// Before the rebalance: flat numbers, and a rate curve shared by the
    /// speed mods in both directions.
    V1,
    /// After it. Harder mods pay more, several combinations are priced
    /// together rather than multiplied, and half the values now depend on the
    /// mod's own settings.
    V2,
}

/// Replays at or after this version were scored with the second table.
///
/// ```text
/// 30000017: Mod score multiplier rebalance. Recalculates the TotalScore of
///           all scores with TotalScoreWithoutMods present.
/// ```
pub const FIRST_V2_VERSION: i32 = 30_000_017;

impl Generation {
    pub fn of_replay_version(game_version: i32) -> Self {
        if game_version >= FIRST_V2_VERSION {
            Self::V2
        } else {
            Self::V1
        }
    }
}

/// What the mods multiply a lazer score by.
///
/// ```csharp
/// double result = 1;
/// if (allModsByType.Count > 1)
///     foreach (var (combination, multiplier) in combinationMultipliers)
///         if (remainingModTypes.IsSupersetOf(combination)) { … remainingModTypes.ExceptWith(combination); }
/// foreach (var modType in remainingModTypes)
///     if (singleMultipliers.TryGetValue(modType, out var multiplier)) result *= …;
/// ```
///
/// Two things in that are easy to miss and both change the answer. Combinations
/// are consulted in the order they were registered and each one *consumes* its
/// mods, so Hidden with Blinds is priced once at 1.24 rather than twice; and a
/// mod with no entry contributes nothing at all rather than being an error.
pub fn lazer_multiplier(
    generation: Generation,
    mods: &[LazerMod],
    difficulty: &Difficulty,
) -> f64 {
    if mods.is_empty() {
        return 1.0;
    }
    let mut remaining: Vec<&LazerMod> = mods.iter().collect();
    let mut result = 1.0;

    if remaining.len() > 1 {
        for (needs, price) in combinations(generation) {
            let picked: Option<Vec<&LazerMod>> = needs
                .iter()
                .map(|acronym| remaining.iter().copied().find(|m| m.acronym == *acronym))
                .collect();
            if let Some(picked) = picked {
                result *= price(&picked, difficulty);
                remaining.retain(|m| !needs.contains(&m.acronym.as_str()));
            }
        }
    }

    for m in remaining {
        result *= single(generation, m, difficulty);
    }
    result
}

type Price = fn(&[&LazerMod], &Difficulty) -> f64;

/// Combinations priced as a unit, in registration order.
fn combinations(generation: Generation) -> Vec<(&'static [&'static str], Price)> {
    match generation {
        // V1 registers none at all.
        Generation::V1 => Vec::new(),
        Generation::V2 => vec![
            (&["HD", "BL"], (|_, _| BLINDS_V2) as Price),
            (&["HD", "WG"], |m, _| hidden_v2(m[0], true)),
            (&["HD", "GR"], |m, _| hidden_v2(m[0], true)),
            (&["HD", "DF"], |m, _| hidden_v2(m[0], true) * deflate_v2(m[1])),
            (&["HD", "RP"], |m, _| hidden_v2(m[0], true)),
            (&["HD", "DP"], |m, _| hidden_v2(m[0], true)),
            (&["TC", "BL"], |_, _| BLINDS_V2),
            (&["FL", "FF"], |m, _| 1.0 + (flashlight_v2(m[0]) - 1.0) / 2.0),
        ],
    }
}

fn single(generation: Generation, m: &LazerMod, difficulty: &Difficulty) -> f64 {
    let plain = m.uses_default_configuration();
    match generation {
        Generation::V1 => match m.acronym.as_str() {
            "EZ" => 0.5,
            "NF" => 0.5,
            "HT" | "DC" => rate_adjust_v1(m.number("speed_change", 0.75)),
            "HR" => if plain { 1.06 } else { 1.0 },
            "DT" | "NC" => rate_adjust_v1(m.number("speed_change", 1.5)),
            "HD" => if plain { 1.06 } else { 1.0 },
            "FL" => if plain { 1.12 } else { 1.0 },
            "BL" => if plain { 1.12 } else { 1.0 },
            "TP" => 0.1,
            "DA" => 0.5,
            "CL" => 0.96,
            "RX" | "AP" => 0.1,
            "SO" => 0.9,
            "WU" | "WD" => 0.5,
            "MG" => 0.5,
            "AS" => 0.5,
            "SY" => 0.8,
            _ => 1.0,
        },
        Generation::V2 => match m.acronym.as_str() {
            "EZ" => easy_v2(m),
            "NF" => 0.5,
            "HT" | "DC" => half_time_v2(m.number("speed_change", 0.75)),
            "HR" => 1.09,
            "DT" | "NC" => double_time_v2(m.number("speed_change", 1.5)),
            "HD" => hidden_v2(m, false),
            "TC" => 1.02,
            "FL" => flashlight_v2(m),
            "BL" => BLINDS_V2,
            "TP" => 0.01,
            "DA" => difficulty_adjust_v2(m, difficulty),
            // Classic's own note-lock switch is priced, which is the only
            // place a mod's setting changes a multiplier by more than a
            // rounding: keeping stable's lock is worth more than not.
            "CL" => if m.switch("classic_note_lock", true) { 0.985 } else { 0.96 },
            "RD" => 0.7,
            "RX" | "AP" => 0.1,
            "SO" => 0.95,
            "DF" => deflate_v2(m),
            "WU" | "WD" => time_ramp_v2(m),
            "AD" => 0.7,
            "MG" => 0.7 - m.number("attraction_strength", 0.5) * 0.6,
            "AS" => 0.1,
            "SY" => 0.99,
            _ => 1.0,
        },
    }
}

const BLINDS_V2: f64 = 1.24;

/// V1 priced every rate change on one curve, in both directions.
fn rate_adjust_v1(speed: f64) -> f64 {
    let value = (speed * 10.0) as i64 as f64 / 10.0 - 1.0;
    if speed >= 1.0 {
        1.0 + value / 5.0
    } else {
        0.6 + value
    }
}

/// `0.8x base, reduced by 0.1x per extra life, floored at 0.4`.
fn easy_v2(m: &LazerMod) -> f64 {
    const DEFAULT_RETRIES: f64 = 2.0;
    let retries = m.number("retries", DEFAULT_RETRIES);
    (0.8 - (0.1 * (retries - DEFAULT_RETRIES)).max(0.0)).max(0.4)
}

/// `0.2x at half speed, +0.07x per 0.05x` — default HalfTime is 0.55.
fn half_time_v2(speed: f64) -> f64 {
    (speed * 20.0) as i64 as f64 / 20.0 * 1.4 - 0.5
}

/// Linear from 1.0 to 1.46, less a penny for an unusual rate. Default
/// DoubleTime is 1.23.
fn double_time_v2(speed: f64) -> f64 {
    let value = (speed * 10.0) as i64 as f64 / 10.0;
    let penalty = if value != 1.5 && value != 1.0 { 0.01 } else { 0.0 };
    (value - 1.0) * 0.46 + 1.0 - penalty
}

/// Hidden is worth less when something else is already telling the player
/// where the beat is.
fn hidden_v2(m: &LazerMod, other_mods_provide_timing_info: bool) -> f64 {
    let mut value = 1.04;
    if m.switch("only_fade_approach_circles", false) {
        value -= 0.02;
    }
    if other_mods_provide_timing_info {
        value -= 0.02;
    }
    value
}

fn flashlight_v2(m: &LazerMod) -> f64 {
    let size = m.number("size_multiplier", 1.0);
    let mut value = (1.2 - 0.2 * (size - 1.0)).clamp(1.02, 1.2);
    if !m.switch("combo_based_size", true) {
        value = 1.0 + (value - 1.0) / 5.0;
    }
    value
}

/// Every difficulty setting moved away from the map's own costs five per cent
/// per tenth, and the four are multiplied together.
fn difficulty_adjust_v2(m: &LazerMod, map: &Difficulty) -> f64 {
    let term = |name: &str, authored: f64| {
        let chosen = m.number(name, authored);
        (1.0 - (chosen - authored).abs() * 0.5).max(0.1)
    };
    (term("circle_size", map.circle_size)
        * term("drain_rate", map.hp_drain)
        * term("overall_difficulty", map.overall_difficulty)
        * term("approach_rate", map.approach_rate))
    .max(0.1)
}

fn deflate_v2(m: &LazerMod) -> f64 {
    const DEFAULT_START_SCALE: f64 = 2.0;
    1.0 - (0.02 * (m.number("start_scale", DEFAULT_START_SCALE) - DEFAULT_START_SCALE)).max(0.0)
}

/// A ramp is priced mostly by its slower end — four fifths of it.
fn time_ramp_v2(m: &LazerMod) -> f64 {
    let (initial_default, final_default) = if m.acronym == "WU" {
        (1.0, 1.5)
    } else {
        (1.0, 0.75)
    };
    let initial = m.number("initial_rate", initial_default);
    let last = m.number("final_rate", final_default);
    let (slow, fast) = (initial.min(last), initial.max(last));
    let price = |speed: f64| {
        if speed < 1.0 {
            half_time_v2(speed)
        } else {
            double_time_v2(speed)
        }
    };
    0.8 * price(slow) + 0.2 * price(fast)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(acronyms: &[&str]) -> Vec<LazerMod> {
        acronyms.iter().map(|a| LazerMod::plain(a)).collect()
    }

    fn difficulty() -> Difficulty {
        Difficulty::default()
    }

    #[test]
    fn the_two_generations_price_the_same_mods_differently() {
        // The rebalance is not a tweak. Easy went from a flat half to a curve
        // starting at four fifths, which on the corpus's one Easy replay was
        // forty-two per cent of the score.
        let ez = plain(&["EZ"]);
        assert_eq!(lazer_multiplier(Generation::V1, &ez, &difficulty()), 0.5);
        assert_eq!(lazer_multiplier(Generation::V2, &ez, &difficulty()), 0.8);

        let hr = plain(&["HR"]);
        assert_eq!(lazer_multiplier(Generation::V1, &hr, &difficulty()), 1.06);
        assert_eq!(lazer_multiplier(Generation::V2, &hr, &difficulty()), 1.09);
    }

    #[test]
    fn no_mods_is_no_multiplier_in_either() {
        for generation in [Generation::V1, Generation::V2] {
            assert_eq!(lazer_multiplier(generation, &[], &difficulty()), 1.0);
        }
    }

    #[test]
    fn the_speed_mods_follow_their_own_curves() {
        // V1 shared one curve between both directions; V2 gave each its own.
        let dt = plain(&["DT"]);
        let ht = plain(&["HT"]);
        assert!((lazer_multiplier(Generation::V1, &dt, &difficulty()) - 1.1).abs() < 1e-9);
        assert!((lazer_multiplier(Generation::V2, &dt, &difficulty()) - 1.23).abs() < 1e-9);
        // Default HalfTime is 0.55 under V2, against 0.3 under V1 — nearly
        // twice as harsh, which is the single largest move in the rebalance.
        assert!((lazer_multiplier(Generation::V1, &ht, &difficulty()) - 0.3).abs() < 1e-9);
        assert!((lazer_multiplier(Generation::V2, &ht, &difficulty()) - 0.55).abs() < 1e-9);
    }

    #[test]
    fn a_combination_is_priced_once_and_not_twice() {
        // Hidden with Blinds is 1.24 flat, not 1.04 × 1.24. The combination
        // consumes both mods, and getting that wrong is a fifth of the score
        // on every play that uses them.
        let together = plain(&["HD", "BL"]);
        let priced = lazer_multiplier(Generation::V2, &together, &difficulty());
        assert!((priced - BLINDS_V2).abs() < 1e-9, "{priced}");

        // Apart, they are their own numbers and do multiply.
        let hd = lazer_multiplier(Generation::V2, &plain(&["HD"]), &difficulty());
        let bl = lazer_multiplier(Generation::V2, &plain(&["BL"]), &difficulty());
        assert!((hd - 1.04).abs() < 1e-9);
        assert!((bl - BLINDS_V2).abs() < 1e-9);
        assert!(priced < hd * bl, "the combination should be the cheaper one");

        // V1 knows no combinations at all, so there it *is* the product.
        let v1 = lazer_multiplier(Generation::V1, &together, &difficulty());
        assert!((v1 - 1.06 * 1.12).abs() < 1e-9, "{v1}");
    }

    #[test]
    fn a_mod_with_no_price_is_free_rather_than_fatal() {
        // Mirror, Alternate, Single Tap and the rest are registered nowhere.
        // A table that treated an unknown acronym as an error would refuse to
        // score half the replays anyone actually plays.
        let odd = plain(&["MR", "AL", "SG", "TD"]);
        for generation in [Generation::V1, Generation::V2] {
            assert_eq!(lazer_multiplier(generation, &odd, &difficulty()), 1.0);
        }
    }

    #[test]
    fn settings_the_player_changed_are_priced() {
        // lazer only records settings that differ from their defaults, so an
        // empty map means "as it comes" — and several multipliers ask about
        // exactly that.
        let mut fiddled = LazerMod::plain("HR");
        fiddled
            .settings
            .insert("some_setting".into(), dossier_replay::Setting::Bool(true));
        // V1 asks only whether anything was touched at all.
        assert_eq!(
            lazer_multiplier(Generation::V1, &[fiddled], &difficulty()),
            1.0
        );

        // Classic is the one whose switch is priced outright: keeping stable's
        // note lock is worth more than dropping it.
        let mut loose = LazerMod::plain("CL");
        loose.settings.insert(
            "classic_note_lock".into(),
            dossier_replay::Setting::Bool(false),
        );
        assert_eq!(
            lazer_multiplier(Generation::V2, &[LazerMod::plain("CL")], &difficulty()),
            0.985
        );
        assert_eq!(lazer_multiplier(Generation::V2, &[loose], &difficulty()), 0.96);
    }

    #[test]
    fn the_generation_is_read_off_the_replay_version() {
        assert_eq!(Generation::of_replay_version(30_000_016), Generation::V1);
        assert_eq!(Generation::of_replay_version(30_000_017), Generation::V2);
        assert_eq!(Generation::of_replay_version(30_000_018), Generation::V2);
    }
}
