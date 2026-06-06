"""Duel round engine — runs an accepted duel to completion over osu! IRC.

A duel is best-of-N (casual Bo5 → first to 3, ranked Bo10 → first to 6). Each
player has their OWN 6-map pool (stored as "p1ids;p2ids" in
``pool_beatmap_ids``).  Every round the active player — weaker serves first,
then alternating — interactively picks a map from their remaining pool via
:mod:`services.duel.pick_phase` (DM buttons, 2-min timer, auto-pick on
timeout).  Hardcore scoring: a player who **fails** the map scores no point
that round; among passers the higher score wins; if both fail the round is
void (no point).  When both pools are exhausted level a sudden-death tiebreak
map is pulled.  The result feeds the single-track TrueSkill update.

One background task per duel (de-duplicated via ``_active``); it is launched
on accept and re-launched by the recovery pass after a restart, resuming from
``Duel.current_round``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, update as sa_update

from db.database import get_db_session
from db.models.duel import Duel
from db.models.duel_round import DuelRound
from db.models.duel_map_pool import DuelMapPool
from db.models.duel_rating import DuelRating
from db.models.user import User
from services.duel.duel_constants import (
    SCORE_POLL_INTERVAL,
    MAP_READY_COUNTDOWN,
    ROUND_FORFEIT_BUFFER_MIN,
    MAX_MONITOR_HOURS,
    MAX_TIEBREAKERS,
    PICK_TIMEOUT_SECONDS,
    RECONNECT_GRACE_MIN,
    REINVITE_INTERVAL_MIN,
    MAX_RECONNECTS_PER_ROUND,
)
from services.duel.match_monitor import (
    find_round_score, extract_score_stats, scorev2_multiplier,
)
from services.duel.map_selector import get_map_for_round
from services.duel.rating import update_ratings, rating_to_sr
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger("duel.engine")

_active: set[int] = set()


# ── player context ───────────────────────────────────────────────────────────
class _Player:
    __slots__ = ("user_id", "osu_id", "username", "pp")

    def __init__(self, user: User):
        self.user_id = user.id
        self.osu_id = int(user.osu_user_id or 0)
        self.username = user.osu_username or "?"
        self.pp = float(user.player_pp or 0)


# ── public entry ─────────────────────────────────────────────────────────────
def launch(bot, osu_api, duel_id: int) -> None:
    """Start (or resume) the engine task for a duel, de-duplicated."""
    if duel_id in _active:
        return
    _active.add(duel_id)
    asyncio.create_task(_run_guarded(bot, osu_api, duel_id), name=f"duel_engine_{duel_id}")


async def _run_guarded(bot, osu_api, duel_id: int) -> None:
    try:
        await run_duel(bot, osu_api, duel_id)
    except Exception as e:  # never let a duel task die silently
        logger.error(f"run_duel({duel_id}) crashed: {e}", exc_info=True)
    finally:
        from services.duel.duel_state import clear_duel_state
        clear_duel_state(duel_id)


# ── helpers ──────────────────────────────────────────────────────────────────
def _map_label(m: Optional[DuelMapPool], beatmap_id: int) -> str:
    if not m:
        return f"map {beatmap_id}"
    return f"{m.artist} - {m.title} [{m.version}]"


def _round_ok(stats: dict) -> bool:
    """Did this player legitimately clear the map for round-scoring purposes?

    Hardcore rule: failing the map scores nothing.  NoFail neutralises the
    fail (the player can't be failed), so it would let someone who'd otherwise
    fail still "pass" — we therefore treat an NF score as a fail too."""
    return bool(stats.get("passed")) and "NF" not in stats.get("mods", ())


def _round_score(stats: dict, mode: str) -> float:
    """Score used to rank two passers.  In RANKED, divide out the player's
    ScoreV2 mod multiplier so stacking HR/HD/DT/FL can't win a round on the raw
    score bonus alone; CASUAL keeps the raw total."""
    raw = stats["score"]
    if mode != "ranked":
        return raw
    mult = scorev2_multiplier(stats.get("mods", ()))
    return raw / mult if mult else raw


def _decide_round(p1_stats: dict, p2_stats: dict, mode: str = "casual") -> Optional[int]:
    """Hardcore rule → 1, 2, or None (void).  Failing the map (or NoFail-ing it)
    scores nothing; among legitimate passers the higher (mod-normalised in
    ranked) score wins."""
    p1_ok, p2_ok = _round_ok(p1_stats), _round_ok(p2_stats)
    if p1_ok and p2_ok:
        return 1 if _round_score(p1_stats, mode) >= _round_score(p2_stats, mode) else 2
    if p1_ok:
        return 1
    if p2_ok:
        return 2
    return None


async def _duel_inactive(duel_id: int) -> bool:
    """True if the duel is no longer playable (cancelled/completed/expired by an
    admin force-close or an opponent's /duelcancel) — so the engine stops
    polling promptly instead of running out the round deadline."""
    async with get_db_session() as session:
        st = (await session.execute(
            select(Duel.status).where(Duel.id == duel_id)
        )).scalar_one_or_none()
    return st not in ("round_active", "accepted")


async def _await_result_or_disconnect(
    osu_api, duel_id: int, match_id: int, beatmap_id: int,
    p1_osu: int, p2_osu: int, after: datetime, deadline: datetime,
    gone_evt: "asyncio.Event",
) -> tuple[str, Optional[tuple[dict, dict]]]:
    """Poll the linked match for this round's result, returning early on a
    Bancho disconnect or an external cancel.

    Returns ``(outcome, result)`` where ``outcome`` is one of:
      "result"     — both players have a completed game; ``result`` is their stats
      "disconnect" — a player left the lobby mid-round (handle reconnect)
      "cancelled"  — the duel was cancelled out from under us
      "timeout"    — the deadline passed with no result (void / forfeit)
    A completed result is always preferred over a disconnect within the same
    poll, so a player who finishes the map and then leaves still scores."""
    while datetime.now(timezone.utc) < deadline:
        if await _duel_inactive(duel_id):
            return "cancelled", None
        try:
            payload = await osu_api.get_match(int(match_id))
        except Exception as e:
            logger.warning(f"get_match({match_id}) failed: {e}")
            payload = None
        if payload:
            found = find_round_score(payload, beatmap_id, p1_osu, p2_osu, after=after)
            if found:
                return "result", (extract_score_stats(found[0]), extract_score_stats(found[1]))
        if gone_evt.is_set():
            return "disconnect", None
        try:
            # Sleep until the next poll, but wake immediately on a disconnect.
            await asyncio.wait_for(gone_evt.wait(), timeout=SCORE_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
    return "timeout", None


async def _await_reconnect(bot, osu_api, irc, duel_id: int, match_id: int, p1, p2) -> bool:
    """A player dropped mid-round: re-invite every ``REINVITE_INTERVAL_MIN`` for
    up to ``RECONNECT_GRACE_MIN`` minutes. Returns True once everyone is back; on
    timeout it hands off to ``_auto_cancel_no_show`` (forfeit-if-trailing, else
    no-rating cancel) and returns False."""
    from services.duel import reconnect, status_card
    channel = f"#mp_{match_id}"
    back = reconnect.back_event(duel_id)
    deadline = datetime.now(timezone.utc) + timedelta(minutes=RECONNECT_GRACE_MIN)

    while datetime.now(timezone.utc) < deadline:
        gone = reconnect.missing(duel_id)
        if not gone:
            return True
        for uname in gone:
            try:
                await irc.mp_invite(channel, uname)
            except Exception as e:
                logger.debug(f"duel {duel_id}: re-invite {uname} failed: {e}")
        try:
            await asyncio.wait_for(back.wait(), timeout=REINVITE_INTERVAL_MIN * 60)
        except asyncio.TimeoutError:
            pass
        if not reconnect.missing(duel_id):
            return True

    if not reconnect.missing(duel_id):
        return True
    await _auto_cancel_no_show(bot, osu_api, irc, duel_id, match_id, p1, p2)
    return False


async def _auto_cancel_no_show(bot, osu_api, irc, duel_id: int, match_id: int,
                               p1, p2) -> None:
    """A player never returned to the lobby after the reconnect grace.

    If the absentee was **trailing** on the scoreboard, treat it as a forfeit
    and finish the duel in the present player's favour (rating applied) — a
    losing player must not be able to escape the loss by leaving.  A drop while
    ahead or level is a genuine disconnect (or unclear), so it stays a cancel
    with no rating change.  Both players gone → no-rating cancel too.
    """
    from services.duel import reconnect, status_card
    gone = set(reconnect.missing(duel_id))
    now = datetime.now(timezone.utc)

    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("round_active", "accepted"):
            return
        t1, t2 = duel.player1_rounds_won, duel.player2_rounds_won

    p1_gone = bool(p1 and p1.username in gone)
    p2_gone = bool(p2 and p2.username in gone)

    # Forfeit only the *trailing* absentee; never hand a loss to someone who was
    # ahead or to a player when both dropped.
    forfeit_winner: Optional[int] = None
    if p1_gone and not p2_gone and t1 < t2:
        forfeit_winner = 2
    elif p2_gone and not p1_gone and t2 < t1:
        forfeit_winner = 1

    if forfeit_winner is not None:
        loser = p1 if forfeit_winner == 2 else p2
        winner = p1 if forfeit_winner == 1 else p2
        logger.warning(
            f"duel {duel_id}: {loser.username} forfeited by no-show while "
            f"trailing {t1}:{t2} — awarding to {winner.username}"
        )
        # _finish applies the rating, marks the duel completed, closes the room
        # and posts the finish card; re-caption afterwards to name the forfeit.
        await _finish(bot, osu_api, duel_id, forfeit_winner, irc, match_id)
        try:
            await status_card.post_or_update(
                bot, duel_id,
                caption=(f"🏳️ <b>Форфейт</b> — {escape_html(loser.username)} не "
                         f"вернулся в лобби, проигрывая по счёту. Победа и рейтинг "
                         f"— <b>{escape_html(winner.username)}</b>."),
            )
        except Exception:
            logger.debug(f"duel {duel_id}: forfeit caption failed", exc_info=True)
        return

    # No clear loser → cancel, rating untouched.
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("round_active", "accepted"):
            return
        duel.status = "cancelled"
        duel.completed_at = now
        await session.execute(
            sa_update(DuelRound)
            .where(DuelRound.duel_id == duel_id, DuelRound.status.in_(("waiting", "playing")))
            .values(status="cancelled", completed_at=now)
        )
        await session.commit()

    names = ", ".join(escape_html(u) for u in gone) or "игрок"
    logger.warning(f"duel {duel_id}: auto-cancelled — {names} did not return within "
                   f"{RECONNECT_GRACE_MIN} min")
    try:
        await status_card.post_or_update(
            bot, duel_id,
            caption=(f"❌ <b>Дуэль отменена</b> — {names} не вернулся в лобби за "
                     f"{RECONNECT_GRACE_MIN} мин. Рейтинг не изменён."),
        )
    except Exception:
        logger.debug(f"duel {duel_id}: no-show cancel caption failed", exc_info=True)
    try:
        from services.duel.irc_room import close_room
        if irc and getattr(irc, "connected", False):
            await close_room(irc, int(match_id))
    except Exception as e:
        logger.warning(f"duel {duel_id}: no-show close_room failed: {e}")
    # `clear_duel_state` runs in _run_guarded's finally — no need to wipe twice.


async def _play_round_resilient(
    bot, osu_api, irc, duel_id: int, match_id: int, beatmap_id: int,
    p1, p2, length_s: int, after: datetime,
) -> tuple[Optional[tuple[dict, dict]], str]:
    """Set + start the map and return its result, surviving a mid-round Bancho
    disconnect. On a leave we ``!mp abort``, wait out the reconnect grace, then
    replay the same map. Returns ``(result_or_None, outcome)`` where outcome is:
      "ok"        — ``result`` is (p1_stats, p2_stats)
      "forfeit"   — deadline passed / too many disconnects → void round
      "cancelled" — duel ended (no-show auto-cancel or external cancel); stop."""
    from services.duel import reconnect, status_card
    channel = f"#mp_{match_id}"
    gone_evt = reconnect.gone_event(duel_id)
    attempts = 0

    while True:
        attempts += 1
        # A player who left during the pick is already missing → recover first.
        if reconnect.missing(duel_id):
            if not await _await_reconnect(bot, osu_api, irc, duel_id, match_id, p1, p2):
                return None, "cancelled"

        try:
            await _set_map(irc, match_id, beatmap_id)
        except Exception as e:
            logger.error(f"run_duel({duel_id}): set_map_and_start failed: {e}", exc_info=True)

        deadline = (datetime.now(timezone.utc)
                    + timedelta(seconds=length_s)
                    + timedelta(minutes=ROUND_FORFEIT_BUFFER_MIN))
        outcome, result = await _await_result_or_disconnect(
            osu_api, duel_id, match_id, beatmap_id, p1.osu_id, p2.osu_id,
            after, deadline, gone_evt,
        )

        if outcome == "result":
            return result, "ok"
        if outcome == "timeout":
            return None, "forfeit"
        if outcome == "cancelled":
            return None, "cancelled"

        # outcome == "disconnect": abort the partial game and wait for return.
        try:
            await irc.mp_abort(channel)
        except Exception as e:
            logger.debug(f"duel {duel_id}: mp_abort failed: {e}")
        if attempts > MAX_RECONNECTS_PER_ROUND:
            logger.warning(f"duel {duel_id}: round replayed {attempts - 1}× after "
                           f"disconnects — voiding to move on")
            return None, "forfeit"
        await status_card.post_or_update(
            bot, duel_id,
            caption=(f"⚠️ Игрок вылетел из лобби — ждём переподключения "
                     f"(до {RECONNECT_GRACE_MIN} мин, инвайт каждые "
                     f"{REINVITE_INTERVAL_MIN} мин)…"),
        )
        if not await _await_reconnect(bot, osu_api, irc, duel_id, match_id, p1, p2):
            return None, "cancelled"
        await status_card.post_or_update(
            bot, duel_id, caption="✅ Игрок вернулся — переигрываем карту.",
        )


# ── per-player pools / pick helpers ──────────────────────────────────────────
def _parse_pools(field: str) -> tuple[list[int], list[int]]:
    """Decode ``pool_beatmap_ids`` ("p1ids;p2ids") into the two players' pools.

    Tolerates the legacy single-group format (no ``;``) by treating it as p1's
    pool with an empty p2 pool — only relevant for a duel mid-flight across the
    upgrade, which then simply picks from the one list.
    """
    groups = (field or "").split(";")

    def _ids(s: str) -> list[int]:
        return [int(x) for x in s.split(",") if x.strip()]

    p1 = _ids(groups[0]) if len(groups) > 0 else []
    p2 = _ids(groups[1]) if len(groups) > 1 else []
    return p1, p2


async def _weaker_is_p1(p1_id: int, p2_id: int, mode: str) -> bool:
    """True if player 1 is the weaker (lower conservative, μ tie-break) → picks
    first. Ratings only change on duel finish, so this is stable mid-match."""
    async with get_db_session() as session:
        rows = {
            r.user_id: r for r in (await session.execute(
                select(DuelRating).where(
                    DuelRating.user_id.in_([p1_id, p2_id]), DuelRating.mode == mode,
                )
            )).scalars().all()
        }
    r1, r2 = rows.get(p1_id), rows.get(p2_id)
    k1 = (r1.conservative, r1.mu) if r1 else (0.0, 1500.0)
    k2 = (r2.conservative, r2.mu) if r2 else (0.0, 1500.0)
    return k1 <= k2


async def _run_pool_swap_phase(
    bot, duel_id: int, p1, p2, p1_tg: Optional[int], p2_tg: Optional[int],
    p1_pool: list[int], p2_pool: list[int], mode: str,
) -> tuple[list[int], list[int]]:
    """Open the 60-second pre-round-1 swap window for both players in parallel.

    Each player sees their own 6-map pool and may tap any card to swap it for
    a fresh roll at the same target SR.  The two players' pools stay disjoint
    by design: every candidate fetcher excludes the union of both pools plus
    any cards already rejected this session.

    On any failure (DM blocked, candidate fetcher empty) the original pool is
    returned — the swap is a convenience, not a gate."""
    from services.duel.map_selector import get_pick_candidates
    from services.duel.rating import rating_to_sr
    from services.duel import pool_swap, status_card

    # Target SR mirrors the one used in accept_duel: midpoint of μ → SR.
    avg_mu = await _avg_mu(p1.user_id, p2.user_id, mode)
    target_sr = rating_to_sr(avg_mu)

    p1_rows = {r["id"]: r for r in await _pool_rows(p1_pool)}
    p2_rows = {r["id"]: r for r in await _pool_rows(p2_pool)}

    def _make_fetcher():
        async def _fetch(excluded: set[int]) -> Optional[dict]:
            cands = await get_pick_candidates(
                target_sr, n=1, exclude_ids=list(excluded),
            )
            if not cands:
                return None
            m = cands[0]
            return {
                "id": int(m.beatmap_id),
                "title": f"{m.artist} - {m.title}",
                "sr": float(m.star_rating or 0.0),
                "version": m.version or "",
            }
        return _fetch

    await status_card.post_or_update(
        bot, duel_id,
        caption=(f"🔁 <b>Подгонка пула</b> — у каждого до "
                 f"{pool_swap.MAX_SWAPS} замен в личке "
                 f"(⏱ {pool_swap.SWAP_TIMEOUT_SECONDS} с)."),
    )

    # Both players' pools are interleaved in `excluded` so the fetchers can
    # never produce a duplicate across players.  Each fetcher uses its own
    # closure over the shared exclusion set, which `submit_swap` extends after
    # every approved swap.
    new_p1, new_p2 = await asyncio.gather(
        pool_swap.run_swap(
            bot, duel_id, p1_tg, p1_pool, p1_rows,
            all_other_ids=list(p2_pool), fetch_candidate=_make_fetcher(),
        ),
        pool_swap.run_swap(
            bot, duel_id, p2_tg, p2_pool, p2_rows,
            all_other_ids=list(p1_pool), fetch_candidate=_make_fetcher(),
        ),
        return_exceptions=True,
    )
    p1_final = new_p1 if isinstance(new_p1, list) else p1_pool
    p2_final = new_p2 if isinstance(new_p2, list) else p2_pool
    return p1_final, p2_final


async def _pool_rows(ids: list[int]) -> list[dict]:
    """Fetch display rows ``[{"id","title","sr","version"}]`` for pick buttons,
    preserving the order of ``ids``."""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id.in_(ids))
        )).scalars().all()
    by_id = {r.beatmap_id: r for r in rows}
    out = []
    for i in ids:
        r = by_id.get(i)
        out.append({
            "id": i,
            "title": (r.title if r else f"map {i}"),
            "sr": float(r.star_rating or 0.0) if r else 0.0,
            "version": (r.version if r else ""),
        })
    return out


# ── main loop ────────────────────────────────────────────────────────────────
async def run_duel(bot, osu_api, duel_id: int) -> None:
    # Load context.
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("accepted", "round_active"):
            return
        p1u = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one_or_none()
        p2u = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one_or_none()
        if not p1u or not p2u or not duel.pool_beatmap_ids or not duel.osu_match_id:
            logger.error(f"run_duel({duel_id}): missing players / pool / IRC match — abandoning")
            duel.status = "cancelled"
            await session.commit()
            return
        p1, p2 = _Player(p1u), _Player(p2u)
        p1_tg, p2_tg = p1u.telegram_id, p2u.telegram_id
        p1_pool, p2_pool = _parse_pools(duel.pool_beatmap_ids)
        match_id = int(duel.osu_match_id)
        mode = duel.mode
        win_target = duel.win_target
        t1, t2 = duel.player1_rounds_won, duel.player2_rounds_won
        chat_id, thread_id = duel.chat_id, duel.message_thread_id
        if duel.status != "round_active":
            duel.status = "round_active"
        await session.commit()
        rounds = (await session.execute(
            select(DuelRound).where(DuelRound.duel_id == duel_id)
            .order_by(DuelRound.round_number.asc())
        )).scalars().all()

    # Weaker player (lower conservative, μ tie-break) serves first; ratings only
    # change on finish, so recomputing on (re)start is stable.
    weaker_is_p1 = await _weaker_is_p1(p1.user_id, p2.user_id, mode)

    from services.duel import status_card, pick_phase, pool_card

    # Recovery: every map already attached to a round is "played" (never
    # re-picked); a trailing still-"playing" round is resumed on its own map.
    played: list[int] = [r.beatmap_id for r in rounds]
    resume_round = rounds[-1] if rounds and rounds[-1].status == "playing" else None

    # Make sure each player's live pool card is tracked (no-op on a cold start —
    # accept_duel already registered + sent it; on a restart this re-seeds the
    # state so the next show() sends a fresh card) and re-stamp already-played
    # maps so a resumed duel's cards show their PLAYED history.
    pool_target_sr = rating_to_sr(await _avg_mu(p1.user_id, p2.user_id, mode))
    for tg, pool in ((p1_tg, p1_pool), (p2_tg, p2_pool)):
        if tg:
            pool_card.ensure(duel_id, tg, chat_id=tg, order=pool, mode=mode,
                             target_sr=pool_target_sr)
    for bid in played:
        if p1_tg and bid in p1_pool:
            pool_card.mark_played(duel_id, p1_tg, bid)
        elif p2_tg and bid in p2_pool:
            pool_card.mark_played(duel_id, p2_tg, bid)

    await status_card.post_or_update(bot, duel_id)

    # Pre-round-1 swap: each player may reroll up to 3 cards from their pool.
    # Only on a cold start — if we're resuming a duel with rounds already
    # played, the pool is locked in and we go straight to the main loop.
    if not played and not resume_round:
        p1_pool, p2_pool = await _run_pool_swap_phase(
            bot, duel_id, p1, p2, p1_tg, p2_tg, p1_pool, p2_pool, mode,
        )
        # Persist any swaps back to the duel row so a restart mid-duel
        # resumes from the agreed-upon pool, not the original one.
        async with get_db_session() as session:
            duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
            duel.pool_beatmap_ids = ";".join([
                ",".join(str(i) for i in p1_pool),
                ",".join(str(i) for i in p2_pool),
            ])
            await session.commit()
        await status_card.post_or_update(bot, duel_id)

    from services.bancho_irc import get_irc_client
    irc = get_irc_client()
    watchdog = datetime.now(timezone.utc) + timedelta(hours=MAX_MONITOR_HOURS)
    winner_player: Optional[int] = None  # 1 / 2
    tiebreaks_used = 0

    while True:
        if t1 >= win_target or t2 >= win_target:
            winner_player = 1 if t1 >= win_target else 2
            break
        if datetime.now(timezone.utc) > watchdog:
            logger.warning(f"run_duel({duel_id}): watchdog timeout — finishing on current tally")
            break

        is_resume = resume_round is not None
        if is_resume:
            # Crash mid-round → resume the exact same map (already in `played`).
            beatmap_id = resume_round.beatmap_id
            round_number = resume_round.round_number
            resume_round = None
        else:
            p1_rem = [i for i in p1_pool if i not in played]
            p2_rem = [i for i in p2_pool if i not in played]
            # Alternate picks, weaker first; skip a player who is out of maps
            # while the other still has some.
            picker_is_weaker = (len(played) % 2 == 0)
            picker_is_p1 = weaker_is_p1 if picker_is_weaker else (not weaker_is_p1)
            if picker_is_p1 and not p1_rem and p2_rem:
                picker_is_p1 = False
            elif (not picker_is_p1) and not p2_rem and p1_rem:
                picker_is_p1 = True
            rem = p1_rem if picker_is_p1 else p2_rem
            round_number = len(played) + 1

            if not rem:
                # Both pools exhausted → decide on the tally, or pull a tiebreak.
                if t1 != t2:
                    winner_player = 1 if t1 > t2 else 2
                    break
                if tiebreaks_used >= MAX_TIEBREAKERS:
                    # Still tied after the max tiebreaks (we only get here with
                    # t1 == t2): a draw, not a player-1 win → _finish cancels
                    # without a rating change.
                    winner_player = None
                    break
                tiebreaks_used += 1
                target_sr = rating_to_sr((await _avg_mu(p1.user_id, p2.user_id, mode)))
                tb = await get_map_for_round(target_sr, exclude_ids=played)
                if not tb:
                    # Tied and no tiebreak map available → draw, no rating change.
                    winner_player = None
                    break
                beatmap_id = tb.beatmap_id
                await status_card.post_or_update(
                    bot, duel_id,
                    caption="🎲 <b>Тай-брейк</b> — счёт равный, решающая карта!",
                )
            else:
                # Interactive pick from the active player's own remaining pool.
                picker = p1 if picker_is_p1 else p2
                picker_tg = p1_tg if picker_is_p1 else p2_tg
                rows = await _pool_rows(rem)
                # Number each remaining map by its 1-based position in the
                # picker's full pool, so the pick buttons match the pool-card pips.
                picker_pool = p1_pool if picker_is_p1 else p2_pool
                pos_by_id = {bid: idx + 1 for idx, bid in enumerate(picker_pool)}
                for r in rows:
                    r["pos"] = pos_by_id.get(r["id"], 0)
                status_card.set_pick_state(duel_id, 1 if picker_is_p1 else 2, picker.username)
                await status_card.post_or_update(bot, duel_id)
                beatmap_id = await pick_phase.run_pick(
                    bot, duel_id, picker_tg, round_number, rows, PICK_TIMEOUT_SECONDS,
                )
                status_card.clear_pick_state(duel_id)
                if beatmap_id is None:  # safety — shouldn't happen with rem != []
                    # Strict lead decides; a tie falls through to _finish's
                    # draw handling (no rating change), never an auto p1 win.
                    winner_player = 1 if t1 > t2 else (2 if t2 > t1 else None)
                    break

            played.append(beatmap_id)

        # Map row (title / sr / length).
        async with get_db_session() as session:
            mrow = (await session.execute(
                select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()
            map_label = _map_label(mrow, beatmap_id)
            star = float(mrow.star_rating) if mrow else 0.0
            length_s = int(mrow.length or 180) if mrow else 180
            beatmapset_id = int(mrow.beatmapset_id) if mrow else None

        round_started = datetime.now(timezone.utc)
        deadline = round_started + timedelta(seconds=length_s) + timedelta(minutes=ROUND_FORFEIT_BUFFER_MIN)

        # Persist round + progress (so recovery resumes here). Reuse the row when
        # resuming a crashed round.
        async with get_db_session() as session:
            duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
            if duel.status != "round_active":
                pick_phase.cancel_pick(duel_id)
                return  # cancelled meanwhile
            duel.current_round = round_number - 1
            rnd = None
            if is_resume:
                rnd = (await session.execute(
                    select(DuelRound).where(
                        DuelRound.duel_id == duel_id,
                        DuelRound.round_number == round_number,
                    )
                )).scalar_one_or_none()
            if rnd is None:
                rnd = DuelRound(
                    duel_id=duel_id, round_number=round_number,
                    beatmap_id=beatmap_id, beatmapset_id=beatmapset_id,
                    beatmap_title=map_label, star_rating=star,
                    status="playing", started_at=round_started, forfeit_at=deadline,
                )
                session.add(rnd)
            else:
                rnd.status = "playing"
                rnd.started_at = round_started
                rnd.forfeit_at = deadline
            await session.commit()

        # Live scoreboard card → now playing this map.
        await status_card.post_or_update(bot, duel_id)

        # Push the map, await the result, and survive mid-round Bancho drops:
        # leaves trigger ``!mp abort`` + reconnect grace + replay; a no-show
        # auto-cancels the duel (we exit the main loop without a rating change).
        result, outcome = await _play_round_resilient(
            bot, osu_api, irc, duel_id, match_id, beatmap_id,
            p1, p2, length_s, round_started,
        )
        if outcome == "cancelled":
            # The no-show path already updated the duel row and live card.
            return

        # Score the round.
        if result is None:
            point = None
            p1_stats = p2_stats = None
            status = "forfeit"
        else:
            p1_stats, p2_stats = result
            point = _decide_round(p1_stats, p2_stats, mode)
            status = "completed" if point is not None else "void"

        if point == 1:
            t1 += 1
        elif point == 2:
            t2 += 1

        await _persist_round_result(duel_id, round_number, point, status,
                                    p1_stats, p2_stats, t1, t2)
        # Round result rides under the live card as its caption (no separate msg).
        await status_card.post_or_update(
            bot, duel_id,
            caption=_round_result_text(p1, p2, point, p1_stats, p2_stats, t1, t2),
        )

    await _finish(bot, osu_api, duel_id, winner_player, irc, match_id)


async def _set_map(irc, match_id: int, beatmap_id: int) -> None:
    from services.duel.irc_room import set_map_and_start
    await set_map_and_start(irc, int(match_id), int(beatmap_id), countdown=MAP_READY_COUNTDOWN)


async def _avg_mu(p1_id: int, p2_id: int, mode: str) -> float:
    from db.models.duel_rating import DuelRating
    async with get_db_session() as session:
        rows = (await session.execute(
            select(DuelRating.mu).where(
                DuelRating.user_id.in_([p1_id, p2_id]), DuelRating.mode == mode,
            )
        )).scalars().all()
    return sum(rows) / len(rows) if rows else 1500.0


async def _persist_round_result(duel_id, round_number, point, status,
                                p1_stats, p2_stats, t1, t2) -> None:
    async with get_db_session() as session:
        rnd = (await session.execute(
            select(DuelRound).where(
                DuelRound.duel_id == duel_id, DuelRound.round_number == round_number,
            )
        )).scalar_one_or_none()
        if rnd:
            rnd.status = status
            rnd.winner_player = point
            rnd.completed_at = datetime.now(timezone.utc)
            if p1_stats:
                rnd.player1_score = p1_stats["score"]
                rnd.player1_accuracy = p1_stats["accuracy"]
                rnd.player1_combo = p1_stats["combo"]
                rnd.player1_misses = p1_stats["misses"]
                rnd.player1_passed = p1_stats["passed"]
            if p2_stats:
                rnd.player2_score = p2_stats["score"]
                rnd.player2_accuracy = p2_stats["accuracy"]
                rnd.player2_combo = p2_stats["combo"]
                rnd.player2_misses = p2_stats["misses"]
                rnd.player2_passed = p2_stats["passed"]
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one()
        duel.player1_rounds_won = t1
        duel.player2_rounds_won = t2
        duel.current_round = round_number  # next map index
        await session.commit()


def _round_result_text(p1, p2, point, p1_stats, p2_stats, t1, t2) -> str:
    if point is None and p1_stats is None:
        body = "⏱ Раунд не сыгран (форфейт) — очко никому."
    elif point is None:
        body = "💀 Оба не прошли карту — раунд пустой, очко никому."
    else:
        win = p1.username if point == 1 else p2.username
        body = f"🏆 Раунд за <b>{escape_html(win)}</b>."
    if p1_stats and p2_stats:
        def line(name, s):
            mark = "✅" if s["passed"] else "💀"
            return (f"{mark} <b>{escape_html(name)}</b>: {s['score']:,} "
                    f"({s['accuracy']:.2f}%, {s['combo']}x, {s['misses']}✗)")
        body += f"\n{line(p1.username, p1_stats)}\n{line(p2.username, p2_stats)}"
    return f"{body}\n\nСчёт: <b>{p1.username} {t1} : {t2} {p2.username}</b>"


# ── finish ───────────────────────────────────────────────────────────────────
async def _finish(bot, osu_api, duel_id: int, winner_player: Optional[int],
                  irc, match_id: int) -> None:
    now = datetime.now(timezone.utc)
    async with get_db_session() as session:
        duel = (await session.execute(select(Duel).where(Duel.id == duel_id))).scalar_one_or_none()
        if not duel or duel.status not in ("round_active", "accepted"):
            return
        p1u = (await session.execute(select(User).where(User.id == duel.player1_user_id))).scalar_one()
        p2u = (await session.execute(select(User).where(User.id == duel.player2_user_id))).scalar_one()
        p1, p2 = _Player(p1u), _Player(p2u)
        t1, t2 = duel.player1_rounds_won, duel.player2_rounds_won
        mode = duel.mode
        chat_id = duel.chat_id
        thread_id = duel.message_thread_id

    from services.duel import status_card

    # A finish with no decisive lead (watchdog / exhausted tiebreaks / safety
    # fallbacks land here with winner_player=None and t1==t2) is a DRAW, not a
    # player-1 win — cancel without touching rating. CAS so a concurrent path /
    # restart can't double-handle it.
    if winner_player is None and t1 == t2:
        async with get_db_session() as session:
            cas = await session.execute(
                sa_update(Duel)
                .where(Duel.id == duel_id, Duel.status.in_(("round_active", "accepted")))
                .values(status="cancelled", completed_at=now)
            )
            await session.commit()
        if cas.rowcount == 0:
            return
        try:
            await status_card.post_or_update(
                bot, duel_id,
                caption=(f"🤝 <b>Ничья</b> ({mode.upper()}) — счёт {t1}:{t2}. "
                         f"Победитель не определён, рейтинг не изменён."),
            )
        except Exception:
            logger.debug(f"duel {duel_id}: draw card update failed", exc_info=True)
        try:
            from services.duel.irc_room import close_room
            if irc and getattr(irc, "connected", False):
                await close_room(irc, int(match_id))
        except Exception as e:
            logger.warning(f"_finish({duel_id}): draw close_room failed: {e}")
        return

    if winner_player is None:
        winner_player = 1 if t1 > t2 else 2
    winner, loser = (p1, p2) if winner_player == 1 else (p2, p1)

    # Claim the finish atomically BEFORE applying the rating. If a restart
    # re-launched the engine after a prior run already completed this duel, the
    # CAS finds the row no longer active and we bail — so `update_ratings` runs
    # exactly once (no double-counted win/sigma shrink on recovery).
    async with get_db_session() as session:
        cas = await session.execute(
            sa_update(Duel)
            .where(Duel.id == duel_id, Duel.status.in_(("round_active", "accepted")))
            .values(status="completed", winner_user_id=winner.user_id, completed_at=now)
        )
        await session.commit()
    if cas.rowcount == 0:
        return

    # Casual duels are exempt from the TrueSkill/division ladder: only RANKED
    # results move μ/σ. The casual DuelRating row still exists (pp-seeded at
    # accept) purely to target the pool SR — a casual outcome never rates it.
    if mode == "ranked":
        # Snapshot calibration state *before* the rating update: a player in
        # placement has an uncertainty-deflated conservative score, so any
        # division change for them is noise — we suppress the promo/relegation
        # card.
        async with get_db_session() as session:
            pre = (await session.execute(
                select(DuelRating).where(
                    DuelRating.mode == mode,
                    DuelRating.user_id.in_([winner.user_id, loser.user_id]),
                )
            )).scalars().all()
        was_calibrating = {r.user_id: (r.placement_matches_left or 0) > 0 for r in pre}

        w_rating, l_rating, w_old, w_new, l_old, l_new = await update_ratings(
            winner.user_id, loser.user_id, mode,
            winner_pp=winner.pp, loser_pp=loser.pp,
        )

    # Final result lives as the caption under the live card, now re-rendered in
    # its finished state (winner crowned) — not as a separate message.
    text = _finish_text(winner, loser, t1, t2, mode)
    try:
        await status_card.post_or_update(bot, duel_id, caption=text)
    except Exception:
        logger.debug(f"duel {duel_id}: final card update failed", exc_info=True)

    # Ranked division changes → promotion/relegation card to the duel chat and
    # the configured DUEL-notify chat (setduelnotifychat).
    if mode == "ranked":
        try:
            from services.duel.division_notify import notify_division_change
            if w_old != w_new and not was_calibrating.get(winner.user_id, False):
                await notify_division_change(
                    bot, winner.user_id, w_old, w_new, chat_id, thread_id,
                    duel_points=w_rating.conservative, mode=mode,
                )
            if l_old != l_new and not was_calibrating.get(loser.user_id, False):
                await notify_division_change(
                    bot, loser.user_id, l_old, l_new, chat_id, thread_id,
                    duel_points=l_rating.conservative, mode=mode,
                )
        except Exception:
            logger.debug(f"duel {duel_id}: division notify failed", exc_info=True)

    # The live-card mapping is dropped by `_run_guarded`'s finally
    # (`clear_duel_state`) — the final card message itself stays in the topic.

    # Close the IRC room.
    try:
        from services.duel.irc_room import close_room
        if irc and getattr(irc, "connected", False):
            await close_room(irc, int(match_id))
    except Exception as e:
        logger.warning(f"_finish({duel_id}): close_room failed: {e}")


def _finish_text(winner, loser, t1, t2, mode) -> str:
    return (
        f"🏁 <b>ДУЭЛЬ ОКОНЧЕНА</b> ({mode.upper()})\n\n"
        f"🥇 Победитель: <b>{escape_html(winner.username)}</b>\n"
        f"Счёт: <b>{t1} : {t2}</b>"
    )
