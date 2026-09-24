import asyncio

from aiogram import Router, types
from aiogram.types import BufferedInputFile

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.profile.targets import resolve_target, token_for
from db.database import get_db_session
from services import tracking
from services.image import card_renderer
from utils.formatting.text import escape_html, format_error
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import assay_service, rulesets

logger = get_logger("handlers.update")
router = Router(name="update")

ROWS_SHOWN = 5


def _mods(raw: dict) -> list:
    return [m.get("acronym", "") if isinstance(m, dict) else str(m) for m in raw.get("mods") or []]


def _row(position: int, raw: dict) -> dict:
    beatmap, beatmapset = raw.get("beatmap") or {}, raw.get("beatmapset") or {}
    mods = _mods(raw)
    legacy = bool(raw.get("legacy_score_id")) or "CL" in mods
    return {
        "position": position,
        "beatmapset_id": beatmapset.get("id"),
        "title": beatmapset.get("title") or "", "artist": beatmapset.get("artist") or "",
        "version": beatmap.get("version") or "",
        "mods": [m for m in mods if m not in ("CL", "NM")],
        "star_rating": beatmap.get("difficulty_rating") or 0.0,
        "eff_sr": beatmap.get("difficulty_rating") or 0.0,
        "accuracy": (raw.get("accuracy") or 0) * 100,
        "max_combo": raw.get("max_combo") or 0,
        "rank": raw.get("rank") or "F",
        "pp": raw.get("pp") or 0.0,
        "client": f"#{position} · {'stable' if legacy else 'lazer'}",
    }


async def _stars(client, row: dict, raw: dict, ruleset: int) -> None:
    beatmap = raw.get("beatmap") or {}
    try:
        if ruleset == 0:
            rating = await client.effective_sr(beatmap.get("id"), raw.get("mods") or [], row["star_rating"],
                                               beatmap.get("checksum"))
        else:
            served = await assay_service.beatmap(beatmap.get("id"), mods=raw.get("mods") or [],
                                                 checksum=beatmap.get("checksum"), ruleset=ruleset)
            rating = served["star_rating"] if served else None
    except Exception:
        rating = None
    if rating:
        row["eff_sr"] = rating


@router.message(TextTriggerFilter("update", "upd"))
async def cmd_update(message: types.Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    lang = (await get_language(message.from_user.id)).lower()
    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return
    ruleset, query = rulesets.split_args(trigger_args.args or "")
    target = await resolve_target(message, query, tenant_chat_id, osu_api_client, lang)
    if not target:
        return
    wait = await message.answer(t("upd.loading", lang, name=escape_html(target.name)), parse_mode="HTML")
    try:
        token = await token_for(target)
        mode = rulesets.RULESETS[ruleset] if ruleset is not None else None
        user_data = await osu_api_client.get_user_data(target.osu_id, mode=mode, oauth_token=token)
        if not user_data:
            await wait.edit_text(format_error(t("rs.player_not_found", lang, name=escape_html(target.name)), lang),
                                 parse_mode="HTML")
            return
        if ruleset is None:
            ruleset = {v: k for k, v in rulesets.RULESETS.items()}.get(user_data.get("playmode"), 0)
        best = await osu_api_client.get_user_best_scores(target.osu_id, limit=100, oauth_token=token,
                                                          mode=rulesets.RULESETS[ruleset])
        now = tracking.Standing.of(user_data, best)
        async with get_db_session() as session:
            kept = await tracking.load(session, target.osu_id, ruleset)
            changes = tracking.compare(tracking.standing_of(kept), now, best,
                                       since=kept.taken_at if kept else None)
            await tracking.save(session, kept, target.osu_id, ruleset, now)
            await session.commit()

        shown = changes.new_scores[:ROWS_SHOWN]
        rows = [_row(pos, raw) for pos, raw in shown]
        await asyncio.gather(*(_stars(osu_api_client, row, raw, ruleset) for row, (_, raw) in zip(rows, shown)))
        data = {
            "lang": lang, "ruleset": ruleset,
            "username": user_data.get("username") or target.name, "avatar_url": user_data.get("avatar_url"),
            "since": changes.since, "first": changes.first,
            "now": {"pp": now.pp, "global_rank": now.global_rank, "country_rank": now.country_rank,
                    "accuracy": now.accuracy, "play_count": now.play_count},
            "delta": {"pp": changes.pp, "global_rank": changes.global_rank, "country_rank": changes.country_rank,
                      "accuracy": changes.accuracy, "play_count": changes.play_count},
            "rows": rows, "new_count": len(changes.new_scores),
            "more": max(0, len(changes.new_scores) - ROWS_SHOWN),
        }
        buf = await card_renderer.generate_update_card_async(data)
        await wait.delete()
        await message.answer_photo(photo=BufferedInputFile(buf.read(), filename="update.png"))
    except Exception as exc:
        logger.error(f"update of {target.osu_id} failed: {exc}", exc_info=True)
        await wait.edit_text(format_error(t("upd.failed", lang), lang), parse_mode="HTML")


__all__ = ["router"]
