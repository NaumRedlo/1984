//! `dossier` — the command-line front end.
//!
//! Right now it does one thing: judge replays and hold the result up against
//! the totals osu! itself wrote into the `.osr` header. That header is the only
//! ground truth available, so it's the only honest way to tell whether the
//! simulation is right — and it's what has to be right before a single frame of
//! video is worth drawing.

mod debug;
mod events;
mod exhibit;
mod hitsounds;
mod locate;
mod manifest;
mod reel;
mod report;
mod skinfile;
mod video;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use dossier_beatmap::Beatmap;
use dossier_render::elements::{Element, Health, Verdict};
use dossier_render::imported::Sprites;
use dossier_render::{Layout, Scene, Skin};

/// What a render draws, and so what is worth reading out of a skin folder.
///
/// Listed rather than derived from the enum: several of its members are for the
/// skin *exporter* and have no drawing code behind them yet, and reading files
/// nothing will use would be work done for a picture nobody sees.
const DRAWN_FROM_SKINS: &[Element] = &[
    Element::HitCircle,
    Element::HitCircleOverlay,
    Element::ApproachCircle,
    Element::ReverseArrow,
    Element::Cursor,
    Element::CursorMiddle,
    Element::CursorTrail,
    Element::Verdict(Verdict::Miss),
    Element::Verdict(Verdict::Fifty),
    Element::Verdict(Verdict::Hundred),
    Element::Verdict(Verdict::Three),
    // The ten combo digits. For an instafade skin these are the note itself,
    // so they are not optional decoration.
    Element::Digit(0),
    Element::Digit(1),
    Element::Digit(2),
    Element::Digit(3),
    Element::Digit(4),
    Element::Digit(5),
    Element::Digit(6),
    Element::Digit(7),
    Element::Digit(8),
    Element::Digit(9),
    // The slider's own furniture, its two ends included: osu! lets a skin draw
    // those differently from a note, and one that does looks half-applied
    // without them — the notes wear the skin and the sliders do not.
    Element::InputOverlayBackground,
    Element::InputOverlayKey,
    Element::FollowPoint,
    Element::Lighting,
    Element::SliderHead,
    Element::SliderHeadOverlay,
    Element::SliderTail,
    Element::SliderTailOverlay,
    Element::SliderBall,
    Element::SliderFollowCircle,
    Element::SliderScorePoint,
    // The spinner. `SpinnerBackground` is read for what its presence says
    // rather than to be drawn — it is how a skin declares which of osu!'s two
    // spinner styles it is drawn in.
    Element::SpinnerApproachCircle,
    Element::SpinnerCircle,
    Element::SpinnerMiddle,
    Element::SpinnerMiddle2,
    Element::SpinnerBackground,
    Element::SpinnerMetre,
    Element::SpinnerBottom,
    Element::SpinnerGlow,
    Element::SpinnerTop,
    // Read for what a blank one says — that the skin wants no read-out — rather
    // than to be drawn. The HUD still writes the figure in its own letters.
    Element::SpinnerRpm,
];

/// The skin's own HUD lettering: the figures in the corners, and the signs that
/// go with them. Built rather than listed because it is fourteen names of the
/// same shape.
/// The health bar's pieces, including all three of its marks.
fn scorebar_pieces() -> Vec<Element> {
    let mut all = vec![Element::ScoreBarBackground, Element::ScoreBarFill];
    all.extend(
        [Health::Fine, Health::Low, Health::Critical]
            .map(Element::ScoreBarMark),
    );
    all
}

fn hud_glyphs() -> Vec<Element> {
    ('0'..='9')
        .chain([',', '.', '%', 'x'])
        .map(Element::Score)
        .collect()
}

/// Everything worth reading out of a skin folder.
fn wanted_from_skins() -> Vec<Element> {
    let mut all = DRAWN_FROM_SKINS.to_vec();
    all.extend(hud_glyphs());
    all.extend(scorebar_pieces());
    all
}
use dossier_replay::{GameMode, Replay};
use dossier_sim::{GameState, Judgement, Part, Ruleset};

use report::{error_json, Header, PartCheck, Report};

const USAGE: &str = "\
dossier — osu! replay analysis

USAGE:
    dossier inspect [--json] <replay.osr>...
    dossier judge [OPTIONS] <replay.osr>...
    dossier corpus [OPTIONS] <replay.osr>...
    dossier debug [OPTIONS] --from <ms> --to <ms> <replay.osr>
    dossier sliders [OPTIONS] <replay.osr>...
    dossier errors [OPTIONS] <replay.osr>...
    dossier score [OPTIONS] <replay.osr>...
    dossier health [OPTIONS] <replay.osr>...
    dossier frame [OPTIONS] --at <ms> <replay.osr>
    dossier video [OPTIONS] <replay.osr>
    dossier exhibit [OPTIONS] <replay.osr>
    dossier exhibit --survey [OPTIONS] <replay.osr>...
    dossier sounds [OPTIONS] [-o kit.wav]
    dossier skin [OPTIONS] -o <folder>

`inspect` reads the header alone — no map needed. Use it to learn which map a
replay wants before going and fetching it.

`exhibit` picks the few seconds of a play that say something about it, and
says why each was chosen. Unlike everything else here it has no ground truth
to be checked against — no header names the moments worth watching — so it
answers in reasons rather than in numbers, and `--json` shows the whole answer
without rendering a frame. A reel is as long as the play gives it reason to be:
selection stops when nothing left is worth the seconds it would cost, so a
clean run of a quiet map comes out short and a disaster on a marathon does not. With `-o` it renders the chosen clips and cuts them
together, crossfading each into the next and fading from and to black.

`sounds` writes a short WAV of the hit sounds alone — every voice, then a fast
stream — so a kit can be listened to and retuned without rendering a video.

`debug` reads a window of the play back object by object and click by click,
with the difficulty numbers it used and, when the note lock refuses a click,
which note it is stuck on and every press that came near that note. It is the
last step down from a total that disagrees to the one verdict responsible.

`corpus` is the measurement every change is judged by: one line per replay
that disagrees, and a total. With `--strict <n>` it fails when that total is
worse than n, which is what makes it a check rather than a report.

`--expect tools/corpus.tsv` goes further: that file names which replays the
corpus is, by hash, and what each one is expected to do. It catches what a
total cannot — a replay that got worse while another got better, a replay
this machine does not have, the same file counted twice from two folders.
`--update-expect` writes what this run measured into it, leaving the rows for
replays it did not see alone; `--prune` is how one of those rows leaves.

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
    -o, --out <path>     exhibit: the reel. Without it the selection is printed
                         and nothing is rendered.
        --for <s>        exhibit: the most video it may come to (default 120).
                         A ceiling, not a target — a reel is as long as the play
                         gives it reason to be.
        --survey         exhibit: aggregate over every replay given instead of
                         answering about one — what reels are made of, how long
                         they run, and how many say nothing about the play.
                         Selection has no ground truth, so this stands in for
                         one: a change cannot be shown to be right, only what it
                         did to a hundred replays.
        --worth <0..1>   exhibit: the score under which a moment is not worth
                         the seconds it costs (default 0.25). This is what
                         decides how long a reel is. Lower it for a longer reel
                         of weaker moments.
        --clip <s>       exhibit: shortest clip, in seconds (default 6). The
                         more important a moment, the longer its clip runs.
        --fps <n>        video: frames per second (default 60).
        --from <ms>      video: start of the span, in map time.
        --to <ms>        video: end of the span. Both default to the whole play.
        --crf <n>        video: x264 quality, lower is better (default 20).
        --preset <name>  video: x264 preset (default veryfast). Faster presets
                         trade file size for speed, and once the encoder is the
                         bottleneck that trade is the main thing left to make.
        --events         video/exhibit: say what the render is doing on stdout,
                         one JSON object per line — which clip is being drawn,
                         how far along its frames are, the shape of the file
                         and where it was written. For a caller that would
                         otherwise have to read the prose on stderr, which is
                         written for a person and changes like prose does.
        --mute           video: skip the map's audio.
        --ffmpeg <path>  video: the encoder to run (default `ffmpeg`).
    -o, --out <path>     frame: where to write the PNG (default frame.png).
        --size <WxH>     frame: output size (default 1920x1080).
        --threads <n>    video: threads drawing frames. Defaults to one fewer
                         than the machine has, leaving a core for the encoder.
                         corpus: threads judging replays, one replay each.
                         Defaults to every core — nothing waits on an encoder
                         here. `--threads 1` if a measurement has to be watched
                         happening.
        --encoder-threads <n>
                         video: cap the encoder's own threads. ffmpeg otherwise
                         takes about 1.5 per core and fights the drawing for
                         them. Tune it until the report's drawing-per-thread
                         and piping figures meet.
        --samples <dir>  sounds/video: a skin folder of `{set}-hit{sound}.wav`.
                         Whatever it lacks falls back to the synthesised kit.
        --kit <name>     sounds/video: click, soft, drum, glass or wood.
                         Overrides whatever the skin would have chosen.
        --pitch <x>      sounds/video: multiply every hit-sound frequency.
        --decay <x>      sounds/video: multiply every hit-sound decay.
        --level <x>      sounds/video: multiply hit-sound loudness.
        --bare           frame/video/exhibit: draw the play and nothing that
                         talks about it — no score, accuracy, combo, key
                         counters, scoreboard or signature. For a clip that has
                         to stand beside somebody's own footage rather than
                         explain itself. The red of a dying play stays: that is
                         the screen reacting, not a readout.
        --skin <name>    `1984` (the default: the bot's palette and a darker,
                         drier hit kit) or `classic` (the map's own combo
                         colours and a neutral kit).
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
        --leaderboard <tsv>
                         video/frame: who else has played this map, drawn down
                         the left, climbing to the leader. A line each:
                         `name<TAB>score[<TAB>accuracy<TAB>mods<TAB>avatar.png<TAB>cover.png]`.
                         The player's own row is computed, not read. Pictures
                         must be PNG — the engine has one decoder and no
                         network.
        --my-pictures <avatar.png> <cover.png>
                         video/frame: the player's own avatar and cover, which
                         no rival line can carry.
        --expect <tsv>   corpus: the file naming the corpus and what each
                         replay in it does. Fails on any replay that got worse,
                         and with --strict on any that is missing here.
        --update-expect  corpus: write what this run measured into that file.
                         A replay it did not see keeps the row it had — the
                         corpus is a list of replays this machine may or may
                         not be holding today, and a partial run is not news
                         that the rest of it is gone.
        --prune          corpus: with --update-expect, drop the rows this run
                         did not see. For a replay that has left the corpus,
                         which is not the same thing as one that is elsewhere.
        --strict [n]     corpus: fail when the total count error is worse than
                         n. Without a number, judge: fail on any mismatch.
    -e, --explain        List every object we called a miss, and what the input
                         says near it — the difference between a geometry bug
                         and a genuinely missed note.
        --strict         Exit non-zero when a replay doesn't match exactly.
    -h, --help           This text.
