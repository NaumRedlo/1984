//! Health, in both clients' models.
//!
//! Roughly half the replays in the corpus carry osu!'s own life-bar graph in
//! their header, and for those there is nothing to model — the game already
//! said what the bar did. The other half carry nothing at all, and a renderer
//! that only draws the bar when the replay happens to have brought one is a
//! renderer whose HUD changes shape for no reason the viewer can see.
//!
//! So this computes it. The half that *do* carry a graph are the test: the
//! model is measured against them rather than trusted.
//!
//! The two clients disagree about almost everything here. stable does not have
//! a drain formula at all — it *solves* for the drain, running a perfect play
//! over and over and nudging the rate until that play finishes with the bar
//! where the difficulty says it should be. lazer does the same thing by binary
//! search over a much simpler curve. Neither can be written down as an
//! expression in HP, which is why "the drain rate is HP × something" is wrong
//! for every map ever made.

use dossier_beatmap::{difficulty_range, Difficulty};
use dossier_replay::{bits, Mods};

use crate::judge::{Judge, Judgement, Part};
use crate::ruleset::Ruleset;
use crate::timeline::{TimedKind, Timeline};

// ── stable ───────────────────────────────────────────────────────────────

/// stable keeps health on a 0..200 scale internally. Everything below is in
/// those units and only the accessors divide out.
const MAX_HP: f64 = 200.0;

const HP_MU: f64 = 6.0;
const HP_KATU: f64 = 10.0;
const HP_GEKI: f64 = 14.0;

const HP_50: f64 = 0.4;
const HP_100: f64 = 2.2;
const HP_300: f64 = 6.0;

const HP_SLIDER_TICK: f64 = 3.0;
const HP_SLIDER_REPEAT: f64 = 4.0;
const HP_SPINNER_SPIN: f64 = 1.7;

/// The bar, as stable keeps it.
///
/// Two numbers rather than one: the capped health is what the player sees and
/// what kills them, and the uncapped one is how much headroom a perfect play
/// had — the calibration asks whether the map gave back *enough*, and a bar
/// pinned at full tells it nothing.
#[derive(Debug, Clone, Copy)]
struct Meter {
    health: f64,
    uncapped: f64,
}

impl Meter {
    fn full() -> Self {
        Self {
            health: MAX_HP,
            uncapped: MAX_HP,
        }
    }

    fn increase(&mut self, amount: f64) {
        self.uncapped = (self.uncapped + amount).max(0.0);
        self.health = (self.health + amount).clamp(0.0, MAX_HP);
    }
}

/// What the calibration settles on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rates {
    /// Health lost per millisecond of play.
    pub passive_drain: f64,
    /// Applied to every ordinary gain.
    pub normal: f64,
    /// Applied to the bonus at the end of a combo.
    pub combo_end: f64,
}

/// The gain for one judged thing, before the multipliers.
///
/// The 50 and the 100 are worth eight times as much on an HP 0 map as on an
/// HP 5 one, and a 300 is worth the same everywhere. That asymmetry is the
/// whole reason an easy map is hard to fail: it is not that the drain is
/// gentler, it is that a sloppy hit still pays.
fn stable_gain(part: Part, result: Judgement, hp: f64) -> f64 {
    match part {
        Part::Circle | Part::Slider | Part::Spinner => match result {
            Judgement::Great => HP_300,
            Judgement::Ok => difficulty_range(hp, 8.0 * HP_100, HP_100, HP_100),
            Judgement::Meh => difficulty_range(hp, 8.0 * HP_50, HP_50, HP_50),
            // A whole object missed is the expensive one.
            Judgement::Miss => difficulty_range(hp, -6.0, -25.0, -40.0),
        },
        Part::SliderTick => {
            if result.is_miss() {
                slider_miss(hp)
            } else {
                HP_SLIDER_TICK
            }
        }
        Part::SliderHead | Part::SliderRepeat | Part::SliderTail => {
            if result.is_miss() {
                slider_miss(hp)
            } else {
                HP_SLIDER_REPEAT
            }
        }
        // A turn of a spinner pays as it happens, which is what lets a spinner
        // pull a dying play back. Turns *past* the requirement pay nothing:
        // the calibration below counts `required_spins` and no more, so paying
        // for the extra ones would make the model and the play disagree on the
        // same spinner — and the model is what the drain is solved against.
        Part::SpinnerSpin | Part::SpinnerPoints => HP_SPINNER_SPIN,
        Part::SpinnerBonus => 0.0,
    }
}

