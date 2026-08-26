#!/usr/bin/env python3
"""Render replays for the bot, on this machine.

Run from a checkout of this repo on whichever machine should do the rendering —
the point of it is a laptop that is several times the server the bot lives on.
It needs the engine built (`cargo build --release --manifest-path dossier/Cargo.toml`), the osu! API
credentials the bot uses, and the shared secret. Put them once in
`~/.dossier/worker.env` and there is nothing to type after that:

    RENDER_SERVER=https://example.org
    RENDER_WORKER_TOKEN=...
    OSU_CLIENT_ID=...
    OSU_CLIENT_SECRET=...

    $ ./venv/bin/python scripts/render_worker.py --check   # is this set up?
    $ ./venv/bin/python scripts/render_worker.py           # then run it

`--check` answers every question at once rather than one `SystemExit` at a
time, and it reaches the bot without claiming anybody's replay.

It pulls rather than listens. Nothing has to be reachable from outside, no port
is opened, no address has to stay put — which matters because the machine this
is for sits behind a home router and moves. A worker that is off simply stops
claiming, and the bot renders on its own host a few seconds later.

Runs on macOS, Linux and Windows. How hard it works is not this file's decision:
`services.dossier.machine` reads the battery, the energy mode, whether anyone is
at the keyboard and whether the machine is hot, and answers with thread counts —
or with a refusal, which is respected by not claiming anything at all. What each
platform can answer differs; the policy that reads the answers does not.
"""

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import sys
import zipfile
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402

from services.dossier import build as engine_build  # noqa: E402
from services.dossier import machine  # noqa: E402
from services.dossier import maps, runner, skins  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger("render_worker")

# A second. Long enough that an idle worker costs nothing — one request a
# second against a server that answers 204 in microseconds — and short enough
# that the wait before a render starts is not something anybody notices.
POLL_SECONDS = 1.0
# Well inside the server's lease. A render says nothing for long stretches while
# it encodes, so silence has to be reported deliberately rather than inferred.
HEARTBEAT_SECONDS = 20.0
# How often a worker standing by over a build mismatch looks again. Somebody
# has to rebuild for it to change, so this is paced for a person walking to
# another machine rather than for a poll.
MISMATCH_SECONDS = 30.0
# How often a worker that is declining work looks again. Nothing it is waiting
# on — a charger, a cooler room, a hand leaving the trackpad — changes in a
# second, and the poll is only still made at all so the farm can see it is here.
RESTING_SECONDS = 15.0

# Where a worker keeps what it would otherwise be told on the command line.
# The secret is the reason this file exists: a token pasted into a shell is a
# token in that shell's history, and telling somebody to export four variables
# every time they open a terminal is telling them not to run a worker.
CONFIG = "~/.dossier/worker.env"


class Abandoned(Exception):
    """The bot stopped calling this job ours while we were still working on it.

    Not a failure and not something to hand back — by the time it happens the
    lease has expired and the job is somebody else's, or already rendered on the
    bot's own host. The only right answer is to stop and ask for another.
    """


class BuildMismatch(RuntimeError):
    """The server renders with a different build of the engine than this one.

    Fatal rather than a pause. The machine cannot fix this by waiting: somebody
    has to `git pull` and `cargo build` on one side or the other, and a worker
    that quietly polls for ever while the bot renders everything itself is a
    worker that looks like it is working.
    """