";

/// The twelve subcommands, each of which reads its own slice of the shared
/// options.
///
/// The parser used to take every option for every command — `dossier judge
/// --crf 18` was accepted and the crf silently ignored, and `dossier judge
/// --help` was rejected as an unknown option, because the one flat match knew
/// nothing about which command it was serving. Naming the command lets the
/// parser refuse an option a command has no use for, and lets `--help` answer
/// for one command instead of printing the manual for all twelve.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Command {
    Inspect,
    Judge,
    Corpus,
    Debug,
    Sliders,
    Errors,
    Score,
    Health,
    Frame,
    Video,
    Exhibit,
    Sounds,
    Skin,
}

impl Command {
    fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "inspect" => Self::Inspect,
            "judge" => Self::Judge,
            "corpus" => Self::Corpus,
            "debug" => Self::Debug,
            "sliders" => Self::Sliders,
            "errors" => Self::Errors,
            "score" => Self::Score,
            "health" => Self::Health,
            "frame" => Self::Frame,
            "video" => Self::Video,
            "exhibit" => Self::Exhibit,
            "sounds" => Self::Sounds,
            "skin" => Self::Skin,
            _ => return None,
        })
    }

    fn name(self) -> &'static str {
        match self {
            Self::Inspect => "inspect",
            Self::Judge => "judge",
            Self::Corpus => "corpus",
            Self::Debug => "debug",
            Self::Sliders => "sliders",
            Self::Errors => "errors",
            Self::Score => "score",
            Self::Health => "health",
            Self::Frame => "frame",
            Self::Video => "video",
            Self::Exhibit => "exhibit",
            Self::Sounds => "sounds",
            Self::Skin => "skin",
        }
    }

    /// The one-line shape of the command, as the manual lists it.
    fn synopsis(self) -> &'static str {
        match self {
            Self::Inspect => "dossier inspect [--json] <replay.osr>...",
            Self::Judge => "dossier judge [OPTIONS] <replay.osr>...",
            Self::Corpus => "dossier corpus [OPTIONS] <replay.osr>...",
            Self::Debug => "dossier debug [OPTIONS] --from <ms> --to <ms> <replay.osr>",
            Self::Sliders => "dossier sliders [OPTIONS] <replay.osr>...",
            Self::Errors => "dossier errors [OPTIONS] <replay.osr>...",
            Self::Score => "dossier score [OPTIONS] <replay.osr>...",
            Self::Health => "dossier health [OPTIONS] <replay.osr>...",
            Self::Frame => "dossier frame [OPTIONS] --at <ms> <replay.osr>",
            Self::Video => "dossier video [OPTIONS] <replay.osr>",
            Self::Exhibit => "dossier exhibit [OPTIONS] <replay.osr>",
            Self::Sounds => "dossier sounds [OPTIONS] [-o kit.wav]",
            Self::Skin => "dossier skin [OPTIONS] -o <folder>",
        }
    }

    /// Does this command have any use for `flag`, given by its canonical long
    /// name? The one place that knows, so the parser and the per-command help
    /// agree by construction.
    fn accepts(self, flag: &str) -> bool {
        // The map a replay is judged against — every command that loads one.
        const MAP: &[&str] = &["--map", "--songs"];
        // How a frame looks, shared by `frame`, `video` and `exhibit`.
        const LOOK: &[&str] = &[
            "--out",
            "--size",
            "--font",
            "--skin",
            "--bare",
            "--leaderboard",
            "--my-pictures",
        ];
        // How a video is encoded, shared by `video` and `exhibit`.
        const ENCODE: &[&str] = &[
            "--fps",
            "--crf",
            "--preset",
            "--mute",
            "--ffmpeg",
            "--threads",
            "--encoder-threads",
            "--events",
            "--from",
            "--to",
        ];
        // The hit-sound kit, shared by `sounds`, `video` and `exhibit`.
        const HITSOUND: &[&str] = &["--samples", "--kit", "--pitch", "--decay", "--level"];

        let groups: &[&[&str]] = match self {
            Self::Inspect => &[&["--json"]],
            Self::Judge => &[
                MAP,
                &["--json", "--explain", "--trace", "--marginal", "--strict", "--from", "--to"],
            ],
            Self::Corpus => &[MAP, &["--expect", "--update-expect", "--prune", "--strict", "--threads"]],
            Self::Debug => &[MAP, &["--from", "--to"]],
            Self::Sliders | Self::Errors | Self::Score => &[MAP],
            Self::Health => &[MAP, &["--trace"]],
            Self::Frame => &[MAP, LOOK, &["--at", "--background"]],
            Self::Video => &[MAP, LOOK, ENCODE, HITSOUND, &["--slow", "--background"]],
            Self::Exhibit => &[
                MAP,
                LOOK,
                &["--background"],
                // `exhibit` encodes like `video` but chooses its own spans, so
                // it takes the encode options save the two that name a span.
                &["--fps", "--crf", "--preset", "--mute", "--ffmpeg", "--threads", "--encoder-threads", "--events"],
                HITSOUND,
                &["--json", "--for", "--worth", "--clip", "--survey"],
            ],
            Self::Sounds => &[HITSOUND, &["--out"]],
            // The palette comes from `--skin`, the sounds from `--samples`, and
            // the folder to write from `-o`. Nothing else applies: this draws
            // no frame and judges no replay.
            Self::Skin => &[&["--out", "--skin", "--samples", "--font"]],
        };
        groups.iter().any(|group| group.contains(&flag))
    }

    /// The command's own help: what it looks like, and the options it has —
    /// drawn from the one table so it never drifts from what `accepts` allows.
    fn help(self) -> String {
        let mut out = format!("{}\n\nOptions:\n", self.synopsis());
        for (flag, value, summary) in OPTIONS_TABLE {
            if self.accepts(flag) {
                let head = if value.is_empty() {
                    (*flag).to_owned()
                } else {
                    format!("{flag} {value}")
                };
                out.push_str(&format!("    {head:<24} {summary}\n"));
            }
        }
        out.push_str("    -h, --help               this text\n");
        out
    }
}

/// The long name a flag is known by, so `-m` and `--map` are one thing to the
/// gate and the help.
fn canonical(flag: &str) -> &str {
    match flag {
        "-m" => "--map",
        "-s" => "--songs",
        "-a" => "--at",
        "-o" => "--out",
        "-j" => "--json",
        "-e" => "--explain",
        "-t" => "--trace",
        other => other,
    }
}

