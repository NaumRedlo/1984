//! The scorers: each one an independent opinion about where to look.
//!
//! Every scorer is a function from a judged play to a list of [`Candidate`]s,
//! and knows nothing about the others. Adding a seventh is adding a function
//! and a line in [`Scorer::WEIGHT`] — not editing a pile of conditions, which
//! is what the hand-rolled version of this was and why it could only ever say
//! "dense".
//!
//! Four of the six read the *replay*: [`Scorer::Peak`], [`Scorer::Choke`],
//! [`Scorer::Precision`], [`Scorer::Scramble`]. Two read only the *map*:
//! [`Scorer::Kiai`], [`Scorer::Storm`]. A reel made of the last two alone is
//! the same reel for everyone who ever played the map, which is precisely the
//! thing this feature exists to stop being.

use dossier_sim::{GameState, Part};

use crate::{Candidate, Reason, Settings, Span};

/// Who proposed a moment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Scorer {
    Kiai,
    Peak,
    Choke,
    Storm,
    Precision,
    Scramble,
    Opening,
    Finale,
    Travel,
}

impl Scorer {
    pub fn name(self) -> &'static str {
        match self {
            Self::Kiai => "kiai",
            Self::Peak => "peak",
            Self::Choke => "choke",
            Self::Storm => "storm",
            Self::Precision => "precision",
            Self::Scramble => "scramble",
            Self::Opening => "opening",
            Self::Finale => "finale",
            Self::Travel => "travel",
        }
    }

    /// How much this scorer's best is worth against another scorer's best.
    ///
    /// **This table is taste, and it is written down so that it can be argued
    /// with.** Ranking within a scorer is measured — a 700x run beats a 200x
    /// run and no opinion is involved. Ranking *between* scorers cannot be
    /// measured at all: there is no sense in which a choke is 1.4 storms. The
    /// alternative to a table like this is not objectivity, it is the same
    /// preference expressed accidentally by whichever scorer happens to produce
    /// larger numbers.
    ///
    /// The order says: what went wrong and what went best beat what the map was
    /// always going to do. `storm` is last because it is the only one that says
    /// nothing about the player, and it is kept because on a clean play where
    /// nothing dramatic happens it is the only one with anything to say.
    pub(crate) fn weight(self) -> f64 {
        match self {
            Self::Choke => 1.00,
            // Just under a choke: how a play *ended* is the one thing every
            // viewer wants to know, and a death or a landed FC is the answer.
            // Below a choke because a play can end unremarkably and a choke
            // never is one.
            Self::Finale => 0.95,
            Self::Peak => 0.90,
            Self::Scramble => 0.80,
            Self::Precision => 0.70,
            // Above the two map-only signals and below everything the player
            // did, which is where it belongs: the notes are the map's, but how
            // far the hand had to move between them is closer to the play than
            // counting them is.
            Self::Travel => 0.65,
            Self::Kiai => 0.60,
            Self::Storm => 0.50,
            // Last, and it earns its place only when the budget outlasts the
            // things worth watching. A reel that opens two minutes in with a
            // combo of nine hundred gives no sense of the play — but an opening
            // is establishing, not telling, and it should lose to anything that
            // tells.
            Self::Opening => 0.45,
        }
    }
}

/// Ask every scorer, and hand the lot to selection unranked.
pub(crate) fn all(state: &GameState, settings: Settings) -> Vec<(Scorer, Candidate)> {
    let mut out = Vec::new();
    for (scorer, found) in [
        (Scorer::Kiai, kiai(state, settings)),
        (Scorer::Peak, peak(state)),
        (Scorer::Choke, choke(state)),
        (Scorer::Storm, storm(state, settings)),
        (Scorer::Precision, precision(state, settings)),
        (Scorer::Scramble, scramble(state, settings)),
        (Scorer::Opening, opening(state, settings)),
        (Scorer::Finale, finale(state)),
        (Scorer::Travel, travel(state, settings)),
    ] {
        out.extend(found.into_iter().map(|c| (scorer, c)));
    }
    // Deterministic before anything downstream sorts it: by scorer, then by
    // where in the map it is. Two runs of the same replay must produce the same
    // list in the same order or none of the promises in the crate docs hold.
    out.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.anchor_ms.total_cmp(&b.1.anchor_ms)));
    out
}

