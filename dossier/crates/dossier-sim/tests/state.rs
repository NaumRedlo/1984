//! GameState tests.
//!
//! Maps are written inline with numbers chosen so the expected timings come out
//! round: 500ms beats, SliderMultiplier 1.4 (so 140 osu!px per beat), and
//! slider lengths that are whole multiples of that.

use dossier_beatmap::Beatmap;
use dossier_replay::{Keys, Mods, Replay, ReplayFrame};
use dossier_sim::{GameState, TimedKind};

const EPS: f64 = 1e-6;

fn beatmap(body: &str) -> Beatmap {
    Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("test map should parse")
}

/// A replay carrying nothing but frames — the header fields don't matter here.
fn replay_with(frames: Vec<ReplayFrame>, mods: u32) -> Replay {
    Replay {
        mode: dossier_replay::GameMode::Standard,
        game_version: 20_260_101,
        beatmap_hash: String::new(),
        player: "tester".into(),
        replay_hash: String::new(),
        hits: Default::default(),
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

// ── slider timing ────────────────────────────────────────────────────────

/// 500ms per beat, SliderMultiplier 1.4 -> 140 osu!px per beat. A 140px slider
/// therefore takes exactly one beat.
const TIMED_MAP: &str = "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|140:0,1,140
";

#[test]
fn a_slider_lasts_its_length_in_beats() {
    let map = beatmap(TIMED_MAP);
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];

    let TimedKind::Slider {
        slide_duration_ms, ..
    } = &slider.kind
    else {
        panic!("expected a slider");
    };
    assert!((slide_duration_ms - 500.0).abs() < EPS, "one beat");
    assert!((slider.end_ms - 1500.0).abs() < EPS, "start + one beat");
}

#[test]
fn repeats_multiply_the_span_but_not_the_traversal() {
    let map = beatmap(
        "
[Difficulty]
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
0,0,1000,2,0,L|140:0,3,140
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];

    let TimedKind::Slider {
        slide_duration_ms,
        slides,
        ..
    } = &slider.kind
    else {
        panic!("expected a slider");
    };
    assert_eq!(*slides, 3);
    assert!((slide_duration_ms - 500.0).abs() < EPS, "one traversal");
    assert!((slider.duration_ms() - 1500.0).abs() < EPS, "three of them");
}

#[test]
fn a_green_line_speeds_the_slider_up() {
    // -50 encodes SV 2.0, so the same 140px takes half a beat.
    let map = beatmap(
        "
[Difficulty]
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0
900,-50,4,2,0,60,0,0

[HitObjects]
0,0,1000,2,0,L|140:0,1,140
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let TimedKind::Slider {
        slide_duration_ms, ..
    } = &state.timeline().objects[0].kind
    else {
        panic!("expected a slider");
    };
    assert!(
        (slide_duration_ms - 250.0).abs() < EPS,
        "half a beat at SV2"
    );
}

#[test]
fn the_ball_walks_the_path_over_the_slider_span() {
    let map = beatmap(TIMED_MAP);
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];

    let start = slider.ball_at(1000.0).expect("ball at the start");
    let mid = slider.ball_at(1250.0).expect("ball halfway");
    let end = slider.ball_at(1500.0).expect("ball at the end");

    assert!((start.x - 0.0).abs() < 0.5);
    assert!((mid.x - 70.0).abs() < 0.5, "half of 140px");
    assert!((end.x - 140.0).abs() < 0.5);

    // Outside the span there is no ball to draw.
    assert!(slider.ball_at(999.0).is_none());
    assert!(slider.ball_at(1501.0).is_none());
}

#[test]
fn a_circle_has_no_duration_and_no_ball() {
    let map = beatmap("[HitObjects]\n100,100,2000,1,0\n");
    let state = GameState::from_beatmap(&map, Mods::default());
    let circle = &state.timeline().objects[0];

    assert_eq!(circle.duration_ms(), 0.0);
    assert!(circle.ball_at(2000.0).is_none());
}

#[test]
fn a_spinner_spans_to_its_stated_end() {
    let map = beatmap("[HitObjects]\n0,0,1000,12,0,4000\n");
    let state = GameState::from_beatmap(&map, Mods::default());
    assert_eq!(state.timeline().objects[0].duration_ms(), 3000.0);
}

#[test]
fn a_slider_on_a_map_with_no_timing_is_instant_rather_than_infinite() {
    let map = beatmap("[HitObjects]\n0,0,1000,2,0,L|140:0,1,140\n");
    let state = GameState::from_beatmap(&map, Mods::default());
    let slider = &state.timeline().objects[0];
    assert!(slider.duration_ms().is_finite());
    assert_eq!(slider.duration_ms(), 0.0);
}

// ── visibility ───────────────────────────────────────────────────────────

#[test]
fn objects_appear_one_preempt_before_they_are_due() {
    // AR5 -> 1200ms preempt.
    let map = beatmap("[Difficulty]\nApproachRate:5\n\n[HitObjects]\n0,0,5000,1,0\n");
    let state = GameState::from_beatmap(&map, Mods::default());

    assert_eq!(state.update(3799.0).objects.len(), 0, "not spawned yet");
    assert_eq!(state.update(3801.0).objects.len(), 1, "just spawned");
    assert_eq!(state.update(5000.0).objects.len(), 1, "due now");
    assert_eq!(state.update(5001.0).objects.len(), 0, "gone");
}

#[test]
fn approach_runs_from_zero_at_spawn_to_one_when_due() {
    let map = beatmap("[Difficulty]\nApproachRate:5\n\n[HitObjects]\n0,0,5000,1,0\n");
    let state = GameState::from_beatmap(&map, Mods::default());

    let at = |t: f64| state.update(t).objects[0].approach;
    assert!((at(3800.0) - 0.0).abs() < 1e-3, "spawn");
    assert!((at(4400.0) - 0.5).abs() < 1e-3, "halfway");
    assert!((at(5000.0) - 1.0).abs() < 1e-3, "due");
}

#[test]
fn a_slider_stays_visible_while_it_is_being_played() {
    let map = beatmap(TIMED_MAP);
    let state = GameState::from_beatmap(&map, Mods::default());
    // Due at 1000, running to 1500.
    assert_eq!(state.update(1400.0).objects.len(), 1);
    assert!(state.update(1400.0).objects[0].ball.is_some());
    assert_eq!(state.update(1600.0).objects.len(), 0);
}

// ── mods ─────────────────────────────────────────────────────────────────

#[test]
fn hard_rock_tightens_the_difficulty() {
    let map = beatmap("[Difficulty]\nApproachRate:5\nOverallDifficulty:5\nCircleSize:4\n");
    let plain = GameState::from_beatmap(&map, Mods::default());
    let hr = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::HARD_ROCK));

    assert_eq!(hr.difficulty().approach_rate, 7.0); // 5 * 1.4
    assert!((hr.difficulty().circle_size - 5.2).abs() < EPS); // 4 * 1.3
    assert!(hr.difficulty().preempt_ms() < plain.difficulty().preempt_ms());
    assert!(hr.difficulty().circle_radius() < plain.difficulty().circle_radius());
}

