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
fn the_house_palette_alternates_between_two_distinct_colours() {
    // The point of the cycle is that a new combo is visible. A repeat anywhere
    // in it would make two neighbouring combos look like one, which is the one
    // thing a two-colour palette cannot afford — with only two entries every
    // combo change is a change of colour, so there is nowhere to hide a clash.
    let skin = Skin::nineteen_eightyfour();
    let colours: Vec<_> = (0..2)
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
    assert_eq!(unique.len(), 2, "{colours:?}");
    assert_eq!(skin.combo_colour(0), skin.combo_colour(2), "and it wraps");
    assert_ne!(skin.combo_colour(0), skin.combo_colour(1));
}

// ── where the time in a frame goes ───────────────────────────────────────
//
// These measure rather than assert, so they're `#[ignore]`d — a test that can
// only pass is noise in a suite. Run them on demand:
//
//     cargo test --release -p dossier-render profile -- --ignored --nocapture
//
// They exist because three plausible optimisations were tried against them and
// all three lost. Keeping the measurements keeps the next person from paying
// for the same three ideas.

/// Not an assertion about speed — a breakdown of one frame.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_the_phases_of_a_frame() {
    use std::time::Instant;

    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:9
SliderMultiplier:1.8

[TimingPoints]
0,300,4,2,0,60,1,0

[HitObjects]
100,100,1000,1,0
200,150,1100,2,0,L|300:150,1,180
300,200,1300,1,0
150,250,1400,1,0
250,300,1500,2,0,L|400:300,1,180
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default().with_font(font()));
    let layout = Layout::new(1920, 1080);
    let mut pixmap = tiny_skia::Pixmap::new(1920, 1080).unwrap();

    let rounds = 60;
    let fill_colour = Skin::default().background;

    let mark = Instant::now();
    for _ in 0..rounds {
        pixmap.fill(fill_colour);
    }
    let filling = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    let mark = Instant::now();
    for i in 0..rounds {
        scene.draw_into(&mut pixmap, 1000.0 + f64::from(i), &layout);
    }
    let whole = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    // A scene with no font draws everything but the HUD and the numbers.
    let bare = Scene::new(&state, Skin::default());
    let mark = Instant::now();
    for i in 0..rounds {
        bare.draw_into(&mut pixmap, 1000.0 + f64::from(i), &layout);
    }
    let without_text = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "1080p frame: {whole:.2}ms total — fill {filling:.2}ms, text {:.2}ms, shapes {:.2}ms",
        whole - without_text,
        without_text - filling
    );
}

/// Stroking a path and filling one are different amounts of work, and the
/// renderer pays the first every frame for geometry that never changes.
///
/// Result: 1% apart. The stroker is not the cost; the covered area is.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_stroking_against_filling() {
    use std::time::Instant;
    use tiny_skia::{FillRule, LineCap, LineJoin, Paint, PathBuilder, Stroke, Transform};

    // A long curved slider at 1080p scale: a few hundred segments under a
    // stroke two circle-radii wide, which is the shape that dominates a frame.
    let mut builder = PathBuilder::new();
    builder.move_to(200.0, 500.0);
    for i in 1..400 {
        let t = f32::from(i as u16) / 400.0;
        builder.line_to(200.0 + t * 1200.0, 500.0 + (t * 9.0).sin() * 220.0);
    }
    let path = builder.finish().unwrap();

    let stroke = Stroke {
        width: 110.0,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };
    let paint = Paint {
        anti_alias: true,
        ..Default::default()
    };
    let mut pixmap = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let rounds = 100;

    let mark = Instant::now();
    for _ in 0..rounds {
        pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
    }
    let stroking = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    let outline = tiny_skia::PathStroker::new()
        .stroke(&path, &stroke, 1.0)
        .expect("the stroker can outline this");
    let mark = Instant::now();
    for _ in 0..rounds {
        pixmap.fill_path(
            &outline,
            &paint,
            FillRule::Winding,
            Transform::identity(),
            None,
        );
    }
    let filling = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "one slider body: stroke+fill {stroking:.2}ms, fill of a cached outline {filling:.2}ms \
         ({:.0}% saved)",
        (1.0 - filling / stroking) * 100.0
    );
}

