//! What a map is worth, and what a play on it is worth — ppy's arithmetic,
//! ported.
//!
//! The bot showed a star rating the game disagreed with and a pp figure twice
//! what the game gives, and each was patched by asking ppy directly. That works
//! and it is what ships today, but it can only answer questions ppy has an
//! endpoint for: it will say what a map is worth with mods on it, and what a
//! finished play scored, and nothing else. It will not say what a play would
//! have been worth without the misses, and it cannot be asked sixty times a
//! second while a render draws.
//!
//! The port that filled those gaps is behind — 0.20 to 0.82 stars out on the
//! same map and mods — and there is no newer release to move to. So: our own,
//! against ppy's sources.
//!
//! # Why this is tractable
//!
//! The expensive half of a difficulty calculation is not the arithmetic, it is
//! everything underneath it — parsing the map, working out slider curves,
//! stacking, timing, applying mods to both the numbers and the geometry. That
//! is already here, written for the renderer and exercised by every frame it
//! draws: [`dossier_beatmap`] parses and holds the curves,
//! [`dossier_sim::Timeline`] resolves objects under mods with their stacks and
//! their slider ticks. This crate is the layer above.
//!
//! An assay is the test that tells you how much of the precious metal is in the
//! ore. This one is handed a map and a play and asked the same question, which
//! is why it is called that, and it sits beside `dossier-exhibit` in taking its
//! name from the business of putting evidence in front of somebody.
//!
//! # How it is kept honest
//!
//! ppy's attributes endpoint answers with the official numbers for any map with
//! any mods, and `corpus/` holds its answers beside the maps they describe. So
//! every figure here has something to be wrong against, from the first one:
//! `corpus/expected.json` carries the star rating and eight attributes for
//! fifteen mod sets on ten maps, and the tests check ours against theirs.
//!
//! Regenerating the corpus is also how a rebalance is noticed. ppy change these
//! formulas several times a year and announce it nowhere this code would see;
//! rerun `scripts/pp_corpus.py` and a diff on the corpus is a diff on their
//! arithmetic.
//!
//! # What is here so far
//!
//! [`max_combo`], and it is a real first step rather than a placeholder: the
//! largest combo a map allows counts every circle, every slider head, tail,
//! repeat and tick, and every spinner, so agreeing with ppy on it means the
//! slider tick spacing and the repeat handling underneath are right. Those are
//! what the difficulty calculation walks over.

pub mod aim;
pub mod flashlight;
pub mod legacy;
pub mod performance;
pub mod preprocessing;
pub mod reading;
pub mod slider;
pub mod speed;
pub mod strain;
pub mod utils;

use dossier_beatmap::Beatmap;
use dossier_replay::Mods;
use dossier_sim::{TimedKind, TimedObject, Timeline};

use crate::slider::{nested_objects, tick_distance, NestedObject};

/// Everything the attributes endpoint reports about how hard a map is, so far.
#[derive(Debug, Clone, Default)]
pub struct Attributes {
    pub max_combo: u32,
    pub aim_difficulty: f64,
    pub speed_difficulty: f64,
    pub slider_factor: f64,
    pub aim_difficult_slider_count: f64,
    pub aim_difficult_strain_count: f64,
    pub speed_difficult_strain_count: f64,
    pub speed_note_count: f64,
    /// The map's overall difficulty with mods on it, before any window is taken
    /// from it — the performance side needs it to work its own windows out.
    pub overall_difficulty_raw: f64,
    pub reading_difficulty: f64,
    pub reading_difficult_note_count: f64,
    pub flashlight_difficulty: f64,
    pub aim_top_weighted_slider_factor: f64,
    pub speed_top_weighted_slider_factor: f64,
    /// What the map is made of, which the performance side needs and the
    /// difficulty side only counts.
    pub hit_circle_count: u32,
    pub slider_count: u32,
    pub spinner_count: u32,
    /// Slider ticks and repeat arrows together — lazer's "large ticks".
    ///
    /// Needed because accuracy counts them: a play's figure is not derivable
    /// from its four judgements under lazer's rules, and this is half of what
    /// else goes in.
    pub large_tick_count: u32,
    /// What the old scoring would have made of this map, which is what lets a
    /// stable score be read back out of its total.
    pub nested_score_per_object: f64,
    pub legacy_score_base_multiplier: f64,
    pub maximum_legacy_combo_score: f64,
    /// What everything above adds up to.
    pub star_rating: f64,
}

/// How aim, speed and cognition are added into one number.
///
/// ```csharp
/// public const double PERFORMANCE_NORM_EXPONENT = 1.1;
/// public const double PERFORMANCE_BASE_MULTIPLIER = 1.12;
/// ```
pub(crate) const PERFORMANCE_NORM_EXPONENT: f64 = 1.1;
pub(crate) const PERFORMANCE_BASE_MULTIPLIER: f64 = 1.12;

