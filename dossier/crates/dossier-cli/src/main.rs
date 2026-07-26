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
use dossier_replay::{GameMode, Replay};
use dossier_sim::GameState;

use report::{error_json, Header, Report};

const USAGE: &str = "\
dossier — osu! replay analysis

USAGE:
    dossier inspect [--json] <replay.osr>...
    dossier judge [OPTIONS] <replay.osr>...

`inspect` reads the header alone — no map needed. Use it to learn which map a
replay wants before going and fetching it.

OPTIONS (judge):
    -m, --map <path>     .osu or .osz to judge against. Without it, --songs is
                         searched for the map the replay names by hash.
    -s, --songs <dir>    Directory to search (default: $DOSSIER_SONGS_DIR).
    -j, --json           One JSON object per replay, on its own line.
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
    })
}
