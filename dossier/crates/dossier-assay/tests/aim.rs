//! The three readings of aim, checked the ways they can be before the skill
//! that consumes them exists.
//!
//! `aim_difficulty` is in the corpus and will grade all of this the moment the
//! summation is written. Until then these hold the evaluators to what must be
//! true of them whatever the arithmetic.

use dossier_assay::aim::{
    agility_difficulty_of, angle_acuteness, flow_difficulty_of, snap_difficulty_of,
};
use dossier_assay::preprocessing::difficulty_objects;
use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};

fn corpus() -> Vec<(String, Beatmap)> {
    let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("corpus");
    let text = std::fs::read_to_string(dir.join("expected.json")).expect("the corpus");
    let parsed: serde_json::Value = serde_json::from_str(&text).expect("valid json");
    parsed["maps"].as_array().expect("maps").iter().map(|entry| {
        let id = entry["beatmap_id"].as_u64().expect("an id");
        let map = Beatmap::parse(
            &std::fs::read_to_string(dir.join("maps").join(format!("{id}.osu"))).expect("read"),
        ).expect("parse");
        (format!("{} ({id})", entry["title"].as_str().unwrap_or("?")), map)
    }).collect()
}

#[test]
fn every_reading_of_aim_is_a_number_on_every_map() {
    // These three run over every object of every map under mods that move the
    // geometry and the clock. A negative difficulty or a NaN would travel into
    // the summation and come out as a star rating with no way back to the
    // object that caused it.
    for (title, map) in corpus() {
        for mods in [Mods::new(0), Mods::new(bits::HARD_ROCK), Mods::new(bits::EASY),
                     Mods::new(bits::DOUBLE_TIME)] {
            let objects = difficulty_objects(&map, mods);
            for at in 0..objects.len() {
                for (what, value) in [
                    ("snap", snap_difficulty_of(&objects, at, true)),
                    ("snap without sliders", snap_difficulty_of(&objects, at, false)),
                    ("flow", flow_difficulty_of(&objects, at, true)),
                    ("agility", agility_difficulty_of(&objects, at)),
                ] {
                    assert!(value.is_finite(), "{title} #{at}: {what} is {value}");
                    assert!(value >= 0.0, "{title} #{at}: {what} is negative ({value})");
                }
            }
        }
    }
}

#[test]
fn the_first_two_objects_have_no_aim_to_speak_of() {
    // Snap and flow both need two objects of history to have an angle at all,
    // and ppy guard on `Index <= 1` rather than on the angle being present.
    // Agility does not — it only needs the jump — and that difference is real.
    let (_, map) = corpus().into_iter().next().expect("a map");
    let objects = difficulty_objects(&map, Mods::new(0));
    for at in 0..2 {
        assert_eq!(snap_difficulty_of(&objects, at, true), 0.0);
        assert_eq!(flow_difficulty_of(&objects, at, true), 0.0);
    }
}

#[test]
fn a_hairpin_is_acute_and_a_straight_line_is_not() {
    // The two readings of a corner are each other backwards, and everything in
    // snap leans on which is which. Swapping them would still produce numbers.
    assert!(angle_acuteness(0.0) > 0.99, "a fold back on itself is as acute as it gets");
    assert!(angle_acuteness(std::f64::consts::PI) < 0.01, "a straight line is not acute");
    assert!(angle_acuteness(std::f64::consts::PI / 2.0) > 0.0);
    assert!(angle_acuteness(std::f64::consts::PI / 2.0) < 1.0);
}

#[test]
fn sliders_are_only_counted_when_they_are_asked_for() {
    // The skill is built twice, with sliders and without, and the ratio of the
    // two is what `slider_factor` reports. If the flag changed nothing the
    // ratio would be one on every map.
    let (title, map) = corpus().into_iter().next().expect("a map");
    let objects = difficulty_objects(&map, Mods::new(0));
    let with: f64 = (0..objects.len()).map(|at| snap_difficulty_of(&objects, at, true)).sum();
    let without: f64 = (0..objects.len()).map(|at| snap_difficulty_of(&objects, at, false)).sum();
    assert!(with > without, "{title}: {with} against {without}");
}
