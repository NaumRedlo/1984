//! Judgement tests.
//!
//! Maps use the default CS 5 (radius 32 osu!px, follow circle 76.8) and OD 5
//! (windows 50 / 100 / 150 ms) unless a test says otherwise, so the numbers in
//! the assertions can be read directly.

use std::f64::consts::TAU;

use dossier_beatmap::Beatmap;
use dossier_replay::{HitCounts, Keys, Mods, Replay, ReplayFrame};
use dossier_sim::{GameState, Judgement, Part, Verdict};

fn beatmap(body: &str) -> Beatmap {
    Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("test map should parse")
}

fn replay_with(frames: Vec<ReplayFrame>, mods: u32) -> Replay {
    Replay {
        mode: dossier_replay::GameMode::Standard,
        game_version: 20_260_101,
        beatmap_hash: String::new(),
        player: "tester".into(),
        replay_hash: String::new(),
        hits: HitCounts::default(),
        score: 0,
        max_combo: 0,
        perfect_combo: false,
        mods: Mods::new(mods),
        life_bar: String::new(),
        timestamp_ticks: 0,
        online_score_id: 0,
        target_practice_accuracy: None,
        frames,
        rng_seed: None,
        score_info: None,
    }
}

fn frame(time_ms: i64, x: f32, y: f32, keys: u8) -> ReplayFrame {
    ReplayFrame {
        time_ms,
        x,
        y,
        keys: Keys(keys),
    }
}

/// A press: idle, one frame with the button down, then released. The click
/// lands exactly on `time_ms`.
fn click(time_ms: i64, x: f32, y: f32) -> Vec<ReplayFrame> {
    vec![
        frame(time_ms - 10, x, y, 0),
        frame(time_ms, x, y, Keys::K1),
        frame(time_ms + 10, x, y, 0),
    ]
}

/// Frames every 10 ms over `[from, to]`, with the cursor placed by `pos` and
/// the button held whenever `held` says so.
fn frames_over(
    from: i64,
    to: i64,
    pos: impl Fn(i64) -> (f32, f32),
    held: impl Fn(i64) -> bool,
) -> Vec<ReplayFrame> {
    (from..=to)
        .step_by(10)
        .map(|t| {
            let (x, y) = pos(t);
            frame(t, x, y, if held(t) { Keys::K1 } else { 0 })
        })
        .collect()
}

fn judged(map: &Beatmap, replay: &Replay) -> HitCounts {
    GameState::new(map, replay)
        .judge()
        .unwrap()
        .final_state()
        .counts
}

// ── circles ──────────────────────────────────────────────────────────────

const ONE_CIRCLE: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
";

#[test]
fn a_click_on_time_is_a_three_hundred() {
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::new(&map, &replay_with(click(1000, 100.0, 100.0), 0));
    let score = state.judge().unwrap().final_state();

    assert_eq!(score.counts.count_300, 1);
    assert_eq!(score.combo, 1);
    assert!((score.accuracy() - 100.0).abs() < 1e-9);
}

#[test]
fn the_windows_step_down_as_the_click_drifts() {
    let map = beatmap(ONE_CIRCLE);
    let at = |t: i64| {
        let state = GameState::new(&map, &replay_with(click(t, 100.0, 100.0), 0));
        state.judge().unwrap().events()[0].result
    };

    // The windows are exclusive: 50ms on a 50ms window is already a 100. Both
    // frame times and object times are whole milliseconds, so the boundary is a
    // value real hits land on in quantity — on a dense map, dozens of them.
    assert_eq!(at(1049), Judgement::Great, "one under the 300 window");
    assert_eq!(at(1050), Judgement::Ok, "exactly on it is not inside it");
    assert_eq!(at(1099), Judgement::Ok);
    assert_eq!(at(1100), Judgement::Meh, "same rule at the next edge");
    assert_eq!(at(1149), Judgement::Meh, "the last hittable millisecond");
}

#[test]
fn a_click_exactly_on_the_fifty_window_does_not_land_at_all() {
    let map = beatmap(ONE_CIRCLE);
    assert_eq!(
        judged(&map, &replay_with(click(1149, 100.0, 100.0), 0)).count_50,
        1
    );
    assert_eq!(
        judged(&map, &replay_with(click(1150, 100.0, 100.0), 0)).count_miss,
        1
    );
}

#[test]
fn the_error_is_signed_so_early_and_late_are_distinguishable() {
    let map = beatmap(ONE_CIRCLE);
    let early = GameState::new(&map, &replay_with(click(970, 100.0, 100.0), 0));
    let late = GameState::new(&map, &replay_with(click(1030, 100.0, 100.0), 0));

    assert_eq!(early.judge().unwrap().events()[0].error_ms, Some(-30.0));
    assert_eq!(late.judge().unwrap().events()[0].error_ms, Some(30.0));
}

#[test]
fn a_click_past_the_window_never_lands() {
    let map = beatmap(ONE_CIRCLE);
    let counts = judged(&map, &replay_with(click(1151, 100.0, 100.0), 0));
    assert_eq!(counts.count_miss, 1);
}

#[test]
fn an_early_click_on_the_note_takes_it_with_it() {
    // This test used to assert the opposite, and was wrong. Stable judges a
    // click that lands on the circle within 400ms of it but outside the 50
    // window, and a judgement outside the window is a miss — which consumes
    // the note. A second click cannot save it. We were more forgiving than the
    // game, which is a pleasant bug and still a bug.
    let map = beatmap(ONE_CIRCLE);
    let mut frames = click(800, 100.0, 100.0); // 200ms early, window is 150
    frames.extend(click(1000, 100.0, 100.0));
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_miss, 1, "the early click took it");
    assert_eq!(counts.count_300, 0, "so the click on time found nothing");
}

#[test]
fn an_early_click_that_misses_the_circle_takes_nothing() {
    // Position decides first: a click that does not land on the note neither
    // hits it, nor misses it, nor shakes it.
    let map = beatmap(ONE_CIRCLE);
    let mut frames = click(800, 200.0, 100.0); // 100px away, radius 32
    frames.extend(click(1000, 100.0, 100.0));
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_300, 1);
}

#[test]
fn a_click_off_the_circle_does_not_count() {
    let map = beatmap(ONE_CIRCLE);
    // 40px away, radius is 32.
    let counts = judged(&map, &replay_with(click(1000, 140.0, 100.0), 0));
    assert_eq!(counts.count_miss, 1);
    assert_eq!(counts.count_300, 0);
}

#[test]
fn an_unclicked_circle_misses() {
    let map = beatmap(ONE_CIRCLE);
    let frames = frames_over(0, 3000, |_| (100.0, 100.0), |_| false);
    let counts = judged(&map, &replay_with(frames, 0));
    assert_eq!(counts.count_miss, 1);
}

#[test]
fn a_miss_is_recorded_when_its_window_shuts_not_when_it_was_due() {
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::new(&map, &replay_with(Vec::new(), 0));
    let judge = state.judge().unwrap();
    assert_eq!(judge.events()[0].time_ms, 1150.0);
    // Before that instant nothing has been decided.
    assert_eq!(judge.state_at(1100.0).counts.count_miss, 0);
    assert_eq!(judge.state_at(1150.0).counts.count_miss, 1);
}

// ── notelock and press detection ─────────────────────────────────────────

#[test]
fn a_click_cannot_reach_past_an_object_that_is_still_live() {
    // Two circles 50ms apart, so the first is still hittable when the second
    // is due. Clicking the second one's position judges nothing.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
300,300,1050,1,0
",
    );
    let counts = judged(&map, &replay_with(click(1050, 300.0, 300.0), 0));
    assert_eq!(counts.count_miss, 2, "notelock swallowed the click");
    assert_eq!(counts.count_300, 0);
}

#[test]
fn holding_the_button_hits_one_object_not_every_object_under_it() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
",
    );
    let frames = frames_over(900, 1400, |_| (100.0, 100.0), |t| t >= 1000);
    let counts = judged(&map, &replay_with(frames, 0));
    assert_eq!(counts.count_300, 1, "only the rising edge is a click");
    assert_eq!(counts.count_miss, 1);
}

