"""Talking to the `dossier` binary.

Dossier is Rust; the bot is Python. Rather than bind the two together, the bot
runs the binary and reads a line of JSON back. That keeps the engine testable on
its own (`cargo test`, `dossier judge` on a folder of replays) and means a crash
in the simulator is a non-zero exit code, not a dead bot process.
"""

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from typing import NamedTuple, Optional

from config.settings import (
    DOSSIER_BIN,
    DOSSIER_CRF,
    DOSSIER_ENCODER_THREADS,
    DOSSIER_PRESET,
    DOSSIER_SKIN,
)
from utils.formatting.text import plural as _plural
from utils.logger import get_logger

logger = get_logger("services.dossier")

# A pathological map or a very long replay shouldn't be able to wedge a handler.
_TIMEOUT_SECONDS = 120

# Rendering is minutes of honest work, not a hung process. Long enough for a
# marathon map, short enough that a wedged encoder still gets cleaned up.
_VIDEO_TIMEOUT_SECONDS = 1800


class DossierError(RuntimeError):
    """The engine couldn't answer. The message is meant to be shown as-is to a
    render tester — they're the only ones who see it."""


def binary_path() -> str:
    return os.path.expanduser(DOSSIER_BIN)


def is_available() -> bool:
    path = binary_path()
    return os.path.isfile(path) and os.access(path, os.X_OK)


