import asyncio
import logging
import random
import time
from collections import Counter, deque
from datetime import timedelta

from sqlalchemy import exists, func, or_, select

from db.database import AsyncSessionFactory
from db.models.player import OWN, Player
from db.models.user import User
from services.refresh.orchestrator import apply_refresh, claim_refresh
from services.refresh.policy import BACKGROUND_THRESHOLD, STATS_SWEEP_THRESHOLD
from services.refresh.prepared import PreparedProfile
from services.refresh.runtime import background_request
from services.leaderboard.snapshots import ensure_period_snapshot
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("tasks.profile_updater")

PROFILE_FIELDS = tuple(name for name in OWN if name not in ("active_title_code", "share_replays")) + (
    "level", "join_date", "grade_count_s", "grade_count_ss", "best_scores_baseline_at",
    "playcount_week_anchor", "playcount_week_anchor_at", "week_plays_best",
    "last_api_update", "last_full_update",
)


def _bare(value):
    return value.replace(tzinfo=None) if value is not None else None


class ProfileUpdater:
    CONCURRENT_WORKERS = 3
    TICK_SECONDS = 30
    REPORT_EVERY = 1800
    TROUBLE_EVERY = 120
    SWEEP_BATCH_LIMIT = 150
    PROFILE_TIMEOUT = 180
    INACTIVE_STATS_THRESHOLD = timedelta(hours=1)
    INACTIVE_FULL_THRESHOLD = timedelta(hours=6)
    ACTIVE_WINDOW = timedelta(days=1)
    RETRY_BASE = 60
    RETRY_MAX = 3600

    def __init__(self, api_client):
        self.api_client = api_client
        self.queues = {mode: asyncio.Queue(maxsize=self.SWEEP_BATCH_LIMIT)
                       for mode in ("stats_only", "background_full")}
        self.pending = set()
        self.enqueued_at = {}
        self.durations = deque(maxlen=200)
        self.last_wait_seconds = 0.0
        self.scheduler_lag_seconds = 0.0
        self.retries = {}
        self.counts = Counter()
        self.last_success = None
        self.last_success_by_mode = {}
        self.last_schedule = None
        self.active = {}
        self.commit_seconds = 0.0

    def health(self):
        now = time.monotonic()
        durations = sorted(self.durations)
        return {
            "queued": {mode: queue.qsize() for mode, queue in self.queues.items()},
            "active": len(self.active),
            "oldest_active_seconds": round(max((now - t for t in self.active.values()), default=0), 1),
            "oldest_queued_seconds": round(max((now - t for t in self.enqueued_at.values()), default=0), 1),
            "last_wait_seconds": round(self.last_wait_seconds, 1),
            "scheduler_lag_seconds": round(self.scheduler_lag_seconds, 1),
            "job_p95_seconds": round(durations[min(len(durations) - 1, int(len(durations) * .95))], 1) if durations else 0,
            "retrying": len(self.retries), "outcomes": dict(self.counts),
            "last_success": self.last_success, "last_success_by_mode": dict(self.last_success_by_mode),
            "last_schedule": self.last_schedule,
            "api_http_errors": dict(getattr(self.api_client, "http_errors", {})),
            "last_commit_seconds": round(self.commit_seconds, 3),
        }

    @staticmethod
    def health_said(health: dict) -> tuple[bool, str]:
        queued = sum(health.get("queued", {}).values())
        errors = health.get("api_http_errors") or {}
        troubled = bool(
            errors
            or health.get("retrying", 0)
            or queued > 50
            or health.get("oldest_queued_seconds", 0) > 300
            or health.get("oldest_active_seconds", 0) > 120
            or health.get("scheduler_lag_seconds", 0) > 5
        )
        outcomes = health.get("outcomes") or {}
        said = "profiles: " + ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items())) if outcomes else "profiles: nothing done yet"
        said += f"; queued {queued}, active {health.get('active', 0)}, p95 {health.get('job_p95_seconds', 0)} s"
        if troubled:
            said += (
                f"; retrying {health.get('retrying', 0)}, oldest queued {health.get('oldest_queued_seconds', 0)} s, "
                f"oldest active {health.get('oldest_active_seconds', 0)} s, lag {health.get('scheduler_lag_seconds', 0)} s"
            )
            if errors:
                said += "; osu! answered " + ", ".join(f"{code} x{count}" for code, count in sorted(errors.items(), key=lambda item: str(item[0])))
        return troubled, said

    async def get_stale_user_ids(self):
        cutoff = utcnow() - BACKGROUND_THRESHOLD
        async with AsyncSessionFactory() as session:
            return list((await session.scalars(select(func.min(User.id)).where(
                User.osu_user_id.isnot(None),
                or_(User.last_full_update.is_(None), User.last_full_update < cutoff),
            ).group_by(User.osu_user_id).order_by(func.min(User.id)).limit(self.SWEEP_BATCH_LIMIT))).all())

    async def get_stats_sweep_ids(self):
        cutoff = utcnow() - STATS_SWEEP_THRESHOLD
        async with AsyncSessionFactory() as session:
            return list((await session.scalars(select(func.min(User.id)).where(
                User.osu_user_id.isnot(None),
                or_(User.last_api_update.is_(None), User.last_api_update < cutoff),
            ).group_by(User.osu_user_id).order_by(func.min(User.last_api_update).asc().nullsfirst(),
                                               func.min(User.id)).limit(self.SWEEP_BATCH_LIMIT))).all())

    async def get_stale_player_ids(self):
        from services.dossier_accounts import authenticated_player
        from services.render_farm import invites

        async with AsyncSessionFactory() as session:
            return list((await session.scalars(select(Player.id).where(
                or_(Player.id.in_(invites.linked_players()), authenticated_player()),
                ~exists().where(User.player_id == Player.id),
                or_(Player.last_full_update.is_(None), Player.last_full_update < utcnow() - BACKGROUND_THRESHOLD),
            ).order_by(Player.id).limit(self.SWEEP_BATCH_LIMIT))).all())

    async def _due(self, mode, limit):
        from services.dossier_accounts import authenticated_player
        from services.render_farm import invites

        now = utcnow()
        full = mode == "background_full"
        stamp = Player.last_full_update if full else Player.last_api_update
        active_interval = BACKGROUND_THRESHOLD if full else STATS_SWEEP_THRESHOLD
        inactive_interval = self.INACTIVE_FULL_THRESHOLD if full else self.INACTIVE_STATS_THRESHOLD
        owners = invites.present()
        active = or_(Player.last_seen_at >= now - self.ACTIVE_WINDOW,
                     Player.id.in_({o.player_id for o in owners if o.player_id is not None}),
                     Player.telegram_id.in_({o.telegram_id for o in owners if o.telegram_id}))
        excluded = {pid for pid, kind in self.pending if kind == mode}
        excluded.update(pid for (pid, kind), (_, until) in self.retries.items()
                        if kind == mode and until > time.monotonic())
        stmt = select(Player.id).where(
            or_(exists().where(User.player_id == Player.id),
                Player.id.in_(invites.linked_players()), authenticated_player()),
            or_(stamp.is_(None), stamp < now - inactive_interval,
                active & (stamp < now - active_interval)),
        )
        if excluded:
            stmt = stmt.where(Player.id.notin_(excluded))
        async with AsyncSessionFactory() as session:
            return list((await session.scalars(stmt.order_by(stamp.asc().nullsfirst(), Player.id).limit(limit))).all())

    async def schedule(self):
        for mode, queue in self.queues.items():
            free = queue.maxsize - queue.qsize()
            if free <= 0:
                continue
            for pid in await self._due(mode, free):
                key = (pid, mode)
                if key not in self.pending:
                    self.pending.add(key)
                    self.enqueued_at[key] = time.monotonic()
                    queue.put_nowait(pid)
        self.last_schedule = utcnow().isoformat()

    async def _refresh(self, player_id, mode):
        from db.models.best_score import UserBestScore
        from db.models.map_attempt import UserMapAttempt
        from services.oauth.token_manager import token_of
        from utils.osu.api_client import DATED_STATUSES
        from services.render_farm import invites

        async with AsyncSessionFactory() as session:
            player = await session.get(Player, player_id)
            if player is None:
                return "gone"
            now = _bare(utcnow())
            active = (player.last_seen_at is not None and _bare(player.last_seen_at) >= now - self.ACTIVE_WINDOW) or any(
                o.player_id == player.id or (o.telegram_id and o.telegram_id == player.telegram_id)
                for o in invites.present())
            full = mode == "background_full"
            interval = (BACKGROUND_THRESHOLD if active else self.INACTIVE_FULL_THRESHOLD) if full else (
                STATS_SWEEP_THRESHOLD if active else self.INACTIVE_STATS_THRESHOLD)
            stamp = player.last_full_update if full else player.last_api_update
            if stamp is not None and _bare(stamp) >= now - interval:
                return "fresh"
            versions = (_bare(player.last_api_update), _bare(player.last_full_update))
            ratings, dates = {}, set()
            pictures = {"avatar": [], "cover": []}
            if mode == "background_full":
                ratings = dict((await session.execute(select(UserBestScore.score_id, UserBestScore.eff_sr)
                               .where(UserBestScore.player_id == player_id))).all())
                for model in (UserBestScore, UserMapAttempt):
                    dates.update((await session.scalars(select(model.beatmap_id).where(
                        model.player_id == player_id, model.ranked_date.is_(None),
                        model.status.in_(DATED_STATUSES)).limit(150))).all())
                rows = (await session.execute(select(User.avatar_url, User.avatar_data.isnot(None),
                                                     User.cover_url, User.cover_data.isnot(None))
                                               .where(User.player_id == player_id))).all()
                pictures = {"avatar": [(r[0], r[1]) for r in rows], "cover": [(r[2], r[3]) for r in rows]}
            session.expunge(player)
        async with claim_refresh(player) as acquired:
            if not acquired:
                return "busy"
            token = await token_of(player)
            prepared = await PreparedProfile.fetch(self.api_client, player, mode, token, ratings, dates, pictures)
            async with AsyncSessionFactory() as session:
                current = await session.scalar(select(Player).where(Player.id == player_id).with_for_update())
                if current is None or current.osu_user_id != player.osu_user_id:
                    return "gone"
                if (_bare(current.last_api_update), _bare(current.last_full_update)) != versions:
                    return "superseded"
                if not await apply_refresh(current, session, prepared, mode, oauth_token=""):
                    return "failed"
                rows = (await session.scalars(select(User).where(User.player_id == player_id))).all()
                for user in rows:
                    for kind in ("avatar", "cover"):
                        if getattr(user, f"{kind}_url") != getattr(current, f"{kind}_url"):
                            setattr(user, f"{kind}_data", None)
                    for name in PROFILE_FIELDS:
                        setattr(user, name, getattr(current, name))
                    for kind in ("avatar", "cover"):
                        url = getattr(current, f"{kind}_url")
                        if url in prepared.images:
                            setattr(user, f"{kind}_data", prepared.images[url])
                started = time.monotonic()
                await session.commit()
                self.commit_seconds = time.monotonic() - started
            return "updated"

    async def run_job(self, player_id, mode):
        key = (player_id, mode)
        self.active[key] = time.monotonic()
        marker = background_request.set(True)
        try:
            try:
                async with asyncio.timeout(self.PROFILE_TIMEOUT):
                    result = await self._refresh(player_id, mode)
            except TimeoutError:
                result = "timeout"
            except Exception:
                logger.exception("Profile update failed: player_id=%s mode=%s", player_id, mode)
                result = "failed"
            self.counts[result] += 1
            if result in ("failed", "timeout"):
                failures = self.retries.get(key, (0, 0))[0] + 1
                delay = min(self.RETRY_MAX, self.RETRY_BASE * 2 ** min(failures - 1, 10))
                self.retries[key] = (failures, time.monotonic() + random.uniform(delay * .8, delay))
            else:
                self.retries.pop(key, None)
                if result == "updated":
                    self.last_success = utcnow().isoformat()
                    self.last_success_by_mode[mode] = self.last_success
            elapsed = time.monotonic() - self.active[key]
            self.durations.append(elapsed)
            logger.log(30 if result in ("failed", "timeout") else 10,
                       "Profile update: player_id=%s mode=%s outcome=%s seconds=%.2f",
                       player_id, mode, result, elapsed)
            return result
        finally:
            background_request.reset(marker)
            self.active.pop(key, None)

    async def _worker(self, mode):
        queue = self.queues[mode]
        while True:
            pid = await queue.get()
            self.last_wait_seconds = time.monotonic() - self.enqueued_at.pop((pid, mode), time.monotonic())
            try:
                await self.run_job(pid, mode)
            finally:
                self.pending.discard((pid, mode))
                queue.task_done()

    async def start_loop(self, shutdown_event):
        workers = [asyncio.create_task(self._worker(mode), name=f"profile-{mode}-{i}")
                   for i, mode in enumerate(("stats_only", "stats_only", "background_full"))]
        next_snapshot = 0.0
        next_report = 0.0
        next_trouble = 0.0
        expected_tick = time.monotonic()
        try:
            while not shutdown_event.is_set():
                self.scheduler_lag_seconds = max(0, time.monotonic() - expected_tick)
                for i, worker in enumerate(workers):
                    if worker.done():
                        logger.error("Profile worker stopped: %s", worker, exc_info=False)
                        if not worker.cancelled():
                            worker.exception()
                        mode = "background_full" if i == 2 else "stats_only"
                        workers[i] = asyncio.create_task(self._worker(mode), name=f"profile-{mode}-{i}")
                try:
                    async with asyncio.timeout(30):
                        await self.schedule()
                        if time.monotonic() >= next_snapshot:
                            async with AsyncSessionFactory() as session:
                                await ensure_period_snapshot(session)
                            next_snapshot = time.monotonic() + 300
                except Exception:
                    logger.exception("Profile scheduling failed")
                troubled, said = self.health_said(self.health())
                if time.monotonic() >= next_report or (troubled and time.monotonic() >= next_trouble):
                    logger.log(logging.WARNING if troubled else logging.INFO, "%s", said)
                    next_report = time.monotonic() + self.REPORT_EVERY
                    next_trouble = time.monotonic() + self.TROUBLE_EVERY
                expected_tick = time.monotonic() + self.TICK_SECONDS
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=self.TICK_SECONDS)
                except TimeoutError:
                    pass
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            for mode, queue in self.queues.items():
                while not queue.empty():
                    pid = queue.get_nowait()
                    self.pending.discard((pid, mode))
                    self.enqueued_at.pop((pid, mode), None)
                    queue.task_done()


async def periodic_profile_updates(api_client, shutdown_event):
    await ProfileUpdater(api_client).start_loop(shutdown_event)
