//! Frame tests.
//!
//! A renderer can't be checked by comparing pixels to a reference without
//! pinning every colour choice for ever, so these ask the questions that stay
//! true whatever the look: is anything drawn, is it drawn *when* it should be,
//! and does it land where the playfield says.

use dossier_beatmap::Beatmap;
use dossier_render::{Effects, Layout, Scene, Skin};
use dossier_replay::{bits, Mods};
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
///
/// On the map's own colours, which is also the skin without a playfield outline.
/// The 1984 skin draws one on every frame, which would make "nothing is drawn
/// yet" fail on a constant and put a symmetric rectangle into every centroid.
fn drawn(map: &Beatmap, time_ms: f64) -> usize {
    drawn_with(map, time_ms, Mods::default())
}

fn drawn_with(map: &Beatmap, time_ms: f64, mods: Mods) -> usize {
    let state = GameState::from_beatmap(map, mods);
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
        score_info: None,
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

/// Any ink at all in a small box around a playfield point.
///
/// The white probe above cannot see a slider's body: the body is the combo
/// colour with a border that is nowhere near white, which is why a plain
/// slider's tail reads as zero white ink for its whole life. Measuring where
/// the body has grown to needs a probe that counts anything that is not the
/// background.
fn ink_at(map: &Beatmap, time_ms: f64, x: f64, y: f64) -> usize {
    let state = GameState::from_beatmap(map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);
    let frame = scene.frame(time_ms, &layout);
    let (cx, cy) = layout.map(dossier_beatmap::Point { x, y });
    // The frame's own pixels are 8-bit; the skin's colour is not. Comparing
    // them means bringing the background down to the frame's units rather than
    // the other way round, which is what the white probe above does too.
    let background = Skin::default().background;
    let level = |c: f32| (c * 255.0).round() as i32;
    let (br, bg, bb) = (
        level(background.red()),
        level(background.green()),
        level(background.blue()),
    );

    let mut count = 0;
    for dy in -6i32..6 {
        for dx in -6i32..6 {
            let Some(p) = frame.pixel((cx as i32 + dx) as u32, (cy as i32 + dy) as u32) else {
                continue;
            };
            let off = (i32::from(p.red()) - br).abs()
                + (i32::from(p.green()) - bg).abs()
                + (i32::from(p.blue()) - bb).abs();
            // A few levels of anti-aliasing noise is not a slider.
            if off > 12 {
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
    // once they are already on it. The arrow sits at the turn and the turn is
    // the tail, so this only holds while the body reaches the tail early —
    // which it does, over the first third of the approach.
    //
    // Against a slider that does *not* repeat, rather than against zero. The
    // tail carries the body's own white border cap, so "some near-white ink is
    // there" was true with no arrow at all — this test passed through a
    // regression that left the arrow dark for the whole approach.
    let turning = white_ink_at(&repeating_slider(2), 700.0, 240.0, 192.0);
    let plain = white_ink_at(&repeating_slider(1), 700.0, 240.0, 192.0);
    assert!(
        turning > plain,
        "the arrow is up on the approach: {turning} against {plain} with no turn"
    );
}

#[test]
fn a_slider_is_whole_from_the_moment_it_appears() {
    // osu! grows a body out of its head as it approaches and retracts it behind
    // the ball as it is played, and both are on by default there. Off here,
    // asked for: growth is a cue about *where a slider goes*, aimed at somebody
    // who has to read it in the half second before they hit it, and retraction
    // says how much is left. A viewer has neither job, and both movements read
    // as the shape changing under them.
    //
    // Measured at the tail, a quarter of the way into an approach that used to
    // leave it empty.
    let map = repeating_slider(1);
    assert!(
        ink_at(&map, -100.0, 240.0, 192.0) > 0,
        "the tail is there as soon as the slider is"
    );
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

// ── colours ──────────────────────────────────────────────────────────────

#[test]
fn the_maps_own_combo_colour_is_what_gets_drawn() {
    // A mapper chose these. This used to be half of a test that also checked
    // the house skin overriding them on purpose; the house skin is gone, and
    // what it was contrasted against is the part worth keeping.
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

    let (_, green, _) = at_note(Skin::with_combo_colours(map.combo_colours()));
    assert!(green > 200, "the map asked for green");
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
        scene.draw_into(&mut pixmap, 1000.0 + f64::from(i), &layout, None);
    }
    let whole = mark.elapsed().as_secs_f64() / f64::from(rounds) * 1000.0;

    // A scene with no font draws everything but the HUD and the numbers.
    let bare = Scene::new(&state, Skin::default());
    let mark = Instant::now();
    for i in 0..rounds {
        bare.draw_into(&mut pixmap, 1000.0 + f64::from(i), &layout, None);
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

#[test]
fn a_slider_does_not_change_shape_while_it_is_watched() {
    // Neither end moves: what is drawn while the body is still fading in
    // reaches as far as what is drawn at the moment it is due.
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    // Compared by *reach* rather than by ink, and at two moments when the body
    // is at full strength: while it is still fading in its dim edge falls below
    // any threshold, and that is the fade rather than the length.
    let reach = |t: f64| {
        let frame = scene.frame(t, &layout);
        let bg = background.to_color_u8();
        (0..frame.width())
            .filter(|&x| {
                (0..frame.height()).any(|y| {
                    frame.pixel(x, y).is_some_and(|p| {
                        p.red() != bg.red() || p.green() != bg.green() || p.blue() != bg.blue()
                    })
                })
            })
            .count() as i64
    };
    // Two moments inside the slide, both long past the head's own fade — its
    // glow reaches further than the body does and would be measured as length.
    // Between them the body used to retract behind the ball.
    let object = &state.timeline().objects[0];
    let span = object.end_ms - object.start_ms;
    let early = reach(object.start_ms + span * 0.4);
    let late = reach(object.start_ms + span * 0.8);
    assert!(
        early > 0 && (early - late).abs() <= 4,
        "{early} against {late}"
    );
}

/// How far across the frame anything is drawn, as a count of columns with ink.
///
/// A length rather than an area: a body that has retracted is *shorter*, and
/// ink alone would also count the head's fade and the ball's own glow.
fn reach(frame: &tiny_skia::Pixmap, background: tiny_skia::Color) -> i64 {
    let bg = background.to_color_u8();
    (0..frame.width())
        .filter(|&x| {
            (0..frame.height()).any(|y| {
                frame.pixel(x, y).is_some_and(|p| {
                    p.red() != bg.red() || p.green() != bg.green() || p.blue() != bg.blue()
                })
            })
        })
        .count() as i64
}

#[test]
fn a_slider_retracts_behind_the_ball_when_asked() {
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let mut skin = Skin::default();
    Effects::apply(&mut skin, "snake-out");
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    // Both moments are inside the slide and long past the head's own fade, so
    // what differs between them is the body and not the note that started it.
    let object = &state.timeline().objects[0];
    let span = object.end_ms - object.start_ms;
    let early = reach(
        &scene.frame(object.start_ms + span * 0.4, &layout),
        background,
    );
    let late = reach(
        &scene.frame(object.start_ms + span * 0.8, &layout),
        background,
    );

    assert!(
        late + 20 < early,
        "four fifths through, most of the body is behind the ball: {late} against {early}"
    );
}

#[test]
fn a_slider_grows_out_of_its_head_when_asked() {
    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let mut skin = Skin::default();
    Effects::apply(&mut skin, "snake-in");
    let background = skin.background;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);

    // Just after it appears against just before it is due: the growth window is
    // the first third of the approach, so the second moment is a full body.
    let object = &state.timeline().objects[0];
    let approach = state.timeline().objects[0].start_ms - 1200.0;
    let young = reach(&scene.frame(approach.max(0.0) + 60.0, &layout), background);
    let due = reach(&scene.frame(object.start_ms - 20.0, &layout), background);

    assert!(young > 0 && young + 20 < due, "{young} against {due}");
}

#[test]
fn neither_end_of_a_slider_moves_unless_it_is_asked_for() {
    let skin = Skin::default();
    assert!(!skin.snake_in, "growth is off by default");
    assert!(!skin.snake_out, "and so is retraction");
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
    // Sampled at the head's rim rather than inside it. The body runs through
    // the head at the same width, so a probe *within* the circle now reads the
    // body's own lit centre — which is brighter than the head sitting on it,
    // and is meant to be. The rim is where the two differ: a head draws its
    // border there, and a body draws its darkest shade.
    let radius = state.difficulty().circle_radius() * 0.98;
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
fn a_tick_still_waits_for_its_own_moment() {
    // Ticks used to be drawn as soon as the note appeared, which put dots in
    // empty space in front of a slider that had not grown that far. The body no
    // longer grows, so that emptiness is gone — but a tick still has a moment
    // of its own to arrive at, and arriving early would make a slider look like
    // it were already being played.
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
    let bg = background.to_color_u8();
    let lit = |t: f64| {
        let frame = scene.frame(t, &layout);
        let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
        i32::from(p.red()) - i32::from(bg.red()) + i32::from(p.green()) - i32::from(bg.green())
            + i32::from(p.blue())
            - i32::from(bg.blue())
    };
    assert!(
        lit(far_tick - 2000.0) < lit(far_tick - 50.0),
        "{} against {}",
        lit(far_tick - 2000.0),
        lit(far_tick - 50.0)
    );
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
        // Just after the first turn, plus the arrival fade: that is when the
        // head end's arrow is due — one traversal before its own turn — and it
        // now eases in rather than snapping on, so it needs its fade to have
        // run before there is full-brightness ink to count. Earlier than the
        // turn it must NOT be up at all: the head circle sits at that exact
        // spot, and an arrow standing there from the start appears underneath
        // the note.
        let t = object.start_ms + (object.end_ms - object.start_ms) / 3.0 + 180.0;
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
    let skin = Skin::default();
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

/// One spinner, alone, so nothing else can account for a changed pixel.
const LONE_SPINNER: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5
OverallDifficulty:5

[HitObjects]
256,192,2000,12,0,6000
";

#[test]
fn the_spinner_ring_closes_onto_its_centre_mark() {
    // A ring shrinking towards nothing says only that it is shrinking; one
    // arriving at a mark says how far it still has to go. So the mark has to
    // be there from the start, and the ring has to reach it.
    let map = beatmap(LONE_SPINNER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::default();
    let background = skin.background.to_color_u8();
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(640, 480);
    let object = &state.timeline().objects[0];

    // How far the drawn ink reaches from the centre of the field.
    let reach = |t: f64| {
        let frame = scene.frame(t, &layout);
        let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
        let mut furthest = 0.0f32;
        for y in 0..frame.height() {
            for x in 0..frame.width() {
                let p = frame.pixel(x, y).expect("inside the frame");
                if p.red() == background.red()
                    && p.green() == background.green()
                    && p.blue() == background.blue()
                {
                    continue;
                }
                let (dx, dy) = (x as f32 - cx, y as f32 - cy);
                furthest = furthest.max((dx * dx + dy * dy).sqrt());
            }
        }
        furthest
    };

    let opening = reach(object.start_ms + 50.0);
    let closing = reach(object.end_ms - 50.0);
    assert!(
        opening > closing,
        "the ring closes: {opening} then {closing}"
    );

    // …and it closes onto the mark rather than past it or short of it. The
    // mark's own outer edge is what is left at the end.
    let mark = layout.length(20.0);
    assert!(
        (closing - mark).abs() < layout.length(6.0),
        "it lands on the mark: {closing} against a mark of {mark}"
    );

    // And the mark is up from the start, not conjured at the end: a target
    // that appears once you have arrived at it was never a target.
    assert!(
        ink_near_centre(&scene, &layout, object.start_ms + 50.0) > 0,
        "the centre mark is there while the ring is still wide"
    );
}

/// Non-background pixels within the centre mark's own radius.
fn ink_near_centre(scene: &Scene<'_>, layout: &Layout, t: f64) -> usize {
    let background = Skin::default().background.to_color_u8();
    let frame = scene.frame(t, layout);
    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
    let r = layout.length(20.0);
    let mut count = 0;
    for y in 0..frame.height() {
        for x in 0..frame.width() {
            let (dx, dy) = (x as f32 - cx, y as f32 - cy);
            if (dx * dx + dy * dy).sqrt() > r {
                continue;
            }
            let p = frame.pixel(x, y).expect("inside the frame");
            if p.red() != background.red() || p.green() != background.green() {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn no_arrow_stands_under_the_head_while_the_first_slide_runs() {
    // The head end of a slider is exactly where its head circle sits. An arrow
    // that goes up as soon as the slider appears therefore sits underneath the
    // note for the whole first slide — which is what it looked like on a
    // three-slide slider: a second arrow appearing under the note.
    let map = beatmap(THRICE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let scene = Scene::new(&state, Skin::default());
    let layout = Layout::new(640, 480);
    let object = &state.timeline().objects[0];
    let span = (object.end_ms - object.start_ms) / 3.0;

    // Near-white is the arrow's colour; the head circle and body are not.
    let white_at_head = |t: f64| {
        let frame = scene.frame(t, &layout);
        let (x, y) = layout.map(object.pos);
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

    assert_eq!(
        white_at_head(object.start_ms + span * 0.4),
        0,
        "the head's turn is two traversals away — nothing belongs there yet"
    );
    // Its fade has to have run: an arrow that becomes due mid-slide now eases
    // in rather than snapping on, so at +60ms it is present but still dim.
    assert!(
        white_at_head(object.start_ms + span + 180.0) > 0,
        "and it arrives once the ball sets off towards it"
    );
}

// ── Hidden ───────────────────────────────────────────────────────────────

#[test]
fn hidden_takes_the_note_away_before_it_is_due() {
    // The mod is a rendering mod and nothing else — it changes what the player
    // could see and not one thing about how the play is judged — so this is
    // the only place it can be tested, and the only place it can be wrong.
    //
    // AR 5, so preempt is 1200ms and the note spawns at 800. Under Hidden it
    // finishes arriving at 800 + 480 and is gone by 800 + 840, which is three
    // tenths of preempt before it is due. Without the mod it is at full
    // opacity there, with an approach circle around it.
    let map =
        beatmap("[Difficulty]\nApproachRate:5\nCircleSize:4\n\n[HitObjects]\n256,192,2000,1,0\n");

    // Just after the fade-in has finished: both are showing something.
    assert!(drawn_with(&map, 1300.0, Mods::default()) > 0);
    assert!(drawn_with(&map, 1300.0, Mods::new(bits::HIDDEN)) > 0);

    // And once Hidden's fade-out has run its course, one of them is empty.
    assert_eq!(
        drawn_with(&map, 1700.0, Mods::new(bits::HIDDEN)),
        0,
        "the note should be gone"
    );
    assert!(
        drawn_with(&map, 1700.0, Mods::default()) > 0,
        "and plainly visible without the mod"
    );
}

#[test]
fn hidden_draws_no_approach_circle() {
    // The half of the mod a player actually feels: `OsuModHidden` implements
    // `IHidesApproachCircles`.
    //
    // Measured at 1280ms, chosen so the comparison cannot be explained by the
    // note: that is exactly where Hidden's fade-in ends, so its note is at
    // full opacity while the plain one is still only three fifths of the way
    // in. The plain frame nonetheless covers more of the screen, and the only
    // thing it has that the other does not is the ring.
    let map =
        beatmap("[Difficulty]\nApproachRate:5\nCircleSize:4\n\n[HitObjects]\n256,192,2000,1,0\n");
    let plain = drawn_with(&map, 1280.0, Mods::default());
    let hidden = drawn_with(&map, 1280.0, Mods::new(bits::HIDDEN));
    assert!(
        plain > hidden,
        "the ring is missing from neither: {plain} against {hidden}"
    );
}

/// Pixels bright enough to be something the game drew at full strength, rather
/// than the ghost of something fading.
/// The pixel at the slider ball, under whichever mods.
///
/// A count of bright pixels across the frame used to do this, and cannot any
/// more: a slider body now carries a bright border of its own, so "bright"
/// no longer separates the parts Hidden fades from the parts it leaves. The
/// ball is drawn opaque over the body, so its own pixel answers the question
/// the test actually asks.
fn ball_pixel(map: &Beatmap, time_ms: f64, mods: Mods) -> (u8, u8, u8) {
    let state = GameState::from_beatmap(map, mods);
    let object = &state.timeline().objects[0];
    let ball = object
        .ball_at(time_ms)
        .expect("the slider is still running");
    let skin = Skin::with_combo_colours(map.combo_colours());
    let layout = Layout::new(320, 240);
    let frame = Scene::new(&state, skin).frame(time_ms, &layout);
    let (x, y) = layout.map(ball);
    let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

#[test]
fn hidden_leaves_the_ball_and_the_arrow_alone() {
    // The mod fades the body, the ticks and the head. Its own source says so
    // of the arrows outright — "reverse arrow is not affected by hidden" — and
    // the ball and its follow circle are not in the switch at all. It has to
    // be that way round to be playable: the body is what the mod takes away,
    // and the ball is what is left to follow once it has gone.
    //
    // A slider with a repeat, read near its end, where its body has all but
    // dissolved. The ball is drawn over that body at full opacity, so it must
    // look the same with the mod as without.
    let map = beatmap(
        "[Difficulty]\nApproachRate:5\nCircleSize:4\nSliderMultiplier:1.0\nSliderTickRate:1\n\n         [TimingPoints]\n0,500,4,2,0,100,1,0\n\n         [HitObjects]\n100,192,2000,2,0,L|300:192,2,100\n",
    );

    let plain = ball_pixel(&map, 2800.0, Mods::default());
    let hidden = ball_pixel(&map, 2800.0, Mods::new(bits::HIDDEN));
    assert_ne!(plain, (0, 0, 0), "the fixture draws no ball at all");
    assert_eq!(
        hidden, plain,
        "Hidden dimmed the ball, which it does not touch"
    );
}

const THREE_CIRCLES: &str = "
[Difficulty]
CircleSize:5
ApproachRate:5

[HitObjects]
256,192,5000,1,0
256,192,6000,1,0
256,192,7000,1,0
";

fn brightness(frame: &tiny_skia::Pixmap) -> f64 {
    let sum: u64 = frame
        .pixels()
        .iter()
        .map(|p| u64::from(p.red()) + u64::from(p.green()) + u64::from(p.blue()))
        .sum();
    sum as f64 / frame.pixels().len() as f64
}

/// A scene whose play stops after one note of three, which is what gives it
/// an ending for the fail animation to run from.
fn failed_scene(map: &Beatmap) -> (GameState, Skin) {
    let mut replay = replay_over(vec![
        dossier_replay::ReplayFrame {
            time_ms: 5000,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        dossier_replay::ReplayFrame {
            time_ms: 5040,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    // The header says one object was judged where the map has three. That
    // difference is the whole definition of a play that ended early.
    replay.hits.count_300 = 1;
    let state = GameState::new(map, &replay);
    let skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    (state, skin)
}

#[test]
fn the_play_comes_up_from_black_rather_than_cutting_in() {
    // A replay, because the HUD is what is on screen from the first frame —
    // the notes are still a preempt away either way, so they cannot show that
    // the opening fades.
    let map = beatmap(THREE_CIRCLES);
    let (state, skin) = failed_scene(&map);
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(320, 240);
    let (from, _) = state.span_ms();

    let opening = brightness(&scene.frame(from + 20.0, &layout));
    let midway = brightness(&scene.frame(from + 200.0, &layout));
    let settled = brightness(&scene.frame(from + 600.0, &layout));

    assert!(
        opening < midway && midway < settled,
        "the opening should climb: {opening:.3} → {midway:.3} → {settled:.3}"
    );
}

#[test]
fn the_failed_frame_goes_black_the_instant_it_springs_back() {
    // The frame does not fade during the movement — it springs back to size
    // with everything still on it — and the instant it lands, the frame is
    // black. One beat: the arrival and the cut are the same moment.
    //
    // It used to clear over a fifth of a second, on the reasoning that a hard
    // cut there would read as a dropped frame. Watched, it did not: a fade
    // after an arrival is a second, smaller ending trailing the first.
    let map = beatmap(THREE_CIRCLES);
    let (state, skin) = failed_scene(&map);
    let end = state
        .ending()
        .expect("a play that stopped early has an ending")
        .time_ms;
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(320, 240);

    let animation = dossier_render::FAIL_ANIMATION_MS;

    // The last frame of the movement still holds the play…
    let released = brightness(&scene.frame(end + animation - 1.0, &layout));
    // …and the first one after it holds nothing.
    let gone = brightness(&scene.frame(end + animation + 1.0, &layout));

    assert!(
        released > 0.0,
        "the frame should still hold the play when it lets go"
    );

    // "Nothing left" is the background, which is not black — an empty frame is
    // what the renderer fills before it draws anything at all.
    let empty = {
        let mut blank = tiny_skia::Pixmap::new(320, 240).expect("a frame");
        blank.fill(Skin::default().background);
        brightness(&blank)
    };
    assert_eq!(
        gone, empty,
        "the frame did not clear when the movement landed"
    );
}

#[test]
fn nofail_takes_the_health_bar_and_the_warning_off() {
    // The warning is the clear case: red from the edges means *this is about to
    // end*, and under NoFail it never was. A warning that cannot come true is
    // worse than none, because a viewer who learns to discount it discounts the
    // real one too.
    let map = beatmap(THREE_CIRCLES);
    let (state, skin) = failed_scene(&map);
    let plain = brightness(&Scene::new(&state, skin.clone()).frame(5200.0, &Layout::new(320, 240)));

    let mut with_nofail = replay_over(vec![
        dossier_replay::ReplayFrame {
            time_ms: 5000,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        dossier_replay::ReplayFrame {
            time_ms: 5040,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    with_nofail.hits.count_300 = 1;
    with_nofail.mods = Mods::new(dossier_replay::bits::NO_FAIL);
    let nofail_state = GameState::new(&map, &with_nofail);
    let quiet = brightness(&Scene::new(&nofail_state, skin).frame(5200.0, &Layout::new(320, 240)));

    assert!(
        quiet < plain,
        "a NoFail frame should carry less: {quiet:.3} against {plain:.3}"
    );
}

#[test]
fn the_field_is_offset_in_osu_pixels_rather_than_in_frame_pixels() {
    // danser's `SetOsuViewport` shifts the playfield down by eight *osu!pixels*,
    // scaled with everything else. Written as a fraction of the frame height it
    // agrees only at 16:9 and diverges everywhere else — 80% too low on a tall
    // frame, nearly triple on a portrait one — which turns the layout from a
    // property of the game into a property of the window.
    let map = beatmap(ONE_CIRCLE);
    let state = GameState::from_beatmap(&map, Mods::default());
    let skin = Skin::with_combo_colours(map.combo_colours());
    let scene = Scene::new(&state, skin);

    // The same field at three shapes. In each, the offset below centre has to be
    // eight osu!pixels — which is `layout.length(8.0)`.
    for (w, h) in [(1920u32, 1080u32), (960, 1080), (1080, 1920)] {
        let layout = Layout::new(w, h);
        let (_, centre_y) = layout.map(dossier_beatmap::Point { x: 256.0, y: 192.0 });
        let offset = centre_y - h as f32 / 2.0;
        let expected = layout.length(8.0);
        assert!(
            (offset - expected).abs() < 0.01,
            "{w}x{h}: field centre is {offset:.2}px below the frame's, wanted {expected:.2}px"
        );
        let _ = &scene;
    }
}

/// A map that is one long spinner.
const ONE_SPINNER: &str = "
[Difficulty]
CircleSize:4
ApproachRate:8
OverallDifficulty:6

[HitObjects]
256,192,2000,12,0,6000
";

#[test]
fn hidden_does_not_take_the_spinner_away() {
    // Hidden removes what you would otherwise read ahead. A spinner has nothing
    // to read ahead — it is a thing you are already doing — and osu!'s own mod
    // does not touch it. Fading it like a note left the whole spinner section as
    // a black screen with a cursor circling in it.
    let map = beatmap(ONE_SPINNER);
    let skin = Skin::with_combo_colours(map.combo_colours());

    let plain = GameState::from_beatmap(&map, Mods::default());
    let hidden = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::HIDDEN));
    let layout = Layout::new(320, 240);

    // Well into the spinner, where Hidden's fade would long since have finished.
    let without = brightness(&Scene::new(&plain, skin.clone()).frame(5000.0, &layout));
    let with = brightness(&Scene::new(&hidden, skin).frame(5000.0, &layout));

    assert!(without > 0.0, "the spinner should be drawn at all");
    assert!(
        (with - without).abs() < without * 0.05,
        "Hidden should leave the spinner alone: {with:.3} against {without:.3}"
    );
}

#[test]
fn the_play_goes_out_the_way_it_came_in() {
    // A render that ends on a hard cut reads as a file that was trimmed rather
    // than as a run that finished — the mirror of why it fades in.
    // A replay that saw the map out, so the HUD is still up after the last note
    // and there is something left for the fade to take. Without one the frame is
    // already empty by then and the fade has nothing to do.
    let map = beatmap(THREE_CIRCLES);
    let mut replay = replay_over(vec![
        dossier_replay::ReplayFrame {
            time_ms: 5000,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        dossier_replay::ReplayFrame {
            time_ms: 5040,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    replay.hits.count_300 = 3;
    let state = GameState::new(&map, &replay);
    let skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(320, 240);
    let (_, to) = state.span_ms();

    // After the last note, never over it — the render carries a tail for this,
    // so the fade lives past the end of the play rather than across its finish.
    let settled = brightness(&scene.frame(to - 50.0, &layout));
    let going = brightness(&scene.frame(to + dossier_render::OUTRO_FADE_MS * 0.5, &layout));
    let last = brightness(&scene.frame(to + dossier_render::OUTRO_FADE_MS * 0.95, &layout));

    assert!(
        last < going && going < settled,
        "the close should dim: {settled:.3} → {going:.3} → {last:.3}"
    );
}

#[test]
fn a_failed_play_is_not_faded_out_as_well() {
    // It has its own ending — the frame closes in, springs back and clears — and
    // fading that too would be two endings on top of each other.
    let map = beatmap(THREE_CIRCLES);
    let (state, skin) = failed_scene(&map);
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(320, 240);
    let (_, to) = state.span_ms();

    let early = brightness(&scene.frame(to - 50.0, &layout));
    let late = brightness(&scene.frame(to + dossier_render::OUTRO_FADE_MS * 0.95, &layout));
    assert!(
        (late - early).abs() < early * 0.35,
        "no second fade on a failed play: {early:.3} against {late:.3}"
    );
}

#[test]
fn hidden_fades_a_slider_body_slowly_and_its_head_like_a_note() {
    // Two cases in the mod, two schedules here. Sharing one opacity dimmed the
    // head on the body's timetable, so on a long slider the note about to be
    // clicked was already half gone.
    let map = repeating_slider(1);
    let hidden = GameState::from_beatmap(&map, Mods::new(dossier_replay::bits::HIDDEN));
    let skin = Skin::with_combo_colours(map.combo_colours());
    let scene = Scene::new(&hidden, skin);

    // The head is a note: under Hidden it is gone before it is due, which is the
    // whole of the mod. The body is still dissolving at that moment, because it
    // has the length of the slider to do it in.
    let object = &hidden.timeline().objects[0];
    let at = object.start_ms - 30.0;
    let head = scene.head_alpha_for_test(0, at);
    let body = scene.alpha_for_test(0, at);
    assert_eq!(head, 0.0, "the head goes on the note's schedule");
    assert!(
        body > 0.2,
        "while the body is still on its way out: {body:.3}"
    );

    // And they are the same until the fade-out begins — the difference is when
    // each one leaves, not how either arrives.
    let arriving = object.start_ms - hidden.difficulty().preempt_ms() + 1.0;
    assert!(
        (scene.head_alpha_for_test(0, arriving) - scene.alpha_for_test(0, arriving)).abs() < 0.01
    );
}

// ── a skin the player brought ────────────────────────────────────────────
//
// The engine draws every element itself and always could. These are about the
// other way in: the files a player already has, drawn in place of our shapes.
// What is checked is not that a picture appears somewhere — it is that the
// skin's own decisions survive, including the decision to show nothing.

fn skin_folder(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("dossier-frame-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("a folder");
    dir
}

/// A flat square at `alpha`, white — which is how a skin ships an element the
/// game is going to tint.
fn write_element(dir: &std::path::Path, name: &str, size: u32, alpha: u8) {
    let mut pixmap = tiny_skia::Pixmap::new(size, size).expect("a canvas");
    for pixel in pixmap.pixels_mut() {
        *pixel = tiny_skia::PremultipliedColorU8::from_rgba(alpha, alpha, alpha, alpha)
            .expect("a colour");
    }
    std::fs::write(dir.join(name), pixmap.encode_png().expect("png")).expect("written");
}

fn one_note() -> Beatmap {
    beatmap(
        "
[Difficulty]
ApproachRate:5
CircleSize:4

[Colours]
Combo1 : 0,255,0

[HitObjects]
256,192,5000,5,0
",
    )
}

/// The bare field, which every frame is filled with before anything is drawn.
///
/// "Nothing was drawn here" has to be measured against this and not against the
/// alpha channel: a frame is opaque everywhere, so an alpha of 255 says only
/// that a frame exists. Two tests below were written that way first and passed
/// without testing anything.
const FIELD: (u8, u8, u8) = (12, 12, 16);

fn note_pixel(skin: Skin) -> (u8, u8, u8) {
    let map = one_note();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, skin).frame(5000.0, &layout);
    let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
    let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

fn with_sprites(dir: &std::path::Path, map: &Beatmap) -> Skin {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let wanted = [Element::HitCircle, Element::HitCircleOverlay];
    let sprites = Sprites::read(dir, &wanted).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    skin
}

#[test]
fn a_skins_own_hit_circle_is_drawn_in_place_of_ours() {
    // White art, a green combo: the note has to come out green. Left untinted
    // it would be white on every combo of every map.
    let dir = skin_folder("drawn");
    write_element(&dir, "hitcircle.png", 128, 255);
    let (r, g, b) = note_pixel(with_sprites(&dir, &one_note()));

    assert!(
        g > 200,
        "the skin's note is there, in the map's combo colour"
    );
    assert!(
        r < 60 && b < 60,
        "not the white it was drawn in: {r},{g},{b}"
    );
}

#[test]
fn a_skin_that_turned_the_note_off_gets_an_empty_field() {
    // The whole reason blank and absent are kept apart. This is not a corner
    // case: the skin this was written against ships a fully transparent
    // `hitcircle`, reads its notes off the combo numbers, and drawing our own
    // circle there would put back the thing its author deleted.
    let dir = skin_folder("silenced");
    write_element(&dir, "hitcircle.png", 128, 0);
    assert_eq!(
        note_pixel(with_sprites(&dir, &one_note())),
        FIELD,
        "the bare field, where the note would have been"
    );
}

#[test]
fn a_skin_that_says_nothing_leaves_the_note_to_us() {
    // An empty folder is not a skin that hides everything — it is a skin with
    // nothing to say, and every element stays ours to draw.
    let dir = skin_folder("empty");
    assert_ne!(
        note_pixel(with_sprites(&dir, &one_note())),
        FIELD,
        "our own circle is still drawn"
    );
}

/// How far the ink reaches to the right of the note's centre, in pixels.
fn ink_reach(dir: &std::path::Path) -> usize {
    let map = one_note();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
    let frame = Scene::new(&state, with_sprites(dir, &map)).frame(5000.0, &layout);
    (0..300)
        .take_while(|step| {
            frame
                .pixel(cx as u32 + step, cy as u32)
                .is_some_and(|p| (p.red(), p.green(), p.blue()) != FIELD)
        })
        .count()
}

#[test]
fn a_bigger_file_is_a_bigger_element_because_that_is_what_it_means() {
    // Everything in a skin is proportioned against a 128-pixel hit circle, so
    // the file's own size is not incidental — it is the size. This is why the
    // skin this was written against has a 320px `hitcircleoverlay` over a 128px
    // circle and comes out with a rim wider than the note.
    //
    // Written the other way round first, asserting that file size does not
    // matter, which contradicted the rule the renderer documents. The test was
    // wrong, not the renderer.
    let small = skin_folder("small");
    write_element(&small, "hitcircle.png", 128, 255);
    let big = skin_folder("big");
    write_element(&big, "hitcircle.png", 512, 255);

    let (near, far) = (ink_reach(&small), ink_reach(&big));
    let ratio = far as f32 / near as f32;
    assert!(
        (3.5..4.5).contains(&ratio),
        "four times the file, four times the element — got {near} against {far}"
    );
}

#[test]
fn the_high_resolution_suffix_is_what_normalises_a_size() {
    // `@2x` is the one thing that says "this file holds two pixels per skin
    // pixel". A 256px `@2x` and a 128px plain file are the same element at the
    // same size, and only the suffix distinguishes that from the case above.
    let plain = skin_folder("plain-size");
    write_element(&plain, "hitcircle.png", 128, 255);
    let double = skin_folder("double-size");
    write_element(&double, "hitcircle@2x.png", 256, 255);

    let (a, b) = (ink_reach(&plain), ink_reach(&double));
    assert!(
        a.abs_diff(b) <= 2,
        "the same element at the same size: {a} against {b}"
    );
}

#[test]
fn a_skins_digits_are_drawn_where_it_asked_for_them() {
    // For an instafade skin the combo number *is* the note: the hit circle is
    // blank and each digit carries a whole ring, which vanishes on the click
    // because a number is taken away the instant a note is judged.
    let dir = skin_folder("digits");
    for digit in 0..10 {
        write_element(&dir, &format!("default-{digit}.png"), 64, 255);
    }
    std::fs::write(dir.join("skin.ini"), "[Fonts]\nHitCircleOverlap: 0\n").expect("written");

    // Nothing but digits in the folder, so anything drawn at the note is one.
    let map = one_note();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, with_digits(&dir, &map)).frame(5000.0, &layout);
    let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
    let p = frame.pixel(x as u32, y as u32).expect("inside the frame");
    assert_ne!(
        (p.red(), p.green(), p.blue()),
        FIELD,
        "the skin's own figure is on the note"
    );
}

#[test]
fn an_overlap_as_wide_as_the_digit_stacks_the_figures() {
    // The skin this was written against sets 160 against 160-pixel digits.
    // Read literally that is no advance at all, and a two-figure combo comes
    // out as one ring rather than two side by side — which is the point, since
    // each digit carries a ring. A "sensible" clamp would draw two.
    // Deliberately wider than a note: the folder holds no `hitcircle`, so the
    // engine still draws its own circle underneath, and figures smaller than
    // that circle would be measuring the circle rather than the layout.
    let stacked = skin_folder("stacked");
    let spread = skin_folder("spread");
    for dir in [&stacked, &spread] {
        for digit in 0..10 {
            write_element(dir, &format!("default-{digit}.png"), 256, 255);
        }
    }
    std::fs::write(stacked.join("skin.ini"), "[Fonts]\nHitCircleOverlap: 256\n").expect("written");
    std::fs::write(spread.join("skin.ini"), "[Fonts]\nHitCircleOverlap: 0\n").expect("written");

    // Twelve notes in one combo, drawn at the twelfth: two figures, which is
    // the only place an overlap can show at all.
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
CircleSize:4

[HitObjects]
256,192,4000,5,0
256,192,4200,1,0
256,192,4400,1,0
256,192,4600,1,0
256,192,4800,1,0
256,192,5000,1,0
256,192,5200,1,0
256,192,5400,1,0
256,192,5600,1,0
256,192,5800,1,0
256,192,6000,1,0
256,192,6200,1,0
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);

    let reach = |dir: &std::path::Path| {
        let frame = Scene::new(&state, with_digits(dir, &map)).frame(6200.0, &layout);
        (0..300)
            .take_while(|step| {
                frame
                    .pixel(cx as u32 + step, cy as u32)
                    .is_some_and(|p| (p.red(), p.green(), p.blue()) != FIELD)
            })
            .count()
    };
    assert!(
        reach(&stacked) < reach(&spread),
        "stacked {} against spread {}",
        reach(&stacked),
        reach(&spread)
    );
}

fn with_digits(dir: &std::path::Path, map: &Beatmap) -> Skin {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let wanted: Vec<Element> = (0..10).map(Element::Digit).collect();
    let sprites = Sprites::read(dir, &wanted).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    skin
}

// ── a slider's own two ends ──────────────────────────────────────────────
//
// osu! lets a skin draw the start and the end of a slider differently from a
// note, and the wiki binds each overlay to its own base: `sliderstartcircle`
// "overrides `hitcircle.png` for the start of the slider, if skinned", and
// `sliderstartcircleoverlay` "requires `sliderstartcircle.png` to function".
//
// Reported against a real skin, which ships a start circle with a distinctly
// thinner rim and blanks its end circle outright. Both decisions were being
// ignored: every slider end wore the note's picture, so a skin that had gone
// to the trouble came out half-applied — the notes were its own and the
// sliders were not.

/// A slider from (100,192) to (240,192), so the two ends can be sampled apart.
fn plain_slider() -> Beatmap {
    beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
SliderMultiplier:1.4
SliderTickRate:1

[Colours]
Combo1 : 0,255,0

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,1000,2,0,L|240:192,1,140
",
    )
}

/// Everything a slider's ends can be drawn from, so a fixture decides what is
/// present by which files it writes rather than by what the reader asks for.
fn slider_wanted() -> Vec<dossier_render::elements::Element> {
    use dossier_render::elements::Element;
    vec![
        Element::HitCircle,
        Element::HitCircleOverlay,
        Element::SliderHead,
        Element::SliderHeadOverlay,
        Element::SliderTail,
        Element::SliderTailOverlay,
    ]
}

fn dressed(dir: &std::path::Path, map: &Beatmap) -> Skin {
    use dossier_render::imported::Sprites;
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let sprites = Sprites::read(dir, &slider_wanted()).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    skin
}

/// The frame at the moment the slider starts, when the body has finished
/// growing and both ends are up.
fn slider_frame(dir: &std::path::Path) -> tiny_skia::Pixmap {
    let map = plain_slider();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    Scene::new(&state, dressed(dir, &map)).frame(1000.0, &layout)
}

/// The colour of one point on the slider's centreline.
///
/// A count of "is anything here" cannot answer this: the body covers both ends
/// and every probe comes back full. What separates a circle from bare body is
/// the colour at the point, and the body's own shade depends only on distance
/// from the centreline — so a point halfway along is what an end looks like
/// with nothing drawn on it.
fn line_pixel(frame: &tiny_skia::Pixmap, x: f64) -> (u8, u8, u8) {
    let layout = Layout::new(640, 480);
    let (cx, cy) = layout.map(dossier_beatmap::Point { x, y: 192.0 });
    let p = frame
        .pixel(cx as u32, cy as u32)
        .expect("the slider is inside the frame");
    (p.red(), p.green(), p.blue())
}

const HEAD_X: f64 = 100.0;
const TAIL_X: f64 = 240.0;
/// Halfway along, where only the body can be.
const BODY_X: f64 = 170.0;

#[test]
fn a_skins_own_start_circle_is_drawn_in_place_of_the_note() {
    // Same map, same moment, two skins differing only in whether the start
    // circle exists. The note's picture is solid and the start circle is
    // faint, so a head drawn from the wrong file is not a near miss.
    let note_only = skin_folder("slider-note-only");
    write_element(&note_only, "hitcircle.png", 128, 255);

    let with_start = skin_folder("slider-own-start");
    write_element(&with_start, "hitcircle.png", 128, 255);
    write_element(&with_start, "sliderstartcircle.png", 128, 60);

    let plain = line_pixel(&slider_frame(&note_only), HEAD_X);
    let own = line_pixel(&slider_frame(&with_start), HEAD_X);
    assert_ne!(plain, own, "the skin's own start circle was ignored");
}

#[test]
fn an_overlay_without_its_own_base_falls_back_to_the_notes_pair() {
    // "Requires `sliderstartcircle.png` to function". So a skin shipping the
    // overlay alone gets the note's pair for both halves — not the note's disc
    // with somebody else's rim over it, which is the shape this would take if
    // the two were resolved one at a time.
    let note_only = skin_folder("slider-pair-base");
    write_element(&note_only, "hitcircle.png", 128, 255);
    write_element(&note_only, "hitcircleoverlay.png", 128, 90);

    let orphan = skin_folder("slider-pair-orphan");
    write_element(&orphan, "hitcircle.png", 128, 255);
    write_element(&orphan, "hitcircleoverlay.png", 128, 90);
    write_element(&orphan, "sliderstartcircleoverlay.png", 128, 20);

    assert_eq!(
        line_pixel(&slider_frame(&note_only), HEAD_X),
        line_pixel(&slider_frame(&orphan), HEAD_X),
        "an overlay with no base of its own changed the head"
    );
}

#[test]
fn the_end_of_a_slider_wears_the_note_when_the_skin_says_nothing() {
    // The end circle is a thing osu! draws and we did not. A skin shipping no
    // `sliderendcircle` still has one — the note's.
    let dir = skin_folder("slider-end-default");
    write_element(&dir, "hitcircle.png", 128, 255);
    let frame = slider_frame(&dir);
    assert_ne!(
        line_pixel(&frame, TAIL_X),
        line_pixel(&frame, BODY_X),
        "the end of the slider is bare body — no circle was drawn there"
    );
}

#[test]
fn an_end_circle_blanked_on_purpose_stays_blank() {
    // The decision this was reported for. A one-pixel transparent
    // `sliderendcircle` is a skin saying "no circle there", and it has to
    // outrank the fallback — otherwise the answer to blanking a file is the
    // note's picture, which is louder than what was blanked.
    let dir = skin_folder("slider-end-hidden");
    write_element(&dir, "hitcircle.png", 128, 255);
    write_element(&dir, "sliderendcircle.png", 1, 0);
    let frame = slider_frame(&dir);
    // Within a hair rather than exactly: the body's round cap and its straight
    // middle round differently by a level or so, and a level is not a circle.
    assert!(
        apart(line_pixel(&frame, TAIL_X), line_pixel(&frame, BODY_X)) <= 6,
        "something was drawn where the skin asked for nothing: {:?} against {:?}",
        line_pixel(&frame, TAIL_X),
        line_pixel(&frame, BODY_X)
    );
}

#[test]
fn our_own_look_still_ends_a_slider_on_its_body() {
    // The engine's own drawing is not a skin and never had an end circle; the
    // body's cap is the end, and that was tuned against danser. Adding the
    // element must not quietly put a note there on every render made without
    // a skin at all.
    let map = plain_slider();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let bare = Scene::new(&state, Skin::default()).frame(1000.0, &layout);
    assert!(
        apart(line_pixel(&bare, TAIL_X), line_pixel(&bare, BODY_X)) <= 6,
        "our own look grew an end circle it never had: {:?} against {:?}",
        line_pixel(&bare, TAIL_X),
        line_pixel(&bare, BODY_X)
    );
}

// ── the trail between notes, and the flash under one ─────────────────────
//
// Two elements osu! has always drawn and this engine never did, added
// together because they are the same shape of thing: a skin's picture placed
// off a rule the game states, and nothing at all when the skin brought none.

/// Two notes in one combo, far enough apart for a trail to fit between them.
fn spaced_pair(new_combo: bool) -> Beatmap {
    beatmap(&format!(
        "
[Difficulty]
CircleSize:4
ApproachRate:5

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,4000,5,0
400,192,5000,{},0
",
        if new_combo { 5 } else { 1 }
    ))
}

fn trail_ink(dir: Option<&std::path::Path>, map: &Beatmap, time_ms: f64) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    if let Some(dir) = dir {
        let wanted = [Element::HitCircle, Element::FollowPoint];
        skin.sprites = Some(std::sync::Arc::new(
            Sprites::read(dir, &wanted).tint_for(&skin.combo_colours),
        ));
    }
    let state = GameState::from_beatmap(map, Mods::default());
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, skin).frame(time_ms, &layout);

    // The middle of the gap, well clear of either note.
    let (cx, cy) = layout.map(dossier_beatmap::Point { x: 250.0, y: 192.0 });
    let mut count = 0;
    for dy in -30i32..30 {
        for dx in -60i32..60 {
            let Some(p) = frame.pixel((cx as i32 + dx) as u32, (cy as i32 + dy) as u32) else {
                continue;
            };
            if p.red() > 40 || p.green() > 40 || p.blue() > 40 {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn a_trail_runs_between_two_notes_of_one_combo() {
    let dir = skin_folder("trail");
    write_element(&dir, "followpoint.png", 32, 255);
    let map = spaced_pair(false);
    // Eight tenths of a second before the second note: inside the eight
    // hundred milliseconds of warning osu! gives each mark.
    assert!(
        trail_ink(Some(&dir), &map, 4600.0) > 0,
        "nothing was drawn between the two notes"
    );
}

#[test]
fn no_trail_crosses_a_new_combo() {
    // A trail says "this one, then this one" about notes that belong together.
    // A new combo is the map saying they do not.
    let dir = skin_folder("trail-combo");
    write_element(&dir, "followpoint.png", 32, 255);
    assert_eq!(
        trail_ink(Some(&dir), &spaced_pair(true), 4600.0),
        0,
        "a trail was drawn across a combo break"
    );
}

#[test]
fn a_skin_without_the_picture_gets_no_trail() {
    // Our own look has never had them, and giving it a set now would
    // redecorate every render made without a skin.
    let dir = skin_folder("trail-none");
    write_element(&dir, "hitcircle.png", 128, 255);
    assert_eq!(trail_ink(Some(&dir), &spaced_pair(false), 4600.0), 0);
    assert_eq!(trail_ink(None, &spaced_pair(false), 4600.0), 0, "nor ours");
}

#[test]
fn the_trail_is_gone_once_the_note_it_led_to_is_due() {
    // Each mark leaves on its own moment, so by the time the player is at the
    // second note the road there has been taken up behind them.
    let dir = skin_folder("trail-gone");
    write_element(&dir, "followpoint.png", 32, 255);
    let map = spaced_pair(false);
    let before = trail_ink(Some(&dir), &map, 4600.0);
    let after = trail_ink(Some(&dir), &map, 5400.0);
    assert!(after < before, "{after} against {before}");
}

#[test]
fn the_hit_flash_is_off_unless_it_is_asked_for() {
    // osu! makes this a setting rather than a fact about a skin —
    // `config.Get<bool>(OsuSetting.HitLighting)` — and so does this. It was
    // switched on when it was written and turned straight back off: on a dense
    // map each flash lasts a second and a half, so a dozen are up at once and
    // the play is behind them.
    //
    // The skin's picture is read either way. What is checked here is that
    // reading it is not the same as drawing it.
    let dir = skin_folder("flash");
    write_element(&dir, "hitcircle.png", 128, 255);
    write_element(&dir, "lighting.png", 100, 255);

    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let sprites = Sprites::read(&dir, &[Element::HitCircle, Element::Lighting]);
    assert!(
        !sprites.draw_ourselves(Element::Lighting),
        "the skin's flash is read"
    );
    assert!(!Skin::default().hit_lighting, "and not drawn");
}

// ── the key overlay, when the skin brought one ───────────────────────────

/// A map with two notes and a replay that taps both, so the counters have
/// something to count.
fn tapped() -> (Beatmap, dossier_replay::Replay) {
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
CircleSize:4

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,100,3000,5,0
400,300,4000,1,0
",
    );
    let tap = |t: i64, x: f32, y: f32, k: u8| dossier_replay::ReplayFrame {
        time_ms: t,
        x,
        y,
        keys: dossier_replay::Keys(k),
    };
    let replay = replay_over(vec![
        tap(2990, 100.0, 100.0, 0),
        tap(3000, 100.0, 100.0, dossier_replay::Keys::K1),
        tap(3010, 100.0, 100.0, 0),
        tap(3990, 400.0, 300.0, 0),
        tap(4000, 400.0, 300.0, dossier_replay::Keys::K2),
        tap(4010, 400.0, 300.0, 0),
    ]);
    (map, replay)
}

/// Ink in the strip down the right edge, where the counters live.
fn key_column(dir: Option<&std::path::Path>, time_ms: f64) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    if let Some(dir) = dir {
        let wanted = [
            Element::HitCircle,
            Element::InputOverlayKey,
            Element::InputOverlayBackground,
        ];
        skin.sprites = Some(std::sync::Arc::new(
            Sprites::read(dir, &wanted).tint_for(&skin.combo_colours),
        ));
    }
    let state = GameState::new(&map, &replay);
    let frame = Scene::new(&state, skin).frame(time_ms, &Layout::new(640, 480));

    let mut count = 0;
    for y in 180..300u32 {
        for x in 560..640u32 {
            let Some(p) = frame.pixel(x, y) else { continue };
            let off = (i32::from(p.red()) - i32::from(FIELD.0)).abs()
                + (i32::from(p.green()) - i32::from(FIELD.1)).abs()
                + (i32::from(p.blue()) - i32::from(FIELD.2)).abs();
            if off > 12 {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn a_skins_own_key_overlay_replaces_ours() {
    // Ours is a column of rounded cards with a tap trail behind them, which is
    // a good readout and not this one. osu! draws the overlay from two files,
    // and a skin that ships them has said what it wants it to look like.
    let dir = skin_folder("keys");
    write_element(&dir, "inputoverlay-key.png", 46, 255);
    write_element(&dir, "inputoverlay-background.png", 64, 160);

    let ours = key_column(None, 4200.0);
    let theirs = key_column(Some(&dir), 4200.0);
    assert!(ours > 0, "our own counters are drawn when nothing else is");
    assert_ne!(ours, theirs, "the skin's overlay was ignored");
}

#[test]
fn a_skin_with_no_overlay_keeps_our_counters() {
    // The rule everywhere else in here: a skin decides what it ships pictures
    // for and nothing more.
    let dir = skin_folder("keys-none");
    write_element(&dir, "hitcircle.png", 128, 255);
    assert_eq!(key_column(Some(&dir), 4200.0), key_column(None, 4200.0));
}

#[test]
fn a_held_key_is_lit_and_a_loose_one_is_not() {
    // `keySprite.Colour = ActiveColour` on the way down and white on the way
    // up, so the two states are told apart by more than a shrinking box.
    let dir = skin_folder("keys-lit");
    write_element(&dir, "inputoverlay-key.png", 46, 255);

    // 3000ms is the first tap; 3600ms is well clear of both.
    assert_ne!(
        key_column(Some(&dir), 3000.0),
        key_column(Some(&dir), 3600.0),
        "held and loose look the same"
    );
}

#[test]
fn the_skins_overlay_hangs_off_the_edge_of_the_frame() {
    // `Anchor = Anchor.TopRight` with nothing subtracted. Ours is inset
    // because it is a floating column of cards and a card wants air around it;
    // this is a panel, and a panel held off the edge reads as having come
    // loose. Checked in the last column of pixels, which nothing else reaches.
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;

    let dir = skin_folder("keys-edge");
    write_element(&dir, "inputoverlay-key.png", 46, 255);
    write_element(&dir, "inputoverlay-background.png", 64, 200);

    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let wanted = [Element::InputOverlayKey, Element::InputOverlayBackground];
    skin.sprites = Some(std::sync::Arc::new(
        Sprites::read(&dir, &wanted).tint_for(&skin.combo_colours),
    ));
    let state = GameState::new(&map, &replay);
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, skin).frame(4200.0, &layout);

    let lit = (180..300u32)
        .filter(|&y| {
            frame
                .pixel(639, y)
                .is_some_and(|p| i32::from(p.red()) - i32::from(FIELD.0) != 0)
        })
        .count();
    assert!(lit > 0, "the panel does not reach the edge of the frame");
}

// ── how big a judgement is ───────────────────────────────────────────────

/// A missed note on a map of the given circle size, and the skin's mark for it.
fn miss_mark_width(circle_size: &str, dir: &std::path::Path) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::elements::Verdict;
    use dossier_render::imported::Sprites;

    let map = beatmap(&format!(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:{circle_size}

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
256,192,3000,5,0
256,192,9000,5,0
"
    ));
    // Frames, no presses: the first note is missed and the skin's `hit0` is
    // what marks it. The cursor is parked in a corner so it cannot be measured
    // along with the mark.
    let replay = replay_over(
        (0..40)
            .map(|i| dossier_replay::ReplayFrame {
                time_ms: 2000 + i64::from(i) * 100,
                x: 20.0,
                y: 20.0,
                keys: dossier_replay::Keys(0),
            })
            .collect(),
    );

    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let all: Vec<Element> = Verdict::ALL.iter().copied().map(Element::Verdict).collect();
    let sprites = Sprites::read(dir, &all).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));

    let state = GameState::new(&map, &replay);
    let layout = Layout::new(640, 480);
    // Well inside the half-second the mark is held at full size, and clear of
    // the hundred milliseconds it takes to snap down from 1.6.
    let frame = Scene::new(&state, skin).frame(3400.0, &layout);

    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
    (0..640u32)
        .filter(|&x| {
            frame.pixel(x, cy as u32).is_some_and(|p| {
                (i32::from(p.red()) - i32::from(FIELD.0)).abs()
                    + (i32::from(p.green()) - i32::from(FIELD.1)).abs()
                    + (i32::from(p.blue()) - i32::from(FIELD.2)).abs()
                    > 12
            }) && x.abs_diff(cx as u32) < 200
        })
        .count()
}

#[test]
fn a_judgement_below_the_ceiling_keeps_the_size_the_skin_drew() {
    // osu! hangs a judgement in the playfield beside the objects rather than on
    // one, so a mark drawn thirty pixels wide is thirty playfield pixels wide
    // on every map. Ours took the note as its ruler, like everything else a
    // skin brings — right for a piece of a hit object, wrong for this.
    //
    // A ceiling sits over that, and this test stays below it: the skin's own
    // size is what comes out, whatever the circles are doing.
    let dir = skin_folder("verdict-ruler");
    // A CS6 note allows 25 and this is 12, so the ceiling never comes into it.
    write_padded(&dir, "hit0.png", 200, 12);

    let roomy = miss_mark_width("2", &dir);
    let tight = miss_mark_width("6", &dir);
    assert!(roomy > 0, "the skin's mark is drawn at all");
    assert_eq!(
        roomy, tight,
        "the circle size changed the mark: {roomy} against {tight}"
    );

    // And a bigger picture is a bigger mark: below the ceiling the skin is the
    // only ruler.
    let wider = skin_folder("verdict-ruler-wide");
    write_padded(&wider, "hit0.png", 200, 16);
    assert!(
        miss_mark_width("2", &wider) > roomy,
        "a wider picture was not a wider mark"
    );
}

/// Three samples off one frame: each note at its own centre, and the point
/// where the two overlap.
///
/// Self-calibrating on purpose. Written first by assuming which combo colour
/// each note would get, it passed in both drawing orders and tested nothing —
/// the palette rotates, and the assumption was simply wrong. Asking the frame
/// what each note looks like and then asking who owns the overlap cannot be
/// wrong about that.
fn overlap_samples(time_ms: f64, played: bool) -> [(u8, u8, u8); 3] {
    let map = beatmap(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:4

[Colours]
Combo1 : 255,0,0
Combo2 : 0,0,255

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
230,192,3000,5,0
280,192,3150,5,0
",
    );
    let layout = Layout::new(640, 480);
    // With a replay the first note is struck on time and is history by 3100;
    // without one nothing is judged and both notes are still in play.
    let state = if played {
        GameState::new(
            &map,
            &replay_over(vec![
                dossier_replay::ReplayFrame {
                    time_ms: 2990,
                    x: 230.0,
                    y: 192.0,
                    keys: dossier_replay::Keys(0),
                },
                dossier_replay::ReplayFrame {
                    time_ms: 3000,
                    x: 230.0,
                    y: 192.0,
                    keys: dossier_replay::Keys(dossier_replay::Keys::K1),
                },
                dossier_replay::ReplayFrame {
                    time_ms: 3010,
                    x: 230.0,
                    y: 192.0,
                    keys: dossier_replay::Keys(0),
                },
            ]),
        )
    } else {
        GameState::from_beatmap(&map, Mods::default())
    };
    let frame = Scene::new(&state, Skin::default().with_font(font())).frame(time_ms, &layout);
    let at = |x: f64| {
        let (cx, cy) = layout.map(dossier_beatmap::Point { x, y: 192.0 });
        let p = frame.pixel(cx as u32, cy as u32).expect("inside the frame");
        (p.red(), p.green(), p.blue())
    };
    // The far side of each note, clear of the other, and the seam between them.
    [at(215.0), at(295.0), at(255.0)]
}

/// How far apart two colours are, as the sum of their channels' differences.
fn apart(a: (u8, u8, u8), b: (u8, u8, u8)) -> i32 {
    (i32::from(a.0) - i32::from(b.0)).abs()
        + (i32::from(a.1) - i32::from(b.1)).abs()
        + (i32::from(a.2) - i32::from(b.2)).abs()
}

#[test]
fn among_notes_still_in_play_the_soonest_is_on_top() {
    // The game's own order, and the source says what it is for:
    //
    // ```csharp
    // // Put earlier hitobjects towards the end of the list, so they handle input first
    // ```
    //
    // A render takes no input, so that requirement buys nothing here — but the
    // reading it produces is still the right one among notes still to be hit:
    // a viewer's eye is on what happens next, and the soonest note on top is
    // what that looks like.
    let [first, second, seam] = overlap_samples(3100.0, false);
    assert!(
        apart(first, second) > 60,
        "the two notes are tellable apart"
    );
    assert!(
        apart(seam, first) < apart(seam, second),
        "the later note covered one still to be hit: {seam:?} against {first:?}"
    );
}

/// A note at 2000 struck on time, and a slider from 2200 whose body runs over
/// where it was — so at 2100 the note is a tenth of a second into its exit and
/// the slider is being played.
fn exit_over_a_later_body(slider: bool) -> (u8, u8, u8) {
    let mut objects = String::from("256,192,2000,5,0\n");
    if slider {
        objects.push_str("120,192,2200,2,0,L|400:192,1,280\n");
    }
    let map = beatmap(&format!(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:0.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
{objects}"
    ));
    let replay = replay_over(vec![
        dossier_replay::ReplayFrame {
            time_ms: 1000,
            x: 60.0,
            y: 340.0,
            keys: dossier_replay::Keys(0),
        },
        dossier_replay::ReplayFrame {
            time_ms: 1990,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
        dossier_replay::ReplayFrame {
            time_ms: 2000,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        dossier_replay::ReplayFrame {
            time_ms: 2020,
            x: 60.0,
            y: 340.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    let state = GameState::new(&map, &replay);
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, Skin::default().with_font(font())).frame(2100.0, &layout);
    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
    let p = frame.pixel(cx as u32, cy as u32).expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

#[test]
fn a_notes_exit_animation_is_not_dimmed_by_a_later_sliders_body() {
    // Reported once the dimming worked: the swelling, fading circle a struck
    // note leaves behind was being darkened through the body and cut by its
    // border.
    //
    // The game's one rule answers it. A note already struck is almost always
    // *earlier* than the slider being played now, so it is on top — while an
    // approach circle belongs to a note still coming, which is later, so that
    // one passes under the body and is dimmed. Both from the same comparison,
    // where a second rule about "judged" objects broke one to get the other.
    let with_slider = exit_over_a_later_body(true);
    let alone = exit_over_a_later_body(false);
    // Not "unchanged" — "not darker". The circle is part-way through fading, so
    // a body under it shows through and reads *brighter*, which is what
    // compositing in that order looks like. Dimmed would be the other way.
    let sum = |c: (u8, u8, u8)| u32::from(c.0) + u32::from(c.1) + u32::from(c.2);
    assert!(
        sum(with_slider) >= sum(alone),
        "the exit animation was dimmed by a later slider's body: \
         {with_slider:?} against {alone:?}"
    );
}

#[test]
fn the_one_underneath_is_still_drawn() {
    // Order, not omission: the note that lost the overlap is whole everywhere
    // the other one is not.
    for played in [false, true] {
        let [first, _, _] = overlap_samples(3100.0, played);
        assert!(
            u32::from(first.0) + u32::from(first.1) + u32::from(first.2) > 40,
            "the earlier note vanished rather than going underneath: {first:?}"
        );
    }
}

/// One pixel where a slider's body and a note sit on the same spot, sampled
/// from three renders: both objects, the note alone, and the body alone.
///
/// Three because the body is not opaque — whichever is underneath tints
/// whatever is over it, so "is the pixel the body's colour" has no yes or no.
/// What can be answered is which of the two it is *nearer*, and that needs both
/// of them measured rather than assumed.
fn stacked(time_ms: f64, note: bool, slider: bool) -> (u8, u8, u8) {
    let mut objects = String::new();
    if slider {
        objects.push_str("120,192,2000,2,0,L|400:192,1,280\n");
    }
    if note {
        objects.push_str("256,192,2500,5,0\n");
    }
    let map = beatmap(&format!(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:0.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
{objects}"
    ));
    let replay = replay_over(vec![
        // Parked out of the way before the click as well as after it: the
        // cursor sits at the replay's first position until the replay starts.
        dossier_replay::ReplayFrame {
            time_ms: 1000,
            x: 60.0,
            y: 340.0,
            keys: dossier_replay::Keys(0),
        },
        dossier_replay::ReplayFrame {
            time_ms: 2490,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(0),
        },
        dossier_replay::ReplayFrame {
            time_ms: 2500,
            x: 256.0,
            y: 192.0,
            keys: dossier_replay::Keys(dossier_replay::Keys::K1),
        },
        // …and away. The cursor is drawn wherever the replay last left it, on
        // top of everything — parked on the note it becomes the thing being
        // measured, which is a trap this file has fallen into twice.
        dossier_replay::ReplayFrame {
            time_ms: 2520,
            x: 60.0,
            y: 340.0,
            keys: dossier_replay::Keys(0),
        },
    ]);
    let state = GameState::new(&map, &replay);
    let layout = Layout::new(640, 480);
    // With every judgement blanked. They are drawn over the objects they belong
    // to — the slider's head leaves one at this very point — and they are large
    // enough to be what this probe measures instead of the note. The question
    // here is whether a body dims what it passes over, and a mark sitting on
    // top of both answers a different one.
    let quiet = skin_folder("dim-probe");
    for name in ["hit300", "hit100", "hit50", "hit0"] {
        // Transparent rather than empty: a zero-byte file reads as "no such
        // element", which falls back to our own lettering — the very thing
        // being kept out of the frame.
        write_element(&quiet, &format!("{name}.png"), 8, 0);
    }
    let mut skin = Skin::default().with_font(font());
    let wanted: Vec<dossier_render::elements::Element> = [
        dossier_render::elements::Verdict::Three,
        dossier_render::elements::Verdict::Hundred,
        dossier_render::elements::Verdict::Fifty,
        dossier_render::elements::Verdict::Miss,
    ]
    .into_iter()
    .map(dossier_render::elements::Element::Verdict)
    .collect();
    skin.sprites = Some(std::sync::Arc::new(
        dossier_render::imported::Sprites::read(&quiet, &wanted),
    ));
    let frame = Scene::new(&state, skin).frame(time_ms, &layout);
    // On the note's own rim rather than at its centre. The centre is the
    // brightest part of the body's track and the note's fill is faint against
    // it — measured there, "is the note still visible" has no signal at all,
    // which is how this test came to pass on a judgement mark drawn over the
    // same point. The rim is the note's brightest part and the body's darkest.
    let (cx, cy) = layout.map(dossier_beatmap::Point {
        x: 256.0 + 28.0,
        y: 192.0,
    });
    let p = frame.pixel(cx as u32, cy as u32).expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

#[test]
fn a_note_the_body_passes_over_is_dimmed_rather_than_hidden() {
    // The report, twice, against a screenshot of the client: things under the
    // current body are darkened there and painted out here.
    //
    // The cause was a layer of our own invention — every body beneath every
    // note — which meant a body could never be over anything and so could never
    // dim it. The game keeps a slider's body inside the slider and lets the
    // ordering decide, and a track is drawn at seven tenths opacity, so what it
    // passes over shows through.
    //
    // Here the slider starts at 2000 and the note at 2500, so the slider is the
    // earlier object and its body is above. The note must still be *there*.
    // Twenty milliseconds after the click, not a hundred: the note fades over
    // 240 and swells as it goes, so by 2600 its centre is faint enough that
    // what it leaves under the body is a handful of levels. This test used to
    // read at 2600 and pass on the strength of the *miss mark* drawn over the
    // same point — blank the judgements, as the probe now does, and it fails.
    let both = stacked(2520.0, true, true);
    let body_alone = stacked(2520.0, false, true);
    let note_alone = stacked(2520.0, true, false);
    println!("СКВОЗЬ: под телом {both:?}, тело {body_alone:?}, нота {note_alone:?}");
    assert!(
        apart(both, body_alone) > 20,
        "the note under the body left no trace at all: {both:?} against {body_alone:?}"
    );
    // …and dimmed rather than whole: nearer the body than the bare note.
    assert!(
        apart(both, body_alone) < apart(both, note_alone),
        "the note under the body was not dimmed by it: {both:?}, \
         body {body_alone:?}, note {note_alone:?}"
    );
}

/// The same two objects the other way round in time: a note at 2000 and a
/// slider starting at 2200 whose body runs over it.
fn note_before_slider(time_ms: f64, slider: bool) -> (u8, u8, u8) {
    let mut objects = String::from("256,192,2000,5,0\n");
    if slider {
        objects.push_str("120,192,2200,2,0,L|400:192,1,280\n");
    }
    let map = beatmap(&format!(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
OverallDifficulty:5
SliderMultiplier:0.4
SliderTickRate:1

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
{objects}"
    ));
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, Skin::default().with_font(font())).frame(time_ms, &layout);
    let (cx, cy) = layout.map(dossier_beatmap::Point { x: 256.0, y: 192.0 });
    let p = frame.pixel(cx as u32, cy as u32).expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

#[test]
fn a_note_still_to_be_hit_is_above_the_body_of_a_later_slider() {
    // What the invented layer was for, and what the game gets from ordering
    // alone: the earliest object is on top, so a slider beginning a moment
    // after a note cannot cover the thing about to be hit.
    let with_body = note_before_slider(1990.0, true);
    let alone = note_before_slider(1990.0, false);
    assert!(
        apart(with_body, alone) < 30,
        "a later slider's body covered a note still to be hit: {with_body:?} against {alone:?}"
    );
}

#[test]
fn each_mark_plays_the_animation_from_its_own_beginning() {
    // `GetAnimation("followpoint", true, false)` — the `false` is
    // `startAtCurrentTime`, so a mark's strip runs from when *it* appeared.
    //
    // Off map time instead, every mark on screen shows the same frame, so a
    // strip whose frames fade in and out blinks the whole trail together — and
    // on a frame the skin drew empty the trail disappears outright. Measured on
    // a real 61-frame skin: every follow point missing at three moments out of
    // three, which is what "the follow points do not work" turned out to be.
    //
    // Ten frames, the first of them blank. All frames play in a second by
    // default, so on map time frame zero comes round on every whole second —
    // and 4000ms is one. A mark alive then is part-way through its own strip
    // and has to be drawn.
    let dir = skin_folder("trail-frames");
    write_element(&dir, "followpoint-0.png", 32, 0);
    for frame in 1..10 {
        write_element(&dir, &format!("followpoint-{frame}.png"), 32, 255);
    }

    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
100,192,3000,5,0
400,192,5000,1,0
",
    );
    // Measured against the same skin with the strip blanked, so what is counted
    // is the trail and nothing else. Written first as a plain ink count, it
    // passed on either clock: the probe was seeing the second note's approach
    // circle, which at that moment is three and a half times its own size and
    // reaches right across the gap.
    let silent = skin_folder("trail-frames-off");
    for frame in 0..10 {
        write_element(&silent, &format!("followpoint-{frame}.png"), 32, 0);
    }
    assert!(
        trail_ink(Some(&dir), &map, 4000.0) > trail_ink(Some(&silent), &map, 4000.0),
        "the whole trail vanished on a frame the skin drew empty"
    );
}

// ── the slider body's own shading ────────────────────────────────────────

/// The colour across a slider body, from its outer edge to its centreline.
fn across_body(at: f32) -> (u8, u8, u8) {
    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
SliderMultiplier:1.4
SliderTickRate:1

[Colours]
Combo1 : 0,120,255

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
150,120,2000,2,0,L|400:120,1,250
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(1280, 720);
    let frame =
        Scene::new(&state, Skin::with_combo_colours(map.combo_colours())).frame(2000.0, &layout);
    // Straight down through the middle of the body, well clear of either end.
    let radius = state.difficulty().circle_radius();
    let (cx, cy) = layout.map(dossier_beatmap::Point { x: 280.0, y: 120.0 });
    let half = layout.length(radius);
    let y = cy - half * (1.0 - at);
    let p = frame
        .pixel(cx as u32, y.round() as u32)
        .expect("inside the frame");
    (p.red(), p.green(), p.blue())
}

#[test]
fn the_border_is_a_band_of_one_colour_rather_than_a_fade() {
    // ```csharp
    // if (position <= border_portion)
    //     return BorderColour;
    // ```
    //
    // Solid, with no crossfade at either edge — the hard boundary is the point
    // of it. Ours faded into its neighbours over a hundredth of the radius,
    // which is exactly the crisp line a side-by-side against the client showed
    // missing.
    let inner = across_body(0.10);
    let outer = across_body(0.17);
    assert!(
        apart(inner, outer) < 12,
        "the border is not one colour across its width: {inner:?} against {outer:?}"
    );
}

/// What a slider body actually composites at, solved rather than eyeballed.
///
/// The same body drawn on black and on white: `result = a·C + (1 - a)·bg`, so
/// the difference between the two is `(1 - a)` times the difference between the
/// backgrounds, and the alpha falls straight out. Nothing else in the frame can
/// confuse it — no glow, no combo colour, no skin.
fn body_alpha_across() -> [f32; 3] {
    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5
SliderMultiplier:0.4
SliderTickRate:1

[Colours]
Combo1 : 255,0,0

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
120,192,2000,2,0,L|400:192,1,280
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let sample = |bg: u8| {
        let mut skin = Skin::with_combo_colours(map.combo_colours());
        skin.background = tiny_skia::Color::from_rgba8(bg, bg, bg, 255);
        let frame = Scene::new(&state, skin).frame(2300.0, &layout);
        let (cx, cy) = layout.map(dossier_beatmap::Point { x: 300.0, y: 192.0 });
        // Across the body: in the shadow, in the border, and at the centreline.
        [0.05f32, 0.12, 0.9].map(|at| {
            let half = layout.length(state.difficulty().circle_radius());
            let y = cy - half * (1.0 - at);
            let p = frame.pixel(cx as u32, y.round() as u32).expect("in frame");
            f32::from(p.red())
        })
    };
    let dark = sample(0);
    let light = sample(255);
    [0, 1, 2].map(|i| 1.0 - (light[i] - dark[i]) / 255.0)
}

#[test]
fn a_slider_body_dims_what_it_passes_over_rather_than_covering_it() {
    // Reported three times, and right all three: the body covered notes, slider
    // heads and other sliders instead of darkening them.
    //
    // The cause was the blend, not the colours. The tube is built from nested
    // bands, drawn narrowest first with `DestinationOver` — "paint behind what
    // is there" — which is `dst + src·(1 - dst.a)` and not "only where nothing
    // is". Every wider band still added three tenths of itself on top, so three
    // or four bands deep the tube reached full opacity. Measured: the track
    // composited at 1.00 where the game puts it at 0.70.
    //
    // Widest band first now, each one replacing what it covers, so every pixel
    // keeps the alpha of the narrowest band over it — which is what the shading
    // function already said it should be.
    let [shadow, border, track] = body_alpha_across();
    assert!(
        (track - 0.70).abs() < 0.03,
        "the track is not seven tenths opaque: {track}"
    );
    assert!(border > 0.97, "the border is solid: {border}");
    assert!(
        shadow < 0.30,
        "and the shadow is a hint rather than a wall: {shadow}"
    );
}

#[test]
fn the_ring_closing_in_is_never_dimmed_by_a_track() {
    // The game's own top layer, filled by proxy and clear of the whole field:
    //
    // ```csharp
    // borderContainer, Smoke, spinnerProxies, FollowPoints, judgementLayer,
    // HitObjectContainer, judgementAboveHitObjectLayer, approachCircles
    // ...
    // approachCircles.Add(hitCircle.ProxiedLayer.CreateProxy());
    // ```
    //
    // Drawn in its object's own place instead, a ring belonging to a note later
    // than the slider being played passes under that slider's track and is
    // darkened by it.
    //
    // The note here is at 2600 and the slider runs from 2000, so at 2300 the
    // ring is closing in over a body already on the field.
    //
    // One combo colour, pinned: the first go compared two renders that differed
    // by adding the slider, and adding an object shifts the palette — it was
    // measuring a green ring against a yellow one and failing for that.
    let sample = |slider: bool| {
        let mut objects = String::from("256,192,2600,5,0\n");
        if slider {
            objects.push_str("120,192,2000,2,0,L|400:192,1,280\n");
        }
        let map = beatmap(&format!(
            "
[Difficulty]
CircleSize:4
ApproachRate:5
SliderMultiplier:0.4
SliderTickRate:1

[Colours]
Combo1 : 255,255,255

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
{objects}"
        ));
        let state = GameState::from_beatmap(&map, Mods::default());
        let layout = Layout::new(640, 480);
        let skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
        let frame = Scene::new(&state, skin).frame(2300.0, &layout);
        // Where the ring is at 2300: the note's own row, out along the radius.
        let radius = state.difficulty().circle_radius();
        let progress = 1.0 - (2600.0 - 2300.0) / state.difficulty().preempt_ms();
        let scale = 1.0 + 3.0 * (1.0 - progress.clamp(0.0, 1.0));
        let (cx, cy) = layout.map(dossier_beatmap::Point { x: 256.0, y: 192.0 });
        let out = layout.length(radius * scale);
        // The ring is a thin stroke, so walk a few pixels either side of where
        // it should be and take the brightest — its own colour, whatever the
        // rounding.
        let mut best = (0u8, 0u8, 0u8);
        for step in -4i32..=4 {
            let y = cy - out + step as f32;
            if let Some(p) = frame.pixel(cx as u32, y.round().max(0.0) as u32) {
                if u32::from(p.red()) + u32::from(p.green()) + u32::from(p.blue())
                    > u32::from(best.0) + u32::from(best.1) + u32::from(best.2)
                {
                    best = (p.red(), p.green(), p.blue());
                }
            }
        }
        best
    };
    let over_body = sample(true);
    let alone = sample(false);
    assert!(
        u32::from(alone.0) + u32::from(alone.1) + u32::from(alone.2) > 60,
        "the ring is drawn at all: {alone:?}"
    );
    assert!(
        apart(over_body, alone) < 20,
        "the track darkened the ring closing in: {over_body:?} against {alone:?}"
    );
}

// ── the pieces of a slider a skin also draws ─────────────────────────────

#[test]
fn a_skins_own_slider_tick_is_drawn_in_place_of_ours() {
    // `sliderscorepoint` is osu!'s name for the dot a slider passes over, and a
    // skin that redrew every other part of a slider and had this one borrowed
    // back from us looked like two sliders laid over each other.
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;

    let map = beatmap(LONE_SLIDER);
    let state = GameState::from_beatmap(&map, Mods::default());
    let object = &state.timeline().objects[0];
    let far_tick = *object.tick_times().last().expect("this slider has ticks");
    let at = object.ball_at(far_tick).expect("on the path");

    let lit = |skin: Skin| {
        let background = skin.background;
        let layout = Layout::new(640, 480);
        let scene = Scene::new(&state, skin);
        let (x, y) = layout.map(at);
        let bg = background.to_color_u8();
        // Well outside the body, which is bright enough at the tick's own
        // position to swallow anything drawn on it. The skin's art below is
        // four times the note across, so it reaches here and nothing else does.
        let frame = scene.frame(far_tick - 20.0, &layout);
        let p = frame
            .pixel(x as u32 + 52, y as u32)
            .expect("inside the frame");
        i32::from(p.red()) - i32::from(bg.red()) + i32::from(p.green()) - i32::from(bg.green())
            + i32::from(p.blue())
            - i32::from(bg.blue())
    };

    let ours = lit(Skin::default());

    let dir = skin_folder("tick");
    write_element(&dir, "sliderscorepoint.png", 512, 255);
    let mut dressed = Skin::with_combo_colours(map.combo_colours());
    let sprites =
        Sprites::read(&dir, &[Element::SliderScorePoint]).tint_for(&dressed.combo_colours);
    dressed.sprites = Some(std::sync::Arc::new(sprites));
    let theirs = lit(dressed);

    assert!(
        theirs > ours + 30,
        "the skin's own tick was not drawn: {theirs} against {ours}"
    );
}

/// The mark's width for a note that was *hit*, so the 300 is what is measured.
///
/// The same shape as `miss_mark_width` and deliberately beside it: the two
/// differ only in whether the replay presses, which is the whole of what
/// separates a 300 from a miss.
fn scored_mark_width(circle_size: &str, dir: &std::path::Path) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::elements::Verdict;
    use dossier_render::imported::Sprites;

    let map = beatmap(&format!(
        "
[Difficulty]
ApproachRate:5
OverallDifficulty:5
CircleSize:{circle_size}

[TimingPoints]
0,500,4,2,0,60,1,0

[HitObjects]
256,192,3000,5,0
256,192,9000,5,0
"
    ));
    // On the note and pressing at its moment, so it is judged a 300 — and away
    // again directly after, because the probe below runs through the centre and
    // a cursor parked there would be measured along with the mark.
    let mut frames = Vec::new();
    for i in 0..40 {
        let at = 2000 + i64::from(i) * 100;
        let on_note = at <= 3000;
        frames.push(dossier_replay::ReplayFrame {
            time_ms: at,
            x: if on_note { 256.0 } else { 20.0 },
            y: if on_note { 192.0 } else { 20.0 },
            keys: dossier_replay::Keys(if at == 3000 {
                dossier_replay::Keys::K1
            } else {
                0
            }),
        });
    }
    let replay = replay_over(frames);

    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let all: Vec<Element> = Verdict::ALL.iter().copied().map(Element::Verdict).collect();
    let sprites = Sprites::read(dir, &all).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));

    let state = GameState::new(&map, &replay);
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, skin).frame(3400.0, &layout);

    let (cx, cy) = layout.map(dossier_beatmap::Point::CENTRE);
    (0..640u32)
        .filter(|&x| {
            frame.pixel(x, cy as u32).is_some_and(|p| {
                (i32::from(p.red()) - i32::from(FIELD.0)).abs()
                    + (i32::from(p.green()) - i32::from(FIELD.1)).abs()
                    + (i32::from(p.blue()) - i32::from(FIELD.2)).abs()
                    > 12
            }) && x.abs_diff(cx as u32) < 200
        })
        .count()
}

/// A square of ink centred in a larger transparent canvas, which is how skins
/// actually ship a judgement.
fn write_padded(dir: &std::path::Path, name: &str, canvas: u32, ink: u32) {
    write_ink(dir, name, canvas, ink, ink);
}

/// The same, with the two sides given apart — a real judgement is a line of
/// lettering and so wider than it is tall, and how much wider is exactly what
/// the width ceiling is about.
fn write_ink(dir: &std::path::Path, name: &str, canvas: u32, wide: u32, tall: u32) {
    let mut art = tiny_skia::Pixmap::new(canvas, canvas).expect("a canvas");
    let (from_x, from_y) = ((canvas - wide) / 2, (canvas - tall) / 2);
    for y in from_y..from_y + tall {
        for x in from_x..from_x + wide {
            art.pixels_mut()[(y * canvas + x) as usize] =
                tiny_skia::PremultipliedColorU8::from_rgba(255, 255, 255, 255).expect("white");
        }
    }
    std::fs::write(dir.join(name), art.encode_png().expect("png")).expect("written");
}

#[test]
fn squat_lettering_is_held_by_its_width_too() {
    // The height ceiling is not a bound on how big a mark looks, because it
    // only bites on skins that draw tall lettering. Two skins measured side by
    // side both ship a `hit100` sixty-two pixels of ink wide; one draws it
    // fifty-one tall and is taken down to a half of the note, the other draws
    // it twenty-nine — already inside the ceiling — and is drawn untouched at
    // four fifths of a note. Same picture width, same rule, 1.7× apart.
    //
    // So the width is held as well, and the two skins land together. Here the
    // same 120 pixels of ink is drawn once short and once tall.
    let squat = skin_folder("verdict-squat");
    write_ink(&squat, "hit300.png", 200, 120, 28);

    let upright = skin_folder("verdict-upright");
    write_ink(&upright, "hit300.png", 200, 120, 80);

    let held = scored_mark_width("6", &squat);
    let tall = scored_mark_width("6", &upright);
    assert!(held > 0, "the mark is drawn at all");
    assert!(
        held.abs_diff(tall) <= 2,
        "the same picture came out two sizes: {held} squat against {tall} upright"
    );
}

#[test]
fn the_widest_mark_brings_its_siblings_down_with_it() {
    // The width is held over the skin's whole set by one factor, and not mark
    // by mark, which was the obvious thing and is the bug the height ceiling
    // exists to prevent: all four are lettering at one cap height, so squeezing
    // each to a common width would make the number with the most characters the
    // shortest and a 50 would come out taller than a 100 again.
    //
    // One factor over the set cannot reorder it. What it does instead is this:
    // a compact mark shrinks because a wide sibling had to, and keeps its place
    // behind it.
    let together = skin_folder("verdict-set");
    write_ink(&together, "hit300.png", 200, 120, 28);
    write_ink(&together, "hit0.png", 200, 40, 28);

    // The same compact mark with no wide sibling to be held by.
    let alone = skin_folder("verdict-set-alone");
    write_ink(&alone, "hit0.png", 200, 40, 28);

    let long = scored_mark_width("6", &together);
    let short = miss_mark_width("6", &together);
    assert!(short > 0, "the compact mark is drawn at all");
    assert!(
        long > short,
        "the set was squeezed to a common width: {long} against {short}"
    );
    assert!(
        short < miss_mark_width("6", &alone),
        "a mark whose sibling was held did not come down with it"
    );
}

#[test]
fn a_mark_past_the_ceiling_is_brought_down_to_it() {
    // A deliberate departure, asked for. At the size the game draws them a 300
    // on the skin this was settled on is two thirds of a note, and a screen of
    // them over a play reads as clutter — the game has a player watching the
    // notes, a render has somebody watching the play.
    //
    // Measured on the ink's *height*, which is what the two attempts before
    // this got wrong: the first capped the canvas, and a judgement is a small
    // figure in a large transparent square; the second capped the width, and
    // all four are lettering drawn to one cap height, so holding the width made
    // the mark with the most characters the smallest.
    //
    // Downwards only. Bringing a small mark up to the height was tried and is
    // worse than the problem it solves: there is nothing to enlarge a skin's
    // picture with, and one drawn fifteen pixels tall becomes a smear at thirty.
    let dir = skin_folder("verdict-share");
    write_padded(&dir, "hit300.png", 200, 40);
    write_padded(&dir, "hit0.png", 200, 40);

    let big = skin_folder("verdict-share-big");
    write_padded(&big, "hit300.png", 200, 80);
    write_padded(&big, "hit0.png", 200, 80);

    for measure in [
        scored_mark_width as fn(&str, &std::path::Path) -> usize,
        miss_mark_width as fn(&str, &std::path::Path) -> usize,
    ] {
        let modest = measure("6", &dir);
        let huge = measure("6", &big);
        assert!(modest > 0, "the mark is drawn at all");
        assert!(
            huge <= modest + 2,
            "a mark twice as wide was not brought down: {huge} against {modest}"
        );
    }

    // And one already inside the ceiling is left alone, at its own size.
    let small = skin_folder("verdict-share-modest");
    write_padded(&small, "hit300.png", 200, 8);
    write_padded(&small, "hit0.png", 200, 8);
    assert!(
        scored_mark_width("6", &small) < scored_mark_width("6", &dir),
        "a mark already inside the ceiling was resized anyway"
    );
}

#[test]
fn a_disjoint_trail_reaches_back_the_time_the_game_gives_it() {
    // A skin with a cursor and no `cursormiddle` gets osu!'s dotted trail: one
    // mark every sixtieth of a second wherever the cursor is, each gone in
    // 150ms. What this used to draw reached 110ms back, shrank each mark as it
    // aged and held none above a third opacity — a smear where the game draws a
    // trail.
    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5

[HitObjects]
256,192,1500,1,0
256,192,3000,1,0
",
    );
    // Straight across the field at a steady speed, so distance back along the
    // row *is* time back. Two notes, and the frame is read between them, so the
    // play is under way and the cursor is on screen.
    let speed = 0.6_f32; // osu!px per millisecond
    let frames: Vec<_> = (0..200)
        .map(|i| dossier_replay::ReplayFrame {
            time_ms: 1000 + i64::from(i) * 5,
            x: 20.0 + speed * (i as f32) * 5.0,
            // Clear of the notes' own row, or the scan below would find a
            // hit circle and call it trail.
            y: 60.0,
            keys: dossier_replay::Keys(0),
        })
        .collect();
    let state = GameState::new(&map, &replay_over(frames));

    let skin = Skin::default();
    let background = skin.background;
    let layout = Layout::new(640, 480);
    let scene = Scene::new(&state, skin);
    let at = 1800.0;
    let frame = scene.frame(at, &layout);

    let here = state
        .cursor_track()
        .sample(at)
        .expect("the cursor is on the field")
        .pos;
    let (cx, cy) = layout.map(here);
    let bg = background.to_color_u8();
    // The furthest lit pixel behind the cursor along its own row.
    let reach = (0..cx as u32)
        .filter(|&x| {
            frame.pixel(x, cy as u32).is_some_and(|p| {
                (i32::from(p.red()) - i32::from(bg.red())).abs()
                    + (i32::from(p.green()) - i32::from(bg.green())).abs()
                    + (i32::from(p.blue()) - i32::from(bg.blue())).abs()
                    > 10
            })
        })
        .min()
        .map(|x| cx - x as f32);
    let reach = reach.expect("the trail is drawn at all");

    // 150ms at this speed is 90 osu!px behind, and the cursor itself is nine
    // across — so the trail has to reach most of the way there and not stop at
    // the 110ms the old one did.
    let back = |ms: f64| layout.length(f64::from(speed) * ms) as f64;
    assert!(
        reach as f64 > back(120.0),
        "the trail stops short: {reach} against {} for 120ms",
        back(120.0)
    );
    assert!(
        (reach as f64) < back(200.0),
        "the trail runs past its life: {reach} against {} for 200ms",
        back(200.0)
    );
}

#[test]
fn a_long_break_ends_on_the_skins_own_section_banner() {
    // danser's schedule, which is stable's: nothing on a break under 2880ms,
    // and on a longer one a banner at `min(end - 2880, end - length/2)` that
    // blinks twice and holds for a second. Which of the two appears is decided
    // on health alone, at half.
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;

    let map = beatmap(BREAK_MAP);
    let state = GameState::from_beatmap(&map, Mods::default());
    let (from, to) = state.timeline().breaks[0];
    assert!(to - from > 2880.0, "the fixture's break is long enough");
    let at = (to - 2880.0).min(to - (to - from) / 2.0);

    let dir = skin_folder("section");
    write_element(&dir, "section-pass.png", 400, 255);
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let sprites = Sprites::read(&dir, &[Element::SectionPass]).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    let background = skin.background;
    let layout = Layout::new(640, 480);
    let scene = Scene::new(&state, skin);

    let bg = background.to_color_u8();
    let lit = |t: f64| {
        let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
        scene
            .frame(t, &layout)
            .pixel(x as u32, y as u32)
            .map_or(0, |p| {
                (i32::from(p.red()) - i32::from(bg.red())).abs()
                    + (i32::from(p.green()) - i32::from(bg.green())).abs()
                    + (i32::from(p.blue()) - i32::from(bg.blue())).abs()
            })
    };

    // Before its moment there is nothing; on the hold there is.
    assert!(lit(at - 200.0) < 20, "the banner was up before its moment");
    assert!(lit(at + 400.0) > 60, "the banner never appeared");
    // The gap between the first two blinks is dark.
    assert!(lit(at + 130.0) < 20, "the blink does not blink");
    // And it is gone once the fade has run.
    assert!(lit(at + 1600.0) < 20, "the banner outstayed its fade");
}

#[test]
fn a_short_break_gets_no_banner_at_all() {
    // `if overlay.currentBreak.Length() < 2880 { return }` — there is no room
    // to say it and be read.
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;

    let map = beatmap(
        "
[Difficulty]
CircleSize:5
ApproachRate:5

[Events]
2,3000,5000

[HitObjects]
100,100,2000,1,0
400,300,9000,1,0
",
    );
    let state = GameState::from_beatmap(&map, Mods::default());
    let (from, to) = state.timeline().breaks[0];
    assert!(to - from < 2880.0, "the fixture's break is short enough");

    let dir = skin_folder("section-short");
    write_element(&dir, "section-pass.png", 400, 255);
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let sprites = Sprites::read(&dir, &[Element::SectionPass]).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    let background = skin.background;
    let layout = Layout::new(640, 480);
    let scene = Scene::new(&state, skin);

    let bg = background.to_color_u8();
    let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
    for step in 0..40 {
        let t = from + (to - from) * f64::from(step) / 40.0;
        let lit = scene
            .frame(t, &layout)
            .pixel(x as u32, y as u32)
            .map_or(0, |p| {
                (i32::from(p.red()) - i32::from(bg.red())).abs()
                    + (i32::from(p.green()) - i32::from(bg.green())).abs()
                    + (i32::from(p.blue()) - i32::from(bg.blue())).abs()
            });
        assert!(lit < 20, "a short break drew a banner at {t}");
    }
}

#[test]
fn a_trail_mark_is_the_size_the_skin_drew_it() {
    // `STABLE_MAGIC_SCALE_FACTOR` was chased through here three times — as a
    // multiplier, then as a divisor on the trail alone, then as a divisor on
    // the trail and the cursor together. Three readings, three reports: a lamp,
    // a trail too thin beside its cursor, and a cursor visibly smaller than the
    // game draws it.
    //
    // What all three agree on is that the pair must share a ruler, so they
    // share the plainest one — the size the skin's own file states, read in the
    // 768-tall space the interface is stated in, the same ruler the score
    // digits use. The factor is not applied at all.
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;

    let map = beatmap(
        "
[Difficulty]
CircleSize:4
ApproachRate:5

[HitObjects]
256,192,1500,1,0
256,192,4000,1,0
",
    );
    // Straight and fast, along a row well clear of the notes, so the oldest
    // mark stands alone and can be measured.
    let frames: Vec<_> = (0..200)
        .map(|i| dossier_replay::ReplayFrame {
            time_ms: 1000 + i64::from(i) * 5,
            x: 20.0 + 0.6 * (i as f32) * 5.0,
            y: 60.0,
            keys: dossier_replay::Keys(0),
        })
        .collect();
    let state = GameState::new(&map, &replay_over(frames));

    let dir = skin_folder("trail-size");
    write_element(&dir, "cursortrail.png", 64, 255);
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let sprites = Sprites::read(&dir, &[Element::CursorTrail]).tint_for(&skin.combo_colours);
    skin.sprites = Some(std::sync::Arc::new(sprites));
    let background = skin.background;
    let layout = Layout::new(640, 480);
    let scene = Scene::new(&state, skin);

    let at = 1800.0;
    let track = state.cursor_track();
    // The oldest mark still alive: 150ms back, and far enough from the cursor
    // at this speed that nothing else reaches it.
    let oldest = track.sample(at - 140.0).expect("on the field").pos;
    let (ox, oy) = layout.map(oldest);
    let bg = background.to_color_u8();
    let frame = scene.frame(at, &layout);
    // Measured up the column rather than along the row: the marks are strung
    // out along the row and overlap each other there, so a horizontal run is
    // several of them. Vertically only the one centred here is present.
    let lit = (0..480u32)
        .filter(|&y| {
            y.abs_diff(oy as u32) < 60
                && frame.pixel(ox as u32, y).is_some_and(|p| {
                    (i32::from(p.red()) - i32::from(bg.red())).abs()
                        + (i32::from(p.green()) - i32::from(bg.green())).abs()
                        + (i32::from(p.blue()) - i32::from(bg.blue())).abs()
                        > 6
                })
        })
        .count();

    // 64 of the skin's pixels in a 768-tall interface shown 480 tall: 40.
    let expected = 64.0 * 480.0 / 768.0;
    assert!(
        (lit as f32 - expected).abs() < expected * 0.35,
        "the mark is {lit} tall where {expected:.0} was stated"
    );
}

// ── the two faces of the interface, and the corner they meet in ──────────
//
// osu! skins the score and the combo counter apart: `ScorePrefix`/`ScoreOverlap`
// against `ComboPrefix`/`ComboOverlap`. Both default to `score`, so on most
// skins the two are the same pictures under two names and the split shows
// nothing — and on a skin that names them apart it is the difference between
// the counter its author drew and a different one of theirs.
//
// Reported on two skins at once. `azerino` ships `score-*` and `combo-*` and
// names both, and its counter was coming out in the score face. `vv_idke_trail`
// names `num\berlin` for both — and that face has no `x`, which took the whole
// line back into our own typeface beside a score in the skin's.

/// One glyph in a colour of its own, so a line can be told apart from another
/// line by what it is drawn in.
fn write_glyph(dir: &std::path::Path, name: &str, size: u32, colour: (u8, u8, u8)) {
    let mut pixmap = tiny_skia::Pixmap::new(size, size).expect("a canvas");
    for pixel in pixmap.pixels_mut() {
        *pixel = tiny_skia::PremultipliedColorU8::from_rgba(colour.0, colour.1, colour.2, 255)
            .expect("a colour");
    }
    std::fs::write(dir.join(name), pixmap.encode_png().expect("png")).expect("written");
}

/// How many pixels of the bottom-left corner — where the combo counter sits —
/// are within `slack` of `colour`.
fn combo_corner(dir: &std::path::Path, colour: (u8, u8, u8), slack: i32) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let wanted: Vec<Element> = ('0'..='9')
        .chain([',', '.', '%', 'x'])
        .flat_map(|c| [Element::Score(c), Element::Combo(c)])
        .collect();
    skin.sprites = Some(std::sync::Arc::new(
        Sprites::read(dir, &wanted).tint_for(&skin.combo_colours),
    ));
    let state = GameState::new(&map, &replay);
    let frame = Scene::new(&state, skin).frame(4200.0, &Layout::new(640, 480));

    let mut count = 0;
    for y in 380..480u32 {
        for x in 0..240u32 {
            let Some(p) = frame.pixel(x, y) else { continue };
            let off = (i32::from(p.red()) - i32::from(colour.0)).abs()
                + (i32::from(p.green()) - i32::from(colour.1)).abs()
                + (i32::from(p.blue()) - i32::from(colour.2)).abs();
            if off <= slack {
                count += 1;
            }
        }
    }
    count
}

const COMBO_FACE: (u8, u8, u8) = (255, 0, 0);
const SCORE_FACE: (u8, u8, u8) = (0, 0, 255);

#[test]
fn the_counter_is_drawn_in_the_face_the_skin_named_for_it() {
    let dir = skin_folder("two-faces");
    for digit in 0..10 {
        write_glyph(&dir, &format!("score-{digit}.png"), 24, SCORE_FACE);
        write_glyph(&dir, &format!("combo-{digit}.png"), 24, COMBO_FACE);
    }
    write_glyph(&dir, "score-x.png", 24, SCORE_FACE);
    write_glyph(&dir, "combo-x.png", 24, COMBO_FACE);
    std::fs::write(
        dir.join("skin.ini"),
        "[Fonts]\nScorePrefix: score\nComboPrefix: combo\n",
    )
    .expect("written");

    let theirs = combo_corner(&dir, COMBO_FACE, 40);
    let scores = combo_corner(&dir, SCORE_FACE, 40);
    assert!(
        theirs > 100,
        "the combo face is barely there: {theirs} pixels"
    );
    assert_eq!(scores, 0, "the score face turned up in the combo's corner");
}

#[test]
fn a_glyph_the_face_has_not_got_does_not_take_the_line_with_it() {
    // ```csharp
    // var texture = skin.GetTexture($"{fontName}-{lookup}");
    // TexturedCharacterGlyph? glyph = null;
    // if (texture != null) { ... }
    // ```
    //
    // Every glyph is looked up on its own and the ones that are not there are
    // simply not drawn. A face with figures and no `x` draws `146`, not `146x`
    // in somebody else's lettering.
    let dir = skin_folder("no-x");
    for digit in 0..10 {
        write_glyph(&dir, &format!("score-{digit}.png"), 24, COMBO_FACE);
    }
    std::fs::write(dir.join("skin.ini"), "[General]\nVersion: 2.5\n").expect("written");

    assert!(
        combo_corner(&dir, COMBO_FACE, 40) > 100,
        "the line fell back to our typeface over one missing sign"
    );
}

#[test]
fn a_face_the_skin_has_none_of_is_still_ours_to_draw() {
    // The other end of the same rule: skipping what is missing must not end in
    // skipping everything. A skin with no HUD lettering at all still needs its
    // numbers, and they are ours.
    let dir = skin_folder("no-face");
    write_element(&dir, "hitcircle.png", 128, 255);
    assert_eq!(
        combo_corner(&dir, COMBO_FACE, 40),
        0,
        "this skin has no red in it at all"
    );
    // …but something is in that corner.
    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    skin.sprites = Some(std::sync::Arc::new(
        dossier_render::imported::Sprites::read(
            &dir,
            &[dossier_render::elements::Element::HitCircle],
        )
        .tint_for(&skin.combo_colours),
    ));
    let state = GameState::new(&map, &replay);
    let frame = Scene::new(&state, skin).frame(4200.0, &Layout::new(640, 480));
    let lit = (380..480u32)
        .flat_map(|y| (0..240u32).map(move |x| (x, y)))
        .filter(|&(x, y)| {
            frame
                .pixel(x, y)
                .is_some_and(|p| (i32::from(p.red()) - i32::from(FIELD.0)).abs() > 20)
        })
        .count();
    assert!(lit > 50, "nothing was drawn in the combo's corner at all");
}

// ── the health bar, at the size and in the place it was drawn ────────────
//
// ```csharp
// AutoSizeAxes = Axes.Both;
// AddInternal(new Sprite { Texture = getTexture(skin, "bg") });
// ```
//
// Every piece is exactly as big as its picture and the whole display hangs in
// the corner of the screen. This used to derive one length from whichever piece
// the skin had, cut it by a third and stretch both pieces into it — which is
// near enough on a 695×44 scorebar and nowhere near on `vv_idke_trail`, whose
// `scorebar-bg` is a 1366×786 outline drawn round the whole screen.

#[test]
fn a_skins_scorebar_keeps_its_own_size_and_corner() {
    let dir = skin_folder("scorebar");
    let mut picture = tiny_skia::Pixmap::new(400, 200).expect("a canvas");
    for pixel in picture.pixels_mut() {
        *pixel = tiny_skia::PremultipliedColorU8::from_rgba(0, 200, 0, 255).expect("a colour");
    }
    std::fs::write(
        dir.join("scorebar-bg.png"),
        picture.encode_png().expect("png"),
    )
    .expect("written");

    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    skin.sprites = Some(std::sync::Arc::new(
        dossier_render::imported::Sprites::read(
            &dir,
            &[dossier_render::elements::Element::ScoreBarBackground],
        )
        .tint_for(&skin.combo_colours),
    ));
    let state = GameState::new(&map, &replay);
    let frame = Scene::new(&state, skin).frame(4200.0, &Layout::new(640, 480));

    // A 480-tall frame reads a skin in a 768-tall space, so 400×200 of picture
    // is 250×125 of frame — and it starts in the very corner.
    let green = |x: u32, y: u32| {
        frame
            .pixel(x, y)
            .is_some_and(|p| p.green() > 120 && p.red() < 80)
    };
    assert!(green(1, 1), "it does not start in the corner");
    assert!(green(240, 118), "it is short of the size it was drawn at");
    assert!(!green(258, 118), "it runs past its own width");
    assert!(!green(240, 132), "it runs past its own height");
}

// ── the rim, and which side of the number it falls on ────────────────────
//
// ```text
// ldc.i4.1
// stfld  OverlayAboveNumber
// ```
//
// osu!stable's own `SkinOsu` constructor, read out of the client — see
// `docs/stable-client.md`. Over, then, and this drew it under: the figure went
// down last and the rim ended up behind it. On a skin whose overlay is more
// than a thin rim that is the whole face of the note in the wrong order.

/// A note whose rim is opaque and covers the whole square, so "over or under"
/// is a question a single pixel can answer.
fn covered_note(above: Option<bool>) -> Skin {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let dir = skin_folder(match above {
        Some(true) => "rim-over",
        Some(false) => "rim-under",
        None => "rim-unsaid",
    });
    write_glyph(&dir, "hitcircle.png", 128, (0, 0, 90));
    write_glyph(&dir, "hitcircleoverlay.png", 128, (0, 200, 0));
    for digit in 0..10 {
        write_glyph(&dir, &format!("default-{digit}.png"), 48, (255, 0, 0));
    }
    if let Some(above) = above {
        std::fs::write(
            dir.join("skin.ini"),
            format!(
                "[General]\nHitCircleOverlayAboveNumber: {}\n",
                u8::from(above)
            ),
        )
        .expect("written");
    }

    let map = one_note();
    let mut skin = Skin::with_combo_colours(map.combo_colours());
    let mut wanted = vec![Element::HitCircle, Element::HitCircleOverlay];
    wanted.extend((0..10).map(Element::Digit));
    skin.sprites = Some(std::sync::Arc::new(
        Sprites::read(&dir, &wanted).tint_for(&skin.combo_colours),
    ));
    skin
}

/// Whether the figure is visible at the centre of the note.
fn number_shows(skin: Skin) -> bool {
    let map = one_note();
    let state = GameState::from_beatmap(&map, Mods::default());
    let layout = Layout::new(640, 480);
    let frame = Scene::new(&state, skin).frame(5000.0, &layout);
    let (x, y) = layout.map(dossier_beatmap::Point::CENTRE);
    (0..24).any(|step| {
        frame
            .pixel(x as u32, y as u32 - step)
            .is_some_and(|p| p.red() > 120 && p.green() < 120)
    })
}

#[test]
fn a_skin_that_says_nothing_gets_its_rim_over_the_number() {
    assert!(
        !number_shows(covered_note(None)),
        "the figure came out on top of a rim that covers the note"
    );
}

#[test]
fn a_skin_can_put_its_rim_under_the_number_instead() {
    assert!(
        number_shows(covered_note(Some(false))),
        "`HitCircleOverlayAboveNumber: 0` was not honoured"
    );
    assert!(
        !number_shows(covered_note(Some(true))),
        "and 1 is the default"
    );
}

/// How many pixels of the top-right corner — where the score sits — are within
/// `slack` of `colour`, at the given frame size.
fn score_corner(dir: &std::path::Path, colour: (u8, u8, u8), slack: i32) -> usize {
    use dossier_render::elements::Element;
    use dossier_render::imported::Sprites;
    let (map, replay) = tapped();
    let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
    let wanted: Vec<Element> = ('0'..='9')
        .chain([',', '.', '%', 'x'])
        .flat_map(|c| [Element::Score(c), Element::Combo(c)])
        .collect();
    skin.sprites = Some(std::sync::Arc::new(
        Sprites::read(dir, &wanted).tint_for(&skin.combo_colours),
    ));
    let state = GameState::new(&map, &replay);
    let frame = Scene::new(&state, skin).frame(4200.0, &Layout::new(640, 480));

    let mut count = 0;
    for y in 0..120u32 {
        for x in 400..640u32 {
            let Some(p) = frame.pixel(x, y) else { continue };
            let off = (i32::from(p.red()) - i32::from(colour.0)).abs()
                + (i32::from(p.green()) - i32::from(colour.1)).abs()
                + (i32::from(p.blue()) - i32::from(colour.2)).abs();
            if off <= slack {
                count += 1;
            }
        }
    }
    count
}

#[test]
fn a_skin_that_drew_its_numbers_bigger_gets_bigger_numbers() {
    // The size of a HUD face is the skin's, not the renderer's. danser states
    // it plainly — `scoreSize := overlay.scoreFont.GetSize() * scoreScale *
    // 0.96` — and the thing being scaled is the font's own size.
    //
    // This engine used to normalise every skin's digits to one height, which
    // happened to be right for osu!'s own 40-pixel face and for nothing else.
    // The skins in the bot's store run from 40 to 60, so the tallest of them
    // was drawn a full half smaller than the game draws it.
    fn face(name: &str, size: u32) -> std::path::PathBuf {
        let dir = skin_folder(name);
        for digit in 0..10 {
            write_glyph(&dir, &format!("score-{digit}.png"), size, SCORE_FACE);
        }
        std::fs::write(dir.join("skin.ini"), "[Fonts]\nScorePrefix: score\n").expect("written");
        dir
    }

    let small = score_corner(&face("small-face", 20), SCORE_FACE, 40);
    let large = score_corner(&face("large-face", 40), SCORE_FACE, 40);

    assert!(
        small > 0 && large > 0,
        "both skins drew something: {small}, {large}"
    );
    // Twice the face is four times the ink. Two and a half is slack enough for
    // the overlap and the frame's edge without letting "the same size" pass.
    assert!(
        large as f32 > small as f32 * 2.5,
        "the larger face should cover far more: {small} against {large}"
    );
}

#[test]
fn the_cursor_and_its_trail_are_read_by_the_same_ruler() {
    // Both go through `NonPlayfieldSprite` in lazer, and it adjusts whatever it
    // is handed: `value.ScaleAdjust *= LegacySkin.STABLE_MAGIC_SCALE_FACTOR`.
    // Applied to the trail alone, the cursor came out a full 1.6 times too big
    // — 55 pixels at 720p where the game draws 35 — and the trail beside it
    // read as too thin. It was the wrong half of the pair that looked wrong.
    //
    // Measured rather than asserted about constants: the cursor covers ink, and
    // ink is what somebody looking at the render sees.
    let dir = skin_folder("one-ruler");
    write_glyph(&dir, "cursor.png", 64, SCORE_FACE);
    let ink = |scale: f32| -> usize {
        use dossier_render::elements::Element;
        use dossier_render::imported::Sprites;
        let (map, replay) = tapped();
        let mut skin = Skin::with_combo_colours(map.combo_colours()).with_font(font());
        skin.cursor_scale = scale;
        skin.sprites = Some(std::sync::Arc::new(
            Sprites::read(&dir, &[Element::Cursor]).tint_for(&skin.combo_colours),
        ));
        let state = GameState::new(&map, &replay);
        let frame = Scene::new(&state, skin).frame(4200.0, &Layout::new(640, 480));
        let mut count = 0;
        for y in 0..480u32 {
            for x in 0..640u32 {
                if let Some(p) = frame.pixel(x, y) {
                    if p.blue() > 200 && p.red() < 60 && p.green() < 60 {
                        count += 1;
                    }
                }
            }
        }
        count
    };

    let (small, plain, large) = (ink(0.5), ink(1.0), ink(2.0));
    assert!(plain > 0, "the cursor was not drawn at all");
    // Area goes as the square of the scale, so halving covers about a quarter
    // and doubling about four times. Generous bounds: the point is that the
    // setting reaches the cursor, not that anti-aliasing is exact.
    assert!(
        small * 2 < plain,
        "0.5 should be far smaller: {small} against {plain}"
    );
    assert!(
        large > plain * 2,
        "2.0 should be far larger: {large} against {plain}"
    );
}
