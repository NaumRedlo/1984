//! Each object as the difficulty calculation sees it: how far the cursor had to
//! come, how sharply it had to turn, and how long it had to do it in.
//!
//! Ported from `OsuDifficultyHitObject`. Every skill reads these and nothing
//! else, so the whole of the difficulty side rests on this file being right.
//!
//! # The two ideas in it
//!
//! **Distances are normalised.** A map with small circles asks for more precise
//! movement than one with large circles over the same pixels, so every distance
//! is scaled by `50 / radius` — fifty being a made-up radius that makes the
//! diameter a hundred and the mental arithmetic easy. After that a "one
//! diameter" jump means the same thing on every map.
//!
//! **A slider is followed lazily.** A player does not trace a slider; they hold
//! the cursor where the follow circle still catches the ball and move only when
//! it would otherwise slip out. So the cursor's path through a slider is walked
//! piece by piece, moving only when the next piece is further away than the
//! follow circle's reach, and where it ends up is where the next jump starts
//! from. That end is the "lazy end position", and it is why the pieces of a
//! slider had to be built first.

use dossier_beatmap::{Beatmap, Point};
use dossier_replay::Mods;
use dossier_sim::{TimedKind, TimedObject, Timeline};

use crate::slider::{Nested, NestedObject};

/// The radius every distance is scaled to, so circle size stops mattering.
///
/// ```csharp
/// public const int NORMALISED_RADIUS = 50; // Change radius to 50 to make 100 the diameter.
/// ```
pub const NORMALISED_RADIUS: f64 = 50.0;

pub const NORMALISED_DIAMETER: f64 = NORMALISED_RADIUS * 2.0;

/// No two objects are ever treated as closer together in time than this.
///
/// ```csharp
/// // Capped to 25ms to prevent difficulty calculation breaking from simultaneous objects.
/// public const int MIN_DELTA_TIME = 25;
/// ```
pub const MIN_DELTA_TIME: f64 = 25.0;

/// How far the follow circle is assumed to reach, and how far it can be pushed.
///
/// The first is what the cursor is allowed to sit away from the ball before it
/// has to move; the second is used when deciding whether a player cut a slider
/// short or followed it through.
const ASSUMED_SLIDER_RADIUS: f64 = NORMALISED_RADIUS * 1.8;
const MAXIMUM_SLIDER_RADIUS: f64 = NORMALISED_RADIUS * 2.4;

/// One object, with everything the skills ask of it.
///
/// Times are already divided by the clock rate — a skill never sees map time —
/// and distances are already normalised.
#[derive(Debug, Clone)]
pub struct DiffObject {
    /// Where it sits in the map's own object list.
    pub index: usize,
    pub start_time: f64,
    pub end_time: f64,
    /// Since the previous object started.
    pub delta_time: f64,
    /// The same, never less than [`MIN_DELTA_TIME`].
    pub adjusted_delta_time: f64,
    /// Since the previous object *ended*, never less than [`MIN_DELTA_TIME`].
    pub last_object_end_delta_time: f64,
    /// Start of the previous object to start of this one.
    pub jump_distance: f64,
    /// Where the cursor was left by the previous object, to the start of this
    /// one. The same as `jump_distance` unless the previous was a slider.
    pub lazy_jump_distance: f64,
    /// The shorter of two readings of that jump — see [`Self::lazy_jump_distance`]
    /// and the note in `set_distances`.
    pub minimum_jump_distance: f64,
    pub minimum_jump_time: f64,
    /// How far the cursor travelled *within* this object.
    pub travel_distance: f64,
    pub travel_time: f64,
    /// The turn the player makes at this object, in radians, if there is
    /// enough history to say.
    pub angle: Option<f64>,
    /// The same vector's angle folded into one quadrant, so a jump and its
    /// mirror image read alike.
    pub normalised_vector_angle: Option<f64>,
    /// Where the cursor ends up if this slider is followed as lazily as the
    /// game allows.
    pub lazy_end_position: Option<Point>,
    pub lazy_travel_distance: f64,
    pub lazy_travel_time: f64,
    /// The window a Great is given, after the clock rate.
    pub hit_window_great: f64,
    pub is_slider: bool,
    pub is_spinner: bool,
    /// Where the object itself is, stacked.
    pub pos: Point,
    pub radius: f64,
}

impl DiffObject {
    /// How long the object is on screen before it must be hit.
    pub fn preempt(&self, preempt_ms: f64, clock_rate: f64) -> f64 {
        preempt_ms / clock_rate
    }

    /// A nudge for maps whose circles are smaller than usual.
    ///
    /// ```csharp
    /// public double SmallCircleBonus => Math.Max(1.0, 1.0 + (30 - BaseObject.Radius) / 70);
    /// ```
    pub fn small_circle_bonus(&self) -> f64 {
        (1.0 + (30.0 - self.radius) / 70.0).max(1.0)
    }