/// Filling a shape versus copying an already-filled one — the ceiling on what
/// caching a slider's pixels could buy.
///
/// Result: blitting is 1.7× *slower*. A slider's bounding box is mostly empty,
/// and a blit pays for every pixel in the box while an anti-aliased fill pays
/// only for the ones it covers.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_filling_against_blitting() {
    use std::time::Instant;
    use tiny_skia::{
        FillRule, LineCap, LineJoin, Paint, PathBuilder, PixmapPaint, Stroke, Transform,
    };

    let mut builder = PathBuilder::new();
    builder.move_to(200.0, 500.0);
    for i in 1..400 {
        let t = f32::from(i as u16) / 400.0;
        builder.line_to(200.0 + t * 1200.0, 500.0 + (t * 9.0).sin() * 220.0);
    }
    let path = builder.finish().unwrap();
    let stroke = Stroke {
        width: 110.0,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };
    let paint = Paint {
        anti_alias: true,
        ..Default::default()
    };

    let mut frame = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let rounds = 100;

    // What the renderer does now: two wide fills, one for the border and one
    // for the body inside it.
    let inner = Stroke {
        width: 90.0,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };
    let mark = Instant::now();
    for _ in 0..rounds {
        frame.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
        frame.stroke_path(&path, &paint, &inner, Transform::identity(), None);
    }
    let both_fills = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    // The same body drawn once into its own bounding box, then copied.
    let bounds = path.bounds();
    let (w, h) = (
        (bounds.width() + 120.0) as u32,
        (bounds.height() + 120.0) as u32,
    );
    let mut cached = tiny_skia::Pixmap::new(w, h).unwrap();
    let offset = Transform::from_translate(-bounds.x() + 60.0, -bounds.y() + 60.0);
    cached.stroke_path(&path, &paint, &stroke, offset, None);
    cached.stroke_path(&path, &paint, &inner, offset, None);

    let faded = PixmapPaint {
        opacity: 0.8,
        ..Default::default()
    };
    let mark = Instant::now();
    for _ in 0..rounds {
        frame.draw_pixmap(
            (bounds.x() - 60.0) as i32,
            (bounds.y() - 60.0) as i32,
            cached.as_ref(),
            &faded,
            Transform::identity(),
            None,
        );
    }
    let blitting = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "one slider body: two fills {both_fills:.2}ms, blit of a cached raster {blitting:.2}ms \
         ({:.1}× faster), cache {} KiB",
        both_fills / blitting,
        (w * h * 4) / 1024
    );
    let _ = FillRule::Winding;
}

/// The border is drawn as a second full-width fill under the body, of which
/// only the rim is ever visible. Stroking the body's outline would touch the
/// rim alone.
///
/// Result: 2.2× slower. Outlining turns a few hundred segments into a polygon
/// with many more, and stroking that costs more than the fill it replaced.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_the_border_as_a_rim_instead_of_a_fill() {
    use std::time::Instant;
    use tiny_skia::{FillRule, LineCap, LineJoin, Paint, PathBuilder, Stroke, Transform};

    let mut builder = PathBuilder::new();
    builder.move_to(200.0, 500.0);
    for i in 1..400 {
        let t = f32::from(i as u16) / 400.0;
        builder.line_to(200.0 + t * 1200.0, 500.0 + (t * 9.0).sin() * 220.0);
    }
    let path = builder.finish().unwrap();
    let round = |width: f32| Stroke {
        width,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };
    let paint = Paint {
        anti_alias: true,
        ..Default::default()
    };
    let mut frame = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let rounds = 100;

    let mark = Instant::now();
    for _ in 0..rounds {
        frame.stroke_path(&path, &paint, &round(110.0), Transform::identity(), None);
        frame.stroke_path(&path, &paint, &round(90.0), Transform::identity(), None);
    }
    let two_fills = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    // The body's own outline, stroked thinly, is the rim and nothing else.
    let body = tiny_skia::PathStroker::new()
        .stroke(&path, &round(100.0), 1.0)
        .expect("outline");
    let mark = Instant::now();
    for _ in 0..rounds {
        frame.fill_path(
            &body,
            &paint,
            FillRule::Winding,
            Transform::identity(),
            None,
        );
        frame.stroke_path(&body, &paint, &round(10.0), Transform::identity(), None);
    }
    let fill_and_rim = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "one slider body: two fills {two_fills:.2}ms, fill plus a rim {fill_and_rim:.2}ms \
         ({:.0}% saved)",
        (1.0 - fill_and_rim / two_fills) * 100.0
    );
}

