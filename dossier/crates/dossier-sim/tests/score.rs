//! Score tests.
//!
//! The score is the one quantity in the engine with an unarguable answer: the
//! `.osr` header carries the number the client itself arrived at. These tests
//! pin the two rules that were wrong when the arithmetic was first written and
//! that cost thirty per cent between them — neither of which any amount of
//! staring at the formula would have caught, because the formula as written
//! down is right and the details underneath it are not.

use dossier_beatmap::Beatmap;
use dossier_replay::{bits, HitCounts, Keys, Mods, Replay, ReplayFrame};
use dossier_sim::score::{difficulty_multiplier, stable_mod_multiplier};
use dossier_sim::{GameState, Ruleset, ScoreTrack};

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

fn click(time_ms: i64, x: f32, y: f32) -> Vec<ReplayFrame> {
    vec![
        ReplayFrame {
            time_ms: time_ms - 10,
            x,
            y,
            keys: Keys(0),
        },
        ReplayFrame {
            time_ms,
            x,
            y,
            keys: Keys(Keys::K1),
        },
        ReplayFrame {
            time_ms: time_ms + 10,
            x,
            y,
            keys: Keys(0),
        },
    ]
}

// ── the difficulty multiplier ────────────────────────────────────────────

#[test]
fn a_multiplier_landing_on_a_half_rounds_to_the_even_side() {
    // The map that found this: HP 5, OD 9.2, CS 4, dense enough to clamp the
    // density term at 16. (5 + 9.2 + 4 + 16) / 38 * 5 is exactly 4.5, and C#'s
    // Math.Round sends a half to the *even* neighbour — so the game uses 4
    // where rounding away from zero gives 5.
    //
    // The multiplier is a small integer, so one step of it is a fifth of the
    // score. Two replays in the corpus were thirty per cent over on this alone.
    let m = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:4\nOverallDifficulty:9.2\n\n\
         [HitObjects]\n0,0,1000,1,0\n",
    );
    let raw: f64 = (5.0 + 9.2 + 4.0 + 16.0) / 38.0 * 5.0;
    assert!((raw - 4.5).abs() < 1e-12, "the premise of this test: {raw}");
    assert_eq!(difficulty_multiplier(&m, 1000, 100.0), 4);
}

// ── what the combo multiplier applies to ─────────────────────────────────

#[test]
fn the_pieces_of_a_slider_score_flat_however_long_the_combo() {
    // A slider's head, ticks, repeats and end are worth their ten or thirty and
    // nothing more — only whole objects are paid the combo multiplier. Leaving
    // the pieces multiplied put every score in the corpus four to eight per
    // cent over, and nothing in the formula as it is usually written down says
    // otherwise.
    //
    // Small enough to state the answer outright. Three circles then one slider
    // with no interior tick, all landed, so the combo runs 1, 2, 3, then the
    // head takes it to 4 and the end to 5:
    //
    //   circle 1   300            (combo carried in: 0, less one, floored at 0)
    //   circle 2   300            (carried 1, less one, is 0)
    //   circle 3   300 + 12·M     (carried 2, less one, is 1)
    //   head        30            flat
    //   end         30            flat
    //   slider     300 + 48·M     (carried 5, less one, is 4)
    //
    // which is 1260 + 60·M. With the pieces multiplied it comes to 1260 + 66·M
    // instead, because the head and the end pick up a combo bonus they are not
    // entitled to.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\
         SliderMultiplier:1.0\nSliderTickRate:1\n\n\
         [TimingPoints]\n0,500,4,2,0,100,1,0\n\n\
         [HitObjects]\n\
         100,100,1000,1,0\n100,100,2000,1,0\n100,100,3000,1,0\n\
         100,100,4000,2,0,L|200:100,1,100\n",
    );

    let mut frames = Vec::new();
    for t in [1000, 2000, 3000] {
        frames.extend(click(t, 100.0, 100.0));
    }
    // Hold across the slider and let go after its end, following the path.
    for t in (3990..=4600).step_by(10) {
        let progress = ((t - 4000) as f32 / 500.0).clamp(0.0, 1.0);
        frames.push(ReplayFrame {
            time_ms: t,
            x: 100.0 + 100.0 * progress,
            y: 100.0,
            keys: Keys(if (4000..=4500).contains(&t) { Keys::K1 } else { 0 }),
        });
    }

    let replay = replay_with(frames, 0);
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("the map should be judged");

    // The premise: everything landed and the slider produced a head and an end
    // and no tick. If the fixture stops being true the arithmetic below is
    // meaningless, so it is checked rather than assumed.
    assert_eq!(judge.final_state().combo, 5, "{:?}", judge.events());
    assert!(
        !judge.events().iter().any(|e| e.result.is_miss()),
        "{:?}",
        judge.events()
    );

    let m = u64::from(difficulty_multiplier(
        &map,
        map.objects.len(),
        dossier_sim::score::drain_seconds(&map),
    ));
    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::STABLE);
    assert_eq!(track.total(), 1260 + 60 * m, "multiplier was {m}");
}

