//! `dossier` — the command-line front end.
//!
//! Right now it does one thing: judge replays and hold the result up against
//! the totals osu! itself wrote into the `.osr` header. That header is the only
//! ground truth available, so it's the only honest way to tell whether the
//! simulation is right — and it's what has to be right before a single frame of
//! video is worth drawing.

mod debug;
mod hitsounds;
mod locate;
mod report;
mod video;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use dossier_beatmap::Beatmap;
use dossier_render::{Layout, Scene, Skin};
use dossier_replay::{GameMode, Replay};
use dossier_sim::{GameState, Judgement, Part, Ruleset, ScoreTrack};

use report::{error_json, Header, Report};

const USAGE: &str = "\
dossier — osu! replay analysis

USAGE:
    dossier inspect [--json] <replay.osr>...
    dossier judge [OPTIONS] <replay.osr>...
    dossier debug [OPTIONS] --from <ms> --to <ms> <replay.osr>
    dossier sliders [OPTIONS] <replay.osr>...
    dossier errors [OPTIONS] <replay.osr>...
    dossier score [OPTIONS] <replay.osr>...
    dossier frame [OPTIONS] --at <ms> <replay.osr>
    dossier video [OPTIONS] <replay.osr>
    dossier sounds [OPTIONS] [-o kit.wav]

`inspect` reads the header alone — no map needed. Use it to learn which map a
replay wants before going and fetching it.

`sounds` writes a short WAV of the hit sounds alone — every voice, then a fast
stream — so a kit can be listened to and retuned without rendering a video.

`debug` reads a window of the play back object by object and click by click,
with the difficulty numbers it used and, when the note lock refuses a click,
which note it is stuck on and every press that came near that note. It is the
last step down from a total that disagrees to the one verdict responsible.

`sliders` and `errors` are for when `judge` disagrees with the replay and the
question is where. The first breaks slider verdicts down by which part was
dropped; the second shows how hits pile up around the judgement windows, which
is where off-by-one hides.

