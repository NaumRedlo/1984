//! Ours against ppy's, on the corpus in `corpus/`.
//!
//! Every figure this crate produces is checked here rather than against a
//! number somebody wrote down once. `corpus/expected.json` is ppy's own answer
//! from the attributes endpoint — ten maps, fifteen mod sets each — and
//! `corpus/maps/` holds the maps those answers describe.
//!
//! Rebuild it with `python scripts/pp_corpus.py`. A diff on the corpus after a
//! rebuild is ppy having changed their arithmetic, which is the other half of
//! why it lives in the repository.

use std::collections::BTreeMap;
use std::path::PathBuf;

use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};

fn corpus_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("corpus")
}

/// The mods a corpus key names, as the engine's bitmask.
///
/// The keys are what the endpoint was asked for — `NM`, `HD`, `HDHR` — so this
/// is the same two-letter reading the rest of the project does, and `NC` brings
/// DoubleTime with it the way the game's own bitmask does.
fn mods_of(key: &str) -> Option<Mods> {
    let mut raw = 0u32;
    if key == "NM" {
        return Some(Mods::new(0));
    }
    for pair in key.as_bytes().chunks(2) {
        raw |= match std::str::from_utf8(pair).ok()? {
            "NF" => bits::NO_FAIL,
            "EZ" => bits::EASY,
            "TD" => bits::TOUCH_DEVICE,
            "HD" => bits::HIDDEN,
            "HR" => bits::HARD_ROCK,
            "DT" => bits::DOUBLE_TIME,
            "HT" => bits::HALF_TIME,
            "NC" => bits::NIGHTCORE | bits::DOUBLE_TIME,
            "FL" => bits::FLASHLIGHT,
            _ => return None,
        };
    }
    Some(Mods::new(raw))
}

struct Case {
    map: Beatmap,
    title: String,
    /// Mod key to the attributes ppy gave for it.
    expected: BTreeMap<String, serde_json::Value>,
}

fn cases() -> Vec<Case> {
    let dir = corpus_dir();
    let text = std::fs::read_to_string(dir.join("expected.json"))
        .expect("corpus/expected.json — build it with scripts/pp_corpus.py");
    let corpus: serde_json::Value = serde_json::from_str(&text).expect("valid json");

    corpus["maps"]
        .as_array()
        .expect("a list of maps")
        .iter()
        .map(|entry| {
            let id = entry["beatmap_id"].as_u64().expect("an id");
            let path = dir.join("maps").join(format!("{id}.osu"));
            let map = Beatmap::parse(&std::fs::read_to_string(&path).expect("the map"))
                .unwrap_or_else(|e| panic!("{id} did not parse: {e}"));
            let expected = entry["attributes"]
                .as_object()
                .expect("attributes per mod set")
                .iter()
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect();
            Case {
                map,
                title: format!("{} [{}] ({id})",
                    entry["title"].as_str().unwrap_or("?"),
                    entry["version"].as_str().unwrap_or("?")),
                expected,
            }
        })
        .collect()
}

#[test]
fn the_corpus_is_there_and_is_worth_checking_against() {
    // A corpus that quietly emptied would turn every test below into a pass.
    let cases = cases();
    assert!(cases.len() >= 5, "only {} maps in the corpus", cases.len());
    let pairs: usize = cases.iter().map(|c| c.expected.len()).sum();
    assert!(pairs >= 100, "only {pairs} map-and-mods pairs to check against");
}

#[test]
fn the_greatest_combo_a_map_allows_is_the_one_ppy_reports() {
    // Everything that can be hit, counted once: a circle, a slider's head, its
    // ticks, its repeats, its tail, a spinner. Agreeing with ppy on this means
    // the slider tick spacing and repeat handling underneath are right, which
    // is what the difficulty calculation walks over — so it is the first thing
    // worth being sure of.
    let mut checked = 0;
    let mut wrong = Vec::new();
    for case in cases() {
        for (key, attrs) in &case.expected {
            let Some(mods) = mods_of(key) else { continue };
            let Some(theirs) = attrs["max_combo"].as_u64() else { continue };
            let ours = u64::from(dossier_assay::max_combo(&case.map, mods));
            checked += 1;
            if ours != theirs {
                wrong.push(format!(
                    "  {} {key}: наш {ours}, ppy {theirs} (разница {})",
                    case.title,
                    ours as i64 - theirs as i64
                ));
            }
        }
    }
    assert!(checked > 0, "nothing was checked");
    assert!(
        wrong.is_empty(),
        "{} of {checked} disagree with ppy:\n{}",
        wrong.len(),
        wrong.join("\n")
    );
}