class Server:
    """The bot's render endpoints, as this worker sees them."""

    def __init__(self, base: str, token: str, name: str) -> None:
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "X-Render-Worker": name}
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, *_):
        await self.session.close()

    async def claim(self, engine: str | None, capacity=None) -> dict | None:
        """Ask for a job, saying which build of the engine will do it and what
        this machine is currently willing to give.

        The server compares that against its own and turns away a worker whose
        binary is not the same — see `services/dossier/build.py`. A refusal
        reads like nothing to do, because from the worker's side that is what it
        is; the reason is logged once rather than every poll.
        """
        told = {"engine": engine}
        if capacity is not None:
            # Sent even when it says no. A worker that declines by going quiet
            # is a worker nobody can tell from one that is switched off, and
            # "three machines are here and all on battery" wants a different
            # reaction from "nobody is here".
            told["capacity"] = {
                "take": bool(capacity.take),
                "reason": capacity.reason,
                # And the same thing as a word, so the bot can say it in
                # whatever language the person looking at the farm reads. A
                # worker too old to send this leaves the sentence to stand.
                "code": capacity.code,
                "detail": capacity.detail,
                "threads": int(capacity.threads or 0),
                "polite": bool(capacity.polite),
            }
        async with self.session.post(
            f"{self.base}/render/claim", json=told
        ) as reply:
            if reply.status == 204:
                return None
            if reply.status == 401:
                raise SystemExit("the server rejected the token")
            if reply.status == 409:
                body = await reply.json()
                raise BuildMismatch(body.get("reason", "the builds do not match"))
            reply.raise_for_status()
            return await reply.json()

    async def fetch_replay(self, job_id: str, into: str) -> None:
        async with self.session.get(f"{self.base}/render/job/{job_id}/replay") as reply:
            reply.raise_for_status()
            with open(into, "wb") as handle:
                async for chunk in reply.content.iter_chunked(1 << 16):
                    handle.write(chunk)

    async def fetch_asset(self, job_id: str, name: str, into: str) -> None:
        async with self.session.get(
            f"{self.base}/render/job/{job_id}/file/{name}"
        ) as reply:
            reply.raise_for_status()
            with open(into, "wb") as handle:
                async for chunk in reply.content.iter_chunked(1 << 16):
                    handle.write(chunk)

    async def heartbeat(self, job_id: str, progress: dict | None = None) -> bool:
        """False means the job stopped being ours and the render should stop."""
        try:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/heartbeat",
                json={"progress": progress},
            ) as reply:
                return reply.status == 200
        except aiohttp.ClientError as exc:
            # A blip is not a lost job: the lease outlives several of these, and
            # abandoning a half-finished render over one failed request would
            # throw away minutes of work.
            logger.warning("heartbeat failed: %s", exc)
            return True

    async def deliver(self, job_id: str, path: str, meta: dict) -> None:
        with open(path, "rb") as handle:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/result",
                data=handle,
                headers={"X-Render-Meta": json.dumps(meta),
                         "Content-Type": "application/octet-stream"},
            ) as reply:
                reply.raise_for_status()

    async def give_back(self, job_id: str, reason: str) -> None:
        try:
            async with self.session.post(
                f"{self.base}/render/job/{job_id}/give-back", json={"reason": reason}
            ):
                pass
        except aiohttp.ClientError as exc:
            # The lease expiring does the same thing a moment later, so this is
            # a courtesy rather than the mechanism.
            logger.warning("could not hand job %s back: %s", job_id, exc)


def _skin_cache() -> str:
    """Where skins the bot sent are kept between renders.

    Keyed by the hash the job carries, so the same skin is fetched once however
    many replays are rendered in it — a skin is megabytes and a render is
    seconds, and downloading it every time would be most of the wait.
    """
    return os.path.expanduser("~/.dossier/worker-skins")


def _localised_skin(settings: dict, here: dict) -> str | None:
    """Where this job's skin landed on this machine, or `None` for the engine's
    own look.

    Three cases and they are all ordinary: no skin at all, a skin already in the
    cache, and one that arrived with the job. A fourth — a skin named but not
    sent — falls back rather than failing, since a render in the wrong skin
    beats no render.
    """
    named = settings.get("skin")
    if not named:
        return None
    if not named.startswith("{{"):
        # A path only the bot's own host knows. Not ours to guess at.
        return None

    digest = settings.get("skin_hash") or "unknown"
    if not digest.isalnum():
        logger.warning("odd skin hash %r", digest)
        return None
    folder = os.path.join(_skin_cache(), digest)
    if os.path.isdir(folder):
        logger.info("skin %s already here", digest)
        return folder

    archive = here.get(named.strip("{}"))
    if not archive or not os.path.isfile(archive):
        logger.warning("job named a skin that did not arrive")
        return None

    staging = folder + ".incoming"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as pack:
            for item in pack.infolist():
                if item.is_dir():
                    continue
                leaf = os.path.basename(item.filename)
                if not leaf:
                    continue
                with pack.open(item) as source, open(
                    os.path.join(staging, leaf), "wb"
                ) as sink:
                    shutil.copyfileobj(source, sink)
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("skin %s would not unpack: %s", digest, exc)
        shutil.rmtree(staging, ignore_errors=True)
        return None

    # Before it is swapped in, so a skin is never in the cache half-readable.
    # The engine decodes WAV and nothing else; a skin ships `.ogg`, and whether
    # the bot happened to have swept its store before it built this zip is not
    # something a render should depend on.
    converted = skins.convert_folder(staging)

    os.makedirs(_skin_cache(), exist_ok=True)
    os.replace(staging, folder)
    logger.info(
        "skin %s unpacked%s",
        digest,
        f", {converted} sample(s) converted" if converted else "",
    )
    return folder


# Failures a worker hits over and over, and the one thing each of them means.
# A machine that hands back every job is usually not failing at rendering — it
# is missing a program or a credential, and the exception for that says so only
# to somebody who already knew.
_HINTS = (
    ("ffmpeg", "ffmpeg is not on PATH — a skin's samples cannot be converted "
               "and the audio cannot be muxed"),
    ("dossier", "the engine may not be built: cargo build --release --manifest-path dossier/Cargo.toml"),
    ("401", "the osu! API refused this worker's credentials — check "
            "OSU_CLIENT_ID and OSU_CLIENT_SECRET"),
    ("No space left", "the disk is full where this worker renders"),
)


