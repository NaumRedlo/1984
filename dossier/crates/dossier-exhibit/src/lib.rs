//! Exhibit — the telling moments of a play.
//!
//! Given a judged replay, choose the few seconds that actually say something
//! about it, and say why each was chosen. `docs/exhibit.md` is the design; this
//! is the part of it that decides *what*, with the video left to the caller.
//!
//! # Why this is a crate of its own
//!
//! Everything in [`dossier_sim`] can be checked. osu! wrote the score into the
//! replay header, so a judgement is either right or wrong and the corpus says
//! which. **Nothing here can be checked that way.** There is no header naming
//! the six seconds worth watching, and there never will be.
//!
//! Keeping that under its own roof is the point. A file in `dossier-sim` is
//! held to the corpus; a file here is held to the weaker promise below, and the
//! two should not be able to be confused for one another by someone reading the
//! tree.
//!
//! # The promise this crate does make
//!
//! - **Every clip carries its reason.** A moment that cannot say why it was
//!   chosen is not chosen. [`Reason`] ships in the output, not in a comment.
//! - **Selection is deterministic.** The same replay gives the same clips,
//!   always — which is what makes a disagreement about taste arguable at all.
//! - **It is inspectable without rendering.** [`choose`] returns the spans and
//!   reasons; drawing them is a separate, later, much slower step.
//! - **Tests pin behaviour, not taste.** "A choke is chosen over a quiet
//!   stretch" is testable. "This is the best clip" is not, and no test here
//!   claims it.
//!
//! ```no_run
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! # let map = dossier_beatmap::Beatmap::parse("osu file format v14")?;
//! # let replay = dossier_replay::Replay::parse(&[])?;
//! let state = dossier_sim::GameState::new(&map, &replay);
//! for clip in dossier_exhibit::choose(&state, dossier_exhibit::Settings::default()) {
//!     println!("{:.0}..{:.0} — {}", clip.span.from_ms, clip.span.to_ms, clip.reason.describe());
//! }
//! # Ok(())
//! # }
//! ```

mod scorers;
mod select;

pub use scorers::Scorer;

use dossier_sim::GameState;

/// A stretch of map time.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Span {
    pub from_ms: f64,
    pub to_ms: f64,
}

impl Span {
    pub fn new(from_ms: f64, to_ms: f64) -> Self {
        Self { from_ms, to_ms }
    }

    pub fn length_ms(&self) -> f64 {
        self.to_ms - self.from_ms
    }

    pub fn centre_ms(&self) -> f64 {
        (self.from_ms + self.to_ms) / 2.0
    }

    /// Whether the two share any time at all. Touching at an edge does not
    /// count: two clips cut end-to-end are two clips, not one overlap.
    pub fn overlaps(&self, other: &Span) -> bool {
        self.from_ms < other.to_ms && other.from_ms < self.to_ms
    }

    /// Move the whole span so it starts at `from_ms`, keeping its length.
    fn shifted_to(&self, from_ms: f64) -> Self {
        Self::new(from_ms, from_ms + self.length_ms())
    }
}

/// Why a moment was chosen, with the numbers that chose it.
///
/// The numbers are carried rather than formatted away because the reason is the
/// output: "a 743 combo ended here, 96% into the map" is a claim somebody can
/// check against the play, and "choke" is not.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Reason {
    /// The mapper's own mark for where the song peaks.
    Kiai { bpm: f64, length_ms: f64 },
    /// The end of a long combo run — where the play was at its best.
    Peak { combo: u32 },
    /// A combo break that ended a long run, weighted by how late it came.
    Choke {
        combo: u32,
        /// How far into the play it happened, 0 to 1.
        through: f64,
    },
    /// Local object density. A property of the map, not of the play.
    Storm {
        objects: usize,
        /// How this window compares to the map's own busiest, 0 to 1.
        ///
        /// Carried because a reel can hold more than one of these and only the
        /// first is "the densest stretch" — saying so of the second and third
        /// as well is a claim that is simply false, and the number that makes
        /// it false was already computed to rank them.
        of_densest: f64,
    },
    /// A run of clicks with unusually low timing error.
    Precision {
        clicks: usize,
        mean_error_ms: f64,
        /// The player's own average over the whole play, for comparison. A
        /// tight window means nothing without the hand it belongs to.
        baseline_ms: f64,
    },
    /// A cluster of misses and refused clicks.
    Scramble { misses: usize, refused: usize },
    /// The play beginning. Establishing, and graded on whether the map gives
    /// the opening anything to establish.
    Opening { objects: usize },
    /// The play ending, and how it ended.
    Finale {
        /// The play stopped here because the health bar emptied.
        failed: bool,
        accuracy: f64,
        combo: u32,
        /// Whether the combo survived the whole map.
        full_combo: bool,
    },
    /// How far the cursor had to move — the distance between the notes rather
    /// than the number of them.
    Travel {
        /// osu!pixels a second, averaged over the window.
        speed: f64,
        /// Against the play's own busiest movement, 0 to 1.
        of_fastest: f64,
    },
}