#[test]
fn hard_rock_caps_at_ten() {
    let map = beatmap("[Difficulty]\nApproachRate:9\nOverallDifficulty:9\n");
    let hr = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::HARD_ROCK));
    assert_eq!(hr.difficulty().approach_rate, 10.0); // not 12.6
    assert_eq!(hr.difficulty().overall_difficulty, 10.0);
}

#[test]
fn easy_halves_the_difficulty() {
    let map = beatmap("[Difficulty]\nApproachRate:8\nOverallDifficulty:8\n");
    let ez = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::EASY));
    assert_eq!(ez.difficulty().approach_rate, 4.0);
}

#[test]
fn doubletime_changes_the_playback_rate_not_the_timeline() {
    // DT plays the same map faster; it does not move the notes, so object
    // times stay put and only the clock the encoder runs on changes.
    let map = beatmap(TIMED_MAP);
    let plain = GameState::from_beatmap(&map, Mods::default());
    let dt = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::DOUBLE_TIME));

    assert_eq!(dt.playback_rate(), 1.5);
    assert_eq!(plain.playback_rate(), 1.0);
    assert_eq!(
        dt.timeline().objects[0].start_ms,
        plain.timeline().objects[0].start_ms
    );
}

// ── cursor ───────────────────────────────────────────────────────────────

#[test]
fn the_cursor_is_interpolated_between_frames() {
    let map = beatmap("[HitObjects]\n0,0,1000,1,0\n");
    let replay = replay_with(vec![frame(0, 0.0, 0.0, 0), frame(100, 100.0, 200.0, 0)], 0);
    let state = GameState::new(&map, &replay);

    let cursor = state.update(50.0).cursor.expect("mid-frame cursor");
    assert!((cursor.pos.x - 50.0).abs() < EPS, "halfway in x");
    assert!((cursor.pos.y - 100.0).abs() < EPS, "halfway in y");
}

