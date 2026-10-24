import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel import Duel, DuelGlicko, DuelParticipant, DuelRatingChange, DuelRatingPeriod, DuelRatingResult
from services.duels import glicko
from services.duels.rules import PARAMETERS
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("duels.rating")

VERSION = "glicko2-v1"
PERIOD_SECONDS = PARAMETERS["rating_period_seconds"]
EPOCH = datetime(1970, 1, 1)

def period_of(moment: datetime) -> int:
    return int((moment - EPOCH).total_seconds()) // PERIOD_SECONDS

def period_end(period: int) -> datetime:
    return EPOCH + timedelta(seconds=(period + 1) * PERIOD_SECONDS)

async def record(session, duel: Duel, people: list[DuelParticipant], reason: str, now: datetime, version: str = VERSION) -> int:
    if duel.mode != "rated" or duel.winner_id is None:
        return 0
    period = period_of(now)
    made = 0
    for person in people:
        other = next(p for p in people if p.player_id != person.player_id)
        session.add(DuelRatingResult(version=version, duel_id=duel.id, player_id=person.player_id, opponent_id=other.player_id,
                                     result=1.0 if duel.winner_id == person.player_id else 0.0, calibrates=reason == "played",
                                     reason=reason, period=period))
        made += 1
    return made

async def _held(session, player_id: int, version: str) -> DuelGlicko:
    row = await session.scalar(select(DuelGlicko).where(DuelGlicko.player_id == player_id, DuelGlicko.version == version))
    if row is None:
        row = DuelGlicko(player_id=player_id, version=version, rating=glicko.START_RATING, rd=glicko.START_RD,
                         sigma=glicko.START_SIGMA, calibration_count=0)
        session.add(row)
        await session.flush()
    return row

def _as_rating(row: DuelGlicko, period: int) -> glicko.Rating:
    current = glicko.Rating(row.rating, row.rd, row.sigma)
    skipped = period - row.last_period - 1 if row.last_period is not None else 0
    return glicko.idle(current, skipped) if skipped > 0 else current

async def process(factory, period: int, now: Optional[datetime] = None, version: str = VERSION) -> str:
    now = now or utcnow()
    if period_end(period) > now:
        return "open"
    async with factory() as session:
        async with session.begin():
            row = await session.scalar(select(DuelRatingPeriod).where(DuelRatingPeriod.version == version,
                                                                     DuelRatingPeriod.period == period).with_for_update())
            if row is not None and row.state == "done":
                return "done"
            if row is None:
                row = DuelRatingPeriod(version=version, period=period, state="running")
                session.add(row)
                await session.flush()
            results = list((await session.scalars(
                select(DuelRatingResult).where(DuelRatingResult.version == version, DuelRatingResult.period == period,
                                               DuelRatingResult.processed_at.is_(None))
                .order_by(DuelRatingResult.player_id, DuelRatingResult.duel_id)
            )).all())
            players = sorted({result.player_id for result in results} | {result.opponent_id for result in results})
            rows = {player: await _held(session, player, version) for player in players}
            start = {player: _as_rating(rows[player], period) for player in players}
            digest = hashlib.sha256(json.dumps([[r.duel_id, r.player_id, r.opponent_id, r.result] for r in results],
                                               separators=(",", ":")).encode()).hexdigest()
            for player in sorted({result.player_id for result in results}):
                games = [glicko.Game(start[result.opponent_id], result.result) for result in results if result.player_id == player]
                after = glicko.update(start[player], games)
                held = rows[player]
                session.add(DuelRatingChange(version=version, period=period, player_id=player,
                                             before={"rating": held.rating, "rd": held.rd, "sigma": held.sigma},
                                             after={"rating": after.rating, "rd": after.rd, "sigma": after.sigma}, input_hash=digest))
                held.rating, held.rd, held.sigma = after.rating, after.rd, after.sigma
                held.last_period = period
                held.calibration_count += sum(1 for result in results if result.player_id == player and result.calibrates)
                held.updated_at = now
            for result in results:
                result.processed_at = now
            row.state = "done"
            row.input_hash = digest
            row.completed_at = now
            return "done"

