"""Custom skins registry.

Installed skin files live on the worker (danser's Skins dir); the bot keeps just
the list of names here so the /settings picker works even when the on-demand GPU
is asleep. Each entry also records the uploader's tg_id ("owner") so only they
can rename/delete it later — entries from before ownership tracking existed (or
a malformed row) have owner=None and are treated as un-manageable, select-only.
"""

import json
from typing import Optional

from sqlalchemy import select, update

from db.database import get_db_session
from db.models.render_settings import UserRenderSettings
from db.models.bot_settings import BotSettings
from utils.osu import render_client


_SKINS_KEY = "render_skins"


async def get_render_skins() -> list:
    """[{'name': str, 'owner': Optional[int]}, ...] uploaded skins (not 'default')."""
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == _SKINS_KEY)
        )).scalar_one_or_none()
        if not (row and row.value):
            return []
        try:
            raw = json.loads(row.value)
        except Exception:
            return []
        out = []
        for entry in raw:
            if isinstance(entry, str):
                out.append({"name": entry, "owner": None})  # legacy, uploader unknown
            elif isinstance(entry, dict) and entry.get("name"):
                out.append({"name": entry["name"], "owner": entry.get("owner")})
        return out


async def get_my_render_skins(tg_id: int) -> list:
    """Skins uploaded by this tg_id — the only ones they may rename/delete."""
    return [e for e in await get_render_skins() if e.get("owner") == tg_id]


async def _save_render_skins(entries: list) -> None:
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == _SKINS_KEY)
        )).scalar_one_or_none()
        value = json.dumps(entries)
        if row:
            row.value = value
        else:
            session.add(BotSettings(key=_SKINS_KEY, value=value))
        await session.commit()


async def _add_render_skin(name: str, owner_tg_id: Optional[int] = None) -> None:
    entries = await get_render_skins()
    for e in entries:
        if e["name"] == name:
            if e.get("owner") is None and owner_tg_id is not None:
                e["owner"] = owner_tg_id  # claim a previously-unowned re-upload
            break
    else:
        entries.append({"name": name, "owner": owner_tg_id})
    await _save_render_skins(entries)


async def _remove_render_skin(name: str) -> None:
    entries = [e for e in await get_render_skins() if e["name"] != name]
    await _save_render_skins(entries)


async def _rename_render_skin_entry(name: str, new_name: str) -> None:
    entries = await get_render_skins()
    for e in entries:
        if e["name"] == name:
            e["name"] = new_name
    await _save_render_skins(entries)


async def _reassign_users_off_skin(old_name: str, new_name: str = "default") -> None:
    """Point any player's UserRenderSettings.skin away from a skin that just got
    renamed or deleted, so their next render doesn't reference a missing/stale
    folder name on the worker."""
    async with get_db_session() as session:
        await session.execute(
            update(UserRenderSettings)
            .where(UserRenderSettings.skin == old_name)
            .values(skin=new_name)
        )
        await session.commit()


async def do_delete_skin(name: str) -> None:
    """Delete the skin folder on the remote worker, then clean up bot-side
    records (drop from the list, fall any current users of it back to 'default').
    Raises render_client.RenderWorkerUnreachable / danser_renderer.DanserError."""
    await render_client.delete_skin_remote(name)
    await _remove_render_skin(name)
    await _reassign_users_off_skin(name, "default")


async def do_rename_skin(name: str, new_name: str) -> str:
    """Rename the skin folder on the remote worker, then update bot-side
    records (the list entry, and anyone currently using it). Returns the
    sanitized name actually used.
    Raises render_client.RenderWorkerUnreachable / DanserError."""
    final_name = await render_client.rename_skin_remote(name, new_name)
    await _rename_render_skin_entry(name, final_name)
    await _reassign_users_off_skin(name, final_name)
    return final_name
