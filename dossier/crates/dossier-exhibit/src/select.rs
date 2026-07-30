//! Scorers propose; selection disposes.
//!
//! Five steps, in this order, and the order is the design:
//!
//! 1. **Every scorer answers in the same units** — [`Candidate::strength`],
//!    from 0 to 1, meaning how much of what that scorer can detect is present.
//!    A scorer must not be able to win by using a bigger unit: combo runs into
//!    the hundreds and milliseconds saved into the tens, and left raw the whole
//!    ranking would be an accident of what each signal happens to be measured
//!    in. Each scorer states what its 1.0 means; see [`crate::Candidate`] for
//!    why this is absolute rather than relative to a scorer's own best.
//! 2. **Weight between scorers.** The one place taste is applied, and it is a
//!    table in [`Scorer::weight`] rather than a thumb on any scorer's scale.
//! 3. **Take best-first under three constraints** — budget, no overlap, and no
//!    two clips from the same stretch unless nothing else qualifies.
//! 4. **Order by time.** A highlight reel that jumps backwards through the map
//!    is disorienting however good each clip is on its own.
//! 5. **Snap each clip to a beat**, using the timing already carried for the
//!    break arrows. A cut that lands off the beat reads as a mistake even to
//!    someone who could not say why.

use dossier_beatmap::Timing;
use dossier_sim::Timeline;

use crate::scorers::{clip_for, Scorer};
use crate::{Candidate, Clip, Settings, Span};

/// How far a cut may be moved to land on a beat, as a fraction of one beat.
///
/// Half, which is as far as it can ever need to go — the nearest beat is never
/// further than that. It is written down anyway because at 200 BPM half a beat
/// is 150ms and at 60 BPM it is half a second, and a clip that slides half a
/// second to please the metronome can slide the thing it was chosen for out of
/// frame. Snapping is a nicety; the moment is the point.
const SNAP_LIMIT: f64 = 0.5;

/// Beyond this the snap is refused outright, in milliseconds.
const SNAP_CEILING_MS: f64 = 200.0;

/// What a scorer's next clip is worth once it has already won one.
///
/// A reel is a description of a play, and a description that says the same
/// thing five times is a worse description than one that says five things. On a
/// map of uniform streams the density scorer produces dozens of windows all
/// within a hair of each other, and without this it simply filled the reel —
/// three clips of "the densest stretch", each of a different stretch, each
/// telling the viewer nothing the first had not.
///
/// A discount rather than a limit, so a scorer that really is the whole story
/// can still take a second clip: two chokes in a play that broke twice is the
/// right reel.
const REPEAT_DECAY: f64 = 0.55;

/// What a clip is worth when it sits close to one already chosen.
///
/// The design says no two clips from the same stretch "unless nothing else
/// qualifies", and this is that sentence: heavy enough that a crowded clip
/// loses to almost anything, light enough that it beats leaving the budget
/// unspent.
const CROWDED: f64 = 0.25;

