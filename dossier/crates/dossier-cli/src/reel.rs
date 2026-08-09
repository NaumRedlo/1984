//! Several spans of one play, rendered and cut together.
//!
//! The clips come from [`dossier_exhibit`]; this file only knows how to turn a
//! list of spans into one file. Each clip is rendered by the same [`video`]
//! path a whole replay goes through — there is no second renderer here and
//! there must not be, or a reel and a full render could disagree about what the
//! play looked like.
//!
//! # Why a second pass over the encoded clips
//!
//! Frames could be drawn straight into one long stream and the cuts made in the
//! drawing, which would encode once instead of twice. The audio is what stops
//! it: each clip needs its own slice of the song, seeked, rate-adjusted and
//! faded into the next, and that is a filter graph over N inputs — while the
//! existing audio path is built around one span and is the part of this program
//! that has been wrong the most times. Rendering each clip the way a clip is
//! already rendered, and then cutting, keeps every one of those fixes.
//!
//! The second encode costs a re-compress of the finished reel — thirty seconds
//! of video, against the minutes spent drawing it. It is not where the time
//! goes.
//!
//! # Why the video crossfades too
//!
//! What makes a hard cut unpleasant is the audio: six songs spliced end to end
//! click at every join. So the audio must crossfade — and once it does, the
//! video has to overlap by the same amount or the two drift apart by one fade
//! per cut, which by the fifth clip is seconds of the wrong sound over the
//! right picture.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use dossier_exhibit::Clip;
use dossier_render::Scene;
use dossier_sim::GameState;

use crate::video;

/// How long one clip dissolves into the next, in seconds.
///
/// Short. A reel is made of six-second clips and a long dissolve spends the
/// clip on the dissolve — but the join has to be audible as a join rather than
/// as a glitch, and under about a third of a second it stops reading as one.
const CROSSFADE_S: f64 = 0.4;

/// The fade from black at the start and back to it at the end.
///
/// Longer than the crossfade on purpose: this one is the reel starting and
/// ending, and it is the only moment the viewer is told the thing has a shape.
const EDGE_FADE_S: f64 = 0.6;

/// What the caller has to supply per clip that this file cannot work out.
///
/// Only the hit sounds, and they need a callback rather than a path because
/// they are synthesised *for* a span: the track is built on the video's own
/// timebase, so every clip needs its own and the plan that says how long it is
/// only exists once the clip is being set up.
pub type Hitsounds<'a> = dyn Fn(&video::Plan, usize) -> Option<PathBuf> + 'a;

/// Render every clip and cut them into one file.
///
/// `settings.out` is the reel; `settings.from_ms` and `to_ms` are ignored,
/// since the clips say what to render.
///
/// The play is taken whole rather than as the three numbers this needs from it.
/// Those three — the span, the rate and where the play ended — always come from
/// the same state, and passed separately they are three chances to hand a reel
/// one replay's span with another's rate.
pub fn render(
    scene: &Scene<'_>,
    state: &GameState,
    clips: &[Clip],
    settings: &video::Settings,
    scratch: Option<&Path>,
    hitsounds: &Hitsounds<'_>,
) -> Result<(), String> {
    let span = state.span_ms();
    let rate = state.playback_rate();
    let fail_at_ms = state.ending().map(|end| end.time_ms);
    if clips.is_empty() {
        return Err("no clips to render".to_owned());
    }
    let Some(scratch) = scratch else {
        return Err("a reel needs a scratch directory to build its clips in".to_owned());
    };

    let mut parts: Vec<Part> = Vec::with_capacity(clips.len());
    for (index, clip) in clips.iter().enumerate() {
        let path = scratch.join(format!("clip-{index}.mp4"));
        let mut one = video::Settings {
            out: path.clone(),
            from_ms: Some(clip.span.from_ms),
            to_ms: Some(clip.span.to_ms),
            hitsounds: None,
            ..clone_settings(settings)
        };
        let plan = video::Plan::new(span, rate, &one, fail_at_ms)?;
        one.hitsounds = hitsounds(&plan, index);

        let reason = clip.reason.describe();
        eprintln!(
            "[{}/{}] {} — {reason}",
            index + 1,
            clips.len(),
            stamp(clip.span.from_ms),
        );
        settings
            .events
            .clip(index + 1, clips.len(), clip.span.from_ms, &reason);
        video::encode(scene, span, rate, &one, fail_at_ms)?;
        parts.push(Part {
            path,
            seconds: plan.video_seconds,
            sound: one.audio.is_some() || one.hitsounds.is_some(),
        });
    }

    stitch(&parts, settings)
}

