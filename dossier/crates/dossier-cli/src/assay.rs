//! `dossier assay` — what a map demands and what a play on it was worth.
//!
//! The engine's other half, for a caller that wants numbers rather than a
//! video. It answers in JSON because the only caller is the bot.
//!
//! Two questions in one command, because they share nearly all their work: the
//! map's difficulty is most of the cost, and a play's worth is arithmetic on
//! top of it. Ask about a map alone and the score half is left out; hand it a
//! play and both come back.
//!
//! ```text
//! dossier assay --map <path> --mods HDDT
//! dossier assay --map <path> --mods HD --accuracy 98.6 --combo 1420 --misses 1
//! ```
//!
//! # What it replaces
//!
//! The bot has been asking ppy for star ratings one map at a time and a
//! third-party port for everything ppy has no endpoint for. This answers both
//! without the network, and the hypotheticals — what a play would have been
//! worth without the misses, or played perfectly — stop being estimates
//! anchored to an official figure and become the figure.

use std::path::Path;

use dossier_assay::performance::Score;
use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};

/// Read acronyms — `HDDT`, `hd dt`, `HD,DT` — into the engine's bitmask.
///
/// Classic is not among them: the old bitmask never had a bit for it, because
/// it is lazer's name for the rules that bitmask assumed. It arrives as
/// `--classic` instead.
pub fn parse_mods(text: &str) -> Result<Mods, String> {
    let cleaned: String = text
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_ascii_uppercase();
    let mut raw = 0u32;
    for pair in cleaned.as_bytes().chunks(2) {
        let name = std::str::from_utf8(pair).map_err(|_| "mods must be acronyms".to_owned())?;
        raw |= match name {
            "NF" => bits::NO_FAIL,
            "EZ" => bits::EASY,
            "TD" => bits::TOUCH_DEVICE,
            "HD" => bits::HIDDEN,
            "HR" => bits::HARD_ROCK,
            "SD" => bits::SUDDEN_DEATH,
            "DT" => bits::DOUBLE_TIME,
            "RX" => bits::RELAX,
            "HT" => bits::HALF_TIME,
            "NC" => bits::NIGHTCORE | bits::DOUBLE_TIME,
            "FL" => bits::FLASHLIGHT,
            "SO" => bits::SPUN_OUT,
            "AP" => bits::AUTOPILOT,
            "PF" => bits::PERFECT | bits::SUDDEN_DEATH,
            // Not a mod the bitmask can hold, and not an error either — a
            // caller listing what a lazer score wore will include it.
            "CL" | "" => 0,
            other => return Err(format!("no such mod: {other}")),
        };
    }
    Ok(Mods::new(raw))
}

