from typing import Dict, Optional

from sqlalchemy import select

from db.models.best_score import UserBestScore
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import rulesets
from utils.titles import TITLE_REGISTRY

logger = get_logger("handlers.profile")

def format_play_time(seconds: int, lang: str = "en") -> str:
    if not seconds or seconds <= 0:
        return "—"
    hours = seconds // 3600
    suffix = "ч" if (lang or "en").lower() == "ru" else "h"
    return f"{hours}{suffix}"

async def build_page_data(
    user, osu_api_client, session, tg_handle: Optional[str] = None,
    viewer_tg_id: Optional[int] = None, ruleset: Optional[int] = None,
) -> Dict:
    def _get(field: str, default=0):
        if isinstance(user, dict):
            aliases = {
                "osu_username": ["osu_username", "username"],
                "osu_user_id": ["osu_user_id", "id"],
                "player_pp": ["player_pp", "pp"],
                "global_rank": ["global_rank", "rank"],
                "country": ["country", "country_code"],
                "accuracy": ["accuracy"],
                "play_count": ["play_count"],
                "play_time": ["play_time"],
                "ranked_score": ["ranked_score"],
                "total_hits": ["total_hits"],
                "total_score": ["total_score"],
                "avatar_url": ["avatar_url"],
                "cover_url": ["cover_url"],
            }
            for key in aliases.get(field, [field]):
                if key in user:
                    return user.get(key, default)
            return default
        return getattr(user, field, default)

    fallback_tg_id = _get("telegram_id", None)
    lang_tg_id = viewer_tg_id if viewer_tg_id is not None else fallback_tg_id
    card_lang = await get_language(lang_tg_id) if lang_tg_id else "EN"

    base = {
        "username": _get("osu_username", "???"),
        "handle": tg_handle or None,
        "osu_id": _get("osu_user_id", 0),
        "pp": _get("player_pp", 0) or 0,
        "global_rank": _get("global_rank", 0) or 0,
        "country": _get("country", "—") or "—",
        "accuracy": _get("accuracy", 0.0) or 0.0,
        "play_count": _get("play_count", 0) or 0,
        "play_time": format_play_time(_get("play_time", 0) or 0, card_lang),
        "ranked_score": _get("ranked_score", 0) or 0,
        "total_hits": _get("total_hits", 0) or 0,
        "total_score": _get("total_score", 0) or 0,
        "avatar_url": _get("avatar_url", None),
        "cover_url": _get("cover_url", None),
        "lang": card_lang,
    }

    base["title"] = None
    base["title_color"] = None
    if not isinstance(user, dict):
        tc = getattr(user, "active_title_code", None)
        if tc:
            td = TITLE_REGISTRY.get(tc)
            if td:
                base["title"] = td.name_for(card_lang.lower())
                base["title_color"] = td.color

    osu_user_id = _get("osu_user_id", 0)
    is_registered = not isinstance(user, dict)
    other_mode = ruleset not in (None, 0)
    mode = rulesets.RULESETS[ruleset] if other_mode else "osu"
    if other_mode:
        base["ruleset"] = ruleset

    if osu_user_id:
        try:
            ext = await (osu_api_client.get_user_extended_data(osu_user_id, mode=mode) if other_mode else osu_api_client.get_user_extended_data(osu_user_id))
        except Exception:
            ext = None
        if ext and other_mode:
            base["pp"] = ext.get("pp") or 0
            base["global_rank"] = ext.get("global_rank") or 0
            base["accuracy"] = ext.get("accuracy") or 0.0
            base["play_count"] = ext.get("play_count") or 0
            base["play_time"] = format_play_time(ext.get("play_time") or 0, card_lang)
            base["ranked_score"] = ext.get("ranked_score") or 0
            base["total_hits"] = ext.get("total_hits") or 0
        if ext:
            base["rank_history"] = ext.get("rank_history", [])
            base["monthly_playcounts"] = ext.get("monthly_playcounts", [])
            base["level"] = ext.get("level", 0)
            base["level_progress"] = ext.get("level_progress", 0)
            base["country_rank"] = ext.get("country_rank") or 0
            if ext.get("total_score"):
                base["total_score"] = ext["total_score"]
            base["country_name"] = ext.get("country_name")
            base["team"] = ext.get("team")
            base["maximum_combo"] = ext.get("maximum_combo", 0)
            base["replays_watched"] = ext.get("replays_watched", 0)
            base["grade_counts"] = ext.get("grade_counts", {})
            base["total_maps"] = ext.get("total_maps", 0)
            base["is_online"] = ext.get("is_online", False)
            base["is_supporter"] = ext.get("is_supporter", False)
            base["support_level"] = ext.get("support_level", 0)
            base["join_date"] = ext.get("join_date")
            base["last_visit"] = ext.get("last_visit")
            base["avatar_url"] = ext.get("avatar_url") or base["avatar_url"]
            base["cover_url"] = ext.get("cover_url") or base["cover_url"]

        try:
            from services.render_farm.billing import palette_of, worn_mark
            base["dossier_mark"] = await worn_mark(_get("player_id", None), osu_user_id or None)
            base["palette"] = await palette_of(_get("player_id", None), osu_user_id or None)
            if base.get("title"):
                from services.image.tint import title_colour
                title_palette = await palette_of(_get("player_id", None), osu_user_id or None, "tint_titles")
                tinted = title_colour(title_palette)
                if tinted:
                    base["title_color"] = tinted
                    base["title_palette"] = title_palette
        except Exception:
            logger.exception("profile: the Dossier mark was not read")
            base["dossier_mark"] = None
            base["palette"] = None
        best_pp = None
        if is_registered and not other_mode:
            from sqlalchemy import func
            stmt = (
                select(func.max(UserBestScore.pp))
                .where(UserBestScore.player_id == user.player_id)
            )
            result = await session.execute(stmt)
            best_pp = result.scalar()
        if not best_pp:
            try:
                top1 = await osu_api_client.get_user_best_scores(osu_user_id, limit=1, mode=mode)
                if top1 and isinstance(top1, list) and top1[0].get("pp"):
                    best_pp = top1[0]["pp"]
            except Exception:
                pass
        base["best_pp"] = best_pp or 0

        if is_registered and not other_mode:
            stmt = (
                select(UserBestScore)
                .where(UserBestScore.player_id == user.player_id)
                .order_by(UserBestScore.pp.desc())
                .limit(5)
            )
            result = await session.execute(stmt)
            scores = result.scalars().all()

            for s in scores:
                if (not s.beatmapset_id or not s.creator) and s.beatmap_id:
                    bm = await osu_api_client.get_beatmap(s.beatmap_id)
                    if bm:
                        if not s.beatmapset_id and bm.get("beatmapset_id"):
                            s.beatmapset_id = bm["beatmapset_id"]
                        beatmapset = bm.get("beatmapset") or {}
                        if not s.creator and beatmapset.get("creator"):
                            s.creator = beatmapset["creator"]
            if scores:
                await session.commit()

            base["top_scores"] = [
                {
                    "rank": s.rank or "F",
                    "artist": s.artist or "",
                    "title": s.title or "",
                    "version": s.version or "",
                    "pp": s.pp or 0,
                    "accuracy": s.accuracy or 0,
                    "max_combo": s.max_combo or 0,
                    "mods": s.mods or "",
                    "beatmap_id": s.beatmap_id or 0,
                    "beatmapset_id": s.beatmapset_id or 0,
                    "creator": s.creator or "",
                }
                for s in scores
            ]
        else:
            try:
                api_scores = await osu_api_client.get_user_best_scores(osu_user_id, limit=5, mode=mode)
            except Exception:
                api_scores = []
            base["top_scores"] = [
                {
                    "rank": s.get("rank", "F"),
                    "artist": (s.get("beatmapset") or {}).get("artist", ""),
                    "title": (s.get("beatmapset") or {}).get("title", ""),
                    "version": (s.get("beatmap") or {}).get("version", ""),
                    "pp": s.get("pp") or 0,
                    "accuracy": (s.get("accuracy") or 0) * 100,
                    "max_combo": s.get("max_combo") or 0,
                    "mods": ",".join(
                        m.get("acronym", "") if isinstance(m, dict) else str(m)
                        for m in (s.get("mods") or [])
                    ),
                    "beatmap_id": (s.get("beatmap") or {}).get("id", 0),
                    "beatmapset_id": (s.get("beatmapset") or {}).get("id", 0),
                    "creator": (s.get("beatmapset") or {}).get("creator", ""),
                }
                for s in (api_scores or [])
            ]

    return base
