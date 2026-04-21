from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from db.database import get_db_session
from services.image import card_renderer
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error
from utils.osu.resolve_user import resolve_osu_user, get_registered_user, get_registered_user_by_osu
from utils.osu.helpers import remember_message_context
from utils.osu.mod_utils import apply_mods
from utils.osu.pp_calculator import calculate_pp
from bot.filters import TextTriggerFilter, TriggerArgs

logger = get_logger("handlers.recent")
router = Router(name="recent")


def _pick_score_value(score: dict) -> int:
    """Pick the best total score value from an osu! API score object.
    Lazer scores have legacy_total_score=0 and real value in total_score.
    Stable scores have legacy_total_score with the original value.
    """
    legacy = score.get("legacy_total_score")
    total = score.get("total_score")
    classic = score.get("score")

    logger.debug(f"Score values: legacy={legacy}, total={total}, classic={classic}, "
                 f"build_id={score.get('build_id')}, id={score.get('id')}")

    # legacy_total_score > 0 means stable score — use it
    if legacy is not None and legacy > 0:
        return int(legacy)
    # total_score is the lazer value
    if total is not None and total > 0:
        return int(total)
    # fallback
    if classic is not None and classic > 0:
        return int(classic)
    return 0


def _detect_client(score: dict) -> str:
    """Detect whether a score was set on stable or lazer."""
    # build_id is only present in lazer scores
    if score.get("build_id") is not None:
        return "lazer"
    # lazer scores have legacy_total_score = 0 or null with total_score > 0
    legacy = score.get("legacy_total_score")
    total = score.get("total_score")
    if (legacy is None or legacy == 0) and total and total > 0:
        return "lazer"
    return "stable"


@router.message(TextTriggerFilter("rs", "recent"))
async def cmd_recent(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id
    user_input = trigger_args.args

    target_id = None
    display_name = ""
    wait_msg = None

    # Reply-to-user: if no args but replying to someone, look up their recent
    if not user_input and message.reply_to_message and message.reply_to_message.from_user:
        reply_tg_id = message.reply_to_message.from_user.id
        if reply_tg_id != tg_id:
            async with get_db_session() as session:
                reply_user = await get_registered_user(session, reply_tg_id)
            if reply_user and reply_user.osu_user_id:
                target_id = reply_user.osu_user_id
                display_name = reply_user.osu_username

    if not target_id and not user_input:
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

    if not target_id and user_input:
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

        async with get_db_session() as session:
            registered_user = await get_registered_user_by_osu(session, osu_user_id=target_id)
            if registered_user:
                try:
                    synced = await osu_api_client.sync_user_map_attempts(registered_user, session, recent_scores)
                    if synced:
                        await session.commit()
                except Exception as e:
                    logger.debug(f"Failed to sync recent map attempts for {target_id}: {e}")

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
            count_300 = stats.get("count_300") or stats.get("great", 0)
            count_100 = stats.get("count_100") or stats.get("ok", 0)
            count_50 = stats.get("count_50") or stats.get("meh", 0)
            beatmap_id = beatmap.get("id", 0)

            # Apply mod adjustments to difficulty params
            raw_cs = float(beatmap.get("cs", 0) or 0)
            raw_ar = float(beatmap.get("ar", 0) or 0)
            raw_od = float(beatmap.get("accuracy", 0) or 0)
            raw_hp = float(beatmap.get("drain", 0) or 0)
            raw_bpm = float(beatmap.get("bpm", 0) or 0)
            raw_length = int(beatmap.get("total_length", 0) or 0)
            adjusted = apply_mods(raw_cs, raw_ar, raw_od, raw_hp, raw_bpm, raw_length, mods_joined)

            # Calculate PP (current, if FC, if SS) via rosu-pp
            pp_if_fc = 0.0
            pp_if_ss = 0.0
            modded_stars = stars
            try:
                pp_result = await calculate_pp(
                    beatmap_id=beatmap_id,
                    mods_str=mods_joined,
                    accuracy=acc,
                    combo=combo,
                    misses=misses,
                    count_300=count_300,
                    count_100=count_100,
                    count_50=count_50,
                )
                if pp_result:
                    pp_if_fc = pp_result["pp_if_fc"]
                    pp_if_ss = pp_result["pp_if_ss"]
                    modded_stars = pp_result["star_rating"]
                    # Use calculated PP if API didn't provide it
                    if not pp:
                        pp = pp_result["pp_current"]
            except Exception as pp_err:
                logger.debug(f"PP calculation failed: {pp_err}")

            recent_data = {
                "score_id": score.get("id", 0),
                "username": display_name,
                "artist": artist,
                "title": title,
                "version": version,
                "star_rating": modded_stars,
                "mods": mods_joined,
                "rank_grade": rank,
                "accuracy": acc,
                "combo": combo,
                "misses": misses,
                "pp": pp,
                "beatmap_id": beatmap_id,
                "beatmapset_id": beatmapset.get("id", 0),
                "max_combo": beatmap.get("max_combo") or 0,
                # Mod-adjusted difficulty params
                "cs": adjusted["cs"],
                "ar": adjusted["ar"],
                "od": adjusted["od"],
                "hp": adjusted["hp"],
                "bpm": adjusted["bpm"],
                "total_length": adjusted["total_length"],
                # Score details
                "total_score": _pick_score_value(score),
                "score_client": _detect_client(score),
                # Mapper info
                "mapper_name": beatmapset.get("creator", "Unknown"),
                "mapper_id": beatmapset.get("user_id", 0),
                # Player info
                "player_id": target_id,
                "player_cover_url": player_cover_url,
                # Hit statistics
                "count_300": count_300,
                "count_100": count_100,
                "count_50": count_50,
                "pp_if_fc": pp_if_fc,
                "pp_if_ss": pp_if_ss,
                # Requester info (who typed the command)
                "requester_name": message.from_user.first_name or message.from_user.username or "???",
                "beatmap_status": beatmap.get("status", ""),
                "played_at": score.get("ended_at") or score.get("created_at", ""),
                # Pass/fail and total objects for completion %
                "passed": score.get("passed", rank != "F"),
                "total_objects": (beatmap.get("count_circles", 0) or 0)
                    + (beatmap.get("count_sliders", 0) or 0)
                    + (beatmap.get("count_spinners", 0) or 0),
            }
            buf = await card_renderer.generate_recent_card_async(recent_data)
            photo = BufferedInputFile(buf.read(), filename="recent.png")

            beatmap_url = f"https://osu.ppy.sh/beatmapsets/{beatmapset.get('id', 0)}#{beatmap.get('mode', 'osu')}/{beatmap.get('id', 0)}"
            buttons = [InlineKeyboardButton(text="Карта", url=beatmap_url)]
            if beatmap_id:
                buttons.append(InlineKeyboardButton(text="Топ карты", callback_data=f"lbm:{beatmap_id}"))
            kb = InlineKeyboardMarkup(inline_keyboard=[buttons])

            await wait_msg.delete()
            sent = await message.answer_photo(photo=photo, reply_markup=kb)
            remember_message_context(sent.chat.id, sent.message_id, recent_data)
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
