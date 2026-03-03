# utils/osu_api_client.py
"""
osu! API v2 Client
Handles authentication and data fetching from osu! API.
"""

import aiohttp
import asyncio
from datetime import datetime, timedelta, timezone
from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET
from utils.logger import get_logger
import pytz

logger = get_logger("client.osu")


class OsuApiClient:
    """Client for interacting with osu! API v2."""
    
    API_URL = "https://osu.ppy.sh/api/v2"
    
    def __init__(self):
        self.token = None
        self.token_expiry = None
        self.session = None
    
    async def initialize(self):
        """Initializes aiohttp session and fetches access token."""
        logger.info("Initializing osu! API client...")
        
        self.session = aiohttp.ClientSession()
        await self._ensure_token()
        logger.info(f"Token initialized until {self.token_expiry}")
    
    async def _ensure_token(self):
        """Ensures valid token exists, refreshes if expired."""
        now = datetime.now(timezone.utc)
        
        if self.token and self.token_expiry and now < self.token_expiry - timedelta(minutes=5):
            logger.debug("Token is still valid")
            return
        
        logger.info("Refreshing osu! API token...")
        try:
            url = "https://osu.ppy.sh/oauth/token"
            data = {
                "client_id": OSU_CLIENT_ID,
                "client_secret": OSU_CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "public"
            }
            
            async with self.session.post(url, data=data) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Token request failed: {resp.status} - {error_text[:200]}")
                    raise Exception(f"Token request failed: {resp.status}")
                
                result = await resp.json()
                self.token = result["access_token"]
                
                # Calculate expiry (typically 3600 seconds)
                expires_in = result.get("expires_in", 3600)
                self.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                
                logger.info(f"Token acquired successfully. Expires at: {self.token_expiry}")
                
        except Exception as e:
            logger.critical(f"Failed to authenticate with osu! API: {e}", exc_info=True)
            raise
    
    async def get_user_stats(self, user_id: int, mode: str = "osu") -> dict:
        """
        Get full user statistics from osu! API v2.
        
        Args:
            user_id: osu! user ID
            mode: "osu", "taiko", "fruits", "mania"
        
        Returns:
            dict with stats or empty dict on failure
        """
        await self._ensure_token()
        
        url = f"{self.API_URL}/users/{user_id}/{mode}"
        logger.debug(f"Fetching stats for user_id: {user_id}")
        
        try:
            async with self.session.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                
                logger.info(f"API Response Status: {resp.status} for user_id {user_id}")
                
                if resp.status == 401:
                    logger.error("Token unauthorized - may need re-authentication")
                    return {}
                elif resp.status == 404:
                    logger.error(f"User not found on osu!: {user_id}")
                    return {}
                elif resp.status == 429:
                    logger.error("Rate limit exceeded!")
                    return {}
                elif resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Unexpected status {resp.status}: {error_text[:200]}")
                    return {}
                
                data = await resp.json()
                
                if "error" in data:
                    logger.error(f"API returned error: {data['error']}")
                    return {}
                
                logger.info(f"Successfully retrieved stats for user_id {user_id}")
                
                # Extract data
                statistics = data.get("statistics", {})
                country = data.get("country", {})
                
                return {
                    "pp": statistics.get("pp", 0),
                    "global_rank": statistics.get("global_rank", 0),
                    "country_rank": statistics.get("country_rank", 0),
                    "country_code": country.get("code", "XX"),
                    "country_name": country.get("name", "Unknown"),
                    "accuracy": statistics.get("hit_accuracy", 0.0),
                    "play_count": statistics.get("play_count", 0),
                    "play_time": statistics.get("play_time", 0),
                    "ranked_score": statistics.get("ranked_score", 0),
                    "total_score": statistics.get("total_score", 0),
                    "level": statistics.get("level", {}).get("current", 0),
                    "max_combo": statistics.get("max_combo", 0),
                    "total_hits": statistics.get("total_hits", 0),
                    "replays_watched": statistics.get("replays_watched_by_others", 0),
                    "is_ranked": statistics.get("is_ranked", False),
                    "total_ss": statistics.get("grade_counts", {}).get("ss", 0),
                    "total_s": statistics.get("grade_counts", {}).get("s", 0),
                    "total_a": statistics.get("grade_counts", {}).get("a", 0),
                    "username": data.get("username", ""),
                    "last_visit": data.get("last_visit"),
                }
                
        except aiohttp.ClientError as e:
            logger.error(f"Network error fetching stats: {e}")
            return {}
        except Exception as e:
            logger.exception(f"Unexpected error in get_user_stats: {e}")
            return {}
    
    async def get_user_recent_scores(self, user_id: int, limit: int = 1) -> list:
        """
        Get recent scores from osu! API.
        
        Args:
            user_id: osu! user ID
            limit: Number of scores to retrieve
        
        Returns:
            List of score dictionaries
        """
        await self._ensure_token()
        
        url = f"{self.API_URL}/users/{user_id}/scores/recent?limit={limit}&include_fails=0"
        logger.debug(f"Fetching recent scores for user_id: {user_id}")
        
        try:
            async with self.session.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                
                if resp.status != 200:
                    logger.error(f"Failed to get recent scores: {resp.status}")
                    return []
                
                return await resp.json()
                
        except Exception as e:
            logger.error(f"Error fetching recent scores: {e}")
            return []
    
    async def get_beatmap(self, beatmap_id: str) -> dict:
        """
        Get beatmap data from osu! API.
        
        Args:
            beatmap_id: osu! beatmap ID
        
        Returns:
            Dict with beatmap data or empty dict on failure
        """
        await self._ensure_token()
        
        url = f"{self.API_URL}/beatmaps/{beatmap_id}"
        logger.debug(f"Fetching beatmap data for id: {beatmap_id}")
        
        try:
            async with self.session.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                
                if resp.status != 200:
                    logger.warning(f"Failed to get beatmap: {resp.status}")
                    return {}
                
                return await resp.json()
                
        except Exception as e:
            logger.error(f"Error fetching beatmap: {e}")
            return {}
    
    async def update_user_in_db(self, session, user) -> bool:
        """
        Update user data in database from osu! API.
        
        Args:
            session: SQLAlchemy session
            user: User object to update
        
        Returns:
            True if successful, False if error
        """
        if not user.osu_user_id:
            logger.error(f"Cannot update user {user.osu_username}: missing osu! user ID")
            return False
        
        try:
            stats = await self.get_user_stats(user.osu_user_id)
            
            if not stats:
                logger.error(f"Empty stats received for user {user.osu_username} (ID: {user.osu_user_id})")
                return False
            
            # Check required fields
            required_fields = ["pp", "global_rank", "country_code", "hit_accuracy", "play_count"]
            missing = [f for f in required_fields if f not in stats]
            
            if missing:
                logger.warning(f"Missing fields in API response for {user.osu_username}: {missing}")
            
            # Update fields
            user.player_pp = int(stats.get("pp", 0))
            user.global_rank = stats.get("global_rank", 0)
            user.country = stats.get("country_code", "XX")
            user.accuracy = round(stats.get("accuracy", stats.get("hit_accuracy", 0.0)), 2)
            user.play_count = stats.get("play_count", 0)
            user.last_api_update = datetime.now(timezone.utc)
            
            await session.commit()
            
            logger.info(f"Successfully updated {user.osu_username}: {user.player_pp} PP, #{user.global_rank} rank")
            return True
            
        except Exception as e:
            logger.critical(f"Exception during update for user {user.username}: {e}", exc_info=True)
            return False
    
    async def close(self):
        """Close aiohttp session."""
        if self.session:
            await self.session.close()
            logger.info("osu! API client session closed")


__all__ = ["OsuApiClient"]
