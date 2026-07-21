"""SSRF / validation guards for URL-based skin upload
(bot/handlers/profile/render._is_public_host / _download_osk_from_url)."""

from bot.handlers.profile import render as r


def _gai(ip):
    # Stand-in for socket.getaddrinfo returning a single resolved IP (no DNS).
    return lambda host, *a, **k: [(2, 1, 6, "", (ip, 0))]


def test_public_host_guard(monkeypatch):
    monkeypatch.setattr(r.skin_handlers.socket, "getaddrinfo", _gai("10.0.0.1"))
    assert r._is_public_host("evil.internal") is False      # private
    monkeypatch.setattr(r.skin_handlers.socket, "getaddrinfo", _gai("127.0.0.1"))
    assert r._is_public_host("loopback") is False
    monkeypatch.setattr(r.skin_handlers.socket, "getaddrinfo", _gai("169.254.1.1"))
    assert r._is_public_host("linklocal") is False
    monkeypatch.setattr(r.skin_handlers.socket, "getaddrinfo", _gai("1.1.1.1"))
    assert r._is_public_host("cdn.example") is True          # public


async def test_download_rejects_bad_scheme():
    data, err = await r._download_osk_from_url("ftp://host/skin.osk")
    assert data is None and "http" in err.lower()


async def test_download_rejects_private_host(monkeypatch):
    monkeypatch.setattr(r.skin_handlers.socket, "getaddrinfo", _gai("127.0.0.1"))
    data, err = await r._download_osk_from_url("http://internal-service/skin.osk")
    assert data is None and err == "Invalid address."
    # localised error text, threaded through explicitly (no DB lookup here)
    data, err_ru = await r._download_osk_from_url("http://internal-service/skin.osk", "ru")
    assert data is None and err_ru == "Недопустимый адрес."