    /// The overall difficulty this object's own hit window implies.
    pub fn overall_difficulty(&self) -> f64 {
        (79.5 - self.hit_window_great / 2.0) / 6.0
    }

    /// How possible it is, from nothing to one, to hit this object and the next
    /// with a single roll of two fingers and still be judged perfectly.
    ///
    /// Ported from `CalculateDoubleTapFeasibility`. Three things make it
    /// possible: the two gaps being alike, the gap being short against the hit
    /// window, and the two circles overlapping enough that one aim serves both.
    pub fn double_tap_feasibility(&self, next: Option<&DiffObject>) -> f64 {
        let Some(next) = next else { return 0.0 };

        let here = self.delta_time.max(1.0);
        let there = next.delta_time.max(1.0);
        let difference = (there - here).abs();

        let speed_ratio = here / here.max(difference);
        let window_ratio = (here / self.hit_window_great).min(1.0).powi(5);
        // No double-tapping two circles that do not touch.
        let distance_factor = crate::utils::reverse_lerp(
            self.lazy_jump_distance,
            NORMALISED_DIAMETER,
            NORMALISED_RADIUS,
        )
        .powi(2);

        1.0 - speed_ratio.powf(distance_factor * (1.0 - window_ratio))
    }
}

/// Everything after the first object, which has nothing to be measured against.
///
/// The list is built in order because each entry leans on the two before it:
/// the cursor's position at the end of the previous object decides where this
/// one's jump began, and the one before that decides the angle.
pub fn difficulty_objects(beatmap: &Beatmap, mods: Mods) -> Vec<DiffObject> {
    let timeline = Timeline::build(beatmap, mods);
    let clock_rate = mods.speed_multiplier();
    let radius = timeline.difficulty.circle_radius();
    // Deliberately not `hit_window_300`, which truncates to a whole
    // millisecond. That truncation is stable's, and the judge is right to want
    // it: the game casts the window to an integer before comparing anything
    // against it, so a fractional OD really does hand out a 100 where the
    // fraction would have given a 300.
    //
    // The difficulty calculation does no such thing — `OsuHitWindows.WindowFor`
    // hands back the interpolated value — and the difference is not academic.
    // It showed up as HardRock agreeing with ppy exactly while everything else
    // was a fraction of a per cent out and Easy was four per cent out: HardRock
    // caps overall difficulty at ten, where the window is a whole number and
    // there is nothing to truncate, and Easy halves it into a fraction almost
    // every time.
    //
    // And doubled, because ppy's is the *full* window — both sides of the note:
    //
    // ```csharp
    // protected double HitWindow(HitResult hitResult) => 2 * getRawHitWindow(hitResult) / ClockRate;
    // ```
    //
    // `OverallDifficulty => (79.5 - HitWindowGreat / 2) / 6` is the same fact
    // stated twice: the halving there only recovers an overall difficulty
    // because what it halves is the doubled window.
    //
    // Missing it left the pressing figure a fraction of a per cent out almost
    // everywhere and four per cent out under Easy — the cap it feeds saturates
    // when the window is small, so HardRock could not feel the mistake and
    // Easy, with the widest window of any mod, felt it most.
    let hit_window_great = 2.0
        * dossier_beatmap::difficulty_range(timeline.difficulty.overall_difficulty, 80.0, 50.0, 20.0)
        / clock_rate;

    // Walked once up front: the lazy path through a slider depends only on the
    // slider, so it is worked out before anything asks where a jump started.
    let parts: Vec<Vec<NestedObject>> = timeline
        .objects
        .iter()
        .map(|object| crate::slider_parts(beatmap, object))
        .collect();

    let mut out: Vec<DiffObject> = Vec::with_capacity(timeline.objects.len().saturating_sub(1));
    for (index, object) in timeline.objects.iter().enumerate().skip(1) {
        let last = &timeline.objects[index - 1];
        let delta_time = (object.start_ms - last.start_ms) / clock_rate;
        let adjusted_delta_time = delta_time.max(MIN_DELTA_TIME);

        let mut current = DiffObject {
            index,
            start_time: object.start_ms / clock_rate,
            end_time: object.end_ms / clock_rate,
            delta_time,
            adjusted_delta_time,
            last_object_end_delta_time: match out.last() {
                Some(previous) => (object.start_ms / clock_rate - previous.end_time)
                    .max(MIN_DELTA_TIME),
                // Nothing before it to have ended, so the plain gap stands.
                None => adjusted_delta_time,
            },
            jump_distance: 0.0,
            lazy_jump_distance: 0.0,
            minimum_jump_distance: 0.0,
            minimum_jump_time: adjusted_delta_time,
            travel_distance: 0.0,
            travel_time: 0.0,
            angle: None,
            normalised_vector_angle: None,
            lazy_end_position: None,
            lazy_travel_distance: 0.0,
            lazy_travel_time: 0.0,
            hit_window_great,
            is_slider: object.is_slider(),
            is_spinner: object.is_spinner(),
            pos: object.pos,
            radius,
        };

        compute_slider_cursor_position(&mut current, object, &parts[index], radius);
        set_distances(&mut current, object, last, &out, &parts, clock_rate, radius);
        out.push(current);
    }
    out
}