// ── the mapper's own mark ────────────────────────────────────────────────

/// Kiai sections: the cheapest good signal there is.
///
/// It is the only thing in the whole file that knows what the *music* is doing.
/// Everything else here counts notes or clicks, and a song's chorus is not a
/// property of either.
fn kiai(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let timeline = state.timeline();
    let (play_from, play_to) = state.span_ms();
    let mut out = Vec::new();
    for (start, end) in timeline.timing.kiai_spans() {
        // An unterminated kiai runs to infinity by design; the play is what
        // bounds it.
        let from = start.max(play_from);
        let to = end.min(play_to);
        // Half a clip is the floor: a two-second kiai cannot fill a clip and
        // padding it means showing seconds the mapper did not mark.
        if to - from < settings.clip_ms / 2.0 {
            continue;
        }
        let bpm = timeline.timing.bpm_at(start) * state.playback_rate();
        out.push(Candidate {
            anchor_ms: from,
            // A beat of run-in, so the drop lands inside the clip rather than
            // on its first frame.
            bias: 0.12,
            // 1.0 is a section at least two clips long. Kiai is close to a
            // binary signal — the mapper either marked it or did not — so the
            // only thing left to grade is whether there is enough of it to
            // fill a clip and have somewhere to cut.
            strength: ((to - from) / (settings.clip_ms * 2.0)).min(1.0),
            reason: Reason::Kiai {
                bpm,
                length_ms: to - from,
            },
        });
    }
    out
}

// ── what the player did ──────────────────────────────────────────────────

/// How many of a play's longest runs are worth proposing.
///
/// Three, because selection will usually take one — but if the longest run ends
/// in the same place as the best choke, they cancel out on the overlap rule and
/// the second-longest is what is left to show.
const RUNS_PROPOSED: usize = 3;

/// The end of a long combo run — where the play was at its best.
///
/// Anchored so the run *ends* at the clip's last frame. That is the whole
/// difference between this and [`choke`] on the same chain: watching a number
/// climb to 743 and stop is a different thing from watching it fall off, and
/// the second belongs to the other scorer.
fn peak(state: &GameState) -> Vec<Candidate> {
    // 1.0 is an FC: the longest run *is* the map. That anchor is what stops a
    // play whose best run was 12 notes from being awarded a "peak" simply for
    // having a longest run, which every play does.
    let full_combo = f64::from(state.max_possible_combo()).max(1.0);
    let last_object_ms = state
        .timeline()
        .objects
        .last()
        .map_or(0.0, |object| object.end_ms);
    state
        .combo_chains()
        .into_iter()
        .take(RUNS_PROPOSED)
        .filter(|chain| chain.length > 0)
        .map(|chain| Candidate {
            // The run the play finished on has no ending — nothing broke it —
            // so it ends where the map does.
            anchor_ms: if chain.ended_at_ms.is_finite() {
                chain.ended_at_ms
            } else {
                last_object_ms
            },
            bias: 1.0,
            strength: f64::from(chain.length) / full_combo,
            reason: Reason::Peak {
                combo: chain.length,
            },
        })
        .collect()
}

/// A break that ended a long run, weighted by how late it came.
///
/// A break at 96% into a map nobody has FC'd is the most interesting thing in
/// the whole replay, and a break at 4% is a warm-up. Both are the same event to
/// anything that only counts combo, which is why lateness is in the weight and
/// not left to whoever reads the output.
fn choke(state: &GameState) -> Vec<Candidate> {
    let (play_from, play_to) = state.span_ms();
    let played = (play_to - play_from).max(1.0);
    // The same anchor as `peak`, for the same reason and so the two are
    // comparable: a choke is measured by how much of the map the run had
    // already survived.
    let full_combo = f64::from(state.max_possible_combo()).max(1.0);
    state
        .combo_chains()
        .into_iter()
        .filter(|chain| chain.part.is_some() && chain.ended_at_ms.is_finite() && chain.length > 0)
        .take(RUNS_PROPOSED)
        .map(|chain| {
            let through = ((chain.ended_at_ms - play_from) / played).clamp(0.0, 1.0);
            Candidate {
                anchor_ms: chain.ended_at_ms,
                // Two thirds in: a run-up long enough to have something to lose,
                // and a moment of afterwards.
                bias: 0.7,
                // Half weight at the very start, one and a half at the very
                // end. Lateness scales the run rather than being added to it,
                // so a late break of 20 combo still loses to an early one of
                // 700 — it is a tilt, not a veto.
                //
                // 1.0 is therefore a run that had two thirds of the map behind
                // it when it fell, which is about as bad as a choke gets.
                strength: (f64::from(chain.length) / full_combo * (0.5 + through)).min(1.0),
                reason: Reason::Choke {
                    combo: chain.length,
                    through,
                },
            }
        })
        .collect()
}