#[test]
fn the_first_two_objects_of_a_map_are_worth_their_face_value() {
    // stable reads the combo *before* the hit adds to it and subtracts one
    // more, so the opening notes carry no combo bonus at all. Getting this off
    // by one shifts every hit on the map.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n\
         [HitObjects]\n100,100,1000,1,0\n100,100,2000,1,0\n100,100,3000,1,0\n",
    );
    let mut frames = Vec::new();
    for t in [1000, 2000, 3000] {
        frames.extend(click(t, 100.0, 100.0));
    }
    let replay = replay_with(frames, 0);
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("the map should be judged");
    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::STABLE);

    assert_eq!(track.at(1000.0), 300, "the first note is worth 300 flat");
    assert_eq!(track.at(2000.0), 600, "so is the second");
    // The third is the first to carry a bonus: combo before it is 2, minus one
    // is 1, so it gets one unit of 300 / 25 * multiplier on top.
    let multiplier = f64::from(difficulty_multiplier(
        &map,
        map.objects.len(),
        dossier_sim::score::drain_seconds(&map),
    ));
    assert_eq!(track.at(3000.0), 900 + (300.0 / 25.0 * multiplier) as u64);
}

// ── the mods ─────────────────────────────────────────────────────────────

#[test]
fn nofail_halves_the_whole_score_and_not_just_the_combo_part() {
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n\
         [HitObjects]\n100,100,1000,1,0\n100,100,2000,1,0\n100,100,3000,1,0\n",
    );
    let mut frames = Vec::new();
    for t in [1000, 2000, 3000] {
        frames.extend(click(t, 100.0, 100.0));
    }
    let plain = GameState::new(&map, &replay_with(frames.clone(), 0));
    let nofail = GameState::new(&map, &replay_with(frames, bits::NO_FAIL));

    let a = ScoreTrack::build(
        plain.judge().expect("judged"),
        &map,
        Mods::new(0),
        Ruleset::STABLE,
    );
    let b = ScoreTrack::build(
        nofail.judge().expect("judged"),
        &map,
        Mods::new(bits::NO_FAIL),
        Ruleset::STABLE,
    );

    // NoFail scales the multiplier, so the flat 900 survives untouched and only
    // the combo part halves — halving the total instead would be wrong in the
    // other direction.
    assert_eq!(stable_mod_multiplier(Mods::new(bits::NO_FAIL)), 0.5);
    assert!(b.total() < a.total(), "{} against {}", b.total(), a.total());
    assert!(b.total() >= 900, "the face value is not scaled");
}

// ── lazer ────────────────────────────────────────────────────────────────

#[test]
fn lazers_score_is_capped_near_a_million_where_stables_is_not() {
    // The whole reason both exist. The same short map is worth a few thousand
    // under stable and most of a million under lazer, because lazer normalises
    // and stable does not — so a track built under the wrong ruleset is not
    // slightly wrong, it is off by orders of magnitude.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n\
         [HitObjects]\n100,100,1000,1,0\n100,100,2000,1,0\n100,100,3000,1,0\n",
    );
    let mut frames = Vec::new();
    for t in [1000, 2000, 3000] {
        frames.extend(click(t, 100.0, 100.0));
    }
    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("judged");

    let stable = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::STABLE);
    let lazer = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::LAZER);

    assert!(stable.total() < 2_000, "{}", stable.total());
    // A perfect play: full combo, full accuracy, so both halves are complete.
    assert_eq!(lazer.total(), 1_000_000);
}

#[test]
fn lazer_never_exceeds_a_million_on_a_clean_play() {
    // The cap is the point of the design, and it is easy to break by counting
    // a maximum one way and a running total another.
    let map = beatmap(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\nSliderMultiplier:1.0\n\n\
         [HitObjects]\n100,100,1000,2,0,L|200:100,1,100\n300,200,3000,1,0\n",
    );
    let mut frames: Vec<ReplayFrame> = (990..=1600)
        .step_by(10)
        .map(|t| {
            let progress = ((t - 1000) as f32 / 100.0).clamp(0.0, 1.0);
            ReplayFrame {
                time_ms: t,
                x: 100.0 + 100.0 * progress,
                y: 100.0,
                keys: Keys(if t >= 1000 { Keys::K1 } else { 0 }),
            }
        })
        .collect();
    frames.extend(click(3000, 300.0, 200.0));

    let state = GameState::new(&map, &replay_with(frames, 0));
    let judge = state.judge().expect("judged");
    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::LAZER);
    assert!(track.total() <= 1_000_000, "{}", track.total());
}

// ── the track itself ─────────────────────────────────────────────────────

