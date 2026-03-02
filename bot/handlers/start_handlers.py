from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

router = Router()

@router.message(Command("start"))
async def send_welcome(message: Message):
    await message.answer(
        "👋 Hello! This bot created for *1984 Bounties Competitive*.\n"
        "Only the /start command is available at this time.\n"
        "We will soon add registration and other features.",
        parse_mode="Markdown"
    )
