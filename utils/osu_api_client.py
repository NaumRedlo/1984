import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union
from functools import wraps

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
            "last_visit": data.get("last_visit")
        }

    async def get_user_recent_scores(self, user_id: int, limit: int = 1) -> List[Dict]:
        data = await self._make_request("GET", f"users/{user_id}/scores/recent", params={"limit": limit})
        return data if isinstance(data, list) else []

    async def update_user_in_db(self, session, user_model) -> bool:
        stats = await self.get_user_data(user_model.osu_user_id)
        if not stats: return False
        try:
            user_model.player_pp = int(stats.get("pp", 0))
            user_model.global_rank = stats.get("global_rank") or 0
            user_model.accuracy = round(float(stats.get("accuracy", 0.0)), 2)
            user_model.play_count = int(stats.get("play_count", 0))
            user_model.last_api_update = datetime.now(timezone.utc)
            user_model.rank = get_rank_for_hp(user_model.hps_points or 0)
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            return False
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
