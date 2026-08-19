//! What a slider is made of, in the order osu! makes it.
//!
//! A slider is one object to the map file and several to everything else: a
//! head, a tick wherever the ball passes a scoring distance, a repeat at each
//! turn, and a tail. The difficulty calculation walks that list — it is how the
//! cursor's path through a slider is worked out — so it has to be the same list
//! ppy builds, in the same order, at the same places.
//!
//! Ported from `SliderEventGenerator.Generate` and the `CreateNestedHitObjects`
//! that consumes it.
//!
//! # Why this is here and not taken from the renderer
//!
//! [`dossier_sim`] already places slider ticks, and does it by time: the ball
//! moves at a constant speed along a slide, so a tick every `scoring_distance /
//! tick_rate` of path works out to one every `beat_length / tick_rate` of
//! clock. That equivalence is exact and the renderer is right to use it.
//!
//! It is right about *where* the ticks are and silent about where they stop.
//! osu! walks the path and stops a tick short of the end by `velocity * 10` —
//! ten milliseconds of travel, expressed as a distance — where the renderer
//! stops an eighth of a tick short. The two agree on ordinary sliders and part
//! company on strange ones, and this crate exists to agree with ppy on the
//! strange ones too.

use dossier_beatmap::{Point, SliderPath};

/// A slider's tail is judged this many milliseconds before the ball actually
/// arrives.
///
/// ```csharp
/// public const double TAIL_LENIENCY = -36;
/// ```
///
/// ppy's own note on it is worth keeping: it began as a workaround, and stayed
/// because players came to expect that a fast slider can be left a hair early.
pub const TAIL_LENIENCY: f64 = -36.0;

/// A slider longer than this is not given ticks at all.
///
/// ```csharp
/// // This exists for edge cases such as /b/1573664 where the beatmap has been
/// // edited by the user, and should never be reached in normal usage.
/// const double max_length = 100000;
/// ```
const MAX_LENGTH: f64 = 100_000.0;

/// Which part of the slider a nested object is.
///
/// The tail is told apart from the rest because the cursor is allowed to leave
/// it early, and a repeat because the game expects the cursor to be tighter to
/// it than to a tick.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Nested {
    Head,
    Tick,
    Repeat,
    Tail,
}

/// One piece of a slider: when it is due, and where.
#[derive(Debug, Clone, Copy)]
pub struct NestedObject {
    pub kind: Nested,
    pub time_ms: f64,
    /// Absolute, with the slider's stack shift already in it — the path this
    /// was taken from was translated when the map was stacked.
    pub pos: Point,
}

/// Every piece of a slider, earliest first.
///
/// `span_duration_ms` is one traversal, `spans` is how many traversals there
/// are — one more than the repeat count — and `tick_distance` is
/// `scoring_distance / tick_rate` in osu!pixels.
///
/// The `LegacyLastTick` the generator also emits is left out, exactly as
/// `CreateNestedHitObjects` leaves it out: it has no case there and survives
/// only for osu!catch's conversion.
pub fn nested_objects(
    path: &SliderPath,
    start_ms: f64,
    span_duration_ms: f64,
    spans: u32,
    tick_distance: f64,
    velocity: f64,
) -> Vec<NestedObject> {
    let mut out = Vec::new();
    let at = |progress: f64| path.position_at(progress).unwrap_or(Point { x: 0.0, y: 0.0 });

    out.push(NestedObject { kind: Nested::Head, time_ms: start_ms, pos: at(0.0) });
    if span_duration_ms <= 0.0 || spans == 0 {
        return out;
    }

    let length = path.length().min(MAX_LENGTH);
    let tick_distance = tick_distance.clamp(0.0, length);
    // Ticks are not placed within ten milliseconds of travel from the end.
    let min_distance_from_end = velocity * 10.0;

    for span in 0..spans {
        let span_start = start_ms + f64::from(span) * span_duration_ms;
        let reversed = span % 2 == 1;

        if tick_distance > 0.0 {
            // Always measured from the start of the path rather than of the
            // span, so a tick on a reversed traversal sits where its twin sits
            // on a forward one.
            let mut ticks = Vec::new();
            let mut d = tick_distance;
            while d <= length {
                if d >= length - min_distance_from_end {
                    break;
                }
                let path_progress = d / length;
                let time_progress = if reversed { 1.0 - path_progress } else { path_progress };
                ticks.push(NestedObject {
                    kind: Nested::Tick,
                    time_ms: span_start + time_progress * span_duration_ms,
                    pos: at(path_progress),
                });
                d += tick_distance;
                if ticks.len() >= 10_000 {
                    break;
                }
            }
            if reversed {
                // Generated in reverse-time order on a reversed traversal.
                ticks.reverse();
            }
            out.extend(ticks);
        }

        if span + 1 < spans {
            out.push(NestedObject {
                kind: Nested::Repeat,
                time_ms: span_start + span_duration_ms,
                pos: at(f64::from((span + 1) % 2)),
            });
        }
    }

    out.push(NestedObject {
        kind: Nested::Tail,
        time_ms: start_ms + f64::from(spans) * span_duration_ms,
        pos: at(f64::from(spans % 2)),
    });
    out
}

/// The distance between ticks, in osu!pixels.
///
/// ```csharp
/// double scoringDistance = base_scoring_distance * difficulty.SliderMultiplier * SliderVelocityMultiplier;
/// Velocity = scoringDistance / timingPoint.BeatLength;
/// TickDistance = scoringDistance / difficulty.SliderTickRate;
/// ```
///
/// Given as velocity and beat length because that is what a resolved slider
/// knows: its speed is its path over its span, and multiplying back by the beat
/// length recovers the scoring distance the two were derived from.
pub fn tick_distance(velocity: f64, beat_length_ms: f64, tick_rate: f64) -> f64 {
    if tick_rate <= 0.0 {
        return 0.0;
    }
    velocity * beat_length_ms / tick_rate
}
