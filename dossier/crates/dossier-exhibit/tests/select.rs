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

/// A clip is at least the length it was asked for and at most that much again
/// times the stretch — never shorter, whatever it was chosen for.
#[test]
fn a_clip_runs_from_the_asked_length_up_to_the_stretch() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let mut settings = settings();
    settings.clip_ms = 4_000.0;
    let longest = settings.clip_ms * (1.0 + settings.stretch);
    for clip in choose(&GameState::new(&map, &replay), settings) {
        // A clip holding two moments is bounded elsewhere, and by more: see
        // `a_merged_clip_is_still_bounded`.
        if clip.with.is_some() {
            continue;
        }
        assert!(
            clip.span.length_ms() >= 4_000.0 - 1e-6
                && clip.span.length_ms() <= longest + 1e-6,
            "{:?} is {:.0}ms, outside 4000..{longest:.0}",
            clip.reason.scorer().name(),
            clip.span.length_ms()
        );
    }
}

/// Turning the stretch off puts every clip back to one length, which is what a
/// caller who wants uniform clips has to be able to ask for.
#[test]
fn no_stretch_means_every_clip_is_the_length_it_was_asked_for() {
    let map = map_of(&circles(1_000, 60_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let mut settings = settings();
    settings.clip_ms = 4_000.0;
    settings.stretch = 0.0;
    for clip in choose(&GameState::new(&map, &replay), settings) {
        if clip.with.is_some() {
            continue;
        }
        assert!(
            (clip.span.length_ms() - 4_000.0).abs() < 1e-6,
            "{:?}",
            clip.span
        );
    }
}

/// The more important moment gets the longer clip. That is the whole of what
/// length is for here — it is the only thing a reel without narration has to
/// say "this one" with.
#[test]
fn the_more_important_moment_gets_the_longer_clip() {
    let map = map_of(&circles(1_000, 120_000, 300), "0,500,4,2,0,60,1,0");
    let missed_at = 90_000i64;
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

    let mut by_rank = clips.clone();
    by_rank.sort_by_key(|clip| clip.rank);
    let best = by_rank.first().expect("a reel");
    let worst = by_rank.last().expect("a reel");
    assert!(
        best.span.length_ms() > worst.span.length_ms(),
        "the reel's best clip ({}, {:.0}ms) is no longer than its last ({}, {:.0}ms)",
        best.reason.scorer().name(),
        best.span.length_ms(),
        worst.reason.scorer().name(),
        worst.span.length_ms(),
    );
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

    let mut settings = settings();
    settings.stretch = 0.0;
    let clips = choose(&GameState::new(&map, &replay), settings);
    assert!(!clips.is_empty());
    // Six seconds of watching is nine seconds of map under DoubleTime, so no
    // clip may be shorter than nine — with the rate ignored every one of them
    // would have come out at six. A clip holding two moments runs longer, which
    // is why the claim is a floor rather than an equality.
    let shortest = clips
        .iter()
        .map(|clip| clip.span.length_ms())
        .fold(f64::INFINITY, f64::min);
    assert!(
        shortest >= 9_000.0 - 1.0,
        "6s of video is 9s of map under DT, shortest was {shortest:.0}ms"
    );
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

// ── the edges of the play, and the hand ──────────────────────────────────

/// How a play ended is the one thing every viewer wants to know, and a play
/// that died ends at the moment the bar empties.
#[test]
fn a_play_that_ends_well_gets_its_ending_shown() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let state = GameState::new(&map, &replay);
    let clips = choose(&state, settings());

    let finale = clips
        .iter()
        .find(|clip| matches!(clip.reason, Reason::Finale { .. }))
        .expect("an FC's landing is worth showing");
    let (_, play_to) = state.span_ms();
    assert!(
        (finale.span.to_ms - play_to).abs() < 1.0,
        "the finale has to end where the play does: {:?} against {play_to}",
        finale.span
    );
    assert!(
        matches!(finale.reason, Reason::Finale { full_combo: true, .. }),
        "{}",
        finale.reason.describe()
    );
}

/// …and a play that ended on nothing in particular does not get one. "If they
/// are important" is the whole of what the edges were asked for.
#[test]
fn a_play_that_just_runs_out_does_not_claim_a_finale() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    // Every fourth note dropped: it finishes, at about 75%, having said nothing.
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        if i % 4 == 0 {
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
    let offered: Vec<_> = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Finale)
        .collect();
    assert!(
        offered.is_empty() || offered[0].1.strength < 0.2,
        "a 75% finish is the map running out, not a payoff: {offered:?}"
    );
}

/// The opening is always offered and rarely wins — it fills a budget that
/// outlasts the things worth watching, and loses to all of them.
#[test]
fn the_opening_is_offered_and_loses_to_anything_that_tells() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    let replay = played_perfectly(&map);
    let state = GameState::new(&map, &replay);

    let opening: Vec<_> = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Opening)
        .collect();
    assert_eq!(opening.len(), 1, "one play, one opening");

    let (play_from, _) = state.span_ms();
    assert!((opening[0].1.anchor_ms - play_from).abs() < 1.0);

    // With the budget cut to two clips there is no room for establishing.
    let mut tight = settings();
    tight.budget_ms = 12_000.0;
    assert!(
        choose(&state, tight)
            .iter()
            .all(|clip| !matches!(clip.reason, Reason::Opening { .. })),
        "an opening took a slot a telling moment wanted"
    );
}