/// Circles and slider bodies are different shapes with different costs, and a
/// specialised rasteriser could only ever help one of them. This says which.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_circles_against_slider_bodies() {
    use std::time::Instant;
    use tiny_skia::{FillRule, LineCap, LineJoin, Paint, PathBuilder, Stroke, Transform};

    let mut frame = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let paint = Paint {
        anti_alias: true,
        ..Default::default()
    };
    let rounds = 200;

    // A busy 1080p frame: four notes on screen, each drawn as three discs and
    // a ring, plus a cursor and its trail. Roughly forty circles.
    let circles: Vec<_> = (0..40)
        .map(|i| {
            let angle = f32::from(i as u16) * 0.9;
            PathBuilder::from_circle(
                960.0 + angle.cos() * 400.0,
                540.0 + angle.sin() * 300.0,
                90.0 - (i % 5) as f32 * 15.0,
            )
            .unwrap()
        })
        .collect();

    let mark = Instant::now();
    for _ in 0..rounds {
        for path in &circles {
            frame.fill_path(path, &paint, FillRule::Winding, Transform::identity(), None);
        }
    }
    let discs = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    // Two slider bodies, drawn the way the renderer draws them.
    let mut bodies = Vec::new();
    for k in 0..2 {
        let mut builder = PathBuilder::new();
        builder.move_to(200.0, 300.0 + f32::from(k as u16) * 400.0);
        for i in 1..300 {
            let t = f32::from(i as u16) / 300.0;
            builder.line_to(
                200.0 + t * 1400.0,
                300.0 + f32::from(k as u16) * 400.0 + (t * 7.0).sin() * 150.0,
            );
        }
        bodies.push(builder.finish().unwrap());
    }
    let round = |width: f32| Stroke {
        width,
        line_cap: LineCap::Round,
        line_join: LineJoin::Round,
        ..Default::default()
    };

    let mark = Instant::now();
    for _ in 0..rounds {
        for path in &bodies {
            frame.stroke_path(path, &paint, &round(110.0), Transform::identity(), None);
            frame.stroke_path(path, &paint, &round(90.0), Transform::identity(), None);
        }
    }
    let sliders = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "1080p: {} circles {discs:.2}ms, 2 slider bodies {sliders:.2}ms",
        circles.len()
    );
}

