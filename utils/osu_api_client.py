import aiohttp
import time
import asyncio
from datetime import datetime
from config.settings import OSU_CLIENT_ID, OSU_CLIENT_SECRET

class OsuApiClient:
    BASE_URL = "https://osu.ppy.sh/api/v2"
    TOKEN_URL = "https://osu.ppy.sh/oauth/token"

    def __init__(self):
        self.token = None
        self.token_expires_at = 0
        self.client_id = OSU_CLIENT_ID
        self.client_secret = OSU_CLIENT_SECRET
        self.session = None
        self.connector_limit = 10
        self.timeout = aiohttp.ClientTimeout(total=10)

    async def initialize(self):
        """Initializes aiohttp.ClientSession."""
        if not self.session:
            connector = aiohttp.TCPConnector(limit=self.connector_limit)
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=self.timeout
            )
            print("OsuApiClient: aiohttp session initialized.")

    async def close(self):
        """Closes the session."""
        if self.session:
            await self.session.close()
            print("OsuApiClient: aiohttp session closed.")

    async def _ensure_token(self):
        """Ensures the token is valid, refreshing if necessary."""
        if not self.session:
            raise RuntimeError("OsuApiClient not initialized. Call initialize().")

        now = time.time()
        if not self.token or now >= self.token_expires_at:
            print("Refreshing osu! API token...") # Debug log
            await self._refresh_token()
            # Optional: Add a tiny delay after token refresh
            # await asyncio.sleep(0.1) # Very short delay, maybe 0.1 seconds

    async def _refresh_token(self):
        """Fetches a new OAuth token using Client Credentials."""
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials", # Use client_credentials
            "scope": "public" # Ensure 'public' scope is requested
        }

        try:
            print(f"Requesting token from {self.TOKEN_URL}") # Debug log
            async with self.session.post(self.TOKEN_URL, data=payload) as resp:
                print(f"Token request status: {resp.status}") # Debug log
                if resp.status != 200:
                    text = await resp.text()
                    print(f"Token request failed: {resp.status} - {text}") # Debug log
                    raise Exception(f"Failed to get token: {resp.status} - {text}")

                data = await resp.json()
                self.token = data.get("access_token") # Get the token string
                if not self.token:
                    raise Exception("Response did not contain 'access_token'")

                # Calculate expiration time based on 'expires_in' (usually 86400 seconds = 24 hours)
                expires_in = data.get("expires_in", 86400)
                # Get the current time NOW
                now = time.time()
                # Refresh slightly before expiry (e.g., 1 minute = 60 seconds)
                self.token_expires_at = now + expires_in - 60

                print(f"New token acquired successfully. Expires at: {time.ctime(self.token_expires_at)}") # Debug log

        except aiohttp.ClientError as e:
            print(f"AIOHTTP error during token refresh: {e}") # Debug log
            raise
        except Exception as e:
            print(f"General error during token refresh: {e}") # Debug log
            raise


    # async def get_user_by_name(self, username: str):
    #     """Fetches user data by username using the API."""

    async def get_user_by_name(self, username: str):
        """
        Retrieves user data by name using the API.
        Attempts to retrieve data by passing the name directly to the endpoint /users/{user_id_or_username}.
        This may be more compatible with the client_credentials token.
        """
        await self._ensure_token() # Ensure token is valid before making request

        if not self.token:
            raise RuntimeError("Token is not available after ensure_token.")

        url = f"{self.BASE_URL}/users/{username}"
        headers = {
            "Authorization": f"Bearer {self.token}", # Use Bearer token
            "Content-Type": "application/json" # Sometimes helps, though not always required
        }

        print(f"Making request to {url}") # Debug log
        try:
            async with self.session.get(url, headers=headers) as resp:
                print(f"User request status: {resp.status}") # Debug log
                if resp.status == 200:
                    user_data = await resp.json()
                    print(f"Successfully retrieved user data for '{username}': {user_data.get('id')}") # Debug log
                    return user_data
                elif resp.status == 404:
                    print(f"User '{username}' not found via API.") # Debug log
                    return None
                elif resp.status == 401:
                    error_text = await resp.text()
                    print(f"Unauthorized (401) accessing user '{username}': {error_text}") # Debug log
                    # Log headers sent for inspection (careful with real tokens!)
                    print(f"Headers sent: {headers}") # Be careful with printing token!
                    # Potentially clear the token and force a refresh next time
                    self.token = None
                    self.token_expires_at = 0
                    raise Exception(f"API returned 401 Unauthorized for user {username}. Token might be invalid or scopes insufficient. Error: {error_text}")
                else:
                    error_text = await resp.text()
                    print(f"Error ({resp.status}) getting user '{username}': {error_text}") # Debug log
                    return None
        except aiohttp.ClientError as e:
            print(f"AIOHTTP error getting user '{username}': {e}") # Debug log
            return None
        except Exception as e:
            print(f"General error getting user '{username}': {e}") # Debug log
            return None


    async def get_beatmap_by_id(self, beatmap_id: int):
        """Fetches beatmap data by ID."""
        await self._ensure_token()
        if not self.token:
            raise RuntimeError("Token is not available after ensure_token.")

        url = f"{self.BASE_URL}/beatmaps/{beatmap_id}"
        headers = {"Authorization": f"Bearer {self.token}"}

        print(f"Making request to {url}") # Debug log
        try:
            async with self.session.get(url, headers=headers) as resp:
                print(f"Beatmap request status: {resp.status}") # Debug log
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 404:
                    print(f"Beatmap ID {beatmap_id} not found via API.") # Debug log
                    return None
                elif resp.status == 401:
                    error_text = await resp.text()
                    print(f"Unauthorized (401) accessing beatmap {beatmap_id}: {error_text}") # Debug log
                    self.token = None
                    self.token_expires_at = 0
                    raise Exception(f"API returned 401 for beatmap {beatmap_id}. Error: {error_text}")
                else:
                    error_text = await resp.text()
                    print(f"Error ({resp.status}) getting beatmap {beatmap_id}: {error_text}") # Debug log
                    return None
        except Exception as e:
            print(f"Error getting beatmap {beatmap_id}: {e}") # Debug log
            return None

    async def get_user_stats(self, user_id: int, mode: str = "osu") -> dict:
        """
        Retrieves complete user statistics from the osu! API v2.
        
        Args:
            user_id: osu! user ID
            mode: "osu", "taiko", "fruits", "mania"
        
        Returns:
            dict with data: pp, global_rank, country, accuracy, play_count and so on.
        """
        await self._ensure_token()
        
        url = f"{self.BASE_URL}/users/{user_id}/{mode}"
        
        try:
            async with self.session.get(
                url,
                headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                if resp.status != 200:
                    print(f"Failed to get user stats: {resp.status}")
                    return {}
                
                data = await resp.json()
                
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
                
        except Exception as e:
            print(f"Error fetching user stats: {e}")
            return {}


    async def update_user_in_db(self, session, user) -> bool:
        """
        Updates user data in the database from the osu! API.
        
        Returns:
            True if successful, False if error
        """
        stats = await self.get_user_stats(user.osu_user_id)
        
        if not stats:
            return False
        
        user.player_pp = int(stats.get("pp", 0))
        user.global_rank = stats.get("global_rank", 0)
        user.country = stats.get("country_code", "XX")
        user.accuracy = round(stats.get("accuracy", 0.0), 2)
        user.play_count = stats.get("play_count", 0)
        user.last_api_update = datetime.utcnow()
        
        await session.commit()
        
        return True