#[test]
fn a_keyboard_press_setting_two_bits_is_still_one_click() {
    // osu! sets M1 alongside K1 for a keyboard hit. Counting both would let a
    // single tap consume two stacked objects.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1005,1,0
",
    );
    let frames = vec![
        frame(990, 100.0, 100.0, 0),
        frame(1000, 100.0, 100.0, Keys::K1 | Keys::M1),
        frame(1010, 100.0, 100.0, 0),
    ];
    let counts = judged(&map, &replay_with(frames, 0));
    assert_eq!(counts.count_300, 1);
    assert_eq!(counts.count_miss, 1);
}

#[test]
fn releasing_and_pressing_again_is_two_clicks() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1200,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1200, 100.0, 100.0));
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_300, 2);
}

// ── combo and accuracy ───────────────────────────────────────────────────

#[test]
fn a_miss_resets_the_combo_but_not_the_peak() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
100,100,1800,1,0
100,100,2200,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1400, 100.0, 100.0));
    // 1800 goes unclicked.
    frames.extend(click(2200, 100.0, 100.0));

    let state = GameState::new(&map, &replay_with(frames, 0));
    let score = state.judge().unwrap().final_state();
    assert_eq!(score.max_combo, 2);
    assert_eq!(score.combo, 1);
    assert_eq!(score.counts.count_miss, 1);
}

#[test]
fn accuracy_weighs_the_judgements() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0); // 300
    frames.extend(click(1480, 100.0, 100.0)); // +80ms -> 100
    let score = GameState::new(&map, &replay_with(frames, 0))
        .judge()
        .unwrap()
        .final_state();

    assert_eq!(score.counts.count_300, 1);
    assert_eq!(score.counts.count_100, 1);
    // (300 + 100) / 600
    assert!((score.accuracy() - 400.0 / 600.0 * 100.0).abs() < 1e-9);
}

#[test]
fn the_score_can_be_read_at_any_instant() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1400, 100.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));

    assert_eq!(state.update(500.0).score.unwrap().combo, 0);
    assert_eq!(state.update(1000.0).score.unwrap().combo, 1);
    assert_eq!(state.update(1399.0).score.unwrap().combo, 1);
    assert_eq!(state.update(1400.0).score.unwrap().combo, 2);
}

#[test]
fn a_map_with_no_replay_reports_no_score_rather_than_a_wall_of_misses() {
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    assert!(state.judge().is_none());
    assert!(state.update(1000.0).score.is_none());
}

// ── sliders ──────────────────────────────────────────────────────────────

/// 500ms beats, SliderMultiplier 1.4 -> 140px per beat. A 140px slider from
/// (0,0) to (140,0) starting at 1000 therefore runs to 1500, with no ticks
/// (the only candidate lands on the end).
const SHORT_SLIDER: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|140:0,1,140
";

/// Cursor riding a linear slider that runs from (0,0) to (140,0) over
/// `[start, start + duration]`, computed independently of the path code.
fn ball_x(t: i64, start: f64, duration: f64) -> f32 {
    let progress = ((t as f64 - start) / duration).clamp(0.0, 1.0);
    (140.0 * progress) as f32
}

#[test]
fn a_fully_tracked_slider_is_a_three_hundred() {
    let map = beatmap(SHORT_SLIDER);
    let frames = frames_over(
        900,
        1600,
        |t| (ball_x(t, 1000.0, 500.0), 0.0),
        |t| t >= 1000,
    );
    let state = GameState::new(&map, &replay_with(frames, 0));
    let score = state.judge().unwrap().final_state();

    assert_eq!(score.counts.count_300, 1);
    // Head and tail each move the counter.
    assert_eq!(score.max_combo, 2);
}

#[test]
fn dropping_the_tail_costs_the_slider_a_hundred() {
    let map = beatmap(SHORT_SLIDER);
    let frames = frames_over(
        900,
        1600,
        |t| (ball_x(t, 1000.0, 500.0), 0.0),
        |t| (1000..1200).contains(&t),
    );
    let state = GameState::new(&map, &replay_with(frames, 0));
    let score = state.judge().unwrap().final_state();

    assert_eq!(score.counts.count_100, 1);
    // …and costs nothing else. A dropped tail is the one part that doesn't
    // take the combo with it, which is why real scores end up full of 100s
    // with the combo intact.
    assert_eq!(score.combo, 1, "the head still counts");
    assert_eq!(score.max_combo, 1);
}

#[test]
fn a_dropped_tick_breaks_the_combo_but_a_dropped_tail_does_not() {
    // The contrast is the whole point: both cost the 300, only one costs the
    // combo. Treating them alike shreds the combo on any map with sliders.
    let map = beatmap(TICKED_SLIDER);
    let ball = |t: i64| {
        let progress = ((t as f64 - 1000.0) / 1000.0).clamp(0.0, 1.0);
        ((280.0 * progress) as f32, 0.0)
    };

    let lost_tick = frames_over(900, 2100, ball, |t| t >= 1000 && !(1450..1550).contains(&t));
    let lost_tail = frames_over(900, 2100, ball, |t| (1000..1900).contains(&t));

    let after_tick = GameState::new(&map, &replay_with(lost_tick, 0))
        .judge()
        .unwrap()
        .final_state();
    let after_tail = GameState::new(&map, &replay_with(lost_tail, 0))
        .judge()
        .unwrap()
        .final_state();

    assert_eq!(after_tick.counts.count_100, 1);
    assert_eq!(after_tick.combo, 1, "combo restarted at the tail");

    assert_eq!(after_tail.counts.count_100, 1);
    assert_eq!(after_tail.combo, 2, "head and tick, uninterrupted");
}

#[test]
fn letting_go_a_hair_early_still_keeps_the_tail() {
    let map = beatmap(SHORT_SLIDER);
    // The tail is checked 36ms before the end, i.e. at 1464.
    let kept = frames_over(
        900,
        1600,
        |t| (ball_x(t, 1000.0, 500.0), 0.0),
        |t| (1000..1470).contains(&t),
    );
    let dropped = frames_over(
        900,
        1600,
        |t| (ball_x(t, 1000.0, 500.0), 0.0),
        |t| (1000..1450).contains(&t),
    );

    assert_eq!(judged(&map, &replay_with(kept, 0)).count_300, 1);
    assert_eq!(judged(&map, &replay_with(dropped, 0)).count_100, 1);
}

#[test]
fn a_short_slide_gets_half_its_length_of_grace_not_a_flat_36ms() {
    // A 1/8 slide at 240bpm runs 62.5ms. A flat 36ms window would hand the
    // player more than half of it; the rule caps the grace at the slide's
    // midpoint, so the tail is decided at 31.25ms before the end.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,250,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|35:0,1,35
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];
    assert!((slider.end_ms - 1062.5).abs() < 1e-6, "{}", slider.end_ms);

    let ball = |t: i64| {
        let progress = ((t as f64 - 1000.0) / 62.5).clamp(0.0, 1.0);
        ((35.0 * progress) as f32, 0.0)
    };
    // Released at 1040: past the midpoint check (1031.25), before a flat
    // 36ms one would have fired (1026.5). Only the stricter rule drops it.
    let frames = frames_over(900, 1200, ball, |t| (1000..1040).contains(&t));
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_300, 1);

    let early = frames_over(900, 1200, ball, |t| (1000..1030).contains(&t));
    assert_eq!(judged(&map, &replay_with(early, 0)).count_100, 1);
}

#[test]
fn a_missed_head_still_lets_the_body_score() {
    let map = beatmap(SHORT_SLIDER);
    // The button goes down at 500 — long before the head's window opens at
    // 850 — and never comes back up, so there is no click inside the window.
    // Tracking still works, because that only asks whether a button is held.
    let frames = frames_over(500, 1600, |t| (ball_x(t, 1000.0, 500.0), 0.0), |_| true);
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_100, 1, "half the parts landed");
    assert_eq!(counts.count_miss, 0, "the slider itself is not a miss");
}

#[test]
fn a_slider_nobody_touched_is_a_miss() {
    let map = beatmap(SHORT_SLIDER);
    let frames = frames_over(900, 1600, |_| (300.0, 300.0), |_| false);
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_miss, 1);
}

#[test]
fn straying_outside_the_follow_circle_drops_tracking() {
    let map = beatmap(SHORT_SLIDER);
    // Button held the whole way, but the cursor sits still at the head while
    // the ball runs off to x=140 — 140px away, follow circle is 76.8.
    let frames = frames_over(900, 1600, |_| (0.0, 0.0), |t| t >= 1000);
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_100, 1);
}

