"""Telegram transport helpers for BSK duels.

Provides:
- send_or_edit_photo: idempotent send/edit-photo with caption-timer auto-refresh
- caption-timer machinery (cancellable per (chat, message))
- send_dm_with_fallback: DM-send with group-chat fallback when the bot is blocked
"""

from __future__ import annotations

import asyncio
import enum
import re
from io import BytesIO
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InputMediaPhoto

from services.bsk.duel_ui import fmt_seconds_ru
from utils.logger import get_logger

logger = get_logger("bsk.duel_telegram")


class DmResult(enum.Enum):
    """Outcome of an attempted DM send.

    - DM_SENT: the player received the message in their personal chat.
    - FORBIDDEN_FALLBACK: the bot can't message this user (blocked / never /start).
      The caller should have already received a group-chat ping notification
      via the `group_fallback_*` parameters.
    - FAILED: unexpected error (network, telegram outage, malformed payload).
      Logged with exc_info; caller decides whether to cancel the duel.
    """
    DM_SENT = "dm_sent"
    FORBIDDEN_FALLBACK = "forbidden_fallback"
    FAILED = "failed"


# ── Caption-timer state ──────────────────────────────────────────────────────

_TIMER_UPDATE_STEP_SECONDS = 10
_TIMER_TASKS: dict[tuple[int, int], asyncio.Task] = {}


def _is_pick_or_ban_group_caption(caption: str) -> bool:
    if not caption:
        return False
    return (
        "Фаза бана" in caption
        or "Выбор карты" in caption
        or "Очередь:" in caption
    )


def _extract_timeout_seconds(caption: str) -> Optional[int]:
    """Pull "⏳ Осталось: <b>NN секунд</b>" or "⏳ NN сек" out of a caption."""
    m = re.search(r"⏳[^0-9]*?(\d+)\s*сек", caption)
    if m:
        return int(m.group(1))
    return None


def _normalize_pick_ban_caption(caption: str, remaining_seconds: int) -> str:
    """Re-render a pick/ban caption with updated remaining seconds."""
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
            f"⏳ Осталось: <b>{fmt_seconds_ru(remaining_seconds)}</b>"
        )

    if "Выбор карты" in caption or "Очередь:" in caption:
        turn_match = re.search(r"Очередь:\s*<b>(.*?)</b>", caption)
        turn_line = ""
        if turn_match:
            turn_line = f"\nОчередь: <b>{turn_match.group(1)}</b>"

        return (
            f"🗳 <b>Раунд {round_num} · Выбор карты{test_tag}</b>"
            f"{turn_line}\n"
            f"⏳ Осталось: <b>{fmt_seconds_ru(remaining_seconds)}</b>"
        )

    return caption


def cancel_caption_timer(chat_id: int, message_id: int) -> None:
    """Cancel an in-flight caption timer for (chat_id, message_id)."""
    key = (chat_id, message_id)
    task = _TIMER_TASKS.pop(key, None)
    if task and not task.done():
        task.cancel()


async def _run_caption_timer(
    bot: Bot,
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
    bot: Bot,
    chat_id: int,
    message_id: Optional[int],
    caption: str,
    timeout_seconds: Optional[int],
) -> None:
    if message_id is None or timeout_seconds is None:
        return

    cancel_caption_timer(chat_id, message_id)

    task = asyncio.create_task(_run_caption_timer(
        bot=bot,
        chat_id=chat_id,
        message_id=message_id,
        original_caption=caption,
        timeout_seconds=timeout_seconds,
    ))
    _TIMER_TASKS[(chat_id, message_id)] = task


# ── Photo send/edit ──────────────────────────────────────────────────────────