def hint(exc: Exception) -> None:
    """Say what a repeated failure probably is, right next to the failure."""
    said = str(exc)
    for needle, meaning in _HINTS:
        if needle in said:
            logger.warning("  ^ %s", meaning)
            return


async def _render(server: Server, job: dict, capacity, api) -> bool:
    """Do one job, or hand it back saying why. True if a video was delivered.

    The answer is only ever counted — a worker that fails a job asks for the
    next one either way, since the failure is usually the job's and the machine
    is still good.
    """
    job_id = job["id"]
    workdir = tempfile.mkdtemp(prefix="render-worker-")
    replay = os.path.join(workdir, "replay.osr")
    out = os.path.join(workdir, "render.mp4")
    # Set the moment the bot stops calling this job ours. Anything still
    # running for it is then work nobody will collect.
    lost = asyncio.Event()

    async def on_progress(told) -> None:
        if lost.is_set():
            return
        if not await server.heartbeat(job_id, {
            "done": told.done, "total": told.total, "fps": told.fps,
            "seconds_left": told.seconds_left,
            "clip": list(told.clip) if told.clip else None,
        }):
            lost.set()

    async def keep_alive() -> None:
        """Say we are here even while the engine has nothing to report."""
        while not lost.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not lost.is_set() and not await server.heartbeat(job_id):
                lost.set()

    try:
        await server.fetch_replay(job_id, replay)

        # The scoreboard's pictures, and the player's own. The job refers to
        # them as `{{a0}}` and such; each is fetched by that name and the name
        # is swapped for where it landed here. Names are the server's own —
        # checked all the same, since they end up in a filename.
        here = {}
        for name in job.get("assets") or []:
            if not name.isalnum():
                logger.warning("job %s offered an odd asset name %r", job_id, name)
                continue
            # The skin comes as a zip; everything else is a picture. The name
            # the job used says which, since the skin's asset is the one the
            # settings point at.
            suffix = "zip" if f"{{{{{name}}}}}" == job["settings"].get("skin") else "png"
            landed = os.path.join(workdir, f"{name}.{suffix}")
            await server.fetch_asset(job_id, name, landed)
            here[name] = landed

        # A skin arrives as one archive rather than as its files, so it is not
        # a `.png` like the rest and is not localised the same way.
        def localise(text):
            for name, path in here.items():
                text = text.replace("{{%s}}" % name, path)
            # Anything still templated names a file the server did not send.
            # Left as an empty column rather than a path that does not exist:
            # the engine draws an empty frame, which is honest.
            return re.sub(r"\{\{a\d+\}\}", "", text)

        settings = job["settings"]
        skin = _localised_skin(settings, here)
        board = settings.get("leaderboard")
        board = localise(board) if board else None
        mine = tuple(localise(p) or None for p in (settings.get("my_pictures") or ["", ""]))

        header = await runner.inspect(replay)
        # The replay names its map by hash and nothing else, so the worker
        # fetches it the same way the bot would — which is why nothing but the
        # `.osr` has to cross the network.
        await maps.ensure_map(api, header.get("beatmap_hash") or "")

        # Hold the machine awake for exactly as long as the engine runs. A
        # laptop that sleeps mid-render wakes to find the job long since
        # given to somebody else — see `machine.awake`, which is a command
        # prefix on macOS and Linux and a flag held in this process on
        # Windows.
        with machine.awake() as stay_awake:
            watcher = asyncio.create_task(keep_alive())
            # A reel is several renders cut together, and the engine does the
            # cutting — so the only difference here is which command is run. The
            # moments it will choose are not sent: selection is deterministic, and
            # the bot keeps the list it already showed somebody rather than trusting
            # this machine to report the same one.
            engine = runner.exhibit if job["settings"].get("kind") == "exhibit" else runner.video
            render = asyncio.create_task(engine(
                replay, maps.songs_dir(), out,
                size=settings.get("size") or "1280x720",
                fps=int(settings.get("fps") or 60),
                mute=bool(settings.get("mute")),
                background=bool(settings.get("background")),
                bare=bool(settings.get("bare")),
                # Not coerced to text: `None` is a job from a bot that has never
                # been asked, and the engine's own defaults are the right answer to
                # that. `""` is somebody who switched every one of them off.
                effects=settings.get("effects"),
                music=settings.get("music"),
                hitsounds=settings.get("hitsounds"),
                # Absent means a bot older than the setting, and the answer for one
                # of those is the engine's own default.
                map_hitsounds=bool(settings.get("map_hitsounds", True)),
                dim=settings.get("dim"),
                meter=settings.get("meter"),
                cursor=settings.get("cursor"),
                blur=settings.get("blur"),
                volume=settings.get("volume"),
                skin=skin,
                leaderboard=board,
                my_pictures=mine,
                on_progress=on_progress,
                threads=capacity.threads,
                encoder_threads=capacity.encoder_threads,
                polite=capacity.polite,
                prefix=stay_awake,
            ))
            # Whichever comes first: the render, or the bot deciding this is no
            # longer our job. Losing it used to change nothing at all — the flag was
            # set and the engine went on drawing for minutes, on battery, for a file
            # the bot would refuse. Cancelling reaches the engine as a
            # `CancelledError`, which it already answers by killing the process.
            gone = asyncio.create_task(lost.wait())
            try:
                await asyncio.wait({render, gone}, return_when=asyncio.FIRST_COMPLETED)
                if not render.done():
                    render.cancel()
                    raise Abandoned("задачу забрали, пока шёл рендер")
                result = render.result()
            finally:
                lost.set()
                for task in (watcher, gone):
                    task.cancel()
                if not render.done():
                    render.cancel()
                # Awaited so the engine is actually gone before the workdir under
                # its output is removed.
                await asyncio.gather(render, watcher, gone, return_exceptions=True)

        # `exhibit` answers with the reel and its selection; only the reel
        # travels back, since the selection is already on the other side.
        made = getattr(result, "render", result)
        await server.deliver(job_id, out, {
            "report": made.report, "width": made.width,
            "height": made.height, "duration": made.duration,
        })
        logger.info("job %s delivered", job_id)
        return True
    except Abandoned as exc:
        # Nothing to hand back: it stopped being ours before we got here, and
        # the bot has already moved on. Said out loud because a worker going
        # quiet mid-render otherwise looks like the worker failing.
        logger.info("job %s: %s", job_id, exc)
        await asyncio.sleep(POLL_SECONDS)
        return False
    except (runner.DossierError, maps.MapUnavailable, aiohttp.ClientError, OSError) as exc:
        logger.warning("job %s handed back: %s", job_id, exc)
        hint(exc)
        await server.give_back(job_id, str(exc))
        # Not straight back to asking. Whatever went wrong is usually still
        # wrong a moment later, and a worker that fails and immediately reaches
        # for the same job again spins several times a second.
        await asyncio.sleep(POLL_SECONDS)
        return False
    except Exception as exc:  # noqa: BLE001 — see below
        # A bug, not a condition. The list above names the ways a render is
        # *expected* to fail; anything else is this file being wrong, and the
        # first time it happened — a keyword argument one of the two engine
        # commands did not have — it escaped to `main` and killed the worker
        # outright, leaving the job leased and the session unclosed. The bot
        # then waited out a lease for a machine that no longer existed.
        #
        # A worker is a daemon on somebody's laptop. Whatever it gets wrong
        # about one job, the job goes back and the machine keeps answering:
        # the bot's fallback is what turns this into a slower render instead
        # of no render. Logged with its traceback, because unlike the failures
        # above this one has nobody to read it but us.
        logger.exception("job %s failed on this worker", job_id)
        await server.give_back(job_id, f"воркер не справился: {exc}")
        await asyncio.sleep(POLL_SECONDS)
        return False
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def fingerprint(secret: str) -> str:
    """A short, shareable name for a secret, which is never the secret.

    Two sides that disagree about a token cannot compare it by pasting it into
    a chat, and "the token was rejected" says nothing about *which* of the two
    is wrong. Eight hex characters of a hash and the length settle it: equal
    fingerprints and it is not the token, different ones and somebody has the
    wrong string — and a length that is one longer than expected is a quote or
    a newline that came along for the ride.
    """
    if not secret:
        return "nothing"
    import hashlib

    short = hashlib.sha256(secret.encode()).hexdigest()[:8]
    return f"{len(secret)} chars, {short}"


