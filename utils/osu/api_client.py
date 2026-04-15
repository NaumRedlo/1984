import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union
from functools import wraps
from urllib.parse import quote


from sqlalchemy import select, delete

from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET
from utils.logger import get_logger
from utils.hp_calculator import get_rank_for_hp

logger = get_logger("client.osu")


def with_retry(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        break
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        f"Attempt {attempt + 1}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
            logger.error(f"All {max_retries} attempts failed: {last_exception}")
            raise last_exception
        return wrapper
    return decorator


class OsuApiClient:
    BASE_URL = "https://osu.ppy.sh/api/v2"
    TOKEN_URL = "https://osu.ppy.sh/oauth/token"

    DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10)
    RATE_LIMIT_DELAY = 1.0

    def __init__(self):
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_request_time: float = 0
        self._request_lock = asyncio.Lock()

    async def initialize(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.DEFAULT_TIMEOUT)

        logger.info("Initializing osu! API client...")
        await self._ensure_token()

    @with_retry(max_retries=3, base_delay=2.0)
    async def _ensure_token(self):
        now = datetime.now(timezone.utc)

        if self.token and self.token_expiry and now < self.token_expiry - timedelta(minutes=5):
            return

        logger.info("Refreshing osu! API token...")
        data = {
            "client_id": OSU_CLIENT_ID,
            "client_secret": OSU_CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "public"
        }

        async with self.session.post(self.TOKEN_URL, data=data) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"Token request failed: {resp.status} - {error_text[:200]}")
                raise Exception(f"OAuth authentication failed with status {resp.status}")

            result = await resp.json()
            self.token = result["access_token"]
            expires_in = result.get("expires_in", 3600)
            self.token_expiry = now + timedelta(seconds=expires_in)
            logger.info(f"Token acquired successfully. Expires at: {self.token_expiry}")

    async def _rate_limit(self):
        async with self._request_lock:
            now = asyncio.get_running_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < self.RATE_LIMIT_DELAY:
                await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
            self._last_request_time = asyncio.get_running_loop().time()

    @with_retry(max_retries=3, base_delay=1.0)
    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        retry_on_429: bool = True
    ) -> Any:
        await self._ensure_token()
        await self._rate_limit()

        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        try:
            async with self.session.request(method, url, headers=headers, params=params) as resp:
                if resp.status == 404:
                    logger.debug(f"Resource not found: {endpoint}")
                    return None

                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "60"))
                    logger.warning(f"Rate limited. Waiting {retry_after}s before retry...")
                    if retry_after < 300:
                        await asyncio.sleep(retry_after)
                        raise aiohttp.ClientError(f"Rate limited, retrying after {retry_after}s")
                    return None

                if resp.status == 401:
                    logger.warning("Token expired or invalid, refreshing...")
                    self.token = None
                    self.token_expiry = None
                    await self._ensure_token()
                    raise aiohttp.ClientError("Token refreshed, retrying request")

                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"API error {resp.status} for {endpoint}: {error_text[:200]}")
                    return None

                return await resp.json()

        except aiohttp.ClientError as e:
            logger.error(f"Network error during request to {endpoint}: {e}")
            raise
        except asyncio.TimeoutError:
            logger.error(f"Timeout during request to {endpoint}")
            raise

    async def get_user_data(self, user: Union[int, str], mode: str = "osu") -> Optional[Dict[str, Any]]:
        if isinstance(user, str):
            user = quote(user, safe="")
        key_type = "id" if isinstance(user, int) else "username"
        data = await self._make_request("GET", f"users/{user}/{mode}", params={"key": key_type})
        if not data or "id" not in data: return None
            
        stats = data.get("statistics", {})
        return {
            "id": data.get("id"),
            "username": data.get("username"),
            "country_code": data.get("country", {}).get("code", "XX"),
            "pp": stats.get("pp", 0),
            "global_rank": stats.get("global_rank"),
            "accuracy": stats.get("hit_accuracy", 0.0),
            "play_count": stats.get("play_count", 0),
            "play_time": stats.get("play_time", 0),
            "ranked_score": stats.get("ranked_score", 0),
            "total_hits": stats.get("total_hits", 0),
            "total_score": stats.get("total_score", 0),
            "last_visit": data.get("last_visit"),
            "avatar_url": data.get("avatar_url"),
            "cover_url": data.get("cover", {}).get("url"),
        }

    async def get_user_extended_data(self, user: Union[int, str], mode: str = "osu") -> Optional[Dict[str, Any]]:
        """Like get_user_data but also returns rank_history and monthly_playcounts."""
        if isinstance(user, str):
            user = quote(user, safe="")
        key_type = "id" if isinstance(user, int) else "username"
        data = await self._make_request("GET", f"users/{user}/{mode}", params={"key": key_type})
        if not data or "id" not in data:
            return None

        stats = data.get("statistics", {})
        level = stats.get("level", {})
        return {
            "id": data.get("id"),
            "username": data.get("username"),
            "country_code": data.get("country", {}).get("code", "XX"),
            "pp": stats.get("pp", 0),
            "global_rank": stats.get("global_rank"),
            "accuracy": stats.get("hit_accuracy", 0.0),
            "play_count": stats.get("play_count", 0),
            "play_time": stats.get("play_time", 0),
            "ranked_score": stats.get("ranked_score", 0),
            "total_hits": stats.get("total_hits", 0),
            "total_score": stats.get("total_score", 0),
            "last_visit": data.get("last_visit"),
            "avatar_url": data.get("avatar_url"),
            "cover_url": data.get("cover", {}).get("url"),
            "level": level.get("current", 0),
            "level_progress": level.get("progress", 0),
            "country_rank": stats.get("country_rank"),
            "rank_history": data.get("rank_history", {}).get("data", []),
            "monthly_playcounts": data.get("monthly_playcounts", []),
        }

    async def get_user_recent_scores(self, user_id: int, limit: int = 1) -> List[Dict]:
        data = await self._make_request("GET", f"users/{user_id}/scores/recent", params={"limit": limit, "include_fails": 1})
        return data if isinstance(data, list) else []

    async def sync_user_stats_from_api(self, user_model) -> bool:
        """Fetch fresh stats from osu! API and mutate user_model. Caller must commit."""
        stats = await self.get_user_data(user_model.osu_user_id)
        if not stats:
            return False
        user_model.player_pp = int(stats.get("pp", 0))
        user_model.global_rank = stats.get("global_rank") or 0
        user_model.accuracy = round(float(stats.get("accuracy", 0.0)), 2)
        user_model.play_count = int(stats.get("play_count", 0))
        user_model.play_time = int(stats.get("play_time", 0))
        user_model.ranked_score = int(stats.get("ranked_score", 0))
        user_model.total_hits = int(stats.get("total_hits", 0))
        user_model.total_score = int(stats.get("total_score", 0))

        new_avatar_url = stats.get("avatar_url")
        new_cover_url = stats.get("cover_url")

        # Cache avatar bytes if URL changed or cache is empty
        if new_avatar_url and (new_avatar_url != user_model.avatar_url or not user_model.avatar_data):
            user_model.avatar_data = await self._download_image_bytes(new_avatar_url)

        # Cache cover bytes if URL changed or cache is empty
        if new_cover_url and (new_cover_url != user_model.cover_url or not user_model.cover_data):
            user_model.cover_data = await self._download_image_bytes(new_cover_url)

        user_model.avatar_url = new_avatar_url
        user_model.cover_url = new_cover_url
        user_model.last_api_update = datetime.now(timezone.utc)
        user_model.rank = get_rank_for_hp(user_model.hps_points or 0)
        return True

    async def _download_image_bytes(self, url: str, timeout: float = 5.0) -> Optional[bytes]:
        """Download image from URL and return raw bytes, or None on failure."""
        if not url:
            return None
        try:
            if not self.session or self.session.closed:
                await self.initialize()
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except Exception as e:
            logger.debug(f"Failed to download image bytes {url}: {e}")
            return None

    async def sync_user_best_scores(self, user_model, session) -> bool:
        """Sync top-100 best scores for a user. Caller must commit."""
        from db.models.best_score import UserBestScore

        raw_scores = await self.get_user_best_scores(user_model.osu_user_id, limit=100)
        if not raw_scores:
            return False

        # Build lookup of existing scores
        stmt = select(UserBestScore).where(UserBestScore.user_id == user_model.id)
        result = await session.execute(stmt)
        existing = {s.score_id: s for s in result.scalars().all()}

        incoming_ids = set()
        for raw in raw_scores:
            score_id = raw.get("id")
            if not score_id:
                continue
            incoming_ids.add(score_id)

            beatmapset = raw.get("beatmapset") or {}
            beatmap = raw.get("beatmap") or {}
            mods_list = raw.get("mods", [])
            mods_str = ",".join(str(m) if isinstance(m, str) else str(m.get("acronym", "")) for m in mods_list) if mods_list else None

            pp_val = raw.get("pp") or 0.0
            acc_val = raw.get("accuracy")
            if acc_val is not None:
                acc_val = round(acc_val * 100, 2)

            score_val = raw.get("total_score")
            if score_val is None:
                score_val = raw.get("legacy_total_score")
            if score_val is None:
                score_val = raw.get("score")

            star_rating = beatmap.get("difficulty_rating")
            if star_rating is not None:
                star_rating = float(star_rating)

            if score_id in existing:
                score_obj = existing[score_id]
                score_obj.score = score_val
                # Always update beatmapset_id if missing
                if not score_obj.beatmapset_id and beatmapset.get("id"):
                    score_obj.beatmapset_id = beatmapset.get("id")
                # Always backfill star_rating if missing
                if score_obj.star_rating is None and star_rating is not None:
                    score_obj.star_rating = star_rating
                if abs((score_obj.pp or 0) - pp_val) > 0.01:
                    score_obj.pp = pp_val
                    score_obj.accuracy = acc_val
                    score_obj.max_combo = raw.get("max_combo")
                    score_obj.rank = raw.get("rank")
                    score_obj.mods = mods_str
                    if star_rating is not None:
                        score_obj.star_rating = star_rating
            else:
                new_score = UserBestScore(
                    user_id=user_model.id,
                    score_id=score_id,
                    beatmap_id=beatmap.get("id", 0),
                    beatmapset_id=beatmapset.get("id"),
                    score=score_val,
                    pp=pp_val,
                    accuracy=acc_val,
                    max_combo=raw.get("max_combo"),
                    rank=raw.get("rank"),
                    mods=mods_str,
                    artist=beatmapset.get("artist", ""),
                    title=beatmapset.get("title", ""),
                    version=beatmap.get("version", ""),
                    creator=beatmapset.get("creator", ""),
                    star_rating=star_rating,
                )
                session.add(new_score)

        # Remove scores that fell out of top-100
        stale_ids = set(existing.keys()) - incoming_ids
        if stale_ids:
            await session.execute(
                delete(UserBestScore).where(
                    UserBestScore.user_id == user_model.id,
                    UserBestScore.score_id.in_(stale_ids)
                )
            )

        logger.debug(f"Synced best scores for {user_model.osu_username}: {len(incoming_ids)} current, {len(stale_ids)} removed")
        return True

    async def sync_user_map_attempts(self, user_model, session, raw_scores: List[Dict]) -> int:
        """Persist map attempts for a user without deleting older history."""
        from db.models.map_attempt import UserMapAttempt

        if not raw_scores:
            return 0

        incoming_ids = []
        normalized_scores = []
        for raw in raw_scores:
            score_id = raw.get("id")
            beatmap = raw.get("beatmap") or {}
            beatmapset = raw.get("beatmapset") or {}
            beatmap_id = beatmap.get("id")
            pp_val = raw.get("pp")
            if not score_id or beatmap_id is None or pp_val is None:
                continue
            incoming_ids.append(score_id)
            normalized_scores.append((raw, beatmap, beatmapset, score_id, beatmap_id, float(pp_val or 0.0)))

        if not incoming_ids:
            return 0

        stmt = select(UserMapAttempt).where(
            UserMapAttempt.user_id == user_model.id,
            UserMapAttempt.score_id.in_(incoming_ids),
        )
        result = await session.execute(stmt)
        existing = {row.score_id: row for row in result.scalars().all()}

        synced = 0
        for raw, beatmap, beatmapset, score_id, beatmap_id, pp_val in normalized_scores:
            mods_list = raw.get("mods", [])
            mods_str = ",".join(
                str(m) if isinstance(m, str) else str(m.get("acronym", ""))
                for m in mods_list
                if m
            ) if mods_list else None

            acc_val = raw.get("accuracy")
            if acc_val is not None:
                acc_val = round(float(acc_val) * 100, 2)

            score_val = raw.get("total_score")
            if score_val is None:
                score_val = raw.get("legacy_total_score")
            if score_val is None:
                score_val = raw.get("score")

            star_rating = beatmap.get("difficulty_rating")
            if star_rating is not None:
                star_rating = float(star_rating)

            attrs = {
                "beatmap_id": beatmap_id,
                "beatmapset_id": beatmapset.get("id"),
                "score": score_val,
                "pp": pp_val,
                "accuracy": acc_val,
                "max_combo": raw.get("max_combo"),
                "rank": raw.get("rank"),
                "mods": mods_str,
                "artist": beatmapset.get("artist", ""),
                "title": beatmapset.get("title", ""),
                "version": beatmap.get("version", ""),
                "creator": beatmapset.get("creator", ""),
                "star_rating": star_rating,
            }

            attempt = existing.get(score_id)
            if attempt:
                for key, value in attrs.items():
                    setattr(attempt, key, value)
            else:
                session.add(UserMapAttempt(user_id=user_model.id, score_id=score_id, **attrs))
            synced += 1

        logger.debug(f"Synced map attempts for {user_model.osu_username}: {synced} rows")
        return synced

    async def get_user_best_scores(self, user_id: int, limit: int = 5, mode: str = "osu") -> List[Dict]:
        data = await self._make_request(
            "GET",
            f"users/{user_id}/scores/best",
            params={"mode": mode, "limit": limit}
        )
        return data if isinstance(data, list) else []

    async def get_beatmap_scores(self, beatmap_id: int, limit: int = 50) -> List[Dict]:
        data = await self._make_request(
            "GET",
            f"beatmaps/{beatmap_id}/scores",
            params={"limit": limit}
        )
        return data.get("scores", []) if isinstance(data, dict) else []

    async def get_beatmap(self, beatmap_id: Union[int, str]) -> Optional[Dict]:
        logger.debug(f"Fetching beatmap data for ID: {beatmap_id}")
        return await self._make_request("GET", f"beatmaps/{beatmap_id}")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

__all__ = ["OsuApiClient"]