#[test]
fn the_pressing_difficulty_is_the_one_ppy_reports() {
    // The first figure here that ppy grades outright, and so the first check on
    // everything under it: rhythm reads the gaps between objects, double-tapping
    // reads the normalised jump distances, and a mistake in either surfaces here
    // as a number that is simply not theirs.
    let mut checked = 0;
    let mut worst: Option<(String, f64, f64)> = None;
    for case in cases() {
        for (key, attrs) in &case.expected {
            let Some(mods) = mods_of(key) else { continue };
            let Some(theirs) = attrs["speed_difficulty"].as_f64() else { continue };
            let ours = dossier_assay::speed_difficulty(&case.map, mods);
            checked += 1;
            let off = if theirs > 0.0 { (ours - theirs).abs() / theirs } else { (ours - theirs).abs() };
            if worst.as_ref().is_none_or(|(_, _, w)| off > *w) {
                worst = Some((format!("{} {key}: наш {ours:.4}, ppy {theirs:.4}", case.title), ours, off));
            }
        }
    }
    assert!(checked > 0, "nothing was checked");
    let (what, _, off) = worst.expect("something to report");
    // Half a per cent, which is where the port stands rather than where it
    // should end up. It was nine per cent until the hit window was doubled —
    // ppy's is the full window, both sides of the note — and what is left is a
    // hundredth of a per cent on most pairs and a third of one at worst, on
    // Easy with DoubleTime. That last is not yet accounted for and the
    // threshold is set to catch a regression rather than to bless the gap:
    // tighten it when the cause is found.
    assert!(off < 0.005, "худшее расхождение {:.2}% на {checked} парах — {what}", off * 100.0);
}

/// The same walk for any attribute the corpus carries, reported as the worst
/// relative disagreement.
fn worst_against_ppy(field: &str, ours: impl Fn(&Beatmap, Mods) -> f64) -> (usize, f64, String) {
    let mut checked = 0;
    let mut worst = (0.0, String::from("nothing"));
    for case in cases() {
        for (key, attrs) in &case.expected {
            let Some(mods) = mods_of(key) else { continue };
            let Some(theirs) = attrs[field].as_f64() else { continue };
            let mine = ours(&case.map, mods);
            checked += 1;
            let scale = theirs.abs().max(1e-9);
            let off = (mine - theirs).abs() / scale;
            if off > worst.0 {
                worst = (off, format!("{} {key}: наш {mine:.6}, ppy {theirs:.6}", case.title));
            }
        }
    }
    (checked, worst.0, worst.1)
}

#[test]
fn the_aiming_difficulty_is_the_one_ppy_reports() {
    // The figure the three evaluators and the section summation exist to
    // produce. It came out three times too large and the cause was one sign:
    // ppy have two logistic overloads and the single-argument one takes its
    // exponent already formed, so feeding it to the four-argument form inverts
    // the probability of snapping against flowing.
    let (checked, off, what) = worst_against_ppy("aim_difficulty", |map, mods| {
        dossier_assay::aim_difficulty(map, mods).0
    });
    assert!(checked >= 150, "only {checked} pairs");
    assert!(off < 0.005, "худшее расхождение {:.2}% на {checked} парах — {what}", off * 100.0);
}

#[test]
fn the_slider_factor_is_the_one_ppy_reports() {
    // Aim built twice, once counting slider travel and once not, and this is
    // the ratio. It grades the two runs against each other rather than either
    // alone, so it catches the flag being ignored — which would put it at one
    // on every map.
    let (checked, off, what) = worst_against_ppy("slider_factor", |map, mods| {
        dossier_assay::aim_difficulty(map, mods).1
    });
    assert!(checked >= 150, "only {checked} pairs");
    assert!(off < 0.005, "худшее расхождение {:.2}% на {checked} парах — {what}", off * 100.0);
}
