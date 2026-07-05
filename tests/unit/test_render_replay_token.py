"""Replay-download token priority (bot/handlers/profile/render._resolve_replay_token):
requester's own OAuth token → shared service token → None (app-token fallback).
osu! only serves replays to a user token, so this is what decides render coverage."""


from bot.handlers.profile import render


def _fake_tokens(mapping):
    """An async stand-in for OsuApiClient.try_get_oauth_token backed by a dict."""
    async def _get(telegram_id):
        return mapping.get(telegram_id)
    return _get


async def test_prefers_requester_token(monkeypatch):
    monkeypatch.setattr(render.OsuApiClient, "try_get_oauth_token",
                        _fake_tokens({111: "req-tok", 999: "svc-tok"}))
    monkeypatch.setattr(render, "RENDER_SERVICE_OAUTH_TG_ID", 999)
    assert await render._resolve_replay_token(111) == "req-tok"


async def test_falls_back_to_service_token(monkeypatch):
    # Requester has no linked account -> use the service account's token.
    monkeypatch.setattr(render.OsuApiClient, "try_get_oauth_token",
                        _fake_tokens({999: "svc-tok"}))
    monkeypatch.setattr(render, "RENDER_SERVICE_OAUTH_TG_ID", 999)
    assert await render._resolve_replay_token(111) == "svc-tok"


async def test_none_when_service_disabled(monkeypatch):
    # No requester token and no service account configured -> None (app token).
    monkeypatch.setattr(render.OsuApiClient, "try_get_oauth_token",
                        _fake_tokens({}))
    monkeypatch.setattr(render, "RENDER_SERVICE_OAUTH_TG_ID", 0)
    assert await render._resolve_replay_token(111) is None


async def test_none_when_service_has_no_token(monkeypatch):
    # Service account configured but its token is gone -> None (app token).
    monkeypatch.setattr(render.OsuApiClient, "try_get_oauth_token",
                        _fake_tokens({}))
    monkeypatch.setattr(render, "RENDER_SERVICE_OAUTH_TG_ID", 999)
    assert await render._resolve_replay_token(111) is None
