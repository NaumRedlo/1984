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
//! 3. **Take best-first while there is anything worth taking** — stopping at
//!    [`Settings::worth`] rather than at a length, because how long a reel
//!    should be is a property of the play. The budget is a ceiling over that,
//!    and there are two more constraints: no overlap, and no two clips from
//!    the same stretch unless nothing else qualifies.
//! 4. **Order by time.** A highlight reel that jumps backwards through the map
//!    is disorienting however good each clip is on its own.
//! 5. **Snap each clip to a beat**, using the timing already carried for the
//!    break arrows. A cut that lands off the beat reads as a mistake even to
//!    someone who could not say why.

use dossier_beatmap::Timing;
use dossier_sim::Timeline;

use crate::scorers::{clip_for, Scorer};
use crate::{Candidate, Clip, Settings, Span};

/// How far a cut may be moved to land on a bar or a beat, as a fraction of one.
///
/// Half, which is as far as it can ever need to go — the nearest one is never
/// further than that. It is written down anyway because half a bar at 60 BPM is
/// two seconds, and a clip that slides two seconds to please the metronome
/// slides the thing it was chosen for out of frame. Snapping is a nicety; the
/// moment is the point.
const SNAP_LIMIT: f64 = 0.5;

/// Beyond this the snap is refused, as a share of the clip being moved.
///
/// Measured against the clip rather than in milliseconds, because the clip is
/// what decides whether a slide costs anything: the moment sits at a fixed
/// place inside it, so moving the window a tenth of its length moves the moment
/// a tenth of the way across the frame, at any tempo, on any map.
///
/// It was 200ms flat, and that number quietly undid the change it was guarding.
/// A bar is two seconds at 120 BPM in four, so a cut is typically most of a
/// second from one — outside 200ms — and every snap fell through to the beat.
/// A tenth of a six-second clip is 600ms, which reaches a bar most of the time
/// and still leaves the moment where it was put.
const SNAP_SHARE: f64 = 0.1;

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
pub(crate) const REPEAT_DECAY: f64 = 0.55;

/// What a *second* look at the same kind of thing is worth.
///
/// Gentler than repeating one scorer, and it exists because counting per scorer
/// was too generous by exactly one axis. Two scorers can measure one thing from
/// two sides and neither has repeated *itself*: `storm` and `travel` did, and
/// the survey found their picks landing within half a minute of each other 59
/// times over 123 reels, both at full price.
///
/// It was applied to the map alone at first, and the moment a second hand-side
/// scorer arrived the same imbalance appeared there — `travel` and `tapping`
/// together came to 43% of every clip drawn. So it applies to every facet: a
/// reel gets a good look at what the map is like, at what the hand is doing and
/// at what became of the run, and each next look has to earn its place against
/// the other two.
const FACET_DECAY: f64 = 0.75;

/// What a clip is worth when it sits close to one already chosen.
///
/// The design says no two clips from the same stretch "unless nothing else
/// qualifies", and this is that sentence: heavy enough that a crowded clip
/// loses to almost anything, light enough that it beats leaving the budget
/// unspent.
const CROWDED: f64 = 0.25;

/// How far apart two clips from one scorer must be before the second stops
/// counting as a repeat of the first, as a multiple of the clip length.
///
/// [`REPEAT_DECAY`] exists to stop a reel saying one thing five times, and it
/// counted every earlier clip of a kind at full strength wherever it sat. On a
/// long play that is the wrong question: a second look at the map's density four
/// minutes after the first is not another look at the same thing, it is a
/// different part of the map. Counted that way, a reel covered 45% of a play
/// under two minutes and 13% of one over six — the same handful of clips
/// whatever there was to choose from.
///
/// Five clips, half a minute at the default. **A stretch of map time and not a
/// share of the play**, which was the first attempt and had it backwards: a
/// share shrinks with the play, so the discount evaporated on exactly the short
/// plays that can least afford five looks at one thing. What "the same thing"
/// means is a claim about how a map is built — a section is half a minute,
/// whether it sits in a marathon or is the whole play.
///
/// The corpus is nearly flat across 3, 5 and 7 clips of reach (26/42/32% by
/// facet either way), so this is chosen for what it says rather than for what it
/// scores: a section is the unit a mapper builds in, and it is the distance at
/// which "the same part of the map" stops being true.
///
/// Applies to [`REPEAT_DECAY`] only. [`FACET_DECAY`] is counted by tally — see
/// the note where the two are applied.
const REPEAT_REACH: f64 = 5.0;

