//! Stacking tests.
//!
//! Default CS 5 → radius 32, so one step of a stack is 3.2 osu!px up and left.
//! AR 5 → preempt 1200ms, and with the default leniency 0.7 the window in which
//! two objects count as stacked is 840ms.

use dossier_beatmap::Beatmap;
use dossier_replay::{Keys, Mods, Replay, ReplayFrame};
use dossier_sim::GameState;

const STEP: f64 = 3.2;
const EPS: f64 = 1e-9;

fn beatmap(body: &str) -> Beatmap {
    Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("test map should parse")
}

fn positions(map: &Beatmap, mods: u32) -> Vec<(f64, f64)> {
    GameState::from_beatmap(map, Mods::new(mods))
        .timeline()
        .objects
        .iter()
        .map(|o| (o.pos.x, o.pos.y))
        .collect()
}

const HEADER: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0
";

#[test]
fn the_earlier_note_of_a_pair_is_the_one_that_moves() {
    // osu! stacks upwards from the last object, so the note played last sits
    // where it was authored and the ones before it climb away from it.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
"
    ));
    let pos = positions(&map, 0);
    assert!((pos[0].0 - (100.0 - STEP)).abs() < EPS, "{pos:?}");
    assert!((pos[0].1 - (100.0 - STEP)).abs() < EPS, "{pos:?}");
    assert_eq!(pos[1], (100.0, 100.0), "the last one stays put");
}

#[test]
fn a_longer_stack_climbs_one_step_at_a_time() {
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
100,100,1400,1,0
"
    ));
    let pos = positions(&map, 0);
    assert!((pos[0].0 - (100.0 - 2.0 * STEP)).abs() < EPS, "{pos:?}");
    assert!((pos[1].0 - (100.0 - STEP)).abs() < EPS, "{pos:?}");
    assert_eq!(pos[2], (100.0, 100.0));
}

#[test]
fn notes_too_far_apart_in_time_do_not_stack() {
    // 900ms apart, and the window is 840ms.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
100,100,1900,1,0
"
    ));
    assert_eq!(positions(&map, 0), vec![(100.0, 100.0), (100.0, 100.0)]);
}

#[test]
fn notes_too_far_apart_in_space_do_not_stack() {
    // 4px apart; the threshold is 3.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
104,100,1200,1,0
"
    ));
    assert_eq!(positions(&map, 0), vec![(100.0, 100.0), (104.0, 100.0)]);
}

#[test]
fn a_leniency_of_zero_switches_stacking_off() {
    let map = beatmap(
        "
[General]
StackLeniency:0

[Difficulty]
CircleSize:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
",
    );
    assert_eq!(positions(&map, 0), vec![(100.0, 100.0), (100.0, 100.0)]);
}

#[test]
fn spinners_neither_stack_nor_break_a_stack() {
    // The spinner sits between two stacked circles: it must be stepped over,
    // not treated as an object at (256,192) that ends the run.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
256,192,1100,12,0,1150
100,100,1200,1,0
"
    ));
    let pos = positions(&map, 0);
    assert!((pos[0].0 - (100.0 - STEP)).abs() < EPS, "{pos:?}");
    assert_eq!(pos[1], (256.0, 192.0), "the spinner never moves");
    assert_eq!(pos[2], (100.0, 100.0));
}

#[test]
fn a_slider_carries_its_path_when_it_moves() {
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
0,0,1000,2,0,L|140:0,1,140
0,0,1200,1,0
"
    ));
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];

    assert!((slider.pos.x - -STEP).abs() < EPS, "head moved");
    let ball = slider.ball_at(1000.0).expect("ball at the head");
    assert!(
        (ball.x - -STEP).abs() < 0.5,
        "the path came along: {ball:?}"
    );
    let end = slider.ball_at(slider.end_ms).expect("ball at the tail");
    assert!((end.x - (140.0 - STEP)).abs() < 0.5, "{end:?}");
}

#[test]
fn a_circle_on_a_sliders_tail_pulls_the_run_down_instead_of_up() {
    // The circle lands where the slider ends. osu! keeps the slider put and
    // drops what stacked onto it, rather than lifting the slider away.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
0,0,1000,2,0,L|140:0,1,140
140,0,1600,1,0
"
    ));
    let state = GameState::from_beatmap(&map, Mods::default());
    let objects = &state.timeline().objects;

    assert_eq!(
        (objects[0].pos.x, objects[0].pos.y),
        (0.0, 0.0),
        "slider stays"
    );
    assert!(
        (objects[1].pos.x - (140.0 + STEP)).abs() < EPS,
        "the circle drops the other way: {:?}",
        objects[1].pos
    );
}

#[test]
fn hard_rock_stacks_the_mirrored_positions() {
    // Mirroring happens first, so the stack is built where the notes actually
    // are — and the step still goes up-and-left in screen terms.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
"
    ));
    let pos = positions(&map, dossier_replay::bits::HARD_ROCK);
    // HR shrinks CS to 6.5, so the radius is 25.28 and a step is 2.528.
    let step = (54.4 - 4.48 * 6.5) * 0.1;
    assert!((pos[0].1 - (284.0 - step)).abs() < 1e-6, "{pos:?}");
    assert_eq!(pos[1], (100.0, 284.0));
}

// ── why this matters for judgement ───────────────────────────────────────

#[test]
fn a_click_on_the_stacked_position_counts() {
    // The player sees and clicks the shifted note. Judging against the
    // authored coordinates would call this a miss on a perfectly good hit —
    // which is the entire reason stacking lives in the simulator.
    let map = beatmap(&format!(
        "{HEADER}
[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
"
    ));

    let frames = vec![
        ReplayFrame {
            time_ms: 990,
            x: 96.8,
            y: 96.8,
            keys: Keys(0),
        },
        ReplayFrame {
            time_ms: 1000,
            x: 96.8,
            y: 96.8,
            keys: Keys(Keys::K1),
        },
        ReplayFrame {
            time_ms: 1010,
            x: 96.8,
            y: 96.8,
            keys: Keys(0),
        },
        ReplayFrame {
            time_ms: 1200,
            x: 100.0,
            y: 100.0,
            keys: Keys(Keys::K1),
        },
        ReplayFrame {
            time_ms: 1210,
            x: 100.0,
            y: 100.0,
            keys: Keys(0),
        },
    ];
    let replay = Replay {
        mode: dossier_replay::GameMode::Standard,
        game_version: 20_260_101,
        beatmap_hash: String::new(),
        player: "tester".into(),
        replay_hash: String::new(),
        hits: Default::default(),
        score: 0,
        max_combo: 0,
        perfect_combo: false,
        mods: Mods::default(),
        life_bar: String::new(),
        timestamp_ticks: 0,
        online_score_id: 0,
        target_practice_accuracy: None,
        frames,
        rng_seed: None,
    };

    let score = GameState::new(&map, &replay).judge().unwrap().final_state();
    assert_eq!(score.counts.count_300, 2);
    assert_eq!(score.counts.count_miss, 0);
}