/// A general path rasteriser builds edge lists and walks scanlines. A circle
/// needs none of that: the coverage of a pixel is a function of its distance
/// from the centre, and the interior needs no function at all.
///
/// This is the one place the measurements say a hand-written rasteriser could
/// beat the library, so it's measured before it's believed.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_a_hand_written_circle_against_the_library() {
    use std::time::Instant;
    use tiny_skia::{Color, FillRule, Paint, PathBuilder, PremultipliedColorU8, Transform};

    /// Fill an anti-aliased circle directly into the buffer.
    fn disc(pixmap: &mut tiny_skia::Pixmap, cx: f32, cy: f32, r: f32, colour: Color) {
        let (w, h) = (pixmap.width() as i32, pixmap.height() as i32);
        let (sr, sg, sb, sa) = (colour.red(), colour.green(), colour.blue(), colour.alpha());
        let pixels = pixmap.pixels_mut();

        let top = ((cy - r - 1.0).floor() as i32).max(0);
        let bottom = ((cy + r + 1.0).ceil() as i32).min(h - 1);
        for y in top..=bottom {
            let dy = y as f32 + 0.5 - cy;
            let inside = r * r - dy * dy;
            if inside <= 0.0 {
                continue;
            }
            let half = inside.sqrt();
            let left = ((cx - half - 1.0).floor() as i32).max(0);
            let right = ((cx + half + 1.0).ceil() as i32).min(w - 1);
            // Everything more than a pixel inside the edge is fully covered,
            // and needs no distance and no square root.
            let solid = (half - 1.5).max(0.0);
            let row = (y * w) as usize;

            for x in left..=right {
                let dx = x as f32 + 0.5 - cx;
                let coverage = if dx.abs() <= solid {
                    1.0
                } else {
                    (r + 0.5 - (dx * dx + dy * dy).sqrt()).clamp(0.0, 1.0)
                };
                if coverage <= 0.0 {
                    continue;
                }
                let a = sa * coverage;
                let keep = 1.0 - a;
                let slot = row + x as usize;
                let dst = pixels[slot];
                let mix =
                    |src: f32, dst: u8| ((src * a + f32::from(dst) / 255.0 * keep) * 255.0) as u8;
                let (r8, g8, b8) = (
                    mix(sr, dst.red()),
                    mix(sg, dst.green()),
                    mix(sb, dst.blue()),
                );
                let a8 = ((a + f32::from(dst.alpha()) / 255.0 * keep) * 255.0) as u8;
                pixels[slot] =
                    PremultipliedColorU8::from_rgba(r8.min(a8), g8.min(a8), b8.min(a8), a8)
                        .unwrap_or(dst);
            }
        }
    }

    let mut frame = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let colour = Color::from_rgba8(226, 72, 72, 255);
    let paint = Paint {
        shader: tiny_skia::Shader::SolidColor(colour),
        anti_alias: true,
        ..Default::default()
    };
    let placed: Vec<_> = (0..40)
        .map(|i| {
            let angle = f32::from(i as u16) * 0.9;
            (
                960.0 + angle.cos() * 400.0,
                540.0 + angle.sin() * 300.0,
                90.0 - (i % 5) as f32 * 15.0,
            )
        })
        .collect();
    let paths: Vec<_> = placed
        .iter()
        .map(|&(x, y, r)| PathBuilder::from_circle(x, y, r).unwrap())
        .collect();
    let rounds = 200;

    let mark = Instant::now();
    for _ in 0..rounds {
        for path in &paths {
            frame.fill_path(path, &paint, FillRule::Winding, Transform::identity(), None);
        }
    }
    let library = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    let mark = Instant::now();
    for _ in 0..rounds {
        for &(x, y, r) in &placed {
            disc(&mut frame, x, y, r, colour);
        }
    }
    let ours = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "40 circles: tiny-skia {library:.2}ms, hand-written {ours:.2}ms ({:.1}× faster)",
        library / ours
    );
}

/// ffmpeg is handed RGBA and converts it to YUV before encoding — single
/// threaded, on the same cores the renderer wants. Doing it ourselves moves
/// that work into the render threads and cuts the pipe by 62%.
///
/// Worth it only if our conversion is cheaper than what it saves.
#[test]
#[ignore = "a measurement, not a check"]
fn profile_converting_to_yuv_ourselves() {
    use std::time::Instant;

    let frame = tiny_skia::Pixmap::new(1920, 1080).unwrap();
    let (w, h) = (1920usize, 1080usize);
    let mut yuv = vec![0u8; w * h * 3 / 2];
    let rounds = 100;

    let mark = Instant::now();
    for _ in 0..rounds {
        let src = frame.data();
        // Luma at full resolution, chroma at half in both directions — the
        // planar layout every encoder wants.
        let (luma, chroma) = yuv.split_at_mut(w * h);
        let (u_plane, v_plane) = chroma.split_at_mut(w * h / 4);

        for y in 0..h {
            for x in 0..w {
                let i = (y * w + x) * 4;
                let (r, g, b) = (
                    f32::from(src[i]),
                    f32::from(src[i + 1]),
                    f32::from(src[i + 2]),
                );
                luma[y * w + x] = (0.257 * r + 0.504 * g + 0.098 * b + 16.0) as u8;
                if y % 2 == 0 && x % 2 == 0 {
                    let at = (y / 2) * (w / 2) + x / 2;
                    u_plane[at] = (-0.148 * r - 0.291 * g + 0.439 * b + 128.0) as u8;
                    v_plane[at] = (0.439 * r - 0.368 * g - 0.071 * b + 128.0) as u8;
                }
            }
        }
    }
    let converting = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    println!(
        "1080p frame: RGBA→I420 {converting:.2}ms, pipe {} KiB → {} KiB",
        w * h * 4 / 1024,
        yuv.len() / 1024
    );
}

