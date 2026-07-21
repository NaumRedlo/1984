"""Per-user render library ("Мои рендеры" in /settings).

Each finished render stores its Telegram file_id + a metadata snapshot, deduped
per (user, score). Re-sending from here costs nothing (file_id), so the only
bound is _MAX_USER_RENDERS — oldest are pruned.
"""

import json

from sqlalchemy import select, delete

from db.database import get_db_session
from db.models.user_render import UserRender
from utils.timeutils import utcnow


_MAX_USER_RENDERS = 50


def _meta_from_ctx(ctx: dict) -> dict:
    """Snapshot the score details from a recent-card context for the library.
    beatmapset_id + length are kept so a stale entry can be re-rendered."""
    return {
        "artist": ctx.get("artist"),
        "title": ctx.get("title"),
        "version": ctx.get("version"),
        "mods": ctx.get("mods"),
        "rank": ctx.get("rank_grade"),
        "pp": ctx.get("pp"),
        "acc": ctx.get("accuracy"),
        "stars": ctx.get("star_rating"),
        "combo": ctx.get("combo"),
        "misses": ctx.get("misses"),
        "player": ctx.get("username"),
        "beatmapset_id": ctx.get("beatmapset_id"),
        "length": ctx.get("total_length"),
    }


def _render_label(meta: dict) -> str:
    """Short one-line label for the library list ('Artist - Title')."""
    if not meta:
        return ""
    artist = (meta.get("artist") or "").strip()
    title = (meta.get("title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or (meta.get("label") or "")


async def store_user_render(user_id, ref: str, file_id: str, label: str, meta: dict) -> None:
    if not user_id or not file_id:
        return
    async with get_db_session() as session:
        existing = (await session.execute(
            select(UserRender).where(UserRender.user_id == user_id, UserRender.ref == ref)
        )).scalar_one_or_none()
        payload = json.dumps(meta, ensure_ascii=False)
        if existing:
            existing.file_id = file_id
            existing.label = (label or "")[:255]
            existing.meta = payload
            existing.created_at = utcnow()
        else:
            session.add(UserRender(
                user_id=user_id, ref=ref, file_id=file_id,
                label=(label or "")[:255], meta=payload,
            ))
        await session.commit()
        # Prune anything past the newest _MAX_USER_RENDERS.
        ids = (await session.execute(
            select(UserRender.id).where(UserRender.user_id == user_id)
            .order_by(UserRender.created_at.desc())
        )).scalars().all()
        if len(ids) > _MAX_USER_RENDERS:
            await session.execute(delete(UserRender).where(UserRender.id.in_(ids[_MAX_USER_RENDERS:])))
            await session.commit()


async def get_user_renders(user_id) -> list:
    async with get_db_session() as session:
        return list((await session.execute(
            select(UserRender).where(UserRender.user_id == user_id)
            .order_by(UserRender.created_at.desc())
        )).scalars().all())


async def get_user_render(user_id, render_id):
    async with get_db_session() as session:
        return (await session.execute(
            select(UserRender).where(UserRender.id == render_id, UserRender.user_id == user_id)
        )).scalar_one_or_none()


async def delete_user_render(user_id, render_id) -> None:
    async with get_db_session() as session:
        await session.execute(delete(UserRender).where(
            UserRender.id == render_id, UserRender.user_id == user_id))
        await session.commit()
