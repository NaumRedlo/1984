from datetime import datetime
from uuid import uuid4

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils.formatting.text import escape_html

EDIT_COOLDOWN_HOURS = 4

BOUNTY_TYPES = [
    "First FC", "Snipe", "History", "Accuracy",
    "Pass", "Mod", "SS", "Marathon",
    "Memory", "Metronome", "Easter Egg",
]


async def _generate_bounty_id() -> str:
    today = datetime.utcnow().strftime("%Y.%m.%d")
    return f"{today}/{uuid4().hex[:8]}"


def _build_summary(data: dict) -> str:
    lines = [
        "<b>Сводка баунти</b>",
        "═" * 28,
        f"<b>Тип:</b> {escape_html(data.get('bounty_type', 'First FC'))}",
        f"<b>Карта:</b> {escape_html(data['beatmap_title'])}",
        f"<b>Beatmap ID:</b> {data['beatmap_id']}",
        f"<b>Сложность:</b> {data['star_rating']:.2f}★",
        f"<b>Длительность:</b> {data['drain_time'] // 60}:{data['drain_time'] % 60:02d}",
        f"<b>Название:</b> {escape_html(data['title'])}",
        "═" * 28,
    ]
    if data.get('min_accuracy') is not None:
        lines.append(f"<b>Мин. точность:</b> {data['min_accuracy']}%")
    else:
        lines.append("<b>Мин. точность:</b> Без ограничения")
    lines.append(f"<b>Обязательные моды:</b> {data.get('required_mods') or 'Нет'}")
    if data.get('max_misses') is not None:
        lines.append(f"<b>Макс. миссов:</b> {data['max_misses']}")
    else:
        lines.append("<b>Макс. миссов:</b> Без ограничения")

    # Rank / HP requirement
    rank_text = data.get('min_rank') or "Нет"
    hp_text = f"{data['min_hp']} HP" if data.get('min_hp') is not None else None
    if hp_text and rank_text != "Нет":
        lines.append(f"<b>Мин. ранг:</b> {rank_text} (или {hp_text})")
    elif hp_text:
        lines.append(f"<b>Мин. HP:</b> {hp_text}")
    else:
        lines.append(f"<b>Мин. ранг:</b> {rank_text}")

    if data.get('max_participants') is not None:
        lines.append(f"<b>Макс. участников:</b> {data['max_participants']}")
    else:
        lines.append("<b>Макс. участников:</b> Без лимита")
    if data.get('deadline'):
        lines.append(f"<b>Дедлайн:</b> {data['deadline'].strftime('%d.%m.%Y %H:%M UTC')}")
    else:
        lines.append("<b>Дедлайн:</b> Нет")
    return "\n".join(lines)


def _rank_keyboard(prefix: str, include_keep: bool = False, current: str = ""):
    rows = [
        [
            InlineKeyboardButton(text="Candidate", callback_data=f"{prefix}_Candidate"),
            InlineKeyboardButton(text="Member", callback_data=f"{prefix}_Member"),
        ],
        [
            InlineKeyboardButton(text="Inspector", callback_data=f"{prefix}_Inspector"),
            InlineKeyboardButton(text="Commissioner", callback_data=f"{prefix}_Commissioner"),
        ],
        [
            InlineKeyboardButton(text="Big Brother", callback_data=f"{prefix}_Big Brother"),
            InlineKeyboardButton(text="Без ограничения", callback_data=f"{prefix}_none"),
        ],
    ]
    if include_keep:
        rows.append([InlineKeyboardButton(text=f"Оставить ({current})", callback_data=f"{prefix}_keep")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _canonical_bounty_type(raw: str) -> str | None:
    """Return the canonical BOUNTY_TYPES entry matching ``raw`` (case-insensitive)."""
    key = raw.strip().lower()
    for t in BOUNTY_TYPES:
        if t.lower() == key:
            return t
    return None
