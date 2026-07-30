//! The score, in both clients' arithmetic.
//!
//! The two are not variations on a theme. stable's grows without bound — every
//! hit is multiplied by the combo carried into it — so a long map is worth
//! hundreds of millions and a short one a few. lazer's is normalised to a
//! million whatever the map, split between the combo reached and the accuracy
//! held, so two plays on different maps can be compared.
//!
//! Both are checkable, which is why they are computed here rather than
//! approximated: the `.osr` header carries the score the client itself arrived
//! at, so every replay in the corpus is its own test.

use dossier_beatmap::{Beatmap, Difficulty};
use dossier_replay::{bits, Mods};

use crate::judge::{window_judgement, Event, Judge, Judgement, Part};
use crate::ruleset::{Client, Ruleset};

// ── stable: ScoreV1 ──────────────────────────────────────────────────────

/// What one judged part is worth before the combo multiplier.
///
/// stable pays for the pieces of a slider as they pass — ten for a tick, thirty
/// for a head, repeat or end — and then for the slider as a whole when its
/// verdict is known.
/// Whether a part is paid the combo multiplier at all.
///
/// Only whole objects are. The pieces of a slider score their flat ten or
/// thirty and nothing more, however long the combo — which is the difference
/// between a slider being worth a little more than a circle and its being worth
/// several times as much. Leaving them multiplied put every score in the corpus
/// four to eight per cent over.
fn takes_combo_multiplier(part: Part) -> bool {
    matches!(part, Part::Circle | Part::Slider | Part::Spinner)
}

fn stable_base_value(part: Part, result: Judgement) -> u32 {
    match part {
        Part::SliderTick => 10,
        // `SpinnerPoints` and `SpinnerBonus` in danser's table. Flat, and never
        // multiplied by the combo — a spinner at 900x is worth what it is worth
        // at 1x, which is why a long spinner cannot carry a score the way a
        // long stream can.
        Part::SpinnerSpin => 0,
        Part::SpinnerPoints => 100,
        Part::SpinnerBonus => 1100,
        Part::SliderRepeat | Part::SliderTail | Part::SliderHead => 30,
        Part::Circle | Part::Slider | Part::Spinner => match result {
            Judgement::Great => 300,
            Judgement::Ok => 100,
            Judgement::Meh => 50,
            Judgement::Miss => 0,
        },
    }
}

/// What a part is worth under ScoreV2.
///
/// `ScoreValueV2` is `ScoreValue` with one substitution — a spinner bonus drops
/// from 1100 to 500 — and spinner bonuses are not modelled here at all, so the
/// table is the same one ScoreV1 uses. It is written out separately anyway,
/// because the two are the same by coincidence rather than by construction and
/// a shared function would hide the day they stop being.
///
/// Unlike ScoreV1's, this one pays nothing for a piece that did not land: a
/// dropped tick is worth zero, not ten.
fn v2_value(part: Part, result: Judgement) -> u32 {
    if result.is_miss() {
        return 0;
    }
    match part {
        Part::SliderTick => 10,
        Part::SpinnerSpin => 0,
        Part::SpinnerPoints => 100,
        // The one substitution ScoreV2 makes in the table: eleven hundred down
        // to five. Bonus is worth less when the rest of the score is bounded.
        Part::SpinnerBonus => 500,
        Part::SliderHead | Part::SliderRepeat | Part::SliderTail => 30,
        Part::Circle | Part::Slider | Part::Spinner => result.value(),
    }
}

/// C#'s `Math.Round`: halves go to the *even* neighbour, not away from zero.
///
/// Not a detail. The difficulty multiplier is a small integer, so one step of
/// it is twenty per cent of the score, and two maps in the corpus land on
/// exactly 4.5 — where Rust's `round` gives 5 and the game gives 4. Both were
/// thirty per cent over until this existed.
fn round_half_to_even(x: f64) -> f64 {
    let rounded = x.round();
    if (x - x.trunc()).abs() == 0.5 && rounded % 2.0 != 0.0 {
        rounded - x.signum()
    } else {
        rounded
    }
}

