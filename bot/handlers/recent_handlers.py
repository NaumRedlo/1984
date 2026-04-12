from aiogram import Router, types
from aiogram.types import BufferedInputFile

from db.database import get_db_session
from services.image_generator import card_renderer
from utils.logger import get_logger
from utils.text_utils import escape_html, format_error
from utils.resolve_user import resolve_osu_user, get_registered_user
from bot.filters import TextTriggerFilter, TriggerArgs

logger = get_logger("handlers.recent")
router = Router(name="recent")

@router.message(TextTriggerFilter("rs", "recent"))
async def cmd_recent(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id
    user_input = trigger_args.args

    target_id = None
    display_name = ""
    wait_msg = None

    if not user_input:
        async with get_db_session() as session:
            user = await get_registered_user(session, tg_id)

            if not user or not user.osu_user_id:
                await message.answer(
                    "<b>Вы не зарегистрированы!</b>\n"
                    "Используйте <code>register [никнейм]</code> или укажите имя: <code>rs [никнейм]</code>.",
                    parse_mode="HTML"
                )
                return
            target_id = user.osu_user_id
            display_name = user.osu_username
    else:
        display_name = user_input.strip()
        wait_msg = await message.answer(f"Поиск игрока <b>{escape_html(display_name)}</b>...", parse_mode="HTML")

        try:
            user_data = await resolve_osu_user(osu_api_client, display_name)

            if not user_data:
                await wait_msg.edit_text(format_error(f"Игрок <b>{escape_html(display_name)}</b> не найден."), parse_mode="HTML")
                return

            target_id = user_data.get("id")
            display_name = user_data.get("username")
        except Exception as e:
            logger.error(f"Failed to find user {display_name}: {e}")
            await wait_msg.edit_text(format_error(f"Ошибка при поиске игрока <b>{escape_html(display_name)}</b>."), parse_mode="HTML")
            return
    
    if not wait_msg:
        wait_msg = await message.answer(f"Загрузка последней игры <b>{escape_html(display_name)}</b>...", parse_mode="HTML")

    try:
        logger.info(f"Fetching recent score for ID: {target_id} ({display_name})")
        recent_scores = await osu_api_client.get_user_recent_scores(target_id, limit=1)

        if not recent_scores:
            await wait_msg.edit_text(f"У {escape_html(display_name)} нет недавних игр за последние 24ч.")
            return

        score = recent_scores[0]
        beatmap = score.get("beatmap", {})
        beatmapset = score.get("beatmapset", {})

        # Fetch player cover URL via separate API call (recent scores don't include it)
        player_cover_url = ""
        try:
            user_data = await osu_api_client.get_user_data(target_id)
            if user_data:
                player_cover_url = user_data.get("cover_url") or ""
        except Exception as e:
            logger.debug(f"Failed to fetch user cover for {target_id}: {e}")
        
        artist = beatmapset.get("artist", "Unknown")
        title = beatmapset.get("title", "Unknown")
        version = beatmap.get("version", "Unknown")
        stars = beatmap.get("difficulty_rating", 0.0)
        
        acc = score.get("accuracy", 0) * 100
        rank = score.get("rank", "F")
        pp = score.get("pp") or 0.0
        combo = score.get("max_combo", 0)
        
        raw_mods = score.get("mods", [])
        mods_list = []
        for m in raw_mods:
            if isinstance(m, dict):
                mods_list.append(m.get("acronym", ""))
            else:
                mods_list.append(str(m))
        mods_str = f" +{''.join(mods_list)}" if mods_list else ""
        stats = score.get("statistics", {})
        misses = stats.get("count_miss") or stats.get("miss_count") or 0

        lines = [
            f"<b>Последняя игра {escape_html(display_name)}</b>",
            f"<b>{escape_html(artist)} - {escape_html(title)}</b>",
            f"<i>[{escape_html(version)}]</i>{mods_str} ({stars:.2f}★)",
            "═" * 25,
            f"<b>Ранг:</b> {rank} | <b>Точность:</b> {acc:.2f}%",
            f"<b>Комбо:</b> {combo}x" + (f" ({misses} миссов)" if misses else " (FC)"),
            f"<b>PP:</b> <b>{pp:.0f}pp</b>" if pp > 0 else "<b>PP:</b> —",
        ]

        fallback_text = "\n".join(lines)

        # Try PNG card, fallback to cover photo or text
        try:
            mods_joined = "".join(mods_list) if mods_list else ""
            recent_data = {
                "username": display_name,
                "artist": artist,
                "title": title,
                "version": version,
                "star_rating": stars,
                "mods": mods_joined,
                "rank_grade": rank,
                "accuracy": acc,
                "combo": combo,
                "misses": misses,
                "pp": pp,
                "beatmapset_id": beatmapset.get("id", 0),
                "max_combo": beatmap.get("max_combo", 0),
                # Map difficulty params
                "cs": beatmap.get("cs", 0),
                "ar": beatmap.get("ar", 0),
                "od": beatmap.get("accuracy", 0),
                "hp": beatmap.get("drain", 0),
                "bpm": beatmap.get("bpm", 0),
                "total_length": beatmap.get("total_length", 0),
                # Score details
                "total_score": score.get("total_score") if score.get("total_score") is not None
                    else score.get("legacy_total_score") if score.get("legacy_total_score") is not None
                    else score.get("score", 0),
                # Mapper info
                "mapper_name": beatmapset.get("creator", "Unknown"),
                "mapper_id": beatmapset.get("user_id", 0),
                # Player info
                "player_id": target_id,
                "player_cover_url": player_cover_url,
                # Hit statistics
                "count_300": stats.get("count_300") or stats.get("great", 0),
                "count_100": stats.get("count_100") or stats.get("ok", 0),
                "count_50": stats.get("count_50") or stats.get("meh", 0),
                "pp_if_fc": 0,
                # Requester info (who typed the command)
                "requester_name": message.from_user.first_name or message.from_user.username or "???",
            }
            buf = await card_renderer.generate_recent_card_async(recent_data)
            photo = BufferedInputFile(buf.read(), filename="recent.png")
            await wait_msg.delete()
            await message.answer_photo(photo=photo)
        except Exception as img_err:
            logger.warning(f"Recent card generation failed: {img_err}")
            cover_url = beatmapset.get("covers", {}).get("list@2x")
            if cover_url:
                await wait_msg.delete()
                await message.answer_photo(photo=cover_url, caption=fallback_text, parse_mode="HTML")
            else:
                await wait_msg.edit_text(fallback_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error fetching score for {target_id}: {e}", exc_info=True)
        await wait_msg.edit_text(format_error("Не удалось получить последний скор из osu! API."))

__all__ = ["router"]
