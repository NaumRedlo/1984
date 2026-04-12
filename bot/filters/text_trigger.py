from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Union

from aiogram.filters import BaseFilter
from aiogram.types import Message


@dataclass
class TriggerArgs:
    trigger: str        # matched trigger (lowercased)
    args: str | None    # everything after the trigger word, or None
    raw_text: str       # original message text


# Lazy import to avoid circular dependency — populated on first call
_button_map: Dict[str, str] | None = None


def _get_button_map() -> Dict[str, str]:
    global _button_map
    if _button_map is None:
        from bot.keyboards.reply_keyboard import BUTTON_TRIGGER_MAP
        _button_map = {k.lower(): v.lower() for k, v in BUTTON_TRIGGER_MAP.items()}
    return _button_map


class TextTriggerFilter(BaseFilter):
    """Match plain-text messages whose first word equals one of the given triggers.

    Also translates Russian reply-keyboard button labels via BUTTON_TRIGGER_MAP.
    Case-insensitive, exact word match (not prefix).
    """

    def __init__(self, *triggers: str) -> None:
        # Store lowercased triggers for O(1) lookup
        self.triggers: frozenset[str] = frozenset(t.lower() for t in triggers)

    async def __call__(self, message: Message) -> Union[bool, Dict[str, Any]]:
        text = message.text
        if not text:
            return False

        parts = text.strip().split(maxsplit=1)
        first_word = parts[0].lower()

        # Translate button label → trigger name
        btn_map = _get_button_map()
        resolved = btn_map.get(first_word, first_word)

        if resolved not in self.triggers:
            return False

        args = parts[1].strip() if len(parts) > 1 else None
        return {
            "trigger_args": TriggerArgs(
                trigger=resolved,
                args=args,
                raw_text=text,
            )
        }