/// Where the cursor ends up, and how far it travelled, if this slider is
/// followed as lazily as the game allows.
///
/// Ported from `computeSliderCursorPosition`, including the part ppy's own
/// comment calls not correct: when the last real tick falls after the point the
/// player may let go, that tick is moved to the end of the list. It produces an
/// ordering nobody would describe a slider with, and it is what the official
/// numbers are computed from, so it is what happens here.
fn compute_slider_cursor_position(
    current: &mut DiffObject,
    object: &TimedObject,
    parts: &[NestedObject],
    radius: f64,
) {
    let TimedKind::Slider { path, slide_duration_ms, .. } = &object.kind else {
        return;
    };
    if parts.is_empty() {
        return;
    }

    let duration = object.end_ms - object.start_ms;
    // The player must hold until a hair before the end, or halfway, whichever
    // is later — a slider under 72ms gets less leniency than the flat 36.
    let mut tracking_end = (object.start_ms + duration + crate::slider::TAIL_LENIENCY)
        .max(object.start_ms + duration / 2.0);

    let mut ordered: Vec<NestedObject> = parts.to_vec();
    let last_tick = ordered
        .iter()
        .rposition(|part| part.kind == Nested::Tick);
    if let Some(at) = last_tick {
        if ordered[at].time_ms > tracking_end {
            tracking_end = ordered[at].time_ms;
            // Not a sensible order for a slider and it is the order the
            // official figures come from. ppy's note: "this is definitely not
            // correct from a difficulty calculation perspective ... but allows
            // a zero-diff with known diffcalc output".
            let moved = ordered.remove(at);
            ordered.push(moved);
        }
    }

    current.lazy_travel_time = tracking_end - object.start_ms;

    // How far along one traversal that leaves the ball, bouncing back and forth
    // for a slider with repeats.
    let mut progress = if *slide_duration_ms > 0.0 {
        current.lazy_travel_time / slide_duration_ms
    } else {
        0.0
    };
    progress = if progress % 2.0 >= 1.0 {
        1.0 - progress % 1.0
    } else {
        progress % 1.0
    };
    let mut lazy_end = path.position_at(progress).unwrap_or(object.pos);

    let mut cursor = object.pos;
    let scaling = NORMALISED_RADIUS / radius;

    for (at, part) in ordered.iter().enumerate().skip(1) {
        let last = at == ordered.len() - 1;
        let mut movement = Point {
            x: part.pos.x - cursor.x,
            y: part.pos.y - cursor.y,
        };
        let mut length = scaling * hypot(movement);

        // How far the ball may drift before the cursor has to follow.
        let mut required = ASSUMED_SLIDER_RADIUS;

        if last {
            // The end of a slider is judged loosely enough that the player may
            // take whichever of the two paths is shorter — to where the ball
            // actually stops, or to the lazy end. On a circular slider the
            // lazy end can be the further of the two, and this keeps that from
            // being rewarded.
            let lazy_movement = Point {
                x: lazy_end.x - cursor.x,
                y: lazy_end.y - cursor.y,
            };
            if hypot(lazy_movement) < hypot(movement) {
                movement = lazy_movement;
            }
            length = scaling * hypot(movement);
        } else if part.kind == Nested::Repeat {
            // A repeat is turned on the spot, so the cursor is expected to be
            // closer to it than to a tick.
            required = NORMALISED_RADIUS;
        }

        if length > required {
            let keep = (length - required) / length;
            cursor = Point {
                x: cursor.x + movement.x * keep,
                y: cursor.y + movement.y * keep,
            };
            current.lazy_travel_distance += length * keep;
        }

        if last {
            lazy_end = cursor;
        }
    }

    current.lazy_end_position = Some(lazy_end);
}