/// Four circles with the third of them missed, as a `[HitObjects]` body and the
/// frames that play it. Shared by the two tests below because the whole
/// question is what the *same* play is worth under each client.
fn missed_middle() -> (String, Vec<ReplayFrame>) {
    let body = String::from(
        "[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n\
         [HitObjects]\n100,100,1000,1,0\n100,100,2000,1,0\n\
         300,200,3000,1,0\n100,100,4000,1,0\n",
    );
    let mut frames = Vec::new();
    for t in [1000, 2000, 4000] {
        frames.extend(click(t, 100.0, 100.0));
    }
    (body, frames)
}

#[test]
fn stables_score_never_goes_backwards() {
    let (body, frames) = missed_middle();
    let map = beatmap(&body);
    let replay = replay_with(frames, 0);
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("judged");
    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::STABLE);

    let mut last = 0;
    for t in (0..5000).step_by(50) {
        let now = track.at(f64::from(t));
        assert!(now >= last, "stable went backwards at {t}ms");
        last = now;
    }
    // And the miss did cost something. On a map this short it costs
    // everything: the combo never gets past two, so no hit on it is ever paid
    // a bonus and the whole play is worth its face value. The same four notes
    // played clean are worth half again as much.
    assert_eq!(track.total(), 900, "three hits at face value");

    let mut clean = Vec::new();
    for t in [1000, 2000, 4000] {
        clean.extend(click(t, 100.0, 100.0));
    }
    clean.extend(click(3000, 300.0, 200.0));
    clean.sort_by_key(|f| f.time_ms);
    let replay = replay_with(clean, 0);
    let state = GameState::new(&map, &replay);
    let track = ScoreTrack::build(state.judge().expect("judged"), &map, Mods::new(0), Ruleset::STABLE);
    assert!(
        track.total() > 1200,
        "the clean play should carry a combo bonus: {}",
        track.total()
    );
}

#[test]
fn lazers_score_falls_when_a_note_is_missed() {
    // Not a bug to be smoothed over. lazer multiplies the combo half of the
    // score by the accuracy held, so a miss takes points already on the board
    // away — the number visibly drops, and a renderer that clamped it to
    // monotonic would be showing something the game does not.
    let (body, frames) = missed_middle();
    let map = beatmap(&body);
    let replay = replay_with(frames, 0);
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("judged");
    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::LAZER);

    let before = track.at(2500.0);
    let after = track.at(3500.0);
    assert!(after < before, "the miss cost nothing: {before} then {after}");
}

#[test]
fn lazers_combo_half_is_weighted_by_what_the_note_was_worth_at_best() {
    // ```csharp
    // GetBaseScoreForResult(result.Judgement.MaxResult)
    //     * Math.Pow(result.ComboAfterJudgement, COMBO_EXPONENT)
    // ```
    //
    // `MaxResult`, not the result. A hundred carries its full three hundred
    // into the combo half, because that half is about the combo — the accuracy
    // is applied to it separately, once, in the total. Weighting it by what was
    // actually earned charges the accuracy twice and put both lazer replays in
    // the corpus two thirds of a per cent under.
    //
    // Twelve circles, every one hit sixty milliseconds late: a flawless combo
    // made entirely of hundreds. The combo half must be untouched, so the whole
    // score is decided by the accuracy of one third:
    //
    //   500000 × ⅓ × 1  +  500000 × (⅓)⁵ × 1  =  168724
    //
    // Weighted by the earned value instead, the combo half would also fall to a
    // third and the total to about 58000.
    let mut body =
        String::from("[Difficulty]\nHPDrainRate:5\nCircleSize:5\nOverallDifficulty:5\n\n[HitObjects]\n");
    let mut frames = Vec::new();
    for n in 0..12i64 {
        // Spread out: a pile of circles on one spot is a stack, and stable
        // shifts every object in it away from the cursor.
        let (x, y) = (80.0 + (n % 4) as f32 * 90.0, 80.0 + (n / 4) as f32 * 90.0);
        let t = 1000 + n * 400;
        body.push_str(&format!("{x},{y},{t},1,0\n"));
        frames.extend(click(t + 60, x, y));
    }

    let map = beatmap(&body);
    let mut replay = replay_with(frames, 0);
    replay.game_version = 30_000_016;
    let state = GameState::new(&map, &replay);
    let judge = state.judge().expect("judged");

    // The premise: twelve hundreds, nothing missed, combo unbroken.
    assert_eq!(judge.final_state().counts.count_100, 12, "{:?}", judge.events());
    assert_eq!(judge.final_state().combo, 12);

    let track = ScoreTrack::build(judge, &map, Mods::new(0), Ruleset::LAZER);
    let expected = (500_000.0 / 3.0 + 500_000.0 * (1.0f64 / 3.0).powi(5)).round() as u64;
    assert_eq!(track.total(), expected);
}
