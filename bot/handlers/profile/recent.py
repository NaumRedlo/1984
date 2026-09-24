import asyncio

from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from db.database import get_db_session
from services.image import card_renderer
from utils.logger import get_logger
from utils.formatting.text import escape_html, format_error
from utils.i18n import t
from utils.osu.resolve_user import resolve_osu_user, get_registered_user, get_registered_user_by_osu, get_real_reply
from utils.osu.helpers import remember_message_context
from bot.handlers.common.auth import require_registered_user
from services.oauth.token_manager import get_valid_token
from utils.title_progress import evaluate_recent_plays
from utils.osu.api_client import _is_perfect
from utils.language import get_language
from bot.filters import TextTriggerFilter, TriggerArgs
from services.image.render.recent import build_recent_card_data, _pick_score_value
from utils.osu import rulesets

logger = get_logger("handlers.recent")
router = Router(name="recent")

RECENT_LIMIT = 50

def _ended(raw: dict) -> str:
    return str(raw.get("ended_at") or raw.get("created_at") or "")

async def fetch_recent(client, osu_id: int, token, ruleset=None) -> tuple[list, dict | None]:
    """osu!standard plays (the ones leaderboards and titles count) and the play to show.

    With no mode named, the play to show is the newest of all four modes; osu! itself only
    answers with the player's main mode when none is asked for."""
    wanted = [ruleset] if ruleset is not None else list(rulesets.RULESETS)
    lists = await asyncio.gather(*(
        client.get_user_recent_scores(osu_id, limit=RECENT_LIMIT if rid == 0 else 1, oauth_token=token,
                                      mode=rulesets.RULESETS[rid])
        for rid in wanted
    ), return_exceptions=True)
    found = {rid: (got if isinstance(got, list) else []) for rid, got in zip(wanted, lists)}
    standard = found.get(0, [])
    newest = [plays[0] for plays in found.values() if plays]
    shown = max(newest, key=_ended) if newest else None
    return standard, shown

def _play_from_score(raw: dict) -> dict:
    bm = raw.get("beatmap") or {}
    stats = raw.get("statistics") or {}
    passed = raw.get("passed", True)
    miss = stats.get("miss")
    if miss is None:
        miss = stats.get("count_miss")
    n100 = stats.get("ok")
    if n100 is None:
        n100 = stats.get("count_100")
    return {
        "star_rating": bm.get("difficulty_rating") or 0.0,
        "rank": raw.get("rank") if passed else "F",
        "beatmap_id": bm.get("id"),
        "mods": ",".join(
            m.get("acronym", "") if isinstance(m, dict) else str(m)
            for m in (raw.get("mods") or [])
        ),
        "accuracy": (raw.get("accuracy") or 0) * 100,
        "bpm": bm.get("bpm"),
        "length": bm.get("total_length"),
        "max_combo": raw.get("max_combo") or 0,
        "map_max_combo": bm.get("max_combo"),
        "count_miss": miss,
        "count_100": n100,
        "is_fc": _is_perfect(raw),
        "passed": passed,
        "score": _pick_score_value(raw),
    }

