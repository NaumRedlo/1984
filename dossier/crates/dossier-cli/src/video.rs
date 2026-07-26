//! Rendering a play to a video file.
//!
//! Frames go to ffmpeg down a pipe as raw RGBA, never touching the disk. The
//! alternative — writing a few thousand PNGs and pointing an encoder at the
//! folder — costs a compress and a decompress per frame plus gigabytes of
//! temporary files, to move bytes between two processes that are already
//! connected.
//!
//! ffmpeg is invoked rather than linked. It is the one program on every machine
//! that already knows every container and codec anyone will ask for, and
//! swapping it for something else later changes this file and nothing else.
//! The cost is a dependency the host has to have, which is why its absence is
//! reported as plainly as possible.

use std::io::Write;
use std::path::Path;
use std::process::{Child, Command, Stdio};

use dossier_render::{Layout, Scene};
use tiny_skia::Pixmap;

pub struct Settings {
    pub out: std::path::PathBuf,
    pub fps: f64,
    pub size: (u32, u32),
    /// Span to render, in map time. `None` means the whole play.
    pub from_ms: Option<f64>,
    pub to_ms: Option<f64>,
    pub ffmpeg: String,
    pub crf: u32,
    /// The map's audio track. Absent means a silent render.
    pub audio: Option<std::path::PathBuf>,
}

/// How the audio is lined up with the video.
///
/// osu! states object times in audio time, so the two clocks already agree: the
/// track only has to be seeked to where the render starts. Under a rate mod it
/// also has to be stretched, or the map plays fast against music that doesn't.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AudioSync {
    pub seek_seconds: f64,
    pub tempo: f64,
}

impl AudioSync {
    pub fn new(from_ms: f64, rate: f64) -> Self {
        Self {
            // A negative start is the lead-in before the first note; there is
            // no audio there to seek to, so begin at zero and let the silence
            // be silence.
            seek_seconds: (from_ms / 1000.0).max(0.0),
            tempo: rate,
        }
    }

    /// `atempo` handles 0.5–2.0 in one pass, which covers every rate osu! has.
    /// A rate outside that would need the filter chained, and quietly emitting
    /// one that ffmpeg rejects would fail the render at the last moment.
    pub fn filter(&self) -> Option<String> {
        if (self.tempo - 1.0).abs() < 1e-9 {
            return None;
        }
        (0.5..=2.0)
            .contains(&self.tempo)
            .then(|| format!("atempo={:.6}", self.tempo))
    }
}

/// What a render is going to be, worked out before anything is drawn.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Plan {
    /// Span in map time.
    pub from_ms: f64,
    pub to_ms: f64,
    pub frames: u64,
    pub video_seconds: f64,
}

impl Plan {
    /// `rate` is the mod speed multiplier: under DoubleTime a second of video
    /// has to cover a second and a half of map time, or the video plays the map
    /// at the wrong speed while claiming to be a recording of it.
    pub fn new(span: (f64, f64), rate: f64, settings: &Settings) -> Result<Self, String> {
        let (width, height) = settings.size;
        if width % 2 != 0 || height % 2 != 0 {
            // yuv420p halves both dimensions; an odd one has no valid encoding.
            return Err(format!("{width}x{height}: both sides have to be even"));
        }
        if settings.fps <= 0.0 {
            return Err("fps has to be positive".to_owned());
        }
        if rate <= 0.0 {
            return Err("the playback rate has to be positive".to_owned());
        }

        let from_ms = settings.from_ms.unwrap_or(span.0);
        let to_ms = settings.to_ms.unwrap_or(span.1);
        if to_ms <= from_ms {
            return Err(format!(
                "nothing to render between {from_ms}ms and {to_ms}ms"
            ));
        }

        // Map time is what the timeline speaks; video time is what the viewer
        // experiences. The rate is the only place the two differ.
        let video_seconds = (to_ms - from_ms) / 1000.0 / rate;
        Ok(Self {
            from_ms,
            to_ms,
            frames: (video_seconds * settings.fps).ceil() as u64,
            video_seconds,
        })
    }

