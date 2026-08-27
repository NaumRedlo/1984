"""Hand a render to one of the machines people have lent, and wait for it.

Drop-in for `dossier.runner.video`: same arguments, same return, so the handler
that renders a replay does not learn which machine did it.

**This host does not render.** It used to: a job nobody claimed within twelve
seconds was drawn here instead, and falling back was the ordinary path rather
than the error one. That was right while the farm was one laptop and a maybe.
It is wrong now — the server has one core, a render is minutes of it, and the
whole point of the engine having a release is that the drawing happens on
machines with something to draw with.

So what is left here is the waiting, and the waiting has to be honest. A job
nobody takes is a person watching a message that will not change, and the two
things they need to know are that nobody is on the farm right now and that
this will not go on for ever. Both are said out loud rather than inferred from
silence.
"""

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


class Waiting(NamedTuple):
    """What to tell somebody whose render has not started.

    Two numbers rather than one, because "third in the queue" and "nobody is
    here" want different sentences and the difference is not something a
    position can carry. A queue of one behind two busy machines is a wait; a
    queue of one behind no machines at all is a person who should be told to
    come back later.
    """

    ahead: int
    workers: int


def _where_it_stands(job) -> Waiting:
    """How many jobs are in front of this one, and how many machines are here.

    Both read at the moment of asking rather than remembered. Workers come and
    go on a minute's timer and the queue moves on its own, so a number kept
    from the last tick is a number that was true then.
    """
    line = queue.waiting()
    try:
        ahead = next(at for at, other in enumerate(line) if other.id == job.id)
    except StopIteration:
        # Claimed between the sweep and this look, which is the good outcome.
        ahead = 0
    return Waiting(ahead=ahead, workers=len(roster.here()))


class NobodyCame(runner.DossierError):
    """No machine took this job, and now none is going to.

    A `DossierError` because that is what the handler already knows how to
    show, and because from the person's side it is the same kind of news: the
    render is not happening, and here is why.
    """


async def _watch(
    job,
    on_progress: Optional[Callable[[Progress], Awaitable[None]]],
    on_queue: Optional[Callable[[Waiting], Awaitable[None]]],
) -> dict[str, Any]:
    """Watch a job until a worker finishes it, or nobody ever does.

    The only clock kept here is how long the job has gone unclaimed. Everything
    else the queue already owns and owns better: a lease that returns a job
    when its worker goes quiet, a cap on how many machines may fail at it, and
    an age past which nothing is offered at all. Two modules counting the same
    seconds is two modules that will one day disagree about them.
    """
    unclaimed_since: Optional[float] = monotonic()
    last_progress = None
    last_told = 0.0

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
            if job.withdrawn or not job.payload:
                # The queue gave up on it: too old, or too many machines failed
                # at it. Either way nothing is coming and this host is not
                # going to draw it instead.
                raise NobodyCame("задачу никто не довёл до конца")
            return job.payload

        queue.sweep()
        now = monotonic()
        if job.state is State.WAITING:
            # A job that was claimed and came back starts this clock again, so
            # a machine that dies costs one more wait rather than the whole
            # patience of the person watching.
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

            # Said only once the wait is long enough to be worth mentioning,
            # and then no oftener than the queue itself is worth re-reading.
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
    # The map's numbers, so a worker fetches it without osu! credentials.
    beatmap: Optional[dict[str, Any]] = None,
    # Told where this render stands while it waits for a machine to take it:
    # how many are in front, and how many machines are on the farm at all.
    # Never called for a job somebody claims straight away, because a wait
    # nobody had is not worth mentioning.
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
    # A local run answers with the reel *and* its selection; a remote one
    # answers with the reel alone, and the selection is the one we already had.
    return result if isinstance(result, runner.ReelResult) else runner.ReelResult(result, chosen)


# How often somebody waiting is told where they stand. Long enough not to be
# an edit per second against Telegram, short enough that a queue that is moving
# looks like one.
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
    # The map's numbers, so a worker fetches it without osu! credentials.
    beatmap: Optional[dict[str, Any]] = None,
    # Told where this render stands while it waits for a machine to take it:
    # how many are in front, and how many machines are on the farm at all.
    # Never called for a job somebody claims straight away, because a wait
    # nobody had is not worth mentioning.
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
    """Offer the job out and wait for a machine to do it.

    Shared by both kinds of render because everything about *where* a render
    happens is the same for both — only the engine command differs, and that
    travels as one word in the job. Two copies of this would drift.
    """
    if not RENDER_WORKER_TOKEN:
        # No token means the endpoints were never registered, so there is no
        # farm to offer anything to and nothing here that draws. Said plainly
        # rather than left as a job that waits half an hour for machines that
        # cannot reach this bot in the first place.
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
        # Whether it was delivered, refused or cancelled, this job stops being
        # on offer — otherwise a replay whose asker walked away goes on being
        # handed to machines that will render it for nobody.
        queue.withdraw(job.id)

    produced = payload.get("path")
    if not produced or not os.path.isfile(produced):
        logger.warning("job %s came back without a file", job.id)
        raise NobodyCame("машина взялась за задачу, но видео не прислала")

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