/// How far the cursor came to this object, and how sharply it turned.
///
/// Ported from `setDistances`.
#[allow(clippy::too_many_arguments)]
fn set_distances(
    current: &mut DiffObject,
    object: &TimedObject,
    last: &TimedObject,
    previous: &[DiffObject],
    parts: &[Vec<NestedObject>],
    clock_rate: f64,
    radius: f64,
) {
    if let TimedKind::Slider { slides, .. } = &object.kind {
        // A slider with repeats asks for more than one without, and this stands
        // in for judging each piece on its own.
        let repeats = f64::from(slides.saturating_sub(1));
        current.travel_distance = current.lazy_travel_distance * repeats.powf(0.3).max(1.0);
        current.travel_time = (current.lazy_travel_time / clock_rate).max(MIN_DELTA_TIME);
    }

    current.minimum_jump_time = current.adjusted_delta_time;

    // A spinner is not aimed at, so neither the distance to it nor the angle
    // through it means anything.
    if current.is_spinner || last.is_spinner() {
        return;
    }

    let scaling = NORMALISED_RADIUS / radius;
    let last_diff = previous.last();
    let last_last_diff = previous.len().checked_sub(2).and_then(|at| previous.get(at));

    let mut last_cursor = last_diff
        .and_then(|diff| diff.lazy_end_position)
        .unwrap_or(last.pos);

    current.jump_distance = distance(last.pos, object.pos) * scaling;
    current.lazy_jump_distance = distance(last_cursor, object.pos) * scaling;
    current.minimum_jump_distance = current.lazy_jump_distance;

    if last.is_slider() {
        if let Some(last_diff) = last_diff {
            let last_travel = (last_diff.lazy_travel_time / clock_rate).max(MIN_DELTA_TIME);
            current.minimum_jump_time =
                (current.adjusted_delta_time - last_travel).max(MIN_DELTA_TIME);

            // Two ways to leave a slider, and the player is assumed to take
            // whichever is shorter.
            //
            // Cutting it short — moving off before the ball is done — is what
            // the lazy jump distance describes. Following it through to the
            // visible end and jumping from there is described by the distance
            // from the slider's tail. A pattern where the next circle is
            // stacked inside the slider is the first; a pattern where it
            // continues past the tail is the second.
            let tail = parts[last_diff.index]
                .last()
                .map_or(last.pos, |part| part.pos);
            let tail_jump = distance(tail, object.pos) * scaling;
            current.minimum_jump_distance = (current.lazy_jump_distance
                - (MAXIMUM_SLIDER_RADIUS - ASSUMED_SLIDER_RADIUS))
                .min(tail_jump - MAXIMUM_SLIDER_RADIUS)
                .max(0.0);
        }
    }

    let Some(last_last_diff) = last_last_diff else { return };
    if last_last_diff.is_spinner {
        return;
    }
    let Some(last_diff) = last_diff else { return };

    // A slider the cursor genuinely travelled through is turned *from its
    // head*, not from where the ball was let go.
    if last_diff.is_slider && last_diff.travel_distance > 0.0 {
        last_cursor = parts[last_diff.index]
            .first()
            .map_or(last.pos, |part| part.pos);
    }

    let last_last_cursor = last_last_diff
        .lazy_end_position
        .unwrap_or(last_last_diff.pos);

    let angle = corner(object.pos, last_cursor, last_last_cursor);
    let slider_angle = slider_angle(object.pos, last_diff, parts, last, last_last_cursor);

    let v = Point {
        x: object.pos.x - last_cursor.x,
        y: object.pos.y - last_cursor.y,
    };
    current.normalised_vector_angle = Some(v.y.abs().atan2(v.x.abs()));
    current.angle = Some(angle.min(slider_angle));
}

/// The same corner, measured as though the previous slider were left at its
/// second-to-last piece rather than wherever the cursor drifted to.
fn slider_angle(
    pos: Point,
    last_diff: &DiffObject,
    parts: &[Vec<NestedObject>],
    last: &TimedObject,
    fallback: Point,
) -> f64 {
    let last_cursor = last_diff.lazy_end_position.unwrap_or(last.pos);
    let mut last_last = fallback;
    if last_diff.is_slider && last_diff.travel_distance > 0.0 {
        let pieces = &parts[last_diff.index];
        if pieces.len() >= 2 {
            last_last = pieces[pieces.len() - 2].pos;
        }
    }
    corner(pos, last_cursor, last_last)
}

/// The turn at `middle`, in radians, always positive.
fn corner(current: Point, middle: Point, before: Point) -> f64 {
    let v1 = Point { x: before.x - middle.x, y: before.y - middle.y };
    let v2 = Point { x: current.x - middle.x, y: current.y - middle.y };
    let dot = v1.x * v2.x + v1.y * v2.y;
    let det = v1.x * v2.y - v1.y * v2.x;
    det.atan2(dot).abs()
}

fn hypot(p: Point) -> f64 {
    p.x.hypot(p.y)
}

fn distance(a: Point, b: Point) -> f64 {
    (a.x - b.x).hypot(a.y - b.y)
}