#[test]
fn key_state_is_held_not_blended() {
    // Interpolating a bitmask would invent presses that never happened.
    let map = beatmap("[HitObjects]\n0,0,1000,1,0\n");
    let replay = replay_with(
        vec![frame(0, 0.0, 0.0, 0), frame(100, 0.0, 0.0, Keys::K1)],
        0,
    );
    let state = GameState::new(&map, &replay);

    assert!(!state.update(99.0).cursor.unwrap().keys.is_pressed());
    assert!(state.update(100.0).cursor.unwrap().keys.is_pressed());
}

#[test]
fn outside_the_recording_the_nearest_end_is_held() {
    let map = beatmap("[HitObjects]\n0,0,1000,1,0\n");
    let replay = replay_with(
        vec![frame(500, 10.0, 20.0, 0), frame(600, 90.0, 80.0, 0)],
        0,
    );
    let state = GameState::new(&map, &replay);

    let before = state.update(0.0).cursor.unwrap();
    assert!((before.pos.x - 10.0).abs() < EPS, "held at the first frame");
    let after = state.update(9999.0).cursor.unwrap();
    assert!((after.pos.x - 90.0).abs() < EPS, "held at the last frame");
}

#[test]
fn sequential_and_random_access_agree() {
    // The track keeps a hint for forward playback; it must not disagree with a
    // cold lookup.
    let map = beatmap("[HitObjects]\n0,0,1000,1,0\n");
    let frames: Vec<_> = (0..200)
        .map(|i| frame(i * 16, i as f32, (i * 2) as f32, 0))
        .collect();
    let replay = replay_with(frames, 0);
    let state = GameState::new(&map, &replay);

    let forward: Vec<f64> = (0..400)
        .map(|i| state.update(f64::from(i) * 8.0).cursor.unwrap().pos.x)
        .collect();

    // Same queries, but on a track whose hint has been dragged backwards first —
    // a seek in the timeline, which is the case the hint could get wrong.
    let seeking = GameState::new(&map, &replay);
    let sought: Vec<f64> = (0..400)
        .map(|i| {
            seeking.update(f64::from(399 - i) * 8.0);
            seeking.update(f64::from(i) * 8.0).cursor.unwrap().pos.x
        })
        .collect();
    assert_eq!(forward, sought);
}

#[test]
fn a_map_with_no_replay_has_no_cursor() {
    let map = beatmap("[HitObjects]\n0,0,1000,1,0\n");
    let state = GameState::from_beatmap(&map, Mods::default());
    assert!(state.update(1000.0).cursor.is_none());
}

// ── span ─────────────────────────────────────────────────────────────────

#[test]
fn the_render_span_covers_the_lead_in_and_the_whole_replay() {
    let map = beatmap("[Difficulty]\nApproachRate:5\n\n[HitObjects]\n0,0,5000,1,0\n");
    let replay = replay_with(vec![frame(-2000, 0.0, 0.0, 0), frame(9000, 0.0, 0.0, 0)], 0);
    let state = GameState::new(&map, &replay);

    let (from, to) = state.span_ms();
    assert_eq!(from, -2000.0, "the replay starts before the first spawn");
    assert_eq!(to, 9000.0, "and runs past the last object");
}

/// Four notes, two played, and the header saying so.
fn a_play_that_died_halfway() -> (Beatmap, Replay) {
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:5

[HitObjects]
100,100,1000,1,0
100,100,1400,1,0
100,100,1800,1,0
100,100,2200,1,0
",
    );
    let mut frames = Vec::new();
    for at in [1000, 1400] {
        frames.push(frame(at - 10, 100.0, 100.0, 0));
        frames.push(frame(at, 100.0, 100.0, Keys::K1));
        frames.push(frame(at + 10, 100.0, 100.0, 0));
    }
    // The recording runs on past the last thing the player did — stable keeps
    // writing frames while the health drains, and this is where cutting the
    // play at its last frame would go wrong.
    frames.push(frame(3000, 100.0, 100.0, 0));

    let mut replay = replay_with(frames, 0);
    replay.hits.count_300 = 2;
    replay.max_combo = 2;
    (map, replay)
}

#[test]
fn a_play_that_ended_early_ends_the_render_span_with_it() {
    // Otherwise the video runs on over a map with no player in it: no cursor,
    // no judgements, a frozen HUD. On the run this came from, for two minutes.
    let (map, replay) = a_play_that_died_halfway();
    let state = GameState::new(&map, &replay);

    assert_eq!(state.objects_played(), 2);
    let ending = state.ending().expect("this play ended early");
    assert_eq!(ending.time_ms, 1400.0, "the last judgement it produced");
    assert_eq!(state.span_ms().1, 1400.0);
}