/// stable's difficulty multiplier, rounded to a whole number.
///
/// ```text
/// round((HP + OD + CS + clamp(objects / drainSeconds * 8, 0, 16)) / 38 * 5)
/// ```
///
/// The fourth term is density: how thick the map is with notes. It is clamped
/// at 16, so past a certain rate a map stops being worth more for being busier
/// — which is why nearly every map anyone plays sits at the clamp and the term
/// does no work at all.
///
/// `drainSeconds` is the playable span: the map's length with its breaks taken
/// out, since a break is time in which nothing can be scored. The stats are the
/// map's own, before mods — HardRock does not make the multiplier bigger, it
/// gets its bonus from the mod multiplier instead.
pub fn difficulty_multiplier(beatmap: &Beatmap, object_count: usize, drain_seconds: f64) -> u32 {
    let d = &beatmap.difficulty;
    let density = if drain_seconds > 0.0 {
        (object_count as f64 / drain_seconds * 8.0).clamp(0.0, 16.0)
    } else {
        0.0
    };
    let raw = (d.hp_drain + d.overall_difficulty + d.circle_size + density) / 38.0 * 5.0;
    round_half_to_even(raw) as u32
}

/// The mods' own multiplier on stable's score.
///
/// Easier mods take it down and harder ones put it up, which is why a NoFail
/// score is worth half: the play could not be lost.
pub fn stable_mod_multiplier(mods: Mods) -> f64 {
    // Relax and Autopilot play part of the game for you, and score nothing at
    // all — checked first because nothing else can lift them off zero.
    if mods.contains(bits::RELAX) || mods.contains(bits::AUTOPILOT) {
        return 0.0;
    }
    // ScoreV2 does not merely rescale ScoreV1 — it rebalances three of the mod
    // multipliers, and one of them by a factor of two.
    //
    // ```go
    // if mods&NoFail > 0 && mods&ScoreV2 == 0 { multiplier *= 0.5 }
    // if mods&HardRock > 0 { if mods&ScoreV2 > 0 { *= 1.10 } else { *= 1.06 } }
    // if mods&DoubleTime > 0 { if mods&ScoreV2 > 0 { *= 1.20 } else { *= 1.12 } }
    // ```
    //
    // NoFail is the one that matters most and reads oddest: under ScoreV2 it
    // costs nothing at all. That makes sense of the mod — V2 is meant to score
    // a play on how it was played, and refusing to die is not a way of playing
    // better. Missing it put a NoFail score exactly half where it belonged,
    // which is the sort of error that looks like a broken formula rather than a
    // missing condition.
    let v2 = mods.contains(bits::SCORE_V2);
    let mut m = 1.0;
    for (bit, factor) in [
        (bits::NO_FAIL, if v2 { 1.0 } else { 0.5 }),
        (bits::EASY, 0.5),
        (bits::HALF_TIME, 0.3),
        (bits::HARD_ROCK, if v2 { 1.10 } else { 1.06 }),
        (bits::HIDDEN, 1.06),
        (bits::FLASHLIGHT, 1.12),
        (bits::SPUN_OUT, 0.9),
    ] {
        if mods.contains(bit) {
            m *= factor;
        }
    }
    // NC sets DT as well on stable, so testing both would square the bonus.
    if mods.contains(bits::DOUBLE_TIME) || mods.contains(bits::NIGHTCORE) {
        m *= if v2 { 1.20 } else { 1.12 };
    }
    m
}

/// The playable span in seconds: first object to last, less the breaks.
pub fn drain_seconds(beatmap: &Beatmap) -> f64 {
    let (Some(first), Some(last)) = (beatmap.objects.first(), beatmap.objects.last()) else {
        return 0.0;
    };
    let span = last.time_ms - first.time_ms;
    let breaks: f64 = beatmap
        .breaks
        .iter()
        .map(|&(from, to)| (to - from).max(0.0))
        .sum();
    ((span - breaks) / 1000.0).max(0.0)
}

