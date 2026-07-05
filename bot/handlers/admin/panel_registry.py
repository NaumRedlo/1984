"""Single source of truth for the admin panel (bot.handlers.admin.panel).

Every admin command is described once here; the panel UI is rendered from
this registry. Buckets are derived from the fields:

  * executor set       → read-only, no-arg, text-output commands get a
                         "▶️ Выполнить здесь" button (runs in the DM).
  * args is not None    → command needs arguments → copy-the-text hint.
  * where in {group,topic} → must be run elsewhere → "где выполнять" hint.
  * destructive         → ⚠️ note; never executed from the panel (the command
                         has its own confirm flow when typed manually).

Executors are tiny lazy wrappers so importing this module never drags in the
handler modules at import time (avoids import-order fragility).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Optional

Where = Literal["dm", "group", "topic", "any"]


@dataclass(frozen=True)
class CommandSpec:
    trigger: str
    label: str
    desc: str
    args: Optional[str] = None
    where: Where = "any"
    destructive: bool = False
    executor: Optional[Callable[[], Awaitable[str]]] = None


@dataclass(frozen=True)
class Category:
    key: str          # short, used in callback_data (ap:c:<key>)
    icon: str
    title: str
    commands: tuple[CommandSpec, ...]


# ── lazy executors for the safe read-only commands ──────────────────────────



# ── the registry ────────────────────────────────────────────────────────────

CATEGORIES: tuple[Category, ...] = (
    Category("users", "👤", "Юзеры", (
        CommandSpec("userslist", "Список юзеров",
                    "Все зарегистрированные, по last_seen."),
        CommandSpec("whois", "Кто это",
                    "Инфо о юзере по User.id или telegram_id.", args="<id>"),
        CommandSpec("purgeuser", "Удалить юзера",
                    "Каскадно удалить юзера и его данные.",
                    args="<id>", destructive=True),
        CommandSpec("notifyrelink", "Просьба перепривязки",
                    "DM юзеру с просьбой перепривязать osu!.", args="<id>"),
    )),
    Category("chats", "🔔", "Чаты / топики", (
        CommandSpec("whereami", "Где я",
                    "Показать chat_id и message_thread_id.", where="group"),
    )),
)


def find_category(key: str) -> Optional[Category]:
    return next((c for c in CATEGORIES if c.key == key), None)


def find_command(trigger: str) -> Optional[CommandSpec]:
    for cat in CATEGORIES:
        for cmd in cat.commands:
            if cmd.trigger == trigger:
                return cmd
    return None


def all_commands() -> tuple[CommandSpec, ...]:
    return tuple(cmd for cat in CATEGORIES for cmd in cat.commands)