/// Twice as long: 280px over two beats, so one tick lands mid-way at 1500.
const TICKED_SLIDER: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|280:0,1,280
";

#[test]
fn ticks_land_on_the_beat() {
    let map = beatmap(TICKED_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];
    assert_eq!(slider.tick_times(), vec![1500.0]);
    assert!(slider.repeat_times().is_empty());
}

#[test]
fn dropping_a_tick_breaks_combo_and_downgrades_the_slider() {
    let map = beatmap(TICKED_SLIDER);
    let duration = 1000.0;
    let frames = frames_over(
        900,
        2100,
        |t| {
            let progress = ((t as f64 - 1000.0) / duration).clamp(0.0, 1.0);
            ((280.0 * progress) as f32, 0.0)
        },
        // Released across the tick at 1500, back on for the tail.
        |t| t >= 1000 && !(1450..1550).contains(&t),
    );
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().unwrap();
    let score = judge.final_state();

    let tick = judge
        .events()
        .iter()
        .find(|e| e.part == Part::SliderTick)
        .expect("the slider has a tick");
    assert_eq!(tick.result, Judgement::Miss);
    assert_eq!(tick.combo_after, 0, "a dropped tick resets combo");

    // Head and tail landed, the tick didn't: 2 of 3.
    assert_eq!(score.counts.count_100, 1);
    assert_eq!(score.max_combo, 1);
}

#[test]
fn the_full_combo_counts_every_part_of_every_object() {
    // One circle, plus a slider worth head + tick + tail. This number can be
    // checked against the figure osu! publishes for a map, which makes it the
    // one part of the simulation with an independent answer key.
    let map = beatmap(&format!("{}\n100,100,4000,1,0\n", TICKED_SLIDER.trim_end()));
    let state = GameState::from_beatmap(&map, Mods::default());
    assert_eq!(state.timeline().objects.len(), 2);
    assert_eq!(state.max_possible_combo(), 4);
}

#[test]
fn a_reversed_slide_meets_its_ticks_in_the_opposite_order() {
    // Two slides of 500ms with a tick rate of 2 -> ticks every 250ms.
    let map = beatmap(
        "
[Difficulty]
SliderMultiplier:1.4
SliderTickRate:2

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|140:0,2,140
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];

    // Forward slide ticks at 1250; the reversed one mirrors to 1750.
    assert_eq!(slider.tick_times(), vec![1250.0, 1750.0]);
    assert_eq!(slider.repeat_times(), vec![1500.0]);
    assert_eq!(slider.end_ms, 2000.0);
}

// ── spinners ─────────────────────────────────────────────────────────────

const SPINNER: &str = "
[Difficulty]
OverallDifficulty:5

[HitObjects]
256,192,1000,12,0,2000
";

/// A cursor circling the playfield centre `turns` times over the spinner.
fn spin_frames(from: i64, to: i64, turns: f64) -> Vec<ReplayFrame> {
    let span = (to - from) as f64;
    frames_over(
        from,
        to,
        |t| {
            let angle = (t - from) as f64 / span * turns * TAU;
            (
                (256.0 + 100.0 * angle.cos()) as f32,
                (192.0 + 100.0 * angle.sin()) as f32,
            )
        },
        |_| false,
    )
}

/// Four seconds at OD5: 175rpm × 4s = 11.67, truncated to 11 turns.
const LONG_SPINNER: &str = "
[Difficulty]
OverallDifficulty:5

[HitObjects]
256,192,1000,12,0,5000
";

#[test]
fn the_requirement_is_revolutions_per_minute_not_per_second() {
    // Getting this wrong is invisible in a totals table and fails every
    // spinner in every replay: at 5 turns a second the map would be asking for
    // 300rpm, which almost nobody sustains.
    let od5 = dossier_beatmap::Difficulty::default();
    assert!((od5.spins_per_second() - 175.0 / 60.0).abs() < 1e-9);

    let od10 = dossier_beatmap::Difficulty {
        overall_difficulty: 10.0,
        ..Default::default()
    };
    assert!((od10.spins_per_second() - 250.0 / 60.0).abs() < 1e-9);
}

#[test]
fn a_completed_spinner_is_a_three_hundred() {
    // OD5 asks for 175rpm; one second of spinner truncates to 2 turns.
    let map = beatmap(SPINNER);
    let counts = judged(&map, &replay_with(spin_frames(1000, 2000, 3.0), 0));
    assert_eq!(counts.count_300, 1);
}

#[test]
fn a_spinner_nobody_span_is_a_miss() {
    let map = beatmap(SPINNER);
    let frames = frames_over(900, 2100, |_| (256.0, 100.0), |_| false);
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_miss, 1);
}

#[test]
fn a_nearly_finished_spinner_scores_partially() {
    let map = beatmap(LONG_SPINNER);
    // 10.5 of the 11 required turns — past 90%, short of the full thing.
    let counts = judged(&map, &replay_with(spin_frames(1000, 5000, 10.5), 0));
    assert_eq!(counts.count_100, 1);

    // 8.5 of 11 is past 75% but not 90%.
    let counts = judged(&map, &replay_with(spin_frames(1000, 5000, 8.5), 0));
    assert_eq!(counts.count_50, 1);
}

#[test]
fn an_ordinary_spin_rate_clears_an_ordinary_spinner() {
    // The measurement that sent us here: real players cleared spinners we were
    // failing. 200rpm is unremarkable and must be enough at OD5.
    let map = beatmap(LONG_SPINNER);
    let turns = 200.0 / 60.0 * 4.0;
    assert_eq!(
        judged(&map, &replay_with(spin_frames(1000, 5000, turns), 0)).count_300,
        1
    );
}

#[test]
fn spinners_do_not_need_a_button_held() {
    // osu!standard spinners are spun, not clicked; requiring a press would
    // fail every honest spinner in every replay.
    let map = beatmap(SPINNER);
    let frames = spin_frames(1000, 2000, 6.0);
    assert!(frames.iter().all(|f| !f.keys.is_pressed()));
    assert_eq!(judged(&map, &replay_with(frames, 0)).count_300, 1);
}

#[test]
fn a_click_during_a_spinner_does_not_reach_the_object_behind_it() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
256,192,1000,12,0,2000
100,100,2500,1,0
",
    );
    let mut frames = spin_frames(1000, 2000, 6.0);
    frames.extend(click(1500, 100.0, 100.0));
    frames.extend(click(2500, 100.0, 100.0));
    frames.sort_by_key(|f| f.time_ms);

    let counts = judged(&map, &replay_with(frames, 0));
    assert_eq!(counts.count_300, 2, "spinner and circle both landed");
    assert_eq!(counts.count_miss, 0);
}

// ── mods ─────────────────────────────────────────────────────────────────

#[test]
fn hard_rock_mirrors_the_playfield() {
    let map = beatmap("[HitObjects]\n100,100,1000,1,0\n");
    let plain = GameState::from_beatmap(&map, Mods::default());
    let hr = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::HARD_ROCK));

    assert_eq!(plain.timeline().objects[0].pos.y, 100.0);
    assert_eq!(hr.timeline().objects[0].pos.y, 284.0, "384 - 100");
    assert_eq!(hr.timeline().objects[0].pos.x, 100.0, "x is untouched");
}

#[test]
fn a_hard_rock_replay_is_judged_against_mirrored_positions() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
",
    );
    let hr = dossier_replay::bits::HARD_ROCK;
    // The player clicked where the mirrored circle actually was.
    let mirrored = replay_with(click(1000, 100.0, 284.0), hr);
    let authored = replay_with(click(1000, 100.0, 100.0), hr);

    assert_eq!(judged(&map, &mirrored).count_300, 1);
    assert_eq!(judged(&map, &authored).count_miss, 1);
}

#[test]
fn hard_rock_tightens_the_windows_it_is_judged_with() {
    let map = beatmap(
        "[Difficulty]\nCircleSize:5\nOverallDifficulty:5\n\n[HitObjects]\n100,100,1000,1,0\n",
    );
    // OD5 -> OD7 under HR, so the 300 window shrinks from 50ms to 38ms.
    let hr = dossier_replay::bits::HARD_ROCK;
    let state = GameState::new(&map, &replay_with(click(1045, 100.0, 284.0), hr));
    assert_eq!(state.judge().unwrap().events()[0].result, Judgement::Ok);

    let plain = GameState::new(&map, &replay_with(click(1045, 100.0, 100.0), 0));
    assert_eq!(plain.judge().unwrap().events()[0].result, Judgement::Great);
}

