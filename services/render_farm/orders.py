import asyncio
import os
import shutil
import tempfile
from collections import Counter
from time import monotonic
from typing import Any, Optional

from aiogram import Bot, types
from sqlalchemy import select

from config import settings
from db.database import AsyncSessionFactory
from db.models.player import Player
from services.render_farm import app_storage, invites, members, skins
from services.render_farm.queue import RIVALS, STANDARD, Job, State, chat_rivals_of, queue, rivals_of
from services.render_farm.roster import roster
from utils.i18n import t
from utils.logger import get_logger
from utils.osr import Header, header

logger = get_logger("services.render_farm.orders")

TICK = 2.0
EDIT_EVERY = 4.0
_receiving = Counter()

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
        worker = job.worker_name or "?"
        if stage == "sending":
            return t("farm.sending", lang, title=job.title, worker=worker)
        if stage == "polishing":
            return t("farm.drawing", lang, title=job.title, worker=worker, share=100, left=_clock(0.0))
        if total > 0 and stage == "drawing":
            share = min(100, int(done * 100 / total))
            left = float(progress.get("seconds_left") or 0.0)
            return t("farm.drawing", lang, title=job.title, worker=worker, share=share, left=_clock(left))
        return t("farm.preparing", lang, title=job.title, worker=worker)
    workers = roster.taking()
    if workers == 0:
        return t("farm.waiting_alone", lang, title=job.title, minutes=int(settings.RENDER_GIVE_UP // 60))
    return t("farm.waiting", lang, title=job.title, ahead=queue.ahead_of(job), workers=workers)

async def board_chat(telegram_id: int, chat_id: int) -> int:
    from db.models.chat_member import ChatMember
    from services.render_farm import players
    from sqlalchemy import select

    if not telegram_id:
        return chat_id
    async with AsyncSessionFactory() as session:
        pinned = await players.pinned_chat(session, telegram_id)
        player = await players._player_of(session, telegram_id)
        if pinned is not None and player is not None:
            held = await session.scalar(select(ChatMember.id).where(ChatMember.player_id == player.id, ChatMember.chat_id == pinned))
            if held is not None:
                return pinned
    return chat_id

async def rivals_for(osu_api_client, beatmap: Optional[dict], head: Header, telegram_id: int, chat_id: int,
                     board: Optional[str] = None) -> list[dict[str, Any]]:
    beatmap_id = int((beatmap or {}).get("id") or 0)
    if not beatmap_id:
        return []
    if (board or skins.board_for(telegram_id)) == "chat" and head.mode == 0:
        chat_id = await board_chat(telegram_id, chat_id)
    if (board or skins.board_for(telegram_id)) == "chat" and chat_id < 0 and head.mode == 0:
        try:
            from services.render_farm import maps

            async with AsyncSessionFactory() as session:
                shown = await maps.board(session, osu_api_client, beatmap_id, chat_id, telegram_id)
            chat = chat_rivals_of(shown.get("rows"))
            if chat:
                return chat
        except Exception as exc:
            logger.info("no chat leaderboard for %s in %s: %s", beatmap_id, chat_id, exc)
    if osu_api_client is None:
        return []
    try:
        return rivals_of(await osu_api_client.get_beatmap_scores(beatmap_id, limit=RIVALS, mode="mania" if head.mode == 3 else "osu"))
    except Exception as exc:
        logger.info("no leaderboard for %s: %s", head.beatmap_md5, exc)
        return []

async def take(bot: Bot, message: types.Message, lang: str, osu_api_client=None) -> None:
    person = message.from_user
    document = message.document
    if person is None or document is None:
        return
    if document.file_size and document.file_size > settings.RENDER_REPLAY_MOST:
        await message.reply(t("farm.too_big", lang))
        return

    async def fetch(path: str) -> bool:
        try:
            await bot.download(document, destination=path)
            return True
        except Exception as exc:
            logger.warning("could not take a replay from %s: %s", person.id, exc)
            return False

    await order(bot, message, person, fetch, lang, osu_api_client)

async def order(bot: Bot, message: types.Message, person: types.User, fetch, lang: str, osu_api_client=None, missing: str = "farm.unreadable") -> None:
    if queue.open_for(person.id) + _receiving[person.id] >= settings.RENDER_ORDERS_EACH:
        await message.reply(t("farm.too_many", lang, n=settings.RENDER_ORDERS_EACH))
        return
    _receiving[person.id] += 1
    try:
        await _order(bot, message, person, fetch, lang, osu_api_client, missing)
    finally:
        _receiving[person.id] -= 1
        if not _receiving[person.id]:
            del _receiving[person.id]

async def _order(bot: Bot, message: types.Message, person: types.User, fetch, lang: str, osu_api_client=None, missing: str = "farm.unreadable") -> None:
    if message.chat.type == "private" and not await members.shares_a_group(bot, person.id):
        await message.reply(t("farm.members_only", lang))
        return
    async with AsyncSessionFactory() as session:
        player = (await session.execute(select(Player.id).where(Player.telegram_id == person.id))).scalar_one_or_none()
    in_app = player is not None and (person.id in invites.linked() or player in invites.linked_players())
    workdir = tempfile.mkdtemp(prefix="order-")
    job = None
    handed = False
    try:
        path = os.path.join(workdir, "replay.osr")
        try:
            if not await fetch(path):
                await message.reply(t(missing, lang))
                return
            with open(path, "rb") as source:
                head = header(source.read())
        except Exception as exc:
            logger.warning("could not read a replay from %s: %s", person.id, exc)
            await message.reply(t("farm.unreadable", lang))
            return
        if head is None:
            await message.reply(t("farm.unreadable", lang))
            return
        if head.mode not in (0, 3):
            await message.reply(t("farm.standard_only", lang))
            return
        beatmap = None
        if osu_api_client is not None:
            try:
                beatmap = await osu_api_client.lookup_beatmap_by_checksum(head.beatmap_md5)
            except Exception as exc:
                logger.info("no beatmap for %s: %s", head.beatmap_md5, exc)
        title, set_id = title_of(head, beatmap)
        rivals = await rivals_for(osu_api_client, beatmap, head, person.id, message.chat.id)
        skin = await skins.chosen_for(person.id)
        beatmapset = (beatmap or {}).get("beatmapset") or {}
        song = " - ".join(part for part in (str(beatmapset.get("artist") or "").strip(), str(beatmapset.get("title") or "").strip()) if part)
        meta = {"player": head.player, "song": song, "version": str((beatmap or {}).get("version") or ""), "map_hash": head.beatmap_md5}
        job = queue.offer(path, title, beatmap_md5=head.beatmap_md5, beatmapset_id=set_id, skin=skin,
                          requester=person.id, chat_id=message.chat.id, in_app=in_app, video_meta=meta, ruleset=head.mode,
                          leaderboard=rivals)
        if head.mode == 3:
            await message.reply(t("farm.mania_note", lang))
        status = await message.reply(status_of(job, lang))
        asyncio.create_task(follow(bot, job, message, status, lang, workdir))
        handed = True
    finally:
        if not handed:
            if job is not None:
                queue.withdraw(job.id, "submission_failed")
            shutil.rmtree(workdir, ignore_errors=True)

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

def _telegram_most() -> int:
    return 2000 * 1024 * 1024 if settings.TELEGRAM_BOT_API_URL else 48 * 1024 * 1024

async def deliver(bot: Bot, job: Job, asked: types.Message, status: types.Message, lang: str) -> None:
    payload = job.payload or {}
    meta = payload.get("meta") or {}
    duration = meta.get("duration")
    caption = t("farm.caption", lang, title=job.title, worker=job.worker_name or "?")
    if job.ruleset == 3:
        caption += t("farm.mania_caption", lang)
    if job.in_app:
        kept = app_storage.path_for(payload["video"]) if payload.get("video") is not None else None
        path = str(kept) if kept is not None and kept.is_file() and kept.stat().st_size <= _telegram_most() else None
        caption += t("farm.also_in_app", lang)
    else:
        path = payload.get("path")
    try:
        if path is None:
            await status.edit_text(t("farm.in_app", lang, title=job.title))
            logger.info("job %s appeared in the app for %s", job.id, job.requester)
            return
        await asked.reply_video(
            types.FSInputFile(path, filename="render.mp4"),
            caption=caption[:1024],
            supports_streaming=True,
            width=STANDARD["width"],
            height=STANDARD["height"],
            duration=int(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        )
        try:
            await status.delete()
        except Exception:
            pass
        logger.info("job %s delivered to %s%s", job.id, job.chat_id, " and the app" if job.in_app else "")
    except Exception as exc:
        logger.warning("could not deliver job %s: %s", job.id, exc)
        try:
            await status.edit_text(t("farm.in_app" if job.in_app else "farm.undelivered", lang, title=job.title))
        except Exception:
            pass
    finally:
        if path and not job.in_app:
            try:
                os.unlink(path)
            except OSError:
                pass
