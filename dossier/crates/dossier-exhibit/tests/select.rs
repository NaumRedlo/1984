//! What selection is held to.
//!
//! Every assertion here is of the form "this beats that" or "this cannot
//! happen". None is of the form "this is the best clip", because there is
//! nothing to check such a claim against — see the crate docs. A test that
//! pinned a particular millisecond would pass, and would fail the next time
//! anybody improved the feature, having measured nothing in between.

use dossier_beatmap::Beatmap;
use dossier_exhibit::{choose, Reason, Scorer, Settings, Span};
use dossier_replay::{GameMode, Keys, Mods, Replay, ReplayFrame};
use dossier_sim::GameState;

/// Six-second clips out of a thirty-second budget: five of them.
fn settings() -> Settings {
    Settings::default()
}

fn beatmap(body: &str) -> Beatmap {
    Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("test map should parse")
}

fn replay_with(frames: Vec<ReplayFrame>) -> Replay {
    Replay {
        mode: GameMode::Standard,
        game_version: 20_260_101,
        beatmap_hash: String::new(),
        player: "tester".into(),
        replay_hash: String::new(),
        hits: Default::default(),
        score: 0,
        max_combo: 0,
        perfect_combo: false,
        mods: Mods::new(0),
        life_bar: String::new(),
        timestamp_ticks: 0,
        online_score_id: 0,
        target_practice_accuracy: None,
        frames,
        rng_seed: None,
        score_info: None,
    }
}

/// A circle every `gap` ms from `from` to `to`, walked around the playfield.
///
/// The positions have to differ: notes on the same spot inside the stack
/// window get nudged by stacking, and then a cursor placed from the *file's*
/// coordinates is a growing distance from where the note actually is. Every
/// hit in the fixture silently became a miss, which is a fixture bug that
/// reads exactly like a scorer bug.
fn circles(from: i64, to: i64, gap: i64) -> String {
    (from..to)
        .step_by(gap as usize)
        .enumerate()
        .map(|(n, t)| {
            let x = 60 + (n as i64 * 71) % 390;
            let y = 50 + (n as i64 * 97) % 290;
            format!("{x},{y},{t},1,0\n")
        })
        .collect()
}

/// A map with a header, and whatever objects the caller wants.
fn map_of(objects: &str, timing: &str) -> Beatmap {
    beatmap(&format!(
        "[Difficulty]\nApproachRate:8\nOverallDifficulty:8\nCircleSize:4\nHPDrainRate:5\nSliderMultiplier:1.4\n\n[TimingPoints]\n{timing}\n\n[HitObjects]\n{objects}"
    ))
}

/// Click every object dead on time, at the object's own position.
fn played_perfectly(map: &Beatmap) -> Replay {
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        let keys = if i % 2 == 0 { 1 } else { 2 };
        frames.push(ReplayFrame {
            time_ms: object.time_ms as i64 - 8,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(0),
        });
        frames.push(ReplayFrame {
            time_ms: object.time_ms as i64,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(keys),
        });
    }
    replay_with(frames)
}

// ── the shape of the output ──────────────────────────────────────────────

#[test]
fn clips_come_back_in_time_order() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let clips = choose(&GameState::new(&map, &replay), settings());

    assert!(clips.len() > 1, "a minute of map should yield several clips");
    for pair in clips.windows(2) {
        assert!(
            pair[0].span.from_ms <= pair[1].span.from_ms,
            "a reel that jumps backwards is disorienting: {:?} then {:?}",
            pair[0].span,
            pair[1].span
        );
    }
}

#[test]
fn no_two_clips_overlap() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let clips = choose(&GameState::new(&map, &replay), settings());

    for (i, a) in clips.iter().enumerate() {
        for b in &clips[i + 1..] {
            assert!(!a.span.overlaps(&b.span), "{:?} overlaps {:?}", a.span, b.span);
        }
    }
}