/// A slider can be shorter than the window its head is judged on. The head is
/// still not a miss until that window shuts — which is *after* the slider has
/// ended, so the tail's combo lands first and the break comes after it.
///
/// Clamping the miss to the slider's end instead reverses those two, and the
/// maximum combo comes out one short. Three replays in the local corpus turn
/// on it; the clearest is a play whose four counts match osu! exactly and
/// whose combo read 111 against a header saying 112.
#[test]
fn a_head_missed_on_a_short_slider_breaks_after_the_tail_lands() {
    // 140 osu!px per beat at 500ms a beat, so 35px is 125ms — inside OD 5's
    // 150ms fifty window.
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:5
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,1000,1,0
300,100,1500,2,0,L|335:100,1,35
",
    );
    // One press, on the circle, and the button never comes back up: the slider
    // gets no press of its own but is tracked the whole way.
    let replay = replay_with(
        frames_over(
            990,
            1700,
            |t| {
                if t < 1200 {
                    (100.0, 100.0)
                } else {
                    (317.0, 100.0)
                }
            },
            |t| t >= 1000,
        ),
        0,
    );

    let state = GameState::new(&map, &replay);
    let judge = state.judge().unwrap();

    let head = judge
        .events_for(1)
        .find(|e| e.part == Part::SliderHead)
        .expect("the slider has a head");
    let tail = judge
        .events_for(1)
        .find(|e| e.part == Part::SliderTail)
        .expect("the slider has a tail");
    assert!(head.result.is_miss(), "no press ever reached the head");
    assert!(!tail.result.is_miss(), "the slider was tracked throughout");
    assert!(
        tail.time_ms < head.time_ms,
        "the tail happens at {}ms and the head's window shuts at {}ms",
        tail.time_ms,
        head.time_ms
    );

    // Circle, then the tail: two before the break, not one.
    assert_eq!(judge.final_state().max_combo, 2);
}

// ── verification against the replay's own header ─────────────────────────

#[test]
fn verification_compares_our_totals_with_the_replays_own() {
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1400, 100.0, 100.0));

    let mut replay = replay_with(frames, 0);
    replay.hits.count_300 = 2;
    replay.max_combo = 2;

    let state = GameState::new(&map, &replay);
    let check = state.verify(&replay).expect("a replay was supplied");
    assert!(check.is_exact(), "{check:?}");

    // Geki and katu are combo-section awards we don't compute. Comparing them
    // would mark every real replay as a mismatch and bury the numbers that do
    // mean something — which it did, on ten replays, until this was fixed.
    replay.hits.count_geki = 42;
    replay.hits.count_katu = 7;
    let with_awards = GameState::new(&map, &replay).verify(&replay).unwrap();
    assert!(with_awards.is_exact(), "{with_awards:?}");

    replay.max_combo = 3;
    let off = GameState::new(&map, &replay).verify(&replay).unwrap();
    assert!(off.counts_match());
    assert!(!off.combo_matches());
}

#[test]
fn a_play_that_ended_early_is_compared_over_the_part_that_happened() {
    // The player dies two notes in. osu! judged two objects and stopped; the
    // other two were never presented. Scoring them anyway turns a clean
    // comparison into two invented misses — which on a real 1127-object map
    // came out 869 of them.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
100,100,1800,1,0
100,100,2200,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1400, 100.0, 100.0));

    let mut replay = replay_with(frames, 0);
    replay.hits.count_300 = 2;
    replay.max_combo = 2;

    let check = GameState::new(&map, &replay)
        .verify(&replay)
        .expect("a replay was supplied");
    assert!(!check.finished(), "{check:?}");
    assert_eq!((check.judged, check.objects), (2, 4));
    assert!(check.is_exact(), "{check:?}");
    assert_eq!(check.ours.count_miss, 0, "{check:?}");
}

#[test]
fn a_header_with_no_counts_at_all_is_not_read_as_a_play_that_ended_early() {
    // Some replays arrive with an empty header — the frames are the whole
    // record. Treating a zero there as "the play reached no objects" would
    // silently compare nothing against nothing and call it exact.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
",
    );
    let replay = replay_with(click(1000, 100.0, 100.0), 0);

    let check = GameState::new(&map, &replay)
        .verify(&replay)
        .expect("a replay was supplied");
    assert!(check.finished(), "{check:?}");
    assert_eq!(check.ours.count_300, 1);
    assert_eq!(
        check.ours.count_miss, 1,
        "the unclicked note is still a miss"
    );
}

// ── slider tracking ──────────────────────────────────────────────────────

/// One 280px slider over two beats at 140px/beat, with a tick in the middle.
const TRACKED_SLIDER: &str = "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:4
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|280:0,1,280
";

#[test]
fn the_follow_circle_only_opens_once_a_slide_has_started() {
    // The rule a per-part check gets wrong. The cursor rides along the slider
    // at 60px away — inside 2.4 radii but well outside the circle itself — and
    // never presses on the head's position. No slide ever starts, so nothing
    // is collected, where checking each part at 2.4 radii would collect the
    // lot.
    let map = beatmap(TRACKED_SLIDER);
    let radius = map.difficulty.circle_radius();
    assert!(
        60.0 > radius && 60.0 < radius * 2.4,
        "the test sits between the two radii"
    );

    let mut frames = Vec::new();
    for step in 0..=20 {
        let t = 1000 + step * 50;
        let x = (step as f32) * 14.0;
        frames.push(frame(t, x, 60.0, 1));
    }
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_300, 0, "no slide was ever established");
}

#[test]
fn a_slide_that_starts_inside_the_circle_keeps_the_wider_tolerance() {
    // Same ride, but the cursor begins on the head. That opens the follow
    // circle, and 60px stays inside it for the rest of the slider.
    let map = beatmap(TRACKED_SLIDER);
    let mut frames = vec![frame(1000, 0.0, 0.0, 1)];
    for step in 1..=20 {
        let t = 1000 + step * 50;
        let x = (step as f32) * 14.0;
        frames.push(frame(t, x, 60.0, 1));
    }
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_300, 1, "the slide carried the whole slider");
}

#[test]
fn a_click_before_the_window_opens_is_recorded_as_a_shake() {
    // It hits nothing, and saying nothing about it would look like dropped
    // input rather than like a player who jumped the gun.
    let map = beatmap(ONE_CIRCLE);
    // The note is at (100, 100) and due at 1000. One press at 600, another
    // on time.
    let frames = frames_over(
        500,
        1100,
        |_| (100.0, 100.0),
        |t| (600..=620).contains(&t) || (1000..=1020).contains(&t),
    );
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("a replay was attached");

    let shakes = judge.shakes();
    assert_eq!(shakes.len(), 1, "{shakes:?}");
    assert_eq!(shakes[0].0, 0, "aimed at the first note");
    assert!(
        (shakes[0].1 - 600.0).abs() < 1.0,
        "at the moment of the click"
    );
    // …and the real click still landed.
    assert_eq!(judge.final_state().counts.count_300, 1);
}

#[test]
fn a_click_on_a_note_that_has_not_appeared_shakes_nothing() {
    // The game can only shake what it is drawing. This test used to click
    // 900ms before a note and expect silence, which was wrong: at AR5 the note
    // has been on screen for 300ms by then and stable shakes it. The note is
    // at 3000 here, so it appears at 1800 and a click at 500 finds nothing.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,3000,1,0
",
    );
    let frames = frames_over(400, 600, |_| (100.0, 100.0), |t| (500..=520).contains(&t));
    let state = GameState::new(&map, &replay_with(frames, 0));
    assert!(state.judge().expect("attached").shakes().is_empty());
}

#[test]
fn a_click_far_out_on_a_visible_note_shakes_it() {
    // Beyond the 400ms it will accept input within, but on screen and under
    // the cursor: the game answers by shaking rather than by consuming it.
    let map = beatmap(ONE_CIRCLE);
    let frames = frames_over(0, 200, |_| (100.0, 100.0), |t| (100..=120).contains(&t));
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("attached");

    assert_eq!(judge.shakes().len(), 1, "{:?}", judge.shakes());
    // Shaken, not taken: the note is still there to be missed on its own time.
    assert_eq!(judge.final_state().counts.count_miss, 1);
}