pub(crate) fn choose(
    candidates: Vec<(Scorer, Candidate)>,
    play: (f64, f64),
    timeline: &Timeline,
    settings: Settings,
) -> Vec<Clip> {
    let wanted = settings.clips_wanted();
    if wanted == 0 || candidates.is_empty() || play.1 - play.0 < settings.clip_ms {
        return Vec::new();
    }

    let ranked = rank(candidates, settings, play);

    // Taken one at a time rather than swept through a sorted list, because what
    // a candidate is worth depends on what has already been taken. Both of the
    // rules below are discounts and not bans, so the budget always fills: the
    // "unless nothing else qualifies" in the design is what a discount does on
    // its own, with nothing to special-case.
    let spread_ms = settings.spread * settings.clip_ms;
    let mut chosen: Vec<Chosen> = Vec::new();
    let mut taken = std::collections::BTreeMap::<Scorer, u32>::new();
    let mut spent = vec![false; ranked.len()];
    while chosen.len() < wanted {
        let mut best: Option<(f64, usize)> = None;
        for (index, candidate) in ranked.iter().enumerate() {
            if spent[index] {
                continue;
            }
            // Overlap is the one hard rule. It is structural rather than
            // editorial: two clips over the same seconds are the same seconds
            // twice, whatever they were chosen for.
            if chosen.iter().any(|already| already.span.overlaps(&candidate.span)) {
                continue;
            }
            let crowded = chosen
                .iter()
                .any(|already| (already.anchor_ms - candidate.anchor_ms).abs() < spread_ms);
            let effective = candidate.score
                * REPEAT_DECAY.powi(taken.get(&candidate.scorer).copied().unwrap_or(0) as i32)
                * if crowded { CROWDED } else { 1.0 };
            if best.is_none_or(|(top, _)| effective > top) {
                best = Some((effective, index));
            }
        }
        let Some((_, index)) = best else { break };
        spent[index] = true;
        *taken.entry(ranked[index].scorer).or_insert(0) += 1;
        chosen.push(ranked[index]);
    }

    // Rank is where it came in the choosing; time is what it is returned in.
    // Both matter and they are not the same order, which is why `rank` is a
    // field rather than the position in this vector.
    for (rank, clip) in chosen.iter_mut().enumerate() {
        clip.rank = rank;
    }
    chosen.sort_by(|a, b| a.span.from_ms.total_cmp(&b.span.from_ms));

    chosen
        .into_iter()
        .map(|clip| Clip {
            span: snap(clip.span, &timeline.timing, play),
            reason: clip.reason,
            rank: clip.rank,
            score: clip.score,
        })
        .collect()
}

/// A candidate that has been turned into a span and given a comparable score.
#[derive(Debug, Clone, Copy)]
struct Chosen {
    span: Span,
    anchor_ms: f64,
    scorer: Scorer,
    score: f64,
    rank: usize,
    reason: crate::Reason,
}

/// Weight between scorers, best first.
fn rank(candidates: Vec<(Scorer, Candidate)>, settings: Settings, play: (f64, f64)) -> Vec<Chosen> {
    let mut ranked: Vec<Chosen> = candidates
        .iter()
        .filter_map(|(scorer, candidate)| {
            // `is_finite` and not just `<= 0.0`: a scorer dividing by a count
            // it believed non-zero produces NaN, and NaN compares false against
            // everything — so it would sail through a bare `<= 0.0` and then
            // poison every sort it touched.
            let strength = candidate.strength.clamp(0.0, 1.0);
            if !strength.is_finite() || strength <= 0.0 {
                return None;
            }
            Some(Chosen {
                span: clip_for(candidate, settings.clip_ms, play),
                anchor_ms: candidate.anchor_ms,
                scorer: *scorer,
                score: strength * scorer.weight(),
                rank: 0,
                reason: candidate.reason,
            })
        })
        .collect();

    // Ties broken by time, so the order does not depend on the order the
    // scorers were asked in. Nothing here may depend on that: it is the whole
    // of "the same replay gives the same clips, always".
    ranked.sort_by(|a, b| {
        b.score
            .total_cmp(&a.score)
            .then(a.anchor_ms.total_cmp(&b.anchor_ms))
    });
    ranked
}

/// Move a cut onto the nearest beat, if the nearest beat is near enough.
///
/// The whole clip moves — snapping the start and leaving the end would make
/// clips of uneven length out of a setting that says how long a clip is.
fn snap(span: Span, timing: &Timing, play: (f64, f64)) -> Span {
    let Some(point) = timing.timing_point_at(span.from_ms) else {
        return span;
    };
    let beat = point.beat_length;
    if !beat.is_finite() || beat <= 0.0 {
        return span;
    }
    let beats = (span.from_ms - point.time_ms) / beat;
    let snapped = point.time_ms + beats.round() * beat;
    let moved = (snapped - span.from_ms).abs();
    if moved > beat * SNAP_LIMIT || moved > SNAP_CEILING_MS {
        return span;
    }
    let moved = span.shifted_to(snapped);
    // Snapping must not push a clip off the end of the play; a cut on the beat
    // is not worth a frame of nothing.
    if moved.from_ms < play.0 || moved.to_ms > play.1 {
        span
    } else {
        moved
    }
}
