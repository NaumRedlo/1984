from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

router = Router(name="start")

@router.message(Command("start"))
async def send_welcome(message: Message):
    name = message.from_user.first_name
    await message.answer(
        f"<b>PROJECT 1984: CLASSIFIED</b>\n"
        f"{'═' * 30}\n\n"
        f"Welcome, <b>{name}</b>.\n"
        f"You have been granted access to the <b>Bounty Department</b> surveillance system.\n\n"
        f"<b>Quick Start:</b>\n"
        f"• <code>/register [nickname]</code> — Link your osu! identity\n"
        f"• <code>/profile</code> — View your stats & hunter rank\n"
        f"• <code>/rs</code> — Your most recent play\n"
        f"• <code>/hps</code> — Analyze map HP potential\n"
        f"• <code>/compare [player]</code> — Compare stats\n"
        f"• <code>/leaderboard</code> — Hunter Points rankings\n"
        f"• <code>/help</code> — Full list of directives\n\n"
        f"<i>Big Brother is watching your rank.</i>",
        parse_mode="HTML"
    )