/// Every option, in the order help lists them: the canonical flag, its value
/// placeholder, and a one-line summary. The single source a per-command help is
/// built from — [`Command::accepts`] picks which rows each command shows.
const OPTIONS_TABLE: &[(&str, &str, &str)] = &[
    ("--map", "<path>", "the .osu or .osz to judge against (short -m)"),
    ("--songs", "<dir>", "where to search for the map (short -s; $DOSSIER_SONGS_DIR)"),
    ("--json", "", "one JSON object per replay, on its own line (short -j)"),
    ("--explain", "", "list every miss and what the input says near it (short -e)"),
    ("--trace", "", "account for every click; with --from/--to, list that window (short -t)"),
    ("--marginal", "<n>", "the n hits that came closest to not being hits"),
    ("--strict", "[n]", "fail on a mismatch; with n, fail when the corpus total is worse"),
    ("--expect", "<tsv>", "the corpus manifest to check against"),
    ("--update-expect", "", "write what this run measured into the manifest"),
    ("--prune", "", "with --update-expect, drop rows this run did not see"),
    ("--threads", "<n>", "threads drawing frames, or judging replays for corpus"),
    ("--at", "<ms>", "the instant to draw, in map time"),
    ("--slow", "<ms>", "a map instant to slow into and back out of"),
    ("--background", "", "draw the map's own artwork behind the play"),
    ("--from", "<ms>", "start of the span, in map time"),
    ("--to", "<ms>", "end of the span, in map time"),
    ("--for", "<s>", "the most video a reel may come to (a ceiling, not a target)"),
    ("--worth", "<0..1>", "the score under which a moment is not worth its seconds"),
    ("--clip", "<s>", "shortest clip, in seconds"),
    ("--survey", "", "aggregate over every replay instead of answering about one"),
    ("--out", "<path>", "where to write the output (short -o)"),
    ("--size", "<WxH>", "output size"),
    ("--fps", "<n>", "frames per second"),
    ("--crf", "<n>", "x264 quality, lower is better"),
    ("--preset", "<name>", "x264 preset"),
    ("--mute", "", "skip the map's audio"),
    ("--ffmpeg", "<path>", "the encoder to run"),
    ("--encoder-threads", "<n>", "cap the encoder's own threads"),
    ("--events", "", "report what the render is doing on stdout, as JSON lines"),
    ("--skin", "<name>", "`1984` (default) or `classic`"),
    ("--bare", "", "draw the play and nothing that talks about it"),
    ("--font", "<path>", "typeface for the HUD ($DOSSIER_FONT)"),
    ("--leaderboard", "<tsv>", "who else has played this map, down the left"),
    ("--my-pictures", "<a> <c>", "the player's own avatar and cover"),
    ("--samples", "<dir>", "a skin folder of hit-sound WAVs"),
    ("--kit", "<name>", "click, soft, drum, glass or wood"),
    ("--pitch", "<x>", "multiply every hit-sound frequency"),
    ("--decay", "<x>", "multiply every hit-sound decay"),
    ("--level", "<x>", "multiply hit-sound loudness"),
];

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() || args[0] == "-h" || args[0] == "--help" {
        print!("{USAGE}");
        return ExitCode::SUCCESS;
    }
    let Some(command) = Command::from_name(&args[0]) else {
        eprintln!("dossier: unknown command `{}`\n\n{USAGE}", args[0]);
        return ExitCode::FAILURE;
    };
    let rest = &args[1..];
    // A command's own `--help` answers for that command, not for all twelve.
    if rest.iter().any(|a| a == "-h" || a == "--help") {
        print!("{}", command.help());
        return ExitCode::SUCCESS;
    }
    match Options::parse(command, rest) {
        Ok(options) => dispatch(command, options),
        Err(message) => {
            // The command's own help, not the manual: the mistake was in one
            // command's options, and that is the list worth showing.
            eprintln!("dossier: {message}\n\n{}", command.help());
            ExitCode::FAILURE
        }
    }
}