/// Fewest clicks a window needs before its average error means anything.
///
/// Three clicks at 2ms is luck. This is the one number in the scorer that
/// decides whether it is measuring a hand or a coincidence.
const PRECISION_MIN_CLICKS: usize = 10;

/// A stretch played unusually tightly, judged against the player's own average.
///
/// Against their *own*, deliberately. An absolute threshold would hand every
/// clip to whoever is best at the game and say nothing about the play in front
/// of it; measured this way, a 20ms player having a 12ms minute is a moment and
/// a 6ms player having a 12ms minute is not.
fn precision(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let Some(judge) = state.judge() else {
        return Vec::new();
    };
    let clicks: Vec<(f64, f64)> = judge.errors_ms().map(|(at, err)| (at, err.abs())).collect();
    if clicks.len() < PRECISION_MIN_CLICKS {
        return Vec::new();
    }
    let baseline = clicks.iter().map(|(_, err)| err).sum::<f64>() / clicks.len() as f64;

    let mut windows = Vec::with_capacity(clicks.len());
    let mut end = 0usize;
    let mut sum = 0.0;
    for start in 0..clicks.len() {
        if end < start {
            end = start;
            sum = 0.0;
        }
        while end < clicks.len() && clicks[end].0 < clicks[start].0 + settings.clip_ms {
            sum += clicks[end].1;
            end += 1;
        }
        let count = end - start;
        let strength = if count >= PRECISION_MIN_CLICKS {
            let mean = sum / count as f64;
            // The fraction of their own error they shed here. 1.0 would be a
            // window played perfectly by a player who is otherwise not, which
            // does not happen — so this scorer's real range is the bottom half
            // of its scale, and that is honest: precision is a quiet signal and
            // should lose to a choke.
            //
            // Against their *own* average, deliberately. An absolute threshold
            // would hand every clip to whoever is best at the game.
            ((baseline - mean) / baseline).clamp(0.0, 1.0)
        } else {
            0.0
        };
        windows.push((clicks[start].0, strength, count, sum));
        sum -= clicks[start].1;
    }

    peaks(&windows, |w| w.1)
        .into_iter()
        .map(|i| {
            let (at, strength, count, sum) = windows[i];
            Candidate {
                anchor_ms: at,
                // The window *is* the clip here — it was measured over exactly
                // this length — so it starts where it starts.
                bias: 0.0,
                strength,
                reason: Reason::Precision {
                    clicks: count,
                    mean_error_ms: sum / count as f64,
                    baseline_ms: baseline,
                },
            }
        })
        .collect()
}

/// What one refused click is worth against one miss.
///
/// Less, because a refusal is the game's doing and a miss is the play's — but
/// not nothing: a cascade of refusals is exactly the moment a player wants
/// explained to them, and it is invisible in any count of misses.
const REFUSAL_WEIGHT: f64 = 0.4;

