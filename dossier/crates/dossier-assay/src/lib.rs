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

pub mod slider;

use dossier_beatmap::Beatmap;
use dossier_replay::Mods;
use dossier_sim::{TimedKind, TimedObject, Timeline};

use crate::slider::{nested_objects, tick_distance, NestedObject};

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

