from typing import Optional, Dict

from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.best_score import UserBestScore
from db.models.user import User
from services.command_refresh import RefreshBusy, RefreshCooldown, requests as refresh_requests
from services import incidents
from services.image import card_renderer
from utils.logger import get_logger
from utils.osu.resolve_user import get_registered_user, get_reply_target_user, resolve_osu_query_status
from utils.formatting.text import escape_html
from utils.titles import TITLE_REGISTRY
from utils.title_progress import bump_profile_opens
from utils.i18n import t
from utils.language import get_language
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from services.refresh import refresh_user, needs_blocking_refresh
from services.profile_page import build_page_data as _build_page_data

from utils.osu import rulesets

router = Router(name="profile")
logger = get_logger("handlers.profile")

def _tg_handle(from_user) -> Optional[str]:
    username = getattr(from_user, "username", None) if from_user else None
    return f"@{username}" if username else None

async def _handle_of(bot, chat_id: int, telegram_id: Optional[int]) -> Optional[str]:
    if not telegram_id:
        return None
    try:
        return _tg_handle((await bot.get_chat_member(chat_id, telegram_id)).user)
    except Exception:
        pass
    try:
        return _tg_handle(await bot.get_chat(telegram_id))
    except Exception:
        return None

async def _resolve_profile_user(session, osu_api_client, tg_id: int, chat_id: int, query: Optional[str] = None):
    if not query:
        user = await get_registered_user(session, tg_id, chat_id)
        return user, None, "registered" if user else "not_found"

    return await resolve_osu_query_status(session, osu_api_client, query, chat_id)

def _pf_keyboard(osu_id, subject_tg_id: Optional[int] = None, viewer_tg_id: Optional[int] = None,
                 lang: str = "en", ruleset: Optional[int] = None) -> Optional[InlineKeyboardMarkup]:
    rows = []
    if osu_id:
        rows.append([InlineKeyboardButton(text=t("pf.kb.osu_profile", lang), url=f"https://osu.ppy.sh/users/{osu_id}")])
    if subject_tg_id and viewer_tg_id:
        rows.append([InlineKeyboardButton(text=t("pf.kb.top_plays", lang),
                                          callback_data=f"tpp|open|{viewer_tg_id}|{subject_tg_id}|{ruleset or 0}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

@router.message(TextTriggerFilter("pf"))
async def show_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    ruleset, query = await rulesets.chosen((trigger_args.args or "") if trigger_args else "", tg_id)
    async with get_db_session() as session:
        try:
            public_lookup = False
            tg_handle = None

            if not query:
                reply_user = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
                if reply_user and reply_user.osu_user_id:
                    user = reply_user
                    if message.reply_to_message:
                        tg_handle = _tg_handle(message.reply_to_message.from_user)
                else:
                    user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
                    if not user:
                        return
                    tg_handle = _tg_handle(message.from_user)
            else:
                user, user_data, status = await _resolve_profile_user(session, osu_api_client, tg_id, tenant_chat_id, query)
                if status == "not_found" or not user_data:
                    await message.answer(
                        t("pf.user_not_found", lang, name=escape_html(query)),
                        parse_mode="HTML",
                    )
                    return
                if status == "unregistered":
                    user = user_data
                    public_lookup = True
                else:
                    tg_handle = await _handle_of(message.bot, message.chat.id, getattr(user, "telegram_id", None))

            is_self = not public_lookup and getattr(user, "telegram_id", None) == tg_id
            if is_self:
                bump_profile_opens(user)
                await session.commit()

            if is_self:
                if needs_blocking_refresh(user.last_api_update):
                    wait_msg = await message.answer(t("pf.refreshing", lang))
                    ok = await refresh_user(user, session, osu_api_client, mode="full")
                    if ok:
                        await session.commit()
                        await session.refresh(user)
                        await wait_msg.delete()
                    else:
                        await wait_msg.edit_text(t("pf.refresh_failed_cached", lang))

            try:
                data = await _build_page_data(user, osu_api_client, session, tg_handle=tg_handle, viewer_tg_id=tg_id, ruleset=ruleset)
                buf = await card_renderer.generate_profile_dashboard_async(data)
                photo = BufferedInputFile(buf.read(), filename="profile.png")
                subject_tg_id = getattr(user, "telegram_id", None) if not isinstance(user, dict) else None
                keyboard = _pf_keyboard(data.get("osu_id"), subject_tg_id, tg_id, lang, ruleset)
                await message.answer_photo(photo=photo, reply_markup=keyboard)
            except Exception as img_err:
                logger.warning(f"Profile card generation failed: {img_err}", exc_info=True)
                code = await incidents.note("bot", "pf.card", f"{type(img_err).__name__}: {img_err}", telegram_id=tg_id,
                                            chat_id=message.chat.id, detail={"query": query, "ruleset": ruleset}, error=img_err)
                await message.answer(t("pf.card_gen_failed", lang) + t("farm.code", lang, code=code))

        except Exception as e:
            logger.error(f"Error in /profile for {tg_id}: {e}", exc_info=True)
            code = await incidents.note("bot", "pf.load", f"{type(e).__name__}: {e}", telegram_id=tg_id,
                                        chat_id=message.chat.id, detail={"query": query, "ruleset": ruleset}, error=e)
            await message.answer(t("pf.load_error", lang) + t("farm.code", lang, code=code))

@router.message(TextTriggerFilter("rf"))
async def refresh_profile(message: types.Message, osu_api_client, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    wait_msg = None
    async with get_db_session() as session:
        try:
            user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
            if not user:
                return

            wait_msg = await message.answer(t("rf.loading", lang), parse_mode="HTML")

            user_id = user.id

            async def refresh():
                async with get_db_session() as updating:
                    subject = await updating.get(User, user_id)
                    if subject is None:
                        return False
                    ok = await refresh_user(subject, updating, osu_api_client, mode="full")
                    if ok:
                        await updating.commit()
                    return ok

            scope = ("profile", user.osu_user_id or ("user", user_id))
            ok = await refresh_requests.run(scope + (user_id,), refresh, scope=scope)

            if ok:
                await wait_msg.edit_text(t("rf.success", lang), parse_mode="HTML")
            else:
                await wait_msg.edit_text(t("rf.failed", lang), parse_mode="HTML")

        except RefreshCooldown as exc:
            await wait_msg.edit_text(t("common.refresh_wait", lang, seconds=exc.seconds))
        except RefreshBusy:
            await wait_msg.edit_text(t("common.command_running", lang))
        except Exception as e:
            logger.error(f"Unhandled exception in /refresh for {tg_id}: {e}", exc_info=True)
            error_text = t("rf.error", lang)
            if wait_msg:
                await wait_msg.edit_text(error_text)
            else:
                await message.answer(error_text)

__all__ = ["router"]
