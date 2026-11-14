import os
import sys
from typing import Any

from utils.logger import get_logger

logger = get_logger("utils.aiogram_lazy")

_namespace: dict[str, Any] = {}
_original = None


def installed() -> bool:
    return _original is not None


def install() -> bool:
    global _original
    if _original is not None:
        return True
    if os.getenv("AIOGRAM_EAGER_MODELS", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if "aiogram.types" in sys.modules:
        return False
    from pydantic import BaseModel

    _original = BaseModel.__dict__["model_rebuild"].__func__

    def model_rebuild(cls, **told):
        if cls.__module__.startswith("aiogram."):
            given = told.get("_types_namespace")
            if given is not None and sys._getframe(1).f_globals.get("__name__") == "aiogram.types":
                if len(given) > len(_namespace):
                    _namespace.clear()
                    _namespace.update(given)
                return None
            if given is None and _namespace:
                told["_types_namespace"] = _namespace
        return _original(cls, **told)

    BaseModel.model_rebuild = classmethod(model_rebuild)
    return True


def build_everything() -> int:
    if _original is None:
        return 0
    import aiogram.types

    built = 0
    for name in aiogram.types.__all__:
        entity = getattr(aiogram.types, name)
        if hasattr(entity, "model_rebuild"):
            _original(entity, force=True, _types_namespace=_namespace or None)
            built += 1
    return built


def _exercise() -> None:
    from aiogram import types
    from aiogram.methods import Response, SendMessage

    update = types.Update.model_validate({
        "update_id": 1,
        "message": {
            "message_id": 5, "date": 1700000000, "chat": {"id": -100, "type": "supergroup", "title": "Room"},
            "from": {"id": 7, "is_bot": False, "first_name": "Naum"}, "text": "/start",
            "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            "reply_to_message": {"message_id": 4, "date": 1700000000, "chat": {"id": -100, "type": "supergroup", "title": "Room"},
                                 "document": {"file_id": "f", "file_unique_id": "u", "file_name": "a.osr"}},
        },
    })
    if update.message.reply_to_message.document.file_name != "a.osr" or update.message.entities[0].type != "bot_command":
        raise ValueError("an update was read wrong")
    pressed = types.Update.model_validate({
        "update_id": 2,
        "callback_query": {"id": "1", "chat_instance": "1", "data": "pair:yes:ABCD",
                           "from": {"id": 7, "is_bot": False, "first_name": "Naum"}},
    })
    if pressed.callback_query.data != "pair:yes:ABCD":
        raise ValueError("a callback was read wrong")
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="a", callback_data="b")]])
    sent = SendMessage(chat_id=1, text="hi", reply_markup=keyboard).model_dump(warnings=False, exclude_none=True)
    if sent["reply_markup"] != {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]}:
        raise ValueError("a message was written wrong")
    answer = Response[SendMessage.__returning__].model_validate(
        {"ok": True, "result": {"message_id": 9, "date": 1700000000, "chat": {"id": 1, "type": "private"}, "text": "hi"}})
    if answer.result.message_id != 9:
        raise ValueError("an answer was read wrong")


def verify() -> bool:
    if _original is None:
        return True
    try:
        _exercise()
        return True
    except Exception as exc:
        logger.warning("Telegram types built on first use do not work here (%s: %s); building all of them now",
                       type(exc).__name__, exc)
    try:
        build_everything()
        _exercise()
    except Exception as exc:
        logger.error("Telegram types could not be built: %s: %s", type(exc).__name__, exc, exc_info=True)
    return False