/// One rendered clip, waiting to be cut in.
struct Part {
    path: PathBuf,
    seconds: f64,
    sound: bool,
}

/// Cut the rendered clips together in one ffmpeg pass.
fn stitch(parts: &[Part], settings: &video::Settings) -> Result<(), String> {
    // A clip with no audio has no audio *stream*, and asking a filter for one
    // is an error rather than silence. Either every clip has sound or the reel
    // has none — which is what happens: the setting is per render, not per clip.
    let sound = parts.iter().all(|part| part.sound);
    let total = parts.iter().map(|part| part.seconds).sum::<f64>()
        - CROSSFADE_S * (parts.len() - 1) as f64;
    if total <= 0.0 {
        return Err("the crossfades are longer than the clips they join".to_owned());
    }

    let mut command = Command::new(&settings.ffmpeg);
    command.args(["-y", "-loglevel", "error"]);
    for part in parts {
        command.arg("-i").arg(&part.path);
    }
    command.args(["-filter_complex", &graph(parts, total, sound)]);
    command.args(["-map", "[v]"]);
    if sound {
        command.args(["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]);
    }
    if let Some(threads) = settings.encoder_threads {
        command.args(["-threads", &threads.to_string()]);
    }
    command
        .args([
            "-c:v",
            "libx264",
            "-preset",
            &settings.preset,
            "-crf",
            &settings.crf.to_string(),
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
        ])
        .arg(&settings.out)
        .stdin(Stdio::null())
        .stderr(Stdio::piped());
    if std::env::var("DOSSIER_FFMPEG_ARGS").is_ok() {
        eprintln!("ffmpeg {:?}", command.get_args().collect::<Vec<_>>());
    }

    eprintln!("   cutting {} clips together, {total:.1}s", parts.len());
    let output = command
        .output()
        .map_err(|error| format!("could not start {}: {error}", settings.ffmpeg))?;
    if output.status.success() {
        // The same line a single render ends on, and it has to be the *reel's*
        // numbers: a caller reading the shape of the finished file out of the
        // engine's report would otherwise find five clips each announcing six
        // seconds, and send a thirty-second video labelled as six.
        let (width, height) = settings.size;
        eprintln!("dossier: video {width}x{height} {total:.3}s");
        settings.events.video(width, height, total);
        return Ok(());
    }
    // The same discipline as the render: ffmpeg's own words, not our guess at
    // them. A filter graph is long enough that a typo in it is unrecognisable
    // from anything but the complaint it produces.
    let said = String::from_utf8_lossy(&output.stderr);
    let lines: Vec<&str> = said.lines().filter(|line| !line.trim().is_empty()).collect();
    Err(format!(
        "cutting the reel together failed ({}){}",
        output.status,
        match lines.is_empty() {
            true => String::new(),
            false => format!(":\n{}", lines[lines.len().saturating_sub(6)..].join("\n")),
        }
    ))
}

/// The filter graph, built as text because that is the only way ffmpeg takes one.
///
/// Split out from [`stitch`] so it can be read as a whole and tested without an
/// encoder: the offsets are the part that is easy to get subtly wrong, and a
/// graph that is off by one fade produces a reel that plays and is wrong.
fn graph(parts: &[Part], total: f64, sound: bool) -> String {
    let mut chain = Vec::new();

    // `xfade` states *when* the dissolve starts, measured in the stream built
    // so far — and that stream is shorter than the clips it holds by one fade
    // per join already made. Getting this wrong does not fail; it produces a
    // reel that plays with the cuts in the wrong places.
    let mut label = "0:v".to_owned();
    let mut so_far = parts[0].seconds;
    for (index, part) in parts.iter().enumerate().skip(1) {
        let next = format!("vx{index}");
        chain.push(format!(
            "[{label}][{index}:v]xfade=transition=fade:duration={CROSSFADE_S}:offset={:.3}[{next}]",
            so_far - CROSSFADE_S
        ));
        so_far += part.seconds - CROSSFADE_S;
        label = next;
    }
    chain.push(format!(
        "[{label}]fade=t=in:st=0:d={EDGE_FADE_S},fade=t=out:st={:.3}:d={EDGE_FADE_S}[v]",
        (total - EDGE_FADE_S).max(0.0)
    ));

    if sound {
        // `acrossfade` needs no offset: it always joins the end of the first
        // stream to the start of the second, which is the same instant `xfade`
        // was told about the long way round.
        let mut label = "0:a".to_owned();
        for index in 1..parts.len() {
            let next = format!("ax{index}");
            chain.push(format!(
                "[{label}][{index}:a]acrossfade=d={CROSSFADE_S}:c1=tri:c2=tri[{next}]"
            ));
            label = next;
        }
        chain.push(format!(
            "[{label}]afade=t=in:st=0:d={EDGE_FADE_S},afade=t=out:st={:.3}:d={EDGE_FADE_S}[a]",
            (total - EDGE_FADE_S).max(0.0)
        ));
    }

    chain.join(";")
}

/// `video::Settings` holds paths and strings and so cannot be `Copy`; this is
/// the one place a copy is wanted, and spelling it out beats deriving `Clone`
/// on a type whose whole purpose is to be built once and read.
fn clone_settings(settings: &video::Settings) -> video::Settings {
    video::Settings {
        out: settings.out.clone(),
        fps: settings.fps,
        size: settings.size,
        from_ms: settings.from_ms,
        to_ms: settings.to_ms,
        ffmpeg: settings.ffmpeg.clone(),
        crf: settings.crf,
        preset: settings.preset.clone(),
        threads: settings.threads,
        encoder_threads: settings.encoder_threads,
        audio: settings.audio.clone(),
        hitsounds: settings.hitsounds.clone(),
        // Each clip reports its own frames, which is the only way a watcher
        // can show movement during the twenty seconds one of them takes.
        events: settings.events,
        slow_at_ms: settings.slow_at_ms,
    }
}

fn stamp(ms: f64) -> String {
    let total = (ms / 1000.0).max(0.0);
    let minutes = (total / 60.0).floor();
    format!("{minutes:.0}:{:04.1}", total - minutes * 60.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parts(lengths: &[f64], sound: bool) -> Vec<Part> {
        lengths
            .iter()
            .enumerate()
            .map(|(i, &seconds)| Part {
                path: PathBuf::from(format!("clip-{i}.mp4")),
                seconds,
                sound,
            })
            .collect()
    }

    fn total(parts: &[Part]) -> f64 {
        parts.iter().map(|p| p.seconds).sum::<f64>() - CROSSFADE_S * (parts.len() - 1) as f64
    }

    /// The offsets are cumulative *after* the fades already taken out, not
    /// cumulative over the clips. The difference is one fade per join, and it
    /// grows: by the fifth clip a graph that ignores it is 1.6s out.
    #[test]
    fn each_dissolve_starts_where_the_stream_so_far_ends() {
        let parts = parts(&[6.0, 6.0, 6.0], true);
        let graph = graph(&parts, total(&parts), true);

        // First join: six seconds in, less the fade.
        assert!(graph.contains("offset=5.600"), "{graph}");
        // Second: the stream so far is 6 + 6 - 0.4 = 11.6, less the fade.
        assert!(graph.contains("offset=11.200"), "{graph}");
    }

    #[test]
    fn the_reel_fades_out_a_fade_before_it_ends() {
        let parts = parts(&[6.0, 6.0], true);
        // 6 + 6 - 0.4 = 11.6 long, so the closing fade starts at 11.0.
        let graph = graph(&parts, total(&parts), true);
        assert!(graph.contains("fade=t=out:st=11.000"), "{graph}");
        assert!(graph.contains("afade=t=out:st=11.000"), "{graph}");
    }

    /// A muted render has no audio stream to ask for, and asking anyway is an
    /// error rather than silence.
    #[test]
    fn a_silent_reel_builds_no_audio_chain() {
        let parts = parts(&[6.0, 6.0], false);
        let graph = graph(&parts, total(&parts), false);
        assert!(!graph.contains(":a]"), "{graph}");
        assert!(!graph.contains("acrossfade"), "{graph}");
        assert!(graph.ends_with("[v]"), "{graph}");
    }

    /// One clip is a reel of one: nothing to dissolve, but it still opens and
    /// closes like a reel.
    #[test]
    fn a_single_clip_still_gets_its_edges() {
        let parts = parts(&[6.0], true);
        let graph = graph(&parts, total(&parts), true);
        assert!(!graph.contains("xfade"), "{graph}");
        assert!(graph.contains("[0:v]fade=t=in:st=0"), "{graph}");
        assert!(graph.contains("[0:a]afade=t=in:st=0"), "{graph}");
    }

    /// Clips are not all the same length — the last one of a play that ends
    /// carries the closing fade with it — so the offsets have to come from the
    /// lengths rather than from the count.
    #[test]
    fn uneven_clips_still_line_up() {
        let parts = parts(&[6.0, 6.7, 6.0], true);
        let graph = graph(&parts, total(&parts), true);
        assert!(graph.contains("offset=5.600"), "{graph}");
        // 6 + 6.7 - 0.4 = 12.3, less the fade.
        assert!(graph.contains("offset=11.900"), "{graph}");
    }
}
