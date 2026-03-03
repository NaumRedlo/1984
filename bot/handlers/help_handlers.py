# bot/handlers/help_handlers.py
"""
Help Handler
Command: /help
Displays comprehensive bot commands and information about HPS system.
"""

from aiogram import Router, types
from aiogram.filters import Command

router = Router()


@router.message(Command("help"))
async def show_help(message: types.Message):
    """
    Shows help message with all available commands and system info.
    """
    help_text = """
🎯 **1984 | Global & Competitive Bot**

─────────────────────────────
📋 **CORE COMMANDS:**

/start — Start and register
/register <nickname> — Register in the system
/profile — Show your profile
/refresh — Manually update data from osu! API
/help — This help menu

─────────────────────────────
🏆 **RANKING & SEASONS:**

/leaderboard active — Top players by HP (current season)
/leaderboard legacy — Hall of Fame (all-time)
/season — Current season information

─────────────────────────────
🎮 **BOUNTIES:**

/weekly — Current weekly bounty
/bounty list — List of active bounties
/submit — Submit result for bounty

─────────────────────────────
🧮 **HPS CALCULATOR:**

/hps last — Analyze last played map
/hps <beatmap_id> — Analyze map by ID

─────────────────────────────
📊 **HPS RANK SYSTEM:**

🔵 Candidate — 0–250 HP
🟢 Party Member — 251–750 HP
🟠 Inspector — 751–1500 HP
🔴 High Commissioner — 1501–3000 HP
🟣 Big Brother — 3001+ HP

─────────────────────────────
📞 **SUPPORT:**

Telegram: @NaumRedlo, @nazeetskyyy
osu!: NaumRedlo, nazeetskyyy
GitHub: Project 1984: CLASSIFIED

📄 Documentation: [TBA]

*Project 1984: CLASSIFIED © 2026*

*"Big Brother is watching you play."* 👁️
"""
    
    await message.answer(help_text, parse_mode="Markdown")


__all__ = ["router"]