    /// Map time of the `index`-th frame.
    pub fn map_time_of(&self, index: u64, fps: f64, rate: f64) -> f64 {
        self.from_ms + (index as f64 / fps) * 1000.0 * rate
    }
}

/// Render `scene` over `span` and encode it.
pub fn encode(
    scene: &Scene<'_>,
    span: (f64, f64),
    rate: f64,
    settings: &Settings,
) -> Result<(), String> {
    let plan = Plan::new(span, rate, settings)?;
    let (width, height) = settings.size;
    let total = plan.frames;

    let sync = AudioSync::new(plan.from_ms, rate);
    let mut child = spawn(settings, sync)?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or("ffmpeg gave us no pipe to write to")?;

    let layout = Layout::new(width, height);
    let mut pixmap = Pixmap::new(width, height).ok_or("could not allocate a frame")?;
    let started = std::time::Instant::now();

    for index in 0..total {
        let map_ms = plan.map_time_of(index, settings.fps, rate);
        scene.draw_into(&mut pixmap, map_ms, &layout);

        if let Err(error) = stdin.write_all(pixmap.data()) {
            // A broken pipe means ffmpeg died; its own message is the useful
            // one, so let it surface instead of this.
            drop(stdin);
            let status = child.wait().map_err(|e| e.to_string())?;
            return Err(format!(
                "ffmpeg stopped after {index} frames ({status}): {error}"
            ));
        }

        if index % (settings.fps as u64 * 5).max(1) == 0 {
            report(index, total, started);
        }
    }

    drop(stdin);
    let status = child.wait().map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!("ffmpeg exited with {status}"));
    }

    let elapsed = started.elapsed().as_secs_f64();
    eprintln!(
        "\r{total} frames in {elapsed:.1}s ({:.0} fps, {:.1}× realtime){:20}",
        total as f64 / elapsed,
        plan.video_seconds / elapsed,
        ""
    );
    Ok(())
}

fn spawn(settings: &Settings, sync: AudioSync) -> Result<Child, String> {
    let (width, height) = settings.size;
    let mut command = Command::new(&settings.ffmpeg);
    command.args([
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgba",
        "-video_size",
        &format!("{width}x{height}"),
        "-framerate",
        &format!("{}", settings.fps),
        "-i",
        "-",
    ]);

    // `-ss` binds to the input that follows it, so the seek has to be stated
    // between the two inputs rather than up front.
    if let Some(audio) = &settings.audio {
        command
            .arg("-ss")
            .arg(format!("{:.3}", sync.seek_seconds))
            .arg("-i")
            .arg(audio);
        if let Some(filter) = sync.filter() {
            command.args(["-filter:a", &filter]);
        }
        command.args(["-c:a", "aac", "-b:a", "192k"]);
        // The track outlasts the clip whenever only part of a map is rendered.
        command.arg("-shortest");
    }

    command
        .args([
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            &settings.crf.to_string(),
            "-pix_fmt",
            "yuv420p",
        ])
        .arg(&settings.out)
        .stdin(Stdio::piped())
        .spawn()
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                format!(
                    "{} not found. Install it (macOS: brew install ffmpeg, \
                     Debian: apt install ffmpeg) or pass --ffmpeg <path>.",
                    settings.ffmpeg
                )
            } else {
                format!("could not start {}: {error}", settings.ffmpeg)
            }
        })
}

fn report(index: u64, total: u64, started: std::time::Instant) {
    let done = index.max(1);
    let rate = done as f64 / started.elapsed().as_secs_f64();
    let left = (total - done) as f64 / rate.max(0.001);
    eprint!("\r{done}/{total} frames, {rate:.0}/s, {left:.0}s left     ",);
    let _ = std::io::stderr().flush();
}

