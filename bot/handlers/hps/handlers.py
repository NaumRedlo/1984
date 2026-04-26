from aiogram import Router, types
from aiogram.types import BufferedInputFile
from sqlalchemy import select

from db.models.user import User
from db.database import get_db_session
from services.image import card_renderer
from utils.hp_calculator import calculate_hps
from utils.osu.api_client import OsuApiClient
from utils.osu.helpers import extract_beatmap_id, get_community_stats
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error
from bot.filters import TextTriggerFilter, TriggerArgs
from services.oauth.token_manager import get_valid_token

logger = get_logger(__name__)

router = Router(name="hps")


@router.message(TextTriggerFilter("hps"))
async def calculate_hps_command(
    message: types.Message,
    trigger_args: TriggerArgs,
    osu_api_client
):
    user_id = message.from_user.id
    args = trigger_args.args

    wait_msg = None
    try:
        async with get_db_session() as session:
            stmt = select(User).where(User.telegram_id == user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()

            if not user:
                await message.answer(
                    format_error("Вы не зарегистрированы. Используйте register [никнейм]"),
                    parse_mode="HTML"
                )
                return

            player_pp = user.player_pp or 0
            osu_user_id = user.osu_user_id
            user_db_id = user.id
            community_stats = await get_community_stats(session)

        token = await get_valid_token(user_db_id)
        is_last = not args or args.strip().lower() == "last"
        wait_msg = await message.answer("Обработка запроса...")

        if is_last:
            await wait_msg.edit_text("Загрузка последней сыгранной карты...")
            scores = await osu_api_client.get_user_recent_scores(osu_user_id, limit=1, oauth_token=token)
            
            if not scores:
                await wait_msg.edit_text(format_error("Не удалось найти недавние скоры."))
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
                await wait_msg.edit_text(format_error("Не удалось распознать ID или ссылку на карту."))
                return

            await wait_msg.edit_text(f"Загрузка информации о карте ID: {beatmap_id}...")
            beatmap = await osu_api_client.get_beatmap(beatmap_id)
            
            if not beatmap:
                await wait_msg.edit_text(format_error(f"Карта {beatmap_id} не найдена."))
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

        map_cs = float(beatmap.get("cs", 0.0))
        map_od = float(beatmap.get("accuracy", 0.0))
        map_ar = float(beatmap.get("ar", 0.0))
        map_hp = float(beatmap.get("drain", 0.0))
        map_bpm = float(beatmap.get("bpm", 0.0))
        map_max_combo = int(beatmap.get("max_combo", 0))

        scenarios = [
            {"type": "win",           "name": "Win",              "acc": accuracy},
            {"type": "condition",     "name": "FC / SS",          "acc": accuracy},
            {"type": "partial",       "name": "Partial (>=98%)",  "acc": 98.0},
            {"type": "participation", "name": "Participation",    "acc": 0},
        ]

        lines = [
            "<b>HPS 2.0 — Map Analysis</b>",
            "═" * 30,
            f"<b>Map:</b> {escape_html(map_title)} <i>[{escape_html(map_version)}]</i>",
            f"<b>Stars:</b> {star_rating:.2f}★",
            f"<b>Duration:</b> {total_length // 60}:{total_length % 60:02d}",
            f"<b>Params:</b> CS{map_cs} | OD{map_od} | AR{map_ar} | HP{map_hp} | {map_bpm:.0f}BPM",
            "═" * 30,
        ]

        results = []
        for sc in scenarios:
            result = calculate_hps(
                result_type=sc["type"],
                star_rating=star_rating,
                drain_time_seconds=total_length,
                player_pp=player_pp,
                community_stats=community_stats,
                accuracy=sc["acc"],
                is_first_submission=False,
                has_zero_fifty=False,
                extra_challenge=False,
                cs=map_cs,
                od=map_od,
                ar=map_ar,
                hp_drain=map_hp,
                bpm=map_bpm,
                max_combo=map_max_combo,
            )
            results.append((sc, result))

        multiplier = results[0][1].get('total_multiplier', 1.0)
        lines.append(f"<b>Potential HP</b> (x{multiplier:.2f}):")

        for sc, result in results:
            final_hp = result.get('final_hp', 0)
            lines.append(f"{sc['name']}: <b>{final_hp} HP</b>")

        rf_data = result.get("relativity_factor", {})
        rf_value = rf_data.get("value", 1.0)
        rf_cat = rf_data.get("category", "Unknown")
        tsf_data = result.get("tsf", {})
        tsf_value = tsf_data.get("value", 1.0)

        lines.extend([
            "═" * 30,
            f"<b>Your PP:</b> {player_pp}",
            f"<b>Progress Multiplier:</b> x{rf_value:.2f} ({escape_html(rf_cat)})",
            f"<b>Tech Skill Factor:</b> x{tsf_value:.2f}",
        ])

        fallback_text = "\n".join(lines)

        # Try PNG card, fallback to text
        try:
            beatmapset_id = beatmapset.get("id", 0)
            creator = beatmapset.get("creator", "")
            creator_id = beatmapset.get("user_id", 0)
            hps_data = {
                "map_title": map_title,
                "map_version": map_version,
                "creator": creator,
                "creator_id": creator_id,
                "star_rating": star_rating,
                "duration": total_length,
                "cs": map_cs,
                "od": map_od,
                "ar": map_ar,
                "hp": map_hp,
                "bpm": map_bpm,
                "max_combo": map_max_combo,
                "beatmapset_id": beatmapset_id,
                "scenarios": [
                    {"name": sc["name"], "hp_reward": res.get("final_hp", 0)}
                    for sc, res in results
                ],
                "player_pp": player_pp,
                "total_multiplier": multiplier,
                "rf_value": rf_value,
                "rf_category": rf_cat,
                "tsf_value": tsf_value,
            }
            buf = await card_renderer.generate_hps_card_async(hps_data)
            photo = BufferedInputFile(buf.read(), filename="hps.png")
            await wait_msg.delete()
            await message.answer_photo(photo=photo)
        except Exception as img_err:
            logger.warning(f"HPS card generation failed: {img_err}")
            await wait_msg.edit_text(fallback_text, parse_mode="HTML")

    except Exception as e:
        logger.exception(f"Critical error in /hps for user {message.from_user.id}")
        error_text = format_error("Внутренняя ошибка при расчёте HPS.")

        if wait_msg:
            await wait_msg.edit_text(error_text, parse_mode="HTML")
        else:
            await message.answer(error_text, parse_mode="HTML")