def where(path: str) -> str:
    """A path with `~` expanded and its separators the ones this system uses.

    Written with forward slashes here because that is how a constant is
    written, and expanded on Windows into `C:\\Users\\name/.dossier/worker.env`
    — which works and reads as something broken. Asked about within a minute of
    the first person seeing it.
    """
    return os.path.normpath(os.path.expanduser(path))


def read_pairs(path: str) -> dict[str, str]:
    """`KEY=value` lines from a file, or `{}` when there is no such file.

    Missing is not an error. Everything in it can be given another way, and a
    worker on a server has its variables from systemd.
    """
    try:
        # `utf-8-sig` rather than `utf-8`, because Notepad writes a byte-order
        # mark and nothing on Windows warns anybody about it. With plain utf-8
        # that mark lands on the front of the first key, so `RENDER_SERVER`
        # arrives as `\ufeffRENDER_SERVER` and is silently not the key
        # anybody meant. The file looks perfect in the editor.
        with open(where(path), "r", encoding="utf-8-sig") as handle:
            lines = handle.readlines()
    except OSError:
        return {}

    found: dict[str, str] = {}
    for line in lines:
        # A no-break space is what a browser leaves behind when a line is
        # copied out of a web page, and it is not what `strip()` removes by
        # default on a key.
        line = line.replace("\u00a0", " ").strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        # Quotes are stripped because somebody who pasted a token out of a
        # password manager has quite likely pasted the quotes with it.
        found[key.strip()] = value.strip().strip("'\"")
    return found


