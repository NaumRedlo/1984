from __future__ import annotations

from typing import Optional, Dict, Any, Tuple

from aiogram import Router, types
from aiogram.types import BufferedInputFile

from db.database import get_db_session
from services.image import card_renderer
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import resolve_osu_query_status
from utils.titles import TITLE_REGISTRY
from utils.title_progress import unlock_title
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from services.oauth.token_manager import get_valid_token

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


async def _build_subject(session, osu_api_client, query: str, chat_id: int, oauth_token: str = None) -> Tuple[Optional[Dict[str, Any]], str]:
    registered, user_data, status = await resolve_osu_query_status(session, osu_api_client, query, chat_id)
    if not user_data:
        return None, status

    fresh = None
    if registered:
        fresh = await osu_api_client.get_user_data(registered.osu_user_id, oauth_token=oauth_token)
        if fresh:
            user_data = fresh

    source = fresh or user_data
    subject = {
        "username": source.get("username") or (registered.osu_username if registered else query),
        "pp": source.get("pp") or ((registered.player_pp or 0) if registered else 0),
        "rank": source.get("global_rank") or ((registered.global_rank or 0) if registered else 0),
        "accuracy": source.get("accuracy") or ((registered.accuracy or 0.0) if registered else 0.0),
        "play_count": source.get("play_count") or ((registered.play_count or 0) if registered else 0),
        "play_time": source.get("play_time") or ((registered.play_time or 0) if registered else 0),
        "ranked_score": source.get("ranked_score") or ((registered.ranked_score or 0) if registered else 0),
        "avatar_url": source.get("avatar_url") or (registered.avatar_url if registered else None),
        "cover_url": source.get("cover_url") or (registered.cover_url if registered else None),
    }
    return subject, status


async def _build_self_subject(session, osu_api_client, user, oauth_token: str = None) -> Dict[str, Any]:
    fresh = None
    if user and user.osu_user_id:
        try:
            fresh = await osu_api_client.get_user_data(user.osu_user_id, oauth_token=oauth_token)
        except Exception:
            fresh = None

    source = fresh or {}
    return {
        "username": source.get("username") or user.osu_username,
        "pp": source.get("pp") or user.player_pp or 0,
        "rank": source.get("global_rank") or user.global_rank or 0,
        "accuracy": source.get("accuracy") or user.accuracy or 0.0,
        "play_count": source.get("play_count") or user.play_count or 0,
        "play_time": source.get("play_time") or user.play_time or 0,
        "ranked_score": source.get("ranked_score") or user.ranked_score or 0,
        "avatar_url": source.get("avatar_url") or user.avatar_url,
        "cover_url": source.get("cover_url") or user.cover_url,
    }



@router.message(TextTriggerFilter("cmp"))
async def compare_users(message: types.Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    left_arg, right_arg = _parse_compare_args(trigger_args.args if trigger_args else "")

    async def _not_found_msg(wait_msg, name, status):
        key = "common.user_not_found" if status == "not_found" else "common.user_not_registered"
        await wait_msg.edit_text(t(key, lang, name=escape_html(name)), parse_mode="HTML")

    async with get_db_session() as session:
        try:
            self_user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
            if not self_user:
                return

            if not right_arg and not left_arg:
                await message.answer(t("cmp.usage", lang), parse_mode="HTML")
                return

            wait_msg = await message.answer(t("common.loading", lang))
            token = await get_valid_token(self_user.telegram_id)

            if right_arg is None:
                target_query = left_arg
                if not target_query:
                    await wait_msg.edit_text(t("cmp.parse_failed", lang))
                    return
                user1 = await _build_self_subject(session, osu_api_client, self_user, oauth_token=token)
                user2, status = await _build_subject(session, osu_api_client, target_query, tenant_chat_id, oauth_token=token)
                if not user2:
                    await _not_found_msg(wait_msg, target_query, status)
                    return
            else:
                query1 = left_arg or self_user.osu_username
                query2 = right_arg
                if left_arg is None:
                    user1 = await _build_self_subject(session, osu_api_client, self_user, oauth_token=token)
                else:
                    user1, status = await _build_subject(session, osu_api_client, query1, tenant_chat_id, oauth_token=token)
                    if not user1:
                        await _not_found_msg(wait_msg, query1, status)
                        return
                user2, status = await _build_subject(session, osu_api_client, query2, tenant_chat_id, oauth_token=token)
                if not user2:
                    await _not_found_msg(wait_msg, query2, status)
                    return

            if user1["username"].lower() == user2["username"].lower():
                await wait_msg.edit_text(t("cmp.same_player", lang))
                return

            # Count this /compare-on-others toward "Informant" (secret, 50 uses).
            self_user.compare_uses = (self_user.compare_uses or 0) + 1
            informant = None
            if self_user.compare_uses >= 50 and await unlock_title(self_user, "compare_50", session):
                informant = TITLE_REGISTRY["compare_50"]
            await session.commit()

            pp_diff = (user1["pp"] or 0) - (user2["pp"] or 0)
            rank_diff = (user1["rank"] or 0) - (user2["rank"] or 0) if user1["rank"] and user2["rank"] else 0
            acc_diff = (user1["accuracy"] or 0.0) - (user2["accuracy"] or 0.0)
            pc_diff = (user1["play_count"] or 0) - (user2["play_count"] or 0)
            pt_diff = (user1["play_time"] or 0) - (user2["play_time"] or 0)
            rs_diff = (user1["ranked_score"] or 0) - (user2["ranked_score"] or 0)

            compare_text = t(
                "cmp.text", lang, sep="═" * 40,
                u1=escape_html(user1["username"]), u2=escape_html(user2["username"]),
                pp1=_format_number(user1["pp"]), pp2=_format_number(user2["pp"]), ppd=_format_diff(pp_diff),
                rank1=_format_number(user1["rank"]), rank2=_format_number(user2["rank"]), rankd=_format_diff(rank_diff),
                acc1=f"{user1['accuracy']:.2f}", acc2=f"{user2['accuracy']:.2f}",
                accd=_format_diff(acc_diff, suffix="%"),
                pc1=_format_number(user1["play_count"]), pc2=_format_number(user2["play_count"]),
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

            # Secret reveal: announce Informant the moment it unlocks.
            if informant is not None:
                try:
                    await message.answer(
                        t("common.title_unlocked", lang,
                          user=escape_html(self_user.osu_username),
                          title=escape_html(informant.name), rarity=informant.rarity_label),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error in /compare: {e}", exc_info=True)
            await message.answer(t("cmp.error", lang))


def _format_diff(value: float, suffix: str = "") -> str:
    if value == 0:
        return "±0" + suffix

    symbol = "+" if value > 0 else ""
    emoji = "🟢" if value > 0 else "🔴"

    return f"{emoji} {symbol}{value:,.2f}{suffix}"


__all__ = ["router"]
