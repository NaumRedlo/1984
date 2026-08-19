//! The preprocessing layer, checked the only ways it can be before a skill
//! stands on it.
//!
//! `OsuDifficultyHitObject` has no figure of its own in ppy's attributes reply,
//! so there is nothing here to compare against the way `max_combo` is compared.
//! What can be done is to run it over every map and mod set in the corpus and
//! insist on the things that must hold whatever the arithmetic — no infinities,
//! no negative distances, angles that are angles — and to pin the handful of
//! definitions that a careless edit would quietly invert.
//!
//! That is worth more than it sounds. Every one of these caught nothing on the
//! day it was written, and each of them is a way the port could have been
//! wrong without any test failing until a star rating came out odd three files
//! later.

use dossier_assay::preprocessing::{
    difficulty_objects, MIN_DELTA_TIME, NORMALISED_DIAMETER, NORMALISED_RADIUS,
};
use dossier_assay::slider_parts;
use dossier_beatmap::Beatmap;
use dossier_replay::{bits, Mods};
use dossier_sim::Timeline;

fn corpus() -> Vec<(String, Beatmap)> {
    let dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("corpus");
    let text = std::fs::read_to_string(dir.join("expected.json")).expect("the corpus");
    let parsed: serde_json::Value = serde_json::from_str(&text).expect("valid json");
    parsed["maps"]
        .as_array()
        .expect("maps")
        .iter()
        .map(|entry| {
            let id = entry["beatmap_id"].as_u64().expect("an id");
            let path = dir.join("maps").join(format!("{id}.osu"));
            let map = Beatmap::parse(&std::fs::read_to_string(path).expect("read"))
                .expect("parse");
            (format!("{} ({id})", entry["title"].as_str().unwrap_or("?")), map)
        })
        .collect()
}

/// The mod sets worth walking every map under: nothing, the two that move the
/// geometry, and the two that move the clock.
fn interesting() -> Vec<(&'static str, Mods)> {
    vec![
        ("NM", Mods::new(0)),
        ("HR", Mods::new(bits::HARD_ROCK)),
        ("EZ", Mods::new(bits::EASY)),
        ("DT", Mods::new(bits::DOUBLE_TIME)),
        ("HT", Mods::new(bits::HALF_TIME)),
    ]
}

#[test]
fn nothing_in_the_corpus_produces_a_figure_that_is_not_a_number() {
    // A NaN here does not fail; it spreads. It would travel through every skill
    // and come out as a star rating of NaN several files away from the slider
    // that made it, which is a bad afternoon.
    for (title, map) in corpus() {
        for (name, mods) in interesting() {
            for object in difficulty_objects(&map, mods) {
                let at = format!("{title} {name} #{}", object.index);
                for (what, value) in [
                    ("jump", object.jump_distance),
                    ("lazy jump", object.lazy_jump_distance),
                    ("minimum jump", object.minimum_jump_distance),
                    ("travel", object.travel_distance),
                    ("lazy travel", object.lazy_travel_distance),
                    ("delta", object.delta_time),
                    ("travel time", object.travel_time),
                    ("minimum jump time", object.minimum_jump_time),
                ] {
                    assert!(value.is_finite(), "{at}: {what} is {value}");
                    assert!(value >= 0.0, "{at}: {what} is negative ({value})");
                }
                if let Some(angle) = object.angle {
                    assert!(
                        angle.is_finite() && (0.0..=std::f64::consts::PI).contains(&angle),
                        "{at}: angle {angle} is not an angle"
                    );
                }
            }
        }
    }
}

#[test]
fn no_two_objects_are_ever_closer_together_than_the_floor() {
    // Maps do stack objects on the same millisecond, and a delta of zero
    // divides into everything downstream.
    for (title, map) in corpus() {
        for object in difficulty_objects(&map, Mods::new(0)) {
            assert!(
                object.adjusted_delta_time >= MIN_DELTA_TIME,
                "{title} #{}: {} ms", object.index, object.adjusted_delta_time
            );
            assert!(object.last_object_end_delta_time >= MIN_DELTA_TIME);
            assert!(object.minimum_jump_time >= MIN_DELTA_TIME);
        }
    }
}