// ── the lock, per stable's own rule ──────────────────────────────────────

/// Two circles 100ms apart. Closer than the 50 window, so the first is still
/// unjudged when the second is due — at 300ms apart it would already have
/// timed out and there would be nothing left to block with.
const TWO_CIRCLES: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
300,100,1100,1,0
";

#[test]
fn an_unjudged_earlier_note_still_blocks_a_later_one() {
    // The lock's ordinary case: the first note has neither been hit nor timed
    // out, so a click on the second finds nothing.
    let map = beatmap(TWO_CIRCLES);
    let state = GameState::new(&map, &replay_with(click(1100, 300.0, 100.0), 0));
    let counts = state.judge().expect("attached").final_state().counts;

    assert_eq!(counts.count_300, 0, "the click was refused");
    assert_eq!(counts.count_miss, 2, "and both notes ran out");
}

// ── two notes in the same place ──────────────────────────────────────────

/// Two circles on one point, 100ms apart. Stacking lifts the earlier one
/// 3.2px up and left at CS 5, so they overlap almost entirely: (96.8, 96.8)
/// and (100, 100), 4.53px between centres against a 32px radius.
const STACK: &str = "
[General]
StackLeniency: 0.7

[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
100,100,1100,1,0
";

#[test]
fn a_click_inside_both_notes_of_a_stack_takes_the_earlier_one() {
    // The pile is drawn earliest-on-top and judged in time order, so a cursor
    // covering the whole stack reaches the front of it. Taking the nearer
    // centre instead would eat the note the player has not come to yet.
    let map = beatmap(STACK);
    let state = GameState::new(&map, &replay_with(click(1000, 100.0, 100.0), 0));
    let judge = state.judge().expect("attached");

    assert_eq!(
        judge.trace()[0].verdict,
        Verdict::Landed { object: 0 },
        "{:?}",
        judge.trace()
    );
    let counts = judge.final_state().counts;
    assert_eq!((counts.count_300, counts.count_miss), (1, 1));
}

#[test]
fn a_click_only_on_the_later_note_of_a_stack_passes_through_untouched() {
    // Stable's stack exemption:
    //
    // ```csharp
    // if (previousHitObject.HitObject.StackHeight > 0 && !previousHitObject.AllJudged)
    //     return ClickAction.Ignore;
    // ```
    //
    // `Ignore` is neither a hit nor a shake — the click vanishes rather than
    // rattling a pile the player is merely early on.
    //
    // The cursor has to be placed with care for this to mean anything: down
    // and right of the later note, 31.1px from it and 35.6px from the earlier
    // one, so only the later note is under it. A click on the shared middle
    // lands on the earlier note and says nothing about the exemption at all —
    // which is exactly how the previous version of this test passed with the
    // exemption deleted.
    let map = beatmap(STACK);
    let state = GameState::new(&map, &replay_with(click(1100, 122.0, 122.0), 0));
    let judge = state.judge().expect("attached");

    assert_eq!(
        judge.trace()[0].verdict,
        Verdict::Ignored { object: 1 },
        "{:?}",
        judge.trace()
    );
    assert!(judge.shakes().is_empty(), "ignored, not shaken");
    assert_eq!(
        judge.final_state().counts.count_miss,
        2,
        "the click did nothing, so both notes ran out"
    );
}

#[test]
fn once_the_stacks_front_is_judged_the_click_reaches_the_note_behind() {
    // The exemption is about an *unjudged* predecessor. With the front of the
    // pile taken, the note behind it is the front, and a click on its own
    // sliver of circle counts normally.
    let map = beatmap(STACK);
    let mut frames = click(1000, 96.8, 96.8);
    frames.extend(click(1100, 122.0, 122.0));
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_300, 2, "both notes were clicked");
    assert_eq!(counts.count_miss, 0);
}

