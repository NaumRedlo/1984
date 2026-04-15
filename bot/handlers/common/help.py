from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    InputMediaPhoto, BufferedInputFile,
)

from bot.filters import TextTriggerFilter, TriggerArgs
from services.image import card_renderer
from utils.logger import get_logger

logger = get_logger("handlers.help")


router = Router(name="help")


async def _send_help(message: types.Message):
    try:
        photo = await card_renderer.generate_help_main_card_async()
        await message.answer_photo(
            photo=BufferedInputFile(photo.read(), filename="help.png"),
            reply_markup=get_help_keyboard(),
        )
    except Exception as e:
        logger.warning(f"Help card generation failed: {e}", exc_info=True)
        await message.answer(
            "Команды: profile, rs, hps, lb, duel, bounty, register, unlink.",
            reply_markup=get_help_keyboard(),
        )

CATEGORIES = ["osu", "hps", "duel", "bounty", "account", "about"]

CATEGORY_LABELS = {
    "osu": "Команды osu!",
    "hps": "Система HPS",
    "duel": "Дуэли",
    "bounty": "Баунти",
    "account": "Аккаунт",
    "about": "О проекте",
}


def get_help_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text=CATEGORY_LABELS["osu"], callback_data="help_osu"),
            InlineKeyboardButton(text=CATEGORY_LABELS["hps"], callback_data="help_hps"),
        ],
        [
            InlineKeyboardButton(text=CATEGORY_LABELS["duel"], callback_data="help_duel"),
            InlineKeyboardButton(text=CATEGORY_LABELS["bounty"], callback_data="help_bounty"),
        ],
        [
            InlineKeyboardButton(text=CATEGORY_LABELS["account"], callback_data="help_account"),
            InlineKeyboardButton(text=CATEGORY_LABELS["about"], callback_data="help_about"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад к разделам", callback_data="help_main")]
    ])


@router.message(Command("help"))
async def cmd_help_command(message: types.Message):
    await _send_help(message)


@router.message(TextTriggerFilter("help"))
async def cmd_help_text(message: types.Message, trigger_args: TriggerArgs = None):
    await _send_help(message)


@router.message(TextTriggerFilter("help"))
async def cmd_help_button(message: types.Message, trigger_args: TriggerArgs = None):
    await _send_help(message)


@router.message(TextTriggerFilter("/help"))
async def cmd_help_slash(message: types.Message, trigger_args: TriggerArgs = None):
    await _send_help(message)


@router.message(F.text.regexp(r"^/help(?:@\w+)?(?:\s|$)"))
async def cmd_help_regex(message: types.Message):
    await _send_help(message)


@router.callback_query(F.data.startswith("help_"))
async def process_help_callback(callback: CallbackQuery):
    action = callback.data.replace("help_", "", 1)

    if action == "main":
        photo = await card_renderer.generate_help_main_card_async()
        media = InputMediaPhoto(media=BufferedInputFile(photo.read(), filename="help.png"))
        await callback.message.edit_media(media=media, reply_markup=get_help_keyboard())
    elif action in CATEGORIES:
        photo = await card_renderer.generate_help_card_async(action)
        media = InputMediaPhoto(media=BufferedInputFile(photo.read(), filename=f"help_{action}.png"))
        await callback.message.edit_media(media=media, reply_markup=_back_keyboard())

    await callback.answer()


__all__ = ["router"]