/// The most of a play a reel may be, and the least it may be cut to.
///
/// Two fifths, but never less than [`LEAST_REEL_CLIPS`] clips' worth. The
/// ceiling in [`Settings::budget_ms`] is in seconds and cannot know how much
/// play there is: once a scorer stopped paying full price for a repeat on the
/// far side of the map, a ninety-second play started handing back most of
/// itself, and a highlight that is most of the thing it highlights is not one —
/// at that point somebody may as well watch the replay.
///
/// The proportion binds at the short end and the seconds at the long one, which
/// is the right way round: a short play is cut by how much of it a reel may be,
/// a long one by how much anybody will sit through.
const MOST_OF_A_PLAY: f64 = 0.4;

/// The fewest clips a reel is allowed room for, whatever the proportion says.
///
/// Three. Two fifths of a very short play is one clip, and one clip cannot tell
/// a story: a twenty-three-second play came back as "the hardest movement in the
/// play" alone, having dropped a 252x full combo — the one thing that play had
/// to say. Below about a minute the proportion stops being a sane rule, because
/// there is nothing to skip; a short play is *allowed* to be most of its own
/// reel.
const LEAST_REEL_CLIPS: f64 = 3.0;

/// How far apart two moments must be before one clip can be said to hold both,
/// as a share of a clip.
///
/// A third. Closer than that they are not two moments, they are one moment
/// under two names — `peak` anchors at the end of a combo run and `choke`
/// anchors at the break that ended it, which is the same instant, and merging
/// them produced clips captioned "a 1425x run breaks 63% of the way in" and
/// "the play's longest run, 1425x, ends here" one under the other. Two lines
/// saying one thing is worse than one line, because a reader spends the second
/// one looking for the difference.
const MERGE_APART: f64 = 1.0 / 3.0;

/// The longest a clip holding two moments may run, over what one may.
///
/// One more clip's worth. A merged clip is two moments and gets two moments'
/// room; past that it stops being a moment held longer and becomes a stretch of
/// map, which is a different thing and wants its own clip rather than a longer
/// sentence.
const MERGE_ROOM: f64 = 1.0;