async def sweep(factory, now: Optional[datetime] = None, version: str = VERSION) -> int:
    now = now or utcnow()
    async with factory() as session:
        waiting = sorted(set((await session.scalars(
            select(DuelRatingResult.period).where(DuelRatingResult.version == version, DuelRatingResult.processed_at.is_(None))
        )).all()))
    done = 0
    for period in waiting:
        if period_end(period) > now:
            break
        try:
            if await process(factory, period, now, version) == "done":
                done += 1
        except glicko.RatingFailure as failure:
            logger.error("rating period %s was not applied: %s", period, failure)
            break
    return done

async def projection(session, duel: Duel, viewer_id: Optional[int], version: str = VERSION) -> Optional[dict[str, int]]:
    if duel.mode != "rated" or duel.winner_id is None or viewer_id is None:
        return None
    people = (await session.scalars(select(DuelParticipant).where(DuelParticipant.duel_id == duel.id))).all()
    if viewer_id not in {person.player_id for person in people} or len(people) != 2:
        return None
    other = next(person.player_id for person in people if person.player_id != viewer_id)
    period = period_of(duel.finalized_at or utcnow())

    async def held(player_id: int) -> glicko.Rating:
        row = await session.scalar(select(DuelGlicko).where(DuelGlicko.player_id == player_id, DuelGlicko.version == version))
        return _as_rating(row, period) if row is not None else glicko.Rating()

    before = await held(viewer_id)
    after = glicko.update(before, [glicko.Game(await held(other), 1.0 if duel.winner_id == viewer_id else 0.0)])
    return {"before": round(before.rating), "after": round(after.rating)}

async def meeting(session, duel: Duel, version: str = VERSION) -> dict[str, Any]:
    people = (await session.scalars(select(DuelParticipant).where(DuelParticipant.duel_id == duel.id))).all()
    ids = [person.player_id for person in people]
    done = (await session.execute(
        select(Duel.id, Duel.winner_id, DuelParticipant.player_id).join(DuelParticipant, DuelParticipant.duel_id == Duel.id)
        .where(DuelParticipant.player_id.in_(ids or [0]), Duel.state == "COMPLETED", Duel.winner_id.is_not(None), Duel.id != duel.id)
    )).all()
    period = period_of(utcnow())
    found: dict[str, Any] = {"players": {}, "between": {}}
    for player in ids:
        row = await session.scalar(select(DuelGlicko).where(DuelGlicko.player_id == player, DuelGlicko.version == version))
        shown = _as_rating(row, period) if row is not None else None
        calibrating = row is None or row.calibration_count < PARAMETERS["calibration_series"]
        mine = {duel_id: winner for duel_id, winner, who in done if who == player}
        found["players"][str(player)] = {
            "rating": None if calibrating or shown is None else round(shown.rating), "rd": round(shown.rd) if shown is not None else None,
            "won": sum(1 for winner in mine.values() if winner == player), "lost": sum(1 for winner in mine.values() if winner != player),
        }
    if len(ids) == 2:
        first = {duel_id: winner for duel_id, winner, who in done if who == ids[0]}
        shared = {duel_id: winner for duel_id, winner, who in done if who == ids[1] and duel_id in first}
        found["between"] = {str(player): sum(1 for winner in shared.values() if winner == player) for player in ids}
    return found

async def standing(factory, player_id: int, now: Optional[datetime] = None, version: str = VERSION) -> dict[str, Any]:
    now = now or utcnow()
    async with factory() as session:
        row = await session.scalar(select(DuelGlicko).where(DuelGlicko.player_id == player_id, DuelGlicko.version == version))
    if row is None:
        return {"calibrating": True, "played": 0, "needed": PARAMETERS["calibration_series"], "rating": None, "rd": None,
                "next_update": period_end(period_of(now)).isoformat() + "Z"}
    shown = _as_rating(row, period_of(now))
    calibrating = row.calibration_count < PARAMETERS["calibration_series"]
    return {"calibrating": calibrating, "played": row.calibration_count, "needed": PARAMETERS["calibration_series"],
            "rating": None if calibrating else round(shown.rating), "rd": round(shown.rd), "provisional": round(shown.rating),
            "next_update": period_end(period_of(now)).isoformat() + "Z"}