/// Where it went wrong: misses and refused clicks, clustered.
fn scramble(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let Some(judge) = state.judge() else {
        return Vec::new();
    };
    // One entry per thing that went wrong, in time order. Misses are counted
    // on the parts a player *feels* — a circle, a slider's head, a spinner —
    // and not on ticks, which would let one shredded slider outweigh a whole
    // cluster of dropped notes.
    let mut trouble: Vec<(f64, bool)> = judge
        .events()
        .iter()
        .filter(|event| {
            event.result.is_miss()
                && matches!(event.part, Part::Circle | Part::SliderHead | Part::Spinner)
        })
        .map(|event| (event.time_ms, true))
        .chain(judge.shakes().iter().map(|&(_, at)| (at, false)))
        .collect();
    trouble.sort_by(|a, b| a.0.total_cmp(&b.0));
    if trouble.is_empty() {
        return Vec::new();
    }

    // How many objects the window held, so a cluster can be read as a fraction
    // of what was there rather than as a count. Forty misses is a catastrophe
    // in a window of sixty objects and a rough patch in a window of four
    // hundred, and only the ratio can tell those apart.
    let starts: Vec<f64> = state
        .timeline()
        .objects
        .iter()
        .map(|object| object.start_ms)
        .collect();
    let objects_in = |from: f64| -> usize {
        let to = from + settings.clip_ms;
        starts.partition_point(|&at| at < to) - starts.partition_point(|&at| at < from)
    };

    let mut windows = Vec::with_capacity(trouble.len());
    let mut end = 0usize;
    let (mut misses, mut refused) = (0usize, 0usize);
    for start in 0..trouble.len() {
        if end < start {
            end = start;
            misses = 0;
            refused = 0;
        }
        while end < trouble.len() && trouble[end].0 < trouble[start].0 + settings.clip_ms {
            if trouble[end].1 {
                misses += 1;
            } else {
                refused += 1;
            }
            end += 1;
        }
        // 1.0 is a window where everything went wrong. A refused click counts
        // for less than a miss but is not free — a cascade of refusals is
        // exactly the moment a player wants explained, and it is invisible in
        // any count of misses.
        let trouble_here = misses as f64 + refused as f64 * REFUSAL_WEIGHT;
        let strength = (trouble_here / objects_in(trouble[start].0).max(1) as f64).min(1.0);
        windows.push((trouble[start].0, strength, misses, refused));
        if trouble[start].1 {
            misses -= 1;
        } else {
            refused -= 1;
        }
    }

    peaks(&windows, |w| w.1)
        .into_iter()
        .map(|i| {
            let (at, strength, misses, refused) = windows[i];
            Candidate {
                anchor_ms: at,
                // Centred: the cluster's first event opens it, and what a
                // scramble needs is the pattern around it, not before it.
                bias: 0.25,
                strength,
                reason: Reason::Scramble { misses, refused },
            }
        })
        .collect()
}

// ── what the map does ────────────────────────────────────────────────────

/// What a slider is worth against a circle when counting density.
///
/// More, because it is continuous work — the hand does not get the gap a circle
/// leaves behind it. Not double: a long slider is often the map's rest.
const SLIDER_DENSITY: f64 = 1.4;

/// A spinner is the opposite of dense however long it is, and a window that
/// counted it like a note would put the map's one quiet moment in the reel.
const SPINNER_DENSITY: f64 = 0.2;

/// Local object density — the hand-rolled version of this whole feature.
///
/// Kept because it is right about one thing: on a clean play where nothing
/// dramatic happens, the densest stretch is genuinely the most watchable, and
/// every other scorer here has nothing to say. Kept *last* in the weight table
/// because it is a property of the map, so it picks the same seconds no matter
/// who played it or how — which is the limitation that made the rest necessary.
/// Every window of the map by how much is in it, graded against the busiest.
///
/// Returned as `(starts_at, share_of_densest, objects)`. Shared with
/// [`opening`], which asks the same question of one particular window: the
/// alternative is two ideas of what "dense" means, and the one that would drift
/// is the one nobody looks at.
///
/// Graded against the map's own busiest window, which is the one place in this
/// crate a relative measure is right rather than a shortcut: "dense" means
/// nothing on its own. Eleven notes a second is a wall on one map and the calm
/// before the drop on another.
fn density_curve(state: &GameState, settings: Settings) -> Vec<(f64, f64, usize)> {
    let objects = &state.timeline().objects;
    if objects.is_empty() {
        return Vec::new();
    }
    let density = |object: &dossier_sim::TimedObject| {
        if object.is_spinner() {
            SPINNER_DENSITY
        } else if matches!(object.kind, dossier_sim::TimedKind::Slider { .. }) {
            SLIDER_DENSITY
        } else {
            1.0
        }
    };

    let mut windows = Vec::with_capacity(objects.len());
    let mut end = 0usize;
    let mut sum = 0.0;
    for start in 0..objects.len() {
        if end < start {
            end = start;
            sum = 0.0;
        }
        while end < objects.len()
            && objects[end].start_ms < objects[start].start_ms + settings.clip_ms
        {
            sum += density(&objects[end]);
            end += 1;
        }
        windows.push((objects[start].start_ms, sum, end - start));
        sum -= density(&objects[start]);
    }

    let densest = windows.iter().map(|w| w.1).fold(0.0f64, f64::max).max(1.0);
    for window in &mut windows {
        window.1 /= densest;
    }
    windows
}

