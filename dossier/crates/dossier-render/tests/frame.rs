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
