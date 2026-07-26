//! Frame tests.
//!
//! A renderer can't be checked by comparing pixels to a reference without
//! pinning every colour choice for ever, so these ask the questions that stay
//! true whatever the look: is anything drawn, is it drawn *when* it should be,
//! and does it land where the playfield says.

use dossier_beatmap::Beatmap;
use dossier_render::{Layout, Scene, Skin};
use dossier_replay::Mods;
use dossier_sim::GameState;

fn beatmap(body: &str) -> Beatmap {
    Beatmap::parse(&format!("osu file format v14\n\n{body}")).expect("test map should parse")
}

const ONE_CIRCLE: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5

[HitObjects]
256,192,5000,1,0
";

/// Pixels that aren't the background — i.e. how much was actually drawn.
fn drawn(map: &Beatmap, time_ms: f64) -> usize {
    let state = GameState::from_beatmap(map, Mods::default());
    let skin = Skin::with_combo_colours(map.combo_colours());
    let background = skin.background.to_color_u8();
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(320, 240);
    scene
        .frame(time_ms, &layout)
        .pixels()
        .iter()
        .filter(|p| {
            p.red() != background.red()
                || p.green() != background.green()
                || p.blue() != background.blue()
        })
        .count()
}

#[test]
fn nothing_is_drawn_before_a_note_spawns() {
    // AR5 is a 1200ms preempt, so at 3700 the note has yet to appear.
    let map = beatmap(ONE_CIRCLE);
    assert_eq!(drawn(&map, 3700.0), 0);
}

#[test]
fn a_note_is_on_screen_once_it_spawns_and_gone_after_it_resolves() {
    let map = beatmap(ONE_CIRCLE);
    assert!(drawn(&map, 4500.0) > 0, "mid-approach");
    assert!(drawn(&map, 5000.0) > 0, "due");
    // With no replay the note runs to the end of its window and fades; well
    // past that the screen is clear again.
    assert_eq!(drawn(&map, 7000.0), 0, "long gone");
}

#[test]
fn a_note_grows_more_solid_as_it_approaches() {
    // The approach circle shrinks toward the note, so the ink it covers falls
    // while the note itself fades in. What must hold is that the note is
    // fainter early than late.
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::with_combo_colours(map.combo_colours()));
    let layout = Layout::new(320, 240);

    let alpha_at = |t: f64| {
        let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
        let frame = scene.frame(t, &layout);
        let pixel = frame
            .pixel(x as u32, y as u32)
            .expect("the centre of the field is inside the frame");
        u32::from(pixel.red()) + u32::from(pixel.green()) + u32::from(pixel.blue())
    };
    assert!(alpha_at(3900.0) < alpha_at(5000.0));
}

#[test]
fn the_frame_matches_the_size_it_was_asked_for() {
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);
    let frame = scene.frame(5000.0, &layout);
    assert_eq!((frame.width(), frame.height()), (640, 480));
}

#[test]
fn a_note_is_drawn_where_the_playfield_puts_it() {
    // Two maps differing only in where the note sits: the ink has to move with
    // it, or the transform is decorative rather than real.
    let left = beatmap("[Difficulty]\nApproachRate:5\n\n[HitObjects]\n60,192,5000,1,0\n");
    let right = beatmap("[Difficulty]\nApproachRate:5\n\n[HitObjects]\n450,192,5000,1,0\n");

    let centroid = |map: &Beatmap| {
        let state = GameState::from_beatmap(map, Mods::default());
        let skin = Skin::default();
        let background = skin.background.to_color_u8();
        let scene = Scene::new(&state, skin);
        let layout = Layout::new(320, 240);
        let frame = scene.frame(5000.0, &layout);
        let (mut sum, mut count) = (0u64, 0u64);
        for (i, p) in frame.pixels().iter().enumerate() {
            if p.red() != background.red() || p.blue() != background.blue() {
                sum += (i as u64) % 320;
                count += 1;
            }
        }
        sum as f64 / count.max(1) as f64
    };

    assert!(centroid(&left) < centroid(&right));
}

#[test]
fn the_palette_advances_on_every_new_combo() {
    // Type bit 4 marks a new combo. Two notes in different combos must not come
    // out the same colour, or long maps turn into one flat wash.
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5

[Colours]
Combo1 : 255,0,0
Combo2 : 0,0,255

[HitObjects]
150,192,5000,5,0
350,192,5000,5,0
",
    );
    assert_eq!(map.combo_colours().len(), 2);

    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::with_combo_colours(map.combo_colours()));
    let layout = Layout::new(320, 240);
    let frame = scene.frame(5000.0, &layout);

    let sample = |x: f64| {
        let (px, py) = layout.map(dossier_beatmap::Point { x, y: 192.0 });
        let p = frame.pixel(px as u32, py as u32).expect("inside the frame");
        (p.red(), p.blue())
    };
    let (first_red, first_blue) = sample(150.0);
    let (second_red, second_blue) = sample(350.0);
    assert!(first_red > first_blue, "first combo is the red one");
    assert!(second_blue > second_red, "second combo is the blue one");
}

