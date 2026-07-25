"""Renames follow through to the stored username
(utils/osu/api_client.sync_user_stats_from_api).

osu! usernames change but the numeric id doesn't, and every lookup goes through
the id — so the stored name silently went stale. It's the name printed on every
card and the one `cmp <nick>` matches against, so a stale one makes the player
unfindable under the name they actually use now.
"""

from db.models.user import User
from utils.osu.api_client import OsuApiClient


def _stats(username: str) -> dict:
    return {
        "username": username, "pp": 1234, "global_rank": 500, "accuracy": 98.5,
        "play_count": 1000, "play_time": 3600, "ranked_score": 10**6,
        "total_hits": 400_000, "total_score": 10**7, "is_supporter": False,
        "level": 50, "grade_counts": {"s": 1, "sh": 0, "ss": 0, "ssh": 0},
    }


def _client(monkeypatch, username: str) -> OsuApiClient:
    client = OsuApiClient()

    async def fake_get_user_data(user, mode="osu", oauth_token=None):
        return _stats(username)

    async def fake_download(*a, **kw):
        return None

    monkeypatch.setattr(client, "get_user_data", fake_get_user_data)
    monkeypatch.setattr(client, "_download_image_bytes", fake_download)
    return client


def _user(name: str) -> User:
    return User(chat_id=-100, telegram_id=1, osu_username=name, osu_user_id=42)


async def test_rename_is_picked_up(monkeypatch):
    user = _user("OldName")
    client = _client(monkeypatch, "BrandNewName")

    assert await client.sync_user_stats_from_api(user) is True
    assert user.osu_username == "BrandNewName"
    assert user.osu_user_id == 42          # the id is what we matched on


async def test_capitalisation_change_counts_as_a_rename(monkeypatch):
    """osu! lets you restyle your own name's case; the card should follow."""
    user = _user("naumredlo")
    client = _client(monkeypatch, "NaumRedlo")

    await client.sync_user_stats_from_api(user)
    assert user.osu_username == "NaumRedlo"


async def test_unchanged_name_is_left_alone(monkeypatch):
    user = _user("SameName")
    client = _client(monkeypatch, "SameName")

    await client.sync_user_stats_from_api(user)
    assert user.osu_username == "SameName"


async def test_missing_or_blank_username_never_wipes_the_stored_one(monkeypatch):
    """A malformed payload must not blank out a name we already have."""
    for payload_name in ("", "   ", None):
        user = _user("Keeper")
        client = OsuApiClient()

        async def fake_get_user_data(u, mode="osu", oauth_token=None, _n=payload_name):
            stats = _stats("ignored")
            stats["username"] = _n
            return stats

        async def fake_download(*a, **kw):
            return None

        monkeypatch.setattr(client, "get_user_data", fake_get_user_data)
        monkeypatch.setattr(client, "_download_image_bytes", fake_download)

        await client.sync_user_stats_from_api(user)
        assert user.osu_username == "Keeper"