/// The two halves of a ScoreV1, with the difficulty multiplier left out of the
/// combo half.
///
/// Only useful for working backwards: given the score the client recorded,
/// `(theirs - flat) / combo_units` is the multiplier it must have been using,
/// and if that comes out a clean integer we have found which one rather than
/// guessed it.
pub fn stable_halves(judge: &Judge) -> (f64, f64) {
    let mut flat = 0f64;
    let mut combo_units = 0f64;
    let mut combo = 0u32;
    for event in judge.events() {
        let value = f64::from(stable_base_value(event.part, event.result));
        if value > 0.0 {
            flat += value;
            if takes_combo_multiplier(event.part) {
                combo_units += value * f64::from(combo.saturating_sub(1)) / 25.0;
            }
        }
        combo = event.combo_after;
    }
    (flat, combo_units)
}

// ── lazer: standardised ──────────────────────────────────────────────────

/// What a part is worth in lazer's own judgement set, at best.
///
/// Not the same list as stable's. lazer made the slider's end a judgement in
/// its own right worth 150, and its ticks worth 30, because it scores the
/// slider from its pieces rather than summarising it — which is why
/// [`Part::Slider`], our summary, is worth nothing here and must not be
/// counted twice.
fn lazer_max_value(part: Part) -> f64 {
    match part {
        Part::Circle | Part::Spinner | Part::SliderHead => 300.0,
        Part::SliderTail => 150.0,
        Part::SliderTick | Part::SliderRepeat => 30.0,
        Part::Slider => 0.0,
        // Bonus is outside the standardised million on lazer just as it is on
        // ScoreV2, so it contributes nothing to the maximum the two halves are
        // measured against.
        Part::SpinnerSpin | Part::SpinnerPoints | Part::SpinnerBonus => 0.0,
    }
}

/// What it was actually worth, given how it was judged.
///
/// The head is the awkward one. Our judge records it as hit or not, because
/// that is all stable asks of it — a flat thirty points either way. lazer asks
/// more: the head is an ordinary circle there, judged on the ordinary windows,
/// so a head sixty milliseconds late is worth a hundred and not three hundred.
/// The verdict is recovered from the timing error the judge kept.
fn lazer_value(event: &Event, difficulty: &Difficulty) -> f64 {
    match event.part {
        Part::Slider => 0.0,
        // Counted in the bonus term, not here — see `lazer_max_value`.
        Part::SpinnerSpin | Part::SpinnerPoints | Part::SpinnerBonus => 0.0,
        Part::SliderHead => match event.error_ms {
            Some(error) => tiered(window_judgement(error, difficulty)),
            None => 0.0,
        },
        // The rest of a slider is caught or dropped; there is no partial
        // credit for a tick.
        Part::SliderTail | Part::SliderTick | Part::SliderRepeat => {
            if event.result.is_miss() {
                0.0
            } else {
                lazer_max_value(event.part)
            }
        }
        Part::Circle | Part::Spinner => tiered(event.result),
    }
}

fn tiered(result: Judgement) -> f64 {
    match result {
        Judgement::Great => 300.0,
        Judgement::Ok => 100.0,
        Judgement::Meh => 50.0,
        Judgement::Miss => 0.0,
    }
}

/// How much a hit's combo weighs in the combo half of lazer's score.
///
/// The square root is the whole design: it makes an early break cost more than
/// a late one without making the last note of a long map worth a hundred of the
/// first, which is stable's behaviour and the reason its scores are unreadable
/// across maps.
const COMBO_EXPONENT: f64 = 0.5;

// ── the track ────────────────────────────────────────────────────────────

/// Every score the play passed through, so a frame can be given the number as
/// of its own instant.
///
/// Computed once. It is a fold over the event list, and doing it per frame
/// would walk a hundred thousand events a hundred thousand times.
#[derive(Debug, Clone, Default)]
pub struct ScoreTrack {
    points: Vec<(f64, u64)>,
    ruleset: Option<Ruleset>,
    comparable: bool,
}

impl ScoreTrack {
    /// Build the track for a judged play, in the arithmetic of the client that
    /// recorded it.
    pub fn build(judge: &Judge, beatmap: &Beatmap, mods: Mods, ruleset: Ruleset) -> Self {
        Self::build_with(judge, beatmap, mods, &mods.as_lazer_mods(), ruleset)
    }