/// A spinner is the easiest thing a hand ever does and covers more distance
/// than any jump in the map. Counted, it makes this a spinner detector.
#[test]
fn a_spinner_is_not_the_hardest_movement_in_the_play() {
    // Sparse circles, then a six-second spinner, then more circles.
    let mut objects = circles(1_000, 20_000, 500);
    objects.push_str("256,192,20000,12,0,26000\n");
    objects.push_str(&circles(27_000, 50_000, 500));
    let map = map_of(&objects, "0,500,4,2,0,60,1,0");

    // Played with the cursor whirling through the spinner and walking the rest.
    let mut frames = Vec::new();
    for object in &map.objects {
        let at = object.time_ms as i64;
        frames.push(ReplayFrame {
            time_ms: at,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(1),
        });
    }
    for step in 0..600 {
        let at = 20_000 + step * 10;
        let angle = step as f32 * 0.6;
        frames.push(ReplayFrame {
            time_ms: at,
            x: 256.0 + 60.0 * angle.cos(),
            y: 192.0 + 60.0 * angle.sin(),
            keys: Keys(1),
        });
    }
    frames.sort_by_key(|frame| frame.time_ms);
    let state = GameState::new(&map, &replay_with(frames));

    for (_, candidate) in dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Travel)
    {
        assert!(
            !(20_000.0..26_000.0).contains(&candidate.anchor_ms),
            "the spinner at {}ms was called hard movement",
            candidate.anchor_ms
        );
    }
}

// ── calibration ──────────────────────────────────────────────────────────

/// The asymmetry the survey found, stated as a test.
///
/// A map-side scorer is graded against the same map's own busiest window, and
/// some window always is one — so every map hands `storm` a free 1.0 and
/// `travel` a free 1.0. A play-side scorer anchors at perfection. Read as a
/// plain ratio, the typical play's best run scored a third of an FC and lost to
/// a map that merely existed. Over 123 replays that put 42% of every reel on
/// the map side and 19% on the run.
#[test]
fn a_typical_best_run_outscores_a_map_that_merely_exists() {
    let map = map_of(&circles(1_000, 120_000, 300), "0,500,4,2,0,60,1,0");
    // Broken once early, so the longest run is about two thirds of the map —
    // a good run and nowhere near a full combo.
    let missed_at = 40_000i64;
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
    let state = GameState::new(&map, &replay_with(frames));

    let offered = dossier_exhibit::candidates(&state, settings());
    let best = |want: Scorer| {
        offered
            .iter()
            .filter(|(scorer, _)| *scorer == want)
            .map(|(_, c)| c.strength * 100.0)
            .fold(0.0f64, f64::max)
            / 100.0
    };

    // The map's busiest window is 1.0 by construction — that is what "against
    // its own busiest" means, and it is why the other side has to be graded on
    // a curve rather than a ratio.
    assert!((best(Scorer::Storm) - 1.0).abs() < 1e-6, "{}", best(Scorer::Storm));

    let run = best(Scorer::Peak);
    assert!(
        run > 0.6,
        "a run over most of the map scored {run:.2} — a plain ratio, not a curve"
    );
}

/// …and the bottom of that curve still has to be near zero, or every play with
/// a broken run of nine notes gets a clip about it.
#[test]
fn a_handful_of_notes_is_still_nothing() {
    let map = map_of(&circles(1_000, 120_000, 300), "0,500,4,2,0,60,1,0");
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        // Broken every third note for the first thirty, then clean: the longest
        // *broken* run is a handful.
        if i < 30 && i % 3 == 0 {
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

    for (_, candidate) in dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Choke)
    {
        assert!(
            candidate.strength < 0.1,
            "a broken run of a few notes scored {:.3}",
            candidate.strength
        );
    }
}