OPTIONS (judge):
    -m, --map <path>     .osu or .osz to judge against. Without it, --songs is
                         searched for the map the replay names by hash.
    -s, --songs <dir>    Directory to search (default: $DOSSIER_SONGS_DIR).
    -j, --json           One JSON object per replay, on its own line.
    -a, --at <ms>        frame: the instant to draw, in map time.
        --fps <n>        video: frames per second (default 60).
        --from <ms>      video: start of the span, in map time.
        --to <ms>        video: end of the span. Both default to the whole play.
        --crf <n>        video: x264 quality, lower is better (default 20).
        --preset <name>  video: x264 preset (default veryfast). Faster presets
                         trade file size for speed, and once the encoder is the
                         bottleneck that trade is the main thing left to make.
        --mute           video: skip the map's audio.
        --ffmpeg <path>  video: the encoder to run (default `ffmpeg`).
    -o, --out <path>     frame: where to write the PNG (default frame.png).
        --size <WxH>     frame: output size (default 1920x1080).
        --threads <n>    video: threads drawing frames. Defaults to one fewer
                         than the machine has, leaving a core for the encoder.
        --encoder-threads <n>
                         video: cap the encoder's own threads. ffmpeg otherwise
                         takes about 1.5 per core and fights the drawing for
                         them. Tune it until the report's drawing-per-thread
                         and piping figures meet.
        --samples <dir>  sounds/video: a skin folder of `{set}-hit{sound}.wav`.
                         Whatever it lacks falls back to the synthesised kit.
        --kit <name>     sounds/video: click, soft, drum, glass, wood or 1984.
                         Overrides whatever the skin would have chosen.
        --pitch <x>      sounds/video: multiply every hit-sound frequency.
        --decay <x>      sounds/video: multiply every hit-sound decay.
        --level <x>      sounds/video: multiply hit-sound loudness.
        --skin <name>    `classic` (the map's own colours, default) or `1984`
                         (the bot's palette and a darker, drier hit kit).
        --font <path>    frame: typeface for the HUD and combo numbers.
                         Defaults to $DOSSIER_FONT, then the Torus face in the
                         repo. Without one the play is drawn but no numbers.
    -t, --trace          judge: account for every click — where each one went,
                         and where the note lock refused several in a row. With
                         --from/--to it also lists the clicks in that window one
                         by one, with the object each was tested against.
        --marginal <n>   judge: the n hits that came closest to not being hits,
                         ranked by the room they had against the window and the
                         radius. For when the totals say we credited objects
                         the game did not and nothing structural explains it.
    -e, --explain        List every object we called a miss, and what the input
                         says near it — the difference between a geometry bug
                         and a genuinely missed note.
        --strict         Exit non-zero when a replay doesn't match exactly.
    -h, --help           This text.
";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args[0] == "-h" || args[0] == "--help" {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    match args[0].as_str() {
        "judge" => match Options::parse(&args[1..]) {
            Ok(options) => judge(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "sounds" => match Options::parse(&args[1..]) {
            Ok(options) => sounds(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "video" => match Options::parse(&args[1..]) {
            Ok(options) => video_command(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "frame" => match Options::parse(&args[1..]) {
            Ok(options) => frame(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "score" => match Options::parse(&args[1..]) {
            Ok(options) => score_command(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "errors" => match Options::parse(&args[1..]) {
            Ok(options) => errors(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "debug" => match Options::parse(&args[1..]) {
            Ok(options) => debug_command(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "sliders" => match Options::parse(&args[1..]) {
            Ok(options) => sliders(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        "inspect" => match Options::parse(&args[1..]) {
            Ok(options) => inspect(options),
            Err(message) => {
                eprintln!("dossier: {message}\n\n{USAGE}");
                ExitCode::FAILURE
            }
        },
        other => {
            eprintln!("dossier: unknown command `{other}`\n\n{USAGE}");
            ExitCode::FAILURE
        }
    }
}

struct Options {
    replays: Vec<PathBuf>,
    map: Option<PathBuf>,
    songs: Option<PathBuf>,
    json: bool,
    explain: bool,
    trace: bool,
    marginal: Option<usize>,
    strict: bool,
    at_ms: Option<f64>,
    out: PathBuf,
    size: (u32, u32),
    font: Option<PathBuf>,
    fps: f64,
    from_ms: Option<f64>,
    to_ms: Option<f64>,
    crf: u32,
    preset: String,
    ffmpeg: String,
    mute: bool,
    skin: SkinChoice,
    kit: Option<dossier_audio::Kit>,
    samples: Option<PathBuf>,
    threads: Option<usize>,
    encoder_threads: Option<usize>,
    pitch: Option<f32>,
    decay: Option<f32>,
    level: Option<f32>,
}

/// Which house style to draw and sound in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SkinChoice {
    /// The map's own combo colours and a neutral hit kit.
    Classic,
    /// Dossier's own: the bot's palette, and a darker, drier set of sounds.
    NineteenEightyFour,
}

impl Options {
    /// The hit-sound kit: the skin's, with any explicit knobs applied on top.
    ///
    /// Overrides multiply rather than replace, so `--pitch 1.1` means "a tenth
    /// higher than this skin" regardless of which skin it is.
    fn kit(&self) -> dossier_audio::Kit {
        let mut kit = self.kit.unwrap_or_else(|| self.skin.kit());
        if let Some(pitch) = self.pitch {
            kit.pitch *= pitch;
        }
        if let Some(decay) = self.decay {
            kit.decay *= decay;
        }
        if let Some(level) = self.level {
            kit.level *= level;
        }
        kit
    }
}

impl Options {
    /// A skin's sounds, if one was pointed at.
    ///
    /// An empty result is reported rather than passed on silently: a wrong
    /// path and a skin with no files look identical from here, and finding out
    /// through a video that sounds unchanged is a poor way to learn it.
    fn samples(&self) -> dossier_audio::SamplePack {
        // An explicit path is an instruction: if it holds nothing, say so
        // rather than quietly substituting something else.
        if let Some(folder) = &self.samples {
            let pack = dossier_audio::SamplePack::load(folder);
            if pack.is_empty() {
                eprintln!(
                    "dossier: no `{{set}}-hit{{sound}}.wav` under {} — using the synthesised kit",
                    folder.display()
                );
            } else {
                eprintln!(
                    "dossier: {} sample(s) from {}",
                    pack.len(),
                    folder.display()
                );
            }
            return pack;
        }

        // Otherwise the skin's own folder, looked for from wherever the binary
        // happens to have been run — the same walk the font does.
        let Some(relative) = self.skin.samples_dir() else {
            return dossier_audio::SamplePack::default();
        };
        for prefix in ["", "../", "../../"] {
            let folder = PathBuf::from(format!("{prefix}{relative}"));
            let pack = dossier_audio::SamplePack::load(&folder);
            if !pack.is_empty() {
                eprintln!(
                    "dossier: {} sample(s) from {}",
                    pack.len(),
                    folder.display()
                );
                return pack;
            }
        }
        // Nothing there is not a problem: the synthesised kit is the fallback,
        // and saying so on every render would be noise.
        dossier_audio::SamplePack::default()
    }
}

fn parse_number(value: Option<&String>, flag: &str) -> Result<f32, String> {
    value
        .ok_or_else(|| format!("{flag} needs a number"))?
        .parse()
        .map_err(|_| format!("{flag} wants a number"))
}

impl SkinChoice {
    fn parse(name: &str) -> Result<Self, String> {
        match name.to_ascii_lowercase().as_str() {
            "classic" | "map" => Ok(Self::Classic),
            "1984" | "dossier" => Ok(Self::NineteenEightyFour),
            other => Err(format!("unknown skin `{other}` — try classic or 1984")),
        }
    }

    fn visual(self, beatmap: &Beatmap) -> Skin {
        match self {
            Self::Classic => Skin::with_combo_colours(beatmap.combo_colours()),
            Self::NineteenEightyFour => Skin::nineteen_eightyfour(),
        }
    }

    /// Where this skin keeps its samples, relative to the repository.
    ///
    /// The files aren't in the repository and won't be — they're community
    /// skins nobody licensed for redistribution. What is committed is the
    /// knowledge of where to look, which is enough: drop a skin's `.wav`s in
    /// and the sound follows, leave the folder empty and the synthesised kit
    /// covers it.
    fn samples_dir(self) -> Option<&'static str> {
        match self {
            Self::Classic => None,
            Self::NineteenEightyFour => Some("assets/hitsounds/1984"),
        }
    }

    fn kit(self) -> dossier_audio::Kit {
        match self {
            Self::Classic => dossier_audio::Kit::plain(),
            Self::NineteenEightyFour => dossier_audio::Kit::nineteen_eightyfour(),
        }
    }
}

impl Options {
    fn parse(args: &[String]) -> Result<Self, String> {
        let mut options = Self {
            replays: Vec::new(),
            map: None,
            songs: std::env::var_os("DOSSIER_SONGS_DIR").map(PathBuf::from),
            json: false,
            explain: false,
            trace: false,
            marginal: None,
            strict: false,
            at_ms: None,
            out: PathBuf::from("frame.png"),
            size: (1920, 1080),
            font: std::env::var_os("DOSSIER_FONT").map(PathBuf::from),
            fps: 60.0,
            from_ms: None,
            to_ms: None,
            crf: 20,
            preset: "veryfast".to_owned(),
            ffmpeg: std::env::var("DOSSIER_FFMPEG").unwrap_or_else(|_| "ffmpeg".to_owned()),
            mute: false,
            skin: SkinChoice::Classic,
            kit: None,
            samples: std::env::var_os("DOSSIER_SAMPLES").map(PathBuf::from),
            threads: None,
            encoder_threads: None,
            pitch: None,
            decay: None,
            level: None,
        };

        let mut rest = args.iter();
        while let Some(arg) = rest.next() {
            match arg.as_str() {
                "-m" | "--map" => {
                    options.map = Some(PathBuf::from(
                        rest.next().ok_or("--map needs a path")?.as_str(),
                    ));
                }
                "-s" | "--songs" => {
                    options.songs = Some(PathBuf::from(
                        rest.next().ok_or("--songs needs a path")?.as_str(),
                    ));
                }
                "-a" | "--at" => {
                    options.at_ms = Some(
                        rest.next()
                            .ok_or("--at needs a time in milliseconds")?
                            .parse()
                            .map_err(|_| "--at wants a number")?,
                    );
                }
                "--fps" => {
                    options.fps = rest
                        .next()
                        .ok_or("--fps needs a number")?
                        .parse()
                        .map_err(|_| "--fps wants a number")?;
                }
                "--from" => {
                    options.from_ms = Some(
                        rest.next()
                            .ok_or("--from needs a time")?
                            .parse()
                            .map_err(|_| "--from wants a number")?,
                    );
                }
                "--to" => {
                    options.to_ms = Some(
                        rest.next()
                            .ok_or("--to needs a time")?
                            .parse()
                            .map_err(|_| "--to wants a number")?,
                    );
                }
                "--preset" => {
                    options.preset = rest.next().ok_or("--preset needs a name")?.clone();
                }
                "--crf" => {
                    options.crf = rest
                        .next()
                        .ok_or("--crf needs a number")?
                        .parse()
                        .map_err(|_| "--crf wants a number")?;
                }
                "--skin" => {
                    options.skin = SkinChoice::parse(rest.next().ok_or("--skin needs a name")?)?;
                }
                "--threads" => {
                    options.threads = Some(
                        rest.next()
                            .ok_or("--threads needs a number")?
                            .parse()
                            .map_err(|_| "--threads wants a number")?,
                    );
                }
                "--encoder-threads" => {
                    options.encoder_threads = Some(
                        rest.next()
                            .ok_or("--encoder-threads needs a number")?
                            .parse()
                            .map_err(|_| "--encoder-threads wants a number")?,
                    );
                }
                "--samples" => {
                    options.samples = Some(PathBuf::from(
                        rest.next().ok_or("--samples needs a path")?.as_str(),
                    ));
                }
                "--kit" => {
                    let name = rest.next().ok_or("--kit needs a name")?;
                    options.kit = Some(dossier_audio::Kit::by_name(name).ok_or_else(|| {
                        format!("unknown kit `{name}` — try click, soft, drum, glass, wood or 1984")
                    })?);
                }
                "--pitch" => {
                    options.pitch = Some(parse_number(rest.next(), "--pitch")?);
                }
                "--decay" => {
                    options.decay = Some(parse_number(rest.next(), "--decay")?);
                }
                "--level" => {
                    options.level = Some(parse_number(rest.next(), "--level")?);
                }
                "--mute" => options.mute = true,
                "--ffmpeg" => {
                    options.ffmpeg = rest.next().ok_or("--ffmpeg needs a path")?.clone();
                }
                "--font" => {
                    options.font = Some(PathBuf::from(
                        rest.next().ok_or("--font needs a path")?.as_str(),
                    ));
                }
                "-o" | "--out" => {
                    options.out = PathBuf::from(rest.next().ok_or("--out needs a path")?.as_str());
                }
                "--size" => {
                    let raw = rest.next().ok_or("--size needs WxH")?;
                    let (w, h) = raw.split_once(['x', 'X']).ok_or("--size wants WxH")?;
                    options.size = (
                        w.parse().map_err(|_| "--size wants numbers")?,
                        h.parse().map_err(|_| "--size wants numbers")?,
                    );
                }
                "-j" | "--json" => options.json = true,
                "-e" | "--explain" => options.explain = true,
                "-t" | "--trace" => options.trace = true,
                "--marginal" => {
                    options.marginal = Some(
                        rest.next()
                            .ok_or("--marginal needs a count")?
                            .parse()
                            .map_err(|_| "--marginal needs a number")?,
                    );
                }
                "--strict" => options.strict = true,
                other if other.starts_with('-') => {
                    return Err(format!("unknown option `{other}`"));
                }
                path => options.replays.push(PathBuf::from(path)),
            }
        }

        // Whether a replay is needed depends on the command — `sounds` wants
        // only a kit — so each one checks for itself.
        if options.map.is_some() && options.replays.len() > 1 {
            return Err("--map judges one replay; drop it and use --songs for a batch".to_owned());
        }
        Ok(options)
    }
}

fn judge(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    let mut failures = 0usize;
    let mut mismatches = 0usize;
    let mut exact = 0usize;

    for replay_path in &options.replays {
        match run_one(replay_path, &options) {
            Ok(report) => {
                if report.is_exact() {
                    exact += 1;
                } else {
                    mismatches += 1;
                }
                if options.json {
                    println!("{}", report.json());
                } else {
                    print!("{}", report.human());
                    if options.explain && !report.is_exact() {
                        print!("{}", report.explain());
                    }
                    if let Some(n) = options.marginal {
                        print!("{}", report.marginal(n));
                    }
                    if options.trace {
                        let window = match (options.from_ms, options.to_ms) {
                            (None, None) => None,
                            (from, to) => Some((from.unwrap_or(f64::MIN), to.unwrap_or(f64::MAX))),
                        };
                        print!("{}", report.trace(window));
                    }
                    println!();
                }
            }
            Err(message) => {
                failures += 1;
                if options.json {
                    println!(
                        "{}",
                        error_json(&replay_path.display().to_string(), &message)
                    );
                } else {
                    println!("── {}\n   SKIPPED: {message}\n", replay_path.display());
                }
            }
        }
    }

    if !options.json && options.replays.len() > 1 {
        println!("{exact} exact, {mismatches} mismatched, {failures} skipped");
    }

    if failures > 0 || (options.strict && mismatches > 0) {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

/// Read headers only. Cheap, needs no beatmap, and tells a caller which map to
/// go and fetch before asking for a verdict.
fn inspect(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    let mut failures = 0usize;
    for replay_path in &options.replays {
        let name = replay_path.display().to_string();
        match read_header(replay_path) {
            Ok(header) => {
                if options.json {
                    println!("{}", header.json());
                } else {
                    println!("{}", header.human());
                }
            }
            Err(message) => {
                failures += 1;
                if options.json {
                    println!("{}", error_json(&name, &message));
                } else {
                    println!("── {name}\n   ERROR: {message}\n");
                }
            }
        }
    }
    if failures > 0 {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

/// Where our slider verdicts come from, in aggregate.
///
/// When the totals disagree only on the 300/100 split, the question is which
/// piece of a slider we're reading differently — and a histogram of dropped
/// parts answers that faster than staring at 1500 sliders one at a time.
/// Narrate a window of the judgement.
fn debug_command(options: Options) -> ExitCode {
    let Some(replay_path) = options.replays.first() else {
        eprintln!("dossier: debug needs a replay");
        return ExitCode::FAILURE;
    };
    let (Some(from), Some(to)) = (options.from_ms, options.to_ms) else {
        eprintln!("dossier: debug needs --from and --to — a whole replay is thousands of lines");
        return ExitCode::FAILURE;
    };

    let (beatmap, replay, source) = match load_found(replay_path, &options) {
        Ok(triple) => triple,
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    };
    let state = GameState::new(&beatmap, &replay);
    print!(
        "{}",
        debug::narrate(
            &replay_path.display().to_string(),
            &source,
            &beatmap,
            &replay,
            &state,
            (from, to),
        )
    );
    ExitCode::SUCCESS
}

fn sliders(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    for replay_path in &options.replays {
        let (beatmap, replay) = match load(replay_path, &options) {
            Ok(pair) => pair,
            Err(message) => {
                println!("── {}\n   SKIPPED: {message}\n", replay_path.display());
                continue;
            }
        };
        let state = GameState::new(&beatmap, &replay);
        let Some(judge) = state.judge() else {
            continue;
        };

        let mut verdicts = [0usize; 4];
        let mut dropped = [0usize; 4]; // head, tick, repeat, tail
        let mut imperfect_without_a_dropped_tail = 0usize;
        // A play that ended early never reached the rest of the map, and
        // listing those sliders as dropped would bury the ones it did play.
        let played = state.objects_played();

        for (index, object) in state.timeline().objects.iter().take(played).enumerate() {
            if !object.is_slider() {
                continue;
            }
            let mut verdict = Judgement::Great;
            let mut lost = [false; 4];
            for event in judge.events_for(index) {
                match event.part {
                    Part::Slider => verdict = event.result,
                    Part::SliderHead if event.result.is_miss() => lost[0] = true,
                    Part::SliderTick if event.result.is_miss() => lost[1] = true,
                    Part::SliderRepeat if event.result.is_miss() => lost[2] = true,
                    Part::SliderTail if event.result.is_miss() => lost[3] = true,
                    _ => {}
                }
            }

            verdicts[match verdict {
                Judgement::Great => 0,
                Judgement::Ok => 1,
                Judgement::Meh => 2,
                Judgement::Miss => 3,
            }] += 1;

            if verdict != Judgement::Great {
                for (slot, was_lost) in lost.iter().enumerate() {
                    if *was_lost {
                        dropped[slot] += 1;
                    }
                }
                if !lost[3] {
                    imperfect_without_a_dropped_tail += 1;
                }
            }
        }

        let total: usize = verdicts.iter().sum();
        println!("── {}", replay_path.display());
        println!(
            "   {total} sliders: {} × 300, {} × 100, {} × 50, {} × miss",
            verdicts[0], verdicts[1], verdicts[2], verdicts[3]
        );
        println!(
            "   parts dropped on the rest: head {}, tick {}, repeat {}, tail {}",
            dropped[0], dropped[1], dropped[2], dropped[3]
        );
        println!("   downgraded without losing the tail: {imperfect_without_a_dropped_tail}");
        println!(
            "   tails credited only by the grace window: {}, only near the rim: {}",
            state.lenient_tails(),
            state.tails_near_the_rim()
        );

        // The downgraded ones, in full. When the disagreement is down to a
        // handful of sliders, this is the list to read.
        for (index, object) in state.timeline().objects.iter().take(played).enumerate() {
            if !object.is_slider() {
                continue;
            }
            let verdict = judge
                .events_for(index)
                .find(|e| e.part == Part::Slider)
                .map(|e| e.result);
            if verdict == Some(Judgement::Great) || verdict.is_none() {
                continue;
            }
            let dropped: Vec<&str> = judge
                .events_for(index)
                .filter(|e| e.result.is_miss())
                .map(|e| match e.part {
                    Part::SliderHead => "head",
                    Part::SliderTick => "tick",
                    Part::SliderRepeat => "repeat",
                    _ => "tail",
                })
                .collect();
            let follow = state.difficulty().circle_radius() * 2.4;
            let mut trail = String::new();
            for offset in [-60.0, -48.0, -36.0, -24.0, -12.0, 0.0] {
                let t = object.end_ms + offset;
                let detail = match (object.ball_at(t), state.cursor_track().sample(t)) {
                    (Some(ball), Some(cursor)) => format!(
                        "{:+.0}ms {:.0}px{}",
                        offset,
                        cursor.pos.distance_to(ball),
                        if cursor.keys.is_pressed() { "" } else { " up" }
                    ),
                    _ => format!("{offset:+.0}ms —"),
                };
                trail.push_str(&format!("  {detail}"));
            }
            println!(
                "   #{index} at {:.0}ms, {:.0}ms long over {} slide(s) — lost {}",
                object.start_ms,
                object.duration_ms(),
                object.repeat_times().len() + 1,
                dropped.join(", ")
            );
            println!("      follow circle {follow:.0}px;{trail}");
            // Where the cursor actually was when each dropped part was tested.
            // The trail above only ever shows the run-in to the tail, which
            // says nothing about a tick lost in the middle of a long slide.
            for event in judge.events_for(index).filter(|e| e.result.is_miss()) {
                let name = match event.part {
                    Part::SliderHead => "head",
                    Part::SliderTick => "tick",
                    Part::SliderRepeat => "repeat",
                    Part::SliderTail => "tail",
                    _ => continue,
                };
                let at = if event.part == Part::SliderTail {
                    dossier_sim::tail_check_ms(object)
                } else {
                    event.time_ms
                };
                let detail = match (object.ball_at(at), state.cursor_track().sample(at)) {
                    (Some(ball), Some(cursor)) => format!(
                        "ball ({:.0},{:.0}), cursor {:.1}px away{}",
                        ball.x,
                        ball.y,
                        cursor.pos.distance_to(ball),
                        if cursor.keys.is_pressed() {
                            ""
                        } else {
                            ", button up"
                        }
                    ),
                    _ => "no ball or no cursor there".to_owned(),
                };
                println!("         {name} at {at:.0}ms — {detail}");
            }
        }
        println!();
    }
    ExitCode::SUCCESS
}

/// How circle hits pile up around the edges of the judgement windows.
///
/// A window is a threshold, and thresholds are where off-by-one lives. If the
/// disagreement with the replay equals the number of hits sitting exactly on a
/// boundary, the rule is inclusive on one side and shouldn't be.
/// Our score against the one the client wrote into the header.
///
/// The header is ground truth and the score is a pure function of the
/// judgement, so a replay whose totals match exactly and whose score does not
/// is telling us something specific about the arithmetic rather than about the
/// simulation.
fn score_command(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    let mut worst = 0f64;
    let mut counted = 0usize;
    for replay_path in &options.replays {
        let (beatmap, replay) = match load(replay_path, &options) {
            Ok(pair) => pair,
            Err(message) => {
                println!("── {}\n   SKIPPED: {message}\n", replay_path.display());
                continue;
            }
        };
        let state = GameState::new(&beatmap, &replay);
        let Some(judge) = state.judge() else {
            continue;
        };
        let ruleset = Ruleset::of_replay_version(replay.game_version);
        let track = ScoreTrack::build(judge, &beatmap, replay.mods, ruleset);

        let theirs = i64::from(replay.score);
        let ours = track.total() as i64;
        let off = if theirs > 0 {
            (ours - theirs) as f64 / theirs as f64 * 100.0
        } else {
            0.0
        };
        if theirs > 0 {
            worst = worst.max(off.abs());
            counted += 1;
        }
        println!("── {}", replay_path.display());
        let (flat, combo_units) = dossier_sim::score::stable_halves(judge);
        let mods = dossier_sim::score::stable_mod_multiplier(replay.mods);
        let fitted = if combo_units > 0.0 && mods > 0.0 {
            (theirs as f64 - flat) / combo_units / mods
        } else {
            f64::NAN
        };
        let drain = dossier_sim::score::drain_seconds(&beatmap);
        println!(
            "   {:?}  mods {:#x}  ×{}  ours {ours}  theirs {theirs}  off {off:+.2}%  fitted ×{fitted:.3}",
            ruleset,
            replay.mods.0,
            dossier_sim::score::difficulty_multiplier(&beatmap, beatmap.objects.len(), drain),
        );
        println!(
            "   HP {:.1} OD {:.1} CS {:.1}  objects {}  drain {drain:.1}s  density {:.2}  combo {}/{}",
            beatmap.difficulty.hp_drain,
            beatmap.difficulty.overall_difficulty,
            beatmap.difficulty.circle_size,
            beatmap.objects.len(),
            (beatmap.objects.len() as f64 / drain * 8.0).clamp(0.0, 16.0),
            judge.final_state().max_combo,
            replay.max_combo,
        );
    }
    if counted > 1 {
        println!("\nworst {worst:.2}% across {counted}");
    }
    ExitCode::SUCCESS
}

fn errors(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    for replay_path in &options.replays {
        let (beatmap, replay) = match load(replay_path, &options) {
            Ok(pair) => pair,
            Err(message) => {
                println!("── {}\n   SKIPPED: {message}\n", replay_path.display());
                continue;
            }
        };
        let state = GameState::new(&beatmap, &replay);
        let Some(judge) = state.judge() else {
            continue;
        };
        let difficulty = state.difficulty();

        let mut histogram = std::collections::BTreeMap::<i64, usize>::new();
        for event in judge.events() {
            if event.part != Part::Circle {
                continue;
            }
            if let Some(error) = event.error_ms {
                *histogram.entry(error.abs().round() as i64).or_default() += 1;
            }
        }

        println!("── {}", replay_path.display());
        println!(
            "   presses {}   objects {}   hit by the replay {}",
            state.press_count(),
            state.timeline().objects.len(),
            replay.hits.total_hits() - u32::from(replay.hits.count_miss),
        );
        println!(
            "   OD {:.1}  windows {:.0} / {:.0} / {:.0}",
            difficulty.overall_difficulty,
            difficulty.hit_window_300(),
            difficulty.hit_window_100(),
            difficulty.hit_window_50()
        );
        for (label, window) in [
            ("300", difficulty.hit_window_300()),
            ("100", difficulty.hit_window_100()),
        ] {
            let edge = window.round() as i64;
            let counts: Vec<String> = (edge - 2..=edge + 2)
                .map(|ms| format!("{ms}ms:{}", histogram.get(&ms).copied().unwrap_or(0)))
                .collect();
            println!("   around the {label} edge  {}", counts.join("  "));
        }
        println!();
    }
    ExitCode::SUCCESS
}

fn load(replay_path: &Path, options: &Options) -> Result<(Beatmap, Replay), String> {
    load_with_origin(replay_path, options).map(|(b, r, _)| (b, r))
}

/// Same, but keeping the human-readable note of where the map came from.
fn load_found(replay_path: &Path, options: &Options) -> Result<(Beatmap, Replay, String), String> {
    let bytes = std::fs::read(replay_path).map_err(|e| format!("{e}"))?;
    let replay = Replay::parse(&bytes).map_err(|e| format!("{e}"))?;
    let found = match &options.map {
        Some(path) => locate::load_map(path, &replay.beatmap_hash)?,
        None => {
            let songs = options
                .songs
                .as_ref()
                .ok_or("no --map and no --songs to search")?;
            locate::search_dir(songs, &replay.beatmap_hash)?
                .ok_or_else(|| format!("map {} not found", replay.beatmap_hash))?
        }
    };
    let beatmap = Beatmap::parse(&found.text).map_err(|e| format!("{e}"))?;
    Ok((beatmap, replay, found.source))
}

fn load_with_origin(
    replay_path: &Path,
    options: &Options,
) -> Result<(Beatmap, Replay, locate::Origin), String> {
    let bytes = std::fs::read(replay_path).map_err(|e| format!("{e}"))?;
    let replay = Replay::parse(&bytes).map_err(|e| format!("{e}"))?;
    let found = match &options.map {
        Some(path) => locate::load_map(path, &replay.beatmap_hash)?,
        None => {
            let songs = options
                .songs
                .as_ref()
                .ok_or("no --map and no --songs to search")?;
            locate::search_dir(songs, &replay.beatmap_hash)?
                .ok_or_else(|| format!("map {} not found", replay.beatmap_hash))?
        }
    };
    let beatmap = Beatmap::parse(&found.text).map_err(|e| format!("{e}"))?;
    Ok((beatmap, replay, found.origin))
}

/// Which client wrote a replay, for the report headers.
///
/// It is not a cosmetic label: stable and lazer judge differently, so this is
/// the line that says which ruleset the numbers underneath were read with.
fn client_name(replay: &Replay) -> String {
    let ruleset = dossier_sim::Ruleset::of_replay_version(replay.game_version);
    format!("{} {}", ruleset.name(), replay.game_version)
}

fn read_header(replay_path: &Path) -> Result<Header, String> {
    let bytes = std::fs::read(replay_path).map_err(|e| format!("{e}"))?;
    let replay = Replay::parse(&bytes).map_err(|e| format!("{e}"))?;
    Ok(Header {
        client: client_name(&replay),
        replay_path: replay_path.display().to_string(),
        player: replay.player.clone(),
        mode: format!("{:?}", replay.mode),
        mods: replay.mods.to_string(),
        beatmap_hash: replay.beatmap_hash.clone(),
        counts: replay.hits,
        max_combo: u32::from(replay.max_combo),
        frames: replay.frames.len(),
        duration_ms: replay.duration_ms(),
    })
}

fn run_one(replay_path: &Path, options: &Options) -> Result<Report, String> {
    let bytes = std::fs::read(replay_path).map_err(|e| format!("{e}"))?;
    let replay = Replay::parse(&bytes).map_err(|e| format!("{e}"))?;

    if replay.mode != GameMode::Standard {
        return Err(format!("{:?} replays aren't simulated yet", replay.mode));
    }
    if replay.frames.is_empty() {
        // Online-fetched replays sometimes carry only a header.
        return Err("replay has no frames".to_owned());
    }

    let found = match &options.map {
        Some(path) => locate::load_map(path, &replay.beatmap_hash)?,
        None => {
            let songs = options
                .songs
                .as_ref()
                .ok_or("no --map and no --songs to search")?;
            locate::search_dir(songs, &replay.beatmap_hash)?.ok_or_else(|| {
                format!(
                    "map {} not found under {}",
                    replay.beatmap_hash,
                    songs.display()
                )
            })?
        }
    };

    let beatmap = Beatmap::parse(&found.text).map_err(|e| format!("{e}"))?;
    let state = GameState::new(&beatmap, &replay);
    let check = state
        .verify(&replay)
        .ok_or("nothing to verify — no replay attached")?;

    Ok(Report {
        replay_path: replay_path.display().to_string(),
        map_source: found.source,
        title: format!(
            "{} - {} [{}]",
            beatmap.metadata.artist, beatmap.metadata.title, beatmap.metadata.version
        ),
        player: replay.player.clone(),
        mods: replay.mods.to_string(),
        objects: beatmap.object_count(),
        client: client_name(&replay),
        our_accuracy: check.ours.accuracy_std(),
        their_accuracy: check.theirs.accuracy_std(),
        check,
        misses: state.explain_misses(),
        lenient_tails: state.lenient_tails(),
        tails_near_the_rim: state.tails_near_the_rim(),
        max_possible_combo: state.max_possible_combo(),
        combo_chains: state.combo_chains(),
        combo_suspects: state.combo_break_suspects(u32::from(replay.max_combo)),
        presses: state.press_verdicts(),
        press_detail: state.press_detail(),
        window_50: state.difficulty().hit_window_50(),
    })
}

/// Draw one instant to a PNG.
///
/// A single frame is the smallest thing that can be looked at and judged by
/// eye, which makes it the right first output: video is this repeated, and
/// nothing about the repetition will fix a frame that is wrong.
fn frame(options: Options) -> ExitCode {
    let Some(at_ms) = options.at_ms else {
        eprintln!("dossier: frame needs --at <ms>");
        return ExitCode::FAILURE;
    };
    let Some(replay_path) = options.replays.first() else {
        eprintln!("dossier: frame needs a replay");
        return ExitCode::FAILURE;
    };

    let (beatmap, replay) = match load(replay_path, &options) {
        Ok(pair) => pair,
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    };

    let state = GameState::new(&beatmap, &replay);
    let mut skin = options.skin.visual(&beatmap);
    match load_font(options.font.as_deref()) {
        Ok(Some(font)) => skin = skin.with_font(font),
        Ok(None) => eprintln!("dossier: no font found — drawing without numbers"),
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    }
    let scene = Scene::new(&state, skin);
    let layout = Layout::new(options.size.0, options.size.1);
    let pixmap = scene.frame(at_ms, &layout);

    match pixmap
        .encode_png()
        .map_err(|e| e.to_string())
        .and_then(|png| std::fs::write(&options.out, png).map_err(|e| e.to_string()))
    {
        Ok(()) => {
            let score = state.update(at_ms).score;
            println!(
                "{} — {}ms, {}×{}{}",
                options.out.display(),
                at_ms,
                options.size.0,
                options.size.1,
                match score {
                    Some(s) => format!(", {}x {:.2}%", s.combo, s.accuracy()),
                    None => String::new(),
                }
            );
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("dossier: {message}");
            ExitCode::FAILURE
        }
    }
}

/// Find a typeface to draw numbers with.
///
/// An explicit path is an instruction and its failure is fatal. Without one we
/// look for the Torus face the project already ships — osu!'s own — and if that
/// isn't there either, say so and carry on: a frame with no numbers is still
/// worth looking at, and stopping over a font would be a poor trade.
fn load_font(explicit: Option<&Path>) -> Result<Option<dossier_render::Font>, String> {
    const FALLBACKS: [&str; 3] = [
        "assets/fonts/TorusNotched-Bold.ttf",
        "../assets/fonts/TorusNotched-Bold.ttf",
        "../../assets/fonts/TorusNotched-Bold.ttf",
    ];

    if let Some(path) = explicit {
        let bytes = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
        return dossier_render::Font::from_bytes(&bytes)
            .map(Some)
            .map_err(|e| format!("{}: {e}", path.display()));
    }

    for candidate in FALLBACKS {
        if let Ok(bytes) = std::fs::read(candidate) {
            if let Ok(font) = dossier_render::Font::from_bytes(&bytes) {
                return Ok(Some(font));
            }
        }
    }
    Ok(None)
}

/// Render a play to video.
fn video_command(options: Options) -> ExitCode {
    let Some(replay_path) = options.replays.first() else {
        eprintln!("dossier: video needs a replay");
        return ExitCode::FAILURE;
    };
    // The default output name is for `frame`; video wants a container.
    let out = if options.out == Path::new("frame.png") {
        PathBuf::from("replay.mp4")
    } else {
        options.out.clone()
    };
    if let Err(message) = video::check_output(&out) {
        eprintln!("dossier: {message}");
        return ExitCode::FAILURE;
    }

    let (beatmap, replay, origin) = match load_with_origin(replay_path, &options) {
        Ok(triple) => triple,
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    };

    let state = GameState::new(&beatmap, &replay);
    let mut skin = options.skin.visual(&beatmap);
    match load_font(options.font.as_deref()) {
        Ok(Some(font)) => skin = skin.with_font(font),
        Ok(None) => eprintln!("dossier: no font found — drawing without numbers"),
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    }

    // The unpacked track lives only as long as the render. Holding the guard
    // in scope is what keeps it on disk while ffmpeg reads it.
    let scratch = Scratch::new();
    let audio = if options.mute {
        None
    } else {
        let found = scratch
            .as_ref()
            .and_then(|dir| locate::extract_audio(&origin, &beatmap.audio_filename, dir));
        if found.is_none() {
            eprintln!("dossier: no audio track found — rendering silent");
        }
        found
    };

    // Hit sounds are built on the video's own timebase, so they need the same
    // span the encoder will use — worked out before anything is drawn.
    let probe = video::Settings {
        out: out.clone(),
        fps: options.fps,
        size: options.size,
        from_ms: options.from_ms,
        to_ms: options.to_ms,
        ffmpeg: options.ffmpeg.clone(),
        crf: options.crf,
        preset: options.preset.clone(),
        threads: options.threads,
        encoder_threads: options.encoder_threads,
        audio: audio.clone(),
        hitsounds: None,
    };
    let hitsounds = match video::Plan::new(
        state.span_ms(),
        state.playback_rate(),
        &probe,
        state.ending().map(|end| end.time_ms),
    ) {
        Ok(plan) if !options.mute => write_hitsounds(
            &state,
            &beatmap,
            &plan,
            state.playback_rate(),
            options.kit(),
            options.samples(),
            scratch.as_ref(),
        ),
        _ => None,
    };

    let scene = Scene::new(&state, skin);
    let settings = video::Settings {
        out,
        fps: options.fps,
        size: options.size,
        from_ms: options.from_ms,
        to_ms: options.to_ms,
        ffmpeg: options.ffmpeg.clone(),
        crf: options.crf,
        preset: options.preset.clone(),
        threads: options.threads,
        encoder_threads: options.encoder_threads,
        audio,
        hitsounds,
    };

    eprintln!(
        "{} — {} [{}], {} · {}",
        replay.player,
        beatmap.metadata.title,
        beatmap.metadata.version,
        replay.mods,
        settings.out.display()
    );

    match video::encode(
        &scene,
        state.span_ms(),
        state.playback_rate(),
        &settings,
        state.ending().map(|end| end.time_ms),
    ) {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("dossier: {message}");
            ExitCode::FAILURE
        }
    }
}

/// A temporary directory that clears itself up.
///
/// The audio has to exist as a file for the length of the render and not one
/// moment longer. Tying that to a value's lifetime means an early return or a
/// failed encode can't leave a hundred megabytes of someone's music behind.
struct Scratch(Option<PathBuf>);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!("dossier-{}", std::process::id()));
        Self(std::fs::create_dir_all(&path).ok().map(|()| path))
    }

    fn as_ref(&self) -> Option<&Path> {
        self.0.as_deref()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        if let Some(path) = &self.0 {
            let _ = std::fs::remove_dir_all(path);
        }
    }
}

/// Synthesise the hit sounds and leave them where ffmpeg can read them.
///
/// A failure here loses the hit sounds, not the render: the music and the video
/// are worth having on their own, and a missing scratch directory is not a
/// reason to refuse the whole job.
fn write_hitsounds(
    state: &GameState,
    beatmap: &Beatmap,
    plan: &video::Plan,
    rate: f64,
    kit: dossier_audio::Kit,
    pack: dossier_audio::SamplePack,
    scratch: Option<&Path>,
) -> Option<PathBuf> {
    let track = hitsounds::build(
        state,
        beatmap,
        plan.from_ms,
        rate,
        plan.video_seconds,
        kit,
        pack,
    );
    if track.is_empty() {
        return None;
    }
    let path = scratch?.join("hitsounds.pcm");
    std::fs::write(&path, track.to_pcm()).ok()?;
    Some(path)
}

/// Write a short WAV of the hit sounds alone.
///
/// Tuning a kit by rendering a video is a minute per idea, most of it spent on
/// pixels that aren't in question. This is under a second, and the sounds are
/// heard without music over them — which is how you tell what a sound *is*,
/// as opposed to whether it survives the mix.
fn sounds(options: Options) -> ExitCode {
    let kit = options.kit();
    let out = if options.out == Path::new("frame.png") {
        PathBuf::from("kit.wav")
    } else {
        options.out.clone()
    };

    let track = hitsounds::audition(kit, options.samples());
    match std::fs::write(&out, track.to_wav()) {
        Ok(()) => {
            println!(
                "{} — {:.1}s, pitch {:.2} decay {:.2} level {:.2}",
                out.display(),
                track.seconds(),
                kit.pitch,
                kit.decay,
                kit.level
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("dossier: {}: {error}", out.display());
            ExitCode::FAILURE
        }
    }
}