impl Reason {
    /// Which scorer proposed this, as it appears in the output.
    pub fn scorer(&self) -> Scorer {
        match self {
            Self::Kiai { .. } => Scorer::Kiai,
            Self::Peak { .. } => Scorer::Peak,
            Self::Choke { .. } => Scorer::Choke,
            Self::Storm { .. } => Scorer::Storm,
            Self::Precision { .. } => Scorer::Precision,
            Self::Scramble { .. } => Scorer::Scramble,
            Self::Opening { .. } => Scorer::Opening,
            Self::Finale { .. } => Scorer::Finale,
            Self::Travel { .. } => Scorer::Travel,
        }
    }

    /// One line a human can read, in the engine's voice.
    pub fn describe(&self) -> String {
        match *self {
            Self::Kiai { bpm, length_ms } => {
                format!("kiai — {:.0}s the mapper marked, at {bpm:.0} BPM", length_ms / 1000.0)
            }
            Self::Peak { combo } => format!("the play's longest run, {combo}x, ends here"),
            Self::Choke { combo, through } => format!(
                "a {combo}x run breaks {:.0}% of the way in",
                through * 100.0
            ),
            Self::Storm {
                objects,
                of_densest,
            } if of_densest >= 0.999 => {
                format!("the densest stretch of the map, {objects} objects")
            }
            Self::Storm {
                objects,
                of_densest,
            } => format!(
                "a dense stretch, {objects} objects — {:.0}% of the map's busiest",
                of_densest * 100.0
            ),
            Self::Precision {
                clicks,
                mean_error_ms,
                baseline_ms,
            } => format!(
                "{clicks} clicks at {mean_error_ms:.1}ms average error, against {baseline_ms:.1}ms for the play"
            ),
            Self::Scramble { misses, refused } => match (misses, refused) {
                (m, 0) => format!("{m} misses together"),
                (0, r) => format!("{r} clicks the game refused"),
                (m, r) => format!("{m} misses and {r} refused clicks together"),
            },
            Self::Opening { objects } => {
                format!("how the play opens, {objects} objects in")
            }
            Self::Finale {
                failed: true,
                accuracy,
                combo,
                ..
            } => format!("the play ends here — the bar empties at {combo}x, {accuracy:.2}%"),
            Self::Finale {
                accuracy,
                combo,
                full_combo: true,
                ..
            } => format!("it lands — {combo}x all the way, {accuracy:.2}%"),
            Self::Finale {
                accuracy, combo, ..
            } => format!("how it finishes — {combo}x, {accuracy:.2}%"),
            Self::Travel {
                speed,
                of_fastest,
            } if of_fastest >= 0.999 => {
                format!("the hardest movement in the play, {speed:.0} osu!px a second")
            }
            Self::Travel { speed, .. } => {
                format!("hard movement, {speed:.0} osu!px a second")
            }
        }
    }
}

/// One moment a scorer proposes, before anything has been chosen.
///
/// A scorer says *where* and *how much*, not how long: the clip length is the
/// caller's setting, and a scorer that decided it too would make two knobs out
/// of one. What a scorer does get to say is where in the clip its moment
/// belongs — see [`Candidate::bias`].
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Candidate {
    /// The instant the moment is about.
    pub anchor_ms: f64,
    /// Where the anchor sits inside the clip: 0 puts it at the first frame, 1
    /// at the last, 0.5 in the middle.
    ///
    /// This is most of what separates a clip that reads from one that does not.
    /// A choke wants the break about two thirds through, so there is a run-up to
    /// watch and a moment of aftermath; a peak wants its run ending near the
    /// end, so the number climbs while you watch. Centring everything throws
    /// that away and the reel comes out feeling arbitrary.
    pub bias: f64,
    /// How much of what this scorer can ever detect is present here, from 0 to 1.
    ///
    /// **Absolute, not relative to the scorer's other candidates.** The
    /// difference decides what a reel looks like. Normalising a scorer against
    /// its own best means its best always scores exactly its weight, so every
    /// scorer that fired at all contributes one clip and the reel is the weight
    /// table read aloud — which is what the first version of this did, and it
    /// gave a flawless play a "choke" clip because one combo run happened to be
    /// the longest of the three it broke.
    ///
    /// Made absolute, a scorer with nothing to say scores near zero and drops
    /// out on its own: a 12x longest run on a 2000-combo map is 0.006, not
    /// 1.0. The weight table becomes a ceiling rather than a result.
    ///
    /// Each scorer states what its 1.0 means, and that statement is the thing
    /// to argue with.
    pub strength: f64,
    pub reason: Reason,
}

