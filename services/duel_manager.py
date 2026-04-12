"""
Simplified duel manager.

Flow: challenge → accept → bot suggests map → players play on their own →
duelresult checks recent scores → round result → repeat until best-of-N.
No Referee Hub / SignalR dependency.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.user import User
from db.models.best_score import UserBestScore
from utils.logger import get_logger

logger = get_logger("services.duel_manager")

# How long a duel can be active before auto-cleanup (seconds)
DUEL_TIMEOUT = 3600
# How long players have to play a round (seconds)
ROUND_TIMEOUT = 900  # 15 minutes


@dataclass
class DuelState:
    """In-memory state for an active duel."""
    duel_id: int
    player1_osu_id: int
    player2_osu_id: int
    player1_user_id: int
    player2_user_id: int
    best_of: int
    player1_wins: int = 0
    player2_wins: int = 0
    current_round: int = 0
    current_beatmap_id: Optional[int] = None
    round_picked_at: Optional[datetime] = None  # UTC timestamp when map was picked
    mappool: List[int] = field(default_factory=list)
    mappool_info: Dict[int, Dict] = field(default_factory=dict)
    played_maps: List[int] = field(default_factory=list)
    tg_chat_id: int = 0
    player1_name: str = ""
    player2_name: str = ""

    @property
    def wins_needed(self) -> int:
        return self.best_of // 2 + 1

    @property
    def is_finished(self) -> bool:
        return self.player1_wins >= self.wins_needed or self.player2_wins >= self.wins_needed


# Callback type for sending timeout notifications to Telegram
TelegramCallback = Callable[..., Coroutine[Any, Any, None]]


class DuelManager:
    """Orchestrates duel lifecycle without Referee Hub."""

    def __init__(self, osu_api, session_factory):
        self._osu_api = osu_api
        self._session_factory = session_factory
        self._by_duel: Dict[int, DuelState] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._tg_callback: Optional[TelegramCallback] = None

    def set_telegram_callback(self, callback: TelegramCallback):
        """Set callback for sending timeout/forfeit messages to TG."""
        self._tg_callback = callback

    async def start(self):
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("DuelManager started (simplified mode)")

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # ── Public API ───────────────────────────────────────────

    async def create_duel(
        self,
        player1_tg_id: int,
        player2_tg_id: int,
        best_of: int,
        chat_id: int,
    ) -> Optional[Duel]:
        """Create a new pending duel."""
        async with self._session_factory() as session:
            p1 = await self._get_user_by_tg(session, player1_tg_id)
            p2 = await self._get_user_by_tg(session, player2_tg_id)
            if not p1 or not p2 or not p1.osu_user_id or not p2.osu_user_id:
                return None

            # Check no active duel for either player
            for state in self._by_duel.values():
                if state.player1_osu_id in (p1.osu_user_id, p2.osu_user_id) or \
                   state.player2_osu_id in (p1.osu_user_id, p2.osu_user_id):
                    return None

            duel = Duel(
                player1_user_id=p1.id,
                player2_user_id=p2.id,
                best_of=best_of,
                status="pending",
                chat_id=chat_id,
            )
            session.add(duel)
            await session.commit()
            await session.refresh(duel)
            return duel

    async def accept_duel(self, duel_id: int) -> Optional[DuelState]:
        """Accept a pending duel: build mappool, create in-memory state."""
        async with self._session_factory() as session:
            duel = await session.get(Duel, duel_id)
            if not duel or duel.status != "pending":
                return None

            p1 = await session.get(User, duel.player1_user_id)
            p2 = await session.get(User, duel.player2_user_id)
            if not p1 or not p2:
                return None

            mappool, mappool_info = await self._build_mappool(session, p1, p2)
            if not mappool:
                duel.status = "cancelled"
                await session.commit()
                return None

            duel.status = "playing"
            await session.commit()

            state = DuelState(
                duel_id=duel_id,
                player1_osu_id=p1.osu_user_id,
                player2_osu_id=p2.osu_user_id,
                player1_user_id=p1.id,
                player2_user_id=p2.id,
                best_of=duel.best_of,
                mappool=mappool,
                mappool_info=mappool_info,
                tg_chat_id=duel.chat_id or 0,
                player1_name=p1.osu_username,
                player2_name=p2.osu_username,
            )
            self._by_duel[duel_id] = state
            return state

    def suggest_maps(self, duel_id: int, count: int = 5) -> List[Dict]:
        """Return map suggestions from the mappool (unplayed maps).

        Uses spread-by-difficulty: sorts available maps by star_rating,
        splits into `count` buckets, picks one from each for variety.
        """
        state = self._by_duel.get(duel_id)
        if not state:
            return []

        available = [m for m in state.mappool if m not in state.played_maps]
        if not available:
            available = list(state.mappool)

        if len(available) <= count:
            selected = available
        else:
            # Sort by star rating for spread selection
            sorted_maps = sorted(
                available,
                key=lambda bid: state.mappool_info.get(bid, {}).get("star_rating", 0.0),
            )
            # Pick one from each evenly-spaced bucket
            selected = []
            bucket_size = len(sorted_maps) / count
            for i in range(count):
                bucket_start = int(i * bucket_size)
                bucket_end = int((i + 1) * bucket_size)
                bucket = sorted_maps[bucket_start:bucket_end]
                if bucket:
                    selected.append(random.choice(bucket))

        return [
            {
                "beatmap_id": bid,
                "title": state.mappool_info.get(bid, {}).get("title", "Unknown"),
                "star_rating": state.mappool_info.get(bid, {}).get("star_rating", 0.0),
            }
            for bid in selected
        ]

    def pick_beatmap(self, duel_id: int, beatmap_id: int) -> bool:
        """Set the current beatmap for the round. Records timestamp for anti-cheat."""
        state = self._by_duel.get(duel_id)
        if not state:
            return False
        state.current_beatmap_id = beatmap_id
        state.round_picked_at = datetime.now(timezone.utc)
        state.played_maps.append(beatmap_id)
        state.current_round += 1
        return True

    async def check_results(self, duel_id: int, force_timeout: bool = False) -> Optional[Dict]:
        """Check recent scores of both players for the current beatmap.

        Anti-cheat protections:
        1. Only scores with created_at > round_picked_at are accepted
        2. Takes the FIRST (earliest) valid score, not best — no retry abuse
        3. Requires BOTH players to have played (unless force_timeout)
        4. Round timeout handled by cleanup loop via force_timeout=True

        Returns dict with round result data, or None if not enough scores yet.
        """
        state = self._by_duel.get(duel_id)
        if not state or not state.current_beatmap_id:
            return None

        beatmap_id = state.current_beatmap_id
        picked_at = state.round_picked_at

        # Fetch recent scores for both players (last 50)
        p1_scores = await self._osu_api.get_user_recent_scores(state.player1_osu_id, limit=50)
        p2_scores = await self._osu_api.get_user_recent_scores(state.player2_osu_id, limit=50)

        # Anti-cheat: filter by timestamp + take FIRST valid score
        p1_match = self._find_valid_score(p1_scores, beatmap_id, picked_at)
        p2_match = self._find_valid_score(p2_scores, beatmap_id, picked_at)

        # Protection 3: both must have played (unless timeout forced)
        if not force_timeout:
            if not p1_match or not p2_match:
                # Return status info so handler can tell user who's missing
                return {
                    "waiting": True,
                    "p1_played": p1_match is not None,
                    "p2_played": p2_match is not None,
                    "player1_name": state.player1_name,
                    "player2_name": state.player2_name,
                }

        # Extract scores (0 if player didn't play — forfeit on timeout)
        p1_total = p1_match.get("score", 0) if p1_match else 0
        p1_acc = p1_match.get("accuracy", 0.0) if p1_match else 0.0
        p1_combo = p1_match.get("max_combo", 0) if p1_match else 0
        p2_total = p2_match.get("score", 0) if p2_match else 0
        p2_acc = p2_match.get("accuracy", 0.0) if p2_match else 0.0
        p2_combo = p2_match.get("max_combo", 0) if p2_match else 0

        # Normalize accuracy (API returns 0-1, we want 0-100)
        if p1_acc <= 1.0:
            p1_acc *= 100
        if p2_acc <= 1.0:
            p2_acc *= 100

        # Determine winner by total score (forfeit = 0)
        round_winner = 0
        if p1_total > p2_total:
            round_winner = 1
            state.player1_wins += 1
        elif p2_total > p1_total:
            round_winner = 2
            state.player2_wins += 1
        # If both 0 (both forfeited on timeout), draw — no wins awarded

        # Get beatmap info
        info = state.mappool_info.get(beatmap_id, {})
        beatmap_title = info.get("title", "Unknown")
        star_rating = info.get("star_rating", 0.0)

        # Custom beatmap — try to extract info from score data
        if beatmap_id not in state.mappool_info:
            for s in (p1_match, p2_match):
                if not s:
                    continue
                if s.get("beatmapset"):
                    bs = s["beatmapset"]
                    beatmap_title = f"{bs.get('artist', '')} - {bs.get('title', '')}"
                if s.get("beatmap"):
                    star_rating = s["beatmap"].get("difficulty_rating", star_rating)
                if beatmap_title != "Unknown":
                    break

        # Save round to DB
        winner_user_id = None
        if round_winner == 1:
            winner_user_id = state.player1_user_id
        elif round_winner == 2:
            winner_user_id = state.player2_user_id

        async with self._session_factory() as session:
            duel = await session.get(Duel, state.duel_id)
            if duel:
                duel.player1_rounds_won = state.player1_wins
                duel.player2_rounds_won = state.player2_wins

            duel_round = DuelRound(
                duel_id=state.duel_id,
                round_number=state.current_round,
                beatmap_id=beatmap_id,
                beatmap_title=beatmap_title,
                star_rating=star_rating,
                player1_score=p1_total,
                player1_accuracy=p1_acc,
                player1_combo=p1_combo,
                player2_score=p2_total,
                player2_accuracy=p2_acc,
                player2_combo=p2_combo,
                winner_user_id=winner_user_id,
                completed_at=datetime.now(timezone.utc),
            )
            session.add(duel_round)
            await session.commit()

        # Clear current beatmap so next round can be picked
        state.current_beatmap_id = None
        state.round_picked_at = None

        result = {
            "duel_id": state.duel_id,
            "chat_id": state.tg_chat_id,
            "round_number": state.current_round,
            "round_winner": round_winner,
            "player1_name": state.player1_name,
            "player2_name": state.player2_name,
            "player1_score": p1_total,
            "player1_accuracy": p1_acc,
            "player1_combo": p1_combo,
            "player2_score": p2_total,
            "player2_accuracy": p2_acc,
            "player2_combo": p2_combo,
            "player1_wins": state.player1_wins,
            "player2_wins": state.player2_wins,
            "best_of": state.best_of,
            "beatmap_title": beatmap_title,
            "star_rating": star_rating,
            "finished": state.is_finished,
            "p1_played": p1_match is not None,
            "p2_played": p2_match is not None,
            "forced_timeout": force_timeout and (not p1_match or not p2_match),
        }

        if state.is_finished:
            await self._finalize_duel(state)

        return result

    async def cancel_duel(self, duel_id: int, reason: str = "cancelled") -> bool:
        """Cancel a duel."""
        state = self._by_duel.pop(duel_id, None)

        async with self._session_factory() as session:
            duel = await session.get(Duel, duel_id)
            if duel and duel.status not in ("completed", "cancelled"):
                duel.status = "cancelled"
                duel.completed_at = datetime.now(timezone.utc)
                await session.commit()
                return True
        return state is not None

    def get_active_state(self, duel_id: int) -> Optional[DuelState]:
        return self._by_duel.get(duel_id)

    def find_user_duel(self, user_id: int) -> Optional[DuelState]:
        """Find active duel state for a user (by DB user.id)."""
        for state in self._by_duel.values():
            if state.player1_user_id == user_id or state.player2_user_id == user_id:
                return state
        return None

    # ── Finalization ─────────────────────────────────────────

    async def _finalize_duel(self, state: DuelState):
        """Update DB stats, mark duel completed."""
        winner_user_id = None
        if state.player1_wins > state.player2_wins:
            winner_user_id = state.player1_user_id
        elif state.player2_wins > state.player1_wins:
            winner_user_id = state.player2_user_id

        async with self._session_factory() as session:
            duel = await session.get(Duel, state.duel_id)
            if duel:
                duel.status = "completed"
                duel.winner_user_id = winner_user_id
                duel.completed_at = datetime.now(timezone.utc)

            if winner_user_id:
                winner = await session.get(User, winner_user_id)
                loser_id = state.player2_user_id if winner_user_id == state.player1_user_id else state.player1_user_id
                loser = await session.get(User, loser_id)
                if winner:
                    winner.duel_wins = (winner.duel_wins or 0) + 1
                if loser:
                    loser.duel_losses = (loser.duel_losses or 0) + 1

            await session.commit()

        self._by_duel.pop(state.duel_id, None)

    async def get_duel_rounds(self, duel_id: int) -> List[Dict]:
        """Get round data for the final card."""
        async with self._session_factory() as session:
            stmt = select(DuelRound).where(DuelRound.duel_id == duel_id).order_by(DuelRound.round_number)
            result = await session.execute(stmt)
            rounds = result.scalars().all()

            state = self._by_duel.get(duel_id)
            round_data = []
            for r in rounds:
                rw_name = "—"
                if state:
                    if r.winner_user_id == state.player1_user_id:
                        rw_name = state.player1_name
                    elif r.winner_user_id == state.player2_user_id:
                        rw_name = state.player2_name
                # Determine which player won this round
                winner_player = 0
                if state:
                    if r.winner_user_id == state.player1_user_id:
                        winner_player = 1
                    elif r.winner_user_id == state.player2_user_id:
                        winner_player = 2
                round_data.append({
                    "round_number": r.round_number,
                    "beatmap_title": r.beatmap_title or "Unknown",
                    "star_rating": r.star_rating or 0.0,
                    "winner_name": rw_name,
                    "winner_player": winner_player,
                    "player1_score": r.player1_score or 0,
                    "player2_score": r.player2_score or 0,
                })
            return round_data

    # ── Mappool ──────────────────────────────────────────────

    async def _build_mappool(
        self, session: AsyncSession, p1: User, p2: User
    ) -> tuple[List[int], Dict[int, Dict]]:
        """Build mappool from both players' best scores."""
        avg_pp = ((p1.player_pp or 0) + (p2.player_pp or 0)) / 2.0

        if avg_pp < 1000:
            star_min, star_max = 3.0, 4.5
        elif avg_pp < 3000:
            star_min, star_max = 4.0, 5.5
        elif avg_pp < 6000:
            star_min, star_max = 5.0, 6.5
        elif avg_pp < 10000:
            star_min, star_max = 5.5, 7.0
        else:
            star_min, star_max = 6.0, 8.0

        stmt = (
            select(
                UserBestScore.beatmap_id,
                UserBestScore.title,
                UserBestScore.star_rating,
            )
            .where(
                UserBestScore.user_id.in_([p1.id, p2.id]),
                UserBestScore.star_rating >= star_min,
                UserBestScore.star_rating <= star_max,
                UserBestScore.star_rating.isnot(None),
            )
            .group_by(UserBestScore.beatmap_id)
            .order_by(func.random())
            .limit(15)
        )
        result = await session.execute(stmt)
        rows = result.all()

        if len(rows) < 5:
            stmt2 = (
                select(
                    UserBestScore.beatmap_id,
                    UserBestScore.title,
                    UserBestScore.star_rating,
                )
                .where(
                    UserBestScore.user_id.in_([p1.id, p2.id]),
                    UserBestScore.star_rating.isnot(None),
                )
                .group_by(UserBestScore.beatmap_id)
                .order_by(func.random())
                .limit(15)
            )
            result2 = await session.execute(stmt2)
            rows = result2.all()

        mappool = []
        mappool_info = {}
        for row in rows:
            bid = row[0]
            mappool.append(bid)
            mappool_info[bid] = {
                "title": row[1] or "Unknown",
                "star_rating": row[2] or 0.0,
            }

        return mappool, mappool_info

    # ── Score matching (anti-cheat) ────────────────────────────

    @staticmethod
    def _find_valid_score(
        scores: List[Dict], beatmap_id: int, picked_at: Optional[datetime]
    ) -> Optional[Dict]:
        """Find the FIRST valid score on a beatmap set AFTER the round was picked.

        Anti-cheat:
        - Rejects scores with created_at before round_picked_at (old scores)
        - Returns the earliest valid score (first attempt, no retry advantage)
        """
        matches = []
        for s in scores:
            bm = s.get("beatmap", {})
            if bm.get("id") != beatmap_id:
                continue

            # Timestamp check: reject scores set before the map was picked
            if picked_at:
                score_time_str = s.get("created_at") or s.get("ended_at")
                if score_time_str:
                    try:
                        # osu! API returns ISO 8601 format
                        score_time = datetime.fromisoformat(
                            score_time_str.replace("Z", "+00:00")
                        )
                        if score_time < picked_at:
                            continue  # old score — skip
                    except (ValueError, TypeError):
                        pass  # if parsing fails, let it through

            matches.append(s)

        if not matches:
            return None

        # Return the EARLIEST valid score (first attempt — no retry abuse)
        def _score_time(s):
            t = s.get("created_at") or s.get("ended_at") or ""
            try:
                return datetime.fromisoformat(t.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return datetime.max.replace(tzinfo=timezone.utc)

        return min(matches, key=_score_time)

    # ── Helpers ──────────────────────────────────────────────

    async def _get_user_by_tg(self, session: AsyncSession, tg_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_id == tg_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Cleanup ──────────────────────────────────────────────

    async def _cleanup_loop(self):
        """Periodically check for round timeouts and stale duels."""
        while True:
            try:
                await asyncio.sleep(60)  # check every minute
                now = datetime.now(timezone.utc)

                stale_ids = []
                round_timeout_ids = []

                for duel_id, state in list(self._by_duel.items()):
                    # Check round timeout (15 min since map was picked)
                    if state.round_picked_at and state.current_beatmap_id:
                        elapsed = (now - state.round_picked_at).total_seconds()
                        if elapsed > ROUND_TIMEOUT:
                            round_timeout_ids.append(duel_id)
                            continue

                    # Check overall duel timeout (1 hour)
                    async with self._session_factory() as session:
                        duel = await session.get(Duel, duel_id)
                        if not duel:
                            stale_ids.append(duel_id)
                            continue
                        age = (now - duel.created_at).total_seconds() if duel.created_at else 0
                        if age > DUEL_TIMEOUT:
                            stale_ids.append(duel_id)

                # Handle round timeouts — force-resolve with whoever played
                for duel_id in round_timeout_ids:
                    state = self._by_duel.get(duel_id)
                    if not state:
                        continue
                    logger.info(f"Round timeout for duel {duel_id}, forcing result")
                    result = await self.check_results(duel_id, force_timeout=True)
                    if result and self._tg_callback:
                        await self._tg_callback("round_timeout", result)

                # Handle stale duels
                for duel_id in stale_ids:
                    state = self._by_duel.get(duel_id)
                    await self.cancel_duel(duel_id, reason="timeout")
                    if state and self._tg_callback:
                        await self._tg_callback("duel_timeout", {
                            "duel_id": duel_id,
                            "chat_id": state.tg_chat_id,
                        })
                    logger.info(f"Cleaned up stale duel {duel_id}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}", exc_info=True)