async def _launch(args: tuple[str, ...], timeout: int) -> tuple[int, str, str]:
    """Run the binary and hand back everything it said.

    Both streams are returned rather than judged here, because what counts as
    interesting depends on the command: `judge` speaks JSON on stdout, `video`
    writes a file and reports on stderr, and throwing either away on the
    success path is how diagnostics go missing.
    """
    path = binary_path()
    if not is_available():
        raise DossierError(
            f"движок не собран: {path} нет или он не исполняемый.\n"
            "Собрать: cd dossier && cargo build --release"
        )

    try:
        process = await asyncio.create_subprocess_exec(
            path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise DossierError(f"не удалось запустить движок: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise DossierError(f"движок не ответил за {timeout} с")

    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def _run(*args: str, timeout: int = _TIMEOUT_SECONDS) -> list[dict]:
    """Run a command that answers in JSON, one object per line."""
    _, stdout, stderr = await _launch(args, timeout)

    # A non-zero exit still carries usable JSON — `judge` fails the run when any
    # replay was skipped, but reports every replay it did manage. So parse
    # first and only complain if there's nothing to show.
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("dossier emitted a non-JSON line: %r", line[:200])

    if not results:
        raise DossierError((stderr.strip() or "движок ничего не вернул")[:500])
    return results


class Progress(NamedTuple):
    """Where a render has got to, as the engine last said."""

    done: int
    total: int
    fps: float
    seconds_left: float
    # Which clip of a reel this is, as (n, of). None for an ordinary render.
    #
    # A reel is several renders in a row, so the frame counter runs 0..360 once
    # per clip and a progress line built from it alone counts to a hundred
    # percent five times. That looks exactly like a render restarting.
    clip: tuple[int, int] | None = None

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0


def _progress_of(event: dict, clip: tuple[int, int] | None) -> Progress | None:
    """One `progress` event, as far as this side is concerned.

    A malformed event is dropped rather than raised on: the render itself is
    fine and the only thing at stake is a counter in a chat message.
    """
    try:
        return Progress(
            int(event["frames"]),
            int(event["of"]),
            float(event["per_second"]),
            float(event["left_seconds"]),
            clip,
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("dossier sent a progress event this side cannot read: %r", event)
        return None


def _clip_of(event: dict) -> tuple[int, int] | None:
    try:
        return int(event["index"]), int(event["of"])
    except (KeyError, TypeError, ValueError):
        return None


def _polite_prefix() -> tuple[str, ...]:
    """How to ask for a lower share of the machine, on this machine.

    `nice` is POSIX and is not on Windows, where the equivalent is a creation
    flag rather than a wrapper — and where a worker with no wrapper simply
    competes on equal terms, which is what it did everywhere before there was a
    policy. Looked for rather than assumed: the path is not the same on every
    Linux either.
    """
    for candidate in ("/usr/bin/nice", "/bin/nice"):
        if os.access(candidate, os.X_OK):
            return (candidate, "-n", "10")
    found = shutil.which("nice")
    return (found, "-n", "10") if found else ()


async def _launch_watched(
    args: tuple[str, ...],
    timeout: int,
    on_progress: Callable[[Progress], Awaitable[None]] | None,
    polite: bool = False,
    prefix: tuple[str, ...] = (),
) -> tuple[int, str, list[dict]]:
    """Run the engine and watch it work.

    `communicate()` hands everything over at the end, which is fine for a
    command that answers in a second and useless for one that runs for minutes:
    what a render says is only worth anything while it is still saying it. So
    both of its channels are read as they arrive.

    They are two channels on purpose. **stderr** is what the engine says to a
    person — sentences, and a ticker that redraws one line — and the whole of it
    is kept for the report. **stdout**, under `--events`, is what it says to a
    program: one JSON object per line, flushed as it happens.

    This side used to read the person's channel with regular expressions, and
    the arrangement was quietly fragile in a way neither half could catch:
    rewording a progress line in Rust — a sentence, in a file about drawing
    frames — stopped the live counter in a Telegram chat, and every test on both
    sides went on passing, because neither side was wrong.
    """
    path = binary_path()
    if not is_available():
        raise DossierError(
            f"движок не собран: {path} нет или он не исполняемый.\n"
            "Собрать: cd dossier && cargo build --release"
        )
    # `nice` is free on an idle machine — measured at 0.82s against 0.86s for
    # the same encode — and under contention it is the thing that decides who
    # yields. So it is asked for exactly when a render shares a machine with
    # somebody who is using it, and never otherwise.
    # `prefix` is whatever the host wants wrapped round the render — on a laptop
    # that is `caffeinate`, so the machine cannot fall asleep halfway through and
    # wake to find the job handed to somebody else. Outside `nice`, which is
    # about how this process competes rather than about the wrapper.
    engine = (*prefix, *_polite_prefix(), path) if polite else (*prefix, path)
    argv = (*engine, *args)
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise DossierError(f"не удалось запустить движок: {exc}") from exc

    collected: list[str] = []
    events: list[dict] = []

    async def watch() -> None:
        """Events, as they happen."""
        clip: tuple[int, int] | None = None
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("dossier sent a non-event line: %r", text[:200])
                continue
            events.append(event)
            kind = event.get("event")
            if kind == "clip":
                # A reel announces each clip before drawing it, so whichever was
                # announced last is the one the frames belong to. Without this a
                # counter built from frames alone reaches a hundred per cent once
                # per clip, which reads as a render restarting.
                clip = _clip_of(event) or clip
            elif kind == "progress" and on_progress:
                progress = _progress_of(event, clip)
                if progress:
                    await on_progress(progress)

    async def keep() -> None:
        """The prose, kept whole for the report."""
        while True:
            block = await process.stderr.read(4096)
            if not block:
                break
            collected.append(block.decode("utf-8", "replace"))

    try:
        await asyncio.wait_for(asyncio.gather(watch(), keep(), process.wait()), timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise DossierError(f"движок не ответил за {timeout} с")
    except asyncio.CancelledError:
        # Somebody pressed cancel. Killing the engine is the whole point — an
        # abandoned render would otherwise keep a core busy for minutes while
        # the bot pretends it stopped, and on a one-core host that is the same
        # as the bot being down.
        process.kill()
        await process.wait()
        raise

    return process.returncode or 0, "".join(collected), events


def _report_lines(stderr: str) -> list[str]:
    """The engine's own account of a render, minus the progress ticker.

    Progress redraws one line with carriage returns, so splitting on those as
    well leaves the finished statements and drops the thousand partial ones.
    """
    lines = []
    for chunk in stderr.replace("\r", "\n").splitlines():
        chunk = chunk.strip()
        if chunk and "frames," not in chunk:
            lines.append(chunk)
    return lines


async def inspect(replay_path: str) -> dict:
    """Read the replay header. Needs no beatmap — this is how the caller learns
    which map to fetch."""
    return (await _run("inspect", "--json", replay_path))[0]


async def judge(replay_path: str, songs_dir: str) -> dict:
    """Judge the replay against whatever map in `songs_dir` matches its hash."""
    return (await _run("judge", "--json", "--songs", os.path.expanduser(songs_dir), replay_path))[0]


class RenderResult(NamedTuple):
    """What a finished render was, and what the engine said about making it.

    The dimensions and duration matter beyond the log: Telegram draws a video's
    placeholder from the numbers it is given, not from the stream, so a video
    sent without them arrives square on a phone and only corrects itself once
    playback starts.
    """

    report: list[str]
    width: int | None
    height: int | None
    duration: int | None


def _video_meta(events: list[dict]) -> tuple[int | None, int | None, int | None]:
    """The finished file's shape, as the process that wrote it reported it.

    From the engine rather than measured afterwards — it is the one that knows.
    Absent or malformed is not an error: the video still sends, it just goes
    without the hints.
    """
    # Backwards, because a reel reports this once per clip and then once more
    # for the file it cut them into. Reading forwards found the first clip and
    # labelled a seventy-second reel as ten seconds long — which Telegram
    # believes, and draws its scrubber from.
    for event in reversed(events):
        if event.get("event") != "video":
            continue
        try:
            return (
                int(event["width"]),
                int(event["height"]),
                round(float(event["seconds"])),
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("dossier sent a video event this side cannot read: %r", event)
            return None, None, None
    return None, None, None


def _render_args(
    command: str,
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    size: str,
    fps: int,
    mute: bool,
    skin: str | None,
    leaderboard: str | None,
    my_pictures: tuple[str | None, str | None],
    extra: tuple[str, ...] = (),
    threads: int | None = None,
    encoder_threads: int | None = None,
    background: bool = False,
    bare: bool = False,
    effects: str | None = None,
    music: int | None = None,
    hitsounds: int | None = None,
    map_hitsounds: bool = False,
) -> list[str]:
    """The command line a render is made of.

    Shared by `video` and `exhibit` because everything about how a frame is
    drawn is the same for both — the skin, the size, the scoreboard, the
    player's own face. Only which spans get drawn differs, and that is the one
    thing `extra` carries. Two copies of this list would drift, and the way they
    would drift is that a reel would quietly stop wearing the deployment's skin.
    """
    args = [
        command,
        # Both renders are watched, and this is what they are watched by: the
        # engine's stdout becomes a stream of events instead of prose nobody
        # was meant to parse.
        "--events",
        "--skin",
        skin or DOSSIER_SKIN,
        "--preset",
        DOSSIER_PRESET,
        "--crf",
        DOSSIER_CRF,
        "--songs",
        os.path.expanduser(songs_dir),
        "--size",
        size,
        "--fps",
        str(fps),
        *extra,
        "--out",
        out_path,
        replay_path,
    ]
    if mute:
        args.append("--mute")
    # Two flags rather than values, so they are absent unless asked for.
    if background:
        args.append("--background")
    if bare:
        args.append("--bare")
    # A value rather than a flag, and absent rather than empty when nobody has
    # chosen: an empty list is a viewer who switched everything off, and the
    # engine obeys that, so it must not be what "never asked" looks like.
    if effects is not None:
        args += ["--effects", effects]
    # Absent at the natural level rather than passed as 100: an untouched
    # setting should leave the engine's command the one it always was.
    if music is not None and music != 100:
        args += ["--music", str(music)]
    if hitsounds is not None and hitsounds != 100:
        args += ["--hitsounds", str(hitsounds)]
    # A flag rather than a value, so it is absent unless asked for: the engine
    # plays the skin alone without it, which is what danser does and what a
    # render is expected to sound like.
    if map_hitsounds:
        args.append("--map-hitsounds")
    # Written beside the output rather than passed on the command line: a chat's
    # worth of names is longer than an argument list wants to be, and a name can
    # contain anything.
    if leaderboard:
        path = os.path.join(os.path.dirname(out_path) or ".", "rivals.tsv")
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(leaderboard)
            args[1:1] = ["--leaderboard", path]
        except OSError as exc:
            logger.warning("could not write the scoreboard: %s", exc)
    # The player's own row is computed by the engine, so its pictures cannot ride
    # in on a line of the file — they come in on their own.
    if leaderboard and all(my_pictures):
        args[1:1] = ["--my-pictures", my_pictures[0], my_pictures[1]]
    # How much of the machine to use. The deployment's own setting is the
    # default; a render worker overrides it per job, because the answer on a
    # laptop depends on whether its owner is currently typing on it — see
    # `services.dossier.machine`.
    if threads:
        args[1:1] = ["--threads", str(threads)]
    cap = str(encoder_threads) if encoder_threads else DOSSIER_ENCODER_THREADS.strip()
    if cap:
        args[1:1] = ["--encoder-threads", cap]
    return args


async def video(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: str | None = None,
    leaderboard: str | None = None,
    my_pictures: tuple[str | None, str | None] = (None, None),
    on_progress: Callable[[Progress], Awaitable[None]] | None = None,
    threads: int | None = None,
    encoder_threads: int | None = None,
    polite: bool = False,
    prefix: tuple[str, ...] = (),
    background: bool = False,
    bare: bool = False,
    effects: str | None = None,
    music: int | None = None,
    hitsounds: int | None = None,
    map_hitsounds: bool = False,
) -> RenderResult:
    """Render the replay to `out_path`.

    Nothing is returned: the engine writes a file and reports progress on
    stderr. Minutes, not seconds — a two-minute map at 720p is around two and a
    half — so this gets its own timeout rather than the one sized for judging.

    The skin comes from settings rather than being fixed here: which look the
    bot renders in is a deployment's decision, not this function's.

    Returns what the engine said about the render — thread count, the timing
    breakdown — which is the only way to tell a render that is slow because the
    encoder is saturated from one that is slow because it is drawing on one
    core. It was being captured and discarded, so nobody could see either.
    """
    args = _render_args(
        "video",
        replay_path,
        songs_dir,
        out_path,
        size=size,
        fps=fps,
        mute=mute,
        skin=skin,
        leaderboard=leaderboard,
        my_pictures=my_pictures,
        threads=threads,
        encoder_threads=encoder_threads,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
    )

    code, stderr, events = await _launch_watched(
        tuple(args), _VIDEO_TIMEOUT_SECONDS, on_progress, polite=polite, prefix=prefix
    )
    report = _report_lines(stderr)

    if code != 0:
        # The report, not the raw tail. stderr is almost entirely the progress
        # ticker, so the last 500 characters of it are the last 500 characters
        # of "6600/6849 frames, 70/s, 4s left" — which is what a render tester
        # was shown when a render failed on a server, and it told them nothing.
        # `_report_lines` already drops the ticker; the reason is in what's left.
        said = "\n".join(report[-6:]).strip()
        raise DossierError(said or f"движок завершился с кодом {code} и ничего не сказал")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise DossierError("движок отработал, но файла нет")

    for line in report:
        logger.info("dossier: %s", line)
    width, height, duration = _video_meta(events)
    return RenderResult(report, width, height, duration)


class Moment(NamedTuple):
    """One stretch the engine chose, and why."""

    from_ms: float
    to_ms: float
    scorer: str
    # The engine's own sentence, in English. Kept as the fallback and for logs.
    reason: str
    # The numbers behind it, so this side can say the same thing in Russian.
    detail: dict
    # A second moment the same seconds turned out to hold — a strong jump
    # pattern is the hardest movement in the map *and* where the misses are, so
    # one clip says both. Nested rather than listed flat, because it shares the
    # first one's seconds and must not be counted as more of them.
    also: "Moment | None" = None

    def stamp(self) -> str:
        """`1:23` — where in the map it is, as the editor would say it."""
        total = max(self.from_ms, 0.0) / 1000.0
        return f"{int(total // 60)}:{int(total % 60):02d}"

    def say(self) -> str:
        """Why this moment was chosen, in the language the bot speaks.

        Built from the numbers rather than translated from the engine's
        sentence. The engine speaks English because a terminal is where it
        lives; the bot speaks Russian because that is who is reading it. A
        scorer this side does not recognise falls back to the engine's own
        words, which is a worse answer than a translation and a much better one
        than nothing.
        """
        say = _PHRASE.get(self.scorer)
        if not say:
            return self.reason
        try:
            return say(self.detail)
        except (KeyError, TypeError, ValueError):
            return self.reason


_PHRASE = {
    "kiai": lambda d: (
        f"кияй — {d['length_ms'] / 1000:.0f} с, отмеченные маппером, на {d['bpm']:.0f} BPM"
    ),
    "peak": lambda d: f"самая длинная серия игры, {d['combo']}x, кончается здесь",
    "choke": lambda d: (
        f"серия {d['combo']}x рвётся на {d['through'] * 100:.0f}% пути"
    ),
    "storm": lambda d: (
        f"самый плотный участок карты, {d['objects']} "
        f"{_plural(d['objects'], 'объект', 'объекта', 'объектов')}"
        if d.get("of_densest", 1.0) >= 0.999
        else f"плотный участок, {d['objects']} "
        f"{_plural(d['objects'], 'объект', 'объекта', 'объектов')} — "
        f"{d['of_densest'] * 100:.0f}% от самого плотного"
    ),
    "precision": lambda d: (
        f"{d['clicks']} {_plural(d['clicks'], 'клик', 'клика', 'кликов')} "
        f"со средней {d['mean_error_ms']:.1f} мс против {d['baseline_ms']:.1f} мс за игру"
    ),
    "scramble": lambda d: _scramble(d["misses"], d["refused"]),
    "opening": lambda d: (
        f"как игра начинается, {d['objects']} "
        f"{_plural(d['objects'], 'объект', 'объекта', 'объектов')} в кадре"
    ),
    "brink": lambda d: (
        f"полоса падает до {d['low']:.0f}% и возвращается к {d['recovered_to']:.0f}%"
    ),
    "finale": lambda d: _finale(d),
    "tapping": lambda d: (
        f"самый частый тап в игре, {d['taps']} "
        f"{_plural(d['taps'], 'нажатие', 'нажатия', 'нажатий')} "
        f"по {d['per_second']:.1f} в секунду"
        if d.get("of_hardest", 1.0) >= 0.999
        else (
            f"частый тап, {d['taps']} "
            f"{_plural(d['taps'], 'нажатие', 'нажатия', 'нажатий')} "
            f"по {d['per_second']:.1f} в секунду"
        )
    ),
    "travel": lambda d: (
        f"самое тяжёлое движение в игре, {d['speed']:.0f} osu!px в секунду"
        if d.get("of_fastest", 1.0) >= 0.999
        else f"тяжёлое движение, {d['speed']:.0f} osu!px в секунду"
    ),
}


def _finale(d: dict) -> str:
    if d.get("failed"):
        return f"игра обрывается — полоса пустеет на {d['combo']}x, {d['accuracy']:.2f}%"
    if d.get("full_combo"):
        return f"доигрывает — {d['combo']}x без единого срыва, {d['accuracy']:.2f}%"
    return f"чем всё кончается — {d['combo']}x, {d['accuracy']:.2f}%"


def _scramble(misses: int, refused: int) -> str:
    parts = []
    if misses:
        parts.append(
            f"{misses} {_plural(misses, 'промах', 'промаха', 'промахов')}"
        )
    if refused:
        parts.append(
            f"{refused} {_plural(refused, 'отказанный клик', 'отказанных клика', 'отказанных кликов')}"
        )
    return " и ".join(parts) + " подряд"


class Selection(NamedTuple):
    """What the engine chose, and the clock it chose it on.

    The rate travels with the clips because it has to: the spans are **map**
    time and a rate mod compresses them — six seconds of map under DoubleTime is
    four seconds of video. Without it a caller adding the spans up promises a
    minute and sends forty seconds.
    """

    clips: list[Moment]
    rate: float

    def watch_seconds(self) -> float:
        """How long these come to, in seconds of somebody watching."""
        span = sum(clip.to_ms - clip.from_ms for clip in self.clips)
        return span / 1000.0 / (self.rate or 1.0)


class ReelResult(NamedTuple):
    render: RenderResult
    selection: Selection


def _moments_of(answer: dict) -> Selection:
    return Selection(
        [
            Moment(
                float(clip.get("from_ms", 0.0)),
                float(clip.get("to_ms", 0.0)),
                str(clip.get("scorer", "?")),
                str(clip.get("reason", "")),
                clip.get("detail") or {},
                _also_of(clip),
            )
            for clip in answer.get("clips", [])
        ],
        float(answer.get("rate") or 1.0),
    )


def _also_of(clip: dict) -> "Moment | None":
    """The second moment of a merged clip, if it has one."""
    with_ = clip.get("with")
    if not with_:
        return None
    return Moment(
        float(clip.get("from_ms", 0.0)),
        float(clip.get("to_ms", 0.0)),
        str(with_.get("scorer", "?")),
        str(with_.get("reason", "")),
        with_.get("detail") or {},
    )


def _reel_args(budget_s: int | None, clip_s: int | None) -> list[str]:
    """The two knobs, passed only when somebody asked for them.

    Left alone, the engine decides how long a reel is from the play — a clean
    run of a quiet map has three things worth showing and a disaster on a
    marathon has a dozen. Passing a default from here would be this side
    guessing at an answer the other side computes.
    """
    args = []
    if budget_s is not None:
        args += ["--for", str(budget_s)]
    if clip_s is not None:
        args += ["--clip", str(clip_s)]
    return args


async def moments(
    replay_path: str,
    songs_dir: str,
    *,
    budget_s: int | None = None,
    clip_s: int | None = None,
) -> Selection:
    """Which seconds of the play are worth watching — chosen, not rendered.

    Seconds rather than minutes, because nothing is drawn: the engine judges the
    replay it would have judged anyway and then reads its own answer. That is
    what makes it worth asking for separately — the bot can say what it is about
    to render, and how long it will be, before spending the minutes on it.
    """
    answer = await _run(
        "exhibit",
        "--json",
        "--songs",
        os.path.expanduser(songs_dir),
        *_reel_args(budget_s, clip_s),
        replay_path,
    )
    return _moments_of(answer[0])


async def exhibit(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: str | None = None,
    leaderboard: str | None = None,
    my_pictures: tuple[str | None, str | None] = (None, None),
    budget_s: int | None = None,
    clip_s: int | None = None,
    chosen: Selection | None = None,
    on_progress: Callable[[Progress], Awaitable[None]] | None = None,
    threads: int | None = None,
    encoder_threads: int | None = None,
    polite: bool = False,
    prefix: tuple[str, ...] = (),
    background: bool = False,
    bare: bool = False,
    effects: str | None = None,
    music: int | None = None,
    hitsounds: int | None = None,
    map_hitsounds: bool = False,
) -> ReelResult:
    """Render the telling moments of the play and cut them into one reel.

    Asked for the selection first and the reel second — two runs of the same
    command, which agree because selection is deterministic and is the property
    the engine promises above all others. The alternative is to parse the list
    out of the render's own chatter, which would tie the bot's message to the
    engine's logging format.

    `chosen` is for a caller that has already asked. The bot has: it names the
    moments in the message somebody stares at for the minutes the render takes,
    and asking again here would judge the same replay a third time for an answer
    already in hand.
    """
    if chosen is None:
        chosen = await moments(replay_path, songs_dir, budget_s=budget_s, clip_s=clip_s)
    if not chosen.clips:
        raise DossierError(
            "в этом реплее нечего показать — он короче одного клипа"
        )

    args = _render_args(
        "exhibit",
        replay_path,
        songs_dir,
        out_path,
        size=size,
        fps=fps,
        mute=mute,
        skin=skin,
        leaderboard=leaderboard,
        my_pictures=my_pictures,
        extra=tuple(_reel_args(budget_s, clip_s)),
        threads=threads,
        encoder_threads=encoder_threads,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
    )

    code, stderr, events = await _launch_watched(
        tuple(args), _VIDEO_TIMEOUT_SECONDS, on_progress, polite=polite, prefix=prefix
    )
    report = _report_lines(stderr)

    if code != 0:
        said = "\n".join(report[-6:]).strip()
        raise DossierError(said or f"движок завершился с кодом {code} и ничего не сказал")
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise DossierError("движок отработал, но файла нет")

    for line in report:
        logger.info("dossier: %s", line)
    width, height, duration = _video_meta(events)
    return ReelResult(RenderResult(report, width, height, duration), chosen)


async def version() -> Optional[str]:
    """Best-effort build identity, for the status line. None when unavailable."""
    if not is_available():
        return None
    try:
        stat = os.stat(binary_path())
    except OSError:
        return None
    return f"{stat.st_size // 1024} KiB, mtime {int(stat.st_mtime)}"
