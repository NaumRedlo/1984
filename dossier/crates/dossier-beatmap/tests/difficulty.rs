//! The four difficulty numbers, pinned against osu!'s own tables.
//!
//! CS, AR, OD and HP are the whole of what a map tells the game about how hard
//! it is, and every one of them feeds judgement: OD sets the windows, CS sets
//! the circle and with it the follow circle and the stack offset, AR sets
//! preempt and with it the stacking threshold. A quiet error in any of them
//! looks exactly like a bug in the note lock — the totals go wrong and nothing
//! says why — so they are pinned here rather than trusted.
//!
//! The reference values are the interpolation osu! documents: `min` at 0, `mid`
//! at 5, `max` at 10, the two halves scaled separately. Where a value below is
//! not a round number it was worked out by hand from that rule and checked
//! against a real map in the corpus.

use dossier_beatmap::Beatmap;

fn difficulty(body: &str) -> dossier_beatmap::Difficulty {
    Beatmap::parse(&format!("osu file format v14\n\n[Difficulty]\n{body}\n"))
        .expect("test map should parse")
        .difficulty
}

// ── AR: how long an object is on screen ──────────────────────────────────

#[test]
fn approach_rate_sets_preempt_by_the_documented_table() {
    // 1200 + 120 * (5 - AR) below 5; 1200 - 150 * (AR - 5) above it.
    for (ar, preempt) in [
        (0.0, 1800.0),
        (2.0, 1560.0),
        (5.0, 1200.0),
        (8.0, 750.0),
        (9.0, 600.0),
        (9.6, 510.0),
        (10.0, 450.0),
    ] {
        let d = difficulty(&format!("ApproachRate:{ar}"));
        assert!(
            (d.preempt_ms() - preempt).abs() < 1e-9,
            "AR {ar}: {} against {preempt}",
            d.preempt_ms()
        );
    }
}

#[test]
fn an_approach_rate_past_ten_keeps_going() {
    // Mods cap AR at 10, but a map may author more, and osu! extrapolates
    // rather than clamping. Clamping here would make such a map read easier
    // than it plays.
    let d = difficulty("ApproachRate:11");
    assert!((d.preempt_ms() - 300.0).abs() < 1e-9, "{}", d.preempt_ms());
}

#[test]
fn the_fade_in_is_two_thirds_of_preempt() {
    // osu!'s table: 1200ms of fade at AR0, 800 at AR5, 300 at AR10 — against
    // preempts of 1800, 1200 and 450. Every one of those is exactly two
    // thirds. lazer instead uses `400 * min(1, preempt / 450)`, a flat 400ms
    // for every AR up to 10, which is one of the places it is simply not
    // stable and the Classic mod does not restore it.
    for (ar, fade) in [(0.0, 1200.0), (5.0, 800.0), (10.0, 300.0)] {
        let d = difficulty(&format!("ApproachRate:{ar}"));
        assert!(
            (d.fade_in_ms() - fade).abs() < 1e-9,
            "AR {ar}: {} against {fade}",
            d.fade_in_ms()
        );
    }
}

// ── OD: the judgement windows ────────────────────────────────────────────

#[test]
fn overall_difficulty_sets_the_windows_by_the_documented_table() {
    // 80 - 6·OD, 140 - 8·OD, 200 - 10·OD, each truncated to a whole
    // millisecond. Every value here appears on a map in the corpus.
    for (od, windows) in [
        (0.0, (80.0, 140.0, 200.0)),
        (3.5, (59.0, 112.0, 165.0)),
        (4.25, (54.0, 106.0, 157.0)),
        (6.5, (41.0, 88.0, 135.0)),
        (8.5, (29.0, 72.0, 115.0)),
        (9.2, (24.0, 66.0, 108.0)),
        (9.3, (24.0, 65.0, 107.0)),
        (10.0, (20.0, 60.0, 100.0)),
    ] {
        let d = difficulty(&format!("OverallDifficulty:{od}"));
        assert_eq!(
            (d.hit_window_300(), d.hit_window_100(), d.hit_window_50()),
            windows,
            "OD {od}"
        );
    }
}

