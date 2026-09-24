import asyncio
import os
import shutil
import tempfile
from time import monotonic
from typing import Any, Optional

from aiogram import Bot, types

from config import settings
from services.render_farm import members, skins
from services.render_farm.queue import STANDARD, Job, State, queue
from services.render_farm.roster import roster
from utils.i18n import t
from utils.logger import get_logger
from utils.osr import Header, header

logger = get_logger("services.render_farm.orders")

TICK = 2.0
EDIT_EVERY = 4.0

def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"

def title_of(head: Header, beatmap: Optional[dict[str, Any]]) -> tuple[str, Optional[int]]:
    if not beatmap:
        return (head.player or "?"), None
    beatmapset = beatmap.get("beatmapset") or {}
    artist = str(beatmapset.get("artist") or "").strip()
    name = str(beatmapset.get("title") or "").strip()
    version = str(beatmap.get("version") or "").strip()
    song = " - ".join(part for part in (artist, name) if part)
    if version:
        song = f"{song} [{version}]" if song else f"[{version}]"
    set_id = beatmap.get("beatmapset_id") or beatmapset.get("id")
    player = head.player or "?"
    return (f"{player} — {song}" if song else player), (int(set_id) if str(set_id or "").isdigit() else None)

def status_of(job: Job, lang: str) -> str:
    if job.state is State.CLAIMED:
        progress = job.progress or {}
        total = int(progress.get("total") or 0)
        done = int(progress.get("done") or 0)
        stage = str(progress.get("stage") or "")
        if total > 0 and stage == "drawing":
            share = min(100, int(done * 100 / total))
            left = float(progress.get("seconds_left") or 0.0)
            return t("farm.drawing", lang, title=job.title, worker=job.worker_name or "?", share=share, left=_clock(left))
        return t("farm.preparing", lang, title=job.title, worker=job.worker_name or "?")
    workers = roster.taking()
    if workers == 0:
        return t("farm.waiting_alone", lang, title=job.title, minutes=int(settings.RENDER_GIVE_UP // 60))
    return t("farm.waiting", lang, title=job.title, ahead=queue.ahead_of(job), workers=workers)

async def take(bot: Bot, message: types.Message, lang: str, osu_api_client=None) -> None:
    document = message.document
    person = message.from_user
    if document is None or person is None:
        return
    if message.chat.type == "private" and not await members.shares_a_group(bot, person.id):
        await message.reply(t("farm.members_only", lang))
        return
    if document.file_size and document.file_size > settings.RENDER_REPLAY_MOST:
        await message.reply(t("farm.too_big", lang))
        return
    if queue.open_for(person.id) >= settings.RENDER_ORDERS_EACH:
        await message.reply(t("farm.too_many", lang, n=settings.RENDER_ORDERS_EACH))
        return
    workdir = tempfile.mkdtemp(prefix="order-")
    path = os.path.join(workdir, "replay.osr")
    try:
        await bot.download(document, destination=path)
        with open(path, "rb") as source:
            head = header(source.read())
    except Exception as exc:
        logger.warning("could not take a replay from %s: %s", person.id, exc)
        shutil.rmtree(workdir, ignore_errors=True)
        await message.reply(t("farm.unreadable", lang))
        return
    if head is None:
        shutil.rmtree(workdir, ignore_errors=True)
        await message.reply(t("farm.unreadable", lang))
        return
    if head.mode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        await message.reply(t("farm.standard_only", lang))
        return
    beatmap = None
    if osu_api_client is not None:
        try:
            beatmap = await osu_api_client.lookup_beatmap_by_checksum(head.beatmap_md5)
        except Exception as exc:
            logger.info("no beatmap for %s: %s", head.beatmap_md5, exc)
    title, set_id = title_of(head, beatmap)
    skin = await skins.chosen_for(person.id)
    job = queue.offer(path, title, beatmap_md5=head.beatmap_md5, beatmapset_id=set_id, skin=skin,
                      requester=person.id, chat_id=message.chat.id)
    status = await message.reply(status_of(job, lang))
    asyncio.create_task(follow(bot, job, message, status, lang, workdir))

async def follow(bot: Bot, job: Job, asked: types.Message, status: types.Message, lang: str, workdir: str) -> None:
    said = status.text or ""
    edited = monotonic()
    unclaimed_since: Optional[float] = monotonic()
    try:
        while not job.settled.is_set():
            queue.sweep()
            now = monotonic()
            if job.state is State.WAITING:
                unclaimed_since = unclaimed_since or now
                if now - unclaimed_since > settings.RENDER_GIVE_UP:
                    queue.withdraw(job.id, "nobody")
                    break
            else:
                unclaimed_since = None
            fresh = status_of(job, lang)
            if fresh != said and now - edited >= EDIT_EVERY:
                try:
                    await status.edit_text(fresh)
                    said, edited = fresh, now
                except Exception:
                    pass
            try:
                await asyncio.wait_for(job.settled.wait(), TICK)
            except asyncio.TimeoutError:
                pass
        if job.withdrawn or not job.payload:
            key = "farm.nobody" if job.reason in ("nobody", "expired") else "farm.failed"
            try:
                await status.edit_text(t(key, lang, title=job.title, reason=job.reason or "?"))
            except Exception:
                pass
            return
        await deliver(bot, job, asked, status, lang)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

async def deliver(bot: Bot, job: Job, asked: types.Message, status: types.Message, lang: str) -> None:
    payload = job.payload or {}
    path = payload.get("path")
    meta = payload.get("meta") or {}
    duration = meta.get("duration")
    try:
        await asked.reply_video(
            types.FSInputFile(path, filename="render.mp4"),
            caption=t("farm.caption", lang, title=job.title, worker=job.worker_name or "?")[:1024],
            supports_streaming=True,
            width=STANDARD["width"],
            height=STANDARD["height"],
            duration=int(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        )
        try:
            await status.delete()
        except Exception:
            pass
        logger.info("job %s delivered to %s", job.id, job.chat_id)
    except Exception as exc:
        logger.warning("could not deliver job %s: %s", job.id, exc)
        try:
            await status.edit_text(t("farm.undelivered", lang, title=job.title))
        except Exception:
            pass
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