    /// The same, given the mods as lazer itself recorded them.
    ///
    /// Half of lazer's multipliers now depend on a mod's settings, and the
    /// legacy bitmask cannot carry a setting — nor Classic, nor Blinds, nor
    /// anything else without a bit. Where the replay brought the real list,
    /// this is the one to use.
    pub fn build_with(
        judge: &Judge,
        beatmap: &Beatmap,
        mods: Mods,
        lazer_mods: &[dossier_replay::LazerMod],
        ruleset: Ruleset,
    ) -> Self {
        Self::build_for(judge, beatmap, mods, lazer_mods, None, usize::MAX, ruleset)
    }

    /// The same again, given the replay's own account of what the mods were
    /// worth.
    ///
    /// lazer records the total *before* the mods multiplied it, which turns
    /// the multiplier from a lookup into a division — and the lookup is the
    /// part that has been rebalanced under us. Where the replay says, it is
    /// read; where it does not, the table for its generation is used.
    pub fn build_for(
        judge: &Judge,
        beatmap: &Beatmap,
        mods: Mods,
        lazer_mods: &[dossier_replay::LazerMod],
        recorded_multiplier: Option<f64>,
        played: usize,
        ruleset: Ruleset,
    ) -> Self {
        // The score follows the client outright. Classic changes what a
        // slider is judged as, which reaches the score through the judgement,
        // but it does not turn lazer's standardised million back into
        // ScoreV1 — a Classic score is still a lazer score.
        let score_v2 = mods.contains(dossier_replay::bits::SCORE_V2);
        let mut track = match ruleset.client() {
            Client::Stable if score_v2 => Self::stable_v2(judge, mods, played),
            Client::Stable => Self::stable(judge, beatmap, mods, played),
            Client::Lazer => {
                Self::lazer(judge, beatmap, lazer_mods, recorded_multiplier, played, ruleset)
            }
        };
        track.ruleset = Some(ruleset);
        track
    }

    /// stable's ScoreV2 — a millionth-scale score with no map difficulty in it.
    ///
    /// `scoreV2Processor`, as danser implements it:
    ///
    /// ```go
    /// s.comboPart += float64(scoreValue) * (1 + float64(s.combo)/10)
    /// acc = float32(s.rawScore) / float32(s.hits*300)
    /// s.score = int64(math.Round((s.comboPart/s.comboPartMax*700000 +
    ///     math.Pow(float64(acc), 10)*(float64(s.hits)/float64(s.maxHits))*300000 +
    ///     s.bonus) * s.modMultiplier))
    /// ```
    ///
    /// Three parts, and none of them is ScoreV1. Seven hundred thousand for
    /// combo, three hundred thousand for accuracy raised to the tenth, and
    /// bonus on top — so the map's own difficulty multiplier, which is the
    /// whole of ScoreV1's spread, does not appear at all. That is the point of
    /// the mod: two plays of the same quality on different maps score the same.
    ///
    /// The combo term is the interesting one. Each result is worth its face
    /// value times `1 + combo/10`, with the combo read *after* it has been
    /// incremented — the opposite of ScoreV1, where it is read before. And the
    /// denominator is the same sum over a play that never breaks, so the term
    /// is a ratio: what the combo was worth against what it could have been.
    ///
    /// The accuracy is computed in `float32` before being raised to the tenth,
    /// and that is not incidental. At the tenth power a difference in the
    /// seventh decimal of the accuracy moves the score by a point or two, so
    /// the width of the float decides the last digits.
    fn stable_v2(judge: &Judge, mods: Mods, played: usize) -> Self {
        // What a flawless play of this map would have been worth. Both halves
        // come from the same walk, because a maximum assembled by any other
        // route than the one the score uses is a maximum that can disagree
        // with it.
        let (mut combo_part_max, mut max_hits) = (0.0f64, 0u32);
        let mut perfect_combo = 0u32;
        for event in judge.events() {
            let value = f64::from(v2_value(event.part, Judgement::Great));
            if event.part.adds_combo() {
                perfect_combo += 1;
            }
            combo_part_max += value * (1.0 + f64::from(perfect_combo) / 10.0);
            if event.part.counts_for_accuracy() {
                max_hits += 1;
            }
        }
        if combo_part_max <= 0.0 || max_hits == 0 {
            return Self {
                points: Vec::new(),
                ruleset: None,
                comparable: true,
            };
        }

        let multiplier = stable_mod_multiplier(mods);
        let (mut combo_part, mut raw_score, mut hits) = (0.0f64, 0u32, 0u32);
        let mut bonus = 0.0f64;
        let mut points = Vec::with_capacity(judge.events().len());
        for event in judge.events() {
            // A play that ended stopped scoring there, exactly as in ScoreV1.
            if event.object_index >= played {
                break;
            }
            if event.part.is_bonus() {
                bonus += f64::from(v2_value(event.part, event.result));
            } else {
                combo_part += f64::from(v2_value(event.part, event.result))
                    * (1.0 + f64::from(event.combo_after) / 10.0);
            }
            if event.part.counts_for_accuracy() {
                raw_score += event.result.value();
                hits += 1;
            }
            // `float32` on the way in, because that is the width the game does
            // the tenth power at.
            let accuracy = if hits > 0 {
                raw_score as f32 / (hits * 300) as f32
            } else {
                1.0
            };
            let total = (combo_part / combo_part_max * 700_000.0
                + f64::from(accuracy).powi(10) * f64::from(hits) / f64::from(max_hits) * 300_000.0
                + bonus)
                * multiplier;
            points.push((event.time_ms, total.round().max(0.0) as u64));
        }
        Self {
            points,
            ruleset: None,
            comparable: true,
        }
    }