/// A stray miss is not a scramble however few objects were around it. A share
/// alone says one dropped note in a four-object break section is a quarter of a
/// catastrophe.
#[test]
fn one_stray_miss_is_not_a_scramble() {
    let map = map_of(&circles(1_000, 90_000, 300), "0,500,4,2,0,60,1,0");
    let missed_at = 45_000i64;
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
    let state = GameState::new(&map, &replay_with(frames));

    assert!(
        dossier_exhibit::candidates(&state, settings())
            .into_iter()
            .filter(|(scorer, _)| *scorer == Scorer::Scramble)
            .all(|(_, c)| c.strength <= 0.0),
        "one miss on its own was called a scramble"
    );
}

/// A beginning gets nothing for being a beginning. An average opening is still
/// an opening and nobody watches a reel for one — so unless the map opens on
/// something, the reel starts wherever the play first has anything to say.
#[test]
fn a_dull_opening_is_skipped_rather_than_shown() {
    // Sparse for the first twenty seconds, then dense for two minutes.
    let mut objects = circles(1_000, 20_000, 900);
    objects.push_str(&circles(20_000, 140_000, 200));
    let map = map_of(&objects, "0,500,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_perfectly(&map));

    let opening = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .find(|(scorer, _)| *scorer == Scorer::Opening)
        .expect("an opening is always offered");
    assert!(
        opening.1.strength < 0.3,
        "a quiet opening scored {:.2}",
        opening.1.strength
    );
    assert!(
        choose(&state, settings())
            .iter()
            .all(|clip| !matches!(clip.reason, Reason::Opening { .. })),
        "a quiet opening took a clip"
    );
}

/// …and a map that opens on its hardest section does get it.
#[test]
fn an_opening_that_is_the_hardest_thing_in_the_map_is_shown() {
    // Dense for the first twenty seconds, then sparse.
    let mut objects = circles(1_000, 20_000, 200);
    objects.push_str(&circles(20_000, 140_000, 900));
    let map = map_of(&objects, "0,500,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_perfectly(&map));

    let opening = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .find(|(scorer, _)| *scorer == Scorer::Opening)
        .expect("an opening is always offered");
    assert!(
        opening.1.strength > 0.7,
        "the map's hardest section is its opening and it scored {:.2}",
        opening.1.strength
    );
}

// ── the bar ──────────────────────────────────────────────────────────────

/// A map long enough to drain on, played cleanly except for a stretch in the
/// middle where every note is dropped — which is what empties a bar.
fn played_with_a_gap(map: &Beatmap, from_ms: i64, to_ms: i64, mods: u32) -> Replay {
    let mut frames = Vec::new();
    for (i, object) in map.objects.iter().enumerate() {
        let at = object.time_ms as i64;
        if (from_ms..to_ms).contains(&at) {
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
    let mut replay = replay_with(frames);
    replay.mods = Mods::new(mods);
    replay
}

/// The bar creeping to nothing and climbing back is the most visible drama in
/// the game and the only thing here a viewer watches happen rather than infers.
#[test]
fn a_bar_that_nearly_empties_and_recovers_is_a_moment() {
    let map = map_of(
        &circles(1_000, 120_000, 300),
        "0,500,4,2,0,60,1,0",
    );
    let state = GameState::new(&map, &played_with_a_gap(&map, 40_000, 55_000, 0));

    let brinks: Vec<_> = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Brink)
        .collect();

    // The fixture only works if the bar actually went down; say so rather than
    // passing vacuously.
    let lowest = (0..1200)
        .filter_map(|i| state.health_at(f64::from(i) * 100.0))
        .fold(1.0f32, f32::min);
    assert!(
        lowest < dossier_sim::DANGER_LEVEL,
        "the fixture never put the bar in danger (lowest {lowest:.2})"
    );
    assert!(!brinks.is_empty(), "a bar in danger that recovered was not noticed");
    for (_, candidate) in &brinks {
        assert!(
            (40_000.0..70_000.0).contains(&candidate.anchor_ms),
            "the dip is in the middle, not at {}",
            candidate.anchor_ms
        );
    }
}

/// Under NoFail the bar comes off the screen, because its whole job is to say
/// how close the play is to being over and the play cannot be over. A reel
/// claiming a brush with death over a HUD that shows no danger would be the
/// engine contradicting itself in the same second.
#[test]
fn a_play_that_cannot_die_has_no_brink() {
    let map = map_of(&circles(1_000, 120_000, 300), "0,500,4,2,0,60,1,0");
    let with = played_with_a_gap(&map, 40_000, 55_000, 0);
    let without = played_with_a_gap(&map, 40_000, 55_000, dossier_replay::bits::NO_FAIL);

    let count = |replay: &Replay| {
        dossier_exhibit::candidates(&GameState::new(&map, replay), settings())
            .into_iter()
            .filter(|(scorer, _)| *scorer == Scorer::Brink)
            .count()
    };
    assert!(count(&with) > 0, "the fixture should find a dip without NoFail");
    assert_eq!(count(&without), 0, "NoFail still produced a brink");
}

// ── where the cuts land ──────────────────────────────────────────────────

/// A listener hears where a *bar* begins, not where a beat does. Six slices of
/// one song cut together are six entries mid-phrase, and a cut on the third
/// beat of a four sounds like a skip however exactly it lands on that beat.
#[test]
fn cuts_land_on_the_bar_where_the_bar_is_within_reach() {
    // 250ms beats in fours: a bar every second, so no cut is ever further than
    // half a second from one and every clip can afford to reach it.
    let map = map_of(&circles(1_000, 120_000, 250), "0,250,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_perfectly(&map));
    let (play_from, play_to) = state.span_ms();

    for clip in choose(&state, settings()) {
        // A clip against either end of the play is there because of that end
        // and is deliberately never moved — see the finale.
        if (clip.span.from_ms - play_from).abs() < 1.0 || (clip.span.to_ms - play_to).abs() < 1.0 {
            continue;
        }
        let into_bar = clip.span.from_ms.rem_euclid(1_000.0);
        let off = into_bar.min(1_000.0 - into_bar);
        assert!(
            off < 1e-6,
            "{} cuts {off:.0}ms from the nearest bar",
            clip.reason.scorer().name()
        );
    }
}

/// …but never at the cost of the moment. A cut may move a tenth of its clip and
/// no further; a bar out of that reach falls through to the beat, and a beat out
/// of reach leaves the cut where the moment put it. Never dragged part way.
#[test]
fn a_cut_is_never_dragged_part_way() {
    // Bars eight seconds apart — further than any clip may travel — so the
    // beat has to carry it, and where the beat cannot the cut stays put.
    let map = map_of(&circles(1_000, 120_000, 250), "0,2000,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_perfectly(&map));
    let (play_from, play_to) = state.span_ms();

    for clip in choose(&state, settings()) {
        if (clip.span.from_ms - play_from).abs() < 1.0 || (clip.span.to_ms - play_to).abs() < 1.0 {
            continue;
        }
        let allowed = clip.span.length_ms() * 0.1;
        let into_beat = clip.span.from_ms.rem_euclid(2_000.0);
        let off = into_beat.min(2_000.0 - into_beat);
        assert!(
            off < 1e-6 || off > allowed,
            "a cut sits {off:.0}ms off the beat having been allowed {allowed:.0}ms"
        );
        assert!(
            clip.span.from_ms >= play_from - 1e-6 && clip.span.to_ms <= play_to + 1e-6,
            "a snap pushed a clip outside the play"
        );
    }
}

// ── two moments in one place ─────────────────────────────────────────────

/// A jump pattern is the hardest movement in the map *and* where the misses
/// are, so two scorers fire within a second of each other. Overlap used to be a
/// hard ban: one won, and the other was cut off by the end of the winner's clip
/// — the strong moment faded out before it had finished being one.
#[test]
fn two_moments_in_one_place_share_a_clip() {
    let map = map_of(&circles(1_000, 150_000, 250), "0,500,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_with_a_gap(&map, 60_000, 68_000, 0));
    let clips = choose(&state, settings());

    let merged: Vec<_> = clips.iter().filter(|c| c.with.is_some()).collect();
    assert!(
        !merged.is_empty(),
        "nothing merged: {:?}",
        clips
            .iter()
            .map(|c| (c.reason.scorer().name(), c.span.from_ms as i64))
            .collect::<Vec<_>>()
    );
    for clip in merged {
        let with = clip.with.expect("filtered on it");
        assert_ne!(
            clip.reason.scorer(),
            with.scorer(),
            "a scorer merged with itself, which is the long flat stretch the \
             repeat discount exists to prevent"
        );
    }
}

/// …but two names for one moment are not two moments. `peak` anchors at the end
/// of a combo run and `choke` at the break that ended it — the same instant —
/// and a clip captioned with both spends the reader's attention on looking for
/// a difference that is not there.
#[test]
fn one_moment_under_two_names_does_not_merge() {
    let map = map_of(&circles(1_000, 120_000, 300), "0,500,4,2,0,60,1,0");
    let missed_at = 85_000i64;
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

    for clip in &clips {
        let pair = (clip.reason, clip.with);
        let both_about_the_run = matches!(
            pair,
            (Reason::Choke { .. }, Some(Reason::Peak { .. }))
                | (Reason::Peak { .. }, Some(Reason::Choke { .. }))
        );
        assert!(
            !both_about_the_run,
            "the break and the run it ended were captioned as two moments"
        );
    }
}

/// A merged clip is two moments and gets two moments' room, and no more. Past
/// that it stops being a moment held longer and becomes a stretch of map, which
/// wants its own clip rather than a longer sentence.
#[test]
fn a_merged_clip_is_still_bounded() {
    let map = map_of(&circles(1_000, 150_000, 250), "0,500,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_with_a_gap(&map, 60_000, 68_000, 0));
    let settings = settings();
    let longest = settings.clip_ms * (1.0 + settings.stretch) + settings.clip_ms;

    for clip in choose(&state, settings) {
        assert!(
            clip.span.length_ms() <= longest + 1e-6,
            "a {:.0}ms clip against a {longest:.0}ms bound",
            clip.span.length_ms()
        );
    }
}

// ── the fingers ──────────────────────────────────────────────────────────

/// Tapping is not density. A stretch of long sliders is dense to `storm` while
/// the hand does almost nothing, and a burst of circles in one place is flat to
/// `travel` while the fingers are working hardest in the map.
#[test]
fn the_hardest_tapping_is_found_where_the_presses_are() {
    // Slow for a minute, then a burst, then slow again.
    let mut objects = circles(1_000, 60_000, 900);
    objects.push_str(&circles(60_000, 70_000, 120));
    objects.push_str(&circles(70_000, 120_000, 900));
    let map = map_of(&objects, "0,500,4,2,0,60,1,0");
    let state = GameState::new(&map, &played_perfectly(&map));

    let best = dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Tapping)
        .max_by(|a, b| a.1.strength.total_cmp(&b.1.strength))
        .expect("a play with presses in it has tapping");
    assert!(
        (55_000.0..71_000.0).contains(&best.1.anchor_ms),
        "the burst is at 60s, the hardest tapping was found at {}",
        best.1.anchor_ms
    );
    assert!((best.1.strength - 1.0).abs() < 1e-6, "the busiest window is the scale");
}

/// A spinner is held, not tapped, and a player who mashes through one would
/// otherwise own the scale for the rest of the map.
#[test]
fn a_spinner_is_not_the_hardest_tapping() {
    let mut objects = circles(1_000, 20_000, 500);
    objects.push_str("256,192,20000,12,0,26000\n");
    objects.push_str(&circles(27_000, 60_000, 500));
    let map = map_of(&objects, "0,500,4,2,0,60,1,0");

    let mut frames = Vec::new();
    for object in &map.objects {
        let at = object.time_ms as i64;
        frames.push(ReplayFrame {
            time_ms: at,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(1),
        });
        frames.push(ReplayFrame {
            time_ms: at + 20,
            x: object.pos.x as f32,
            y: object.pos.y as f32,
            keys: Keys(0),
        });
    }
    // Mashed through the spinner, alternating every 30ms.
    for step in 0..200 {
        let at = 20_000 + step * 30;
        frames.push(ReplayFrame {
            time_ms: at,
            x: 256.0,
            y: 192.0,
            keys: Keys(if step % 2 == 0 { 1 } else { 2 }),
        });
        frames.push(ReplayFrame {
            time_ms: at + 15,
            x: 256.0,
            y: 192.0,
            keys: Keys(0),
        });
    }
    frames.sort_by_key(|frame| frame.time_ms);
    let state = GameState::new(&map, &replay_with(frames));

    for (_, candidate) in dossier_exhibit::candidates(&state, settings())
        .into_iter()
        .filter(|(scorer, _)| *scorer == Scorer::Tapping)
    {
        assert!(
            !(20_000.0..26_000.0).contains(&candidate.anchor_ms),
            "the spinner at {}ms was called hard tapping",
            candidate.anchor_ms
        );
    }
}