def load_config(path: str) -> str | None:
    """Put the file's settings in the environment, once. Returns where it read.

    This is for the things that identify the worker — the server, the token,
    the credentials. They are read at startup and never again, because they
    cannot change without the worker being a different worker.

    The real environment wins over the file. A variable exported for one run —
    a different server, a token being tested — is somebody being deliberate,
    and a config that overrode it would be a config with no way round it.

    The *limits* in the same file are not read this way; see `asked_for`.
    """
    pairs = read_pairs(path)
    if not pairs:
        return None
    for key, value in pairs.items():
        if key and key not in os.environ:
            os.environ[key] = value
    return where(path)


def _limits_read(limits: machine.Limits) -> str:
    """The limits in one line, for the log that says they changed."""
    said = []
    if limits.paused:
        said.append("paused")
    if limits.polite:
        said.append("polite")
    if limits.threads:
        said.append(f"at most {limits.threads} threads")
    if limits.hours:
        said.append("between {:02d}:00 and {:02d}:00".format(*limits.hours))
    return ", ".join(said) or "no limits"


def asked_for(path: str, options) -> machine.Limits:
    """What the owner of this machine wants, right now.

    Re-read on every poll rather than at startup, and deliberately not through
    the environment: once a value is in `os.environ` a second read cannot
    change it, and the whole point of these four is that they change. Somebody
    should be able to pause a render farm from a text editor, and have it take
    effect before they have finished saving the file.

    The command line is the starting position and the file overrides it, so
    `--polite` at launch still means what it says while `RENDER_POLITE=0` in
    the file can take it back without a restart.
    """
    pairs = read_pairs(path)

    def flag(key: str, unless: bool) -> bool:
        said = pairs.get(key)
        if said is None:
            return unless
        return said.strip().lower() in ("1", "true", "yes", "on")

    threads = options.threads
    if pairs.get("RENDER_THREADS", "").strip().isdigit():
        threads = int(pairs["RENDER_THREADS"])

    return machine.Limits(
        polite=flag("RENDER_POLITE", options.polite),
        threads=max(0, threads),
        hours=machine.parse_hours(pairs.get("RENDER_HOURS", "")),
        paused=flag("RENDER_PAUSE", False),
    )


class Check:
    """One line of `--check`: what was asked, how it went, and what to do.

    A remedy rather than a failure, because the failures this catches are all
    somebody's setup and every one of them has a fix that fits on a line. The
    whole list is printed whatever happens — finding out about a missing token,
    fixing it, and then finding out about the engine is how a first evening
    gets spent.
    """

    __slots__ = ("name", "ok", "said", "fix")

    def __init__(self, name: str, ok: bool | None, said: str, fix: str = "") -> None:
        self.name, self.ok, self.said, self.fix = name, ok, said, fix

    def __str__(self) -> str:
        mark = "?" if self.ok is None else ("+" if self.ok else "!")
        line = f" [{mark}] {self.name}: {self.said}"
        return line + (f"\n       -> {self.fix}" if self.fix and not self.ok else "")