/// A moment that was chosen.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Clip {
    pub span: Span,
    pub reason: Reason,
    /// Where it came in the choosing, best first. Clips are returned in *time*
    /// order — a reel that jumps backwards through the map is disorienting —
    /// so this is the only place the ranking survives, and it is what a caller
    /// trims with when the budget has to come down.
    pub rank: usize,
    /// What it scored when it was picked — strength times its scorer's weight,
    /// times whatever the discounts for repetition and crowding came to.
    ///
    /// The *effective* score and not the base one, which is the whole point of
    /// carrying it: three clips from one scorer reported their base score and
    /// so all read 0.500, which said nothing about why they were taken in that
    /// order or why the third was taken at all.
    pub score: f64,
}

/// The knobs, in **video** time.
///
/// Spans come back in map time, because that is what the renderer takes and
/// what every other command means by a millisecond. These do not: "thirty
/// seconds" is thirty seconds of somebody watching, and under DoubleTime that
/// is forty-five seconds of map. [`choose`] converts, using the replay's own
/// rate — the one place in this crate the two clocks meet.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Settings {
    /// How much video to end up with.
    pub budget_ms: f64,
    /// How long one clip is.
    pub clip_ms: f64,
    /// How far apart two clips' anchors must be before both can be chosen —
    /// as a multiple of the clip length.
    ///
    /// Overlap alone is not enough of a rule. Six clips laid end to end across
    /// the same eight seconds do not overlap and are still six views of one
    /// section, which is a worse reel than one that shows the shape of the
    /// play. When nothing else qualifies this is relaxed rather than leaving
    /// the budget unspent.
    pub spread: f64,
    /// How much longer than [`Settings::clip_ms`] the most important moment may
    /// run, as a fraction of it. `0.75` means the best clip is 1.75 clips long.
    ///
    /// Every clip the same length is a reel that gives the map's busiest eight
    /// seconds exactly as much room as the break that cost the play — which
    /// says the two matter equally, and they do not. Length is the one thing a
    /// silent reel has to say "this one" with.
    ///
    /// Zero restores the old behaviour: every clip the length it was asked for.
    pub stretch: f64,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            // A minute. Thirty seconds was chosen when there were six scorers
            // and every clip was the same length, so a reel was five clips and
            // the sixth scorer never appeared at all. With the edges of the
            // play worth showing and the strongest moments running long, thirty
            // seconds is spent before the reel has said anything about how the
            // play ended.
            budget_ms: 60_000.0,
            clip_ms: 6_000.0,
            spread: 3.0,
            stretch: 0.75,
        }
    }
}

impl Settings {
    /// The same knobs in map time, which is what everything downstream works in.
    fn in_map_time(&self, rate: f64) -> Self {
        let rate = if rate > 0.0 { rate } else { 1.0 };
        Self {
            budget_ms: self.budget_ms * rate,
            clip_ms: self.clip_ms * rate,
            spread: self.spread,
            stretch: self.stretch,
        }
    }

    /// How long a clip of this importance runs.
    ///
    /// `score` is what the moment was worth before any discount for repeating a
    /// scorer or crowding one of its neighbours — those say whether to take a
    /// clip, not how long it should be. A clip's length has to be a property of
    /// the moment itself, or the same moment would run longer or shorter
    /// depending on what happened to be picked before it.
    fn length_for(&self, score: f64) -> f64 {
        self.clip_ms * (1.0 + self.stretch.max(0.0) * score.clamp(0.0, 1.0))
    }
}

/// Choose the moments worth watching, in time order.
///
/// Returns an empty list for a play with nothing in it — no objects, or a
/// replay that was never judged. That is a real answer and not an error: some
/// replays are twelve seconds of a map somebody quit.
pub fn choose(state: &GameState, settings: Settings) -> Vec<Clip> {
    let settings = settings.in_map_time(state.playback_rate());
    let candidates = scorers::all(state, settings);
    select::choose(candidates, state.span_ms(), state.timeline(), settings)
}

/// Every candidate every scorer proposed, unranked and unchosen.
///
/// For asking *why* a reel came out the way it did — the chosen clips say what
/// won, and only this says what it beat.
pub fn candidates(state: &GameState, settings: Settings) -> Vec<(Scorer, Candidate)> {
    scorers::all(state, settings.in_map_time(state.playback_rate()))
}
