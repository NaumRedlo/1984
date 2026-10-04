import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from weakref import WeakValueDictionary

from sqlalchemy import select

MATCH_WITHIN = timedelta(seconds=10)
_locks = WeakValueDictionary()


def is_linked(user) -> bool:
    from services.render_farm import invites

    return user.player_id in invites.linked_players() or user.telegram_id in invites.linked()


@asynccontextmanager
async def serial(osu_user_id: int):
    gate = _locks.get(osu_user_id)
    if gate is None:
        gate = asyncio.Lock()
        _locks[osu_user_id] = gate
    async with gate:
        yield


async def unseen(session, player_id: int, scores: list, *, stored: bool = False) -> list:
    from types import SimpleNamespace

    from db.models.witnessed_play import WitnessedPlay
    from db.models.map_attempt import UserMapAttempt
    from services.render_farm.witnessed import same_play
    from utils.osu.api_client import _parse_played_at, _pick_stat

    if player_id is None or not scores:
        return scores
    if stored:
        ids = {raw.get("id") for raw in scores if raw.get("id")}
        seen = set((await session.execute(select(UserMapAttempt.score_id).where(
            UserMapAttempt.player_id == player_id, UserMapAttempt.score_id.in_(ids),
        ))).scalars().all()) if ids else set()
        pending = []
        for raw in scores:
            score_id = raw.get("id")
            if score_id and score_id in seen:
                continue
            if score_id:
                seen.add(score_id)
            pending.append(raw)
        scores = pending
    timed = [(raw, _parse_played_at(raw)) for raw in scores]
    dates = [at for _, at in timed if at is not None]
    if not dates:
        return scores
    witnessed = (await session.execute(select(WitnessedPlay).where(
        WitnessedPlay.player_id == player_id,
        WitnessedPlay.played_at >= min(dates) - MATCH_WITHIN,
        WitnessedPlay.played_at <= max(dates) + MATCH_WITHIN,
    ))).scalars().all()
    if not witnessed:
        return scores

    def mods(values):
        return {value for value in values if value and value != "CL"}

    fresh = []
    for raw, at in timed:
        stats = raw.get("statistics") or {}
        played = SimpleNamespace(
            player_id=player_id, beatmap_id=(raw.get("beatmap") or {}).get("id"),
            played_at=at, created_at=None,
            score=raw.get("legacy_total_score") or raw.get("total_score") or raw.get("score"),
            count_100=_pick_stat(stats, "count_100", "ok"), count_50=_pick_stat(stats, "count_50", "meh"),
            count_miss=_pick_stat(stats, "count_miss", "miss"), max_combo=raw.get("max_combo"),
        )
        picked = mods(value.get("acronym") if isinstance(value, dict) else str(value) for value in raw.get("mods") or [])
        if not any(at is not None and abs(at - row.played_at.replace(tzinfo=None)) <= MATCH_WITHIN
                   and played.beatmap_id is not None and played.beatmap_id == row.beatmap_id
                   and same_play(row, played) and picked == mods((row.mods or "").split(","))
                   and (raw.get("passed") is None or bool(raw["passed"]) == bool(row.passed)) for row in witnessed):
            fresh.append(raw)
    return fresh