    /// stable's ScoreV1.
    ///
    /// ```text
    /// accuracyScore += value
    /// comboScore    += max(0, combo - 1) * (value / 25 * multiplier)
    /// combo++
    /// ```
    ///
    /// `combo` is read *before* the hit adds to it, so the first two objects of
    /// a map are worth their face value and nothing else. The same play scored
    /// back to front is worth a fraction as much, because the multiplier is
    /// still small while most of the notes are going past — which is the whole
    /// character of ScoreV1 and why an early break hurts so much more than a
    /// late one.
    ///
    /// Truncated per hit, not at the end: stable casts each combo term to an
    /// integer as it goes, and over several thousand objects the discarded
    /// fractions add up.
    ///
    /// One thing is still missing — spinner spins, worth a flat 100 each and
    /// 1100 for a bonus spin. They take no combo multiplier, so on maps with a
    /// spinner our number comes in a little under; on maps without one, which
    /// is most of the corpus, it makes no difference.
    fn stable(judge: &Judge, beatmap: &Beatmap, mods: Mods, played: usize) -> Self {
        let multiplier = f64::from(difficulty_multiplier(
            beatmap,
            beatmap.objects.len(),
            drain_seconds(beatmap),
        )) * stable_mod_multiplier(mods);

        let mut total = 0u64;
        let mut combo = 0u32;
        let mut points = Vec::with_capacity(judge.events().len());
        for event in judge.events() {
            // A play that ended stopped scoring there. The judge walks the
            // whole map and calls everything past the death a miss; counting
            // those is inventing a play that did not happen.
            if event.object_index >= played {
                break;
            }
            let value = f64::from(stable_base_value(event.part, event.result));
            if value > 0.0 {
                total += value as u64;
                if takes_combo_multiplier(event.part) {
                    let carried = f64::from(combo.saturating_sub(1));
                    total += (carried * (value / 25.0 * multiplier)) as u64;
                }
            }
            combo = event.combo_after;
            points.push((event.time_ms, total));
        }
        Self {
            points,
            ruleset: None,
            comparable: true,
        }
    }

