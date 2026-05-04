"""Telegram UI helpers for BSK duels.

Captions, inline keyboards, and small formatting utilities live here so the
duel manager only deals with lifecycle / orchestration concerns.
"""

from datetime import datetime, timezone
from typing import Iterable, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.bsk.duel_constants import MAX_BANS
from utils.formatting.text import escape_html


# ── Russian pluralization helpers ────────────────────────────────────────────

def pluralize_ru(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return forms[1]
    return forms[2]


def fmt_seconds_ru(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} {pluralize_ru(seconds, ('секунда', 'секунды', 'секунд'))}"


def deadline_utc_after(seconds: int) -> str:
    deadline = datetime.now(timezone.utc).timestamp() + max(0, int(seconds))
    dt = datetime.fromtimestamp(deadline, tz=timezone.utc)
    return dt.strftime("%H:%M UTC")


# ── Beatmap links ────────────────────────────────────────────────────────────

def beatmap_links(beatmap_id: int, beatmapset_id: int = 0) -> str:
    """Build a clickable inline 'Карта · Скачать' pair for a beatmap.

    Download goes through catboy.best mirror, which works for everyone (the
    osu://dl scheme only worked for supporters and required a custom-scheme
    handler).
    """
    site = f'<a href="https://osu.ppy.sh/b/{beatmap_id}">Карта</a>'
    if beatmapset_id and beatmapset_id > 0:
        download = f'<a href="https://catboy.best/d/{beatmapset_id}">Скачать</a>'
        return f"{site} · {download}"
    return site


def format_pick_pool_links(
    candidates: Iterable,
    available_ids: Optional[set[int]] = None,
) -> str:
    """Map list for ban/pick pool cards.

    Map information is already rendered on the generated image and exposed via
    inline buttons. Keeping a full text list in the caption makes ban/pick
    messages noisy and often hits Telegram caption limits, so this helper
    intentionally returns an empty string.
    """
    return ""


# ── Keyboards ────────────────────────────────────────────────────────────────

def accept_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"bskd:accept:{duel_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bskd:decline:{duel_id}"),
    ]])


def grid_cols_for(n_cards: int) -> int:
    """Match the column count used by the pool DM card image."""
    return 3 if n_cards <= 6 else 4


def pick_keyboard(
    duel_id: int,
    candidates: list,
    available_ids: Optional[set] = None,
) -> InlineKeyboardMarkup:
    """Number buttons under each map; column count mirrors the pool card grid."""
    if available_ids is not None:
        visible = [
            m for m in candidates
            if (m.beatmap_id if hasattr(m, 'beatmap_id') else m.get('beatmap_id')) in available_ids
        ]
    else:
        visible = list(candidates)

    cols = grid_cols_for(len(visible))

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for i, m in enumerate(candidates):
        bid = m.beatmap_id if hasattr(m, 'beatmap_id') else m.get('beatmap_id')
        if available_ids is not None and bid not in available_ids:
            continue

        current_row.append(InlineKeyboardButton(
            text=str(i + 1),
            callback_data=f"bskpick:{duel_id}:{bid}",
        ))

        if len(current_row) == cols:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def ban_keyboard(
    duel_id: int,
    candidates: list,
    user_bans: list,
) -> InlineKeyboardMarkup:
    """Ban phase: toggle buttons under each map + confirm row.

    Column count mirrors the pool card grid so buttons align with cards.
    Russian labels — see duel_caption_patch history.
    """
    cols = grid_cols_for(len(candidates))
    rows: list[list[InlineKeyboardButton]] = []

    for i in range(0, len(candidates), cols):
        chunk = candidates[i:i + cols]
        row: list[InlineKeyboardButton] = []

        for m in chunk:
            bid = m.get('beatmap_id') if isinstance(m, dict) else m.beatmap_id
            selected = bid in user_bans
            title = (m.get('title') if isinstance(m, dict) else m.title) or 'Map'

            row.append(InlineKeyboardButton(
                text=('✕ ' if selected else '') + title[:15],
                callback_data=f"bskban:{duel_id}:{bid}",
            ))

        rows.append(row)

    ban_count = len(user_bans)
    if ban_count >= MAX_BANS:
        confirm_label = f"✓ Подтвердить ({ban_count}/{MAX_BANS})"
    elif ban_count > 0:
        confirm_label = f"✓ Подтвердить: {ban_count}/{MAX_BANS}"
    else:
        confirm_label = "Пропустить баны"

    rows.append([InlineKeyboardButton(
        text=confirm_label,
        callback_data=f"bskbandone:{duel_id}",
    )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Captions ─────────────────────────────────────────────────────────────────

def ban_group_caption(round_num: int, test_tag: str, timeout_seconds: int) -> str:
    return (
        f"🚫 <b>Раунд {round_num} · Фаза бана{test_tag}</b>\n"
        f"Игроки банят карты из пулов соперника.\n"
        f"⏳ Осталось: <b>{fmt_seconds_ru(timeout_seconds)}</b>\n"
        f"🕒 Дедлайн: <b>{deadline_utc_after(timeout_seconds)}</b>"
    )


def pick_group_caption(
    round_num: int,
    active_name: str,
    test_tag: str,
    timeout_seconds: int,
) -> str:
    return (
        f"🗳 <b>Раунд {round_num} · Выбор карты{test_tag}</b>\n"
        f"Очередь: <b>{escape_html(active_name)}</b>\n"
        f"⏳ Осталось: <b>{fmt_seconds_ru(timeout_seconds)}</b>"
    )
