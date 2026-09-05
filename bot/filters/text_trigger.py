from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Union

from aiogram.filters import BaseFilter
from aiogram.types import Message

@dataclass
class TriggerArgs:
    trigger: str
    args: str | None
    raw_text: str

class TextTriggerFilter(BaseFilter):

    def __init__(self, *triggers: str) -> None:

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
