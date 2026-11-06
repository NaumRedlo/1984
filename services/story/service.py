import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from db.models.player import Player
from db.models.story import StoryProgress
from services.story import chapters as written
from services.story import trials
from services.story.engine import Assignment, Progress, StoryError, begin, choose, close, go_on, settle, shown
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("story")

FIRST = "prologue"
CHECK_EVERY = 15.0
RECENT_LIMIT = 50

_chapters: Optional[dict[str, written.Chapter]] = None
_checked: dict[int, float] = {}


def chapters() -> dict[str, written.Chapter]:
    global _chapters
    if _chapters is None:
        _chapters = written.load()
    return _chapters


def forget() -> None:
    global _chapters
    _chapters = None
    _checked.clear()


def _chapter(progress: Progress) -> written.Chapter:
    found = chapters().get(progress.chapter)
    if found is None:
        raise StoryError("LOST_STEP")
    return found


async def _load(session, player_id: int) -> Progress:
    row = await session.get(StoryProgress, player_id)
    if row is None:
        return begin(chapters()[FIRST])
    return Progress(row.chapter, row.step, set(row.flags or []), Assignment.of(row.task), list(row.done or []))


async def _save(session, player_id: int, progress: Progress) -> None:
    row = await session.get(StoryProgress, player_id)
    if row is None:
        row = StoryProgress(player_id=player_id)
        session.add(row)
    row.chapter = progress.chapter
    row.step = progress.step
    row.flags = sorted(progress.flags)
    row.task = progress.task.body() if progress.task is not None else None
    row.done = list(progress.done)


async def _level(session, player_id: int, slot: str) -> float:
    from services.duels import portfolio

    await portfolio.from_attempts(session, player_id)
    skills, _ = portfolio.profile(await portfolio.observations_of(session, player_id), utcnow())
    return float(skills[slot].theta)


async def _catalogue(session):
    from services.duels.coordinator import catalogue

    return await catalogue(session)


async def _assign(session, chapter: written.Chapter, progress: Progress, player_id: int, eased: bool, avoid: tuple[int, ...] = (),
                  under: Optional[float] = None) -> bool:
    task = chapter.steps[progress.step].task
    target = trials.aim(await _level(session, player_id, task.slot), task.below, eased)
    found = trials.pick(await _catalogue(session), task.slot, target, f"{player_id}:{chapter.id}:{progress.step}", avoid, under)
    if found is None:
        logger.warning("story: no map in %s near %.2f for player %s", task.slot, target, player_id)
        return False
    progress.task = Assignment(found.beatmap_id, round(float(found.stars), 2), datetime.now(timezone.utc).isoformat(), 0, eased)
    return True


async def _name(session, player_id: int) -> str:
    player = await session.get(Player, player_id)
    return (player.osu_username or "") if player is not None else ""


def _view(chapter: written.Chapter, progress: Progress, lang: str, player: str = "") -> dict[str, Any]:
    body = shown(chapter, progress, lang, player)
    if body["kind"] == "task" and progress.task is None:
        body["task"]["unavailable"] = True
    body["done"] = list(progress.done)
    return body


async def state(factory, player_id: int, lang: str) -> dict[str, Any]:
    async with factory() as session:
        async with session.begin():
            progress = await _load(session, player_id)
            chapter = _chapter(progress)
            if chapter.steps[progress.step].kind == "task" and progress.task is None:
                if await _assign(session, chapter, progress, player_id, eased=False):
                    await _save(session, player_id, progress)
            return _view(chapter, progress, lang, await _name(session, player_id))


async def _move(factory, player_id: int, lang: str, change: Callable[[written.Chapter, Progress], Progress]) -> dict[str, Any]:
    async with factory() as session:
        async with session.begin():
            progress = await _load(session, player_id)
            progress = change(_chapter(progress), progress)
            chapter = _chapter(progress)
            if chapter.steps[progress.step].kind == "task" and progress.task is None:
                await _assign(session, chapter, progress, player_id, eased=False)
            await _save(session, player_id, progress)
            return _view(chapter, progress, lang, await _name(session, player_id))


async def next_step(factory, player_id: int, step: Optional[str], lang: str) -> dict[str, Any]:
    return await _move(factory, player_id, lang, lambda chapter, progress: go_on(chapter, progress, step))


async def pick_option(factory, player_id: int, step: Optional[str], option: str, lang: str) -> dict[str, Any]:
    return await _move(factory, player_id, lang, lambda chapter, progress: choose(chapter, progress, option, step))


async def finish_chapter(factory, player_id: int, step: Optional[str], lang: str) -> dict[str, Any]:
    def change(chapter: written.Chapter, progress: Progress) -> Progress:
        following = chapter.steps.get(progress.step)
        after = chapters().get(following.next_chapter) if following is not None and following.next_chapter else None
        return close(chapter, progress, after, step)

    return await _move(factory, player_id, lang, change)


async def _scores(osu, osu_user_id: int, beatmap_id: int) -> tuple[list[dict], list[dict]]:
    recent: list[dict] = []
    try:
        recent = await osu.get_user_recent_scores(osu_user_id, limit=RECENT_LIMIT)
    except Exception as error:
        logger.warning("story: recent scores of %s failed: %s", osu_user_id, error)
    on_map: list[dict] = []
    try:
        on_map = await osu.get_user_beatmap_scores(beatmap_id, osu_user_id)
    except Exception as error:
        logger.warning("story: scores of %s on %s failed: %s", osu_user_id, beatmap_id, error)
    return recent or [], on_map or []


async def check(factory, osu, player_id: int, step: Optional[str], lang: str, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    now = clock()
    last = _checked.get(player_id)
    if last is not None and now - last < CHECK_EVERY:
        raise StoryError("TOO_SOON")
    _checked[player_id] = now
    async with factory() as session:
        async with session.begin():
            progress = await _load(session, player_id)
            chapter = _chapter(progress)
            if step is not None and step != progress.step:
                raise StoryError("STEP_MOVED")
            current = chapter.steps[progress.step]
            if current.kind != "task":
                raise StoryError("NOT_A_TASK")
            if progress.task is None:
                raise StoryError("NO_MAP")
            player = await session.get(Player, player_id)
            if player is None or not player.osu_user_id:
                raise StoryError("NOT_LINKED")
            since = trials.moment(progress.task.opened_at)
            if since is None:
                raise StoryError("NO_MAP")
            recent, on_map = await _scores(osu, int(player.osu_user_id), progress.task.beatmap_id)
            outcomes, tried = trials.best(current.task.goal, recent, progress.task.beatmap_id, since)
            if outcomes is None:
                outcomes, _ = trials.best(current.task.goal, on_map, progress.task.beatmap_id, since)
            if outcomes is not None:
                settle(chapter, progress, outcomes)
                after = _chapter(progress)
                if after.steps[progress.step].kind == "task" and progress.task is None:
                    await _assign(session, after, progress, player_id, eased=False)
                await _save(session, player_id, progress)
                return {**_view(after, progress, lang, await _name(session, player_id)), "checked": {"done": True, "outcomes": sorted(outcomes)}}
            tries = max(progress.task.tries, tried)
            progress.task.tries = tries
            eased = False
            if tries >= trials.EASIER_AFTER and not progress.task.eased:
                eased = await _assign(session, chapter, progress, player_id, eased=True, avoid=(progress.task.beatmap_id,),
                                      under=progress.task.stars)
                if not eased:
                    progress.task.eased = True
            await _save(session, player_id, progress)
            return {**_view(chapter, progress, lang, await _name(session, player_id)), "checked": {"done": False, "tries": tries, "eased": eased}}