/// Dropping a piece of a slider costs, but nothing like missing a note.
fn slider_miss(hp: f64) -> f64 {
    difficulty_range(hp, -4.0, -15.0, -28.0)
}

/// Whether a gain is scaled by `normal` — the misses are not, which is why
/// raising the multiplier to keep a perfect play alive does not also make a
/// bad play survivable.
fn scaled_by_normal(part: Part, result: Judgement) -> bool {
    !(result.is_miss() && matches!(part, Part::Circle | Part::Slider | Part::Spinner))
}

/// The bonus at the end of a combo.
///
/// Worth more than any single hit — fourteen against six for a 300 — and it is
/// what actually keeps a player alive through a hard map. A combo broken early
/// costs its whole bonus, not just the missed note.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ComboEnd {
    /// Every hit in the combo was a 300.
    Geki,
    /// No 50s and nothing missed, but some 100s.
    Katu,
    /// Anything else.
    Mu,
}

impl ComboEnd {
    fn value(self) -> f64 {
        match self {
            Self::Geki => HP_GEKI,
            Self::Katu => HP_KATU,
            Self::Mu => HP_MU,
        }
    }
}

/// How many spins a spinner asks for.
fn required_spins(difficulty: &Difficulty, duration_ms: f64) -> f64 {
    (difficulty.spins_per_second() * duration_ms / 1000.0).floor()
}

/// Solve for the drain rate.
///
/// Not a formula — a loop. Start with a guess, play the map perfectly, and see
/// what the bar did:
///
/// * dropped below what the difficulty allows → the drain is too harsh, take
///   4% off and start again;
/// * three combos in a row ended below their floor → the gains are too small,
///   put 7% on the combo bonus and 3% on everything else;
/// * finished below the closing floor, or gave back less headroom than the
///   difficulty promises → both, gently.
///
/// It converges because every adjustment moves the same way, and it has to be
/// run before a single millisecond of the real play can be drawn.
pub fn calibrate(
    timeline: &Timeline,
    difficulty: &Difficulty,
    breaks: &[(f64, f64)],
    format_version: u32,
) -> Rates {
    let hp = difficulty.hp_drain;
    let objects = &timeline.objects;
    let Some(first) = objects.first() else {
        return Rates {
            passive_drain: 0.0,
            normal: 1.0,
            combo_end: 1.0,
        };
    };

    let lowest_ever = difficulty_range(hp, 195.0, 160.0, 60.0);
    let lowest_combo_end = difficulty_range(hp, 198.0, 170.0, 80.0);
    let lowest_end = difficulty_range(hp, 198.0, 180.0, 80.0);
    let recovery_wanted = difficulty_range(hp, 8.0, 4.0, 0.0);

    let mut rates = Rates {
        passive_drain: 0.05,
        normal: 1.0,
        combo_end: 1.0,
    };
    let start = first.start_ms - difficulty.preempt_ms();

    // A map that cannot be calibrated would spin here forever. The real loop
    // converges in a few dozen passes; this is only a floor under a pathology.
    for _ in 0..10_000 {
        let mut meter = Meter::full();
        let mut last_time = start.trunc();
        let mut break_index = 0usize;
        let mut combo_too_low = 0usize;
        let mut failed = false;

        for (i, object) in objects.iter().enumerate() {
            let local_last = last_time;
            let mut break_time = 0.0;
            if let Some(&(from, to)) = breaks.get(break_index) {
                if from >= local_last && to <= object.start_ms {
                    // Before format 8 the drain ran on into the break rather
                    // than stopping at the last note, so the two versions
                    // credit different amounts of free time.
                    break_time = if format_version < 8 {
                        to - from
                    } else {
                        to - local_last
                    };
                    break_index += 1;
                }
            }

            meter.increase(-rates.passive_drain * (object.start_ms - (last_time + break_time)));
            last_time = object.end_ms.trunc();

            if meter.health <= lowest_ever {
                failed = true;
                rates.passive_drain *= 0.96;
                break;
            }

            let over_object = rates.passive_drain * (object.end_ms - object.start_ms);
            let under = (meter.health - over_object).min(0.0);
            meter.increase(-over_object);

            match &object.kind {
                TimedKind::Slider { slides, .. } => {
                    for _ in 0..*slides {
                        meter.increase(rates.normal * HP_SLIDER_REPEAT);
                    }
                    for _ in 0..object.tick_times().len() {
                        meter.increase(rates.normal * HP_SLIDER_TICK);
                    }
                }
                TimedKind::Spinner => {
                    let spins = required_spins(difficulty, object.duration_ms()) as usize;
                    for _ in 0..spins {
                        meter.increase(rates.normal * HP_SPINNER_SPIN);
                    }
                }
                TimedKind::Circle => {}
            }

            // The bar can dip below the floor *during* a long object and come
            // back up by its end. Checking only the end would miss the death.
            if under < 0.0 && meter.health + under <= lowest_ever {
                failed = true;
                rates.passive_drain *= 0.96;
                break;
            }

            let combo_ends = i + 1 == objects.len() || objects[i + 1].new_combo;
            if combo_ends {
                meter.increase(rates.normal * HP_300 + rates.combo_end * HP_GEKI);
                if meter.health < lowest_combo_end {
                    combo_too_low += 1;
                    if combo_too_low > 2 {
                        rates.combo_end *= 1.07;
                        rates.normal *= 1.03;
                        failed = true;
                        break;
                    }
                }
            } else {
                meter.increase(rates.normal * HP_300);
            }
        }

        if !failed && meter.health < lowest_end {
            failed = true;
            rates.passive_drain *= 0.94;
            rates.combo_end *= 1.01;
            rates.normal *= 1.01;
        }

        if !failed {
            let recovery = (meter.uncapped - MAX_HP) / objects.len() as f64;
            if recovery < recovery_wanted {
                failed = true;
                rates.passive_drain *= 0.96;
                rates.combo_end *= 1.02;
                rates.normal *= 1.01;
            }
        }

        if !failed {
            break;
        }
    }

    rates
}

