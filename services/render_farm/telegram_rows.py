import asyncio
import os
import time
from dataclasses import asdict
from datetime import timedelta
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from sqlalchemy import and_, delete, or_, select

from db.models.telegram_task import TelegramTask
from services.outbox import Handler, Outbox
from services.render_farm.telegram import Asleep, Chat, Media, Sent, Thumb
from utils.logger import get_logger
from utils.signals import Signals
from utils.timeutils import utcnow
from utils.ttl_cache import TTLCache

logger = get_logger("render_farm.telegram_rows")

ASKED = "telegram_asked"
ANSWERED = "telegram_answered"

QUICK = 20.0
SLOW = 900.0
CLAIM_WITHIN = 3.0
ASLEEP_FOR = 15.0
POLL = 0.5
IDLE = 5.0
AT_ONCE = 16
SWEEP_EVERY = 600.0
KEPT = timedelta(hours=1)
MEMBER_KEEP = 60.0
LEFT_KEEP = 20.0

Linked = Callable[[int, str, Optional[tuple[int, int]]], Awaitable[None]]


class Refused(Exception):
    pass


def _media(told: Optional[dict]) -> Optional[Media]:
    if not told:
        return None
    thumb = told.get("thumbnail")
    return Media(**{**told, "thumbnail": Thumb(thumb["file_id"]) if thumb else None})


def _sent(told: dict) -> Sent:
    return Sent(int(told.get("message_id") or 0), told.get("kind"), _media(told.get("media")))


class _Opened:
    def __init__(self, rows: "Rows", key: str, path: str, handle, size: int):
        self._rows = rows
        self._key = key
        self._path = path
        self._handle = handle
        self.size = size

    async def chunks(self, size: int) -> AsyncIterator[bytes]:
        while True:
            chunk = await asyncio.to_thread(self._handle.read, size)
            if not chunk:
                return
            yield chunk

    async def release(self) -> None:
        await self._rows._release(self._key, self._path, self._handle)


