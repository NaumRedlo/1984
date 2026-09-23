import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, NamedTuple, Optional

from config.settings import RENDER_GIVE_UP, RENDER_WORKER_TOKEN, RENDER_WORKER_WAIT
from dossier import runner, skins
from dossier.runner import Progress, RenderResult
from services.render_farm.queue import State, queue
from services.render_farm.roster import roster
from utils.logger import get_logger

logger = get_logger("services.render_farm.dispatch")

_TICK = 0.25

TEMPLATE = "{{%s}}"

def bundle(
    leaderboard: Optional[str],
    my_pictures: tuple[Optional[str], Optional[str]],
    skin: Optional[str] = None,
):
    assets: dict[str, str] = {}

    seen: dict[str, str] = {}

    def name_for(path: Optional[str]) -> str:
        if not path or not os.path.isfile(path):
            return ""

        if path not in seen:
            seen[path] = f"a{len(assets)}"
            assets[seen[path]] = path
        return TEMPLATE % seen[path]

    lines = []
    for line in (leaderboard or "").splitlines():
        columns = line.split("\t")

        if len(columns) >= 6:
            columns[4], columns[5] = name_for(columns[4]), name_for(columns[5])
        lines.append("\t".join(columns))

    mine = (name_for(my_pictures[0]), name_for(my_pictures[1]))

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
    except (AttributeError, KeyError, TypeError, ValueError):
        return None

class Waiting(NamedTuple):

    ahead: int
    workers: int

def _where_it_stands(job) -> Waiting:
    line = queue.waiting()
    try:
        ahead = next(at for at, other in enumerate(line) if other.id == job.id)
    except StopIteration:

        ahead = 0
    return Waiting(ahead=ahead, workers=len(roster.here()))

class NobodyCame(runner.DossierError):
    pass

async def _watch(
    job,
    on_progress: Optional[Callable[[Progress], Awaitable[None]]],
    on_queue: Optional[Callable[[Waiting], Awaitable[None]]],
) -> dict[str, Any]:
    unclaimed_since: Optional[float] = monotonic()
    last_progress = None
    last_told = 0.0

    while True:

        if on_progress and job.progress and job.progress != last_progress:
            last_progress = job.progress
            told = _progress_of(job.progress)
            if told:
                await on_progress(told)

        if job.settled.is_set():
            if job.withdrawn or not job.payload:

                raise NobodyCame("задачу никто не довёл до конца")
            return job.payload

        queue.sweep()
        now = monotonic()
        if job.state is State.WAITING:

            unclaimed_since = unclaimed_since or now
            waited = now - unclaimed_since

            if waited > RENDER_GIVE_UP:
                standing = _where_it_stands(job)
                logger.info("job %s: nobody took it in %.0fs, giving up (%d here)",
                            job.id, waited, standing.workers)
                raise NobodyCame(
                    "ни один компьютер не взял эту задачу — попробуй позже"
                    if standing.workers
                    else "сейчас ни один компьютер не на связи — попробуй позже"
                )

            if on_queue and waited > RENDER_WORKER_WAIT and now - last_told >= TELL_EVERY_SECONDS:
                last_told = now
                await on_queue(_where_it_stands(job))
        else:
            unclaimed_since = None
            last_told = 0.0

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

    beatmap: Optional[dict[str, Any]] = None,

    on_queue: Optional[Callable[["Waiting"], Awaitable[None]]] = None,
    background: bool = False,
    bare: bool = False,
    effects: Optional[str] = None,
    music: Optional[int] = None,
    hitsounds: Optional[int] = None,
    map_hitsounds: bool = True,
    dim: Optional[int] = None,
    meter: Optional[int] = None,
    cursor: Optional[int] = None,
    blur: Optional[int] = None,
    volume: Optional[int] = None,
) -> runner.ReelResult:
    if chosen is None:

        chosen = await runner.moments(replay_path, songs_dir, budget_s=budget_s, clip_s=clip_s)
    if not chosen.clips:
        raise runner.DossierError("в этом реплее нечего показать — он короче одного клипа")

    result = await _on_the_farm(
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
        on_queue=on_queue,
        beatmap=beatmap,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
        dim=dim,
        meter=meter,
        cursor=cursor,
        blur=blur,
        volume=volume,
    )

    return result if isinstance(result, runner.ReelResult) else runner.ReelResult(result, chosen)

TELL_EVERY_SECONDS = 5.0

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

    beatmap: Optional[dict[str, Any]] = None,

    on_queue: Optional[Callable[["Waiting"], Awaitable[None]]] = None,
    background: bool = False,
    bare: bool = False,
    effects: Optional[str] = None,
    music: Optional[int] = None,
    hitsounds: Optional[int] = None,
    map_hitsounds: bool = True,
    dim: Optional[int] = None,
    meter: Optional[int] = None,
    cursor: Optional[int] = None,
    blur: Optional[int] = None,
    volume: Optional[int] = None,
) -> RenderResult:
    return await _on_the_farm(
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
        on_queue=on_queue,
        beatmap=beatmap,
        background=background,
        bare=bare,
        effects=effects,
        music=music,
        hitsounds=hitsounds,
        map_hitsounds=map_hitsounds,
        dim=dim,
        meter=meter,
        cursor=cursor,
        blur=blur,
        volume=volume,
    )

async def _on_the_farm(
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
    on_queue: Optional[Callable[[Waiting], Awaitable[None]]],
    beatmap: Optional[dict[str, Any]],
    background: bool,
    bare: bool,
    effects: Optional[str],
    music: Optional[int],
    hitsounds: Optional[int],
    map_hitsounds: bool,
    dim: Optional[int],
    meter: Optional[int],
    cursor: Optional[int],
    blur: Optional[int],
    volume: Optional[int],
):
    if not RENDER_WORKER_TOKEN:

        raise NobodyCame(
            "рендер-ферма не настроена: у бота нет RENDER_WORKER_TOKEN, "
            "и без него воркеры не могут к нему подключиться"
        )

    board, mine, assets, (skin_name, skin_hash) = bundle(leaderboard, my_pictures, skin)
    job = queue.offer(
        replay_path,
        title,
        {
            "kind": kind,
            "size": size,
            "fps": fps,
            "mute": mute,

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
            "dim": dim,
            "meter": meter,
            "cursor": cursor,
            "blur": blur,
            "volume": volume,
            "beatmap": beatmap,
        },
        assets=assets,
    )
    try:
        payload = await _watch(job, on_progress, on_queue)
    finally:

        queue.withdraw(job.id)

    produced = payload.get("path")
    if not produced or not os.path.isfile(produced):
        logger.warning("job %s came back without a file", job.id)
        raise NobodyCame("машина взялась за задачу, но видео не прислала")

    shutil.move(produced, out_path)
    meta = payload.get("meta") or {}
    logger.info("job %s (%s) rendered by %s", job.id, kind, job.worker)
    return RenderResult(
        report=list(meta.get("report") or []),
        width=meta.get("width"),
        height=meta.get("height"),
        duration=meta.get("duration"),
    )