#[test]
fn a_note_under_a_travelling_slider_never_sees_the_click() {
    // ```csharp
    // slider.HitArea.CanBeHit = () => !slider.DrawableSlider.AllJudged;
    // ```
    //
    // A slider is judged as a whole at its end, so its head keeps a live hit
    // area for the length of the slide. A note underneath it is covered: the
    // head swallows the click and, being judged already, does nothing with it.
    //
    // Only a 2B map puts a note there, which is why this changes nothing on
    // the corpus — but a 2B map should not be judged by accident either.
    let map = beatmap(
        "
[General]
StackLeniency: 0.7

[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,1000,2,0,L|240:100,1,140
100,100,1200,1,0
",
    );
    let mut frames = click(1000, 100.0, 100.0);
    frames.extend(click(1200, 100.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("attached");

    // The slider runs 1000..1500, so at 1200 it is still on the playfield.
    assert_eq!(
        judge.trace()[1].verdict,
        Verdict::Ignored { object: 1 },
        "{:?}",
        judge.trace()
    );
}

#[test]
fn a_note_after_the_slider_has_finished_is_clickable_again() {
    // The other half of the rule: once the slider is judged its hit area goes,
    // and the note that follows on the same spot is ordinary. This is the
    // common case — a circle stacked on a slider's tail — and breaking it
    // would cost real maps rather than 2B ones.
    let map = beatmap(
        "
[General]
StackLeniency: 0.7

[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,1000,2,0,L|240:100,1,140
240,100,1700,1,0
",
    );
    let mut frames: Vec<ReplayFrame> = (990..=1500)
        .step_by(10)
        .map(|t| {
            let x = if t < 1000 {
                100.0
            } else {
                100.0 + (t - 1000) as f32 * 0.28
            };
            frame(t, x, 100.0, if t >= 1000 { Keys::K1 } else { 0 })
        })
        .collect();
    frames.extend(click(1700, 243.2, 103.2));
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_300, 2, "the slider and the note after it");
    assert_eq!(counts.count_miss, 0);
}

#[test]
fn two_notes_at_the_very_same_moment_do_not_block_each_other() {
    // 2B proper. The lock only speaks when the earlier object *ended* before
    // the later one started, with 3ms of slack, so notes sharing an instant
    // are both hittable — and stacking still separates them on screen.
    let map = beatmap(
        "
[General]
StackLeniency: 0.7

[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
100,100,1000,1,0
",
    );
    let mut frames = click(1000, 96.8, 96.8);
    frames.extend(click(1010, 100.0, 100.0));
    let counts = judged(&map, &replay_with(frames, 0));

    assert_eq!(counts.count_300, 2, "both were reachable");
    assert_eq!(counts.count_miss, 0);
}

// ── the trace ────────────────────────────────────────────────────────────

#[test]
fn every_press_is_accounted_for() {
    // The point of the trace: the counts add up to the number of clicks, so a
    // play can be asked which of the ways it went wrong rather than only how
    // much. A press that fell through every branch would be invisible.
    let map = beatmap(TWO_CIRCLES);
    let frames = frames_over(
        900,
        1400,
        |_| (100.0, 100.0),
        |t| (950..=970).contains(&t) || (1000..=1020).contains(&t) || (1300..=1320).contains(&t),
    );
    let state = GameState::new(&map, &replay_with(frames, 0));
    let summary = state.press_verdicts();

    assert_eq!(summary.total(), 3, "one per click: {summary:?}");
    assert_eq!(
        summary.total(),
        state.press_count(),
        "and the same number the click reader found"
    );
}

#[test]
fn a_run_of_refusals_is_reported_with_where_it_began() {
    // Scattered refusals are a player clicking early here and there. A run of
    // them is the lock having lost the thread, and the timestamp is the only
    // thing that says where in the replay to look.
    let map = beatmap(TWO_CIRCLES);
    // The cursor sits on the second note and clicks it over and over while the
    // first is still unjudged, so every one of them is refused.
    let frames = frames_over(1000, 1100, |_| (300.0, 100.0), |t| (t / 10) % 2 == 0);
    let state = GameState::new(&map, &replay_with(frames, 0));
    let summary = state.press_verdicts();

    assert!(summary.refused >= 4, "{summary:?}");
    assert_eq!(summary.landed, 0);
    let (at, count) = *summary
        .refusal_runs
        .first()
        .expect("a run of refusals is reported");
    assert!(count >= 4, "{summary:?}");
    assert!((1000.0..=1100.0).contains(&at), "at {at}");
}

#[test]
fn a_map_with_no_replay_has_nothing_to_account_for() {
    let map = beatmap(TWO_CIRCLES);
    let state = GameState::from_beatmap(&map, Mods::default());
    assert_eq!(state.press_verdicts().total(), 0);
}

// ── which client judged the replay ───────────────────────────────────────

#[test]
fn the_header_version_says_which_ruleset_to_read_the_replay_with() {
    // Anything at 30000000 or above came out of lazer. The corpus has both,
    // and they are not variations on a theme: stable blocks a click outright
    // while an earlier note is unjudged, lazer blocks only a click that
    // arrives before that note was due and writes the note off on the next
    // hit. Judging one by the other's rules is what a 232-miss cascade on a
    // 9-miss replay turned out to be.
    use dossier_sim::Ruleset;
    assert_eq!(Ruleset::of_replay_version(20_260_412), Ruleset::STABLE);
    assert_eq!(Ruleset::of_replay_version(20_231_121), Ruleset::STABLE);
    assert_eq!(Ruleset::of_replay_version(30_000_016), Ruleset::LAZER);
    assert_eq!(Ruleset::of_replay_version(30_000_018), Ruleset::LAZER);
}

/// A player one note behind their own cursor: each click lands inside the
/// next circle rather than the one it was meant for. Circles 40px apart at a
/// 36.48px radius overlap, which is what a stream looks like.
const TRAILING_STREAM: &str = "
[Difficulty]
CircleSize:4
OverallDifficulty:6.5
ApproachRate:9

[HitObjects]
100,100,1000,1,0
140,100,1080,1,0
180,100,1160,1,0
220,100,1240,1,0
";

/// Clicks that arrive late, by which time the cursor has left the note they
/// were meant for: 45px from it, outside the 36.48px radius, and 5px into the
/// next one. This is the shape the Camellia cascade turned out to have.
fn trailing_clicks() -> Vec<ReplayFrame> {
    let mut frames = Vec::new();
    for (at, x) in [(1040, 145.0), (1120, 185.0), (1200, 225.0)] {
        frames.push(frame(at - 10, x, 100.0, 0));
        frames.push(frame(at, x, 100.0, Keys::K1));
        frames.push(frame(at + 10, x, 100.0, 0));
    }
    frames
}

#[test]
fn stable_locks_the_stream_and_lazer_lets_it_through() {
    // The same replay under the two rulesets, which is the whole point of
    // telling them apart. Stable refuses every click after the first note is
    // stranded — the lock never lets the player back in. Lazer writes the
    // stranded note off and carries on.
    let map = beatmap(TRAILING_STREAM);

    let mut stable = replay_with(trailing_clicks(), 0);
    stable.game_version = 20_260_412;
    let stable_counts = judged(&map, &stable);

    let mut lazer = replay_with(trailing_clicks(), 0);
    lazer.game_version = 30_000_018;
    let lazer_counts = judged(&map, &lazer);

    assert_eq!(
        (stable_counts.count_300, stable_counts.count_miss),
        (0, 4),
        "stable strands the first note and the lock never lets the player back          in: {stable_counts:?}"
    );
    assert_eq!(
        (lazer_counts.count_300, lazer_counts.count_miss),
        (3, 1),
        "lazer writes the stranded note off and carries on: {lazer_counts:?}"
    );
}

#[test]
fn lazer_writes_off_a_stranded_note_at_the_click_not_at_its_window() {
    // `StartTimeOrderedHitPolicy.HandleHit` misses everything unjudged behind
    // the note that was hit, there and then. The difference is only ever
    // *when* — but when is what a combo is made of: notes clicked after the
    // stranded one and before its window ran out would otherwise count into
    // the run first, and the maximum comes out too high.
    let map = beatmap(TRAILING_STREAM);
    let mut lazer = replay_with(trailing_clicks(), 0);
    lazer.game_version = 30_000_018;

    let state = GameState::new(&map, &lazer);
    let judge = state.judge().expect("attached");
    let miss = judge
        .events()
        .iter()
        .find(|e| e.result == Judgement::Miss)
        .expect("one note was stranded");
    let object = &state.timeline().objects[miss.object_index];

    assert!(
        miss.time_ms < object.start_ms + state.difficulty().hit_window_50(),
        "written off at {:.0}ms, before its window shut at {:.0}ms",
        miss.time_ms,
        object.start_ms + state.difficulty().hit_window_50()
    );
}

#[test]
fn a_slider_swallows_clicks_from_the_moment_it_spawns() {
    // `CanBeHit = () => !AllJudged` is about the object's whole life, and an
    // object's life starts when it *spawns*, not when it is due. A slider
    // whose head was clicked early counts as judged to the note lock, yet it
    // is on the playfield with a live hit area — so a further click landing on
    // it is swallowed rather than passed to whatever comes next.
    //
    // The press below arrives before the slider is even due, which is the case
    // that distinguishes the rule: on `yax03 - down` such a click, 362ms ahead
    // of the following note, was handed to that note and eaten as an early
    // miss, costing a 2687-link run 352 of its links.
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,1000,2,0,L|240:100,1,140
130,100,1300,1,0
",
    );
    // The head goes early, at 980. The second press at 990 is still before the
    // slider is due, and the cursor sits 15px from both the slider head and
    // the circle at 1300 — so something has to decide which one hears it.
    let mut frames = click(980, 100.0, 100.0);
    frames.extend(click(990, 115.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("attached");

    assert_eq!(
        judge.trace()[1].verdict,
        Verdict::Ignored { object: 1 },
        "the slider is on screen and unjudged, so it swallows the press: {:?}",
        judge.trace()
    );
    // And the circle is not consumed by that press: it lives out its own
    // window and is only written off when that shuts, at 1300 + 150.
    let circle = judge
        .events()
        .iter()
        .find(|e| e.object_index == 1)
        .expect("the circle is judged");
    assert_eq!(
        circle.time_ms, 1450.0,
        "eaten at the press instead of running its window out: {circle:?}"
    );
}

// ── when a missed note stops standing in the way ─────────────────────────

#[test]
fn a_note_keeps_blocking_for_two_milliseconds_after_its_window_shuts() {
    // The rule that closed Chambarising. A note whose fifty window has just
    // run out is still in the game's way, because the game has not yet been
    // round to write it off: clicks are offered to the objects first and the
    // misses are swept afterwards, and the comparison that sweeps them is
    // strict. Two milliseconds, from two separate off-by-ones.
    //
    // The map: one note nobody touches, then a second one 200ms later. The
    // click is aimed squarely at the second, and whether it lands depends
    // entirely on whether the first is still blocking.
    //
    // OD 5, so the fifty window is 150ms and the first note's window shuts at
    // 1150. Both notes are far enough apart that only the lock can refuse
    // anything, and far enough in space that a click on one is nowhere near
    // the other.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n\
         [HitObjects]\n100,100,1000,1,0\n400,300,1200,1,0\n",
    );
    let landed = |at: i64| {
        judged(&map, &replay_with(click(at, 400.0, 300.0), 0)).count_300
            + judged(&map, &replay_with(click(at, 400.0, 300.0), 0)).count_100
            + judged(&map, &replay_with(click(at, 400.0, 300.0), 0)).count_50
    };

    // The first note's window shuts at 1150. At 1151 it is still standing in
    // the way — one millisecond is not enough, because the game's own test is
    // `time > start + window`.
    assert_eq!(landed(1151), 0, "the blocker was freed a millisecond early");
    // At 1152 the game has had an update it could write the note off on, and
    // the click goes through.
    assert_eq!(landed(1152), 1, "the blocker was never freed at all");
}

#[test]
fn a_click_on_a_note_whose_window_has_shut_spends_it_there_and_then() {
    // The other half. Because a note can still be reached after its window has
    // gone, something has to happen when a click reaches one — and what osu!
    // does is judge it a miss on the spot rather than let it sit and be swept
    // later:
    //
    // ```go
    // } else if int64(delta) < player.diff.Hit50 { return Hit50 }
    // return Miss
    // ```
    //
    // The difference is visible: the miss is dated to the click, not to the
    // end of the window, which is where the player sees it happen.
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::new(&map, &replay_with(click(1151, 100.0, 100.0), 0));
    let judge = state.judge().expect("judged");
    let event = judge.events()[0];

    assert_eq!(event.result, Judgement::Miss);
    assert_eq!(event.time_ms, 1151.0, "dated to the click");
    assert_eq!(event.error_ms, Some(151.0), "and it knows how late it was");
}

// ── what a slider is worth, which the two clients disagree about ─────────

#[test]
fn a_slider_is_worth_its_head_in_lazer_and_its_pieces_in_stable() {
    // The same play, the same slider, two different verdicts — and not a
    // rounding difference: a whole tier apart.
    //
    // lazer took the slider apart. Its head is an ordinary circle on ordinary
    // windows, its pieces are judgements in their own right, and the slider
    // itself is worth nothing at all. So the number that reaches the
    // scoreboard is the head's, and a slider tracked flawlessly from a head
    // hit sixty milliseconds late is a 100.
    //
    // stable keeps the slider whole: the head is a flat thirty points whenever
    // it lands, and the verdict comes from how much of the slider was caught.
    // Everything caught is a 300, however late the head was.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\
         SliderMultiplier:1.0\nSliderTickRate:1\n\n\
         [TimingPoints]\n0,500,4,2,0,100,1,0\n\n\
         [HitObjects]\n100,100,1000,2,0,L|200:100,1,100\n",
    );

    // Press 60ms late — outside the 50ms three-hundred window, inside the
    // hundred — then hold and follow the ball to the end.
    let mut frames = Vec::new();
    for t in (1050..=1600).step_by(10) {
        let progress = ((t - 1060) as f32 / 500.0).clamp(0.0, 1.0);
        frames.push(frame(
            t,
            100.0 + 100.0 * progress,
            100.0,
            if (1060..=1560).contains(&t) { Keys::K1 } else { 0 },
        ));
    }

    let stable = replay_with(frames.clone(), 0);
    let mut lazer = replay_with(frames, 0);
    lazer.game_version = 30_000_016;

    let verdict = |replay: &dossier_replay::Replay| {
        let state = GameState::new(&map, replay);
        let judge = state.judge().expect("judged");
        let slider = judge
            .events()
            .iter()
            .find(|e| e.part == Part::Slider)
            .expect("the slider was judged");
        // The premise: the head landed, late, and nothing was dropped.
        assert!(
            !judge
                .events()
                .iter()
                .any(|e| e.part != Part::Slider && e.result.is_miss()),
            "{:?}",
            judge.events()
        );
        slider.result
    };

    assert_eq!(verdict(&stable), Judgement::Great, "stable keeps it whole");
    assert_eq!(verdict(&lazer), Judgement::Ok, "lazer scores the head");
}

#[test]
fn landing_a_late_head_starts_the_slide_in_lazer_but_not_in_stable() {
    // `SliderInputManager.PostProcessHeadJudgement` hands the slide over from
    // a landed head using the *expanded* follow area, not the ball itself:
    //
    // ```csharp
    // if (!head.Judged || !head.Result.IsHit) return;
    // if (!IsMouseInFollowArea(true)) return;
    // ```
    //
    // On a fast slider hit late the ball has already left by the time the
    // click is judged, and demanding the cursor be back on top of it drops a
    // slider the player is plainly holding.
    //
    // The map: a slider travelling at one osu!pixel a millisecond. The click
    // lands fifty milliseconds late and the cursor then trails the ball by
    // exactly fifty pixels for the rest of it — always inside the follow
    // circle at 76.8, never inside the ball at 32. So tracking either starts
    // at the head or it never starts at all, and the tail says which.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\
         SliderMultiplier:2.0\nSliderTickRate:1\n\n\
         [TimingPoints]\n0,200,4,2,0,100,1,0\n\n\
         [HitObjects]\n100,100,1000,2,0,L|300:100,1,200\n",
    );

    let mut frames = Vec::new();
    for t in (1040..=1260).step_by(5) {
        let x = 100.0 + (t as f32 - 1050.0).max(0.0);
        frames.push(frame(
            t,
            x,
            100.0,
            if (1050..=1250).contains(&t) { Keys::K1 } else { 0 },
        ));
    }

    let tail_landed = |version: i32| {
        let mut replay = replay_with(frames.clone(), 0);
        replay.game_version = version;
        let state = GameState::new(&map, &replay);
        let judge = state.judge().expect("judged");
        // The premise: the head landed, late enough to be a hundred.
        let head = judge
            .events()
            .iter()
            .find(|e| e.part == Part::SliderHead)
            .expect("a head");
        assert!(!head.result.is_miss(), "the fixture must land the head");
        !judge
            .events()
            .iter()
            .find(|e| e.part == Part::SliderTail)
            .expect("a tail")
            .result
            .is_miss()
    };

    assert!(tail_landed(30_000_016), "lazer should carry the slide over");
    assert!(
        !tail_landed(20_260_101),
        "stable has no such rule, and giving it one costs the corpus half its \
         exact replays"
    );
}

// ── stable's ScoreV2 ─────────────────────────────────────────────────────

/// A slider tracked from end to end, off a head clicked `late_by` too late.
fn score_v2_slider(late_by: i64, hold_until: i64, mods: u32) -> HitCounts {
    let map = beatmap(SHORT_SLIDER);
    let frames = frames_over(
        900,
        1600,
        |t| (ball_x(t, 1000.0, 500.0), 0.0),
        |t| (1000 + late_by..hold_until).contains(&t),
    );
    judged(&map, &replay_with(frames, mods))
}

#[test]
fn score_v2_makes_a_stable_slider_worth_what_its_head_was_worth() {
    // OD5 gives a 50ms three-hundred window and a 100ms hundred window, so a
    // head 60ms late is a 100 on the windows while the slide itself is perfect
    // either way.
    //
    // Without the mod stable assembles the verdict from the pieces and every
    // piece was caught, so a late head still buys a 300. With it the head is
    // the verdict.
    let plain = score_v2_slider(60, 1600, 0);
    assert_eq!((plain.count_300, plain.count_100), (1, 0), "{plain:?}");

    let v2 = score_v2_slider(60, 1600, dossier_replay::bits::SCORE_V2);
    assert_eq!((v2.count_300, v2.count_100), (0, 1), "{v2:?}");
}

#[test]
fn score_v2_still_wants_the_pieces_after_the_head_is_in() {
    // The other half, and the half that mattered: a head well inside the
    // three-hundred window on a slider that let go of its tail. Under the head
    // alone this is a 300 and the replay says 100 — twenty-one of them on one
    // map. The verdict is the worse of the two readings, not the head's.
    let dropped = score_v2_slider(0, 1200, dossier_replay::bits::SCORE_V2);
    assert_eq!((dropped.count_300, dropped.count_100), (0, 1), "{dropped:?}");

    // And a slider that keeps everything is untouched by the mod.
    let whole = score_v2_slider(0, 1600, dossier_replay::bits::SCORE_V2);
    assert_eq!((whole.count_300, whole.count_100), (1, 0), "{whole:?}");
}

#[test]
fn the_unstable_rate_is_ten_times_the_spread_of_the_errors() {
    // Three notes struck at −20, 0 and +20 against their own moments. The mean
    // is zero and the population deviation is √(800/3) ≈ 16.33, so the figure
    // quoted — ten times it, because it is stated in tenths of a millisecond —
    // is about 163.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
200,100,2000,1,0
300,100,3000,1,0
",
    );
    let mut frames = Vec::new();
    for (at, x, offset) in [(1000i64, 100.0f32, -20i64), (2000, 200.0, 0), (3000, 300.0, 20)] {
        let click = at + offset;
        frames.push(frame(click - 5, x, 100.0, 0));
        frames.push(frame(click, x, 100.0, Keys::K1));
        frames.push(frame(click + 5, x, 100.0, 0));
    }
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("a replay was judged");

    let rate = judge.unstable_rate(f64::MAX).expect("three hits have a spread");
    let expected = (800.0f64 / 3.0).sqrt() * 10.0;
    assert!(
        (rate - expected).abs() < 1.0,
        "{rate:.1} against {expected:.1}"
    );
}

#[test]
fn the_unstable_rate_waits_for_a_second_hit() {
    // One error has no spread, and quoting zero would read as a perfect play
    // rather than as an unanswered question.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
OverallDifficulty:5

[HitObjects]
100,100,1000,1,0
200,100,2000,1,0
",
    );
    let mut frames = Vec::new();
    for (at, x) in [(1000i64, 100.0f32), (2000, 200.0)] {
        frames.push(frame(at - 5, x, 100.0, 0));
        frames.push(frame(at, x, 100.0, Keys::K1));
        frames.push(frame(at + 5, x, 100.0, 0));
    }
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("a replay was judged");

    assert!(judge.unstable_rate(1500.0).is_none(), "one hit is not a spread");
    assert!(judge.unstable_rate(2500.0).is_some(), "two are");
}

