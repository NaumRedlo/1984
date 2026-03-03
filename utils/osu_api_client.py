import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Union

from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET
from utils.logger import get_logger

logger = get_logger("client.osu")

class OsuApiClient:
    BASE_URL = "https://osu.ppy.sh/api/v2"
    TOKEN_URL = "https://osu.ppy.sh/oauth/token"

    def __init__(self):
        self.token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()
        
        logger.info("Initializing osu! API client...")
        await self._ensure_token()

    async def _ensure_token(self):
        now = datetime.now(timezone.utc)
        
        if self.token and self.token_expiry and now < self.token_expiry - timedelta(minutes=5):
            return
        
        logger.info("Refreshing osu! API token...")
        try:
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
        except Exception as e:
            logger.critical(f"Critical failure during osu! API authentication: {e}", exc_info=True)
            raise

    async def _make_request(self, method: str, endpoint: str, params: Dict = None) -> Any:
        await self._ensure_token()
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        
        try:
            async with self.session.request(method, url, headers=headers, params=params) as resp:
                if resp.status == 404: return None
                if resp.status == 429:
                    logger.warning("API Rate limit exceeded!")
                    return None
                if resp.status != 200:
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"Network error during request to {endpoint}: {e}")
            return None

    async def get_user_data(self, user: Union[int, str], mode: str = "osu") -> Optional[Dict[str, Any]]:
        key_type = "id" if isinstance(user, int) or (isinstance(user, str) and user.isdigit()) else "username"
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
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            return False

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
