from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.handlers.bsk.common import dm

router = Router(name="bsk.ban")


@router.callback_query(F.data.startswith("bskban:"))
async def on_bsk_ban_toggle(callback: CallbackQuery):
    """Toggle a map in the player's ban selection during the ban phase."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Invalid format.", show_alert=True)
        return
    try:
        duel_id = int(parts[1])
        beatmap_id = int(parts[2])
    except ValueError:
        await callback.answer("Invalid format.", show_alert=True)
        return

    result = await dm.toggle_ban(callback.bot, duel_id, callback.from_user.id, beatmap_id)

    if result == 'ok':
        await callback.answer()
    elif result == 'limit':
        await callback.answer(
            f"Максимум {dm.MAX_BANS} бана — сначала сними один.", show_alert=True
        )
    elif result == 'already_ready':
        await callback.answer("Ты уже подтвердил баны.", show_alert=True)
    else:
        await callback.answer("Фаза бана для этой дуэли не активна.", show_alert=True)


@router.callback_query(F.data.startswith("bskbandone:"))
async def on_bsk_ban_confirm(callback: CallbackQuery):
    """Confirm the player's ban selection."""
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("Неверный формат.", show_alert=True)
        return
    try:
        duel_id = int(parts[1])
    except ValueError:
        await callback.answer("Неверный формат.", show_alert=True)
        return

    result = await dm.confirm_ban(callback.bot, duel_id, callback.from_user.id)

    if result == 'done':
        await callback.answer("✅ Оба готовы — начинаем выбор карты!", show_alert=False)
    elif result == 'ok':
        await callback.answer("✅ Баны подтверждены! Ждём соперника…", show_alert=False)
    elif result == 'already':
        await callback.answer("Ты уже подтвердил баны.", show_alert=True)
    else:
        await callback.answer("Фаза бана для этой дуэли не активна.", show_alert=True)
