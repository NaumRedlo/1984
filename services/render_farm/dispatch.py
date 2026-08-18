"""Render this somewhere — a worker if one is listening, here if not.

Drop-in for `services.dossier.runner.video`: same arguments, same return, so
the handler that renders a replay does not learn which machine did it. That is
the whole point of the shape. The engine's own contract — a command line in, a
stream of events out, a file on disk — is identical on both hosts, and the only
question this module answers is which host runs it.

Falling back is not an error path here, it is the ordinary one. The laptop is
somebody's laptop: shut, on battery, in low power mode, or simply not running a
worker. Every one of those has to end with a rendered video, a few seconds
later than it might have been.
"""

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, Optional

from config.settings import RENDER_WORKER_TOKEN, RENDER_WORKER_WAIT
from services.dossier import runner, skins
from services.dossier.runner import Progress, RenderResult
from services.render_farm.queue import State, queue
from utils.logger import get_logger

logger = get_logger("services.render_farm.dispatch")

# How often the waiter looks at the job. Polling rather than more events: the
# states it has to tell apart — claimed, given back, lease expired, finished,
# withdrawn — do not map onto one flag apiece, and a quarter of a second of
# latency is invisible next to a render.
_TICK = 0.25


# How a file the bot holds is named inside the text a worker receives.
TEMPLATE = "{{%s}}"


def bundle(
    leaderboard: Optional[str],
    my_pictures: tuple[Optional[str], Optional[str]],
    skin: Optional[str] = None,
):
    """Swap every local file path for a name the worker can ask us for.

    A scoreboard is a TSV whose last two columns are paths to an avatar and a
    cover *on this host*, and the player's own two pictures are the same kind
    of thing. Sending the text alone gave a worker paths that mean nothing on
    its machine, which is why scoreboard renders used to stay here — but the
    bot builds a scoreboard for every render it does, so "stay here" meant the
    feature never ran at all.

    They are small: eight rows at most, a 128px avatar and a 512x160 cover
    each. Sending them costs a fraction of what the finished video does.

    Returns the templated text, the templated pair, and what each name means
    here. Names are ours, not the worker's, so nothing it says can name a file
    we did not choose to offer.
    """
    assets: dict[str, str] = {}

    seen: dict[str, str] = {}

    def name_for(path: Optional[str]) -> str:
        if not path or not os.path.isfile(path):
            return ""
        # One name per file, not per mention. A player's own avatar is also
        # their row's avatar, so without this the same picture is named twice
        # and fetched twice.
        if path not in seen:
            seen[path] = f"a{len(assets)}"
            assets[seen[path]] = path
        return TEMPLATE % seen[path]

    lines = []
    for line in (leaderboard or "").splitlines():
        columns = line.split("\t")
        # name, total, accuracy, mods, avatar, cover — the last two are paths.
        if len(columns) >= 6:
            columns[4], columns[5] = name_for(columns[4]), name_for(columns[5])
        lines.append("\t".join(columns))

    mine = (name_for(my_pictures[0]), name_for(my_pictures[1]))

    # The skin travels as one archive rather than as its files. A worker fetches
    # these over the network and a skin is a couple of hundred pictures: a round
    # trip each would cost far more than the pictures themselves. The hash goes
    # with it so a worker that already has this skin skips the fetch.
    skin_name, skin_hash = None, None
    if skin and os.path.isdir(skin):
        got = skins.packed(os.path.basename(skin.rstrip(os.sep)))
        if got:
            archive, skin_hash = got
            skin_name = name_for(archive)
    return (
        ("\n".join(lines) if leaderboard else None),
        mine,
        assets,
        (skin_name, skin_hash),
    )


