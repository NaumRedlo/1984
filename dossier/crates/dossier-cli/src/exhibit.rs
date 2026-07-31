//! `dossier exhibit` — which moments of a play are worth watching.
//!
//! The choosing lives in [`dossier_exhibit`]; this is the part that reads a
//! replay off disk and writes the answer out. Two surfaces, and the order they
//! are listed in the usage text is deliberate: `--json` first because the
//! selection is the feature, `-o` second because the video is a consequence of
//! it. Everything that can go wrong with a reel can be seen without waiting for
//! an encode, and an encode of a minute of gameplay is minutes of waiting.

use std::collections::BTreeMap;

use dossier_exhibit::{Clip, Facet, Reason, Settings};
use dossier_replay::Replay;
use dossier_sim::GameState;

use crate::report::quote;

/// Turn the command-line seconds into the crate's milliseconds.
///
/// The two lengths are video time — what somebody watching would count — and
/// the crate converts to map time on its own, using the replay's rate. Doing it
/// here instead would put the DoubleTime arithmetic in two places.
pub fn settings(budget_s: Option<f64>, clip_s: Option<f64>, worth: Option<f64>) -> Settings {
    let defaults = Settings::default();
    Settings {
        budget_ms: budget_s.map_or(defaults.budget_ms, |s| s * 1000.0),
        clip_ms: clip_s.map_or(defaults.clip_ms, |s| s * 1000.0),
        worth: worth.map_or(defaults.worth, |w| w.clamp(0.0, 1.0)),
        ..defaults
    }
}

/// The chosen clips as one JSON object, on one line.
///
/// One line so a run over many replays is a stream somebody can pipe. The
/// spans are in **map** milliseconds — the same clock `--from` and `--to` take,
/// so a clip can be fed straight back to `dossier video` to look at on its own.
pub fn as_json(replay_path: &str, replay: &Replay, state: &GameState, clips: &[Clip]) -> String {
    let (from, to) = state.span_ms();
    let clips: Vec<String> = clips
        .iter()
        .map(|clip| {
            format!(
                "{{\"from_ms\":{:.1},\"to_ms\":{:.1},\"rank\":{},\"score\":{:.4},\"scorer\":{},\"reason\":{},\"detail\":{}}}",
                clip.span.from_ms,
                clip.span.to_ms,
                clip.rank,
                clip.score,
                quote(clip.reason.scorer().name()),
                quote(&clip.reason.describe()),
                detail(&clip.reason),
            )
        })
        .collect();
    format!(
        "{{\"replay\":{},\"player\":{},\"rate\":{:.3},\"play_ms\":[{from:.1},{to:.1}],\"clips\":[{}]}}",
        quote(replay_path),
        quote(&replay.player),
        state.playback_rate(),
        clips.join(","),
    )
}

/// The numbers behind a reason, as JSON.
///
/// The prose in `reason` is the engine speaking English, which is right for a
/// terminal and wrong for anything that has to show a moment to somebody in
/// another language. A caller with the numbers can phrase them itself; a caller
/// given only the sentence can either print English or translate prose, and the
/// second is worse than the first.
fn detail(reason: &Reason) -> String {
    match *reason {
        Reason::Kiai { bpm, length_ms } => {
            format!("{{\"bpm\":{bpm:.1},\"length_ms\":{length_ms:.0}}}")
        }
        Reason::Peak { combo } => format!("{{\"combo\":{combo}}}"),
        Reason::Choke { combo, through } => {
            format!("{{\"combo\":{combo},\"through\":{through:.4}}}")
        }
        Reason::Storm {
            objects,
            of_densest,
        } => format!("{{\"objects\":{objects},\"of_densest\":{of_densest:.4}}}"),
        Reason::Precision {
            clicks,
            mean_error_ms,
            baseline_ms,
        } => format!(
            "{{\"clicks\":{clicks},\"mean_error_ms\":{mean_error_ms:.2},\"baseline_ms\":{baseline_ms:.2}}}"
        ),
        Reason::Scramble { misses, refused } => {
            format!("{{\"misses\":{misses},\"refused\":{refused}}}")
        }
        Reason::Opening { objects } => format!("{{\"objects\":{objects}}}"),
        Reason::Finale {
            failed,
            accuracy,
            combo,
            full_combo,
        } => format!(
            "{{\"failed\":{failed},\"accuracy\":{accuracy:.4},\"combo\":{combo},\"full_combo\":{full_combo}}}"
        ),
        Reason::Travel { speed, of_fastest } => {
            format!("{{\"speed\":{speed:.1},\"of_fastest\":{of_fastest:.4}}}")
        }
    }
}

