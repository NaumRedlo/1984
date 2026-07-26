//! Judgement tests.
//!
//! Maps use the default CS 5 (radius 32 osu!px, follow circle 76.8) and OD 5
//! (windows 50 / 100 / 150 ms) unless a test says otherwise, so the numbers in
//! the assertions can be read directly.

use std::f64::consts::TAU;

use dossier_beatmap::Beatmap;
use dossier_replay::{HitCounts, Keys, Mods, Replay, ReplayFrame};
use dossier_sim::{GameState, Judgement, Part};

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
fn a_click_before_the_window_is_ignored_rather_than_consumed() {
    let map = beatmap(ONE_CIRCLE);
    let mut frames = click(800, 100.0, 100.0);
    frames.extend(click(1000, 100.0, 100.0));
    // The early click must not eat the object; the real one still counts.
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