// ── animation ────────────────────────────────────────────────────────────

/// One long slider, alone, so nothing else can account for a changed pixel.
const LONE_SLIDER: &str = "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,2000,2,0,L|400:192,1,300
";

/// How much ink is on the pixmap, as a count of non-background pixels.
fn ink(pixmap: &tiny_skia::Pixmap, background: tiny_skia::Color) -> usize {
    let bg = background.to_color_u8();
    pixmap
        .pixels()
        .iter()
        .filter(|p| p.red() != bg.red() || p.green() != bg.green() || p.blue() != bg.blue())
        .count()
}

#[test]
fn a_slider_grows_into_place_instead_of_appearing_whole() {
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let spawn = 2000.0 - state.difficulty().preempt_ms();
    let early = scene.frame(spawn + state.difficulty().fade_in_ms() * 0.25, &layout);
    let grown = scene.frame(2000.0, &layout);

    assert!(
        ink(&early, background) < ink(&grown, background),
        "a quarter of the way in the body should be shorter: {} vs {}",
        ink(&early, background),
        ink(&grown, background)
    );
}

#[test]
fn a_slider_retracts_behind_the_ball() {
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let object = &state.timeline().objects[0];
    let full = ink(&scene.frame(object.start_ms, &layout), background);
    let late = ink(
        &scene.frame(
            object.start_ms + (object.end_ms - object.start_ms) * 0.8,
            &layout,
        ),
        background,
    );

    assert!(
        late < full,
        "four fifths through, most of the body is behind the ball: {late} vs {full}"
    );
}

/// Four slides, so the body never retracts while the first pass runs — any
/// change at the head's own position is the head itself, not the snake.
const REPEATING_SLIDER: &str = "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,2000,2,0,L|400:192,4,300
";

#[test]
fn a_slider_head_leaves_on_its_own_click_not_at_the_end_of_the_slider() {
    // A slider is judged as a whole when it ends, and reusing that time left
    // the head circle sitting on the playfield for the entire slide — over the
    // top of its own reverse arrow — when the player had clicked it on the
    // first frame. The head has its own time and has to be drawn by it.
    let map = beatmap(REPEATING_SLIDER);
    // The press lands on the head's own time, so what the head does afterwards
    // is measured from the click and not from some earlier frame.
    let frames: Vec<_> = (0..90)
        .map(|i| {
            let time_ms = 1800 + i * 20;
            dossier_replay::ReplayFrame {
                time_ms,
                x: 100.0,
                y: 192.0,
                keys: dossier_replay::Keys(u8::from(time_ms >= 2000)),
            }
        })
        .collect();
    let replay = replay_over(frames);
    let state = GameState::new(&map, &replay);
    let skin = Skin::default();
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let object = &state.timeline().objects[0];
    assert!(
        object.end_ms > 5000.0,
        "the slider runs long: {}",
        object.end_ms
    );

    // Sampled on a ring inside the head's fill rather than at its centre: the
    // reverse arrow sits exactly on the centre and is the same white whether
    // the head is there or not, which hid the difference entirely.
    let radius = state.difficulty().circle_radius() * 0.8;
    let brightness = |t: f64| {
        let frame = scene.frame(t, &layout);
        let mut total = 0u32;
        for step in 0..8 {
            let angle = f64::from(step) * std::f64::consts::TAU / 8.0;
            let at = dossier_beatmap::Point {
                x: object.pos.x + radius * angle.cos(),
                y: object.pos.y + radius * angle.sin(),
            };
            let (x, y) = layout.map(at);
            let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
            total += u32::from(p.red()) + u32::from(p.green()) + u32::from(p.blue());
        }
        total
    };

    let clicked = brightness(2000.0);
    let mid_slide = brightness(2600.0);
    assert!(
        mid_slide < clicked,
        "the head should be gone by mid-slide: {mid_slide} against {clicked} at the click"
    );
}