    /// lazer's standardised score.
    ///
    /// ```text
    /// 500000 * accuracy * comboProgress
    ///   + 500000 * accuracy^5 * accuracyProgress
    ///   + bonus
    /// ```
    ///
    /// then multiplied by the mods. Half the million is combo and half is
    /// accuracy, and note that the combo half is scaled by accuracy too — so a
    /// full combo played sloppily still loses on both terms. That is the
    /// deliberate difference from ScoreV1, where combo was nearly everything.
    ///
    /// `comboProgress` is the combo weight earned over the most that was
    /// available, where a hit's weight is its value times the square root of
    /// the combo it landed on. The square root is what makes an early break
    /// cost more than a late one without making the last note of a long map
    /// worth a hundred of the first.
    ///
    /// `accuracyProgress` is a plain count: judgements made over judgements
    /// available. It is what makes the number climb steadily from zero rather
    /// than jumping to its final value at the first note.
    fn lazer(
        judge: &Judge,
        beatmap: &Beatmap,
        mods: &[dossier_replay::LazerMod],
        recorded_multiplier: Option<f64>,
        played: usize,
        ruleset: Ruleset,
    ) -> Self {
        let difficulty = &beatmap.difficulty;
        let multiplier = recorded_multiplier.unwrap_or_else(|| {
            crate::multiplier::lazer_multiplier(ruleset.multipliers(), mods, difficulty)
        });

        // What these same events would have been worth played perfectly.
        // Taken from the event list rather than from the beatmap: a maximum
        // counted one way and a running total counted another would disagree
        // wherever the two see a different number of slider ticks.
        let mut best_combo = 0u32;
        let mut max_combo_portion = 0f64;
        let mut judgements = 0f64;
        for event in judge.events() {
            let max = lazer_max_value(event.part);
            if max == 0.0 {
                continue;
            }
            if event.part.adds_combo() {
                best_combo += 1;
            }
            max_combo_portion += max * f64::from(best_combo).powf(COMBO_EXPONENT);
            judgements += 1.0;
        }

        let mut points = Vec::with_capacity(judge.events().len());
        let mut combo_portion = 0f64;
        let mut base = 0f64;
        let mut reached_base = 0f64;
        let mut made = 0f64;
        for event in judge.events() {
            // Past the death the numerator stops, and the denominator does
            // not: lazer measures a failed play against the whole map, which
            // is why dying a third of the way in is worth far less than a
            // third of the score.
            if event.object_index >= played {
                break;
            }
            let max = lazer_max_value(event.part);
            if max > 0.0 {
                // The combo half is weighted by what the object was worth *at
                // best*, not by what was got for it:
                //
                // ```csharp
                // GetBaseScoreForResult(result.Judgement.MaxResult)
                //     * Math.Pow(result.ComboAfterJudgement, COMBO_EXPONENT)
                // ```
                //
                // A hundred still carries its full three hundred here, because
                // this half is about the combo and the accuracy is applied to
                // it separately in the total. A miss needs no special case: it
                // leaves the combo at zero, and the root of zero is zero.
                combo_portion += max * f64::from(event.combo_after).powf(COMBO_EXPONENT);
                base += lazer_value(event, difficulty);
                reached_base += max;
                made += 1.0;
            }
            let accuracy = if reached_base > 0.0 {
                base / reached_base
            } else {
                1.0
            };
            let combo_progress = if max_combo_portion > 0.0 {
                combo_portion / max_combo_portion
            } else {
                1.0
            };
            let accuracy_progress = if judgements > 0.0 {
                made / judgements
            } else {
                1.0
            };
            let total = (500_000.0 * accuracy * combo_progress
                + 500_000.0 * accuracy.powi(5) * accuracy_progress)
                * multiplier;
            points.push((event.time_ms, total.round() as u64));
        }
        Self {
            points,
            ruleset: None,
            comparable: true,
        }
    }

    /// Score as of `time_ms`, counting everything judged at or before it.
    pub fn at(&self, time_ms: f64) -> u64 {
        let i = self.points.partition_point(|(t, _)| *t <= time_ms);
        if i == 0 {
            0
        } else {
            self.points[i - 1].1
        }
    }

    /// The score the play finished on.
    pub fn total(&self) -> u64 {
        self.points.last().map_or(0, |(_, v)| *v)
    }

    /// Whether the number can honestly be held up against the replay's own.
    ///
    /// stable's ScoreV2 mod replaces ScoreV1 with a millionth-scale formula of
    /// its own, and that is not implemented here — so a replay carrying it
    /// gets a ScoreV1 total, which is the right thing to *draw* and the wrong
    /// thing to compare. One unimplemented mode reading as a 754% error would
    /// swamp the statistic it appears in.
    pub fn comparable(&self) -> bool {
        self.comparable
    }

