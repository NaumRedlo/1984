from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Union

from aiogram.filters import BaseFilter
from aiogram.types import Message


@dataclass
class TriggerArgs:
    trigger: str        # matched trigger (lowercased)
    args: str | None    # everything after the trigger word, or None
    raw_text: str       # original message text


class TextTriggerFilter(BaseFilter):
    """Match plain-text messages whose first word equals one of the given triggers.

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

        if first_word not in self.triggers:
            return False

        args = parts[1].strip() if len(parts) > 1 else None
        return {
            "trigger_args": TriggerArgs(
                trigger=first_word,
                args=args,
                raw_text=text,
            )
        }