#[test]
fn od_nine_point_three_gives_a_hundred_and_seven() {
    // Singled out because it is the one value in the corpus where the
    // arithmetic's precision decides the answer. Read the OD as a 32-bit
    // float first — as lazer does, its difficulty fields being floats — and
    // the fifty window computes to 106.99999809, which floors to 106.
    //
    // A failed replay settles it. Its 258th object is a circle at 78276ms
    // that nobody hit, so osu! judged it a miss when the window shut, and the
    // player's health hit zero at that judgement: the last sample in the
    // replay's own life-bar graph is `78383|0`. 78383 - 78276 = 107.
    let d = difficulty("OverallDifficulty:9.3");
    assert_eq!(d.hit_window_50(), 107.0);
    assert_eq!(78276.0 + d.hit_window_50(), 78383.0);
}

// ── CS: the circle, and everything measured off it ───────────────────────

#[test]
fn circle_size_sets_the_radius() {
    // 54.4 - 4.48·CS osu!pixels, which is `64 * (1 - 0.7·(CS-5)/5) / 2` — the
    // form both danser and lazer use.
    for (cs, radius) in [
        (0.0, 54.4),
        (2.0, 45.44),
        (4.0, 36.48),
        (5.0, 32.0),
        (7.0, 23.04),
        (10.0, 9.6),
    ] {
        let d = difficulty(&format!("CircleSize:{cs}"));
        assert!(
            (d.circle_radius() - radius).abs() < 1e-9,
            "CS {cs}: {} against {radius}",
            d.circle_radius()
        );
    }
}

// ── mods ─────────────────────────────────────────────────────────────────

#[test]
fn hard_rock_scales_every_stat_and_caps_at_ten() {
    // 1.4 for everything except CS, which takes 1.3. That is the game's rule
    // and not a rounding artefact — both references state it outright.
    let d = difficulty("HPDrainRate:5\nCircleSize:4\nOverallDifficulty:6\nApproachRate:7").hard_rock();
    assert_eq!(d.hp_drain, 7.0);
    assert!((d.circle_size - 5.2).abs() < 1e-9, "{}", d.circle_size);
    assert!((d.overall_difficulty - 8.4).abs() < 1e-9);
    assert!((d.approach_rate - 9.8).abs() < 1e-9);

    let capped =
        difficulty("HPDrainRate:9\nCircleSize:9\nOverallDifficulty:9\nApproachRate:9").hard_rock();
    assert_eq!(capped.hp_drain, 10.0);
    assert_eq!(capped.overall_difficulty, 10.0);
    assert_eq!(capped.approach_rate, 10.0);
    assert!(
        (capped.circle_size - 10.0).abs() < 1e-9,
        "CS 9 × 1.3 is 11.7, capped: {}",
        capped.circle_size
    );
}

#[test]
fn easy_halves_every_stat() {
    let d = difficulty("HPDrainRate:5\nCircleSize:4\nOverallDifficulty:7\nApproachRate:9").easy();
    assert_eq!(
        (d.hp_drain, d.circle_size, d.overall_difficulty, d.approach_rate),
        (2.5, 2.0, 3.5, 4.5)
    );
}

#[test]
fn neither_mod_touches_the_slider_settings() {
    // Speed mods change the clock, not the map, and this engine works in map
    // time throughout — so a slider's length in beats is the same under every
    // mod, and only the encoder's playback rate moves.
    let d = difficulty("SliderMultiplier:1.8\nSliderTickRate:2");
    for scaled in [d.hard_rock(), d.easy()] {
        assert_eq!(scaled.slider_multiplier, 1.8);
        assert_eq!(scaled.slider_tick_rate, 2.0);
    }
}