class Rows:
    def __init__(self, factory, signals: Signals):
        self._factory = factory
        self._signals = signals
        self._asleep_until = 0.0
        self._fetching: dict[str, asyncio.Lock] = {}
        self._reading: dict[str, int] = {}
        self._inside = TTLCache(maxsize=4000, ttl=MEMBER_KEEP)
        self._outside = TTLCache(maxsize=4000, ttl=LEFT_KEEP)
        signals.listen(ANSWERED)

    def ready(self) -> bool:
        return time.monotonic() >= self._asleep_until

    def forget(self) -> None:
        self._inside.clear()
        self._outside.clear()
        self._asleep_until = 0.0

    async def _post(self, kind: str, payload: dict[str, Any], blob: Optional[bytes] = None,
                    patience: Optional[float] = None) -> int:
        now = utcnow()
        async with self._factory() as session:
            row = TelegramTask(kind=kind, payload=payload, blob=blob, status="pending", attempts=0,
                               next_attempt_at=now, created_at=now,
                               expires_at=now + timedelta(seconds=patience) if patience else None)
            session.add(row)
            await session.flush()
            await self._signals.announce(session, ASKED)
            await session.commit()
            task_id = row.id
        self._signals.heard(ASKED)
        return task_id

    async def _state(self, task_id: int):
        async with self._factory() as session:
            found = (await session.execute(
                select(TelegramTask.status, TelegramTask.last_error, TelegramTask.answer, TelegramTask.answer_blob)
                .where(TelegramTask.id == task_id)
            )).one_or_none()
        return found or ("gone", None, None, None)

    async def _drop(self, task_id: int, only_pending: bool = False) -> bool:
        wanted = [TelegramTask.id == task_id]
        if only_pending:
            wanted.append(TelegramTask.status == "pending")
        async with self._factory() as session:
            gone = await session.execute(delete(TelegramTask).where(*wanted))
            await session.commit()
        return bool(gone.rowcount)

    async def _ask(self, kind: str, payload: dict[str, Any], blob: Optional[bytes] = None,
                   patience: float = QUICK) -> tuple[dict[str, Any], Optional[bytes]]:
        if not self.ready():
            raise Asleep("telegram is not connected")
        task_id = await self._post(kind, payload, blob, patience)
        started = time.monotonic()
        try:
            while True:
                mark = self._signals.mark(ANSWERED)
                status, error, answer, answer_blob = await self._state(task_id)
                if status == "done":
                    return answer or {}, answer_blob
                if status != "pending" and status != "running":
                    raise Refused(error or "telegram refused")
                waited = time.monotonic() - started
                most = CLAIM_WITHIN if status == "pending" else patience
                if waited >= most:
                    if status == "running":
                        raise Asleep("the bot did not answer in time")
                    if await self._drop(task_id, only_pending=True):
                        self._asleep_until = time.monotonic() + ASLEEP_FOR
                        raise Asleep("the bot does not take requests")
                    continue
                await self._signals.wait(mark, max(0.01, min(POLL, most - waited)))
        finally:
            await asyncio.shield(self._drop(task_id))

    async def is_member(self, chat_id: int, telegram_id: int) -> bool:
        key = (chat_id, telegram_id)
        if key in self._inside:
            return True
        if key in self._outside:
            return False
        try:
            answer, _ = await self._ask("member", {"chat": chat_id, "user": telegram_id})
        except (Asleep, Refused) as exc:
            logger.warning("membership of %s in %s is unknown: %s", telegram_id, chat_id, exc)
            return False
        inside = bool(answer.get("is"))
        (self._inside if inside else self._outside)[key] = True
        return inside

    async def describe(self, chat_id: int) -> Chat:
        answer, _ = await self._ask("describe", {"chat": chat_id})
        return Chat(**answer)

    async def photo(self, chat_id: int) -> Optional[bytes]:
        _, blob = await self._ask("photo", {"chat": chat_id})
        return blob

    async def small(self, file_id: str) -> bytes:
        _, blob = await self._ask("small", {"file": file_id})
        return blob or b""

    async def fetch(self, file_id: str, key: str) -> _Opened:
        async with self._fetching.setdefault(key, asyncio.Lock()):
            answer, _ = await self._ask("file", {"file": file_id}, patience=SLOW)
            path = str(answer.get("path") or "")
            handle = open(path, "rb")
            self._reading[path] = self._reading.get(path, 0) + 1
            return _Opened(self, key, path, handle, os.fstat(handle.fileno()).st_size)

    async def _release(self, key: str, path: str, handle) -> None:
        async with self._fetching.setdefault(key, asyncio.Lock()):
            handle.close()
            left = self._reading.get(path, 1) - 1
            if left > 0:
                self._reading[path] = left
                return
            self._reading.pop(path, None)
            try:
                os.unlink(path)
            except OSError:
                pass

    async def send_photo(self, chat_id: int, png: bytes, filename: str) -> Sent:
        answer, _ = await self._ask("send_photo", {"chat": chat_id, "filename": filename}, blob=png)
        return _sent(answer)

    async def send_video(self, chat_id: int, path: str, filename: str, caption: Optional[str] = None,
                         width: Optional[int] = None, height: Optional[int] = None,
                         duration: Optional[int] = None) -> Sent:
        answer, _ = await self._ask("send_video", {"chat": chat_id, "path": os.path.abspath(path), "filename": filename,
                                                   "caption": caption, "width": width, "height": height,
                                                   "duration": duration}, patience=SLOW)
        return _sent(answer)

    async def pass_on(self, chat_id: int, kind: Optional[str], file_id: str, caption: Optional[str] = None) -> Sent:
        answer, _ = await self._ask("pass_on", {"chat": chat_id, "kind": kind, "file": file_id, "caption": caption})
        return _sent(answer)

    async def linked(self, telegram_id: int, osu_username: str, link_msg: Optional[tuple[int, int]] = None) -> None:
        chat_id, message_id = link_msg or (None, None)
        await self._post("linked", {"user": telegram_id, "name": osu_username, "chat": chat_id, "message": message_id})


def _told(row) -> dict[str, Any]:
    return {"id": row.id, "kind": row.kind, "payload": row.payload, "attempts": row.attempts, "blob": row.blob}


def _due(now):
    return or_(TelegramTask.expires_at.is_(None),
               and_(TelegramTask.status == "pending", TelegramTask.expires_at > now))


def _once(row) -> bool:
    return row.expires_at is not None