    /// Which client's arithmetic this was built in, if it was built at all.
    pub fn ruleset(&self) -> Option<Ruleset> {
        self.ruleset
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(body: &str) -> Beatmap {
        Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("parses")
    }

    #[test]
    fn halves_round_to_the_even_neighbour_in_both_directions() {
        // "Round half to even" is not "round half down", and a test with only
        // the 4.5 case — the one the corpus threw up — would pass under either
        // reading. Everything that is not a half rounds normally.
        assert_eq!(round_half_to_even(4.5), 4.0);
        assert_eq!(round_half_to_even(5.5), 6.0);
        assert_eq!(round_half_to_even(2.5), 2.0);
        assert_eq!(round_half_to_even(3.5), 4.0);
        assert_eq!(round_half_to_even(4.4), 4.0);
        assert_eq!(round_half_to_even(4.6), 5.0);
    }

    #[test]
    fn the_difficulty_multiplier_follows_the_documented_formula() {
        let m = map("[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n[HitObjects]\n0,0,1000,1,0\n0,0,2000,1,0\n");
        // HP 5 + OD 5 + CS 5 = 15. Two objects over one second of drain is a
        // density of 16 once clamped: (15 + 16) / 38 * 5 = 4.08, rounding to 4.
        assert_eq!(difficulty_multiplier(&m, 2, 1.0), 4);
        // With no objects there is no density term: 15 / 38 * 5 = 1.97 → 2.
        assert_eq!(difficulty_multiplier(&m, 0, 1.0), 2);
    }

    #[test]
    fn density_is_clamped_so_a_stream_map_is_not_worth_double() {
        // Twenty notes a second and two hundred a second land on the same
        // multiplier. Without the clamp a burst map would outscore everything.
        let m = map("[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n[HitObjects]\n0,0,1000,1,0\n");
        assert_eq!(
            difficulty_multiplier(&m, 100, 1.0),
            difficulty_multiplier(&m, 10_000, 1.0)
        );
    }

    #[test]
    fn breaks_come_out_of_the_drain_length() {
        // A break is time in which nothing can be scored, so it does not dilute
        // how dense the map is.
        let plain = map("[HitObjects]\n0,0,1000,1,0\n0,0,11000,1,0\n");
        assert!((drain_seconds(&plain) - 10.0).abs() < 1e-9);

        let with_break = map("[Events]\n2,3000,7000\n\n[HitObjects]\n0,0,1000,1,0\n0,0,11000,1,0\n");
        assert!(
            (drain_seconds(&with_break) - 6.0).abs() < 1e-9,
            "{}",
            drain_seconds(&with_break)
        );
    }

    #[test]
    fn the_mods_scale_stables_score() {
        assert!((stable_mod_multiplier(Mods::new(0)) - 1.0).abs() < 1e-9);
        assert!((stable_mod_multiplier(Mods::new(bits::NO_FAIL)) - 0.5).abs() < 1e-9);
        // Compounding, not the largest one winning.
        let hdhr = stable_mod_multiplier(Mods::new(bits::HIDDEN | bits::HARD_ROCK));
        assert!((hdhr - 1.06 * 1.06).abs() < 1e-9, "{hdhr}");
        // Nightcore sets the DoubleTime bit too, and must not be paid twice.
        let nc = stable_mod_multiplier(Mods::new(bits::NIGHTCORE | bits::DOUBLE_TIME));
        assert!((nc - 1.12).abs() < 1e-9, "{nc}");
        // Relax scores nothing whatever else is on.
        assert_eq!(
            stable_mod_multiplier(Mods::new(bits::RELAX | bits::HIDDEN)),
            0.0
        );
    }

    #[test]
    fn the_slider_summary_is_not_paid_twice_in_lazer() {
        // Our judge emits a `Part::Slider` carrying the legacy verdict for the
        // whole slider. lazer has no such judgement — it scores the head, the
        // ticks and the tail — so counting ours would inflate every slider map.
        assert_eq!(lazer_max_value(Part::Slider), 0.0);
    }
}