// ── lazer ────────────────────────────────────────────────────────────────

/// What one judged thing gives back in lazer, as a fraction of the whole bar.
///
/// A flatter table than stable's, and stated directly rather than interpolated
/// — only the misses move with HP.
fn lazer_gain(part: Part, result: Judgement, hp: f64) -> f64 {
    match part {
        Part::Circle | Part::Spinner => match result {
            Judgement::Great => 0.03,
            Judgement::Ok => 0.011,
            Judgement::Meh => 0.002,
            Judgement::Miss => difficulty_range(hp, -0.03, -0.125, -0.2),
        },
        // Our summary of a slider is not a judgement lazer has; its pieces
        // below carry the whole of it.
        Part::Slider => 0.0,
        // `SmallBonus` and `LargeBonus`. lazer pays for both, and by so little
        // that a spinner is worth a fraction of one circle.
        Part::SpinnerSpin | Part::SpinnerPoints => 0.0011,
        Part::SpinnerBonus => 0.0022,
        Part::SliderTick => {
            if result.is_miss() {
                difficulty_range(hp, -0.02, -0.075, -0.14)
            } else {
                0.015
            }
        }
        Part::SliderHead | Part::SliderRepeat | Part::SliderTail => {
            if result.is_miss() {
                difficulty_range(hp, -0.02, -0.075, -0.14)
            } else {
                0.02
            }
        }
    }
}

/// lazer's combo-end bonus, on the same three tiers as stable's but far
/// smaller relative to the bar.
fn lazer_combo_bonus(end: ComboEnd) -> f64 {
    match end {
        ComboEnd::Geki => 0.07,
        ComboEnd::Katu => 0.05,
        ComboEnd::Mu => 0.03,
    }
}

/// Where lazer aims to leave the bar at its lowest, on a perfect play.
fn lazer_target_minimum(hp: f64) -> f64 {
    difficulty_range(hp, 0.99, 0.9, 0.4).clamp(0.0, 1.0)
}

