"""Render-worker HTTP service: auth, health, and a render smoke test
(services/render_worker/server.py). danser itself is monkeypatched out — these
tests never invoke the real binary."""

from aiohttp.test_utils import TestClient, TestServer

from services.render_worker import server as rw
from utils.osu import danser_renderer as dr


async def _client(monkeypatch, secret="testsecret"):
    monkeypatch.setattr(rw, "RENDER_WORKER_SECRET", secret)
    app = rw.RenderWorkerServer().app
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_health_no_auth(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "ok"
    finally:
        await client.close()


async def test_health_reports_gl_ready(monkeypatch):
    # 2026-07-03: /health used to only prove the process was listening — now
    # it also reports whether GLX can actually hand out a context right now.
    async def fake_gl_ready():
        return False

    monkeypatch.setattr(dr, "_check_gl_ready", fake_gl_ready)
    client = await _client(monkeypatch)
    try:
        resp = await client.get("/health")
        body = await resp.json()
        assert body["gl_ready"] is False
    finally:
        await client.close()


async def test_render_rejects_missing_secret(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.post("/render")
        assert resp.status == 401
    finally:
        await client.close()


async def test_render_rejects_wrong_secret(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.post("/render", headers={"Authorization": "Bearer nope"})
        assert resp.status == 401
    finally:
        await client.close()


async def test_render_fails_closed_when_secret_unset(monkeypatch):
    """Even a correct-looking header is rejected when no secret is configured."""
    client = await _client(monkeypatch, secret="")
    try:
        resp = await client.post("/render", headers={"Authorization": "Bearer "})
        assert resp.status == 401
    finally:
        await client.close()


async def test_render_smoke_returns_video(monkeypatch, tmp_path):
    client = await _client(monkeypatch)

    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"\x00\x01\x02fake-mp4-bytes")

    monkeypatch.setattr(dr, "_check_danser", lambda: "/fake/danser")

    async def fake_download_beatmap(bid):
        return True

    async def fake_render(**kwargs):
        return str(fake_mp4)

    async def fake_probe(path):
        return 1280, 720, 5

    monkeypatch.setattr(dr, "download_beatmap", fake_download_beatmap)
    monkeypatch.setattr(dr, "render_replay", fake_render)
    monkeypatch.setattr(dr, "probe_video", fake_probe)

    import aiohttp
    form = aiohttp.FormData()
    form.add_field("replay", b"fake-osr-bytes", filename="replay.osr")
    form.add_field("beatmapset_id", "12345")
    try:
        resp = await client.post(
            "/render", data=form,
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 200
        assert resp.headers["X-Video-Width"] == "1280"
        assert resp.headers["X-Video-Height"] == "720"
        body = await resp.read()
        assert body == b"\x00\x01\x02fake-mp4-bytes"
    finally:
        await client.close()


async def test_render_uses_bot_provided_beatmap_bytes_without_downloading(monkeypatch, tmp_path):
    """2026-07-04: the bot now fetches the .osz itself and sends the bytes
    (beatmap_osz field) instead of leaving the worker to download it - the
    worker's own outbound internet is bandwidth-limited and stalls on files
    this size. When bytes are provided, download_beatmap must NOT be called."""
    client = await _client(monkeypatch)

    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"\x00\x01\x02fake-mp4-bytes")

    monkeypatch.setattr(dr, "_check_danser", lambda: "/fake/danser")

    async def fake_download_beatmap(bid):
        raise AssertionError("download_beatmap should not be called when beatmap_osz bytes are provided")

    save_calls = []

    def fake_save(bid, osz_bytes):
        save_calls.append((bid, osz_bytes))
        return True

    async def fake_render(**kwargs):
        return str(fake_mp4)

    async def fake_probe(path):
        return 1280, 720, 5

    monkeypatch.setattr(dr, "download_beatmap", fake_download_beatmap)
    monkeypatch.setattr(dr, "save_beatmap_osz", fake_save)
    monkeypatch.setattr(dr, "render_replay", fake_render)
    monkeypatch.setattr(dr, "probe_video", fake_probe)

    import aiohttp
    form = aiohttp.FormData()
    form.add_field("replay", b"fake-osr-bytes", filename="replay.osr")
    form.add_field("beatmapset_id", "12345")
    form.add_field("beatmap_osz", b"PK-fake-osz-bytes", filename="beatmap.osz")
    try:
        resp = await client.post(
            "/render", data=form,
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 200
        assert save_calls == [(12345, b"PK-fake-osz-bytes")]
    finally:
        await client.close()


async def test_render_rejects_invalid_bot_provided_beatmap_bytes(monkeypatch):
    client = await _client(monkeypatch)
    monkeypatch.setattr(dr, "_check_danser", lambda: "/fake/danser")
    monkeypatch.setattr(dr, "save_beatmap_osz", lambda bid, data: False)

    import aiohttp
    form = aiohttp.FormData()
    form.add_field("replay", b"fake-osr-bytes", filename="replay.osr")
    form.add_field("beatmapset_id", "12345")
    form.add_field("beatmap_osz", b"not a real osz", filename="beatmap.osz")
    try:
        resp = await client.post(
            "/render", data=form,
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 502
    finally:
        await client.close()


async def test_render_missing_replay(monkeypatch):
    client = await _client(monkeypatch)
    import aiohttp
    form = aiohttp.FormData()
    form.add_field("beatmapset_id", "1")  # multipart, but no "replay" part
    try:
        resp = await client.post(
            "/render", data=form,
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_delete_skin_requires_auth(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.post("/skins/delete", json={"name": "x"})
        assert resp.status == 401
    finally:
        await client.close()


async def test_delete_skin_success(monkeypatch):
    client = await _client(monkeypatch)
    calls = []
    monkeypatch.setattr(dr, "delete_skin", lambda name: calls.append(name))
    try:
        resp = await client.post(
            "/skins/delete", json={"name": "MySkin"},
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 200
        assert calls == ["MySkin"]
    finally:
        await client.close()


async def test_delete_skin_maps_danser_error_to_400(monkeypatch):
    client = await _client(monkeypatch)

    def fake_delete(name):
        raise dr.DanserError("Скин не найден.")

    monkeypatch.setattr(dr, "delete_skin", fake_delete)
    try:
        resp = await client.post(
            "/skins/delete", json={"name": "Nope"},
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_delete_skin_missing_name(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.post(
            "/skins/delete", json={},
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_rename_skin_requires_auth(monkeypatch):
    client = await _client(monkeypatch)
    try:
        resp = await client.post("/skins/rename", json={"name": "a", "new_name": "b"})
        assert resp.status == 401
    finally:
        await client.close()


async def test_rename_skin_success(monkeypatch):
    client = await _client(monkeypatch)
    monkeypatch.setattr(dr, "rename_skin", lambda name, new_name: "Sanitized New")
    try:
        resp = await client.post(
            "/skins/rename", json={"name": "Old", "new_name": "New!!"},
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["name"] == "Sanitized New"
    finally:
        await client.close()


async def test_rename_skin_maps_danser_error_to_400(monkeypatch):
    client = await _client(monkeypatch)

    def fake_rename(name, new_name):
        raise dr.DanserError("Скин с таким именем уже существует.")

    monkeypatch.setattr(dr, "rename_skin", fake_rename)
    try:
        resp = await client.post(
            "/skins/rename", json={"name": "A", "new_name": "B"},
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_render_queue_full_maps_to_429(monkeypatch, tmp_path):
    client = await _client(monkeypatch)
    monkeypatch.setattr(dr, "_check_danser", lambda: "/fake/danser")

    async def fake_download_beatmap(bid):
        return True

    async def fake_render(**kwargs):
        raise dr.RenderQueueFullError("full")

    monkeypatch.setattr(dr, "download_beatmap", fake_download_beatmap)
    monkeypatch.setattr(dr, "render_replay", fake_render)

    import aiohttp
    form = aiohttp.FormData()
    form.add_field("replay", b"osr", filename="replay.osr")
    form.add_field("beatmapset_id", "1")
    try:
        resp = await client.post(
            "/render", data=form,
            headers={"Authorization": "Bearer testsecret"},
        )
        assert resp.status == 429
    finally:
        await client.close()