fn storm(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let windows = density_curve(state, settings);
    if windows.is_empty() {
        return Vec::new();
    }

    peaks(&windows, |w| w.1)
        .into_iter()
        .map(|i| {
            let (at, strength, count) = windows[i];
            Candidate {
                anchor_ms: at,
                bias: 0.0,
                strength,
                reason: Reason::Storm {
                    objects: count,
                    of_densest: strength,
                },
            }
        })
        .collect()
}


// ── the edges of the play ────────────────────────────────────────────────

/// How the play opens.
///
/// A reel that begins two minutes in, at a combo of nine hundred, gives no
/// sense of the play it is about — the viewer joins a run already in progress
/// and has nothing to measure it against. The opening is where a play is
/// established.
///
/// Graded on what the map gives it to establish, on the same density scale
/// `storm` uses: a map that opens on its hardest section deserves the seconds,
/// one that opens with four notes over ten seconds does not. So this proposes
/// on every play and wins on few, which is what it is for — it fills a budget
/// that outlasts the things worth watching, and loses to all of them.
fn opening(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let (play_from, play_to) = state.span_ms();
    if play_to - play_from < settings.clip_ms {
        return Vec::new();
    }
    let curve = density_curve(state, settings);
    // The first window that starts inside the play, which is the first window
    // there is — the play begins a lead-in before the first object.
    let Some(&(_, share, objects)) = curve.first() else {
        return Vec::new();
    };
    vec![Candidate {
        anchor_ms: play_from,
        bias: 0.0,
        strength: share,
        reason: Reason::Opening { objects },
    }]
}

/// How the play ends, which is the one thing every viewer wants to know.
///
/// Two different endings share this scorer because they answer the same
/// question. A play that *died* ends at the moment the bar empties, and that
/// moment is the whole story of the run. A play that finished ends on its
/// result, and a result is worth watching land in proportion to how good it is:
/// a 99.4% arriving is a payoff, and a 68% is the map running out.
///
/// The one place a scorer reads the score rather than the play, and it is the
/// right place: "how did it end" is a question about the score.
fn finale(state: &GameState) -> Vec<Candidate> {
    let (play_from, play_to) = state.span_ms();
    let Some(judge) = state.judge() else {
        return Vec::new();
    };
    let failed = state.ending().is_some();
    let final_state = match state.ending() {
        Some(end) => end.score,
        None => judge.final_state(),
    };
    let accuracy = final_state.accuracy();
    let full_combo = final_state.max_combo >= state.max_possible_combo()
        && state.max_possible_combo() > 0;

    // A death is always the story, and a full combo is worth watching land
    // whatever the accuracy — an FC is an FC. Everything else is graded on the
    // result, with ninety percent as the floor: below it, a finish is the map
    // running out rather than a payoff, and gets no clip at all.
    let strength = if failed || full_combo {
        1.0
    } else {
        ((accuracy - 90.0) / 10.0).clamp(0.0, 1.0)
    };
    if !strength.is_finite() || strength <= 0.0 || play_to <= play_from {
        return Vec::new();
    }
    vec![Candidate {
        anchor_ms: play_to,
        // The end at the last frame: everything before it is the run-up to it.
        bias: 1.0,
        strength,
        reason: Reason::Finale {
            failed,
            accuracy,
            combo: final_state.max_combo,
            full_combo,
        },
    }]
}

// ── what the hand had to do ──────────────────────────────────────────────

/// A spinner's cursor travel is enormous and says nothing.
///
/// Two hundred revolutions of a circle is more distance than any jump pattern
/// in the map, so without this the scorer is a spinner detector — it found the
/// one place in a play where the hand is doing the easiest thing it ever does
/// and called it the hardest movement in the play.
fn outside_spinners(state: &GameState) -> Vec<(f64, f64)> {
    state
        .timeline()
        .objects
        .iter()
        .filter(|object| object.is_spinner())
        .map(|object| (object.start_ms, object.end_ms))
        .collect()
}