@router.message(TextTriggerFilter("rs"))
async def cmd_recent(message: types.Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower() if tg_id else "en"

    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    ruleset, user_input = rulesets.split_args(trigger_args.args or "")

    target_id = None
    display_name = ""
    wait_msg = None
    requester_tg_id = None
    target_tg_id = None

    real_reply = get_real_reply(message)
    if not user_input and real_reply and real_reply.from_user:
        reply_tg_id = real_reply.from_user.id
        if reply_tg_id != tg_id:
            async with get_db_session() as session:
                reply_user = await get_registered_user(session, reply_tg_id, tenant_chat_id)
                requester = await get_registered_user(session, tg_id, tenant_chat_id)
            if reply_user and reply_user.osu_user_id:
                target_id = reply_user.osu_user_id
                display_name = reply_user.osu_username
                target_tg_id = reply_user.telegram_id
            if requester:
                requester_tg_id = requester.telegram_id

    if not target_id and not user_input:
        async with get_db_session() as session:
            user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
            if not user or not user.osu_user_id:
                return
            target_id = user.osu_user_id
            display_name = user.osu_username
            requester_tg_id = user.telegram_id
            target_tg_id = user.telegram_id

    if not target_id and user_input:
        if requester_tg_id is None:
            async with get_db_session() as session:
                requester = await get_registered_user(session, tg_id, tenant_chat_id)
                if requester:
                    requester_tg_id = requester.telegram_id

        display_name = user_input.strip()
        wait_msg = await message.answer(t("rs.searching_player", lang, name=escape_html(display_name)), parse_mode="HTML")

        try:
            user_data = await resolve_osu_user(osu_api_client, display_name)

            if not user_data:
                await wait_msg.edit_text(
                    format_error(t("rs.player_not_found", lang, name=escape_html(display_name)), lang),
                    parse_mode="HTML",
                )
                return

            target_id = user_data.get("id")
            display_name = user_data.get("username")
        except Exception as e:
            logger.error(f"Failed to find user {display_name}: {e}")
            await wait_msg.edit_text(
                format_error(t("rs.search_error", lang, name=escape_html(display_name)), lang),
                parse_mode="HTML",
            )
            return

    if not wait_msg:
        wait_msg = await message.answer(t("rs.loading", lang, name=escape_html(display_name)), parse_mode="HTML")

    try:
        logger.info(f"Fetching recent score for ID: {target_id} ({display_name})")

        token = None
        if requester_tg_id:
            token = await get_valid_token(requester_tg_id)
        if not token and target_tg_id and target_tg_id != requester_tg_id:
            token = await get_valid_token(target_tg_id)

        recent_scores, score = await fetch_recent(osu_api_client, target_id, token, ruleset)

        if not score:
            await wait_msg.edit_text(
                t("rs.no_recent_plays", lang, name=escape_html(display_name)),
                parse_mode="HTML",
            )
            return
        played_in = rulesets.of_score(score)

        logger.info(
            f"Score fields: total_score={score.get('total_score')!r}, "
            f"legacy_total_score={score.get('legacy_total_score')!r}, "
            f"build_id={score.get('build_id')!r}, type={score.get('type')!r}"
        )

        beatmap = score.get("beatmap", {})
        beatmapset = score.get("beatmapset", {})

        registered_user = None
        newly_titles = []
        async with get_db_session() as session:
            registered_user = await get_registered_user_by_osu(session, tenant_chat_id, osu_user_id=target_id)
            if registered_user:
                if not target_tg_id:
                    target_tg_id = registered_user.telegram_id
                try:
                    # leaderboards and titles are osu!standard's: other modes are shown, not counted

                    synced = await osu_api_client.sync_user_map_attempts(registered_user, session, recent_scores)
                    plays = [_play_from_score(rs) for rs in recent_scores]
                    newly_titles = await evaluate_recent_plays(registered_user, plays, session)
                    if synced or newly_titles:
                        await session.commit()
                except Exception as e:
                    logger.debug(f"Failed to sync/eval recent for {target_id}: {e}")

        player_cover_url = ""
        if registered_user and registered_user.cover_url:
            player_cover_url = registered_user.cover_url
        else:
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
        passed = score.get("passed", True)
        rank = score.get("rank", "F") if passed else "F"
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
        misses = stats.get("miss") or stats.get("count_miss") or 0

        pp_line = f"<b>PP:</b> <b>{pp:.0f}pp</b>" if pp > 0 else "<b>PP:</b> —"
        fallback_text = t(
            "rs.fallback_text", lang, sep="═" * 25,
            name=escape_html(display_name), artist=escape_html(artist), title=escape_html(title),
            version=escape_html(version), mods=mods_str, stars=stars, rank=rank, acc=acc, combo=combo,
            miss_or_fc=(t("rs.misses", lang, n=misses) if misses else t("rs.fc", lang)),
            pp_line=pp_line,
        )

        try:

            recent_data = await build_recent_card_data(
                score,
                username=display_name,
                player_id=target_id,
                player_cover_url=player_cover_url,
                requester_name=message.from_user.first_name or message.from_user.username or "???",
                lang=lang,
                client=osu_api_client,
            )
            beatmap_id = recent_data["beatmap_id"]
            buf = await card_renderer.generate_recent_card_async(recent_data)
            photo = BufferedInputFile(buf.read(), filename="recent.png")

            beatmap_url = f"https://osu.ppy.sh/beatmapsets/{beatmapset.get('id', 0)}#{rulesets.RULESETS[played_in]}/{beatmap.get('id', 0)}"
            buttons = [InlineKeyboardButton(text=t("common.kb.beatmap", lang), url=beatmap_url)]
            if beatmap_id and played_in == 0:
                buttons.append(InlineKeyboardButton(text=t("common.kb.leaderboard", lang), callback_data=f"lbm:{beatmap_id}"))
            rows = [buttons]
            kb = InlineKeyboardMarkup(inline_keyboard=rows)

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

        if newly_titles:
            names = ", ".join(f"{td.name} ({td.rarity_label})" for td in newly_titles)
            try:
                await message.answer(
                    t("rs.titles_unlocked", lang, user=escape_html(display_name), titles=escape_html(names)),
                    parse_mode="HTML",
                )
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Error fetching score for {target_id}: {e}", exc_info=True)
        error_text = format_error(t("rs.fetch_failed", lang), lang)
        if wait_msg:
            await wait_msg.edit_text(error_text, parse_mode="HTML")
        else:
            await message.answer(error_text, parse_mode="HTML")

__all__ = ["router"]