async def check(options) -> int:
    """Say whether this machine could take work, and what is stopping it.

    Everything is asked, nothing is claimed. The bot is reached through
    `/render/hello`, which answers the same two questions `claim` would — is
    the token good, do the builds agree — without a replay being involved.
    """
    # Read before `load_config` puts them in the environment, so what is
    # reported is what the *file* said rather than what is set by the time
    # anybody looks.
    in_file = read_pairs(options.config)
    found = load_config(options.config)
    options.server = options.server or os.getenv("RENDER_SERVER", "")
    # *After* the config has been loaded, and that is the whole of the fix.
    # This used to be sampled in `main` and handed in — before `load_config`
    # had run — so a token living in the file, which is where the guide puts
    # it, was reported missing to everybody. The file was even listed as
    # containing it two lines above.
    token = os.getenv("RENDER_WORKER_TOKEN", "")

    # `None` rather than `False`: everything in it can be given another way,
    # so a worker without one is not a worker with a problem.
    checks = [Check("config", True if found else None,
                    found or f"none at {where(options.config)} — "
                             f"the settings can live there instead of in the shell")]
    if found:
        # The keys, never the values. A file that has three of the four is the
        # commonest way to arrive here, and "token: missing" beside a config
        # marked `[+]` reads as the file having been ignored — which sends
        # somebody to check the file they just wrote instead of the line they
        # left out of it.
        wanted = ("RENDER_SERVER", "RENDER_WORKER_TOKEN",
                  "OSU_CLIENT_ID", "OSU_CLIENT_SECRET")
        missing = [key for key in wanted if not in_file.get(key)]
        checks.append(Check(
            "in that file", not missing,
            ", ".join(key for key in wanted if in_file.get(key)) or "nothing readable",
            "not there: " + ", ".join(missing) if missing else "",
        ))

    checks.append(Check("token", bool(token),
                        fingerprint(token) if token else "missing",
                        "RENDER_WORKER_TOKEN, the same one the bot has"))
    creds = bool(os.getenv("OSU_CLIENT_ID")) and bool(os.getenv("OSU_CLIENT_SECRET"))
    checks.append(Check("osu! api", creds, "set" if creds else "missing",
                        "OSU_CLIENT_ID and OSU_CLIENT_SECRET — the worker fetches "
                        "each map itself, so it needs its own credentials"))

    built = runner.is_available()
    checks.append(Check("engine", built,
                        runner.binary_path() if built else f"not at {runner.binary_path()}",
                        "cargo build --release --manifest-path dossier/Cargo.toml"))
    engine = await engine_build.local(refresh=True) if built else None
    checks.append(Check("build", engine is not None,
                        engine or "the engine would not say",
                        "rebuild it — an engine too old to answer --version is "
                        "too old to be trusted with a render"))

    checks.append(Check("ffmpeg", shutil.which("ffmpeg") is not None,
                        shutil.which("ffmpeg") or "not on PATH",
                        "needed to convert a skin's samples and to mux audio"))

    songs = where(maps.songs_dir())
    checks.append(Check("map store", os.path.isdir(songs) or _can_make(songs), songs,
                        "the worker downloads maps here and could not create it"))

    limits = asked_for(options.config, options)
    shut = limits.closed(datetime.now().hour)
    capacity = machine.Capacity(False, shut) if shut else machine.capacity(
        os.cpu_count() or 4, polite=limits.polite, ceiling=limits.threads
    )
    checks.append(Check(
        "this machine", capacity.take or None,
        f"{capacity.reason}" + (f", {capacity.threads} threads" if capacity.take else ""),
        ""))

    checks.extend(await _ask_the_bot(options, token, engine))

    print(f"dossier render worker — {options.name}")
    for line in checks:
        print(line)
    stopped = [c for c in checks if c.ok is False]
    unsure = [c for c in checks if c.ok is None and c.fix]
    if not stopped:
        # A `?` is not a blocker and must not read as one — but it must not be
        # skimmed past either. Somebody whose engine cannot say what it was
        # built from is one stale binary away from an evening, and "ready" on
        # its own is exactly what they would read.
        if unsure:
            count = len(unsure)
            print(f"\nready, but {count} thing{'' if count == 1 else 's'} "
                  f"above worth reading first")
        else:
            print("\nready — run it without --check")
        return 0
    count = len(stopped)
    print(f"\n{count} thing{'' if count == 1 else 's'} to fix "
          f"before this worker can render")
    return 1