/// The same thing for a human, one clip a line.
///
/// The reason is the widest column on purpose. A list of timestamps is a thing
/// to trust or not trust with nothing in between; a list of timestamps that
/// each say what they are is a thing to disagree with, and disagreement is the
/// only feedback this feature can get.
pub fn as_text(clips: &[Clip], rate: f64) -> String {
    if clips.is_empty() {
        return "nothing to show — the play is shorter than one clip\n".to_owned();
    }
    let mut out = String::new();
    let mut watched = 0.0;
    for clip in clips {
        let seconds = clip.span.length_ms() / rate / 1000.0;
        watched += seconds;
        out.push_str(&format!(
            "{:>9} {:>9}  {:<10} {}\n",
            stamp(clip.span.from_ms),
            format!("+{seconds:.1}s"),
            clip.reason.scorer().name(),
            clip.reason.describe(),
        ));
    }
    out.push_str(&format!(
        "\n{} clip(s), {watched:.1}s to watch\n",
        clips.len()
    ));
    out
}

/// `1:23.4` — map time, which is where the map's own editor would put you.
fn stamp(ms: f64) -> String {
    let total = (ms / 1000.0).max(0.0);
    let minutes = (total / 60.0).floor();
    format!("{minutes:.0}:{:04.1}", total - minutes * 60.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamps_read_like_the_editor() {
        assert_eq!(stamp(0.0), "0:00.0");
        assert_eq!(stamp(83_400.0), "1:23.4");
        assert_eq!(stamp(600_000.0), "10:00.0");
    }

    /// A clip that ran off the front of the play must not print a negative
    /// timestamp — the span is real, the clock starts at zero.
    #[test]
    fn a_clip_before_zero_stamps_at_zero() {
        assert_eq!(stamp(-500.0), "0:00.0");
    }
}


// ── the survey ───────────────────────────────────────────────────────────

/// What a run of Exhibit over many replays came to.
///
/// Exhibit has no ground truth and never will, so the substitute is
/// **stability**: a change cannot be shown to be right, but it can be shown
/// what it did to a hundred replays. Without this, tuning a scorer is two
/// people watching two reels and disagreeing, and the numbers that would settle
/// it are computed and thrown away.
#[derive(Default)]
pub struct Survey {
    /// Replays that produced a reel.
    pub reels: usize,
    /// Replays with nothing worth showing — a real answer, not a failure.
    pub empty: usize,
    /// Replays that could not be judged at all.
    pub skipped: usize,
    /// Seconds of video per reel, for the spread.
    lengths: Vec<f64>,
    /// Clips per scorer, by name.
    by_scorer: BTreeMap<&'static str, usize>,
    /// Clips by what kind of thing they are about.
    by_facet: BTreeMap<&'static str, usize>,
    /// Reels holding nothing about what became of the run.
    ///
    /// The headline number. A reel with no combo lost, no combo held and no
    /// cluster of misses is a reel that watched a play and did not notice
    /// anything happen to it — sometimes right, on a clean run of a quiet map,
    /// and the share is the figure worth watching across a change.
    pub no_run: usize,
    /// Reels holding nothing but map-side moments — the same reel for everybody
    /// who ever played it. Should be near zero and is worth proving rather than
    /// assuming.
    pub map_only: usize,
}

impl Survey {
    pub fn add(&mut self, clips: &[Clip], rate: f64) {
        if clips.is_empty() {
            self.empty += 1;
            return;
        }
        self.reels += 1;
        self.lengths
            .push(clips.iter().map(|c| c.span.length_ms()).sum::<f64>() / 1000.0 / rate.max(0.001));
        for clip in clips {
            let scorer = clip.reason.scorer();
            *self.by_scorer.entry(scorer.name()).or_insert(0) += 1;
            *self.by_facet.entry(scorer.facet().name()).or_insert(0) += 1;
        }
        let facets: Vec<Facet> = clips.iter().map(|c| c.reason.scorer().facet()).collect();
        if !facets.contains(&Facet::Run) {
            self.no_run += 1;
        }
        if facets.iter().all(|facet| *facet == Facet::Map) {
            self.map_only += 1;
        }
    }

    pub fn report(&self) -> String {
        if self.reels == 0 {
            return format!(
                "no reels: {} replay(s) had nothing to show, {} could not be judged\n",
                self.empty, self.skipped
            );
        }
        let mut lengths = self.lengths.clone();
        lengths.sort_by(f64::total_cmp);
        let total: usize = self.by_scorer.values().sum();

        let mut out = format!(
            "{} reel(s) from {} replay(s){}\n{:.0}s…{:.0}s, median {:.0}s, {} clips\n\n",
            self.reels,
            self.reels + self.empty + self.skipped,
            match (self.empty, self.skipped) {
                (0, 0) => String::new(),
                (e, 0) => format!(" — {e} with nothing to show"),
                (0, s) => format!(" — {s} unjudged"),
                (e, s) => format!(" — {e} with nothing to show, {s} unjudged"),
            },
            lengths[0],
            lengths[lengths.len() - 1],
            lengths[lengths.len() / 2],
            total,
        );
        out.push_str(&format!("{:<11}{:>7}{:>10}{:>8}\n", "scorer", "clips", "per reel", "share"));

        // Busiest first: the question this table answers is what a reel is
        // *made of*, and alphabetical order buries it.
        let mut rows: Vec<(&str, usize)> = self.by_scorer.iter().map(|(k, v)| (*k, *v)).collect();
        rows.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(b.0)));
        for (name, count) in rows {
            out.push_str(&format!(
                "{name:<11}{count:>7}{:>10.2}{:>7.0}%\n",
                count as f64 / self.reels as f64,
                count as f64 / total as f64 * 100.0,
            ));
        }
        out.push_str("\nby facet\n");
        for facet in [Facet::Run, Facet::Hand, Facet::Map] {
            let count = self.by_facet.get(facet.name()).copied().unwrap_or(0);
            out.push_str(&format!(
                "{:<11}{count:>7}{:>10.2}{:>7.0}%\n",
                facet.name(),
                count as f64 / self.reels as f64,
                count as f64 / total as f64 * 100.0,
            ));
        }
        out.push_str(&format!(
            "\nreels with nothing about the run: {} of {} ({:.0}%)\n",
            self.no_run,
            self.reels,
            self.no_run as f64 / self.reels as f64 * 100.0,
        ));
        if self.map_only > 0 {
            out.push_str(&format!(
                "reels about the map alone:        {} ({:.0}%)\n",
                self.map_only,
                self.map_only as f64 / self.reels as f64 * 100.0,
            ));
        }
        out
    }
}