#[test]
fn the_balls_core_grows_to_fill_it_as_the_slider_runs_out() {
    // How far through a slider you are should be readable from the ball
    // itself, not only from where it sits on the body. The core starts at a
    // third of the ball and grows to meet it, so a point two thirds out is
    // outside the core early and inside it late — and the core is the paler
    // colour, so that point gets brighter.
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);

    let object = &state.timeline().objects[0];
    let span = object.end_ms - object.start_ms;
    let offset = state.difficulty().circle_radius() * 0.6;

    let at_core_edge = |fraction: f64| {
        let t = object.start_ms + span * fraction;
        let ball = object.ball_at(t).expect("the ball is on the path");
        let probe = dossier_beatmap::Point {
            x: ball.x + offset,
            y: ball.y,
        };
        let (x, y) = layout.map(probe);
        let p = scene
            .frame(t, &layout)
            .pixel(x as u32, y as u32)
            .expect("inside");
        u32::from(p.red()) + u32::from(p.green()) + u32::from(p.blue())
    };

    let early = at_core_edge(0.15);
    let late = at_core_edge(0.9);
    assert!(
        late > early,
        "the core should have reached this point by the end: {late} against {early}"
    );
}

/// Length 300 at SliderMultiplier 1.4 puts ticks every 140 osu!px — two of
/// them, at roughly 0.47 and 0.93 along the path.
#[test]
fn a_tick_is_not_drawn_ahead_of_the_body_it_belongs_to() {
    // Ticks used to be drawn as soon as the note appeared, which put dots in
    // empty space in front of a slider that had not grown that far. A dot with
    // no line under it does not read as sitting on the line — which is what it
    // looked like from the outside.
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let object = &state.timeline().objects[0];
    let far_tick = *object.tick_times().last().expect("this slider has ticks");
    let at = object.ball_at(far_tick).expect("on the path");
    let (x, y) = layout.map(at);

    let lit = |t: f64| {
        let frame = scene.frame(t, &layout);
        let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
        let bg = background.to_color_u8();
        p.red() != bg.red() || p.green() != bg.green() || p.blue() != bg.blue()
    };

    let spawn = object.start_ms - state.difficulty().preempt_ms();
    assert!(
        !lit(spawn + state.difficulty().fade_in_ms() * 0.2),
        "the body has not grown this far yet"
    );
    assert!(lit(object.start_ms), "and by the time it is due, it has");
}

/// Three slides: the ball turns at the tail, then at the head. Both ends have
/// a turn coming while the first slide is still running.
const THRICE_SLIDER: &str = "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,2000,2,0,L|400:192,3,300
";

/// Two slides: one turn, at the tail. The head never gets an arrow.
const TWICE_SLIDER: &str = "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:1.4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,2000,2,0,L|400:192,2,300
";

#[test]
fn both_ends_keep_an_arrow_while_both_still_have_a_turn_coming() {
    // Only ever drawing the nearest turn made the far end's arrow vanish the
    // moment the near one appeared, which reads as the slider changing its
    // mind about where it goes.
    let ink_at_head = |source: &str| {
        let map = beatmap(source);
        let state = GameState::from_beatmap(&map, Mods::default());
        let scene = Scene::new(&state, Skin::default());
        let layout = Layout::new(640, 480);
        let object = &state.timeline().objects[0];
        // After the head circle has gone, so what is left at that end is the
        // body and, if there is one, the arrow.
        let t = object.start_ms + state.difficulty().hit_window_50() + 200.0;
        let frame = scene.frame(t, &layout);
        let (x, y) = layout.map(object.pos);
        // The arrow is drawn in the border colour — near-white — while the
        // body is a darkened combo colour. Counting anything brighter than the
        // background caught the body too and saturated at every pixel.
        let mut count = 0;
        for dy in -20i32..=20 {
            for dx in -20i32..=20 {
                let p = frame
                    .pixel((x as i32 + dx) as u32, (y as i32 + dy) as u32)
                    .expect("inside the frame");
                if p.red() > 230 && p.green() > 230 && p.blue() > 230 {
                    count += 1;
                }
            }
        }
        count
    };

    let (three, two) = (ink_at_head(THRICE_SLIDER), ink_at_head(TWICE_SLIDER));
    assert!(
        three > two,
        "three slides turn at the head as well, so that end carries an arrow too: {three} vs {two}"
    );
}