def _can_make(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


async def _ask_the_bot(options, token: str, engine: str | None) -> list:
    """The two answers only the bot can give: is this token good, and do the
    builds match. Both are cheap and neither takes a job."""
    if not options.server:
        return [Check("the bot", False, "no server given",
                      "--server, or RENDER_SERVER in the config")]
    if not token:
        return [Check("the bot", None, "not asked — there is no token to ask with")]

    base = options.server.rstrip("/")
    try:
        async with aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {token}", "X-Render-Worker": options.name},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as session:
            async with session.get(
                f"{base}/render/hello", params={"engine": engine or ""}
            ) as reply:
                if reply.status == 401:
                    return [Check(
                        "the bot", False,
                        f"the token was rejected — this one is {fingerprint(token)}",
                        "compare that against what the bot logs at startup: same "
                        "fingerprint means the token is not the problem, and a "
                        "different one means somebody has the wrong string. A "
                        "length one longer than expected is a quote or a newline "
                        "that came along with it.",
                    )]
                if reply.status == 404:
                    return [Check("the bot", False, "reached, but it has no "
                                  "/render/hello", "the bot is older than this "
                                  "worker — update it")]
                reply.raise_for_status()
                said = await reply.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return [Check("the bot", False, f"could not reach {base}: {exc}",
                      "check the address, and that the bot is running")]

    checks = [Check("the bot", True,
                    f"{base}, {said.get('waiting', 0)} job(s) waiting")]

    # A build that cannot say what it is passes the comparison, on purpose:
    # neither side can tell, and a farm that stops because somebody built from
    # a tarball has failed at something that was never its business. But it
    # passes *silently*, and the first time that mattered it cost an evening —
    # a worker whose engine was months behind the bot's took a job, was handed
    # a flag it had never heard of, printed its usage and gave the job back.
    #
    # So it is said out loud here, where somebody is looking, rather than left
    # as a tick beside "builds agree".
    mine = engine_build.build_of(engine)
    theirs = said.get("build") or engine_build.UNKNOWN
    if engine_build.UNKNOWN in (mine, theirs):
        which = "this worker's" if mine == engine_build.UNKNOWN else "the bot's"
        checks.append(Check(
            "builds", None,
            f"{which} engine cannot say what it was built from, so nothing is "
            f"comparing them",
            "almost always a source tree with no git in it — a downloaded zip "
            "rather than a `git clone`. Clone the repository and build again, "
            "or this worker will render with whatever code it happens to have.",
        ))
    else:
        checks.append(Check("builds", bool(said.get("agree")),
                            said.get("reason") or "?",
                            "the reason says which side to rebuild"))
    return checks


def service(options) -> int:
    """Print the unit or plist that would keep this worker running.

    Printed rather than installed. This writes into the part of somebody's
    machine that decides what runs at boot, and a script that does that on its
    own — to a machine it was handed for rendering videos — has helped itself
    to more than it was lent. The two commands to install it are printed with
    it, and they take a second.

    Nothing here carries the token. It lives in the config file, which the
    worker reads for itself at startup, so a unit file can be pasted into a
    chat without anything going with it.
    """
    python = sys.executable
    script = os.path.abspath(__file__)
    root = os.path.dirname(os.path.dirname(script))
    args = [python, script]
    if options.server:
        args += ["--server", options.server]
    if options.name:
        args += ["--name", options.name]
    if options.polite:
        args.append("--polite")
    if options.threads:
        args += ["--threads", str(options.threads)]
    if options.config != CONFIG:
        args += ["--config", options.config]
    line = " ".join(args)

    if sys.platform == "darwin":
        where = os.path.expanduser("~/Library/LaunchAgents/org.dossier.worker.plist")
        body = "\n".join(
            ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
             '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
             '<plist version="1.0"><dict>',
             '  <key>Label</key><string>org.dossier.worker</string>',
             '  <key>ProgramArguments</key><array>']
            + [f"    <string>{arg}</string>" for arg in args]
            + ['  </array>',
               '  <key>WorkingDirectory</key>' f'<string>{root}</string>',
               # A worker that stops on a bad night should come back on its own.
               '  <key>KeepAlive</key><true/>',
               '  <key>RunAtLoad</key><true/>',
               f'  <key>StandardOutPath</key><string>{root}/worker.log</string>',
               f'  <key>StandardErrorPath</key><string>{root}/worker.log</string>',
               '</dict></plist>'])
        after = (f"launchctl unload {where} 2>/dev/null\n"
                 f"launchctl load -w {where}")
    else:
        where = os.path.expanduser("~/.config/systemd/user/dossier-worker.service")
        body = "\n".join([
            "[Unit]",
            "Description=dossier render worker",
            "After=network-online.target",
            "",
            "[Service]",
            f"ExecStart={line}",
            f"WorkingDirectory={root}",
            # Always, not on-failure: a build mismatch or a lost network are
            # both things this worker now sits through, and the ones it cannot
            # sit through are the ones worth coming back from.
            "Restart=always",
            "RestartSec=10",
            "",
            "[Install]",
            "WantedBy=default.target",
        ])
        after = ("systemctl --user daemon-reload\n"
                 "systemctl --user enable --now dossier-worker\n"
                 "# and, so it survives logging out:\n"
                 f"loginctl enable-linger {os.getenv('USER', 'you')}")

    print(f"# write this to {where}\n")
    print(body)
    print(f"\n# then:\n{after}")
    return 0


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--server", help="where the bot answers "
                                         "(default: RENDER_SERVER)")
    # `platform.node()` rather than `os.uname()`, which does not exist on
    # Windows at all — the worker is meant to run on whatever machine somebody
    # has spare.
    parser.add_argument("--name", default=platform.node() or "worker",
                        help="how to call this worker")
    parser.add_argument("--once", action="store_true", help="take one job and stop")
    parser.add_argument("--config", default=CONFIG,
                        help=f"where the settings are (default: {CONFIG})")
    parser.add_argument("--check", action="store_true",
                        help="say whether this machine is set up, and stop")
    # What the machine cannot be asked, its owner says. On Linux there is no
    # reading of "is anyone at the keyboard" that holds on a tty, on X and on
    # Wayland alike, so a desktop lending itself to the farm has had no way to
    # say so — this module's own comments have named this flag for some time
    # without it existing.
    parser.add_argument("--polite", action="store_true",
                        help="somebody is using this machine — take less of it")
    parser.add_argument("--threads", type=int, default=0, metavar="N",
                        help="never use more than N threads, whatever the policy says")
    parser.add_argument("--service", action="store_true",
                        help="print the unit that would keep this worker "
                             "running, and stop")
    options = parser.parse_args()

    if options.service:
        load_config(options.config)
        options.server = options.server or os.getenv("RENDER_SERVER", "")
        raise SystemExit(service(options))

    if options.check:
        # `check` reads the config itself, because it reports on the reading —
        # whether it found a file, and which keys the file gave it.
        raise SystemExit(await check(options))

    load_config(options.config)
    options.server = options.server or os.getenv("RENDER_SERVER", "")

    token = os.getenv("RENDER_WORKER_TOKEN", "")
    # One refusal at a time is how a first evening is spent. Everything that
    # can be known before the network is asked is asked here, and anything
    # missing points at the one command that answers all of it.
    missing = [what for what, got in (
        ("--server (or RENDER_SERVER)", options.server),
        ("RENDER_WORKER_TOKEN", token),
        ("OSU_CLIENT_ID", os.getenv("OSU_CLIENT_ID")),
        ("OSU_CLIENT_SECRET", os.getenv("OSU_CLIENT_SECRET")),
    ) if not got]
    if missing:
        raise SystemExit(
            f"not set: {', '.join(missing)}\n"
            f"put them in {options.config}, then `--check` to see the rest"
        )
    if not runner.is_available():
        raise SystemExit(f"the engine is not built: {runner.binary_path()}\n"
                         f"cargo build --release --manifest-path dossier/Cargo.toml")

    from utils.osu.api_client import OsuApiClient
    api = OsuApiClient()
    # Constructing it is not enough — the session and the token are made here,
    # exactly as the bot does at startup. Without this every map lookup dies on
    # a session that was never opened.
    await api.initialize()
    try:
        await _watch(options, token, api)
    finally:
        # `Server` closes its own session through `async with`; this one had
        # nobody. A worker that stops on a build mismatch left aiohttp
        # complaining about an unclosed session and connector on the way out,
        # which reads like the crash rather than the tidy exit it is.
        await api.close()