/// How far the cursor had to move: the distance between the notes rather than
/// the number of them.
///
/// The one signal here that `storm` cannot reach. A jump map is sparse — a
/// handful of objects a second — and every one of them is across the playfield;
/// counting objects calls that a quiet stretch and it is the hardest thing in
/// the map to play. This counts the distance the hand actually covered.
///
/// Read off the replay's own frames rather than off the object positions, so it
/// is what the player *did* and not what the map asked for: someone who plays a
/// pattern with wide loops and someone who plays it economically get different
/// numbers, and the numbers are right both times.
fn travel(state: &GameState, settings: Settings) -> Vec<Candidate> {
    let frames = state.cursor_track().frames();
    if frames.len() < 2 {
        return Vec::new();
    }
    let spinners = outside_spinners(state);
    let in_spinner =
        |at: f64| spinners.iter().any(|&(from, to)| at >= from && at <= to);

    // Distance covered between each frame and the one before it, dropped where
    // a spinner would drown it.
    let steps: Vec<(f64, f64)> = frames
        .windows(2)
        .filter_map(|pair| {
            let (a, b) = (&pair[0], &pair[1]);
            let at = b.time_ms as f64;
            if in_spinner(at) {
                return None;
            }
            let (dx, dy) = (f64::from(b.x - a.x), f64::from(b.y - a.y));
            Some((at, (dx * dx + dy * dy).sqrt()))
        })
        .collect();
    if steps.is_empty() {
        return Vec::new();
    }

    let mut windows = Vec::with_capacity(steps.len());
    let mut end = 0usize;
    let mut sum = 0.0;
    for start in 0..steps.len() {
        if end < start {
            end = start;
            sum = 0.0;
        }
        while end < steps.len() && steps[end].0 < steps[start].0 + settings.clip_ms {
            sum += steps[end].1;
            end += 1;
        }
        windows.push((steps[start].0, sum, 0usize));
        sum -= steps[start].1;
    }

    // Against the play's own busiest movement, for the same reason `storm` is
    // graded against the map's own busiest: osu!pixels a second means nothing
    // without a map to mean it in. Circle size, mods and the mapper's spacing
    // all move the scale.
    let fastest = windows.iter().map(|w| w.1).fold(0.0f64, f64::max);
    if !fastest.is_finite() || fastest <= 0.0 {
        return Vec::new();
    }

    peaks(&windows, |w| w.1)
        .into_iter()
        .map(|i| {
            let (at, distance, _) = windows[i];
            Candidate {
                anchor_ms: at,
                bias: 0.0,
                strength: distance / fastest,
                reason: Reason::Travel {
                    speed: distance / (settings.clip_ms / 1000.0),
                    of_fastest: distance / fastest,
                },
            }
        })
        .collect()
}

// ── shared ───────────────────────────────────────────────────────────────

/// Thin a window-per-event scan down to its local maxima.
///
/// A sliding window produces one candidate per event, and a thousand candidates
/// describing the same six seconds are one candidate with noise around it. This
/// keeps the rising edge of each plateau, so a flat stretch contributes once
/// rather than once per note — and it is a rule rather than a threshold, so it
/// cannot be tuned into hiding a real hotspot.
fn peaks<T>(windows: &[T], weight: impl Fn(&T) -> f64) -> Vec<usize> {
    (0..windows.len())
        .filter(|&i| {
            let here = weight(&windows[i]);
            here > 0.0
                && (i == 0 || here > weight(&windows[i - 1]))
                && (i + 1 == windows.len() || here >= weight(&windows[i + 1]))
        })
        .collect()
}

/// Turn a candidate into the clip it is asking for, clamped to the play.
///
/// Clamping moves the window rather than shortening it: a clip that runs off
/// the end of the map and comes back four seconds long makes the reel stutter,
/// and there is always somewhere to slide it to.
pub(crate) fn clip_for(candidate: &Candidate, clip_ms: f64, play: (f64, f64)) -> Span {
    let bias = candidate.bias.clamp(0.0, 1.0);
    let span = Span::new(
        candidate.anchor_ms - bias * clip_ms,
        candidate.anchor_ms + (1.0 - bias) * clip_ms,
    );
    if span.from_ms < play.0 {
        span.shifted_to(play.0)
    } else if span.to_ms > play.1 {
        span.shifted_to((play.1 - clip_ms).max(play.0))
    } else {
        span
    }
}