pub(crate) fn choose(
    candidates: Vec<(Scorer, Candidate)>,
    play: (f64, f64),
    timeline: &Timeline,
    settings: Settings,
) -> Vec<Clip> {
    if candidates.is_empty()
        || settings.clip_ms <= 0.0
        || settings.budget_ms < settings.clip_ms
        || play.1 - play.0 < settings.clip_ms
    {
        return Vec::new();
    }

    let ranked = rank(candidates, settings, play);

    // Taken one at a time rather than swept through a sorted list, because what
    // a candidate is worth depends on what has already been taken. Both of the
    // rules below are discounts and not bans, so the budget always fills: the
    // "unless nothing else qualifies" in the design is what a discount does on
    // its own, with nothing to special-case.
    //
    // Spent in seconds rather than counted in clips, because clips are no
    // longer all one length. A budget in clips would have meant a reel of five
    // long ones running half again over what was asked for.
    //
    // And the budget is only the ceiling. What actually ends a reel is running
    // out of moments worth showing — a reel is as long as the play gives it
    // reason to be, which is not something a caller can know in advance.
    let spread_ms = settings.spread * settings.clip_ms;
    // How near two clips have to be for one to count as a repeat of the other.
    // In map time, so a six-minute play earns more looks than a ninety-second
    // one by having more places to look at rather than by a laxer rule.
    let reach = REPEAT_REACH * settings.clip_ms;
    let mut chosen: Vec<Chosen> = Vec::new();
    let mut spent = vec![false; ranked.len()];
    // Two ceilings, and the tighter one wins: what the caller will sit through,
    // and how much of this particular play a reel may be. The floor under the
    // second is what keeps a very short play from being cut to a single clip.
    let mut budget_left = settings.budget_ms.min(
        ((play.1 - play.0) * MOST_OF_A_PLAY).max(LEAST_REEL_CLIPS * settings.clip_ms),
    );
    loop {
        let mut best: Option<Pick> = None;
        for (index, candidate) in ranked.iter().enumerate() {
            if spent[index] || candidate.span.length_ms() > budget_left {
                continue;
            }
            // Overlap was the one hard rule, and it was too hard. Two things
            // land in one place — a jump pattern is the hardest movement in the
            // map *and* where the misses are — and banning the second meant the
            // first clip cut it off part way. So an overlapping candidate is
            // not skipped; it is offered as a *merge* into the clip it lands
            // in, which then stretches over both.
            let merge = match merge_into(&chosen, candidate, settings) {
                Merge::Fresh => None,
                Merge::Into(into, span) => Some((into, span)),
                Merge::No => continue,
            };
            let cost = match merge {
                Some((into, span)) => span.length_ms() - chosen[into].span.length_ms(),
                None => candidate.span.length_ms(),
            };
            if cost > budget_left {
                continue;
            }
            // A merge is not another clip near an existing one, it is the same
            // clip saying one more thing — so the crowding discount, which
            // exists to stop six views of one section, does not apply to it.
            let crowded = merge.is_none()
                && chosen
                    .iter()
                    .any(|already| (already.anchor_ms - candidate.anchor_ms).abs() < spread_ms);
            // A scorer repeating *itself* is counted by nearness, not by tally:
            // an earlier look at the map's density four minutes away is not
            // another look at the same thing, it is a different part of the map,
            // and charging it in full is what kept a six-minute play to the same
            // five clips a ninety-second one got.
            //
            // The facet is counted by tally, and the asymmetry is the point. "Has
            // this reel already had a good look at the hand" is a question about
            // the reel, not about a stretch of map — distance cannot answer it.
            // Measured by nearness it stopped being answered at all: the hand
            // came to 49% of every clip drawn and 3% of reels ended up saying
            // nothing about what became of the run, which is the one thing the
            // survey exists to watch.
            //
            // The edges are exempt from both. A play has one beginning and one
            // ending, so a discount for repetition has nothing to say about
            // them — applied anyway it deleted the opening.
            let (repeats, facet_repeats) = if candidate.scorer.can_repeat() {
                chosen.iter().fold((0.0, 0), |(same, kind), already| {
                    let near = (1.0 - (already.anchor_ms - candidate.anchor_ms).abs() / reach)
                        .clamp(0.0, 1.0);
                    (
                        same + if already.scorer == candidate.scorer { near } else { 0.0 },
                        kind + i32::from(already.scorer.facet() == candidate.scorer.facet()),
                    )
                })
            } else {
                (0.0, 0)
            };
            let effective = candidate.score
                * REPEAT_DECAY.powf(repeats)
                * FACET_DECAY.powi(facet_repeats)
                * if crowded { CROWDED } else { 1.0 };
            if best.is_none_or(|top| effective > top.score) {
                best = Some(Pick {
                    score: effective,
                    index,
                    merge,
                    cost,
                });
            }
        }
        // Nothing left, or nothing left worth the seconds it would cost.
        let Some(Pick {
            score: effective,
            index,
            merge,
            cost,
        }) = best.filter(|pick| pick.score >= settings.worth)
        else {
            break;
        };
        spent[index] = true;
        budget_left -= cost;
        match merge {
            Some((into, span)) => {
                chosen[into].span = span;
                chosen[into].with = Some(ranked[index].reason);
            }
            None => {
                // What it scored *when it was picked*, discounts and all. The
                // base score would have three clips from one scorer all
                // reporting the same number, which explains neither their order
                // nor why the third was taken.
                let mut clip = ranked[index];
                clip.score = effective;
                chosen.push(clip);
            }
        }
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
            with: clip.with,
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
    with: Option<crate::Reason>,
}


/// The best candidate this pass found, and what taking it would mean.
#[derive(Clone, Copy)]
struct Pick {
    /// The effective score, discounts and all.
    score: f64,
    index: usize,
    /// Which chosen clip it joins and what that clip becomes, when it joins one.
    merge: Option<(usize, Span)>,
    /// Seconds it would add to the reel — the whole clip, or only the stretch a
    /// merge costs the clip it joins.
    cost: f64,
}

/// What can be done with a candidate that lands where a clip already is.
enum Merge {
    /// Nothing is in the way — take it as a clip of its own.
    Fresh,
    /// It lands inside this clip, which can stretch to `Span` and hold both.
    Into(usize, Span),
    /// In the way and not mergeable. Skipped, as overlap always used to be.
    No,
}

/// Whether these seconds can hold one more moment.
///
/// Three conditions, and each one is there for a case that went wrong without
/// it. The candidate must overlap exactly **one** chosen clip, because a clip
/// cannot stretch in two directions at once. It must come from a **different
/// scorer**, or the density scorer swallows its own neighbouring windows and a
/// merged clip becomes the long flat stretch the repeat discount exists to
/// prevent. And the clip it joins must not already hold two, because a third
/// moment in one clip is not a moment held longer, it is a stretch of map, and
/// that wants its own clip rather than a longer sentence.
fn merge_into(chosen: &[Chosen], candidate: &Chosen, settings: Settings) -> Merge {
    let mut found = None;
    for (index, already) in chosen.iter().enumerate() {
        if !already.span.overlaps(&candidate.span) {
            continue;
        }
        if found.is_some() {
            return Merge::No;
        }
        found = Some(index);
    }
    let Some(index) = found else {
        return Merge::Fresh;
    };
    let into = &chosen[index];
    if into.with.is_some() || into.scorer == candidate.scorer {
        return Merge::No;
    }
    if (into.anchor_ms - candidate.anchor_ms).abs() < settings.clip_ms * MERGE_APART {
        return Merge::No;
    }
    // The union, so neither moment is cut off by the other's edge — which is
    // the whole complaint this answers.
    let span = Span::new(
        into.span.from_ms.min(candidate.span.from_ms),
        into.span.to_ms.max(candidate.span.to_ms),
    );
    let longest = settings.length_for(1.0) + settings.clip_ms * MERGE_ROOM;
    if span.length_ms() > longest {
        return Merge::No;
    }
    Merge::Into(index, span)
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
            // Importance decides length as well as order. A reel where the
            // map's busiest eight seconds get exactly as long as the break that
            // cost the play says the two matter equally — and length is the one
            // thing a silent reel has to say "this one" with.
            let score = strength * scorer.weight();
            let length = settings.length_for(score).min(play.1 - play.0);
            Some(Chosen {
                span: clip_for(candidate, length, play),
                anchor_ms: candidate.anchor_ms,
                scorer: *scorer,
                score,
                rank: 0,
                reason: candidate.reason,
                with: None,
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
    // A clip sitting against either end of the play is there *because* of that
    // end — the opening and the finale are the play's edges, and a clip clamped
    // to one has already been put where it belongs. Sliding it a hundredth of a
    // second to please the metronome undoes the clamp: the finale stopped
    // 25ms before the last note and no longer showed the play ending.
    if (span.from_ms - play.0).abs() < 1.0 || (span.to_ms - play.1).abs() < 1.0 {
        return span;
    }
    let Some(point) = timing.timing_point_at(span.from_ms) else {
        return span;
    };
    let beat = point.beat_length;
    if !beat.is_finite() || beat <= 0.0 {
        return span;
    }

    // The bar first, the beat as a fallback. Six slices of one song cut
    // together are six entries mid-phrase — a listener hears where a bar
    // begins, not where a beat does, and a cut landing on the third beat of a
    // four sounds like a skip however exactly it lands on that beat. The
    // metronome was already here; only the meter was going unread.
    //
    // Falling back matters as much as trying: a bar is four beats of room to
    // move at 200 BPM and over two seconds at 60, and a clip that slides two
    // seconds to please the metronome slides the thing it was chosen for out of
    // frame. When the bar is out of reach the beat usually is not.
    let bar = beat * f64::from(point.meter.max(1));
    let allowance = span.length_ms() * SNAP_SHARE;
    let snapped = [bar, beat].into_iter().find_map(|unit| {
        let steps = (span.from_ms - point.time_ms) / unit;
        let at = point.time_ms + steps.round() * unit;
        let moved = (at - span.from_ms).abs();
        (moved <= unit * SNAP_LIMIT && moved <= allowance).then_some(at)
    });
    let Some(snapped) = snapped else {
        return span;
    };
    let moved = span.shifted_to(snapped);
    // Snapping must not push a clip off the end of the play; a cut on the beat
    // is not worth a frame of nothing.
    if moved.from_ms < play.0 || moved.to_ms > play.1 {
        span
    } else {
        moved
    }
}