#[test]
fn the_combo_number_goes_the_instant_the_note_is_judged() {
    // Instafade: the number is a label on a target, and once the target has
    // been taken it answers a question nobody is asking. Stretched and faded
    // out along with the circle it just smears. The circle still swells.
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default().with_font(font()));
    let layout = Layout::new(640, 480);
    let object = &state.timeline().objects[0];

    // The number sits at the centre in the border colour — near-white against
    // the combo colour of the circle around it.
    let (x, y) = layout.map(object.pos);
    let pale_at = |t: f64| {
        let frame = scene.frame(t, &layout);
        let mut count = 0;
        for dy in -8i32..=8 {
            for dx in -8i32..=8 {
                let p = frame
                    .pixel((x as i32 + dx) as u32, (y as i32 + dy) as u32)
                    .expect("inside the frame");
                if p.red() > 200 && p.green() > 200 && p.blue() > 200 {
                    count += 1;
                }
            }
        }
        count
    };

    let resolved = object.start_ms + state.difficulty().hit_window_50();
    assert!(
        pale_at(object.start_ms) > 0,
        "the number is up while it is a target"
    );
    assert_eq!(
        pale_at(resolved + 20.0),
        0,
        "and gone the moment it is judged"
    );
}

#[test]
fn the_arrow_takes_the_skins_colour_not_the_one_it_was_drawn_in() {
    // The shape came from a black icon; only the silhouette was taken, so the
    // arrow is filled with the skin's border colour. Nothing in the renderer
    // ever sees that black, and a skin can change the arrow's colour without
    // anyone re-exporting anything.
    let map = beatmap(THRICE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::nineteen_eightyfour();
    let want = skin.circle_border.to_color_u8();
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let object = &state.timeline().objects[0];
    let path = match &object.kind {
        dossier_sim::TimedKind::Slider { path, .. } => path,
        _ => unreachable!("this map is one slider"),
    };
    let (x, y) = layout.map(path.position_at(1.0).expect("the slider has an end"));
    let frame = scene.frame(object.start_ms + 200.0, &layout);

    let mut matched = 0;
    for dy in -14i32..=14 {
        for dx in -14i32..=14 {
            let p = frame
                .pixel((x as i32 + dx) as u32, (y as i32 + dy) as u32)
                .expect("inside the frame");
            if p.red() == want.red() && p.green() == want.green() && p.blue() == want.blue() {
                matched += 1;
            }
        }
    }
    assert!(
        matched > 40,
        "the arrow is drawn in the skin's colour: {matched} pixels"
    );
}

/// The same, with a tempo: 500ms to the beat, so beats land on 8500 and 9000.
const BREAK_MAP_TIMED: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5

[TimingPoints]
0,500,4,2,0,60,1,0

[Events]
2,3000,9000

[HitObjects]
100,100,2000,1,0
400,300,12000,1,0
";

#[test]
fn the_break_arrows_pulse_on_the_map_s_own_beat() {
    // The music does not stop during a break, so the beat is the one clock the
    // player is still reading. A cue riding it says something they can already
    // feel; a blink of its own competes with the music instead.
    let map = beatmap(BREAK_MAP_TIMED);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);

    let glow = |t: f64| {
        scene
            .frame(t, &layout)
            .pixels()
            .iter()
            .map(|p| u32::from(p.red()) + u32::from(p.green()) + u32::from(p.blue()))
            .sum::<u32>()
    };

    let on_beat = glow(8500.0);
    let between = glow(8980.0);
    assert!(
        on_beat > between,
        "brightest on the beat: {on_beat} against {between} just before the next"
    );
}

/// Two notes with a declared break between them. The second sits far enough
/// past the break that it has not spawned when the break ends — otherwise
/// "the arrows are gone" and "the note is here" cannot be told apart.
const BREAK_MAP: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5

