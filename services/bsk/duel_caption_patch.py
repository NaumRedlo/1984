"""Runtime caption/link helpers for BSK duel manager.

This module keeps user-facing duel captions and inline labels outside the very
large duel_manager module.  It is applied as a small compatibility patch from
bot.handlers.bsk.handlers, so existing imports of services.bsk.duel_manager keep
working while captions can evolve independently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.formatting.text import escape_html


def _pluralize_ru(n: int, forms: tuple[str, str, str]) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return forms[1]
    return forms[2]


def _fmt_seconds_ru(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} {_pluralize_ru(seconds, ('секунда', 'секунды', 'секунд'))}"


def _deadline_utc_after(seconds: int) -> str:
    deadline = datetime.now(timezone.utc).timestamp() + max(0, int(seconds))
    dt = datetime.fromtimestamp(deadline, tz=timezone.utc)
    return dt.strftime("%H:%M UTC")


def _map_value(map_obj, key: str, default=None):
    if isinstance(map_obj, dict):
        return map_obj.get(key, default)
    return getattr(map_obj, key, default)


def _beatmap_links(beatmap_id: int, beatmapset_id: int = 0) -> str:
    site = f'<a href="https://osu.ppy.sh/b/{beatmap_id}">Карта</a>'
    if beatmapset_id and beatmapset_id > 0:
        download = f'<a href="https://catboy.best/d/{beatmapset_id}">Скачать</a>'
        return f"{site} · {download}"
    return site


def _format_pick_pool_links(candidates: Iterable, available_ids: Optional[set[int]] = None) -> str:
    lines = ["<b>Карты:</b>"]

    visible_index = 0
    for idx, map_obj in enumerate(candidates, start=1):
        beatmap_id = _map_value(map_obj, "beatmap_id")
        if not beatmap_id:
            continue
        if available_ids is not None and beatmap_id not in available_ids:
            continue

        visible_index += 1
        beatmapset_id = int(_map_value(map_obj, "beatmapset_id", 0) or 0)
        title = escape_html(str(_map_value(map_obj, "title", "Unknown") or "Unknown"))
        artist = escape_html(str(_map_value(map_obj, "artist", "") or ""))
        version = escape_html(str(_map_value(map_obj, "version", "") or ""))

        display_name = f"{artist} — {title}" if artist else title
        if version:
            display_name += f" [{version}]"
        if len(display_name) > 72:
            display_name = display_name[:69] + "…"

        links = _beatmap_links(int(beatmap_id), beatmapset_id)
        lines.append(f"{idx}. {display_name} — {links}")

    if visible_index == 0:
        return "<i>Нет доступных карт.</i>"

    return "\n".join(lines)


def _ban_group_caption(round_num: int, test_tag: str, timeout_seconds: int) -> str:
    return (
        f"🚫 <b>Раунд {round_num} · Фаза бана{test_tag}</b>\n"
        f"Игроки банят карты из пулов соперника.\n"
        f"⏳ Осталось: <b>{_fmt_seconds_ru(timeout_seconds)}</b>\n"
        f"🕒 Дедлайн: <b>{_deadline_utc_after(timeout_seconds)}</b>"
    )


def _patched_ban_keyboard(duel_manager, duel_id: int, candidates: list, user_bans: list) -> InlineKeyboardMarkup:
    cols = duel_manager._grid_cols_for(len(candidates))
    rows = []

    for i in range(0, len(candidates), cols):
        chunk = candidates[i:i + cols]
        row = []
        for map_obj in chunk:
            beatmap_id = _map_value(map_obj, "beatmap_id")
            selected = beatmap_id in user_bans
            title = str(_map_value(map_obj, "title", "Map") or "Map")
            row.append(InlineKeyboardButton(
                text=("✕ " if selected else "") + title[:15],
                callback_data=f"bskban:{duel_id}:{beatmap_id}",
            ))
        rows.append(row)

    ban_count = len(user_bans)
    if ban_count >= duel_manager.MAX_BANS:
        confirm_label = f"✓ Подтвердить ({ban_count}/{duel_manager.MAX_BANS})"
    elif ban_count > 0:
        confirm_label = f"✓ Подтвердить: {ban_count}/{duel_manager.MAX_BANS}"
    else:
        confirm_label = "Пропустить баны"

    rows.append([InlineKeyboardButton(
        text=confirm_label,
        callback_data=f"bskbandone:{duel_id}",
    )])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def apply_duel_caption_patch(duel_manager) -> None:
    """Patch public helper hooks used by services.bsk.duel_manager.

    The large manager currently references some helpers dynamically by global
    name. Assigning them on the module keeps this refactor backward-compatible.
    """
    duel_manager._beatmap_links = _beatmap_links
    duel_manager._format_pick_pool_links = _format_pick_pool_links
    duel_manager._ban_group_caption = _ban_group_caption
    duel_manager._ban_keyboard = lambda duel_id, candidates, user_bans: _patched_ban_keyboard(
        duel_manager,
        duel_id,
        candidates,
        user_bans,
    )
