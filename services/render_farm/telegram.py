from dataclasses import dataclass
from typing import AsyncIterator, Optional, Protocol


@dataclass(frozen=True)
class Chat:
    title: str = ""
    name: str = ""
    username: str = ""
    photo: bool = False


@dataclass(frozen=True)
class Thumb:
    file_id: str


@dataclass(frozen=True)
class Media:
    file_id: str
    file_unique_id: Optional[str] = None
    file_size: Optional[int] = None
    duration: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    thumbnail: Optional[Thumb] = None


@dataclass(frozen=True)
class Sent:
    message_id: int
    kind: Optional[str] = None
    media: Optional[Media] = None


class Fetched(Protocol):
    size: int

    def chunks(self, size: int) -> AsyncIterator[bytes]: ...

    async def release(self) -> None: ...


class Telegram(Protocol):
    def ready(self) -> bool: ...

    async def is_member(self, chat_id: int, telegram_id: int) -> bool: ...

    async def describe(self, chat_id: int) -> Chat: ...

    async def photo(self, chat_id: int) -> Optional[bytes]: ...

    async def small(self, file_id: str) -> bytes: ...

    async def fetch(self, file_id: str, key: str) -> Fetched: ...

    async def send_photo(self, chat_id: int, png: bytes, filename: str) -> Sent: ...

    async def send_video(self, chat_id: int, path: str, filename: str, caption: Optional[str] = None,
                         width: Optional[int] = None, height: Optional[int] = None,
                         duration: Optional[int] = None) -> Sent: ...

    async def pass_on(self, chat_id: int, kind: Optional[str], file_id: str, caption: Optional[str] = None) -> Sent: ...


class Asleep(Exception):
    pass


class Absent:
    def ready(self) -> bool:
        return False

    async def is_member(self, chat_id: int, telegram_id: int) -> bool:
        return False

    async def describe(self, chat_id: int) -> Chat:
        raise Asleep("telegram is not connected")

    async def photo(self, chat_id: int) -> Optional[bytes]:
        raise Asleep("telegram is not connected")

    async def small(self, file_id: str) -> bytes:
        raise Asleep("telegram is not connected")

    async def fetch(self, file_id: str, key: str) -> Fetched:
        raise Asleep("telegram is not connected")

    async def send_photo(self, chat_id: int, png: bytes, filename: str) -> Sent:
        raise Asleep("telegram is not connected")

    async def send_video(self, chat_id: int, path: str, filename: str, caption: Optional[str] = None,
                         width: Optional[int] = None, height: Optional[int] = None,
                         duration: Optional[int] = None) -> Sent:
        raise Asleep("telegram is not connected")

    async def pass_on(self, chat_id: int, kind: Optional[str], file_id: str, caption: Optional[str] = None) -> Sent:
        raise Asleep("telegram is not connected")