#[test]
fn the_budget_is_a_ceiling() {
    let map = map_of(&circles(1_000, 120_000, 250), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let mut settings = settings();
    settings.budget_ms = 18_000.0;
    let clips = choose(&GameState::new(&map, &replay), settings);

    assert!(clips.len() <= 3, "18s of budget at 6s a clip is three clips, got {}", clips.len());
    let total: f64 = clips.iter().map(|c| c.span.length_ms()).sum();
    assert!(total <= settings.budget_ms + 1.0, "{total}ms of clips against an 18000ms budget");
}

#[test]
fn every_clip_is_the_length_it_was_asked_for() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let mut settings = settings();
    settings.clip_ms = 4_000.0;
    for clip in choose(&GameState::new(&map, &replay), settings) {
        assert!(
            (clip.span.length_ms() - 4_000.0).abs() < 1e-6,
            "clips of uneven length make the reel stutter: {:?}",
            clip.span
        );
    }
}

#[test]
fn every_clip_is_inside_the_play() {
    let map = map_of(&circles(1_000, 40_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let state = GameState::new(&map, &replay);
    let (from, to) = state.span_ms();
    for clip in choose(&state, settings()) {
        assert!(
            clip.span.from_ms >= from - 1e-6 && clip.span.to_ms <= to + 1e-6,
            "{:?} runs outside the play {from}..{to}",
            clip.span
        );
    }
}

#[test]
fn a_map_shorter_than_one_clip_yields_nothing() {
    let map = map_of(&circles(1_000, 3_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    assert!(choose(&GameState::new(&map, &replay), settings()).is_empty());
}

#[test]
fn the_same_replay_gives_the_same_clips() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let once = choose(&GameState::new(&map, &replay), settings());
    let again = choose(&GameState::new(&map, &replay), settings());
    assert_eq!(once, again, "selection has to be reproducible to be arguable");
}

// ── that the play is what is being watched ───────────────────────────────

/// The whole point of the feature, stated as a test.
///
/// Two plays of the same map: one clean, one that breaks a long run three
/// quarters of the way through. The hand-rolled version of this picked by
/// density and gave both the same reel. This must not.
#[test]
fn a_choke_is_chosen_over_a_quiet_stretch() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");

    // Played perfectly except for one note at 45s, which is simply not clicked.
    let missed_at = 45_100i64;
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        let at = object.time_ms as i64;
        if (at - missed_at).abs() < 400 {
            continue;
        }
        let keys = if i % 2 == 0 { 1 } else { 2 };
        frames.push(ReplayFrame { time_ms: at - 8, x: object.pos.x as f32, y: object.pos.y as f32, keys: Keys(0) });
        frames.push(ReplayFrame { time_ms: at, x: object.pos.x as f32, y: object.pos.y as f32, keys: Keys(keys) });
    }
    let state = GameState::new(&map, &replay_with(frames));

    let clips = choose(&state, settings());
    let broke_at = state
        .combo_chains()
        .into_iter()
        .find(|chain| chain.part.is_some())
        .expect("the play should have broken somewhere")
        .ended_at_ms;

    assert!(
        clips.iter().any(|clip| {
            matches!(clip.reason, Reason::Choke { .. })
                && clip.span.from_ms <= broke_at
                && broke_at <= clip.span.to_ms
        }),
        "the break at {broke_at}ms is not in any clip: {:?}",
        clips.iter().map(|c| (c.reason.scorer().name(), c.span)).collect::<Vec<_>>()
    );
}

/// A clean play has no choke and no scramble, so the reel is what the map has
/// to offer — and that is the honest answer, not a failure.
#[test]
fn a_clean_play_falls_back_to_the_map() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let clips = choose(&GameState::new(&map, &replay), settings());

    assert!(!clips.is_empty(), "a clean play still deserves a reel");
    assert!(
        clips.iter().all(|clip| !matches!(clip.reason, Reason::Choke { .. })),
        "nothing broke, so nothing may be called a choke"
    );
}