/// lazer's drain rate, by binary search.
///
/// Same idea as stable's loop and a much tidier execution: halve the step each
/// pass and move toward the target, so thirty passes put the lowest point of a
/// perfect play within a billionth of where the difficulty wants it.
fn lazer_drain_rate(gains: &[(f64, f64)], breaks: &[(f64, f64)], start: f64, target: f64) -> f64 {
    if gains.len() <= 1 {
        return 0.0;
    }
    let mut adjustment = 1.0f64;
    let mut rate = 1.0f64;

    for _ in 0..64 {
        let mut health = 1.0f64;
        let mut lowest = 1.0f64;
        let mut break_index = 0usize;

        for (i, &(time, amount)) in gains.iter().enumerate() {
            let mut last = if i > 0 { gains[i - 1].0 } else { start };
            // Two notes either side of a break drain for none of the time
            // between them.
            while break_index < breaks.len() && breaks[break_index].1 <= time {
                last = time;
                break_index += 1;
            }
            health -= (time - last) * rate;
            lowest = lowest.min(health);
            health = (health + amount).min(1.0);
            if lowest < 0.0 {
                break;
            }
        }

        if (lowest - target).abs() <= 0.000_01 {
            break;
        }
        adjustment *= 2.0;
        rate += 1.0 / adjustment * (lowest - target).signum();
    }

    rate
}

// ── the track ────────────────────────────────────────────────────────────

/// The bar over the whole play, as a curve a renderer can read at any instant.
///
/// Sampled at every judged event and at the edges of every break, and straight
/// between them — which is exact rather than an approximation, because the
/// drain is linear in time and only the events and the breaks bend it.
#[derive(Debug, Clone, Default)]
pub struct HealthTrack {
    samples: Vec<(f64, f32)>,
    /// When the bar first reached zero, if it ever did.
    failed_at: Option<f64>,
    drain_rate: f64,
}

impl HealthTrack {
    /// Build the curve for a judged play under the client that recorded it.
    pub fn build(
        judge: &Judge,
        timeline: &Timeline,
        breaks: &[(f64, f64)],
        format_version: u32,
        mods: Mods,
        ruleset: Ruleset,
    ) -> Self {
        // Which model, not which client: lazer's Classic mod restores stable's
        // drain, and a Classic score wants the solved rate rather than the
        // stated one.
        if ruleset.legacy_health() {
            Self::stable(judge, timeline, breaks, format_version, mods)
        } else {
            Self::lazer(judge, timeline, breaks)
        }
    }

    fn stable(
        judge: &Judge,
        timeline: &Timeline,
        breaks: &[(f64, f64)],
        format_version: u32,
        mods: Mods,
    ) -> Self {
        let difficulty = &timeline.difficulty;
        let rates = calibrate(timeline, difficulty, breaks, format_version);
        let Some(first) = timeline.objects.first() else {
            return Self::default();
        };
        let start = first.start_ms - difficulty.preempt_ms();

        // HalfTime slows the clock but not the drain per millisecond, so the
        // game scales it back down; a spinner drains at a quarter rate on
        // maps that ask for it.
        let scale = if mods.contains(bits::HALF_TIME) {
            0.75
        } else {
            1.0
        };

        let mut meter = Meter::full();
        let mut samples = vec![(start, 1.0f32)];
        let mut failed_at = None;
        let mut last = start;
        let combo_ends = combo_end_map(judge, timeline);

        for (index, event) in judge.events().iter().enumerate() {
            drain_between(
                last,
                event.time_ms,
                breaks,
                rates.passive_drain * scale,
                &mut meter,
                &mut samples,
            );
            last = event.time_ms;

            let mut gain = stable_gain(event.part, event.result, difficulty.hp_drain);
            if scaled_by_normal(event.part, event.result) {
                gain *= rates.normal;
            }
            if let Some(end) = combo_ends.get(&index) {
                gain += rates.combo_end * end.value();
            }
            meter.increase(gain);

            if meter.health <= 0.0 && failed_at.is_none() {
                failed_at = Some(event.time_ms);
            }
            samples.push((event.time_ms, (meter.health / MAX_HP) as f32));
        }

        Self {
            samples,
            failed_at,
            drain_rate: rates.passive_drain,
        }
    }

