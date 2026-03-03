import re
from typing import Optional, Dict

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from db.models.user import User
from db.database import get_db_session
from utils.hp_calculator import calculate_hps
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error

logger = get_logger(__name__)

router = Router(name="hps")


def extract_beatmap_id(text: str) -> Optional[str]:
    """Extracts beatmap ID from osu! links or raw numbers."""
    patterns =[
        r'osu\.ppy\.sh/beatmaps/(\d+)',
        r'osu\.ppy\.sh/beatmapsets/\d+.*?/(\d+)',
        r'^(\d+)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip())
        if match:
            return match.group(1)
    return None


async def get_community_stats(session) -> Dict[str, int]:
    """Fetches PP percentiles across all registered users."""
    stmt = select(User.player_pp).where(User.player_pp.is_not(None))
    result = await session.execute(stmt)
    pp_values = [row[0] for row in result.fetchall() if row[0] and row[0] > 0]

    if len(pp_values) < 10:
        logger.warning("Not enough data for percentiles -> default values used.")
        return {"p25": 2000, "p40": 4500, "p60": 7000, "p75": 10000}

    pp_values.sort()
    count = len(pp_values)

    def percentile(p: int) -> int:
        idx = int(count * p / 100)
        return pp_values[min(idx, count - 1)]

    return {
        "p25": percentile(25),
        "p40": percentile(40),
        "p60": percentile(60),
        "p75": percentile(75),
    }


@router.message(Command("hps"))
async def calculate_hps_command(
    message: types.Message, 
    command: CommandObject, 
    osu_api_client
):
    user_id = message.from_user.id
    args = command.args

    try:
        async with get_db_session() as session:
            stmt = select(User).where(User.telegram_id == user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()

            if not user:
                await message.answer(
                    format_error("You are not registered. Use /register <nickname>"),
                    parse_mode="HTML"
                )
                return

            player_pp = user.player_pp or 0
            osu_user_id = user.osu_user_id
            community_stats = await get_community_stats(session)

        is_last = not args or args.strip().lower() == "last"
        wait_msg = await message.answer("Processing request...")

        if is_last:
            await wait_msg.edit_text("Fetching your last played map...")
            scores = await osu_api_client.get_user_recent_scores(osu_user_id, limit=1)
            
            if not scores:
                await wait_msg.edit_text(format_error("Unable to find recent scores."))
                return

            score = scores[0]
            beatmap = score.get("beatmap", {})
            beatmapset = score.get("beatmapset", {})

            accuracy = float(score.get("accuracy", 0.0)) * 100
            user_combo = score.get("max_combo", 0)
            max_combo = beatmap.get("max_combo", 0)
            is_fc = (user_combo >= max_combo) if max_combo else False

        else:
            beatmap_id = extract_beatmap_id(args)
            if not beatmap_id:
                await wait_msg.edit_text(format_error("Unable to recognize beatmap ID or link."))
                return

            await wait_msg.edit_text(f"Fetching map info for ID: {beatmap_id}...")
            beatmap = await osu_api_client.get_beatmap(beatmap_id)
            
            if not beatmap:
                await wait_msg.edit_text(format_error(f"Map {beatmap_id} not found."))
                return

            beatmapset = beatmap.get("beatmapset", {})
            
            accuracy = 95.0
            is_fc = False

        star_rating = float(beatmap.get("difficulty_rating", 0.0))
        total_length = int(beatmap.get("total_length", 0))
        map_version = beatmap.get("version", "Unknown")
        artist = beatmapset.get("artist", "Unknown")
        title = beatmapset.get("title", "Unknown")
        map_title = f"{artist} - {title}"

        scenarios =[
            {"type": "win",           "name": "🥇 Victory",               "base": 100, "acc": accuracy, "fc": is_fc},
            {"type": "condition",     "name": "✅ Condition (FC/SS)",     "base": 60,  "acc": accuracy, "fc": is_fc},
            {"type": "partial",       "name": "⚠️ Partial",               "base": 30,  "acc": 0,        "fc": False},
            {"type": "participation", "name": "📋 Participation",         "base": 10,  "acc": 0,        "fc": False},
        ]

        lines =[
            "<b>HPS 2.0 — Map Analysis</b>",
            "═" * 30,
            f"<b>Map:</b> {escape_html(map_title)} <i>[{escape_html(map_version)}]</i>",
            f"<b>Difficulty:</b> {star_rating:.2f}★",
            f"<b>Duration:</b> {total_length // 60}:{total_length % 60:02d}",
            "═" * 30,
            "<b>Potential HP:</b>",
        ]

        for sc in scenarios:
            result = calculate_hps(
                result_type=sc["type"],
                star_rating=star_rating,
                drain_time_seconds=total_length,
                player_pp=player_pp,
                community_stats=community_stats,
                accuracy=sc["acc"],
                is_full_combo=sc["fc"],
                is_first_submission=False,
                has_zero_fifty=False,
                extra_challenge=False,
            )
            final_hp = result.get('final_hp', 0)
            multiplier = result.get('total_multiplier', 1.0)
            lines.append(f"{sc['name']}: <b>{final_hp} HP</b> (×{multiplier:.2f})")

        rf_sample = calculate_hps(
            result_type="win", 
            star_rating=star_rating,
            drain_time_seconds=total_length,
            player_pp=player_pp,
            community_stats=community_stats,
            accuracy=accuracy,
            is_full_combo=is_fc
        )
        rf_data = rf_sample.get("relativity_factor", {})
        rf_value = rf_data.get("value", 1.0)
        rf_cat = rf_data.get("category", "Unknown")

        lines.extend([
            "═" * 30,
            f"<b>Your PP:</b> {player_pp}",
            f"<b>Progress Multiplier:</b> ×{rf_value:.2f} ({escape_html(rf_cat)})",
            "",
            "<i>Use /submit to submit the result</i>",
        ])

        await wait_msg.edit_text("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.exception(f"Critical error in /hps for user {message.from_user.id}")
        error_text = format_error("An internal error occurred during HPS calculation.")
        
        try:
            await wait_msg.edit_text(error_text, parse_mode="HTML")
        except NameError:
            await message.answer(error_text, parse_mode="HTML")