#[cfg(test)]
mod survey_tests {
    use super::*;
    use dossier_exhibit::Span;

    fn clip(scorer_reason: Reason, from: f64, to: f64) -> Clip {
        Clip {
            span: Span::new(from, to),
            reason: scorer_reason,
            rank: 0,
            score: 0.5,
        }
    }

    #[test]
    fn a_reel_with_nothing_about_the_run_is_counted() {
        let mut survey = Survey::default();
        // Map and hand only: the reel watched a play and noticed nothing
        // happen to it.
        survey.add(
            &[
                clip(Reason::Storm { objects: 60, of_densest: 1.0 }, 0.0, 6000.0),
                clip(Reason::Travel { speed: 500.0, of_fastest: 1.0 }, 9000.0, 15000.0),
            ],
            1.0,
        );
        // …and one that did notice.
        survey.add(
            &[
                clip(Reason::Storm { objects: 60, of_densest: 1.0 }, 0.0, 6000.0),
                clip(Reason::Choke { combo: 900, through: 0.8 }, 9000.0, 15000.0),
            ],
            1.0,
        );
        assert_eq!((survey.reels, survey.no_run, survey.map_only), (2, 1, 0));

        let report = survey.report();
        assert!(report.contains("nothing about the run: 1 of 2 (50%)"), "{report}");
    }

    /// A replay with nothing to show is a real answer — twelve seconds of
    /// somebody quitting — and must not be counted as a reel it failed at.
    #[test]
    fn an_empty_selection_is_not_a_reel() {
        let mut survey = Survey::default();
        survey.add(&[], 1.0);
        assert_eq!((survey.reels, survey.empty), (0, 1));
        assert!(survey.report().starts_with("no reels:"), "{}", survey.report());
    }

    /// The spans are map time; a rate mod compresses them into fewer seconds of
    /// watching, and a survey that ignored it would report DoubleTime reels as
    /// half again as long as anyone saw them.
    #[test]
    fn lengths_are_seconds_of_watching() {
        let mut survey = Survey::default();
        survey.add(&[clip(Reason::Peak { combo: 500 }, 0.0, 9000.0)], 1.5);
        assert!(survey.report().contains("6s…6s"), "{}", survey.report());
    }
}