/// Does this path look like something we can write?
pub fn check_output(path: &Path) -> Result<(), String> {
    match path.extension().and_then(|e| e.to_str()) {
        Some(_) => Ok(()),
        // ffmpeg picks its container from the extension, and its own error for
        // a missing one is far less clear than saying so here.
        None => Err(format!(
            "{}: give the output a file extension so ffmpeg knows the container",
            path.display()
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> Settings {
        Settings {
            out: std::path::PathBuf::from("out.mp4"),
            fps: 60.0,
            size: (1280, 720),
            from_ms: None,
            to_ms: None,
            ffmpeg: "ffmpeg".to_owned(),
            crf: 20,
            audio: None,
        }
    }

    #[test]
    fn a_plain_play_renders_one_frame_per_tick_of_the_clock() {
        let plan = Plan::new((0.0, 10_000.0), 1.0, &settings()).unwrap();
        assert_eq!(plan.frames, 600);
        assert!((plan.video_seconds - 10.0).abs() < 1e-9);
    }

    #[test]
    fn doubletime_packs_more_map_into_the_same_second_of_video() {
        // The map is played faster, so ten seconds of it is under seven
        // seconds to watch. Ignoring the rate here would render the whole map
        // in slow motion.
        let plan = Plan::new((0.0, 10_000.0), 1.5, &settings()).unwrap();
        assert_eq!(plan.frames, 400);

        // …and the clock still advances at the map's pace, not the viewer's.
        assert!((plan.map_time_of(60, 60.0, 1.5) - 1500.0).abs() < 1e-9);
    }

    #[test]
    fn halftime_stretches_it_the_other_way() {
        let plan = Plan::new((0.0, 10_000.0), 0.75, &settings()).unwrap();
        assert_eq!(plan.frames, 800);
    }

    #[test]
    fn an_explicit_span_overrides_the_plays_own() {
        let mut settings = settings();
        settings.from_ms = Some(2_000.0);
        settings.to_ms = Some(3_000.0);
        let plan = Plan::new((0.0, 100_000.0), 1.0, &settings).unwrap();
        assert_eq!((plan.from_ms, plan.to_ms), (2_000.0, 3_000.0));
        assert_eq!(plan.frames, 60);
    }

    #[test]
    fn odd_dimensions_are_refused_before_a_single_frame_is_drawn() {
        // yuv420p can't encode them, and finding out after rendering for a
        // minute is a poor way to learn it.
        let mut settings = settings();
        settings.size = (1281, 720);
        assert!(Plan::new((0.0, 1000.0), 1.0, &settings).is_err());
    }

    #[test]
    fn an_empty_or_backwards_span_is_refused() {
        let mut settings = settings();
        settings.from_ms = Some(5_000.0);
        settings.to_ms = Some(1_000.0);
        assert!(Plan::new((0.0, 100_000.0), 1.0, &settings).is_err());
    }

    #[test]
    fn the_output_needs_an_extension_for_ffmpeg_to_pick_a_container() {
        assert!(check_output(std::path::Path::new("replay.mp4")).is_ok());
        assert!(check_output(std::path::Path::new("replay")).is_err());
    }
}

#[cfg(test)]
mod audio_tests {
    use super::*;

    #[test]
    fn the_track_is_seeked_to_where_the_render_starts() {
        // Object times are audio times, so this is the whole of the alignment.
        let sync = AudioSync::new(46_000.0, 1.0);
        assert!((sync.seek_seconds - 46.0).abs() < 1e-9);
        assert_eq!(sync.filter(), None, "no stretching at normal speed");
    }

    #[test]
    fn a_lead_in_before_the_song_seeks_to_zero() {
        // The render span starts one preempt before the first note, which on a
        // map that opens early is a negative time. There is no audio there.
        let sync = AudioSync::new(-800.0, 1.0);
        assert_eq!(sync.seek_seconds, 0.0);
    }

    #[test]
    fn rate_mods_stretch_the_track_to_match() {
        assert_eq!(
            AudioSync::new(0.0, 1.5).filter().as_deref(),
            Some("atempo=1.500000")
        );
        assert_eq!(
            AudioSync::new(0.0, 0.75).filter().as_deref(),
            Some("atempo=0.750000")
        );
    }

    #[test]
    fn a_rate_atempo_cannot_do_in_one_pass_is_refused_rather_than_emitted() {
        // ffmpeg would reject it after the render had already run.
        assert_eq!(AudioSync::new(0.0, 3.0).filter(), None);
    }
}
