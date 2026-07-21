"""Menu shell: the `sts` entry command plus the home/close navigation.

`cmd_settings` is a message handler, so the callback-only `_owner_guard` does
not run for it — it resolves `lang` itself and binds the fresh menu to its
opener via `_remember_owner`.
"""

from aiogram import Router, F, types

from utils.i18n import t
from utils.language import get_language
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.settings_menu.common import _home_kb, _remember_owner

router = Router(name="settings_shell")


@router.message(TextTriggerFilter("sts"))
async def cmd_settings(message: types.Message, trigger_args=None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    sent = await message.answer(t("sts.home", lang), reply_markup=_home_kb(lang), parse_mode="HTML")
    # Bind this menu to its opener so bystanders can't drive it (group chats).
    _remember_owner(sent.chat.id, sent.message_id, message.from_user.id)


@router.callback_query(F.data == "st:home")
async def cb_home(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        await callback.message.edit_text(t("sts.home", lang), reply_markup=_home_kb(lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:close")
async def cb_close(callback: types.CallbackQuery, tenant_chat_id=None):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