/// A play built from whatever the caller could say about it.
///
/// The judgement counts are what the calculator wants and an accuracy is what a
/// caller often has, so an accuracy alone is turned into counts: the misses are
/// taken as given, and the rest of the map is split between Greats and Hundreds
/// to land on the accuracy asked for. That is the same thing `osu-tools
/// simulate` does when handed `--accuracy`, and it is why a "what would 98% be
/// worth" question has an answer at all.
#[allow(clippy::too_many_arguments)]
pub fn score_from(
    attributes: &dossier_assay::Attributes,
    accuracy: Option<f64>,
    combo: Option<u32>,
    misses: u32,
    n300: Option<u32>,
    n100: Option<u32>,
    n50: Option<u32>,
    slider_ends: Option<u32>,
    large_tick_misses: u32,
    classic: bool,
    legacy_total: Option<u64>,
) -> Score {
    let objects =
        attributes.hit_circle_count + attributes.slider_count + attributes.spinner_count;

    let (great, ok, meh) = match (n300, n100, n50) {
        // Counts given: they are the truth and the accuracy is derived.
        (Some(great), Some(ok), Some(meh)) => (great, ok, meh),
        _ => {
            let target = accuracy.unwrap_or(100.0) / 100.0;
            let judged = objects.saturating_sub(misses);
            let meh = n50.unwrap_or(0);
            // Solve the accuracy formula for how many Hundreds it takes.
            let want = target * 300.0 * f64::from(objects);
            let have = 50.0 * f64::from(meh);
            let hundreds = ((300.0 * f64::from(judged.saturating_sub(meh)) + have - want) / 200.0)
                .round()
                .clamp(0.0, f64::from(judged.saturating_sub(meh)));
            let ok = hundreds as u32;
            (judged.saturating_sub(meh).saturating_sub(ok), ok, meh)
        }
    };

    let mut score = Score {
        max_combo: combo.unwrap_or(attributes.max_combo),
        great,
        ok,
        meh,
        miss: misses,
        // Every end caught unless told otherwise, which is what a play with no
        // dropped ends looks like and what a caller who cannot say means.
        slider_tail_hit: slider_ends.unwrap_or(attributes.slider_count),
        large_tick_miss: large_tick_misses,
        classic,
        legacy_total_score: legacy_total,
        accuracy: None,
    };
    // The caller's accuracy is believed when there is one, and only worked out
    // from the counts when there is not.
    //
    // Believed because under lazer's rules it is not derivable from the four
    // judgements — slider tails and large ticks count towards it — so a figure
    // computed here would be the *old* accuracy wearing the new name. Every
    // score the bot has comes with the game's own, which is the right one; and
    // a caller asking "what would 98% be worth" means 98%.
    score.accuracy = match accuracy {
        Some(percent) => Some((percent / 100.0).clamp(0.0, 1.0)),
        None => Some(score.accuracy()),
    };
    score
}

