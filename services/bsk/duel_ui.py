"""Telegram UI helpers for BSK duels."""

from typing import Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services.bsk.duel_constants import MAX_BANS
from utils.formatting.text import escape_html


def beatmap_links(beatmap_id: int, beatmapset_id: int = 0) -> str:
    """Build a clickable inline 'site · osu!direct' pair for a beatmap."""
    site = f'<a href="https://osu.ppy.sh/b/{beatmap_id}">Карта</a>'
    if beatmapset_id and beatmapset_id > 0:
        direct = f'<a href="osu://dl/{beatmapset_id}">osu!direct</a>'
        return f"{site} · {direct}"
    return site


def accept_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"bskd:accept:{duel_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"bskd:decline:{duel_id}"),
    ]])


def format_pick_pool_links(dm_candidates: list, available_ids: Optional[set] = None) -> str:
    """Numbered list of beatmap links for the pick-phase DM caption."""
    lines: list[str] = []
    for i, m in enumerate(dm_candidates):
        bid = m.get('beatmap_id') if isinstance(m, dict) else m.beatmap_id
        if available_ids is not None and bid not in available_ids:
            continue

        bset_id = (
            m.get('beatmapset_id')
            if isinstance(m, dict)
            else getattr(m, 'beatmapset_id', 0)
        ) or 0
        artist = (
            m.get('artist')
            if isinstance(m, dict)
            else getattr(m, 'artist', '')
        ) or ''
        title = (
            m.get('title')
            if isinstance(m, dict)
            else getattr(m, 'title', '')
        ) or 'Map'
        version = (
            m.get('version')
            if isinstance(m, dict)
            else getattr(m, 'version', '')
        ) or ''

        label = f"{artist} - {title} [{version}]" if version else f"{artist} - {title}"
        if len(label) > 55:
            label = label[:54] + "…"

        lines.append(
            f"<b>{i + 1}.</b> {escape_html(label)} · {beatmap_links(bid, bset_id)}"
        )

    return "\n".join(lines)


def grid_cols_for(n_cards: int) -> int:
    """Column count used by the pool DM card image."""
    return 3 if n_cards <= 6 else 4


def pick_keyboard(duel_id: int, candidates: list, available_ids: Optional[set] = None) -> InlineKeyboardMarkup:
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


def ban_keyboard(duel_id: int, candidates: list, user_bans: list) -> InlineKeyboardMarkup:
    """Ban phase: toggle buttons under each map + confirm row."""
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
        confirm_label = f"✓ Confirm ({ban_count}/{MAX_BANS} bans)"
    elif ban_count > 0:
        confirm_label = f"✓ Confirm {ban_count} ban(s)"
    else:
        confirm_label = "Skip bans"

    rows.append([InlineKeyboardButton(
        text=confirm_label,
        callback_data=f"bskbandone:{duel_id}",
    )])

    return InlineKeyboardMarkup(inline_keyboard=rows)