#[test]
fn a_live_spinner_takes_a_press_wherever_the_cursor_is() {
    // stable's spinner answers its hittability test with the time gates alone —
    // the implementation in the client uses neither the cursor position nor the
    // radius — so while it is live it says yes to any press, and being earlier
    // in the list it takes that press before the circle behind it can.
    //
    // The click has to be one that would otherwise land, or the test proves
    // nothing: OD5 puts the circle's fifty window at 150ms, so 2150 is a
    // comfortable hit on a circle due at 2200. The spinner is still turning.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\nApproachRate:5\n\n\
         [HitObjects]\n256,192,1000,12,0,2300\n400,300,2200,1,0\n",
    );

    let mut stable = replay_with(click(2150, 400.0, 300.0), 0);
    stable.game_version = 20_260_412;
    let stable_counts = judged(&map, &stable);

    let mut lazer = replay_with(click(2150, 400.0, 300.0), 0);
    lazer.game_version = 30_000_018;
    let lazer_counts = judged(&map, &lazer);

    let stable_landed =
        stable_counts.count_300 + stable_counts.count_100 + stable_counts.count_50;
    let lazer_landed = lazer_counts.count_300 + lazer_counts.count_100 + lazer_counts.count_50;
    assert_eq!(
        stable_landed, 0,
        "the spinner should have swallowed it: {stable_counts:?}"
    );
    assert!(
        lazer_landed > 0,
        "lazer has no such rule and the click reaches the circle: {lazer_counts:?}"
    );
}

