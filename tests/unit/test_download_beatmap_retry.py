"""download_beatmap's retry loop (utils/osu/danser_renderer). 2026-07-03
incident: a real, available beatmapset (2539465) failed "from all mirrors"
right after a fresh worker boot -- narrowed to a single mirror (osu.direct)
as a diagnostic experiment, which meant it needed its own retry resilience
since there's no second mirror left to fall back on. Uses requests (via
asyncio.to_thread), not aiohttp/httpx -- both async clients failed
tunneling HTTPS through the render worker's required outbound proxy;
requests does the proxy CONNECT + TLS the traditional blocking way, like
curl, and works fine through that exact proxy."""

from unittest.mock import patch

from utils.osu import danser_renderer as dr


class _FakeResp:
    def __init__(self, status_code, content=b"PK" + b"x" * 2000):
        self.status_code = status_code
        self.content = content


def _patch_get(outcomes):
    """Returns responses/raises exceptions from `outcomes` in order, one per
    requests.get() call, regardless of URL -- fine since _BEATMAP_MIRRORS is
    a single entry for this test."""
    remaining = list(outcomes)

    def fake_get(*a, **kw):
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return patch("utils.osu.danser_renderer.requests.get", fake_get)


async def test_already_downloaded_short_circuits_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "DANSER_SONGS_DIR", str(tmp_path))
    (tmp_path / "42 Some Set").mkdir()

    with _patch_get([RuntimeError("must not be called")]):
        assert await dr.download_beatmap(42) is True


async def test_succeeds_on_first_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "DANSER_SONGS_DIR", str(tmp_path))
    with _patch_get([_FakeResp(200)]):
        assert await dr.download_beatmap(99) is True
    assert (tmp_path / "99.osz").is_file()


async def test_retries_after_transient_failure_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "DANSER_SONGS_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 3)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    # First pass: connection error. Second pass: succeeds.
    with _patch_get([ConnectionError("network not ready"), _FakeResp(200)]):
        assert await dr.download_beatmap(7) is True
    assert (tmp_path / "7.osz").is_file()


async def test_gives_up_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "DANSER_SONGS_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 3)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    with _patch_get([_FakeResp(404), _FakeResp(404), _FakeResp(404)]):
        assert await dr.download_beatmap(1) is False
    assert not (tmp_path / "1.osz").exists()


async def test_rejects_non_zip_body_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "DANSER_SONGS_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 2)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    # First pass: a small HTML error page, not a real .osz -> rejected.
    with _patch_get([_FakeResp(200, content=b"<html>not found</html>"), _FakeResp(200)]):
        assert await dr.download_beatmap(5) is True
    assert (tmp_path / "5.osz").is_file()