fn dispatch(command: Command, options: Options) -> ExitCode {
    match command {
        Command::Corpus => corpus(options),
        Command::Judge => judge(options),
        Command::Sounds => sounds(options),
        Command::Video => video_command(options),
        Command::Exhibit => exhibit_command(options),
        Command::Frame => frame(options),
        Command::Health => health_command(options),
        Command::Score => score_command(options),
        Command::Errors => errors(options),
        Command::Debug => debug_command(options),
        Command::Sliders => sliders(options),
        Command::Inspect => inspect(options),
        Command::Skin => skin_command(options),
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
    /// corpus: the total count error this run is held to.
    corpus_ceiling: Option<u32>,
    /// corpus: the file naming which replays the corpus is and what each does.
    expect: Option<PathBuf>,
    /// corpus: write what this run measured into that file instead of checking
    /// against it. Rows for replays this run did not see are left as they are.
    update_expect: bool,
    /// corpus: and drop those rows instead of leaving them — for when a replay
    /// has left the corpus rather than merely left this machine.
    prune: bool,
    /// video/exhibit: report what the render is doing on stdout, one JSON
    /// object per line, for a caller that is not a person reading stderr.
    events: bool,
    /// video/frame: rivals to stand the play against, down the left of the
    /// frame. One line each, tab-separated.
    leaderboard: Option<PathBuf>,
    /// video/frame: the player's own pictures, which no rival line can carry.
    my_avatar: Option<PathBuf>,
    my_cover: Option<PathBuf>,
    at_ms: Option<f64>,
    /// video: a map instant to slow into and back out of.
    slow_at_ms: Option<f64>,
    /// video/frame: draw the map's own artwork behind the play.
    background: bool,
    /// exhibit: the most video it may come to, in seconds. A ceiling, not a
    /// target — how long a reel should be is a property of the play.
    exhibit_budget_s: Option<f64>,
    /// exhibit: the score under which a moment is not worth its seconds.
    exhibit_worth: Option<f64>,
    /// exhibit: aggregate over many replays instead of answering about one.
    survey: bool,
    /// Draw the play and nothing that talks about it.
    bare: bool,
    /// exhibit: how long one clip is, in seconds.
    exhibit_clip_s: Option<f64>,
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

/// Which look to draw and sound in.
///
/// One entry, for now. It was two: the engine carried a house style of its own,
/// on the bot's palette. That was a look designed for a bot's cards rather than
/// for a game, and next to a real osu! skin it read as a different program —
/// so it is gone, and what replaces it is the ability to load the skins players
/// actually use. This enum stays because that is where they will arrive.
#[derive(Debug, Clone, PartialEq, Eq)]
enum SkinChoice {
    /// A folder of the player's own skin files.
    ///
    /// What the engine cannot find there it still draws itself, element by
    /// element, so a folder with two pictures in it is as valid as a complete
    /// skin. The colours and the hit sounds come from it too, when it has them.
    Folder(PathBuf),
    /// The map's own combo colours and a neutral hit kit.
    ///
    /// Named "classic" because that is what it is *for*, and it is not there
    /// yet: a real recreation of osu!'s own look is its own piece of work. What
    /// this is today is the engine's neutral fallback, which is honest as a
    /// fallback and would be a poor way to introduce the engine to anybody.
    Classic,
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
    /// Where this run's hit-sound `.wav`s are, if they are anywhere.
    ///
    /// The folder rather than the loaded pack: writing a skin copies the files
    /// themselves, and it has to look in the same places a render does or the
    /// skin would ship different sounds from the videos.
    fn samples_folder(&self) -> Option<PathBuf> {
        if let Some(folder) = &self.samples {
            return folder.is_dir().then(|| folder.clone());
        }
        let named = self.skin.samples_dir()?;
        if named.is_dir() {
            // A skin folder the caller pointed at, which needs no searching.
            return Some(named.to_path_buf());
        }
        ["", "../", "../../"]
            .iter()
            .map(|prefix| PathBuf::from(format!("{prefix}{}", named.display())))
            .find(|folder| folder.is_dir())
    }

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
            let folder = PathBuf::from(format!("{prefix}{}", relative.display()));
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
        // A path wins over a name, and is recognised by being a folder that
        // exists. Nothing else could be: the named skins have no separators in
        // them, and a folder called `classic` next to the binary would be a
        // stranger coincidence than it is worth guarding against.
        let path = Path::new(name);
        if path.is_dir() {
            return Ok(Self::Folder(path.to_path_buf()));
        }
        match name.to_ascii_lowercase().as_str() {
            "classic" | "map" => Ok(Self::Classic),
            // Named plainly rather than dismissed: a deployment that still
            // asks for the house skin should be told it is gone, not told its
            // spelling is wrong.
            "1984" | "dossier" => Err(
                "the `1984` skin was removed — use `classic`, or import a skin".to_owned(),
            ),
            other => Err(format!(
                "unknown skin `{other}` — try classic, or the path to a skin folder"
            )),
        }
    }

    fn visual(&self, beatmap: &Beatmap) -> Skin {
        let mut skin = Skin::with_combo_colours(beatmap.combo_colours());
        if let Self::Folder(path) = self {
            skin = dress(skin, path);
        }
        skin
    }

    /// The same look, with no map to take combo colours from.
    ///
    /// A skin written to disk is not about to be played on any one beatmap, so
    /// `classic` falls back to osu!'s own default cycle — which is exactly what
    /// that skin means when the map has nothing to say.
    fn visual_default(&self) -> Skin {
        let mut skin = Skin::default();
        if let Self::Folder(path) = self {
            skin = dress(skin, path);
        }
        skin
    }

    /// Where this skin keeps its samples, relative to the repository.
    ///
    /// Nowhere, now that the one skin that had its own is gone. Kept as the
    /// seam an imported skin will answer through: a real skin carries its own
    /// `.wav`s, and this is where the renderer will ask for them.
    fn samples_dir(&self) -> Option<&Path> {
        match self {
            // A real skin keeps its `.wav`s beside its pictures, so pointing
            // the sample reader at the same folder is the whole of importing
            // its sounds — that half of a skin already worked before any of
            // this was written.
            Self::Folder(path) => Some(path),
            Self::Classic => None,
        }
    }

    fn kit(&self) -> dossier_audio::Kit {
        match self {
            // Whatever the folder does not cover falls back to synthesis, so a
            // skin missing a `drum-hitclap.wav` still sounds like something.
            Self::Folder(_) | Self::Classic => dossier_audio::Kit::plain(),
        }
    }
}

/// Put a folder's skin on: pictures and settings both.
///
/// Order matters once, and it is easy to get wrong. A skin's `skin.ini` may
/// state combo colours of its own — the one this was written against paints
/// every combo white — and the tinted copies have to be made from *those*
/// rather than from the map's. So the palette is settled before anything is
/// coloured.
fn dress(mut skin: Skin, path: &Path) -> Skin {
    let sprites = Sprites::read(path, &wanted_from_skins());
    let ini = sprites.ini().clone();
    if !ini.combo_colours.is_empty() {
        skin.combo_colours = ini.combo_colours.clone();
    }
    if let Some(border) = ini.slider_border {
        skin.slider_border = border;
    }
    skin.slider_body = ini.slider_track;
    skin.sprites = Some(std::sync::Arc::new(sprites.tint_for(&skin.combo_colours)));
    skin
}

impl Options {
    fn parse(command: Command, args: &[String]) -> Result<Self, String> {
        let mut options = Self {
            replays: Vec::new(),
            map: None,
            songs: std::env::var_os("DOSSIER_SONGS_DIR").map(PathBuf::from),
            json: false,
            explain: false,
            trace: false,
            marginal: None,
            strict: false,
            corpus_ceiling: None,
            expect: None,
            update_expect: false,
            prune: false,
            events: false,
            leaderboard: None,
            my_avatar: None,
            my_cover: None,
            at_ms: None,
            slow_at_ms: None,
            background: false,
            exhibit_budget_s: None,
            exhibit_worth: None,
            survey: false,
            bare: false,
            exhibit_clip_s: None,
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
            // Refuse an option this command has no use for before parsing it,
            // so `dossier judge --crf 18` says so rather than silently dropping
            // the crf. A positional (a replay path) is not an option and falls
            // straight through to the catch-all below.
            if arg.starts_with('-') && arg.as_str() != "-" && !command.accepts(canonical(arg)) {
                return Err(format!(
                    "`{}` has no option `{arg}` — see `dossier {} --help`",
                    command.name(),
                    command.name()
                ));
            }
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
                "--background" => options.background = true,
                "--slow" => {
                    options.slow_at_ms = Some(
                        rest.next()
                            .ok_or("--slow needs a time in milliseconds")?
                            .parse()
                            .map_err(|_| "--slow wants a number")?,
                    );
                }
                "--for" => {
                    options.exhibit_budget_s = Some(
                        rest.next()
                            .ok_or("--for needs a number of seconds")?
                            .parse()
                            .map_err(|_| "--for wants a number")?,
                    );
                }
                "--bare" => options.bare = true,
                "--survey" => options.survey = true,
                "--worth" => {
                    options.exhibit_worth = Some(
                        rest.next()
                            .ok_or("--worth needs a number between 0 and 1")?
                            .parse()
                            .map_err(|_| "--worth wants a number")?,
                    );
                }
                "--clip" => {
                    options.exhibit_clip_s = Some(
                        rest.next()
                            .ok_or("--clip needs a number of seconds")?
                            .parse()
                            .map_err(|_| "--clip wants a number")?,
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
                        format!("unknown kit `{name}` — try click, soft, drum, glass or wood")
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
                // `--strict` alone is judge's; `--strict <n>` is corpus's
                // ceiling. One flag because they mean the same thing: fail
                // when this got worse.
                "--strict" => match rest.clone().next().and_then(|n| n.parse::<u32>().ok()) {
                    Some(ceiling) => {
                        options.corpus_ceiling = Some(ceiling);
                        rest.next();
                    }
                    None => options.strict = true,
                },
                "--expect" => {
                    options.expect =
                        Some(PathBuf::from(rest.next().ok_or("--expect needs a path")?));
                }
                "--update-expect" => options.update_expect = true,
                "--prune" => options.prune = true,
                "--events" => options.events = true,
                "--leaderboard" => {
                    options.leaderboard =
                        Some(PathBuf::from(rest.next().ok_or("--leaderboard needs a path")?));
                }
                "--my-pictures" => {
                    options.my_avatar =
                        Some(PathBuf::from(rest.next().ok_or("--my-pictures needs two paths")?));
                    options.my_cover =
                        Some(PathBuf::from(rest.next().ok_or("--my-pictures needs two paths")?));
                }
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
        // On its own it would silently do nothing, and the thing it does is
        // delete rows — a flag like that should not be quietly ignored.
        if options.prune && !options.update_expect {
            return Err("--prune only means something with --update-expect".to_owned());
        }
        Ok(options)
    }
}

/// How many threads to measure the corpus with when nobody said.
///
/// Every core the machine has, unlike `video`, which leaves one for the
/// encoder: there is nothing on the other end of this one to leave a core for.
fn default_measurers() -> usize {
    std::thread::available_parallelism().map_or(1, std::num::NonZeroUsize::get)
}

/// The whole corpus in one line, and a non-zero exit when it gets worse.
///
/// The measurement that every change in this engine is judged by. It existed
/// for months as a shell script assembled from `judge`, `grep` and an awk
/// one-liner, which meant every measurement depended on whether it was
/// reassembled the same way — and a number that cannot be reproduced exactly
/// is not a measurement, it is an impression.
///
/// `--strict <n>` fails when the total count error is worse than `n`, which is
/// what makes it usable as a check rather than a report.
fn corpus(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }

    struct Row {
        name: String,
        error: u32,
        combo: i64,
        /// How far the score is out, as a percentage, where it can be
        /// compared at all.
        score: Option<f64>,
        client: &'static str,
        /// The file's own hash — the corpus manifest's key.
        md5: String,
        beatmap_md5: String,
    }
    let mut skipped = 0usize;

    let expected = match &options.expect {
        Some(path) if options.update_expect && !path.exists() => Some(BTreeMap::new()),
        Some(path) => match manifest::read(path) {
            Ok(rows) => Some(rows),
            Err(message) => {
                eprintln!("dossier: {message}");
                return ExitCode::FAILURE;
            }
        },
        None => None,
    };

    // The same replay in two directories is one replay. Before the set was
    // written down this went unnoticed, and a duplicate quietly counted twice
    // towards every total.
    // Hashing runs in order, and on its own, so that two runs over the same
    // set agree about which copy of a duplicate is the one that gets measured.
    // It is a file read beside a whole simulation, so it costs nothing to keep
    // it out of the parallel part.
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut duplicates = 0usize;
    let mut queue: Vec<(String, &PathBuf)> = Vec::new();

    for replay_path in &options.replays {
        let md5 = match std::fs::read(replay_path) {
            Ok(bytes) => locate::md5_hex(&bytes),
            Err(_) => {
                skipped += 1;
                continue;
            }
        };
        if !seen.insert(md5.clone()) {
            duplicates += 1;
            continue;
        }
        queue.push((md5, replay_path));
    }

    // Judging one replay shares nothing with judging the next — a map is
    // parsed, a play is simulated, a row comes out — and this is the
    // measurement every change to the engine is judged by, so of all the loops
    // here it is the one worth spending the machine on.
    //
    // Work is handed out by an atomic index rather than sliced up in advance,
    // because replays differ by an order of magnitude in length: a fixed slice
    // leaves most of the threads finished and waiting on whichever one drew
    // the marathon.
    //
    // None of this changes the answer. Rows carry the place they came from and
    // are put back in it, and every total below is a sum.
    let workers = options
        .threads
        .unwrap_or_else(default_measurers)
        .clamp(1, queue.len().max(1));
    let next = std::sync::atomic::AtomicUsize::new(0);
    let measured: Vec<(Vec<(usize, Row)>, usize)> = std::thread::scope(|scope| {
        let threads: Vec<_> = (0..workers)
            .map(|_| {
                let (next, queue, options) = (&next, &queue, &options);
                scope.spawn(move || {
                    let mut mine: Vec<(usize, Row)> = Vec::new();
                    let mut missed = 0usize;
                    loop {
                        let at = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        let Some((md5, replay_path)) = queue.get(at) else {
                            break;
                        };
                        let Ok(report) = run_one(replay_path, options) else {
                            missed += 1;
                            continue;
                        };
                        let check = &report.check;
                        let (ours, theirs) = (check.ours, check.theirs);
                        // Every count that disagrees, added up. Combo is kept
                        // apart: it is one number against one number, where the
                        // four counts trade against each other and a slider read
                        // the wrong way moves two of them at once.
                        let error = u32::from(ours.count_300)
                            .abs_diff(u32::from(theirs.count_300))
                            + u32::from(ours.count_100).abs_diff(u32::from(theirs.count_100))
                            + u32::from(ours.count_50).abs_diff(u32::from(theirs.count_50))
                            + u32::from(ours.count_miss).abs_diff(u32::from(theirs.count_miss));
                        // The score is a separate reading from the counts and
                        // moves on its own: a replay whose four counts are exact
                        // can still be scored a hundred per cent wrong, which is
                        // how a failed play scoring to the end of the map went
                        // unseen for as long as it did.
                        let score = report.score_error;
                        mine.push((
                            at,
                            Row {
                                score,
                                name: replay_path.file_name().map_or_else(
                                    || replay_path.display().to_string(),
                                    |n| n.to_string_lossy().into_owned(),
                                ),
                                error,
                                combo: i64::from(check.our_max_combo)
                                    - i64::from(check.their_max_combo),
                                client: if report.client.starts_with("lazer") {
                                    "lazer"
                                } else {
                                    "stable"
                                },
                                beatmap_md5: report.beatmap_md5.clone(),
                                md5: md5.clone(),
                            },
                        ));
                    }
                    (mine, missed)
                })
            })
            .collect();
        threads
            .into_iter()
            .map(|thread| thread.join().expect("a measuring thread panicked"))
            .collect()
    });

    let mut ordered: Vec<(usize, Row)> = Vec::with_capacity(queue.len());
    for (mine, missed) in measured {
        skipped += missed;
        ordered.extend(mine);
    }
    ordered.sort_unstable_by_key(|(at, _)| *at);
    let mut rows: Vec<Row> = ordered.into_iter().map(|(_, row)| row).collect();

    let total: u32 = rows.iter().map(|r| r.error).sum();
    let exact = rows
        .iter()
        .filter(|r| r.error == 0 && r.combo == 0)
        .count();
    let lazer = rows.iter().filter(|r| r.client == "lazer").count();

    rows.sort_by(|a, b| {
        b.error.cmp(&a.error).then(
            b.score
                .map_or(0.0, f64::abs)
                .total_cmp(&a.score.map_or(0.0, f64::abs)),
        )
    });
    let scored: Vec<f64> = rows.iter().filter_map(|r| r.score).map(f64::abs).collect();
    for row in rows
        .iter()
        .filter(|r| r.error != 0 || r.combo != 0 || r.score.is_some_and(|s| s.abs() >= 0.05))
    {
        let combo = if row.combo == 0 {
            String::new()
        } else {
            format!("  combo {:+}", row.combo)
        };
        let score = match row.score {
            Some(off) if off.abs() >= 0.05 => format!("  score {off:+.2}%"),
            _ => String::new(),
        };
        println!(
            "   {:>4}{combo:<13}{score:<16}  {:<6}  {}",
            row.error,
            row.client,
            row.name.chars().take(46).collect::<String>()
        );
    }

    let worst_score = scored.iter().copied().fold(0.0f64, f64::max);
    println!(
        "\n{exact} exact of {} ({lazer} lazer), total count error {total}, {skipped} skipped",
        rows.len()
    );
    if !scored.is_empty() {
        println!(
            "score compared on {}, worst {worst_score:.2}%, within 0.5% on {}",
            scored.len(),
            scored.iter().filter(|off| **off < 0.5).count()
        );
    }

    if duplicates > 0 {
        println!("{duplicates} duplicate replay file(s) counted once");
    }

    let mut regressed = 0usize;
    let mut absent = 0usize;
    if let (Some(expected), Some(path)) = (&expected, &options.expect) {
        if options.update_expect {
            // This used to rebuild the file from the run alone, which was
            // right while the whole corpus was always on the disk and wrong
            // the moment it was not: a run over the replays that happen to be
            // here took the rest of the list with it, and that list is the
            // only record of what to go and find. `after_run` states the rule.
            let measured = rows
                .iter()
                .map(|row| manifest::Expectation {
                    replay_md5: row.md5.clone(),
                    beatmap_md5: row.beatmap_md5.clone(),
                    beatmap_id: None,
                    error: row.error,
                    combo: row.combo,
                    score: row.score,
                    name: row.name.clone(),
                })
                .collect();
            let (updated, dropped) = manifest::after_run(expected, measured, &seen, options.prune);
            let fresh = rows.iter().filter(|row| !expected.contains_key(&row.md5)).count();
            match manifest::write(path, &updated) {
                Ok(()) => {
                    let kept = updated.len() - rows.len();
                    println!(
                        "\n{} rows written to {}: {} measured ({fresh} new), {kept} kept, {dropped} dropped",
                        updated.len(),
                        path.display(),
                        rows.len(),
                    );
                }
                Err(message) => {
                    eprintln!("dossier: {message}");
                    return ExitCode::FAILURE;
                }
            }
        } else {
            // Replays the file names that this machine does not have. Silence
            // here is what let twelve of them go missing for months: a corpus
            // that shrinks reports a smaller total and looks like progress.
            for row in expected.values() {
                if !seen.contains(&row.replay_md5) {
                    absent += 1;
                    println!(
                        "   ?? {}  {}",
                        &row.replay_md5[..12],
                        row.name.chars().take(46).collect::<String>()
                    );
                }
            }
            let mut unlisted = 0usize;
            for row in &rows {
                let Some(was) = expected.get(&row.md5) else {
                    unlisted += 1;
                    continue;
                };
                if let Some(what) = was.worse_than(row.error, row.combo, row.score) {
                    regressed += 1;
                    println!(
                        "   !! {}  {what}",
                        row.name.chars().take(46).collect::<String>()
                    );
                }
            }
            println!(
                "\nagainst {}: {} of {} rows present, {absent} absent, {unlisted} not listed, \
                 {regressed} worse",
                path.display(),
                expected.len() - absent,
                expected.len()
            );
        }
    }

    if regressed > 0 {
        eprintln!("dossier: {regressed} replay(s) got worse than the corpus says they are");
        return ExitCode::FAILURE;
    }
    // An absent replay is only a failure when the run claimed to be a check.
    // Measuring a handful of replays by hand is a normal thing to do.
    if absent > 0 && options.strict {
        eprintln!("dossier: {absent} replay(s) of the corpus are not on this machine");
        return ExitCode::FAILURE;
    }
    match options.corpus_ceiling {
        Some(ceiling) if total > ceiling => {
            eprintln!("dossier: worse than the {ceiling} this was held to");
            ExitCode::FAILURE
        }
        _ => ExitCode::SUCCESS,
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

            // Any slider that lost a piece, not only the ones whose verdict
            // fell. Under lazer the verdict is the head's, so a dropped tail
            // no longer shows up in it at all — selecting on the verdict hid
            // every one of them.
            if verdict != Judgement::Great || lost.iter().any(|l| *l) {
                for (slot, was_lost) in lost.iter().enumerate() {
                    if *was_lost {
                        dropped[slot] += 1;
                    }
                }
                if !lost[3] && verdict != Judgement::Great {
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
            if verdict.is_none() || (verdict == Some(Judgement::Great) && dropped.is_empty()) {
                continue;
            }
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
/// Our health model against osu!'s own life-bar graph.
///
/// About half the replays carry one. Those are the test — the model exists
/// precisely so the other half can have a bar too, and a model checked only
/// against itself would be no better than a guess drawn smoothly.
fn health_command(options: Options) -> ExitCode {
    if options.replays.is_empty() {
        eprintln!("dossier: no replay given");
        return ExitCode::FAILURE;
    }
    let mut worst = 0f64;
    let mut total = 0f64;
    let mut counted = 0usize;
    for replay_path in &options.replays {
        let (beatmap, replay) = match load(replay_path, &options) {
            Ok(pair) => pair,
            Err(message) => {
                println!("── {}\n   SKIPPED: {message}\n", replay_path.display());
                continue;
            }
        };
        let graph = dossier_replay::life_points(&replay.life_bar);
        if graph.is_empty() {
            println!("── {}\n   no life-bar graph\n", replay_path.display());
            continue;
        }
        let state = GameState::new(&beatmap, &replay);
        let Some(judge) = state.judge() else {
            continue;
        };
        let ruleset = Ruleset::of_replay(&replay);
        let track = dossier_sim::HealthTrack::build(
            judge,
            state.timeline(),
            &beatmap.breaks,
            beatmap.format_version,
            replay.mods,
            ruleset,
        );

        // Compare where osu! actually sampled, so nothing is invented between
        // its points.
        let mut sum = 0f64;
        let mut signed = 0f64;
        let mut peak = 0f64;
        let mut divergences: Vec<(f64, f64, f32, f32)> = Vec::new();
        for &(time, theirs) in &graph {
            let ours = track.at(time);
            let d = f64::from((ours - theirs).abs());
            sum += d;
            signed += f64::from(ours - theirs);
            peak = peak.max(d);
            divergences.push((d, time, ours, theirs));
        }
        divergences.sort_by(|a, b| b.0.total_cmp(&a.0));
        let mean = sum / graph.len() as f64;
        worst = worst.max(mean);
        total += mean;
        counted += 1;
        println!("── {}", replay_path.display());
        println!(
            "   {:?}  HP {:.1}  samples {}  mean {:.3}  bias {:+.3}  worst {:.3}  drain {:.5}",
            ruleset,
            beatmap.difficulty.hp_drain,
            graph.len(),
            mean,
            signed / graph.len() as f64,
            peak,
            track.drain_rate(),
        );
        if options.trace {
            // The whole series. A verdict we credit and osu! did not shows up
            // as a *step* in the gap rather than a level, so the column that
            // matters is the last one: the graph is only sampled every couple
            // of seconds, but a single wrong call on a low-HP map moves the
            // bar several times further than the model's own noise.
            let mut last = 0f64;
            for &(time, theirs) in &graph {
                let ours = track.at(time);
                let gap = f64::from(ours - theirs);
                println!(
                    "      {time:>8.0}ms  ours {ours:.3}  theirs {theirs:.3}  gap {gap:+.3}  step {:+.3}",
                    gap - last
                );
                last = gap;
            }
        } else {
            for &(d, time, ours, theirs) in divergences.iter().take(4) {
                println!("      {time:>8.0}ms  ours {ours:.3}  theirs {theirs:.3}  off {d:.3}");
            }
        }
    }
    if counted > 1 {
        println!(
            "\nmean {:.3} across {counted}, worst replay {worst:.3}",
            total / counted as f64
        );
    }
    ExitCode::SUCCESS
}

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
        let ruleset = Ruleset::of_replay(&replay);
        // Through the state rather than built here, so the command measures
        // the same thing the renderer draws — including the multiplier read
        // off the replay rather than looked up.
        let Some(track) = state.score_track() else {
            continue;
        };

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
    let ruleset = dossier_sim::Ruleset::of_replay(replay);
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
        lazer_mods: replay
            .lazer_mods()
            .iter()
            .map(|m| {
                if m.settings.is_empty() {
                    m.acronym.clone()
                } else {
                    // A mod whose switches were changed is not the same mod,
                    // and Classic's switches decide two rules apiece.
                    let settings: Vec<String> = m
                        .settings
                        .iter()
                        .map(|(k, v)| format!("{k}={v:?}"))
                        .collect();
                    format!("{}({})", m.acronym, settings.join(","))
                }
            })
            .collect(),
        statistics: replay
            .score_info
            .as_ref()
            .map(|info| info.statistics.iter().map(|(k, v)| (k.clone(), *v)).collect())
            .unwrap_or_default(),
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
        beatmap_md5: replay.beatmap_hash.clone(),
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
        parts: part_checks(&state, &replay),
        score_error: state
            .score_track()
            .filter(|track| track.comparable())
            .filter(|_| replay.score > 0)
            .map(|track| {
                (track.total() as f64 - f64::from(replay.score)) / f64::from(replay.score) * 100.0
            }),
    })
}

/// Our count of each of lazer's judgement types, against lazer's own.
///
/// Only lazer replays carry the block, and only recent ones. The mapping is
/// where the thinking is: lazer's names are for its own object model, and ours
/// have to be folded into them rather than the other way round.
fn part_checks(state: &GameState, replay: &Replay) -> Vec<PartCheck> {
    let Some(theirs) = replay.score_info.as_ref().map(|info| &info.statistics) else {
        return Vec::new();
    };
    if theirs.is_empty() {
        return Vec::new();
    }
    let Some(judge) = state.judge() else {
        return Vec::new();
    };

    let mut great = 0i64;
    let mut ok = 0i64;
    let mut meh = 0i64;
    let mut miss = 0i64;
    let mut large_tick_hit = 0i64;
    let mut large_tick_miss = 0i64;
    let mut slider_tail_hit = 0i64;
    let mut ignore_hit = 0i64;
    let mut ignore_miss = 0i64;

    // A slider resolves to IgnoreHit when *anything* on it was caught, and to
    // IgnoreMiss when nothing was:
    //
    // ```csharp
    // r.Type = slider.NestedHitObjects.Any(o => o.Result.IsHit)
    //     ? r.Judgement.MaxResult : r.Judgement.MinResult;
    // ```
    //
    // and a dropped tail is IgnoreMiss in its own right, so the two land in
    // the same bucket and have to be counted together.
    let mut slider_alive = false;
    let mut in_slider = false;

    let close_slider = |alive: bool, hit: &mut i64, missed: &mut i64| {
        if alive {
            *hit += 1;
        } else {
            *missed += 1;
        }
    };

    for event in judge.events() {
        match event.part {
            Part::Circle | Part::Spinner | Part::Slider => match event.result {
                Judgement::Great => great += 1,
                Judgement::Ok => ok += 1,
                Judgement::Meh => meh += 1,
                Judgement::Miss => miss += 1,
            },
            Part::SliderHead => {
                if in_slider {
                    close_slider(slider_alive, &mut ignore_hit, &mut ignore_miss);
                }
                in_slider = true;
                slider_alive = !event.result.is_miss();
            }
            // lazer counts these as `SmallBonus` and `LargeBonus`, which the
            // legacy header has no column for. Nothing to check them against,
            // so they are left out of the comparison rather than folded into a
            // column they are not part of.
            Part::SpinnerSpin | Part::SpinnerPoints | Part::SpinnerBonus => {}
            Part::SliderTick | Part::SliderRepeat => {
                if event.result.is_miss() {
                    large_tick_miss += 1;
                } else {
                    large_tick_hit += 1;
                    slider_alive = true;
                }
            }
            Part::SliderTail => {
                if event.result.is_miss() {
                    ignore_miss += 1;
                } else {
                    slider_tail_hit += 1;
                    slider_alive = true;
                }
            }
        }
    }
    if in_slider {
        close_slider(slider_alive, &mut ignore_hit, &mut ignore_miss);
    }

    [
        ("great", great),
        ("ok", ok),
        ("meh", meh),
        ("miss", miss),
        ("large_tick_hit", large_tick_hit),
        ("large_tick_miss", large_tick_miss),
        ("slider_tail_hit", slider_tail_hit),
        ("ignore_hit", ignore_hit),
        ("ignore_miss", ignore_miss),
    ]
    .into_iter()
    .filter(|(name, ours)| *ours != 0 || theirs.contains_key(*name))
    .map(|(name, ours)| PartCheck {
        name: name.to_owned(),
        ours,
        theirs: theirs.get(name).copied().unwrap_or(0),
    })
    .collect()
}

/// Draw one instant to a PNG.
///
/// A single frame is the smallest thing that can be looked at and judged by
/// eye, which makes it the right first output: video is this repeated, and
/// nothing about the repetition will fix a frame that is wrong.
/// `dossier exhibit` — choose the telling moments, and say why.
///
/// Judging the replay is the whole cost here and it is seconds, not minutes:
/// every signal the scorers read is something the engine already computed to
/// answer a different question. That is why this feature was worth building
/// now rather than after more of the engine exists.
/// `dossier exhibit --survey` — what a hundred replays come to.
///
/// The instrument this feature was missing. Judgement is held to the corpus and
/// a change either improves the count error or does not; selection can be held
/// to nothing of the kind, so what stands in for it is knowing what a change
/// did across every replay to hand rather than across the two somebody watched.
///
/// A replay that cannot be judged is counted and skipped rather than fatal: a
/// survey of a folder is a survey of whatever maps are in it, and stopping at
/// the first missing one turns a measurement into a scavenger hunt.
fn survey(options: &Options) -> ExitCode {
    let settings = exhibit::settings(
        options.exhibit_budget_s,
        options.exhibit_clip_s,
        options.exhibit_worth,
    );
    let mut survey = exhibit::Survey::default();
    for path in &options.replays {
        let Ok((beatmap, replay)) = load(path, options) else {
            survey.skipped += 1;
            continue;
        };
        let state = GameState::new(&beatmap, &replay);
        survey.add(&dossier_exhibit::choose(&state, settings), state.playback_rate());
    }
    print!("{}", survey.report());
    ExitCode::SUCCESS
}

fn exhibit_command(options: Options) -> ExitCode {
    let Some(replay_path) = options.replays.first() else {
        eprintln!("dossier: exhibit needs a replay");
        return ExitCode::FAILURE;
    };
    if options.survey {
        return survey(&options);
    }

    // `load_with_origin` rather than `load`: the origin is where the audio
    // track is unpacked from, and by the time a reel is wanted the archive has
    // long since gone out of scope.
    let (beatmap, replay, origin) = match load_with_origin(replay_path, &options) {
        Ok(triple) => triple,
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    };

    let state = GameState::new(&beatmap, &replay);
    let settings = exhibit::settings(
        options.exhibit_budget_s,
        options.exhibit_clip_s,
        options.exhibit_worth,
    );
    let clips = dossier_exhibit::choose(&state, settings);

    if options.json {
        println!(
            "{}",
            exhibit::as_json(&replay_path.display().to_string(), &replay, &state, &clips)
        );
    } else if options.events {
        // stdout belongs to the watcher now, and this table is prose: mixed
        // into the event stream it is not a table and not a stream either.
        // The same clips arrive as `clip` events, one before each is drawn.
        eprint!("{}", exhibit::as_text(&clips, state.playback_rate()));
    } else {
        print!("{}", exhibit::as_text(&clips, state.playback_rate()));
    }

    // Nothing chosen is a real answer — a replay of somebody quitting twelve
    // seconds in has no moments — but it is also the shape a wrong `--clip`
    // takes, so it is worth saying out loud rather than printing an empty list
    // and exiting zero.
    if clips.is_empty() {
        eprintln!("dossier: no clip fits — the play is shorter than one clip");
        return ExitCode::SUCCESS;
    }

    // Without `-o` the answer *is* the list. Rendering is opt-in rather than
    // the default because it costs minutes and the selection it is made of can
    // be read in full above.
    // `-o` is shared with `frame`, whose default it still carries. Left at
    // that default it means the caller did not ask for a reel — which is the
    // right default here: the selection above is the feature and an encode is
    // minutes.
    if options.out == Path::new("frame.png") {
        return ExitCode::SUCCESS;
    }
    let out = options.out.clone();
    if let Err(message) = video::check_output(&out) {
        eprintln!("dossier: {message}");
        return ExitCode::FAILURE;
    }

    let mut skin = options.skin.visual(&beatmap);
    match load_font(options.font.as_deref()) {
        Ok(Some(font)) => skin = skin.with_font(font),
        Ok(None) => eprintln!("dossier: no font found — drawing without numbers"),
        Err(message) => {
            eprintln!("dossier: {message}");
            return ExitCode::FAILURE;
        }
    }

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

    let scene = Scene::new(&state, skin)
        .signed_by(&replay)
        .with_leaderboard(
            load_leaderboard(options.leaderboard.as_deref(), &replay.player)
                .with_own_pictures(options.my_avatar.clone(), options.my_cover.clone()),
        );
    let scene = if options.bare { scene.bare() } else { scene };
    let scene = match backdrop(&options, &beatmap, &origin, scene.skin(), options.size) {
        Some(art) => scene.with_backdrop(art),
        None => scene,
    };
    let settings = video::Settings {
        out,
        fps: options.fps,
        size: options.size,
        from_ms: None,
        to_ms: None,
        ffmpeg: options.ffmpeg.clone(),
        crf: options.crf,
        preset: options.preset.clone(),
        threads: options.threads,
        encoder_threads: options.encoder_threads,
        audio,
        hitsounds: None,
        events: events::Events::wanted(options.events),
        // exhibit chooses its own moments to slow into; the per-clip render is
        // not driven by a single `--slow` instant. Wired in a later step.
        slow_at_ms: None,
        slow_focus: None,
    };

    // Built once and shared by every clip: loading a skin's samples is a
    // directory walk and a decode per file, and doing it five times over would
    // be five times the wait for the same bytes.
    let (kit, pack) = (options.kit(), options.samples());
    let muted = options.mute;
    let sounds = |plan: &video::Plan, index: usize| -> Option<PathBuf> {
        if muted {
            return None;
        }
        write_hitsounds_as(
            &state,
            &beatmap,
            plan,
            kit,
            pack.clone(),
            scratch.as_ref(),
            &format!("hitsounds-{index}.pcm"),
        )
    };

    eprintln!(
        "{} — {} [{}], {} · {} clips · {}",
        replay.player,
        beatmap.metadata.title,
        beatmap.metadata.version,
        replay.mods,
        clips.len(),
        settings.out.display()
    );

    match reel::render(
        &scene,
        &state,
        &clips,
        &settings,
        scratch.as_ref(),
        &sounds,
    ) {
        Ok(()) => {
            let size = std::fs::metadata(&settings.out).map(|m| m.len()).unwrap_or(0);
            // With `--events` stdout is the watcher's channel and this line
            // would be a stray object in the middle of it. The same fact goes
            // out as an event instead, and the person keeps their sentence.
            if options.events {
                settings.events.wrote(&settings.out, size);
            } else {
                println!(
                    "{} — {:.1} MB",
                    settings.out.display(),
                    size as f64 / 1_048_576.0
                );
            }
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("dossier: {message}");
            ExitCode::FAILURE
        }
    }
}

fn frame(options: Options) -> ExitCode {
    let Some(at_ms) = options.at_ms else {
        eprintln!("dossier: frame needs --at <ms>");
        return ExitCode::FAILURE;
    };
    let Some(replay_path) = options.replays.first() else {
        eprintln!("dossier: frame needs a replay");
        return ExitCode::FAILURE;
    };

    // With the origin, because a background lives beside the map — in the same
    // folder, or inside the same `.osz`.
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
    let scene = Scene::new(&state, skin)
        .signed_by(&replay)
        .with_leaderboard(
            load_leaderboard(options.leaderboard.as_deref(), &replay.player)
                .with_own_pictures(options.my_avatar.clone(), options.my_cover.clone()),
        );
    let scene = if options.bare { scene.bare() } else { scene };
    let scene = match backdrop(&options, &beatmap, &origin, scene.skin(), options.size) {
        Some(art) => scene.with_backdrop(art),
        None => scene,
    };
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

/// Read the rivals to stand the play against, if any were named.
///
/// A missing or unreadable file is not an error. The scoreboard decorates a
/// render; refusing to draw four minutes of video because a list of names could
/// not be opened would be the wrong trade, and its absence from the frame says
/// so plainly enough.
/// The map's artwork, prepared for a frame of this size — or nothing, when it
/// was not asked for, the map names none, or the file will not decode.
///
/// Never a hard failure: a background is the one part of a render the play does
/// not depend on, and a map whose artwork is a format we cannot read is still a
/// map worth watching.
fn backdrop(
    options: &Options,
    beatmap: &Beatmap,
    origin: &locate::Origin,
    skin: &Skin,
    size: (u32, u32),
) -> Option<dossier_render::Pixmap> {
    if !options.background {
        return None;
    }
    let filename = beatmap.background.as_deref()?;
    let bytes = locate::read_background(origin, filename)?;
    let prepared = dossier_render::background::prepare(
        &bytes,
        size.0,
        size.1,
        skin.background_dim,
        skin.background_blur,
        skin.background,
    );
    if prepared.is_none() {
        eprintln!("dossier: could not read the background `{filename}` — rendering without it");
    }
    prepared
}

fn load_leaderboard(path: Option<&Path>, player: &str) -> dossier_render::Leaderboard {
    let Some(path) = path else {
        return dossier_render::Leaderboard::default();
    };
    match std::fs::read_to_string(path) {
        Ok(text) => dossier_render::Leaderboard::parse(&text, player),
        Err(error) => {
            eprintln!("dossier: {}: {error} — drawing without a scoreboard", path.display());
            dossier_render::Leaderboard::default()
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
        // This one only works out a span; nothing is drawn from it, so there
        // is nothing for it to report.
        events: events::Events::wanted(false),
        // The same dip as the render, so the hit-sound plan lays its strikes on
        // the same clock — a hit inside the dip lands where the picture shows it.
        slow_at_ms: options.slow_at_ms,
        // The probe draws nothing, so it has no camera to place.
        slow_focus: None,
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
            options.kit(),
            options.samples(),
            scratch.as_ref(),
        ),
        _ => None,
    };

    let scene = Scene::new(&state, skin)
        .signed_by(&replay)
        .with_leaderboard(
            load_leaderboard(options.leaderboard.as_deref(), &replay.player)
                .with_own_pictures(options.my_avatar.clone(), options.my_cover.clone()),
        );
    let scene = if options.bare { scene.bare() } else { scene };
    let scene = match backdrop(&options, &beatmap, &origin, scene.skin(), options.size) {
        Some(art) => scene.with_backdrop(art),
        None => scene,
    };
    // Where the camera draws in to: where the cursor is at the moment being
    // slowed into — the place on the field the play is at, which is where the
    // eye already is. Only when there is a moment to slow into at all.
    let slow_focus = options
        .slow_at_ms
        .and_then(|at| state.cursor_track().sample(at))
        .map(|cursor| cursor.pos);
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
        events: events::Events::wanted(options.events),
        slow_at_ms: options.slow_at_ms,
        slow_focus,
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
        Ok(()) => {
            let size = std::fs::metadata(&settings.out).map(|m| m.len()).unwrap_or(0);
            settings.events.wrote(&settings.out, size);
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("dossier: {message}");
            ExitCode::FAILURE
        }
    }
}

/// Whether a temp directory is one of ours to remove.
///
/// `dossier-` and then a pid, and the pid has to be there: the empty suffix
/// passes an "all digits" test, because every one of no digits is a digit, and
/// a bare `dossier-` belonging to somebody else would have been swept for it.
///
/// Anything else in the temp directory is somebody's and none of our business,
/// which is worth being exact about — this function's whole job is deciding
/// what to delete.
fn scratch_of_ours(name: &str) -> bool {
    name.strip_prefix("dossier-")
        .is_some_and(|pid| !pid.is_empty() && pid.bytes().all(|b| b.is_ascii_digit()))
}

/// How long an abandoned scratch directory is left alone before it is swept.
///
/// Comfortably past any render. The bot gives one half an hour before it gives
/// up, so anything untouched for six is not a render in progress — it is one
/// that was killed.
const SCRATCH_STALE_HOURS: u64 = 6;

/// A temporary directory that clears itself up.
///
/// The audio has to exist as a file for the length of the render and not one
/// moment longer. Tying that to a value's lifetime means an early return or a
/// failed encode can't leave a hundred megabytes of someone's music behind.
///
/// `Drop` is not enough on its own, which took a cancelled render to notice.
/// The bot's cancel button kills the engine outright — that is the whole point
/// of it, since an abandoned render keeps a core busy for minutes while the bot
/// pretends it stopped — and nothing in this process runs after a kill. Two
/// orphans totalling sixteen megabytes were sitting in the temp directory when
/// this was looked for, one of them a whole extracted song.
///
/// So every run sweeps the ones left by runs before it. Cleaning up on the way
/// *in* is the only kind that survives being killed on the way out.
struct Scratch(Option<PathBuf>);

impl Scratch {
    fn new() -> Self {
        let temp = std::env::temp_dir();
        Self::sweep(&temp);
        let path = temp.join(format!("dossier-{}", std::process::id()));
        Self(std::fs::create_dir_all(&path).ok().map(|()| path))
    }

    /// Remove scratch directories nobody came back for.
    ///
    /// By age rather than by asking whether the process still exists: there is
    /// no portable way to ask, and a pid is reused soon enough that the answer
    /// would sometimes be a confident yes about a different program. Age needs
    /// nothing from the operating system and cannot mistake one process for
    /// another — it only has to be longer than a render, and it is by an order.
    ///
    /// Best-effort throughout. A temp directory somebody else owns, or one
    /// being written by a concurrent render, is a reason to move on rather than
    /// to refuse to render.
    fn sweep(temp: &Path) {
        let Ok(entries) = std::fs::read_dir(temp) else {
            return;
        };
        let stale = std::time::Duration::from_secs(SCRATCH_STALE_HOURS * 3600);
        for entry in entries.flatten() {
            let name = entry.file_name();
            if !name.to_str().is_some_and(scratch_of_ours) {
                continue;
            }
            let old = entry
                .metadata()
                .and_then(|meta| meta.modified())
                .and_then(|at| at.elapsed().map_err(std::io::Error::other))
                .is_ok_and(|since| since > stale);
            if old {
                let _ = std::fs::remove_dir_all(entry.path());
            }
        }
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
    kit: dossier_audio::Kit,
    pack: dossier_audio::SamplePack,
    scratch: Option<&Path>,
) -> Option<PathBuf> {
    let track = hitsounds::build(
        state,
        beatmap,
        |map_ms| plan.video_time_of(map_ms),
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

/// The same, under a name the caller chooses.
///
/// A reel builds one track per clip and they are all alive at once — ffmpeg
/// reads them in the second pass, long after the clip that made them was
/// encoded — so they cannot share a filename the way a single render's can.
fn write_hitsounds_as(
    state: &GameState,
    beatmap: &Beatmap,
    plan: &video::Plan,
    kit: dossier_audio::Kit,
    pack: dossier_audio::SamplePack,
    scratch: Option<&Path>,
    name: &str,
) -> Option<PathBuf> {
    let track = hitsounds::build(
        state,
        beatmap,
        |map_ms| plan.video_time_of(map_ms),
        plan.video_seconds,
        kit,
        pack,
    );
    if track.is_empty() {
        return None;
    }
    let path = scratch?.join(name);
    std::fs::write(&path, track.to_pcm()).ok()?;
    Some(path)
}

/// Write a short WAV of the hit sounds alone.
///
/// Tuning a kit by rendering a video is a minute per idea, most of it spent on
/// pixels that aren't in question. This is under a second, and the sounds are
/// heard without music over them — which is how you tell what a sound *is*,
/// as opposed to whether it survives the mix.
/// Write our look out as a folder osu! can wear.
///
/// A skin nobody has to be told how to install: the palette in a `skin.ini` and
/// the hit sounds beside it, which were already named the way the game reads
/// them. Whatever is not written falls back to the game's own skin, so this is
/// a real skin from the first file rather than only once every element exists.
fn skin_command(options: Options) -> ExitCode {
    let folder = if options.out == Path::new("frame.png") {
        // `-o` is shared with `frame`, whose default this still carries. Left at
        // it, the caller did not name a folder — and writing a skin into
        // `frame.png` would be a surprise.
        eprintln!("dossier: skin needs somewhere to write — pass -o <folder>");
        return ExitCode::FAILURE;
    } else {
        options.out.clone()
    };

    // What the skin calls itself once it is installed in the game. Not "1984"
    // any more: that was the house style's name, and the house style is gone.
    let name = match &options.skin {
        // Writing out a skin that was itself read from a folder: it keeps its
        // own name, since what comes out is that skin plus whatever the engine
        // filled in for it.
        SkinChoice::Folder(path) => path
            .file_name()
            .map_or("dossier", |n| n.to_str().unwrap_or("dossier")),
        SkinChoice::Classic => "dossier",
    };
    // The digits are drawn from the same face the renders set their combo
    // numbers in. Without it they simply are not written — the rest of the skin
    // is still worth having, and the game falls back to its own figures.
    let mut skin = options.skin.visual_default();
    match load_font(options.font.as_deref()) {
        Ok(Some(font)) => skin = skin.with_font(font),
        Ok(None) => eprintln!("dossier: no font found — the combo digits are left to the game"),
        Err(error) => {
            eprintln!("dossier: {error}");
            return ExitCode::FAILURE;
        }
    }
    // The same folder the renderer reads its samples from, so the skin ships
    // the sounds a render is made with rather than a second set like them.
    let samples = options.samples_folder();
    match skinfile::write(&skin, name, &folder, samples.as_deref()) {
        Ok(written) => {
            println!(
                "{} — skin.ini, {} image(s) and {} sound(s)",
                written.folder.display(),
                written.images,
                written.sounds
            );
            eprintln!(
                "   drop it in osu!/Skins/ — anything not written falls back to the game's own"
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("dossier: {error}");
            ExitCode::FAILURE
        }
    }
}

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

#[cfg(test)]
mod scratch_names {
    use super::scratch_of_ours;

    /// The whole of what the sweep is allowed to touch. It deletes directories,
    /// so the predicate saying which ones is worth pinning exactly rather than
    /// approximately.
    #[test]
    fn only_our_own_scratch_directories_are_ours() {
        assert!(scratch_of_ours("dossier-1"));
        assert!(scratch_of_ours("dossier-71847"));

        assert!(!scratch_of_ours("dossier-notapid"));
        assert!(!scratch_of_ours("dossier-12a"));
        assert!(!scratch_of_ours("dossier"));
        assert!(!scratch_of_ours("not-dossier-1"));
        assert!(!scratch_of_ours(""));
        assert!(!scratch_of_ours("com.apple.launchd.abc"));
    }

    /// The one that got away: every one of no digits is a digit, so a bare
    /// `dossier-` passed an all-digits test and would have been swept for
    /// somebody else.
    #[test]
    fn a_prefix_with_no_pid_is_not_ours() {
        assert!(!scratch_of_ours("dossier-"));
    }
}

#[cfg(test)]
mod options_per_command {
    use super::{canonical, Command, Options, OPTIONS_TABLE};

    fn s(items: &[&str]) -> Vec<String> {
        items.iter().map(|i| (*i).to_owned()).collect()
    }

    /// The bug that prompted this: an option a command has no use for was taken
    /// and ignored. Now it is refused, and the message names the command.
    #[test]
    fn a_command_refuses_an_option_that_is_not_its_own() {
        match Options::parse(Command::Judge, &s(&["--crf", "18", "r.osr"])) {
            Err(error) => assert!(error.contains("`judge` has no option `--crf`"), "{error}"),
            Ok(_) => panic!("judge should refuse --crf"),
        }

        assert!(Options::parse(Command::Inspect, &s(&["--songs", "d", "r.osr"])).is_err());
        assert!(Options::parse(Command::Sounds, &s(&["--map", "m.osu"])).is_err());
    }

    /// And still takes the ones that are. `video` encodes, so `--crf` is its
    /// business; a short flag is the same option as its long name.
    #[test]
    fn a_command_takes_its_own_options() {
        assert!(Options::parse(Command::Video, &s(&["--crf", "18", "r.osr"])).is_ok());
        assert!(Options::parse(Command::Judge, &s(&["--songs", "d", "r.osr"])).is_ok());
        assert!(Options::parse(Command::Judge, &s(&["-s", "d", "r.osr"])).is_ok());
        assert!(Options::parse(Command::Inspect, &s(&["--json", "r.osr"])).is_ok());
    }

    /// A positional path is not an option, whatever it looks like — but a lone
    /// `-` is left to fall through as one too, the way it always did.
    #[test]
    fn a_replay_path_is_not_mistaken_for_an_option() {
        assert!(Options::parse(Command::Judge, &s(&["a.osr", "b.osr"])).is_ok());
    }

    /// The exact set of flags the bot sends each command, so a change to the
    /// gate that would strand a render shows up here rather than in a chat.
    #[test]
    fn the_bot_s_invocations_all_pass_the_gate() {
        assert!(Command::Inspect.accepts("--json"));
        for f in ["--json", "--songs"] {
            assert!(Command::Judge.accepts(f), "judge {f}");
        }
        for f in [
            "--events", "--skin", "--preset", "--crf", "--songs", "--size", "--fps", "--mute",
            "--leaderboard", "--my-pictures", "--encoder-threads", "--out",
        ] {
            assert!(Command::Video.accepts(f), "video {f}");
        }
        for f in ["--events", "--skin", "--preset", "--crf", "--songs", "--size", "--fps", "--for", "--clip", "--out", "--leaderboard", "--my-pictures", "--encoder-threads"] {
            assert!(Command::Exhibit.accepts(f), "exhibit {f}");
        }
    }

    /// The help and the gate are drawn from the same table, so neither can grow
    /// a row the other has never heard of: every option some command accepts is
    /// described, and every described option is accepted somewhere.
    #[test]
    fn the_table_and_the_gate_agree() {
        const ALL: &[Command] = &[
            Command::Inspect, Command::Judge, Command::Corpus, Command::Debug,
            Command::Sliders, Command::Errors, Command::Score, Command::Health,
            Command::Frame, Command::Video, Command::Exhibit, Command::Sounds,
        ];
        for (flag, _, _) in OPTIONS_TABLE {
            assert!(
                ALL.iter().any(|c| c.accepts(flag)),
                "{flag} is described in help but no command accepts it"
            );
        }
    }

    /// `canonical` folds a short flag onto its long name, which is what lets the
    /// gate judge `-s` and `--songs` as one option.
    #[test]
    fn short_flags_fold_onto_their_long_names() {
        assert_eq!(canonical("-s"), "--songs");
        assert_eq!(canonical("--songs"), "--songs");
        assert_eq!(canonical("--crf"), "--crf");
    }
}

#[cfg(test)]
mod skin_choice_tests {
    use super::*;

    #[test]
    fn a_folder_that_exists_is_a_skin() {
        // How a player's own skin gets in: `--skin ~/skins/whatever`. Told
        // apart from a named skin by being a folder, which the named ones
        // never are.
        let dir = std::env::temp_dir().join(format!("dossier-choice-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");

        match SkinChoice::parse(dir.to_str().expect("a path")) {
            Ok(SkinChoice::Folder(path)) => assert_eq!(path, dir),
            other => panic!("expected a folder, got {other:?}"),
        }
    }

    #[test]
    fn a_path_that_is_not_there_is_not_silently_a_skin() {
        // A typo in a path must not come back as an empty skin that renders
        // everything with the engine's own drawing and says nothing about it.
        let miss = SkinChoice::parse("/no/such/folder");
        assert!(miss.is_err(), "{miss:?}");
    }

    #[test]
    fn the_named_skin_still_works_and_the_removed_one_says_so() {
        assert_eq!(SkinChoice::parse("classic"), Ok(SkinChoice::Classic));
        assert_eq!(SkinChoice::parse("map"), Ok(SkinChoice::Classic));
        let gone = SkinChoice::parse("1984").expect_err("removed");
        assert!(gone.contains("removed"), "{gone}");
    }

    #[test]
    fn a_skin_folder_is_where_its_sounds_come_from_too() {
        // A real skin keeps its `.wav`s beside its pictures, so importing the
        // sounds is pointing the sample reader at the same place.
        let dir = std::env::temp_dir().join(format!("dossier-sounds-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("a folder");
        let choice = SkinChoice::Folder(dir.clone());
        assert_eq!(choice.samples_dir(), Some(dir.as_path()));
        assert_eq!(SkinChoice::Classic.samples_dir(), None);
    }
}