/// The whole answer for one map, and one play if there is one.
pub fn run(
    map_path: &Path,
    mods: Mods,
    play: Option<Score>,
) -> Result<String, String> {
    let text = std::fs::read_to_string(map_path)
        .map_err(|error| format!("could not read {}: {error}", map_path.display()))?;
    let map = Beatmap::parse(&text).map_err(|error| format!("could not parse the map: {error}"))?;
    let attributes = dossier_assay::attributes(&map, mods);

    let mut out = String::from("{\n");
    out.push_str(&format!("  \"star_rating\": {},\n", attributes.star_rating));
    out.push_str(&format!("  \"max_combo\": {},\n", attributes.max_combo));
    out.push_str(&format!("  \"aim_difficulty\": {},\n", attributes.aim_difficulty));
    out.push_str(&format!("  \"speed_difficulty\": {},\n", attributes.speed_difficulty));
    out.push_str(&format!("  \"reading_difficulty\": {},\n", attributes.reading_difficulty));
    out.push_str(&format!("  \"flashlight_difficulty\": {},\n", attributes.flashlight_difficulty));
    out.push_str(&format!("  \"slider_factor\": {},\n", attributes.slider_factor));
    out.push_str(&format!("  \"hit_circle_count\": {},\n", attributes.hit_circle_count));
    out.push_str(&format!("  \"slider_count\": {},\n", attributes.slider_count));
    out.push_str(&format!("  \"spinner_count\": {}", attributes.spinner_count));

    if let Some(play) = play {
        let performance = dossier_assay::performance::performance(&play, &attributes, mods);
        // What the same play would have been worth unbroken, and played
        // perfectly — the two figures the bot shows beside the real one, and
        // the reason this crate exists rather than a lookup.
        let objects =
            attributes.hit_circle_count + attributes.slider_count + attributes.spinner_count;
        let mut unbroken = play.clone();
        unbroken.great += unbroken.miss;
        unbroken.miss = 0;
        unbroken.max_combo = attributes.max_combo;
        unbroken.large_tick_miss = 0;
        unbroken.slider_tail_hit = attributes.slider_count;
        unbroken.accuracy = None;
        unbroken.accuracy = Some(unbroken.accuracy());
        let if_unbroken = dossier_assay::performance::performance(&unbroken, &attributes, mods);

        let perfect = Score {
            max_combo: attributes.max_combo,
            great: objects,
            ok: 0,
            meh: 0,
            miss: 0,
            slider_tail_hit: attributes.slider_count,
            large_tick_miss: 0,
            classic: play.classic,
            legacy_total_score: None,
            accuracy: Some(1.0),
        };
        let if_perfect = dossier_assay::performance::performance(&perfect, &attributes, mods);

        out.push_str(",\n");
        out.push_str(&format!("  \"pp\": {},\n", performance.pp));
        out.push_str(&format!("  \"pp_if_unbroken\": {},\n", if_unbroken.pp));
        out.push_str(&format!("  \"pp_if_perfect\": {},\n", if_perfect.pp));
        out.push_str(&format!("  \"accuracy\": {},\n", play.accuracy() * 100.0));
        out.push_str(&format!("  \"aim\": {},\n", performance.aim));
        out.push_str(&format!("  \"speed\": {},\n", performance.speed));
        out.push_str(&format!("  \"accuracy_value\": {},\n", performance.accuracy));
        out.push_str(&format!("  \"reading\": {},\n", performance.reading));
        out.push_str(&format!("  \"flashlight\": {},\n", performance.flashlight));
        out.push_str(&format!(
            "  \"effective_miss_count\": {}",
            performance.effective_miss_count
        ));
    }
    out.push_str("\n}\n");
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mods_are_read_however_they_are_written() {
        // The bot has them three ways depending on where they came from: joined
        // by the API, comma-joined by a row, bare from a card.
        for text in ["HDDT", "hddt", "HD,DT", "hd dt"] {
            let mods = parse_mods(text).expect(text);
            assert!(mods.contains(bits::HIDDEN) && mods.contains(bits::DOUBLE_TIME), "{text}");
        }
    }

    #[test]
    fn classic_is_accepted_and_carries_no_bit() {
        // It is lazer's name for the rules the old bitmask assumed, so there is
        // no bit for it — but a caller listing a lazer score's mods will send
        // it, and refusing would fail the commonest case there is.
        let mods = parse_mods("HDCL").expect("HDCL");
        assert!(mods.contains(bits::HIDDEN));
        assert_eq!(parse_mods("CL").expect("CL").raw(), 0);
    }

    #[test]
    fn a_mod_nobody_has_is_refused_rather_than_ignored() {
        assert!(parse_mods("ZZ").is_err());
    }

    fn attributes() -> dossier_assay::Attributes {
        dossier_assay::Attributes {
            max_combo: 1000,
            hit_circle_count: 600,
            slider_count: 300,
            spinner_count: 4,
            ..Default::default()
        }
    }

    #[test]
    fn an_accuracy_alone_is_turned_into_the_judgements_that_produce_it() {
        // What a caller asking "what would 98% be worth" has, and what the
        // calculator needs instead.
        let score = score_from(&attributes(), Some(98.0), None, 0, None, None, None, None, 0,
                               false, None);
        let objects = 904;
        assert_eq!(score.great + score.ok + score.meh + score.miss, objects);
        // Solved rather than guessed: the counts really do give the accuracy.
        let from_counts = (300.0 * f64::from(score.great) + 100.0 * f64::from(score.ok))
            / (300.0 * f64::from(objects));
        assert!((from_counts - 0.98).abs() < 0.001, "{from_counts}");
    }

    #[test]
    fn the_accuracy_a_caller_gives_is_the_one_used() {
        // Under lazer's rules accuracy is not derivable from the four
        // judgements — slider tails and large ticks count towards it — so a
        // figure worked out here would be the old accuracy wearing the new
        // name. The game's own is what every score the bot has carries.
        let score = score_from(&attributes(), Some(99.5), None, 1, Some(800), Some(100),
                               Some(3), None, 0, false, None);
        assert!((score.accuracy() - 0.995).abs() < 1e-12);
        assert_eq!(score.great, 800, "the counts are still the caller's");
    }

    #[test]
    fn a_play_that_said_nothing_about_slider_ends_is_assumed_to_have_caught_them() {
        // Which is what a play with none dropped looks like, and the only
        // honest reading of a caller that cannot say.
        let score = score_from(&attributes(), Some(97.0), None, 0, None, None, None, None, 0,
                               false, None);
        assert_eq!(score.slider_tail_hit, 300);
    }
}
