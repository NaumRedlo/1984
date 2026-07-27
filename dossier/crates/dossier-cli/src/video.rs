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
    /// x264 preset. Once the encoder is the wall — which it becomes as soon as
    /// drawing is parallel — this is the largest lever left, and it belongs to
    /// whoever is waiting for the render rather than to this file.
    pub preset: String,
    /// Threads that draw frames. `None` leaves one core for the encoder.
    pub threads: Option<usize>,
    /// Threads the encoder may use. `None` leaves ffmpeg to decide, which on a
    /// small machine means it takes more than there are cores.
    pub encoder_threads: Option<usize>,
    /// The map's audio track. Absent means a silent render.
    pub audio: Option<std::path::PathBuf>,
    /// Raw stereo PCM of the hit sounds, already on the video's timebase.
    pub hitsounds: Option<std::path::PathBuf>,
}

/// How the audio is lined up with the video.
///
/// osu! states object times in audio time, so the two clocks already agree: the
/// track only has to be seeked to where the render starts. Under a rate mod it
/// also has to be stretched, or the map plays fast against music that doesn't.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AudioSync {
    pub seek_seconds: f64,
    /// How long the video runs before the music is due to start.
    pub delay_seconds: f64,
    pub tempo: f64,
}

impl AudioSync {
    pub fn new(from_ms: f64, rate: f64) -> Self {
        Self {
            seek_seconds: (from_ms / 1000.0).max(0.0),
            // A render that begins before the song does — the lead-in, where
            // the replay is already recording cursor movement — has to hold the
            // music back. Clamping the seek to zero was not enough on its own:
            // it starts the song at the first frame instead of at the right
            // one, which plays the whole map early by the length of the lead-in.
            //
            // The delay is in video time, so a rate mod compresses it along
            // with everything else.
            delay_seconds: (-from_ms / 1000.0 / rate).max(0.0),
            tempo: rate,
        }
    }

