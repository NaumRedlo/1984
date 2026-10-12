import asyncio
import time

from utils.osu.api_client import OsuApiClient


class PreparedProfile:
    sync_user_best_scores = OsuApiClient.sync_user_best_scores
    fill_ranked_dates = OsuApiClient.fill_ranked_dates
    _fill_ranked_dates_quietly = OsuApiClient._fill_ranked_dates_quietly

    def __init__(self, stats=None, scores=None, ratings=None, dates=None, images=None):
        self.stats = stats
        self.scores = scores
        self.ratings = ratings or {}
        self.dates = dates or {}
        self.images = images or {}

    async def sync_user_stats_from_api(self, user, oauth_token=None):
        if self.stats is None:
            return True
        return await OsuApiClient.sync_user_stats_from_api(self, user, oauth_token)

    async def get_user_data(self, *args, **kwargs):
        return self.stats

    async def get_user_best_scores(self, *args, **kwargs):
        return self.scores

    async def effective_sr(self, beatmap_id, mods, nominal, checksum=None):
        return self.ratings.get((beatmap_id, repr(mods)))

    async def ranked_dates(self, beatmap_ids):
        return {bid: self.dates[bid] for bid in beatmap_ids if bid in self.dates}

    async def _download_image_bytes(self, url):
        return self.images.get(url)

    @classmethod
    async def fetch(cls, api, player, mode, token, existing_ratings, dated_maps, picture_state):
        from services.refresh.policy import needs_stats_sweep

        stats = None
        if mode == "stats_only" or needs_stats_sweep(player.last_api_update):
            stats = await api.get_user_data(player.osu_user_id, oauth_token=token)
            if not stats or stats.get("id") != player.osu_user_id:
                raise ValueError("Profile response missing or belongs to another player")
        prepared = cls(stats=stats)
        if mode == "stats_only":
            return prepared
        prepared.scores = await api.get_user_best_scores(player.osu_user_id, limit=100, oauth_token=token)
        if not isinstance(prepared.scores, list) or any(not isinstance(s, dict) or not s.get("id") for s in prepared.scores):
            raise ValueError("Invalid best scores response")
        enrichment_deadline = time.monotonic() + 20
        for score in prepared.scores:
            beatmap = score.get("beatmap") or {}
            if existing_ratings.get(score["id"]) is None:
                mods = score.get("mods", [])
                key = (beatmap.get("id"), repr(mods))
                remaining = enrichment_deadline - time.monotonic()
                if key not in prepared.ratings and remaining > 0:
                    try:
                        async with asyncio.timeout(min(5, remaining)):
                            prepared.ratings[key] = await api.effective_sr(
                                beatmap.get("id"), mods, beatmap.get("difficulty_rating"), beatmap.get("checksum"))
                    except Exception:
                        prepared.ratings[key] = None
            if beatmap.get("id") and not (score.get("beatmapset") or {}).get("ranked_date"):
                dated_maps.add(beatmap["id"])
        try:
            async with asyncio.timeout(10):
                prepared.dates = await api.ranked_dates(dated_maps)
        except Exception:
            prepared.dates = {}
        for kind in ("avatar", "cover"):
            url = (stats or {}).get(f"{kind}_url", getattr(player, f"{kind}_url"))
            if url and any(old_url != url or not present for old_url, present in picture_state[kind]):
                try:
                    async with asyncio.timeout(5):
                        data = await api._download_image_bytes(url)
                except Exception:
                    data = None
                if data is not None:
                    prepared.images[url] = data
        return prepared
