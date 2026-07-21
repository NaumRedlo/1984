"""Account section: osu! link / relink / unlink, and card language (EN/RU).

Both are global per Telegram identity (OAuth is per Telegram id, language drives
both card text and the Telegram UI) — not per-chat — so no tenant_chat_id /
ensure_dm_tenant is involved here.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_identity_user
from utils.language import get_language, set_language
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.settings_menu.common import _nav_row

router = Router(name="settings_account")


# ── Account section (osu! link / relink / unlink) ──────────────────────────

async def _account_view(tg_id: int, lang: str = "en"):
    """Build (text, keyboard) for the Account section from the caller's global
    identity (OAuth is per Telegram id, not per group)."""
    from services.oauth.token_manager import has_oauth
    async with get_db_session() as session:
        user = await get_registered_identity_user(session, tg_id)
        linked = bool(user and user.osu_user_id)
        name = user.osu_username if user else None
    oauth = await has_oauth(tg_id) if linked else False

    if not linked:
        text = t("sts.acc.not_linked", lang)
        return text, InlineKeyboardMarkup(inline_keyboard=[_nav_row(lang)])

    oauth_status = t("sts.acc.oauth_yes" if oauth else "sts.acc.oauth_no", lang)
    text = t("sts.acc.linked", lang, name=escape_html(name), status=oauth_status)
    rows = []
    if oauth:
        rows.append([InlineKeyboardButton(text=t("sts.kb.relink", lang), callback_data="st:acc:relink")])
    else:
        rows.append([InlineKeyboardButton(text=t("sts.kb.link", lang), callback_data="st:acc:link")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.unlink", lang), callback_data="st:acc:unlink")])
    rows.append(_nav_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:acc")
async def cb_account(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    text, kb = await _account_view(callback.from_user.id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# ── Language (card text — EN/RU) ────────────────────────────────────────────
# Global per Telegram identity, same as Account/OAuth — not a per-chat setting,
# so no tenant_chat_id / ensure_dm_tenant involved.

def _language_kb(current: str, lang: str = "en") -> InlineKeyboardMarkup:
    def mark(code):
        return "● " if current == code else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark('EN')}🇬🇧 English", callback_data="st:lang:set:EN")],
        [InlineKeyboardButton(text=f"{mark('RU')}🇷🇺 Русский", callback_data="st:lang:set:RU")],
        _nav_row(lang),
    ])


@router.callback_query(F.data == "st:lang")
async def cb_language(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    current = await get_language(callback.from_user.id)
    text = t("sts.lang.view", lang, current=current)
    try:
        await callback.message.edit_text(text, reply_markup=_language_kb(current, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:lang:set:"))
async def cb_language_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    new_card_lang = callback.data.split(":", 3)[3]
    if new_card_lang not in ("EN", "RU"):
        await callback.answer()
        return
    await set_language(callback.from_user.id, new_card_lang)
    # The Telegram UI itself follows the same setting, so re-render this
    # screen in the language just chosen rather than the stale injected one.
    ui_lang = new_card_lang.lower()
    try:
        await callback.message.edit_text(
            t("sts.lang.view", ui_lang, current=new_card_lang),
            reply_markup=_language_kb(new_card_lang, ui_lang), parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(t("sts.lang.set_alert", ui_lang, lang=new_card_lang))


async def _send_oauth_link(callback: types.CallbackQuery, relink: bool, lang: str = "en"):
    """Send a fresh OAuth authorization link as a new message. For relink, drop
    the stored token first so a clean re-authorization is possible."""
    from services.oauth.server import generate_oauth_url, track_link_message
    tg_id = callback.from_user.id
    if relink:
        from sqlalchemy import delete
        from db.models.oauth_token import OAuthToken
        async with get_db_session() as session:
            await session.execute(delete(OAuthToken).where(OAuthToken.telegram_id == tg_id))
            await session.commit()
    url = generate_oauth_url(tg_id)
    title = t("sts.acc.relink_title" if relink else "sts.acc.link_title", lang)
    sent = await callback.message.answer(
        t("sts.acc.oauth_prompt", lang, title=title, url=url),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    track_link_message(tg_id, sent.chat.id, sent.message_id)
    await callback.answer(t("sts.acc.link_sent", lang))


@router.callback_query(F.data == "st:acc:link")
async def cb_account_link(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _send_oauth_link(callback, relink=False, lang=lang)


@router.callback_query(F.data == "st:acc:relink")
async def cb_account_relink(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _send_oauth_link(callback, relink=True, lang=lang)


@router.callback_query(F.data == "st:acc:unlink")
async def cb_account_unlink(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    # Destructive — confirm first.
    text = t("sts.acc.unlink_confirm", lang)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.confirm_unlink", lang), callback_data="st:acc:unlinkyes")],
        [InlineKeyboardButton(text=t("sts.kb.cancel_back", lang), callback_data="st:acc")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:acc:unlinkyes")
async def cb_account_unlink_confirm(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    from bot.handlers.auth.handlers import perform_unlink
    from utils.osu.resolve_user import get_identity_user
    tg_id = callback.from_user.id
    async with get_db_session() as session:
        user = await get_identity_user(session, tg_id)
        ok, err = await perform_unlink(session, user, tg_id, lang)
    if not ok:
        if err == "not_linked":
            await callback.answer(t("sts.acc.not_linked_alert", lang), show_alert=True)
        else:
            await callback.answer(t("sts.acc.unlink_cooldown", lang, remaining=err), show_alert=True)
        return
    try:
        await callback.message.edit_text(
            t("sts.acc.unlinked", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
            ]),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(t("sts.done", lang))
