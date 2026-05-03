"""Runtime caption/link helpers for BSK duel manager.

This module keeps user-facing duel captions and inline labels outside the very
large duel_manager module.  It is applied as a small compatibility patch from
bot.handlers.bsk.handlers, so existing imports of services.bsk.duel_manager keep
working while captions can evolve independently.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Iterable, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.formatting.text import escape_html


_TIMER_UPDATE_STEP_SECONDS = 10
_TIMER_TASKS: dict[tuple[int, int], asyncio.Task] = {}


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
    """Return no caption map list for ban/pick pool cards.

    Map information is already rendered on the generated image and exposed via
    inline buttons. Keeping a full text list in the caption makes ban/pick
    messages noisy and often hits Telegram caption limits, so the caption helper
    intentionally returns an empty string.
    """
    return ""


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


def _extract_timeout_seconds(caption: str) -> Optional[int]:
    timeout_match = re.search(r"⏳\s*(\d+)\s*сек", caption)
    if timeout_match:
        return int(timeout_match.group(1))

    return None


def _normalize_pick_ban_caption(caption: str, remaining_seconds: int) -> str:
    """Convert duel_manager pick/ban captions to a compact dynamic form."""
    remaining_seconds = max(0, int(remaining_seconds))

    round_match = re.search(r"Раунд\s+(\d+)", caption)
    round_num = round_match.group(1) if round_match else "?"

    test_tag = ""
    if "[TEST]" in caption or "[ТЕСТ]" in caption:
        test_tag = " [TEST]"

    if "Фаза бана" in caption or "бан" in caption.lower():
        return (
            f"🚫 <b>Раунд {round_num} · Фаза бана{test_tag}</b>\n"
            f"Игроки банят карты из пулов соперника.\n"
            f"⏳ Осталось: <b>{_fmt_seconds_ru(remaining_seconds)}</b>"
        )

    if "Выбор карты" in caption or "Очередь:" in caption:
        turn_match = re.search(r"Очередь:\s*<b>(.*?)</b>", caption)
        turn_line = ""
        if turn_match:
            turn_line = f"\nОчередь: <b>{turn_match.group(1)}</b>"

        return (
            f"🗳 <b>Раунд {round_num} · Выбор карты{test_tag}</b>"
            f"{turn_line}\n"
            f"⏳ Осталось: <b>{_fmt_seconds_ru(remaining_seconds)}</b>"
        )

    return caption


def _is_pick_or_ban_group_caption(caption: str) -> bool:
    if not caption:
        return False

    return (
        "Фаза бана" in caption or
        "Выбор карты" in caption or
        "Очередь:" in caption
    )


def _cancel_existing_timer(chat_id: int, message_id: int) -> None:
    key = (chat_id, message_id)
    task = _TIMER_TASKS.pop(key, None)
    if task and not task.done():
        task.cancel()


async def _run_caption_timer(
    bot,
    chat_id: int,
    message_id: int,
    original_caption: str,
    timeout_seconds: int,
) -> None:
    key = (chat_id, message_id)

    try:
        remaining = max(0, int(timeout_seconds))

        while remaining > 0:
            await asyncio.sleep(min(_TIMER_UPDATE_STEP_SECONDS, remaining))
            remaining = max(0, remaining - _TIMER_UPDATE_STEP_SECONDS)

            updated_caption = _normalize_pick_ban_caption(original_caption, remaining)
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=updated_caption,
                    parse_mode="HTML",
                )
            except Exception:
                break
    except asyncio.CancelledError:
        raise
    finally:
        current = _TIMER_TASKS.get(key)
        if current is asyncio.current_task():
            _TIMER_TASKS.pop(key, None)


def _schedule_caption_timer(
    bot,
    chat_id: int,
    message_id: Optional[int],
    caption: str,
    timeout_seconds: Optional[int],
) -> None:
    if message_id is None or timeout_seconds is None:
        return

    _cancel_existing_timer(chat_id, message_id)

    task = asyncio.create_task(
        _run_caption_timer(
            bot=bot,
            chat_id=chat_id,
            message_id=message_id,
            original_caption=caption,
            timeout_seconds=timeout_seconds,
        )
    )
    _TIMER_TASKS[(chat_id, message_id)] = task


def _build_patched_send_or_edit_photo(duel_manager):
    original_send_or_edit_photo = duel_manager._send_or_edit_photo

    async def _patched_send_or_edit_photo(
        bot,
        chat_id: int,
        message_id: Optional[int],
        img_bytes,
        caption: str = "",
        reply_markup=None,
        thread_id: Optional[int] = None,
    ) -> Optional[int]:
        timeout_seconds = None

        if caption and _is_pick_or_ban_group_caption(caption):
            timeout_seconds = _extract_timeout_seconds(caption)
            if timeout_seconds is not None:
                caption = _normalize_pick_ban_caption(caption, timeout_seconds)

        new_message_id = await original_send_or_edit_photo(
            bot=bot,
            chat_id=chat_id,
            message_id=message_id,
            img_bytes=img_bytes,
            caption=caption,
            reply_markup=reply_markup,
            thread_id=thread_id,
        )

        if caption and _is_pick_or_ban_group_caption(caption):
            _schedule_caption_timer(
                bot=bot,
                chat_id=chat_id,
                message_id=new_message_id,
                caption=caption,
                timeout_seconds=timeout_seconds,
            )
        elif new_message_id is not None:
            _cancel_existing_timer(chat_id, new_message_id)

        return new_message_id

    return _patched_send_or_edit_photo


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

    if not getattr(duel_manager, "_caption_timer_patch_applied", False):
        duel_manager._send_or_edit_photo = _build_patched_send_or_edit_photo(duel_manager)
        duel_manager._caption_timer_patch_applied = True