[Events]
2,3000,9000

[HitObjects]
100,100,2000,1,0
400,300,12000,1,0
";

#[test]
fn a_break_puts_arrows_up_before_the_map_resumes() {
    // A break is the one stretch where the rhythm stops saying when the next
    // note is coming, so the game has to say it instead. The arrows blink to
    // catch an eye that has stopped watching, and the blinking strengthens as
    // the break runs out.
    let map = beatmap(BREAK_MAP);
    assert_eq!(map.breaks, vec![(3000.0, 9000.0)], "the break parsed");

    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background.to_color_u8();
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    let ink = |t: f64| {
        scene
            .frame(t, &layout)
            .pixels()
            .iter()
            .filter(|p| {
                p.red() != background.red()
                    || p.green() != background.green()
                    || p.blue() != background.blue()
            })
            .count()
    };
    // Summed rather than counted, because the ramp is a change of brightness
    // and an anti-aliased edge covers the same pixels whatever its alpha.
    let glow = |t: f64| {
        scene
            .frame(t, &layout)
            .pixels()
            .iter()
            .map(|p| u32::from(p.red()) + u32::from(p.green()) + u32::from(p.blue()))
            .sum::<u32>()
    };

    assert_eq!(ink(4000.0), 0, "nothing yet — the break has just begun");
    assert!(ink(8800.0) > 0, "arrows before the map resumes");
    // This map states no timing at all, so there is no beat to pulse on and
    // the arrows hold still rather than inventing a tempo.
    let steady: Vec<u32> = (0..12)
        .map(|i| glow(8300.0 + f64::from(i) * 25.0))
        .collect();
    assert!(
        steady.iter().all(|g| *g == steady[0]),
        "no timing, no pulse: {steady:?}"
    );

    // Play resumes: they go quickly, but they go rather than blink out.
    assert!(ink(9050.0) > 0, "still on their way out just after");
    assert_eq!(ink(9300.0), 0, "and gone once the exit has run");
}

#[test]
fn the_break_arrows_sit_outside_the_field_and_inside_the_frame() {
    // Their tips touch the field's edge and their bodies are wholly outside
    // it, so nothing about the map is ever behind them. And they must survive
    // the frame: placed from the arrow's own size, which follows the circle
    // radius, they move with it rather than being fixed where one map put them.
    let map = beatmap(BREAK_MAP_TIMED);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background.to_color_u8();
    let scene = Scene::new(&state, skin);

    for (w, h) in [(640u32, 480u32), (1280, 720), (1920, 1080)] {
        let layout = Layout::new(w, h);
        let frame = scene.frame(8500.0, &layout); // on the beat, brightest
        let (x0, y0) = layout.map(dossier_beatmap::Point { x: 0.0, y: 0.0 });
        let (x1, y1) = layout.map(dossier_beatmap::Point {
            x: dossier_beatmap::PLAYFIELD_WIDTH,
            y: dossier_beatmap::PLAYFIELD_HEIGHT,
        });

        // Tolerance in osu!pixels rather than screen ones, so it means the
        // same thing at every size: the tips are placed to touch the edge, and
        // a touch drawn with a rounded stroke lands a hair over it.
        let slack = layout.length(5.0);
        let (mut inside, mut outside, mut on_the_border) = (0, 0, 0);
        for y in 0..h {
            for x in 0..w {
                let p = frame.pixel(x, y).expect("inside the frame");
                if p.red() == background.red()
                    && p.green() == background.green()
                    && p.blue() == background.blue()
                {
                    continue;
                }
                let (fx, fy) = (x as f32, y as f32);
                if fx > x0 + slack && fx < x1 - slack && fy > y0 + slack && fy < y1 - slack {
                    inside += 1;
                } else {
                    outside += 1;
                }
                if x == 0 || y == 0 || x == w - 1 || y == h - 1 {
                    on_the_border += 1;
                }
            }
        }

        assert!(outside > 0, "{w}x{h}: the arrows are up");
        assert_eq!(inside, 0, "{w}x{h}: none of them intrudes on the field");
        assert_eq!(on_the_border, 0, "{w}x{h}: nor cut off by the frame");
    }
}