/// The three hit windows a play was judged at, one-sided and in the play's own
/// time — which is the shape the performance calculator wants them in.
pub fn hit_windows(overall_difficulty: f64, clock_rate: f64) -> (f64, f64, f64) {
    let at = |min, mid, max| {
        (dossier_beatmap::difficulty_range(overall_difficulty, min, mid, max).floor() - 0.5)
            / clock_rate
    };
    (at(80.0, 50.0, 20.0), at(140.0, 100.0, 60.0), at(200.0, 150.0, 100.0))
}

/// Work the map out once, under `mods`.
///
/// One entry point rather than a function per figure, because they share the
/// walk: the difficulty objects are built once and both skills read them, and
/// the counters need the difficulty values that produced them.
pub fn attributes(beatmap: &Beatmap, mods: Mods) -> Attributes {
    use dossier_replay::bits;
    let objects = preprocessing::difficulty_objects(beatmap, mods);
    let relax = mods.contains(bits::RELAX);
    let touch = mods.contains(bits::TOUCH_DEVICE);
    let autopilot = mods.contains(bits::AUTOPILOT);

    let mut with = aim::Aim::of(&objects, true, relax, touch, autopilot);
    let mut without = aim::Aim::of(&objects, false, relax, touch, autopilot);
    let aim_value = with.difficulty_value();
    let aim_rating = aim::difficulty_rating(aim_value);

    let mut speed = speed::Speed::of(&objects, relax);
    let speed_value = speed.difficulty_value();

    let hidden = mods.contains(bits::HIDDEN);
    let mut reading = reading::Reading::of(&objects, hidden, relax, touch, autopilot);
    let reading_value = reading.difficulty_value();
    let reading_rating = reading::difficulty_rating(reading_value);

    let has_flashlight = mods.contains(bits::FLASHLIGHT);
    let torch = flashlight::Flashlight::of(
        &objects, has_flashlight, hidden, relax, touch, autopilot, objects.len() + 1,
    );
    let flashlight_rating = if has_flashlight {
        flashlight::difficulty_rating(torch.difficulty_value())
    } else {
        0.0
    };

    // Each skill's rating becomes what it would be worth as performance, and
    // the three are added as a p-norm — so a map hard at everything counts for
    // more than any one of them and less than their sum. The star rating is
    // that total put back on a human scale.
    let cognition = flashlight::sum_cognition(
        speed::harmonic_to_performance(reading_rating),
        flashlight::difficulty_to_performance(flashlight_rating),
        PERFORMANCE_NORM_EXPONENT,
    );
    let base = utils::norm(
        PERFORMANCE_NORM_EXPONENT,
        &[
            aim::difficulty_to_performance(aim_rating),
            speed::harmonic_to_performance(speed::difficulty_rating(speed_value)),
            cognition,
        ],
    );
    let star_rating = (base * PERFORMANCE_BASE_MULTIPLIER).cbrt();

    // How much of each skill's difficulty sits on its sliders, which is what
    // lets a classic score's dropped ends be guessed at.
    let no_slider_value = without.difficulty_value();
    let aim_slider_count = without.count_top_weighted_sliders(no_slider_value);
    let aim_strain_count = without.top_weighted_strains(no_slider_value);
    let aim_top_weighted_slider_factor =
        aim_slider_count / (aim_strain_count - aim_slider_count).max(1.0);
    let speed_slider_count = speed.count_top_weighted_sliders(speed_value);
    let speed_strain_count = speed.top_weighted_strains(speed_value);
    let speed_top_weighted_slider_factor =
        speed_slider_count / (speed_strain_count - speed_slider_count).max(1.0);

    let timeline = dossier_sim::Timeline::build(beatmap, mods);
    let (mut circles, mut sliders, mut spinners) = (0, 0, 0);
    for object in &timeline.objects {
        match object.kind {
            dossier_sim::TimedKind::Circle => circles += 1,
            dossier_sim::TimedKind::Slider { .. } => sliders += 1,
            dossier_sim::TimedKind::Spinner => spinners += 1,
        }
    }

    Attributes {
        max_combo: max_combo(beatmap, mods),
        aim_difficulty: aim_rating,
        speed_difficulty: speed::difficulty_rating(speed_value),
        slider_factor: if aim_value > 0.0 {
            aim::difficulty_rating(without.difficulty_value()) / aim_rating
        } else {
            1.0
        },
        aim_difficult_slider_count: with.difficult_sliders(),
        aim_difficult_strain_count: with.top_weighted_strains(aim_value),
        // After `difficulty_value`, which is what fills the weight sum it divides by.
        speed_difficult_strain_count: speed.top_weighted_strains(speed_value),
        reading_difficult_note_count: reading.top_weighted_notes(reading_value),
        speed_note_count: speed.note_count(),
        overall_difficulty_raw: timeline.difficulty.overall_difficulty,
        reading_difficulty: reading_rating,
        flashlight_difficulty: flashlight_rating,
        star_rating,
        aim_top_weighted_slider_factor,
        speed_top_weighted_slider_factor,
        hit_circle_count: circles,
        slider_count: sliders,
        spinner_count: spinners,
        large_tick_count: timeline
            .objects
            .iter()
            .map(|object| {
                slider_parts(beatmap, object)
                    .iter()
                    .filter(|part| {
                        matches!(part.kind, slider::Nested::Tick | slider::Nested::Repeat)
                    })
                    .count() as u32
            })
            .sum(),
        nested_score_per_object: legacy::nested_score_per_object(
            beatmap, mods, circles + sliders + spinners,
        ),
        legacy_score_base_multiplier: f64::from(legacy::difficulty_peppy_stars(beatmap)),
        maximum_legacy_combo_score: legacy::maximum_combo_score(beatmap, mods),
    }
}

