//! `dossier exhibit` — which moments of a play are worth watching.
//!
//! The choosing lives in [`dossier_exhibit`]; this is the part that reads a
//! replay off disk and writes the answer out. Two surfaces, and the order they
//! are listed in the usage text is deliberate: `--json` first because the
//! selection is the feature, `-o` second because the video is a consequence of
//! it. Everything that can go wrong with a reel can be seen without waiting for
//! an encode, and an encode of a minute of gameplay is minutes of waiting.

use dossier_exhibit::{Clip, Reason, Settings};
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