async def _watch(options, token: str, api) -> None:
    """Poll for jobs until told to stop. Split out so `main` can own the
    lifetimes of the things it opened."""
    cores = os.cpu_count() or 4
    refused = None
    standing_by = None
    told_so = None
    done = handed_back = 0

    # Asked once, here, rather than at every claim: the binary does not change
    # under a running process. A worker restarted after a rebuild says the new
    # thing, which is the only moment it could have changed anyway.
    engine = await engine_build.local()
    logger.info("engine: %s", engine or "could not be asked its version")

    async with Server(options.server, token, options.name) as server:
        logger.info("worker %s watching %s", options.name, options.server)
        while True:
            # Re-read every time round, so the machine can be handed back to
            # its owner — or lent harder — without stopping anything.
            limits = asked_for(options.config, options)
            if limits != told_so:
                if told_so is not None:
                    logger.info("limits changed: %s", _limits_read(limits))
                told_so = limits

            hour = datetime.now().hour
            shut = limits.closed(hour)
            capacity = (
                machine.Capacity(False, shut, code=limits.code(hour),
                                 detail=f"{limits.hours[0]:02d}:00–{limits.hours[1]:02d}:00"
                                 if limits.hours else "")
                if shut
                else machine.capacity(cores, polite=limits.polite, ceiling=limits.threads)
            )
            if not capacity.take and capacity.reason != refused:
                # Said once per change rather than every poll: this is the
                # normal state of a laptop on battery, not an incident.
                logger.info("not taking work: %s", capacity.reason)
                refused = capacity.reason
            if capacity.take:
                refused = None

            try:
                # Called even while declining, so the bot's farm view knows
                # this machine exists and why it is idle. The server answers a
                # declining worker the same way it answers an empty queue.
                job = await server.claim(engine, capacity)
            except BuildMismatch as exc:
                # This used to be fatal, and being fatal was wrong. Every change
                # to the engine killed every worker on the farm at once, and a
                # worker on somebody else's laptop dies quietly overnight — the
                # bot goes on rendering everything itself and nothing says why.
                #
                # So it stands by instead, and asks its own binary again each
                # time round: rebuilding is exactly what fixes this, and a
                # worker that comes back by itself afterwards is the difference
                # between a farm and a chore. `--once` still gives up, since
                # nobody is watching a single-shot run to see it recover.
                #
                # The reason carries its own remedy, because only the side that
                # made the comparison knows which of the two this is — see
                # `services/dossier/build.py`.
                if options.once:
                    raise SystemExit(f"this worker cannot take work: {exc}") from exc
                if str(exc) != standing_by:
                    logger.warning("standing by — %s", exc)
                    standing_by = str(exc)
                await asyncio.sleep(MISMATCH_SECONDS)
                # Re-asked rather than remembered: the binary can be rebuilt
                # under a running process, and that is the whole point.
                engine = await engine_build.local(refresh=True)
                continue
            except aiohttp.ClientError as exc:
                logger.warning("could not reach the bot: %s", exc)
                await asyncio.sleep(POLL_SECONDS)
                continue

            if standing_by is not None:
                logger.info("the builds agree again (%s) — taking work", engine)
                standing_by = None

            if job is None:
                await asyncio.sleep(
                    POLL_SECONDS if capacity.take else RESTING_SECONDS
                )
                continue

            logger.info("job %s (%s): %s, %s threads", job["id"], job.get("title") or "?",
                        capacity.reason, capacity.threads)
            if await _render(server, job, capacity, api):
                done += 1
            else:
                handed_back += 1
            # A running tally, because the alternative is a log a person has to
            # read backwards to answer "is my machine actually helping".
            logger.info("this worker: %s delivered, %s handed back", done, handed_back)
            if options.once:
                return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
