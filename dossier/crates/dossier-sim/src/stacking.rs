//! Stacking — the reason overlapping notes are drawn as a staircase.
//!
//! When objects sit on top of each other within a short window, osu! nudges
//! each one up and to the left of the one after it so the player can see the
//! pile. This is not a cosmetic pass: the shifted position is what the game
//! tests clicks against. Judging against the authored coordinates makes every
//! stacked note look like a near-miss by a few pixels, which is exactly the
//! kind of error that reads as "the player is sloppy" instead of "the
//! simulator is wrong".
//!
//! The algorithm below is osu!'s own, kept in its original shape — a backwards
//! sweep that walks each object's predecessors and raises them a step at a
//! time. It is fiddly and order-dependent, and rewriting it into something
//! tidier is how you end up with subtly different stacks.

use dossier_beatmap::{Difficulty, Point};

use crate::timeline::TimedObject;

/// Objects closer than this are considered stacked, in osu!pixels.
const STACK_DISTANCE: f64 = 3.0;

/// Each step of a stack moves the object by a tenth of a circle radius, up and
/// to the left. osu! writes it as `stack_height * scale * -6.4` with
/// `scale = radius / 64`.
const STACK_SHIFT_PER_STEP: f64 = -6.4 / 64.0;

/// The file format version at which osu! switched stacking algorithms.
///
/// ```csharp
/// if (beatmap.BeatmapVersion >= 6)
///     applyStacking(beatmap, hitObjects, 0, hitObjects.Count - 1);
/// else
///     applyStackingOld(beatmap, hitObjects);
/// ```
const FIRST_MODERN_STACKING_VERSION: u32 = 6;

/// Work out how high each object sits in its stack, then move them.
///
/// Maps before format version 6 stack by a different, older algorithm, and
/// running the modern sweep on them is worse than running nothing: on
/// `Kona-Chan: Farucon Pan!`, format v4, it piles one slider eight steps high
/// and moves the ball out from under a player who tracked it perfectly. The
/// shifted position is what clicks are tested against, so the wrong one reads
/// as somebody who cannot aim.
///
/// So old maps are left flat for now. That is not what the game does either —
/// `applyStackingOld` is a real algorithm and it is not this one — but it is
/// the better of the two answers available: measured over the corpus, leaving
/// them alone costs 465 against 526 for stacking them wrongly. A port of the
/// old sweep was written and withdrawn: it scored 515, worse than doing
/// nothing, so it was wrong somewhere and shipping it would only have hidden
/// that behind an improvement elsewhere.
pub(crate) fn apply(
    objects: &mut [TimedObject],
    difficulty: &Difficulty,
    stack_leniency: f64,
    format_version: u32,
) {
    if objects.len() < 2 {
        return;
    }
    if format_version < FIRST_MODERN_STACKING_VERSION {
        apply_old(objects, difficulty, stack_leniency);
        return;
    }

    let threshold = difficulty.preempt_ms() * stack_leniency;

    // Positions are read from these snapshots, never from `objects`, so that
    // raising one stack can't disturb the distances another is measured with.
    let starts: Vec<Point> = objects.iter().map(|o| o.pos).collect();
    let ends: Vec<Point> = objects.iter().map(end_position).collect();

    let mut heights = vec![0i32; objects.len()];

    for i in (1..objects.len()).rev() {
        if heights[i] != 0 || objects[i].is_spinner() {
            continue;
        }

        // `current` walks backwards through the stack as it grows; `i` stays
        // put as the object whose stack we're building.
        let mut current = i;
        let mut n = i as i64;

        if objects[i].is_slider() {
            // A slider only stacks onto things that end where it begins.
            loop {
                n -= 1;
                if n < 0 {
                    break;
                }
                let n = n as usize;
                if objects[n].is_spinner() {
                    continue;
                }
                if objects[current].start_ms - objects[n].start_ms > threshold {
                    break;
                }
                if ends[n].distance_to(starts[current]) < STACK_DISTANCE {
                    heights[n] = heights[current] + 1;
                    current = n;
                }
            }
        } else {
            loop {
                n -= 1;
                if n < 0 {
                    break;
                }
                let n = n as usize;
                if objects[n].is_spinner() {
                    continue;
                }
                if objects[current].start_ms - objects[n].end_ms > threshold {
                    break;
                }

                // A circle landing on a slider's tail pushes the whole run the
                // other way: the slider stays put and everything stacked on it
                // drops down to meet its end.
                if objects[n].is_slider() && ends[n].distance_to(starts[current]) < STACK_DISTANCE {
                    let offset = heights[current] - heights[n] + 1;
                    for j in (n + 1)..=i {
                        if ends[n].distance_to(starts[j]) < STACK_DISTANCE {
                            heights[j] -= offset;
                        }
                    }
                    break;
                }

                if starts[n].distance_to(starts[current]) < STACK_DISTANCE {
                    heights[n] = heights[current] + 1;
                    current = n;
                }
            }
        }
    }

    let step = difficulty.circle_radius() * STACK_SHIFT_PER_STEP;
    for (object, height) in objects.iter_mut().zip(heights) {
        object.stack_height = height;
        if height == 0 {
            continue;
        }
        let shift = f64::from(height) * step;
        object.translate(shift, shift);
    }
}