    fn lazer(judge: &Judge, timeline: &Timeline, breaks: &[(f64, f64)]) -> Self {
        let difficulty = &timeline.difficulty;
        let Some(first) = timeline.objects.first() else {
            return Self::default();
        };
        let start = first.start_ms - difficulty.preempt_ms();
        let target = lazer_target_minimum(difficulty.hp_drain);

        // The rate is solved against a *perfect* play, so the gains fed to the
        // search are the ones the map would have handed out, not the ones this
        // player earned.
        let combo_ends = combo_end_map(judge, timeline);
        let perfect: Vec<(f64, f64)> = judge
            .events()
            .iter()
            .enumerate()
            .filter_map(|(index, event)| {
                let gain = lazer_gain(event.part, Judgement::Great, difficulty.hp_drain);
                if gain == 0.0 {
                    return None;
                }
                let bonus = combo_ends
                    .get(&index)
                    .map_or(0.0, |_| lazer_combo_bonus(ComboEnd::Geki));
                Some((event.time_ms, gain + bonus))
            })
            .collect();
        let rate = lazer_drain_rate(&perfect, breaks, start, target);

        let mut health = 1.0f64;
        let mut samples = vec![(start, 1.0f32)];
        let mut failed_at = None;
        let mut last = start;
        let mut break_index = 0usize;

        for (index, event) in judge.events().iter().enumerate() {
            while break_index < breaks.len() && breaks[break_index].1 <= event.time_ms {
                last = event.time_ms;
                break_index += 1;
                samples.push((event.time_ms, health as f32));
            }
            health -= (event.time_ms - last) * rate;
            last = event.time_ms;

            let mut gain = lazer_gain(event.part, event.result, difficulty.hp_drain);
            if let Some(&end) = combo_ends.get(&index) {
                if !event.result.is_miss() {
                    gain += lazer_combo_bonus(end);
                }
            }
            health = (health + gain).min(1.0);

            if health <= 0.0 && failed_at.is_none() {
                failed_at = Some(event.time_ms);
            }
            samples.push((event.time_ms, health.max(0.0) as f32));
        }

        Self {
            samples,
            failed_at,
            drain_rate: rate,
        }
    }

    /// The bar at `time_ms`, from 0 to 1.
    pub fn at(&self, time_ms: f64) -> f32 {
        if self.samples.is_empty() {
            return 1.0;
        }
        let i = self.samples.partition_point(|(t, _)| *t <= time_ms);
        if i == 0 {
            return self.samples[0].1;
        }
        let (t0, v0) = self.samples[i - 1];
        let Some(&(t1, v1)) = self.samples.get(i) else {
            return v0;
        };
        let span = t1 - t0;
        if span <= 0.0 {
            return v1;
        }
        let f = ((time_ms - t0) / span).clamp(0.0, 1.0) as f32;
        v0 + (v1 - v0) * f
    }

    /// When the bar first hit zero, if it did.
    pub fn failed_at(&self) -> Option<f64> {
        self.failed_at
    }

    /// Health lost per millisecond — stable's on the 0..200 scale, lazer's on
    /// 0..1. Kept for the debugger; nothing else should need it.
    pub fn drain_rate(&self) -> f64 {
        self.drain_rate
    }
}

/// Apply the passive drain from `from` to `to`, stopping for breaks and
/// leaving a sample at each edge so the curve bends where it should.
fn drain_between(
    from: f64,
    to: f64,
    breaks: &[(f64, f64)],
    rate: f64,
    meter: &mut Meter,
    samples: &mut Vec<(f64, f32)>,
) {
    let mut cursor = from;
    for &(break_from, break_to) in breaks {
        if break_to <= cursor || break_from >= to {
            continue;
        }
        let edge = break_from.max(cursor);
        meter.increase(-rate * (edge - cursor));
        samples.push((edge, (meter.health / MAX_HP) as f32));
        // Nothing drains inside a break, so the bar is flat across it.
        cursor = break_to.min(to);
        samples.push((cursor, (meter.health / MAX_HP) as f32));
    }
    if to > cursor {
        meter.increase(-rate * (to - cursor));
    }
}

