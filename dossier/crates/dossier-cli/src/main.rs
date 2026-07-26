//! `dossier` — the command-line front end.
//!
//! Right now it does one thing: judge replays and hold the result up against
//! the totals osu! itself wrote into the `.osr` header. That header is the only
//! ground truth available, so it's the only honest way to tell whether the
//! simulation is right — and it's what has to be right before a single frame of
//! video is worth drawing.

mod locate;
mod report;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use dossier_beatmap::Beatmap;
use dossier_render::{Layout, Scene, Skin};
use dossier_replay::{GameMode, Replay};
use dossier_sim::{GameState, Judgement, Part};

use report::{error_json, Header, Report};

const USAGE: &str = "\
dossier — osu! replay analysis

USAGE:
    dossier inspect [--json] <replay.osr>...
    dossier judge [OPTIONS] <replay.osr>...
    dossier sliders [OPTIONS] <replay.osr>...
    dossier errors [OPTIONS] <replay.osr>...
    dossier frame [OPTIONS] --at <ms> <replay.osr>

`inspect` reads the header alone — no map needed. Use it to learn which map a
replay wants before going and fetching it.

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
    -o, --out <path>     frame: where to write the PNG (default frame.png).
        --size <WxH>     frame: output size (default 1920x1080).
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
        "frame" => match Options::parse(&args[1..]) {
            Ok(options) => frame(options),
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
    strict: bool,
    at_ms: Option<f64>,
    out: PathBuf,
    size: (u32, u32),
}

impl Options {
    fn parse(args: &[String]) -> Result<Self, String> {
        let mut options = Self {
            replays: Vec::new(),
            map: None,
            songs: std::env::var_os("DOSSIER_SONGS_DIR").map(PathBuf::from),
            json: false,
            explain: false,
            strict: false,
            at_ms: None,
            out: PathBuf::from("frame.png"),
            size: (1920, 1080),
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
                "--strict" => options.strict = true,
                other if other.starts_with('-') => {
                    return Err(format!("unknown option `{other}`"));
                }
                path => options.replays.push(PathBuf::from(path)),
            }
        }

        if options.replays.is_empty() {
            return Err("no replay given".to_owned());
        }
        if options.map.is_some() && options.replays.len() > 1 {
            return Err("--map judges one replay; drop it and use --songs for a batch".to_owned());
        }
        Ok(options)
    }
}

fn judge(options: Options) -> ExitCode {
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
fn sliders(options: Options) -> ExitCode {
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

        for (index, object) in state.timeline().objects.iter().enumerate() {
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
        for (index, object) in state.timeline().objects.iter().enumerate() {
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
fn errors(options: Options) -> ExitCode {
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
    Ok((beatmap, replay))
}

fn read_header(replay_path: &Path) -> Result<Header, String> {
    let bytes = std::fs::read(replay_path).map_err(|e| format!("{e}"))?;
    let replay = Replay::parse(&bytes).map_err(|e| format!("{e}"))?;
    Ok(Header {
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
        our_accuracy: check.ours.accuracy_std(),
        their_accuracy: check.theirs.accuracy_std(),
        check,
        misses: state.explain_misses(),
        lenient_tails: state.lenient_tails(),
        tails_near_the_rim: state.tails_near_the_rim(),
        max_possible_combo: state.max_possible_combo(),
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
    let scene = Scene::new(&state, Skin::with_combo_colours(beatmap.combo_colours()));
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
