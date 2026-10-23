from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from db.models.duel import Duel, DuelEvent, DuelNotification, DuelParticipant, DuelRoom
from db.models.player import Player
from utils.i18n import t
from utils.logger import get_logger

logger = get_logger("duels.notify")

MOST_ATTEMPTS = 5
REASONS = ("refused_before_start", "pool_unavailable", "unresolved_tie", "control_failure")

Send = Callable[[int, str], Awaitable[None]]
Language = Callable[[int], Awaitable[str]]

def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def wording(payload: dict[str, Any], lang: str, other: str, room: Optional[str], score: str, won: Optional[bool]) -> Optional[str]:
    kind = payload.get("kind")
    name = _escape(other)
    if kind == "invitation":
        return t("duels.invitation", lang, name=name)
    if kind == "accepted":
        return t("duels.accepted", lang, name=name)
    if kind in ("declined", "withdrawn"):
        return t(f"duels.{kind}", lang, name=name)
    if kind == "invitation_expired":
        return t("duels.invitation_expired", lang)
    if kind == "room_ready":
        return t("duels.room_ready", lang, room=_escape(room or ""))
    if kind == "finished" and won is not None:
        return t("duels.won" if won else "duels.lost", lang, name=name, score=score)
    if kind in ("cancelled", "refused_before_start"):
        reason = payload.get("reason") or kind
        key = f"duels.reason.{reason}" if reason in REASONS else "duels.reason.other"
        return t("duels.cancelled", lang, name=name, reason=t(key, lang))
    if kind == "range_offered":
        return t("duels.range_offered", lang, name=name)
    return None

async def _gather(session, row: DuelNotification) -> Optional[dict[str, Any]]:
    player = await session.get(Player, row.player_id)
    event = await session.get(DuelEvent, row.event_id)
    if player is None or event is None:
        return None
    duel = await session.get(Duel, event.duel_id)
    people = (await session.execute(
        select(DuelParticipant.player_id, DuelParticipant.side, Player.osu_username)
        .join(Player, Player.id == DuelParticipant.player_id).where(DuelParticipant.duel_id == event.duel_id)
    )).all()
    mine = next((side for player_id, side, _ in people if player_id == row.player_id), None)
    other = next((name for player_id, _, name in people if player_id != row.player_id), "")
    own, theirs = (duel.wins_a, duel.wins_b) if mine == "A" else (duel.wins_b, duel.wins_a)
    room = await session.scalar(select(DuelRoom.room_name).where(DuelRoom.duel_id == duel.id).order_by(DuelRoom.id.desc()))
    won = row.payload.get("winner") == row.player_id if row.payload.get("kind") == "finished" else None
    return {"telegram": player.telegram_id, "other": other, "room": room, "score": f"{own}:{theirs}", "won": won}

async def deliver(factory, send: Send, language: Language, limit: int = 30) -> int:
    async with factory() as session:
        rows = (await session.scalars(
            select(DuelNotification).where(DuelNotification.channel == "telegram", DuelNotification.delivery_state == "pending")
            .order_by(DuelNotification.id).limit(limit)
        )).all()
        jobs = [(row.id, row.payload, await _gather(session, row)) for row in rows]
    sent = 0
    for row_id, payload, facts in jobs:
        state, error = "skipped", None
        if facts is not None and facts["telegram"]:
            lang = (await language(facts["telegram"])).lower()
            text = wording(payload, lang, facts["other"], facts["room"], facts["score"], facts["won"])
            if text is not None:
                try:
                    await send(facts["telegram"], text)
                    state = "sent"
                    sent += 1
                except Exception as exc:
                    state, error = "pending", f"{type(exc).__name__}: {exc}"
        async with factory() as session:
            async with session.begin():
                row = await session.get(DuelNotification, row_id)
                row.attempts += 1
                if state == "pending" and row.attempts >= MOST_ATTEMPTS:
                    state = "failed"
                row.delivery_state = state
        if error:
            logger.warning("duel notification %s was not sent: %s", row_id, error)
    return sent

async def unread(factory, player_id: int, limit: int = 50) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = (await session.execute(
            select(DuelNotification, DuelEvent).join(DuelEvent, DuelEvent.id == DuelNotification.event_id)
            .where(DuelNotification.player_id == player_id, DuelNotification.channel == "app",
                   DuelNotification.delivery_state == "pending").order_by(DuelNotification.id).limit(limit)
        )).all()
        return [{"id": row.id, "duel": event.duel_id, "sequence": event.sequence, "payload": row.payload,
                 "at": event.occurred_at.isoformat() + "Z"} for row, event in rows]

async def seen(factory, player_id: int, ids: list[int]) -> int:
    async with factory() as session:
        async with session.begin():
            rows = (await session.scalars(select(DuelNotification).where(
                DuelNotification.id.in_(ids), DuelNotification.player_id == player_id, DuelNotification.channel == "app"))).all()
            for row in rows:
                row.delivery_state = "sent"
                row.attempts += 1
            return len(rows)