/// Which events end a combo, and how well that combo went.
///
/// Keyed by index into the judge's event list. Only whole objects can end a
/// combo, and only a landed one collects the bonus — breaking on the last note
/// of a combo costs the bonus as well as the note.
fn combo_end_map(judge: &Judge, timeline: &Timeline) -> std::collections::HashMap<usize, ComboEnd> {
    let mut out = std::collections::HashMap::new();
    let mut hundreds = 0usize;
    let mut bad = 0usize;

    for (index, event) in judge.events().iter().enumerate() {
        if !event.part.counts_for_accuracy() {
            continue;
        }
        match event.result {
            Judgement::Ok => hundreds += 1,
            Judgement::Meh | Judgement::Miss => bad += 1,
            Judgement::Great => {}
        }

        let object = event.object_index;
        let ends = object + 1 >= timeline.objects.len()
            || timeline.objects[object + 1].new_combo;
        if !ends {
            continue;
        }
        if !event.result.is_miss() {
            out.insert(
                index,
                if hundreds == 0 && bad == 0 {
                    ComboEnd::Geki
                } else if bad == 0 {
                    ComboEnd::Katu
                } else {
                    ComboEnd::Mu
                },
            );
        }
        hundreds = 0;
        bad = 0;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_gains_that_move_with_hp_move_the_right_way() {
        // A 300 is worth the same on every map; a 50 is worth eight times as
        // much on an HP 0 map as on an HP 5 one, and a miss costs nearly seven
        // times more at HP 10 than at HP 0. That spread is what makes an easy
        // map forgiving — not a gentler drain.
        assert_eq!(stable_gain(Part::Circle, Judgement::Great, 0.0), HP_300);
        assert_eq!(stable_gain(Part::Circle, Judgement::Great, 10.0), HP_300);

        let meh_easy = stable_gain(Part::Circle, Judgement::Meh, 0.0);
        let meh_mid = stable_gain(Part::Circle, Judgement::Meh, 5.0);
        assert!((meh_easy - 8.0 * meh_mid).abs() < 1e-9);
        assert_eq!(meh_mid, stable_gain(Part::Circle, Judgement::Meh, 10.0));

        let miss_easy = stable_gain(Part::Circle, Judgement::Miss, 0.0);
        let miss_hard = stable_gain(Part::Circle, Judgement::Miss, 10.0);
        assert!(miss_hard < miss_easy && miss_easy < 0.0);
    }

    #[test]
    fn a_missed_note_is_not_scaled_by_the_calibration() {
        // The calibration raises `normal` until a perfect play survives. If it
        // scaled the misses too, it would be making bad plays survivable in the
        // same breath — a map that is hard to keep alive on would become one
        // that forgives dropping notes.
        assert!(!scaled_by_normal(Part::Circle, Judgement::Miss));
        assert!(scaled_by_normal(Part::Circle, Judgement::Great));
        // A dropped slider tick is a different thing and is scaled.
        assert!(scaled_by_normal(Part::SliderTick, Judgement::Miss));
    }

    #[test]
    fn the_combo_bonus_outweighs_any_single_hit() {
        // Fourteen against six. It is the combo bonus that keeps a player alive
        // through a hard map, which is why breaking early costs so much more
        // than the note itself.
        assert!(ComboEnd::Geki.value() > HP_300);
        assert!(ComboEnd::Geki.value() > ComboEnd::Katu.value());
        assert!(ComboEnd::Katu.value() > ComboEnd::Mu.value());
    }

    #[test]
    fn lazers_floor_falls_away_as_hp_rises() {
        // At HP 0 a perfect play is barely allowed to dip; at HP 10 it may lose
        // three fifths of the bar and still be within tolerance.
        assert!((lazer_target_minimum(0.0) - 0.99).abs() < 1e-9);
        assert!((lazer_target_minimum(5.0) - 0.9).abs() < 1e-9);
        assert!((lazer_target_minimum(10.0) - 0.4).abs() < 1e-9);
    }
}