#[test]
fn the_score_freezes_where_the_play_ended() {
    // The judge walks the whole map, so it has verdicts out past the death —
    // two misses here, 869 on the replay this came from. None of them are the
    // player's, and none of them belong in the HUD.
    let (map, replay) = a_play_that_died_halfway();
    let state = GameState::new(&map, &replay);

    let at_the_end = state.update(1400.0).score.expect("a replay was judged");
    let long_after = state.update(60_000.0).score.expect("a replay was judged");
    assert_eq!(at_the_end, long_after);
    assert_eq!(long_after.counts.count_300, 2);
    assert_eq!(long_after.counts.count_miss, 0);
    assert_eq!(long_after.max_combo, 2);

    // …and it is the same score the report checks against the header, so the
    // video and the verdict cannot end a failed play on different numbers.
    let check = state.verify(&replay).expect("a replay was supplied");
    assert_eq!(check.ours, long_after.counts);
    assert!(check.is_exact(), "{check:?}");
}

#[test]
fn a_play_that_finished_is_left_alone() {
    let (map, mut replay) = a_play_that_died_halfway();
    replay.hits.count_300 = 2;
    replay.hits.count_miss = 2;
    replay.max_combo = 2;
    let state = GameState::new(&map, &replay);

    assert!(state.ending().is_none(), "every object was judged");
    assert_eq!(
        state.span_ms().1,
        3000.0,
        "the span still covers the replay"
    );
    let final_score = state.update(60_000.0).score.expect("a replay was judged");
    assert_eq!(
        final_score.counts.count_miss, 2,
        "those two are the player's"
    );
}

#[test]
fn a_game_state_can_be_shared_between_threads() {
    // Frames are rendered in parallel, so everything they read has to be
    // shareable. The cursor track keeps a mutable lookup hint, which is
    // exactly the sort of thing that quietly forbids it.
    fn assert_shareable<T: Sync + Send>() {}
    assert_shareable::<GameState>();
}

// ── combo chains ─────────────────────────────────────────────────────────

/// Three circles a second apart, all clickable, with a gap wide enough that
/// skipping one is unambiguous.
const THREE_CIRCLES: &str = "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,1000,1,0
200,100,2000,1,0
300,100,3000,1,0
";

/// A press at `at`, on the object's own position.
fn tap(at: i64, x: f32, y: f32) -> Vec<ReplayFrame> {
    vec![
        frame(at - 20, x, y, 0),
        frame(at, x, y, 1),
        frame(at + 20, x, y, 0),
    ]
}

#[test]
fn combo_chains_report_the_runs_and_what_ended_them() {
    // Hit the first and third, drop the middle one. That is one run of 1 that
    // the miss ended, and one of 1 that the map ended.
    let map = beatmap(THREE_CIRCLES);
    let mut frames = tap(1000, 100.0, 100.0);
    frames.extend(tap(3000, 300.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));

    let chains = state.combo_chains();
    assert_eq!(chains.len(), 2, "{chains:?}");
    assert!(chains.iter().all(|c| c.length == 1), "{chains:?}");

    let ended = chains
        .iter()
        .find(|c| c.ended_at_ms.is_finite())
        .expect("the miss ended a run");
    assert_eq!(ended.object_index, 1, "the dropped circle is the culprit");
    assert!(
        chains.iter().any(|c| c.part.is_none()),
        "the run the map ended has nothing to blame: {chains:?}"
    );
}

#[test]
fn an_unbroken_play_is_one_chain_with_nothing_to_blame() {
    let map = beatmap(THREE_CIRCLES);
    let mut frames = tap(1000, 100.0, 100.0);
    frames.extend(tap(2000, 200.0, 100.0));
    frames.extend(tap(3000, 300.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));

    let chains = state.combo_chains();
    assert_eq!(chains.len(), 1, "{chains:?}");
    assert_eq!(chains[0].length, 3);
    assert!(chains[0].part.is_none());
    assert!(chains[0].ended_at_ms.is_infinite());
}

#[test]
fn there_are_no_break_suspects_when_our_combo_is_not_the_higher_one() {
    // The arithmetic only says anything when we hold a longer run than the
    // game does. Offering candidates otherwise would point at innocent
    // objects, which is worse than saying nothing.
    let map = beatmap(THREE_CIRCLES);
    let mut frames = tap(1000, 100.0, 100.0);
    frames.extend(tap(2000, 200.0, 100.0));
    frames.extend(tap(3000, 300.0, 100.0));
    let state = GameState::new(&map, &replay_with(frames, 0));

    assert!(state.combo_break_suspects(3).is_empty());
    assert!(state.combo_break_suspects(99).is_empty());
    assert_eq!(
        state.combo_break_suspects(1).len(),
        2,
        "1 and 2 into the run"
    );
}