/// The map's aiming difficulty under `mods`, as `aim_difficulty`, and the
/// `slider_factor` that comes with it.
///
/// The skill is built twice — once counting the travel through sliders and once
/// not — because their ratio is exactly what that factor reports.
pub fn aim_difficulty(beatmap: &Beatmap, mods: Mods) -> (f64, f64) {
    use dossier_replay::bits;
    let objects = preprocessing::difficulty_objects(beatmap, mods);
    let relax = mods.contains(bits::RELAX);
    let touch = mods.contains(bits::TOUCH_DEVICE);
    let autopilot = mods.contains(bits::AUTOPILOT);

    let mut with = aim::Aim::of(&objects, true, relax, touch, autopilot);
    let mut without = aim::Aim::of(&objects, false, relax, touch, autopilot);
    let value = with.difficulty_value();
    let rating = aim::difficulty_rating(value);
    let slider_factor = if value > 0.0 {
        aim::difficulty_rating(without.difficulty_value()) / rating
    } else {
        1.0
    };
    (rating, slider_factor)
}

/// The map's pressing difficulty under `mods`, as `speed_difficulty`.
pub fn speed_difficulty(beatmap: &Beatmap, mods: Mods) -> f64 {
    let objects = preprocessing::difficulty_objects(beatmap, mods);
    let relax = mods.contains(dossier_replay::bits::RELAX);
    let mut skill = speed::Speed::of(&objects, relax);
    speed::difficulty_rating(skill.difficulty_value())
}

/// Every piece of `object`, if it is a slider, the way osu! builds them.
///
/// The tick spacing needs the tempo in force where the slider starts, which is
/// the one number a resolved object does not carry, so the map is asked.
pub fn slider_parts(beatmap: &Beatmap, object: &TimedObject) -> Vec<NestedObject> {
    let TimedKind::Slider { path, slides, slide_duration_ms, .. } = &object.kind else {
        return Vec::new();
    };
    let velocity = if *slide_duration_ms > 0.0 {
        path.length() / slide_duration_ms
    } else {
        0.0
    };
    let beat_length = beatmap
        .timing
        .timing_point_at(object.start_ms)
        .map_or(0.0, |point| point.beat_length);
    nested_objects(
        path,
        object.start_ms,
        *slide_duration_ms,
        *slides,
        tick_distance(velocity, beat_length, beatmap.difficulty.slider_tick_rate),
        velocity,
    )
}

/// The greatest combo a map allows, under `mods`.
///
/// Everything that can be hit counts once: a circle, a slider's head, each of
/// its ticks, each of its repeats, its tail, and a spinner. This is `MaxCombo`
/// in ppy's difficulty attributes, and the endpoint reports it, so it is the
/// one figure here that can be checked before any skill exists.
///
/// Mods are taken because they change the answer. Not through the rate — a
/// slider's ticks are spaced by distance and come out the same however fast it
/// is played — but through HardRock and Easy, which move the circle size and so
/// the stacking, and through the difficulty numbers the tick rate is read
/// against.
pub fn max_combo(beatmap: &Beatmap, mods: Mods) -> u32 {
    let timeline = Timeline::build(beatmap, mods);
    timeline
        .objects
        .iter()
        .map(|object| match &object.kind {
            // The circle itself, and the spinner once however long it is spun.
            TimedKind::Circle | TimedKind::Spinner => 1,
            // Everything the slider is made of, each worth one: head, ticks,
            // repeats, tail. Counted off the same list the difficulty
            // calculation walks rather than off a formula, so the two cannot
            // come to different answers about the same slider.
            TimedKind::Slider { .. } => slider_parts(beatmap, object).len() as u32,
        })
        .sum()
}