/// Stacking as maps before format version 6 had it.
///
/// Inside out from the modern sweep: it walks *forwards*, and each object
/// raises **itself** by counting the later objects that land on it, rather
/// than reaching back to lift its predecessors. An object landing on a
/// slider's end is pushed the other way, down and right, by a running count —
/// which is where old maps' negative heights come from.
///
/// ```csharp
/// for (int i = 0; i < hitObjects.Count; i++)
/// {
///     OsuHitObject currHitObject = hitObjects[i];
///     if (currHitObject.StackHeight != 0 && !(currHitObject is Slider)) continue;
///     double startTime = currHitObject.GetEndTime();
///     int sliderStack = 0;
///     for (int j = i + 1; j < hitObjects.Count; j++)
///     {
///         float stackThreshold = calculateStackThreshold(beatmap, hitObjects[i]);
///         if (hitObjects[j].StartTime - stackThreshold > startTime) break;
///         Vector2 position2 = currHitObject is Slider currSlider
///             ? currSlider.Position + currSlider.Path.PositionAt(1)
///             : currHitObject.Position;
///         if (Distance(hitObjects[j].Position, currHitObject.Position) < STACK_DISTANCE)
///         {
///             currHitObject.StackHeight++;
///             startTime = hitObjects[j].StartTime;
///         }
///         else if (Distance(hitObjects[j].Position, position2) < STACK_DISTANCE)
///         {
///             sliderStack++;
///             hitObjects[j].StackHeight -= sliderStack;
///             startTime = hitObjects[j].StartTime;
///         }
///     }
/// }
/// ```
///
/// Two details cost two rewrites of this function before the source was read
/// closely enough. `startTime` advances to the next object's **start**, not
/// its end — the window creeps along the pile object by object. And the
/// slider's comparison point is `Path.PositionAt(1)`, the end of the drawn
/// curve, *not* where the ball finishes: on an even number of slides the ball
/// comes home to the start, and using that stacks the wrong things.
fn apply_old(objects: &mut [TimedObject], difficulty: &Difficulty, stack_leniency: f64) {
    let threshold = difficulty.preempt_ms() * stack_leniency;
    let starts: Vec<Point> = objects.iter().map(|o| o.pos).collect();
    let path_ends: Vec<Point> = objects.iter().map(path_end).collect();
    let mut heights = vec![0i32; objects.len()];

    for i in 0..objects.len() {
        if heights[i] != 0 && !objects[i].is_slider() {
            continue;
        }
        let mut start_time = objects[i].end_ms;
        let mut slider_stack = 0i32;

        for j in (i + 1)..objects.len() {
            if objects[j].start_ms - threshold > start_time {
                break;
            }
            if starts[j].distance_to(starts[i]) < STACK_DISTANCE {
                heights[i] += 1;
                start_time = objects[j].start_ms;
            } else if starts[j].distance_to(path_ends[i]) < STACK_DISTANCE {
                slider_stack += 1;
                heights[j] -= slider_stack;
                start_time = objects[j].start_ms;
            }
        }
    }

    let step = difficulty.circle_radius() * STACK_SHIFT_PER_STEP;
    for (object, height) in objects.iter_mut().zip(heights) {
        object.stack_height = height;
        if height == 0 {
            continue;
        }
        let shift = f64::from(height) * step;
        object.translate(shift, shift);
    }
}

/// The end of a slider's drawn curve — `Path.PositionAt(1)` — regardless of
/// how many times the ball crosses it. Not the same as where the ball stops.
fn path_end(object: &TimedObject) -> Point {
    match &object.kind {
        crate::timeline::TimedKind::Slider { path, .. } => {
            path.position_at(1.0).unwrap_or(object.pos)
        }
        _ => object.pos,
    }
}

/// Where an object leaves the player: a slider's last slide ends where the ball
/// stops, everything else ends where it started.
fn end_position(object: &TimedObject) -> Point {
    object.ball_at(object.end_ms).unwrap_or(object.pos)
}
