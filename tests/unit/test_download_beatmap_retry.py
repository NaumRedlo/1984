from unittest.mock import patch

from dossier.osu import beatmap_download as dr


class _FakeResp:
    def __init__(self, status_code, content=b"PK" + b"x" * 2000, chunk=64 * 1024):
        self.status_code = status_code
        self.content = content
        self._chunk = chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=64 * 1024):
        for start in range(0, len(self.content), self._chunk):
            yield self.content[start : start + self._chunk]


def _patch_get(outcomes):
    remaining = list(outcomes)

    def fake_get(*a, **kw):
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return patch("dossier.osu.beatmap_download.requests.get", fake_get)


async def test_already_downloaded_short_circuits_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    (tmp_path / "42 Some Set").mkdir()

    with _patch_get([RuntimeError("must not be called")]):
        assert await dr.download_beatmap(42) is True


async def test_succeeds_on_first_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    with _patch_get([_FakeResp(200)]):
        assert await dr.download_beatmap(99) is True
    assert (tmp_path / "99.osz").is_file()


async def test_retries_after_transient_failure_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 3)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    # First pass: connection error. Second pass: succeeds.
    with _patch_get([ConnectionError("network not ready"), _FakeResp(200)]):
        assert await dr.download_beatmap(7) is True
    assert (tmp_path / "7.osz").is_file()


async def test_gives_up_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 3)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    with _patch_get([_FakeResp(404), _FakeResp(404), _FakeResp(404)]):
        assert await dr.download_beatmap(1) is False
    assert not (tmp_path / "1.osz").exists()


async def test_rejects_non_zip_body_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 2)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    # First pass: a small HTML error page, not a real .osz -> rejected.
    with _patch_get([_FakeResp(200, content=b"<html>not found</html>"), _FakeResp(200)]):
        assert await dr.download_beatmap(5) is True
    assert (tmp_path / "5.osz").is_file()


async def test_a_body_past_the_ceiling_is_abandoned_and_the_next_mirror_tried(
    tmp_path, monkeypatch
):
    """A mirror does not get to choose how much of this machine's memory to
    use. An oversized body is dropped mid-read and treated like any other
    failed mirror — the download falls through to the next one."""
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 1)
    monkeypatch.setattr(dr, "_MAX_OSZ_BYTES", 4096)
    flood = _FakeResp(200, content=b"PK" + b"x" * 8192, chunk=1024)
    with _patch_get([flood, _FakeResp(200)]):
        assert await dr.download_beatmap(8) is True
    assert (tmp_path / "8.osz").is_file()


# ── save_beatmap_osz ──

_REAL_OSZ = b"PK" + b"x" * 2000


def test_save_beatmap_osz_writes_valid_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    assert dr.save_beatmap_osz(123, _REAL_OSZ) is True
    assert (tmp_path / "123.osz").read_bytes() == _REAL_OSZ


def test_save_beatmap_osz_short_circuits_when_already_present(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    (tmp_path / "123 Some Set").mkdir()
    assert dr.save_beatmap_osz(123, b"not even a zip") is True
    assert not (tmp_path / "123.osz").exists()


def test_save_beatmap_osz_rejects_non_zip_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    assert dr.save_beatmap_osz(123, b"<html>not a map</html>") is False
    assert not (tmp_path / "123.osz").exists()


async def test_fetch_beatmap_osz_returns_bytes_directly(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    with _patch_get([_FakeResp(200, content=_REAL_OSZ)]):
        data = await dr.fetch_beatmap_osz(999)
    assert data == _REAL_OSZ
    assert list(tmp_path.iterdir()) == []


async def test_fetch_beatmap_osz_returns_none_on_exhausted_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(dr, "BEATMAP_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRIES", 2)
    monkeypatch.setattr(dr, "_DOWNLOAD_RETRY_SECONDS", 0)
    with _patch_get([_FakeResp(404), _FakeResp(404)]):
        assert await dr.fetch_beatmap_osz(999) is None
