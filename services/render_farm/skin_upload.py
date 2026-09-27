import asyncio
import html
import os
import shutil
import tempfile
import weakref

from aiogram import types

from config import settings
from services.render_farm import members, skins
from utils.i18n import t
from utils.logger import get_logger

logger = get_logger("services.render_farm.skin_upload")

class LimitedFile:
    def __init__(self, path: str, most: int):
        self.file = open(path, "wb")
        self.most = most
        self.written = 0

    def write(self, data):
        if self.written + len(data) > self.most:
            raise skins.SkinError("too_big")
        written = self.file.write(data)
        self.written += written
        return written

    def seek(self, *args):
        return self.file.seek(*args)

    def flush(self):
        return self.file.flush()

    def close(self):
        self.file.close()

_locks = weakref.WeakValueDictionary()

async def take(bot, message: types.Message, lang: str) -> None:
    if not message.from_user:
        return
    lock = _locks.setdefault(message.from_user.id, asyncio.Lock())
    async with lock:
        await _take(bot, message, lang)

async def _take(bot, message: types.Message, lang: str) -> None:
    person, document = message.from_user, message.document
    if not person or not document:
        return
    if not await members.shares_a_group(bot, person.id):
        await message.reply(t("farm.members_only", lang))
        return
    if document.file_size and document.file_size > skins.archive_limit():
        await message.reply(t("sts.skin.too_big", lang, mb=skins.archive_limit() // (1024 * 1024)))
        return
    workdir = tempfile.mkdtemp(prefix="skin-upload-")
    sink = None
    try:
        path = os.path.join(workdir, "skin.osk")
        sink = LimitedFile(path, skins.archive_limit())
        await bot.download(document, destination=sink)
        sink.close()
        sink = None
        key = await asyncio.to_thread(skins.store_upload, person.id, path, document.file_name or "skin.osk")
        await skins.choose(person.id, key)
        shown = html.escape(skins.display_name(key, person.id))
        await message.reply(t("sts.skin.uploaded", lang, skin=shown), parse_mode="HTML")
    except skins.SkinError as exc:
        await message.reply(t(f"sts.skin.{exc.code}", lang, mb=skins.archive_limit() // (1024 * 1024)))
    except Exception as exc:
        logger.warning("skin upload failed for %s: %s", person.id, type(exc).__name__)
        await message.reply(t("sts.skin.upload_failed", lang))
    finally:
        if sink:
            sink.close()
        shutil.rmtree(workdir, ignore_errors=True)