#[test]
fn a_relax_replay_is_clicked_for_rather_than_read() {
    // A Relax replay records the cursor and nothing else: the game does the
    // clicking and does not write it into the file. On the replay that showed
    // this up — 2861 objects — there is exactly one press in the whole
    // recording, against 550 in an ordinary replay of similar length. Read as
    // written, every note on the map misses.
    //
    // Here: two circles the cursor sits squarely on, and a replay with no key
    // ever held. Without the mod that is two misses; with it the game clicks.
    let map = beatmap(TWO_CIRCLES);
    let frames = vec![
        dossier_replay::ReplayFrame { time_ms: 900, x: 100.0, y: 100.0, keys: dossier_replay::Keys(0) },
        dossier_replay::ReplayFrame { time_ms: 1000, x: 100.0, y: 100.0, keys: dossier_replay::Keys(0) },
        dossier_replay::ReplayFrame { time_ms: 1100, x: 300.0, y: 100.0, keys: dossier_replay::Keys(0) },
        dossier_replay::ReplayFrame { time_ms: 1200, x: 300.0, y: 100.0, keys: dossier_replay::Keys(0) },
    ];

    let mut plain = replay_with(frames.clone(), 0);
    plain.game_version = 20_260_412;
    let plain_counts = judged(&map, &plain);
    assert_eq!(
        plain_counts.count_miss, 2,
        "nobody pressed anything: {plain_counts:?}"
    );

    let mut relaxed = replay_with(frames, dossier_replay::bits::RELAX);
    relaxed.game_version = 20_260_412;
    let relax_counts = judged(&map, &relaxed);
    assert_eq!(
        relax_counts.count_miss, 0,
        "the game should have clicked for them: {relax_counts:?}"
    );
}

#[test]
fn a_relax_slider_is_held_as_well_as_clicked() {
    // The game does the holding too, and records that no more than it records
    // the clicking. A slider read from the file is therefore never held: it
    // drops every tick and tail it has and breaks combo on each. On the
    // corpus's worst Relax replay that was a maximum combo of 34 against a
    // header of 2767, on a play the game scored at 99%.
    //
    // The cursor still decides. Here it follows the slider exactly, with no
    // key ever down.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\nApproachRate:5\n\
         SliderMultiplier:1\nSliderTickRate:4\n\n[TimingPoints]\n0,500,4,2,0,60,1,0\n\n\
         [HitObjects]\n100,100,1000,2,0,L|300:100,1,200\n",
    );
    let mut frames = Vec::new();
    for step in 0..90 {
        let t = 900 + step * 25;
        let travel = ((t - 1000).max(0) as f64 / 1000.0).min(1.0);
        frames.push(dossier_replay::ReplayFrame {
            time_ms: t,
            x: (100.0 + 200.0 * travel) as f32,
            y: 100.0,
            keys: dossier_replay::Keys(0),
        });
    }

    let mut relaxed = replay_with(frames, dossier_replay::bits::RELAX);
    relaxed.game_version = 20_260_412;
    let counts = judged(&map, &relaxed);
    // Not merely "not a miss": a slider whose parts were dropped is still
    // judged, just judged worse, so the miss count says nothing. Held, this
    // fixture collects enough of the slider for a 100; unheld it falls to a 50,
    // and that is the difference the corpus multiplies by every slider on
    // every Relax map.
    assert_eq!(
        (counts.count_50, counts.count_miss),
        (0, 0),
        "the slider was followed and should have been held throughout: {counts:?}"
    );
}

// ── lazer asks one note, and only certain notes ──────────────────────────

/// Three circles far enough apart in time that a click can arrive before all
/// of them, and far enough apart in space that only the last is under it.
const THREE_AHEAD: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
200,100,1100,1,0
300,100,1200,1,0
";

#[test]
fn lazer_asks_the_last_note_behind_the_target_not_the_first() {
    // `StartTimeOrderedHitPolicy` keeps overwriting one variable as it walks:
    //
    // ```csharp
    // foreach (var obj in enumerateHitObjectsUpTo(hitObject.HitObject.StartTime))
    //     if (hitObjectCanBlockFutureHits(obj))
    //         blockingObject = obj;
    // ```
    //
    // so what it ends up testing is the *last* note before the target, and no
    // other. This engine used to answer with the first one that qualified,
    // which names the wrong note in a refusal — and a refusal is read
    // backwards, from the click that was refused to the note nobody judged.
    //
    // Both readings refuse this click, so the counts cannot tell them apart.
    // The name can.
    let map = beatmap(THREE_AHEAD);
    let mut replay = replay_with(click(950, 300.0, 100.0), 0);
    replay.game_version = 30_000_018;
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("attached");

    assert_eq!(
        judge.trace()[0].verdict,
        Verdict::Refused {
            object: 2,
            blocked_by: 1,
        },
        "the note immediately behind the target, not the one before it: {:?}",
        judge.trace()
    );
}

/// A spinner sitting between a note whose moment has passed and the target.
const SPINNER_BETWEEN: &str = "
[Difficulty]
CircleSize:5
OverallDifficulty:5
ApproachRate:5

[HitObjects]
100,100,1000,1,0
256,192,1050,12,0,1150
300,100,1200,1,0
";

#[test]
fn a_spinner_cannot_be_what_blocks_a_note_under_lazer() {
    // `hitObjectCanBlockFutureHits` is one line — `hitObject is
    // DrawableHitCircle` — so a spinner is never the blocking object. A
    // slider's head is one, since `DrawableSliderHead` derives from it, but a
    // spinner has no such part.
    //
    // The click lands after the first circle was due, so that one cannot block
    // it, and before the spinner starts, so under the old reading the spinner
    // could. Under lazer's own rule the enquiry never reaches the spinner, and
    // the last thing that *can* block is the circle at 1000 — which does not.
    let map = beatmap(SPINNER_BETWEEN);
    let mut replay = replay_with(click(1020, 300.0, 100.0), 0);
    replay.game_version = 30_000_018;
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("attached");

    assert!(
        !matches!(judge.trace()[0].verdict, Verdict::Refused { .. }),
        "a spinner is not a blocking object: {:?}",
        judge.trace()
    );
}

