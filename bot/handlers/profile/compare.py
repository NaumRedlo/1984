from __future__ import annotations

from typing import Optional, Dict, Any, Tuple

from aiogram import Router, types
from aiogram.types import BufferedInputFile

from db.database import get_db_session
from services.image import card_renderer
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_user, resolve_osu_query_status
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="compare")
logger = get_logger("handlers.compare")


def _format_number(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _format_play_time(seconds) -> str:
    if not seconds or int(seconds) <= 0:
        return "—"
    s = int(seconds)
    days = s // 86400
    hours = (s % 86400) // 3600
    return f"{days}d {hours}h"


def _parse_compare_args(raw_args: str) -> Tuple[Optional[str], Optional[str]]:
    raw_args = (raw_args or "").strip()
    if not raw_args:
        return None, None

    lowered = raw_args.lower()
    if " vs " in lowered:
        left, right = raw_args.split(" vs ", 1)
        return left.strip(), right.strip()

    return None, raw_args


async def _build_subject(session, osu_api_client, query: str) -> Tuple[Optional[Dict[str, Any]], str]:
    registered, user_data, status = await resolve_osu_query_status(session, osu_api_client, query)
    if not user_data:
        return None, status

    if registered:
        fresh = await osu_api_client.get_user_data(registered.osu_user_id)
        if fresh:
            user_data = fresh

    subject = {
        "username": registered.osu_username if registered else user_data.get("username", query),
        "pp": (registered.player_pp if registered else user_data.get("pp")) or 0,
        "rank": (registered.global_rank if registered else user_data.get("global_rank")) or 0,
        "accuracy": (registered.accuracy if registered else user_data.get("accuracy")) or 0.0,
        "play_count": (registered.play_count if registered else user_data.get("play_count")) or 0,
        "play_time": (registered.play_time if registered else user_data.get("play_time")) or 0,
        "ranked_score": (registered.ranked_score if registered else user_data.get("ranked_score")) or 0,
        "avatar_url": (registered.avatar_url if registered else user_data.get("avatar_url")),
        "cover_url": (registered.cover_url if registered else user_data.get("cover_url")),
    }
    return subject, status


async def _build_self_subject(session, osu_api_client, user) -> Dict[str, Any]:
    fresh = None
    if user and user.osu_user_id:
        try:
            fresh = await osu_api_client.get_user_data(user.osu_user_id)
        except Exception:
            fresh = None

    return {
        "username": user.osu_username,
        "pp": user.player_pp or 0,
        "rank": user.global_rank or 0,
        "accuracy": user.accuracy or 0.0,
        "play_count": user.play_count or 0,
        "play_time": user.play_time or 0,
        "ranked_score": user.ranked_score or 0,
        "avatar_url": (fresh or {}).get("avatar_url") or user.avatar_url,
        "cover_url": (fresh or {}).get("cover_url") or user.cover_url,
    }


@router.message(TextTriggerFilter("compare"))
async def compare_users(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    if not osu_api_client:
        await message.answer("Ошибка: API-клиент не инициализирован.")
        return

    left_arg, right_arg = _parse_compare_args(trigger_args.args if trigger_args else "")

    async with get_db_session() as session:
        try:
            self_user = await get_registered_user(session, message.from_user.id)
            if not self_user:
                await message.answer("Сначала зарегистрируйтесь! Используйте register")
                return

            if not right_arg and not left_arg:
                await message.answer(
                    "Использование: <code>compare &lt;никнейм или id&gt;</code>\n"
                    "Или: <code>compare user1 vs user2</code>\n"
                    "Если указан один игрок, сравнение идёт с вашим профилем.",
                    parse_mode="HTML",
                )
                return

            wait_msg = await message.answer("Загрузка данных...")

            if right_arg is None:
                target_query = left_arg
                if not target_query:
                    await wait_msg.edit_text("Не удалось разобрать запрос сравнения.")
                    return
                user1 = await _build_self_subject(session, osu_api_client, self_user)
                user2, status = await _build_subject(session, osu_api_client, target_query)
                if not user2:
                    if status == "not_found":
                        await wait_msg.edit_text(
                            f"Пользователь <b>{escape_html(target_query)}</b> не найден в базе osu!.",
                            parse_mode="HTML",
                        )
                    else:
                        await wait_msg.edit_text(
                            f"Пользователь <b>{escape_html(target_query)}</b> найден в osu!, но не зарегистрирован в боте.",
                            parse_mode="HTML",
                        )
                    return
            else:
                query1 = left_arg or self_user.osu_username
                query2 = right_arg
                if left_arg is None:
                    user1 = await _build_self_subject(session, osu_api_client, self_user)
                else:
                    user1, status = await _build_subject(session, osu_api_client, query1)
                    if not user1:
                        if status == "not_found":
                            await wait_msg.edit_text(
                                f"Пользователь <b>{escape_html(query1)}</b> не найден в базе osu!.",
                                parse_mode="HTML",
                            )
                        else:
                            await wait_msg.edit_text(
                                f"Пользователь <b>{escape_html(query1)}</b> найден в osu!, но не зарегистрирован в боте.",
                                parse_mode="HTML",
                            )
                        return
                user2, status = await _build_subject(session, osu_api_client, query2)
                if not user2:
                    if status == "not_found":
                        await wait_msg.edit_text(
                            f"Пользователь <b>{escape_html(query2)}</b> не найден в базе osu!.",
                            parse_mode="HTML",
                        )
                    else:
                        await wait_msg.edit_text(
                            f"Пользователь <b>{escape_html(query2)}</b> найден в osu!, но не зарегистрирован в боте.",
                            parse_mode="HTML",
                        )
                    return

            if user1["username"].lower() == user2["username"].lower():
                await wait_msg.edit_text("Нельзя сравнивать одного и того же игрока.")
                return

            pp_diff = (user1["pp"] or 0) - (user2["pp"] or 0)
            rank_diff = (user1["rank"] or 0) - (user2["rank"] or 0) if user1["rank"] and user2["rank"] else 0
            acc_diff = (user1["accuracy"] or 0.0) - (user2["accuracy"] or 0.0)
            pc_diff = (user1["play_count"] or 0) - (user2["play_count"] or 0)
            pt_diff = (user1["play_time"] or 0) - (user2["play_time"] or 0)
            rs_diff = (user1["ranked_score"] or 0) - (user2["ranked_score"] or 0)

            compare_text = (
                f"<b>Сравнение: {escape_html(user1['username'])} vs {escape_html(user2['username'])}</b>\n"
                f"{'═' * 40}\n\n"
                f"<b>PP:</b>\n"
                f"  • {escape_html(user1['username'])}: <code>{_format_number(user1['pp'])}</code> ({_format_diff(pp_diff)} PP)\n"
                f"  • {escape_html(user2['username'])}: <code>{_format_number(user2['pp'])}</code>\n\n"
                f"<b>Глобальный ранг:</b>\n"
                f"  • {escape_html(user1['username'])}: <code>#{_format_number(user1['rank'])}</code> ({_format_diff(rank_diff)} позиций)\n"
                f"  • {escape_html(user2['username'])}: <code>#{_format_number(user2['rank'])}</code>\n\n"
                f"<b>Точность:</b>\n"
                f"  • {escape_html(user1['username'])}: <code>{user1['accuracy']:.2f}%</code> ({_format_diff(acc_diff, suffix='%')})\n"
                f"  • {escape_html(user2['username'])}: <code>{user2['accuracy']:.2f}%</code>\n\n"
                f"<b>Количество игр:</b>\n"
                f"  • {escape_html(user1['username'])}: <code>{_format_number(user1['play_count'])}</code>\n"
                f"  • {escape_html(user2['username'])}: <code>{_format_number(user2['play_count'])}</code>"
            )

            try:
                compare_data = {
                    "user1": {
                        "username": user1["username"],
                        "pp": user1["pp"] or 0,
                        "rank": user1["rank"] or 0,
                        "accuracy": user1["accuracy"] or 0.0,
                        "play_count": user1["play_count"] or 0,
                        "play_time": _format_play_time(user1["play_time"]),
                        "ranked_score": user1["ranked_score"] or 0,
                        "avatar_url": user1.get("avatar_url"),
                        "cover_url": user1.get("cover_url"),
                    },
                    "user2": {
                        "username": user2["username"],
                        "pp": user2["pp"] or 0,
                        "rank": user2["rank"] or 0,
                        "accuracy": user2["accuracy"] or 0.0,
                        "play_count": user2["play_count"] or 0,
                        "play_time": _format_play_time(user2["play_time"]),
                        "ranked_score": user2["ranked_score"] or 0,
                        "avatar_url": user2.get("avatar_url"),
                        "cover_url": user2.get("cover_url"),
                    },
                    "diffs": {
                        "pp": pp_diff,
                        "rank": rank_diff,
                        "accuracy": acc_diff,
                        "play_count": pc_diff,
                        "play_time": pt_diff,
                        "ranked_score": rs_diff,
                    },
                }
                buf = await card_renderer.generate_compare_card_async(compare_data)
                photo = BufferedInputFile(buf.read(), filename="compare.png")
                await wait_msg.delete()
                await message.answer_photo(photo=photo)
            except Exception as img_err:
                logger.warning(f"Compare card generation failed: {img_err}")
                await wait_msg.edit_text(compare_text, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error in /compare: {e}", exc_info=True)
            await message.answer("Произошла ошибка при сравнении.")


def _format_diff(value: float, suffix: str = "") -> str:
    if value == 0:
        return "±0" + suffix

    symbol = "+" if value > 0 else ""
    emoji = "🟢" if value > 0 else "🔴"

    return f"{emoji} {symbol}{value:,.2f}{suffix}"


__all__ = ["router"]
