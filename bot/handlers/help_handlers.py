from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from utils.text_utils import escape_html

router = Router(name="help")

def get_help_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="osu! Commands", callback_data="help_osu"),
            InlineKeyboardButton(text="HPS System", callback_data="help_hps")
        ],
        [
            InlineKeyboardButton(text="Account Management", callback_data="help_account"),
            InlineKeyboardButton(text="About Project", callback_data="help_about")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "<b>PROJECT 1984: CLASSIFIED — ACCESS GRANTED</b>\n"
        "Welcome to the <b>Bounty Department</b> surveillance system.\n\n"
        "Use the buttons below to explore available directives and system protocols.\n\n"
        "<i>Big Brother is watching your rank.</i>"
    )
    await message.answer(text, reply_markup=get_help_keyboard(), parse_mode="HTML")

@router.callback_query(F.data.startswith("help_"))
async def process_help_callback(callback: CallbackQuery):
    action = callback.data.split("_")[1]
    
    if action == "osu":
        text = (
            "<b>SURVEILLANCE DATA (osu!)</b>\n"
            "• <code>/profile</code> — View your stats & hunter rank.\n"
            "• <code>/rs [nick]</code> — Show the most recent play.\n"
            "• <code>/top [nick]</code> — List top 5 performances.\n"
            "• <code>/refresh</code> — Force sync data with osu! servers."
        )
    
    elif action == "hps":
        text = (
            "<b>HPS 2.0 PROTOCOLS</b>\n"
            "• <code>/hps [link/id]</code> — Analyze map potential.\n"
            "• <code>/hps last</code> — Calculate HPS for your last play.\n"
            "• <code>/submit</code> — Submit a score for verification.\n\n"
            "<i>Note: HP rewards scale with difficulty and your current PP.</i>"
        )
    
    elif action == "account":
        text = (
            "<b>IDENTITY MANAGEMENT</b>\n"
            "• <code>/register [nick]</code> — Initial system entry.\n"
            "• <code>/auth</code> — Link your osu! account via OAuth.\n"
            "• <code>/settings</code> — Configure privacy and notifications."
        )
    
    elif action == "about":
        text = (
            "<b>SYSTEM CORE INFO</b>\n"
            "<b>Project 1984</b> is an automated bounty management system "
            "designed for the osu! community.\n\n"
            "Developed to track, calculate, and reward exceptional performances."
        )
    
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back to Directives", callback_data="help_main")]
    ])

    if action == "main":
        await callback.message.edit_text(
            "<b>PROJECT 1984: CLASSIFIED</b>\n"
            "Select a category below:",
            reply_markup=get_help_keyboard(),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(text, reply_markup=back_kb, parse_mode="HTML")
    
    await callback.answer()

__all__ = ["router"]
