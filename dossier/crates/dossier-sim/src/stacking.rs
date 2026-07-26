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

/// Work out how high each object sits in its stack, then move them.
///
/// Maps written before format version 6 used a different, buggier algorithm.
/// They aren't handled yet; on such a map the stacks come out flat, which is
/// wrong but visibly and uniformly wrong rather than subtly so.
pub(crate) fn apply(objects: &mut [TimedObject], difficulty: &Difficulty, stack_leniency: f64) {
    if objects.len() < 2 {
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
        if height == 0 {
            continue;
        }
        let shift = f64::from(height) * step;
        object.translate(shift, shift);
    }
}

/// Where an object leaves the player: a slider's last slide ends where the ball
/// stops, everything else ends where it started.
fn end_position(object: &TimedObject) -> Point {
    object.ball_at(object.end_ms).unwrap_or(object.pos)
}