    /// The chain that lines the music up: stretch, then shift.
    ///
    /// In that order, because the delay is measured in video time and stretching
    /// afterwards would scale the silence too.
    ///
    /// `atempo` handles 0.5–2.0 in one pass, which covers every rate osu! has.
    /// A rate outside that would need the filter chained, and quietly emitting
    /// one that ffmpeg rejects would fail the render at the last moment.
    pub fn filter(&self) -> Option<String> {
        let mut chain = Vec::new();
        if (self.tempo - 1.0).abs() > 1e-9 && (0.5..=2.0).contains(&self.tempo) {
            chain.push(format!("atempo={:.6}", self.tempo));
        }
        if self.delay_seconds > 0.0005 {
            chain.push(format!("adelay={:.0}:all=1", self.delay_seconds * 1000.0));
        }
        (!chain.is_empty()).then(|| chain.join(","))
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
    let workers = settings.threads.unwrap_or_else(default_workers).max(1);
    let started = std::time::Instant::now();

    // Frames are independent and the scene is read-only, so they can be drawn
    // in any order — but they have to reach the encoder in the right one. Each
    // worker owns a couple of buffers; the writer sends each one back to its
    // owner once the frame has gone out.
    //
    // Buffers are owned rather than pooled for a reason. A shared pool needs a
    // lock, and a worker waiting for a buffer holds that lock while it waits —
    // so the moment the encoder falls behind and the pool empties, every worker
    // queues up behind one of them and the whole thing runs single-file. That
    // is invisible while there is slack and total while there isn't, which is
    // the worst way for a bug to behave.
    const OWNED: usize = 2;
    let mut returns = Vec::with_capacity(workers);
    let mut inboxes = Vec::with_capacity(workers);
    for _ in 0..workers {
        let (tx, rx) = std::sync::mpsc::channel::<Frame>();
        for _ in 0..OWNED {
            let frame = Frame::new(width, height).ok_or("could not allocate a frame")?;
            tx.send(frame).map_err(|e| e.to_string())?;
        }
        returns.push(tx);
        inboxes.push(rx);
    }

    let (done_tx, done_rx) = std::sync::mpsc::channel::<(u64, usize, Frame)>();
    let next = std::sync::atomic::AtomicU64::new(0);
    let drawing = std::sync::atomic::AtomicU64::new(0);

    // The span is worth saying out loud. A play whose replay starts recording
    // during the lead-in begins before the song does, and every audio
    // complaint traces back to that number — printing it turns "the music is
    // early" into an arithmetic question instead of a hunt.
    eprintln!(
        "   {:.2}s…{:.2}s of map time{}",
        plan.from_ms / 1000.0,
        plan.to_ms / 1000.0,
        match sync.delay_seconds {
            d if d > 0.0005 => format!(", music held back {d:.2}s"),
            _ => String::new(),
        }
    );
    eprintln!("   {workers} render thread(s), {OWNED} frame buffers each");

    let mut piping = std::time::Duration::ZERO;
    let outcome: Result<(), String> = std::thread::scope(|scope| {
        for (worker, rx) in inboxes.into_iter().enumerate() {
            let (done_tx, next, drawing) = (done_tx.clone(), &next, &drawing);
            let (scene, layout, plan, settings) = (scene, &layout, &plan, settings);
            scope.spawn(move || {
                // The buffer comes first, and the frame number second. A
                // worker that held a number while waiting for a buffer could be
                // holding the very frame the writer is waiting to write — and
                // then nobody moves.
                while let Ok(mut buffer) = rx.recv() {
                    let index = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    if index >= total {
                        break;
                    }

                    let mark = std::time::Instant::now();
                    scene.draw_into(
                        &mut buffer.pixmap,
                        plan.map_time_of(index, settings.fps, rate),
                        layout,
                    );
                    let Frame { pixmap, yuv } = &mut buffer;
                    to_yuv420(pixmap, yuv);
                    drawing.fetch_add(
                        mark.elapsed().as_micros() as u64,
                        std::sync::atomic::Ordering::Relaxed,
                    );

                    if done_tx.send((index, worker, buffer)).is_err() {
                        break;
                    }
                }
            });
        }
        drop(done_tx);

        // Frames arrive in whatever order they finished; the writer holds the
        // early ones back until their turn comes.
        let mut pending: std::collections::HashMap<u64, (usize, Frame)> =
            std::collections::HashMap::new();
        let mut wanted = 0u64;
        while wanted < total {
            let (index, worker, frame) = match done_rx.recv() {
                Ok(triple) => triple,
                Err(_) => return Err("a render thread stopped early".to_owned()),
            };
            pending.insert(index, (worker, frame));

            while let Some((worker, frame)) = pending.remove(&wanted) {
                let mark = std::time::Instant::now();
                let written = stdin.write_all(&frame.yuv);
                piping += mark.elapsed();
                // Home before anything else, so a waiting worker is released
                // even on the failure path.
                let _ = returns[worker].send(frame);
                if let Err(error) = written {
                    return Err(format!("ffmpeg stopped after {wanted} frames: {error}"));
                }
                wanted += 1;
                if wanted.is_multiple_of((settings.fps as u64 * 5).max(1)) {
                    report(wanted, total, started);
                }
            }
        }
        Ok(())
    });

    if let Err(message) = outcome {
        drop(stdin);
        let _ = child.wait();
        return Err(message);
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
    // Machine-readable, for whoever has to describe the file afterwards.
    // Telegram draws its placeholder from the dimensions it is told, not from
    // the stream, so a video sent without them comes out square on a phone and
    // only corrects itself once playback starts. This is the process that made
    // the file and knows exactly what is in it.
    eprintln!("dossier: video {width}x{height} {:.3}s", plan.video_seconds);
    let drawing_ms = drawing.load(std::sync::atomic::Ordering::Relaxed) as f64 / 1000.0;
    eprintln!(
        "   {workers} render thread(s): {:.1}ms of drawing per frame across them, \
         {:.1}ms piping, {:.1}ms elapsed",
        drawing_ms / total as f64,
        piping.as_secs_f64() * 1000.0 / total as f64,
        elapsed * 1000.0 / total as f64,
    );
    Ok(())
}

fn spawn(settings: &Settings, sync: AudioSync) -> Result<Child, String> {
    let (width, height) = settings.size;
    let mut command = Command::new(&settings.ffmpeg);
    // Input 0 is the video on stdin; the audio inputs are counted after it.
    let mut inputs = 0usize;
    command.args([
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "yuv420p",
        "-video_size",
        &format!("{width}x{height}"),
        "-framerate",
        &format!("{}", settings.fps),
        "-i",
        "-",
    ]);

    // `-ss` binds to the input that follows it, so the seek has to be stated
    // between the two inputs rather than up front.
    let mut music = None;
    if let Some(audio) = &settings.audio {
        command
            .arg("-ss")
            .arg(format!("{:.3}", sync.seek_seconds))
            .arg("-i")
            .arg(audio);
        music = Some(command_input_index(&mut inputs));
    }

    let mut hits = None;
    if let Some(pcm) = &settings.hitsounds {
        // Already generated at the video's own timebase, so it needs no seek
        // and no stretching — the rate was applied when it was built.
        command
            .args([
                "-f",
                "s16le",
                "-ar",
                &dossier_audio::SAMPLE_RATE.to_string(),
                "-ac",
                "2",
                "-i",
            ])
            .arg(pcm);
        hits = Some(command_input_index(&mut inputs));
    }

    if let Some(filter) = audio_filter(music, hits, &sync) {
        command.args(["-filter_complex", &filter, "-map", "0:v", "-map", "[a]"]);
        command.args(["-c:a", "aac", "-b:a", "192k"]);
        // The music outlasts the clip whenever only part of a map is rendered.
        command.arg("-shortest");
    }

    // x264 sizes its own thread pool at about 1.5 per core and knows nothing
    // about the drawing threads it is sharing the machine with. On a small box
    // that means both sides oversubscribe it and each one slows the other down.
    // Capping the encoder is the only way to divide the cores deliberately.
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
            // The frames arrive already converted, so the stream has to say
            // which convention they were converted under.
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
            preset: "veryfast".to_owned(),
            threads: None,
            encoder_threads: None,
            audio: None,
            hitsounds: None,
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

/// Inputs are numbered in the order they're given to ffmpeg, and the filter
/// graph refers to them by that number. Counting them as they're added keeps
/// the two from drifting apart.
fn command_input_index(count: &mut usize) -> usize {
    *count += 1;
    *count
}

/// One worker's pair of buffers: what it draws into, and what it sends.
///
/// The conversion to YUV happens here rather than inside ffmpeg for two
/// reasons, and both attack the same bottleneck. It cuts the bytes crossing the
/// pipe by nearly two thirds, and it takes the colour conversion off ffmpeg's
/// hands — where it runs on one thread, competing with the encoder for the very
/// cores the encoder is short of.
struct Frame {
    pixmap: Pixmap,
    yuv: Vec<u8>,
}

impl Frame {
    fn new(width: u32, height: u32) -> Option<Self> {
        Some(Self {
            pixmap: Pixmap::new(width, height)?,
            yuv: vec![0; yuv_len(width as usize, height as usize)],
        })
    }
}

/// Planar 4:2:0 is one byte of luma per pixel and one of each chroma per 2×2.
fn yuv_len(width: usize, height: usize) -> usize {
    width * height + 2 * (width / 2) * (height / 2)
}

/// Convert a drawn frame to BT.709 limited-range planar 4:2:0.
///
/// BT.709 because that is what a player assumes for anything HD, and limited
/// range because that is what `-color_range tv` on the encoder declares. The
/// two have to agree with the tags on the output stream or every colour comes
/// out shifted — quietly, and only for the viewer.
///
/// Chroma is averaged over each 2×2 block rather than point-sampled: taking one
/// pixel of four throws away three quarters of the colour and shows it on the
/// hard edges hit circles are made of.
///
/// The half added to each offset rounds rather than truncates. Truncation
/// costs half a unit on every channel of every pixel, for nothing.
fn to_yuv420(pixmap: &Pixmap, out: &mut [u8]) {
    // BT.709 limited range in 16-bit fixed point. Integers rather than floats
    // because this runs on every pixel of every frame, and the rounding is
    // exact enough that the difference against the float form is under a unit.
    const YR: i32 = 11_966;
    const YG: i32 = 40_254;
    const YB: i32 = 4_064;
    const UR: i32 = -6_595;
    const UG: i32 = -22_189;
    const UB: i32 = 28_784;
    const VR: i32 = 28_784;
    const VG: i32 = -26_142;
    const VB: i32 = -2_642;
    const HALF: i32 = 1 << 15;

    let (width, height) = (pixmap.width() as usize, pixmap.height() as usize);
    let src = pixmap.data();
    let (luma, chroma) = out.split_at_mut(width * height);
    let (blues, reds) = chroma.split_at_mut((width / 2) * (height / 2));

    // Iterating in whole rows lets the bounds checks fall away and the loop
    // vectorise; indexing pixel by pixel does neither.
    for (row, line) in luma.chunks_exact_mut(width).enumerate() {
        let pixels = &src[row * width * 4..(row + 1) * width * 4];
        for (out, rgba) in line.iter_mut().zip(pixels.chunks_exact(4)) {
            let (r, g, b) = (i32::from(rgba[0]), i32::from(rgba[1]), i32::from(rgba[2]));
            *out = (16 + ((YR * r + YG * g + YB * b + HALF) >> 16)) as u8;
        }
    }

    let half_width = width / 2;
    for pair in 0..height / 2 {
        let top = &src[pair * 2 * width * 4..(pair * 2 + 1) * width * 4];
        let bottom = &src[(pair * 2 + 1) * width * 4..(pair * 2 + 2) * width * 4];
        let u_row = &mut blues[pair * half_width..(pair + 1) * half_width];
        let v_row = &mut reds[pair * half_width..(pair + 1) * half_width];

        for (x, (u, v)) in u_row.iter_mut().zip(v_row.iter_mut()).enumerate() {
            // Averaged over the 2×2 block. Point-sampling one pixel of four
            // throws away three quarters of the colour, and it shows on the
            // hard edges hit circles are made of.
            let mut sums = [0i32; 3];
            for row in [top, bottom] {
                for dx in 0..2 {
                    let at = (x * 2 + dx) * 4;
                    sums[0] += i32::from(row[at]);
                    sums[1] += i32::from(row[at + 1]);
                    sums[2] += i32::from(row[at + 2]);
                }
            }
            let (r, g, b) = (sums[0] / 4, sums[1] / 4, sums[2] / 4);
            *u = (128 + ((UR * r + UG * g + UB * b + HALF) >> 16)) as u8;
            *v = (128 + ((VR * r + VG * g + VB * b + HALF) >> 16)) as u8;
        }
    }
}

/// How far the music is turned down under the hit sounds.
///
/// Not a taste setting. A hit is a transient of a few tens of milliseconds; a
/// modern master is continuous and pushed to the ceiling. At equal levels the
/// music wins every time, and the sounds that tell you what the player did go
/// unheard.
const MUSIC_DUCK: f32 = 0.55;

/// The filter graph joining music and hit sounds into one stream.
///
/// Returns `None` when there is no audio at all, in which case no audio
/// options are emitted and the result is a silent video rather than an ffmpeg
/// complaint about an empty graph.
fn audio_filter(music: Option<usize>, hits: Option<usize>, sync: &AudioSync) -> Option<String> {
    let stretched = |index: usize, duck: bool| {
        let mut chain = Vec::new();
        if let Some(tempo) = sync.filter() {
            chain.push(tempo);
        }
        if duck {
            chain.push(format!("volume={MUSIC_DUCK}"));
        }
        if chain.is_empty() {
            chain.push("anull".to_owned());
        }
        format!("[{index}:a]{}[m]", chain.join(","))
    };

    match (music, hits) {
        (Some(m), Some(h)) => Some(format!(
            // `normalize=0` matters: amix otherwise divides every input by the
            // number of them, so adding hit sounds would halve the music.
            "{};[m][{h}:a]amix=inputs=2:duration=first:normalize=0[a]",
            stretched(m, true)
        )),
        // Nothing to compete with, so the music keeps its own level.
        (Some(m), None) => Some(format!("{};[m]anull[a]", stretched(m, false))),
        (None, Some(h)) => Some(format!("[{h}:a]anull[a]")),
        (None, None) => None,
    }
}

#[cfg(test)]
mod filter_tests {
    use super::*;

    fn sync(rate: f64) -> AudioSync {
        AudioSync::new(0.0, rate)
    }

    #[test]
    fn music_alone_is_stretched_and_passed_through() {
        let filter = audio_filter(Some(1), None, &sync(1.5)).unwrap();
        assert!(filter.contains("[1:a]atempo=1.500000[m]"), "{filter}");
        assert!(filter.ends_with("[a]"));
    }

    #[test]
    fn the_two_streams_are_mixed_without_being_quietened() {
        // amix divides by the input count unless told not to, which would drop
        // the music by half the moment hit sounds were switched on.
        let filter = audio_filter(Some(1), Some(2), &sync(1.0)).unwrap();
        assert!(filter.contains("normalize=0"), "{filter}");
        assert!(filter.contains("amix=inputs=2"), "{filter}");
    }

    #[test]
    fn hit_sounds_are_never_stretched() {
        // They're built on the video's timebase, so the rate is already in
        // them; applying atempo again would double the correction.
        let filter = audio_filter(Some(1), Some(2), &sync(1.5)).unwrap();
        assert!(filter.contains("[1:a]atempo"), "{filter}");
        assert!(!filter.contains("[2:a]atempo"), "{filter}");
    }

    #[test]
    fn a_map_with_no_audio_at_all_emits_no_graph() {
        // An empty filter graph is an ffmpeg error, not a silent video.
        assert!(audio_filter(None, None, &sync(1.0)).is_none());
    }

    #[test]
    fn hit_sounds_can_stand_alone() {
        let filter = audio_filter(None, Some(1), &sync(1.0)).unwrap();
        assert_eq!(filter, "[1:a]anull[a]");
    }

    #[test]
    fn a_render_that_starts_before_the_song_holds_the_music_back() {
        // The replay records the lead-in, so the render begins before audio
        // zero. Seeking can't express that — a seek of −1.5s clamps to 0 and
        // starts the song on the first frame, playing the whole map a second
        // and a half early. Only a delay puts it where it belongs.
        let sync = AudioSync::new(-1500.0, 1.0);
        assert_eq!(sync.seek_seconds, 0.0);
        assert!((sync.delay_seconds - 1.5).abs() < 1e-9);
        assert!(sync.filter().unwrap().contains("adelay=1500:all=1"));
    }

    #[test]
    fn a_render_that_starts_inside_the_song_seeks_instead() {
        let sync = AudioSync::new(4000.0, 1.0);
        assert_eq!(sync.seek_seconds, 4.0);
        assert_eq!(sync.delay_seconds, 0.0);
        assert!(sync.filter().is_none(), "nothing to stretch or shift");
    }

    #[test]
    fn the_lead_in_is_measured_in_video_time() {
        // Under DoubleTime the video covers map time faster, so the same
        // lead-in occupies proportionally less of it. Delaying by the map-time
        // figure would leave the music a third of a second late.
        let sync = AudioSync::new(-1500.0, 1.5);
        assert!((sync.delay_seconds - 1.0).abs() < 1e-9);
    }

    #[test]
    fn the_music_is_stretched_before_it_is_shifted() {
        // adelay pads the front of the stream; atempo afterwards would scale
        // the padding too, and the music would land early again.
        let filter = AudioSync::new(-1500.0, 1.5).filter().unwrap();
        assert_eq!(filter, "atempo=1.500000,adelay=1000:all=1");
    }
}

/// How many threads to draw with when nobody said.
///
/// One fewer than the machine has, because ffmpeg is about to want a core and
/// starving the encoder just moves the queue rather than shortening it. On a
/// single-core box this still returns one, and the pipeline degenerates to
/// what it replaced.
fn default_workers() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get().saturating_sub(1).max(1))
        .unwrap_or(1)
}
