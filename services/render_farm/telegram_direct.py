import asyncio
import os
from typing import AsyncIterator, Optional

from aiogram import Bot, types

from services.render_farm import members, videos
from services.render_farm.telegram import Chat, Media, Sent, Thumb

FETCH_PATIENCE = 900

_fetching: dict[str, asyncio.Lock] = {}
_reading: dict[str, int] = {}


def _local(path: str) -> bool:
    return bool(path) and os.path.isabs(path) and os.path.isfile(path)


def _plain(media) -> Optional[Media]:
    if media is None:
        return None
    thumb = getattr(media, "thumbnail", None)
    thumb_id = getattr(thumb, "file_id", None)
    return Media(
        file_id=media.file_id,
        file_unique_id=getattr(media, "file_unique_id", None),
        file_size=getattr(media, "file_size", None),
        duration=getattr(media, "duration", None),
        width=getattr(media, "width", None),
        height=getattr(media, "height", None),
        thumbnail=Thumb(thumb_id) if thumb_id else None,
    )


def _sent(message) -> Sent:
    kind, media = videos.media_of(message)
    return Sent(message.message_id, kind, _plain(media))


class _File:
    def __init__(self, bot: Bot, key: str, path: str, handle, size: int):
        self._bot = bot
        self._key = key
        self._path = path
        self._handle = handle
        self.size = size

    async def chunks(self, size: int) -> AsyncIterator[bytes]:
        if self._handle is not None:
            while True:
                chunk = await asyncio.to_thread(self._handle.read, size)
                if not chunk:
                    return
                yield chunk
        url = self._bot.session.api.file_url(self._bot.token, self._path)
        async for chunk in self._bot.session.stream_content(url, timeout=FETCH_PATIENCE, chunk_size=size):
            yield chunk

    async def release(self) -> None:
        if self._handle is None:
            return
        async with _fetching.setdefault(self._key, asyncio.Lock()):
            self._handle.close()
            left = _reading.get(self._path, 1) - 1
            if left > 0:
                _reading[self._path] = left
                return
            _reading.pop(self._path, None)
            try:
                os.unlink(self._path)
            except OSError:
                pass


class Direct:
    def __init__(self, bot: Bot):
        self.bot = bot

    def ready(self) -> bool:
        return True

    async def is_member(self, chat_id: int, telegram_id: int) -> bool:
        return await members.is_member(self.bot, chat_id, telegram_id)

    async def describe(self, chat_id: int) -> Chat:
        chat = await self.bot.get_chat(chat_id)
        return Chat(
            title=getattr(chat, "title", None) or getattr(chat, "full_name", None) or "",
            name=" ".join(part for part in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if part),
            username=getattr(chat, "username", None) or "",
            photo=getattr(chat, "photo", None) is not None,
        )

    async def photo(self, chat_id: int) -> Optional[bytes]:
        chat = await self.bot.get_chat(chat_id)
        if chat.photo is None:
            return None
        return await self.small(chat.photo.small_file_id)

    async def small(self, file_id: str) -> bytes:
        file = await self.bot.get_file(file_id)
        buffer = await self.bot.download_file(file.file_path)
        return buffer.read() if hasattr(buffer, "read") else bytes(buffer)

    async def fetch(self, file_id: str, key: str) -> _File:
        lock = _fetching.setdefault(key, asyncio.Lock())
        async with lock:
            file = await self.bot.get_file(file_id, request_timeout=FETCH_PATIENCE)
            path = file.file_path or ""
            if not _local(path):
                return _File(self.bot, key, path, None, int(file.file_size or 0))
            handle = open(path, "rb")
            _reading[path] = _reading.get(path, 0) + 1
            return _File(self.bot, key, path, handle, os.fstat(handle.fileno()).st_size)

    async def send_photo(self, chat_id: int, png: bytes, filename: str) -> Sent:
        message = await self.bot.send_photo(chat_id, types.BufferedInputFile(png, filename=filename))
        return Sent(getattr(message, "message_id", 0) or 0)

    async def send_video(self, chat_id: int, path: str, filename: str, caption: Optional[str] = None,
                         width: Optional[int] = None, height: Optional[int] = None,
                         duration: Optional[int] = None) -> Sent:
        message = await self.bot.send_video(
            chat_id,
            types.FSInputFile(path, filename=filename),
            caption=caption,
            supports_streaming=True,
            width=width,
            height=height,
            duration=duration,
        )
        return _sent(message)

    async def pass_on(self, chat_id: int, kind: Optional[str], file_id: str, caption: Optional[str] = None) -> Sent:
        send_as = {"animation": self.bot.send_animation, "document": self.bot.send_document}.get(kind or "", self.bot.send_video)
        message = await send_as(chat_id, file_id, caption=caption)
        return Sent(message.message_id)