// ── text ─────────────────────────────────────────────────────────────────

/// The Torus face the project ships — osu!'s own, so the HUD looks like the
/// game rather than like a debug overlay.
fn font() -> dossier_render::Font {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../assets/fonts/TorusNotched-Bold.ttf"
    );
    let bytes = std::fs::read(path).expect("the repo ships this font");
    dossier_render::Font::from_bytes(&bytes).expect("and it parses")
}

fn replay_over(frames: Vec<dossier_replay::ReplayFrame>) -> dossier_replay::Replay {
    dossier_replay::Replay {
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
    }
}

/// Ink in a corner of the frame — where the HUD lives.
fn corner_ink(frame: &tiny_skia::Pixmap, right: bool, bottom: bool) -> usize {
    let (w, h) = (frame.width(), frame.height());
    let xs = if right { w * 2 / 3..w } else { 0..w / 3 };
    let ys = if bottom { h * 4 / 5..h } else { 0..h / 5 };
    let mut count = 0;
    for y in ys {
        for x in xs.clone() {
            let p = frame.pixel(x, y).expect("inside the frame");
            if p.red() > 60 && p.green() > 60 && p.blue() > 60 {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn a_map_with_no_replay_shows_no_score() {
    // There is nothing to report, and `0x 100.00%` would be a claim rather
    // than a blank.
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default().with_font(font()));
    let frame = scene.frame(5000.0, &Layout::new(640, 480));

    assert_eq!(corner_ink(&frame, true, false), 0, "no accuracy");
    assert_eq!(corner_ink(&frame, false, true), 0, "no combo");
}

#[test]
fn a_replay_puts_accuracy_and_combo_in_the_corners() {
    let map = beatmap(ONE_CIRCLE);
    let replay = replay_over(vec![
        dossier_replay::ReplayFrame {
            time_ms: 4990,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
        dossier_replay::ReplayFrame {
            time_ms: 5000,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        dossier_replay::ReplayFrame {
            time_ms: 5010,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    let state = GameState::new(&map, &replay);
    let scene = Scene::new(&state, Skin::default().with_font(font()));
    let frame = scene.frame(5200.0, &Layout::new(640, 480));

    assert!(corner_ink(&frame, true, false) > 0, "accuracy, top right");
    assert!(corner_ink(&frame, false, true) > 0, "combo, bottom left");
}

#[test]
fn without_a_font_the_play_is_still_drawn() {
    // A missing typeface costs the numbers, not the frame.
    let map = beatmap(ONE_CIRCLE);
    assert!(drawn(&map, 5000.0) > 0);
}

#[test]
fn numbers_measure_wider_the_more_digits_they_have() {
    let font = font();
    assert!(font.width("1", 40.0) < font.width("11", 40.0));
    assert!(font.width("999", 40.0) < font.width("1000", 40.0));
    assert!(font.digit_height(40.0) > 0.0);
}

#[test]
fn the_combo_number_restarts_at_one_in_a_new_combo() {
    // Type bit 4 opens a combo. The number is the only thing telling a player
    // which of two overlapping notes to hit first, so it has to be right.
    let map = beatmap(
        "
[Difficulty]
CircleSize:5
ApproachRate:5

[HitObjects]
150,192,5000,5,0
250,192,5100,1,0
350,192,5200,5,0
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default().with_font(font()));
    let layout = Layout::new(640, 480);
    let frame = scene.frame(4900.0, &layout);

    // The first and third notes both open a combo, so both are numbered 1 and
    // must carry the same amount of ink; the middle one is a 2 and differs.
    let ink_on = |x: f64| {
        let (cx, cy) = layout.map(dossier_beatmap::Point { x, y: 192.0 });
        let mut count = 0;
        for dy in -12i32..12 {
            for dx in -12i32..12 {
                let p = frame
                    .pixel((cx as i32 + dx) as u32, (cy as i32 + dy) as u32)
                    .expect("inside the frame");
                if p.red() > 200 && p.green() > 200 && p.blue() > 200 {
                    count += 1;
                }
            }
        }
        count
    };
    assert_eq!(ink_on(150.0), ink_on(350.0), "both are a 1");
    assert_ne!(ink_on(150.0), ink_on(250.0), "the middle one is a 2");
}

// ── reverse arrows ───────────────────────────────────────────────────────

/// A slider from (100,192) to (240,192) at 1000ms, one beat (500ms) per
/// traversal, repeated as many times as the caller asks.
///
/// The control point and the authored length agree deliberately: a path is
/// trimmed to the length the file states, so a slider drawn to x=300 but
/// declared 140 long actually ends at x=240.
fn repeating_slider(slides: u32) -> Beatmap {
    beatmap(&format!(
        "
[Difficulty]
CircleSize:5
ApproachRate:5
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,1000,2,0,L|240:192,{slides},140
"
    ))
}

/// White ink within a small box around a playfield point — the arrow is white
/// and nothing else white sits at a slider's bare end.
fn white_ink_at(map: &Beatmap, time_ms: f64, x: f64, y: f64) -> usize {
    let state = GameState::from_beatmap(map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);
    let frame = scene.frame(time_ms, &layout);
    let (cx, cy) = layout.map(dossier_beatmap::Point { x, y });

    let mut count = 0;
    for dy in -6i32..6 {
        for dx in -6i32..6 {
            let Some(p) = frame.pixel((cx as i32 + dx) as u32, (cy as i32 + dy) as u32) else {
                continue;
            };
            if p.red() > 230 && p.green() > 230 && p.blue() > 230 {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn a_slider_that_never_turns_gets_no_arrow() {
    // Drawing one would tell the player to come back over something that ends
    // where it stops.
    let map = repeating_slider(1);
    assert_eq!(white_ink_at(&map, 1200.0, 240.0, 192.0), 0);
}

#[test]
fn a_repeating_slider_marks_the_end_it_is_heading_for() {
    let map = repeating_slider(2);
    // Mid-way through the first traversal, the turn ahead is at the far end.
    assert!(
        white_ink_at(&map, 1200.0, 240.0, 192.0) > 0,
        "arrow at the tail"
    );
    assert_eq!(
        white_ink_at(&map, 1200.0, 100.0, 192.0),
        0,
        "and not at the head"
    );
}

#[test]
fn the_arrow_is_up_before_the_slider_even_starts() {
    // The player needs to know it repeats while it is still approaching, not
    // once they are already on it.
    let map = repeating_slider(2);
    assert!(white_ink_at(&map, 700.0, 240.0, 192.0) > 0);
}

#[test]
fn the_arrow_moves_to_the_other_end_after_a_turn() {
    let map = repeating_slider(3);
    // Second traversal (1500–2000ms) runs tail to head, so the next turn is
    // at the head.
    assert!(
        white_ink_at(&map, 1700.0, 100.0, 192.0) > 0,
        "arrow at the head"
    );
    assert_eq!(
        white_ink_at(&map, 1700.0, 240.0, 192.0),
        0,
        "no longer at the tail"
    );
}

#[test]
fn the_last_traversal_has_nothing_left_to_point_at() {
    let map = repeating_slider(2);
    // 1500–2000ms is the final run back to the head; the ball stops there.
    assert_eq!(white_ink_at(&map, 1700.0, 100.0, 192.0), 0);
    assert_eq!(white_ink_at(&map, 1700.0, 240.0, 192.0), 0);
}

// ── house style ──────────────────────────────────────────────────────────

#[test]
fn the_house_skin_uses_its_own_palette_over_the_maps() {
    // Named skins are a deliberate override, not a fallback: the map here does
    // state colours, and the 1984 skin ignores them on purpose.
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5

[Colours]
Combo1 : 0,255,0

[HitObjects]
256,192,5000,5,0
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(320, 240);
    let at_note = |skin: Skin| {
        let frame = Scene::new(&state, skin).frame(5000.0, &layout);
        let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
        let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
        (p.red(), p.green(), p.blue())
    };

    let (_, classic_green, _) = at_note(Skin::with_combo_colours(map.combo_colours()));
    let (house_red, house_green, _) = at_note(Skin::nineteen_eightyfour());
    assert!(classic_green > 200, "the map asked for green");
    assert!(house_red > house_green, "the house skin is coral");
}

#[test]
fn the_house_palette_cycles_through_four_distinct_colours() {
    // A cycle with a repeat in it makes two neighbouring combos look like one.
    let skin = Skin::nineteen_eightyfour();
    let colours: Vec<_> = (0..4)
        .map(|i| {
            let c = skin.combo_colour(i);
            (
                (c.red() * 255.0) as u8,
                (c.green() * 255.0) as u8,
                (c.blue() * 255.0) as u8,
            )
        })
        .collect();
    let mut unique = colours.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), 4, "{colours:?}");
    assert_eq!(skin.combo_colour(0), skin.combo_colour(4), "and it wraps");
}