#[test]
fn a_slider_starts_where_the_slider_is() {
    // The path's own points are absolute — the stack shift is already in them —
    // so a head is the slider's position and nothing is added to it. Getting
    // this backwards would offset every slider in the map by its own
    // coordinates, which is the kind of wrong that still produces numbers.
    for (_, map) in corpus() {
        let timeline = Timeline::build(&map, Mods::new(0));
        for object in &timeline.objects {
            let parts = slider_parts(&map, object);
            let Some(head) = parts.first() else { continue };
            assert!(
                (head.pos.x - object.pos.x).abs() < 0.001
                    && (head.pos.y - object.pos.y).abs() < 0.001,
                "head at {:?}, slider at {:?}", head.pos, object.pos
            );
        }
    }
}

#[test]
fn distances_are_measured_against_a_circle_of_one_size_on_every_map() {
    // The point of normalising: a jump of one diameter has to mean one
    // diameter's worth of difficulty whether the map is CS3 or CS6. So the
    // figure is the plain distance scaled by `50 / radius`, and that is checked
    // against the plain distance rather than by proxy.
    //
    // Written first as "the same jumps are longer under HardRock, which has
    // smaller circles". They are, mostly — 1624 of 1696 — and the seventy-two
    // that are not were the test being wrong: HardRock also mirrors the
    // playfield, so it does not hold the geometry still while changing the
    // circle size, and nothing does.
    for (title, map) in corpus() {
        let timeline = Timeline::build(&map, Mods::new(0));
        let radius = timeline.difficulty.circle_radius();
        let scaling = NORMALISED_RADIUS / radius;
        let mut checked = 0;

        for object in difficulty_objects(&map, Mods::new(0)) {
            let here = &timeline.objects[object.index];
            let before = &timeline.objects[object.index - 1];
            if here.is_spinner() || before.is_spinner() {
                continue;
            }
            let raw = (here.pos.x - before.pos.x).hypot(here.pos.y - before.pos.y);
            assert!(
                (object.jump_distance - raw * scaling).abs() < 0.001,
                "{title} #{}: {} against {}", object.index, object.jump_distance, raw * scaling
            );
            checked += 1;
        }
        assert!(checked > 100, "{title}: only {checked} jumps to look at");
    }
}

#[test]
fn the_shortest_reading_of_a_jump_off_a_slider_is_the_one_taken() {
    // Two ways to leave a slider — cut it short, or follow it through and jump
    // from the tail — and the player is assumed to take whichever is shorter.
    // So the minimum can never exceed the lazy reading it is chosen against.
    for (title, map) in corpus() {
        for object in difficulty_objects(&map, Mods::new(0)) {
            assert!(
                object.minimum_jump_distance <= object.lazy_jump_distance + 0.001,
                "{title} #{}: minimum {} over lazy {}",
                object.index, object.minimum_jump_distance, object.lazy_jump_distance
            );
        }
    }
}

#[test]
fn a_slider_followed_lazily_never_travels_further_than_its_path() {
    // The whole idea of the lazy path is that it is *less* movement than
    // tracing the slider: the cursor sits still while the follow circle keeps
    // up and moves only when it would slip. A lazy distance longer than the
    // path itself would mean the opposite had been implemented.
    for (title, map) in corpus() {
        let timeline = Timeline::build(&map, Mods::new(0));
        let radius = timeline.difficulty.circle_radius();
        for object in difficulty_objects(&map, Mods::new(0)) {
            if !object.is_slider {
                assert_eq!(object.lazy_travel_distance, 0.0);
                continue;
            }
            let path = match &timeline.objects[object.index].kind {
                dossier_sim::TimedKind::Slider { path, slides, .. } => {
                    path.length() * f64::from(*slides)
                }
                _ => continue,
            };
            // Both in normalised units, and with room for the fact that the
            // lazy walk is a straight line between pieces where the path curves.
            let normalised_path = path * NORMALISED_DIAMETER / (radius * 2.0);
            assert!(
                object.lazy_travel_distance <= normalised_path + 1.0,
                "{title} #{}: lazy {} over path {normalised_path}",
                object.index, object.lazy_travel_distance
            );
        }
    }
}
