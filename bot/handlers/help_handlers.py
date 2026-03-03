from aiogram import Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("help"))
async def show_help(message: types.Message):
    """
    Displays help for all bot commands.
    """
    help_text = """
🎯 **1984 | Global & Competitive Bot**

─────────────────────────────
📋 **BASIC COMMANDS:**

/start — Welcome and registration
/register *<nickname>* — Registration in the system
/profile — Show your profile
/refresh — Update data from osu! API
/help — This reference

─────────────────────────────
🏆 **RATING AND SEASONS:**

/leaderboard — Top players by HP (current season)
/leaderboard *legacy* — Hall of Fame (all-time)
/season *info* — Information about the current season

─────────────────────────────
🎮 **BOUNTIES:**

/weekly — Current weekly bounty
/bounty *list* — List of active bounties
/submit — Submit the result to the bounty

─────────────────────────────
📊 **RANKING SYSTEM:**

🟢 Candidate — 0 - 250 HP
🔵 Party Member — 251 - 750 HP
🟣 Inspector — 751 - 1500 HP
🟠 High Commissioner — 1501 - 3000 HP
🔴 Big Brother — 3001+ HP

─────────────────────────────
📞 **SUPPORT:**

Telegram: @NaumRedlo, @nazeetskyyy
osu!: NaumRedlo, nazeetskyyy
GitHub: Project 1984: CLASSIFIED

📄 Documentation: (TBA)

*Project 1984: CLASSIFIED © 2026*
"""
    await message.answer(help_text, parse_mode="Markdown")


__all__ = ["router"]