async def send_or_edit_photo(
    bot: Bot,
    chat_id: int,
    message_id: Optional[int],
    img_bytes,
    caption: str = "",
    reply_markup=None,
    thread_id: Optional[int] = None,
) -> Optional[int]:
    """
    Send a new photo (when message_id is None) or edit an existing one.

    For pick/ban captions, normalizes the caption and schedules an auto-refresh
    timer that ticks down the displayed "⏳ Осталось" line until it reaches zero
    or another send_or_edit_photo replaces the message.

    Returns the actual message_id (may differ from the input if editing fails
    and a fresh message is sent as a fallback).
    """
    if isinstance(img_bytes, BytesIO):
        img_bytes.seek(0)
        raw = img_bytes.read()
    else:
        raw = img_bytes

    file = BufferedInputFile(raw, filename="duel.png")

    timeout_seconds: Optional[int] = None
    if caption and _is_pick_or_ban_group_caption(caption):
        timeout_seconds = _extract_timeout_seconds(caption)
        if timeout_seconds is not None:
            caption = _normalize_pick_ban_caption(caption, timeout_seconds)

    if message_id is None:
        msg = await bot.send_photo(
            chat_id,
            photo=file,
            caption=caption or None,
            parse_mode="HTML" if caption else None,
            reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        new_message_id: Optional[int] = msg.message_id
    else:
        try:
            await bot.edit_message_media(
                media=InputMediaPhoto(
                    media=file,
                    caption=caption or None,
                    parse_mode="HTML" if caption else None,
                ),
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
            new_message_id = message_id
        except Exception as e:
            logger.warning(f"send_or_edit_photo edit failed ({e}), sending new message")
            msg = await bot.send_photo(
                chat_id,
                photo=BufferedInputFile(raw, filename="duel.png"),
                caption=caption or None,
                parse_mode="HTML" if caption else None,
                reply_markup=reply_markup,
                message_thread_id=thread_id,
            )
            new_message_id = msg.message_id

    if caption and _is_pick_or_ban_group_caption(caption) and timeout_seconds is not None:
        _schedule_caption_timer(
            bot=bot,
            chat_id=chat_id,
            message_id=new_message_id,
            caption=caption,
            timeout_seconds=timeout_seconds,
        )
    elif new_message_id is not None:
        cancel_caption_timer(chat_id, new_message_id)

    return new_message_id


# ── DM with group-chat fallback ─────────────────────────────────────────────

async def send_dm_with_fallback(
    bot: Bot,
    *,
    tg_id: int,
    photo_bytes: bytes,
    caption: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    group_chat_id: Optional[int] = None,
    group_thread_id: Optional[int] = None,
    group_fallback_text: Optional[str] = None,
    group_fallback_markup=None,
    context: str = "duel-dm",
) -> tuple[DmResult, Optional[int]]:
    """Send a photo to a player's private chat, with structured fallbacks.

    Behaviour:
      * Normal case → DmResult.DM_SENT, message_id.
      * TelegramForbiddenError (user blocked the bot OR never pressed /start) →
        if `group_chat_id` is provided, posts `group_fallback_text` and
        `group_fallback_markup` into the group so the duel can proceed there.
        Returns DmResult.FORBIDDEN_FALLBACK, message_id of the group post
        (or None if the group post also failed).
      * TelegramBadRequest with "chat not found" → same as Forbidden (likely
        the user never started the bot).
      * Any other exception → logged with exc_info; returns DmResult.FAILED, None.

    `context` is used only in log messages so multiple call sites are
    distinguishable (e.g. "ban-dm-p1", "pick-dm", "end-card-dm").
    """
    file = BufferedInputFile(photo_bytes, filename="duel.png")

    try:
        msg = await bot.send_photo(
            tg_id,
            photo=file,
            caption=caption or None,
            parse_mode=parse_mode if caption else None,
            reply_markup=reply_markup,
        )
        return DmResult.DM_SENT, msg.message_id

    except TelegramForbiddenError as exc:
        logger.error(
            f"{context}: DM forbidden for tg_id={tg_id} ({exc}); "
            f"trying group fallback (chat={group_chat_id})"
        )
    except TelegramBadRequest as exc:
        # "chat not found" / "user is deactivated" — same treatment as Forbidden.
        msg_l = str(exc).lower()
        if "chat not found" in msg_l or "user is deactivated" in msg_l:
            logger.error(
                f"{context}: DM rejected for tg_id={tg_id} ({exc}); "
                f"trying group fallback"
            )
        else:
            logger.error(
                f"{context}: DM bad-request for tg_id={tg_id}: {exc}",
                exc_info=True,
            )
            return DmResult.FAILED, None
    except Exception:
        logger.error(
            f"{context}: DM send failed for tg_id={tg_id} (unexpected)",
            exc_info=True,
        )
        return DmResult.FAILED, None

    # Forbidden / chat-not-found fallback path.
    if group_chat_id is None or group_fallback_text is None:
        # No group fallback configured — bubble up as forbidden.
        return DmResult.FORBIDDEN_FALLBACK, None

    try:
        group_msg = await bot.send_message(
            group_chat_id,
            group_fallback_text,
            parse_mode=parse_mode,
            reply_markup=group_fallback_markup,
            message_thread_id=group_thread_id,
        )
        logger.info(
            f"{context}: group fallback posted in chat={group_chat_id} "
            f"for tg_id={tg_id} (msg_id={group_msg.message_id})"
        )
        return DmResult.FORBIDDEN_FALLBACK, group_msg.message_id
    except Exception:
        logger.error(
            f"{context}: group fallback also failed in chat={group_chat_id} "
            f"for tg_id={tg_id}",
            exc_info=True,
        )
        return DmResult.FORBIDDEN_FALLBACK, None


async def send_dm_text_quiet(
    bot: Bot,
    *,
    tg_id: int,
    text: str,
    parse_mode: str = "HTML",
    context: str = "duel-dm-text",
) -> bool:
    """Send a plain text DM that may silently fail (no group fallback).

    Used for follow-up notifications (e.g. duel cancelled, end-card already
    in group). Logs Forbidden at INFO level since it's an expected user-side
    condition, not a bug. Returns True if delivered.
    """
    try:
        await bot.send_message(tg_id, text, parse_mode=parse_mode)
        return True
    except TelegramForbiddenError:
        logger.info(f"{context}: skipped, tg_id={tg_id} hasn't /start'ed the bot")
        return False
    except Exception:
        logger.error(f"{context}: failed for tg_id={tg_id}", exc_info=True)
        return False