def _answered(row, answer) -> None:
    told, blob = answer if isinstance(answer, tuple) else (answer, None)
    row.answer = told or {}
    row.answer_blob = blob
    row.blob = None


class Carrier:
    def __init__(self, factory, port, signals: Signals, linked: Optional[Linked] = None, idle: Optional[float] = None):
        self._factory = factory
        self._port = port
        self._signals = signals
        self._linked = linked
        self._idle = idle if idle is not None else (IDLE if signals.remote else POLL)
        self._handlers: dict[str, Handler] = {
            "member": self._member,
            "describe": self._describe,
            "photo": self._photo,
            "small": self._small,
            "file": self._file,
            "send_photo": self._send_photo,
            "send_video": self._send_video,
            "pass_on": self._pass_on,
        }
        if linked is not None:
            self._handlers["linked"] = self._tell_linked
        self.box = Outbox(TelegramTask, lambda: self._handlers, "telegram task", told=_told, due=_due, once=_once,
                          answered=_answered, settled=self._settled)
        signals.listen(ASKED)

    async def _settled(self, session, _row) -> None:
        await self._signals.announce(session, ANSWERED)

    async def _member(self, task):
        told = task["payload"]
        return {"is": bool(await self._port.is_member(told["chat"], told["user"]))}

    async def _describe(self, task):
        return asdict(await self._port.describe(task["payload"]["chat"]))

    async def _photo(self, task):
        return {}, await self._port.photo(task["payload"]["chat"])

    async def _small(self, task):
        return {}, await self._port.small(task["payload"]["file"])

    async def _file(self, task):
        return {"path": await self._port.local(task["payload"]["file"])}

    async def _send_photo(self, task):
        told = task["payload"]
        sent = await self._port.send_photo(told["chat"], task["blob"] or b"", told["filename"])
        return asdict(sent)

    async def _send_video(self, task):
        told = task["payload"]
        sent = await self._port.send_video(told["chat"], told["path"], told["filename"], caption=told.get("caption"),
                                           width=told.get("width"), height=told.get("height"),
                                           duration=told.get("duration"))
        return asdict(sent)

    async def _pass_on(self, task):
        told = task["payload"]
        return asdict(await self._port.pass_on(told["chat"], told.get("kind"), told["file"], told.get("caption")))

    async def _tell_linked(self, task):
        told = task["payload"]
        where = (told["chat"], told["message"]) if told.get("chat") is not None and told.get("message") is not None else None
        await self._linked(told["user"], told["name"], where)
        return {}

    async def carry(self, task: dict[str, Any]) -> str:
        outcome = await self.box.carry(self._factory, task)
        self._signals.heard(ANSWERED)
        return outcome

    async def once(self) -> int:
        finished = 0
        for task in await self.box.claim(self._factory):
            if await self.carry(task) == "done":
                finished += 1
        return finished

    async def sweep(self, now=None) -> int:
        now = now or utcnow()
        async with self._factory() as session:
            gone = await session.execute(delete(TelegramTask).where(or_(
                and_(TelegramTask.status.in_(("done", "failed")), TelegramTask.created_at < now - KEPT),
                TelegramTask.expires_at < now - KEPT,
            )))
            await session.commit()
        return int(gone.rowcount or 0)

    async def run(self, shutdown: asyncio.Event) -> None:
        running: set[asyncio.Task] = set()
        swept = 0.0
        stopping = asyncio.create_task(shutdown.wait())
        try:
            while not shutdown.is_set():
                mark = self._signals.mark(ASKED)
                try:
                    for task in await self.box.claim(self._factory, limit=AT_ONCE - len(running)):
                        job = asyncio.create_task(self.carry(task), name=f"telegram_task_{task['id']}")
                        running.add(job)
                        job.add_done_callback(running.discard)
                    if time.monotonic() - swept >= SWEEP_EVERY:
                        swept = time.monotonic()
                        await self.sweep()
                except Exception as exc:
                    logger.error("the telegram carrier stumbled: %s", exc, exc_info=True)
                asked = asyncio.create_task(mark.wait())
                await asyncio.wait({stopping, asked}, timeout=self._idle, return_when=asyncio.FIRST_COMPLETED)
                asked.cancel()
        finally:
            stopping.cancel()
            if running:
                _, late = await asyncio.wait(running, timeout=5.0)
                for job in late:
                    job.cancel()