/// Kiai is the mapper's own mark and nothing else in the file carries it.
#[test]
fn a_kiai_section_is_offered() {
    // Kiai on from 20s (effects bit 0), off again at 40s.
    let timing = "0,500,4,2,0,60,1,0\n20000,-100,4,2,0,60,0,1\n40000,-100,4,2,0,60,0,0";
    let map = map_of(&circles(1_000, 60_000, 300), timing);
    let replay = played_perfectly(&map);
    let state = GameState::new(&map, &replay);

    let offered = dossier_exhibit::candidates(&state, settings());
    let kiai: Vec<_> = offered.iter().filter(|(s, _)| *s == Scorer::Kiai).collect();
    assert_eq!(kiai.len(), 1, "one section was marked, so one candidate");
    assert!(
        (kiai[0].1.anchor_ms - 20_000.0).abs() < 1.0,
        "the section starts at 20s, not {}",
        kiai[0].1.anchor_ms
    );
}

// ── the knobs ────────────────────────────────────────────────────────────

/// Spans are map time and the budget is video time, and under DoubleTime those
/// are not the same second. Six seconds of watching is nine seconds of map.
#[test]
fn a_rate_mod_stretches_the_clip_in_map_time() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    let mut replay = played_perfectly(&map);
    replay.mods = Mods::new(dossier_replay::bits::DOUBLE_TIME);

    let clips = choose(&GameState::new(&map, &replay), settings());
    assert!(!clips.is_empty());
    for clip in clips {
        assert!(
            (clip.span.length_ms() - 9_000.0).abs() < 1.0,
            "6s of video is 9s of map under DT, got {:?}",
            clip.span
        );
    }
}

#[test]
fn spans_overlap_is_exclusive_at_the_edges() {
    let a = Span::new(0.0, 100.0);
    let b = Span::new(100.0, 200.0);
    assert!(!a.overlaps(&b), "two clips cut end to end are two clips");
    assert!(a.overlaps(&Span::new(99.0, 200.0)));
}

// ── strength is absolute ─────────────────────────────────────────────────

/// A scorer with nothing to say has to drop out on its own.
///
/// The first version normalised each scorer against its own best, so its best
/// scored exactly its weight and every scorer that fired at all won a clip —
/// the reel was the weight table read aloud. Here the play breaks three times
/// in the first seconds and then holds a run for the rest of the map: the
/// broken runs are tiny, and a choke clip for the longest of three tiny runs
/// would be the old behaviour returning.
#[test]
fn a_trivial_break_does_not_earn_a_choke_clip() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");

    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        // Drop the 2nd, 4th and 6th notes — three breaks, none worth watching.
        if matches!(i, 1 | 3 | 5) {
            continue;
        }
        let at = object.time_ms as i64;
        let keys = if i % 2 == 0 { 1 } else { 2 };
        frames.push(ReplayFrame {
            time_ms: at - 8,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(0),
        });
        frames.push(ReplayFrame {
            time_ms: at,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(keys),
        });
    }
    let state = GameState::new(&map, &replay_with(frames));

    let chokes: Vec<_> = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Choke)
        .collect();
    assert!(!chokes.is_empty(), "the play did break — the scorer should see it");
    for (_, candidate) in &chokes {
        assert!(
            candidate.strength < 0.05,
            "a run of a few notes out of {} is not a choke, got strength {}",
            state.max_possible_combo(),
            candidate.strength
        );
    }

    let clips = choose(&state, settings());
    assert!(
        clips.iter().all(|clip| !matches!(clip.reason, Reason::Choke { .. })),
        "a trivial break won a clip: {:?}",
        clips.iter().map(|c| (c.reason.scorer().name(), c.score)).collect::<Vec<_>>()
    );
}

/// The other half of the same rule: a break that really cost the play must win.
#[test]
fn a_long_run_lost_late_outscores_everything_else() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    let missed_at = 70_000i64;
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        let at = object.time_ms as i64;
        if (at - missed_at).abs() < 200 {
            continue;
        }
        let keys = if i % 2 == 0 { 1 } else { 2 };
        frames.push(ReplayFrame {
            time_ms: at - 8,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(0),
        });
        frames.push(ReplayFrame {
            time_ms: at,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(keys),
        });
    }
    let clips = choose(&GameState::new(&map, &replay_with(frames)), settings());
    let best = clips.iter().min_by_key(|clip| clip.rank).expect("a reel");
    assert!(
        matches!(best.reason, Reason::Choke { .. }),
        "the play's whole story is one break at 77%, and the reel opened with {}",
        best.reason.describe()
    );
}