def _progress_of(raw: dict[str, Any]) -> Optional[Progress]:
    try:
        clip = raw.get("clip")
        return Progress(
            done=int(raw["done"]), total=int(raw["total"]),
            fps=float(raw.get("fps") or 0.0),
            seconds_left=float(raw.get("seconds_left") or 0.0),
            clip=tuple(clip) if clip else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _wait_for_worker(
    job,
    timeout: float,
    on_progress: Optional[Callable[[Progress], Awaitable[None]]],
) -> Optional[dict[str, Any]]:
    """Watch a job until a worker finishes it or it becomes clear none will.

    None means "render it here". The two ways to get there are nobody claiming
    it, and somebody claiming it and then going quiet — a laptop that closed
    its lid mid-render looks exactly like a laptop that never answered, and
    both have to end the same way.
    """
    started = monotonic()
    unclaimed_since: Optional[float] = started
    last_progress = None

    while True:
        # Before the check for a finished job, not after it. A worker sends its
        # last progress and the file itself moments apart, and a loop that
        # returned first would drop every update that shared a tick with the
        # result — which, on a render short enough to fit in one tick, is all
        # of them.
        if on_progress and job.progress and job.progress != last_progress:
            last_progress = job.progress
            told = _progress_of(job.progress)
            if told:
                await on_progress(told)

        if job.settled.is_set():
            return None if job.withdrawn else job.payload

        queue.sweep()
        now = monotonic()
        if job.state is State.WAITING:
            # A job that was claimed and came back starts this clock again, so
            # a worker that dies costs one more wait rather than the whole
            # render timeout.
            unclaimed_since = unclaimed_since or now
            if now - unclaimed_since > RENDER_WORKER_WAIT:
                logger.info("job %s: nobody took it, rendering here", job.id)
                return None
        else:
            unclaimed_since = None

        if now - started > timeout:
            logger.warning("job %s: worker overran, rendering here", job.id)
            return None

        await asyncio.sleep(_TICK)


async def exhibit(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    title: str = "",
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: Optional[str] = None,
    leaderboard: Optional[str] = None,
    my_pictures: tuple[Optional[str], Optional[str]] = (None, None),
    budget_s: Optional[int] = None,
    clip_s: Optional[int] = None,
    chosen: Optional[runner.Selection] = None,
    on_progress: Optional[Callable[[Progress], Awaitable[None]]] = None,
    background: bool = False,
    bare: bool = False,
    effects: Optional[str] = None,
    music: Optional[int] = None,
    hitsounds: Optional[int] = None,
    map_hitsounds: bool = True,
) -> runner.ReelResult:
    """A reel, on a worker if one is listening.

    The selection never travels. `exhibit` does not hand the engine a list of
    moments — it hands it a replay and the engine chooses, and that choice is
    deterministic, so a worker running the same engine over the same replay
    picks the same seconds. What the caller's `chosen` is for is the *answer*:
    the bot named those moments in the message somebody has been staring at,
    and it must get back the selection it already showed rather than a second
    one that happens to agree.

    So the job carries one extra word, `kind`, and nothing else changes.
    """
    if chosen is None:
        # Judging, not rendering: seconds on the bot's own host, and the price
        # of not having to trust a worker to tell us what it chose.
        chosen = await runner.moments(replay_path, songs_dir, budget_s=budget_s, clip_s=clip_s)
    if not chosen.clips:
        raise runner.DossierError("в этом реплее нечего показать — он короче одного клипа")

    result = await _remote_or_local(
        "exhibit",
        replay_path,
        songs_dir,
        out_path,
        title=title,
        size=size,
        fps=fps,
        mute=mute,
        skin=skin,
        leaderboard=leaderboard,
        my_pictures=my_pictures,
        on_progress=on_progress,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
        local=lambda: runner.exhibit(
            replay_path, songs_dir, out_path,
            size=size, fps=fps, mute=mute, skin=skin, leaderboard=leaderboard,
            my_pictures=my_pictures, budget_s=budget_s, clip_s=clip_s,
            chosen=chosen, on_progress=on_progress,
            background=background, bare=bare, effects=effects,
            music=music, hitsounds=hitsounds, map_hitsounds=map_hitsounds,
        ),
    )
    # A local run answers with the reel *and* its selection; a remote one
    # answers with the reel alone, and the selection is the one we already had.
    return result if isinstance(result, runner.ReelResult) else runner.ReelResult(result, chosen)


async def video(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    title: str = "",
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: Optional[str] = None,
    leaderboard: Optional[str] = None,
    my_pictures: tuple[Optional[str], Optional[str]] = (None, None),
    on_progress: Optional[Callable[[Progress], Awaitable[None]]] = None,
    background: bool = False,
    bare: bool = False,
    effects: Optional[str] = None,
    music: Optional[int] = None,
    hitsounds: Optional[int] = None,
    map_hitsounds: bool = True,
) -> RenderResult:
    return await _remote_or_local(
        "video",
        replay_path,
        songs_dir,
        out_path,
        title=title,
        size=size,
        fps=fps,
        mute=mute,
        skin=skin,
        leaderboard=leaderboard,
        my_pictures=my_pictures,
        on_progress=on_progress,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
        local=lambda: runner.video(
            replay_path, songs_dir, out_path,
            size=size, fps=fps, mute=mute, skin=skin, leaderboard=leaderboard,
            my_pictures=my_pictures, on_progress=on_progress,
            background=background, bare=bare, effects=effects,
            music=music, hitsounds=hitsounds, map_hitsounds=map_hitsounds,
        ),
    )


async def _remote_or_local(
    kind: str,
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    title: str,
    size: str,
    fps: int,
    mute: bool,
    skin: Optional[str],
    leaderboard: Optional[str],
    my_pictures: tuple[Optional[str], Optional[str]],
    on_progress: Optional[Callable[[Progress], Awaitable[None]]],
    background: bool,
    bare: bool,
    effects: Optional[str],
    music: Optional[int],
    hitsounds: Optional[int],
    map_hitsounds: bool,
    local,
):
    """Offer the job out, and do it here if nobody takes it.

    Shared by both kinds of render because everything about *where* a render
    happens is the same for both — only the engine command differs, and that
    travels as one word in the job. Two copies of this would drift, and the way
    they would drift is that one of them would quietly stop falling back.
    """
    if RENDER_WORKER_TOKEN:
        board, mine, assets, (skin_name, skin_hash) = bundle(leaderboard, my_pictures, skin)
        job = queue.offer(
            replay_path,
            title,
            {
                "kind": kind,
                "size": size,
                "fps": fps,
                "mute": mute,
                # A skin the worker must fetch travels as a name it asks for.
                # Anything else goes as it stands — a path only this host knows,
                # which the worker recognises as not-for-it and falls back from.
                "skin": skin_name or skin,
                "skin_hash": skin_hash,
                "leaderboard": board,
                "my_pictures": list(mine),
                "background": background,
                "bare": bare,
                "effects": effects,
                "music": music,
                "hitsounds": hitsounds,
                "map_hitsounds": map_hitsounds,
            },
            assets=assets,
        )
        try:
            payload = await _wait_for_worker(job, runner._VIDEO_TIMEOUT_SECONDS, on_progress)
        finally:
            queue.withdraw(job.id)

        if payload:
            produced = payload.get("path")
            if produced and os.path.isfile(produced):
                # Moved, not copied: the upload already wrote it once.
                shutil.move(produced, out_path)
                meta = payload.get("meta") or {}
                logger.info("job %s (%s) rendered by %s", job.id, kind, job.worker)
                return RenderResult(
                    report=list(meta.get("report") or []),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    duration=meta.get("duration"),
                )
            logger.warning("job %s came back without a file", job.id)

    return await local()
