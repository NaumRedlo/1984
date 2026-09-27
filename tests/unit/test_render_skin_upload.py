import hashlib
import html
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import zipfile

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from services.render_farm import skins, skin_upload
from services.render_farm import http as farm_http
from services.render_farm.queue import RenderQueue
from services.render_farm.roster import Roster
from bot.handlers.farm import _a_skin


def archive(prefix="", value=b"[General]\nName: Player", extra=None):
    into = io.BytesIO()
    with zipfile.ZipFile(into, "w") as made:
        made.writestr(prefix + "skin.ini", value)
        made.writestr(prefix + "cursor.png", b"sprite")
        if extra:
            made.writestr(*extra)
    return into.getvalue()

@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(skins.settings, "RENDER_SKINS_DIR", str(tmp_path / "skins"))
    skins._digests.clear()
    return tmp_path


def store(storage, owner, data, filename="Player.osk"):
    source = storage / "upload.osk"
    source.write_bytes(data)
    return skins.store_upload(owner, str(source), filename)


def test_uploaded_skin_is_private_validated_and_content_addressed(storage):
    data = archive("Player/")
    key = store(storage, 123, data, "<My skin>.ZIP")
    assert len(key) <= 64
    assert skins.available(123) == [key]
    assert skins.available() == skins.available(456) == []
    assert skins.described(key, 456) is None
    assert skins.described(key) is None
    info = skins.described(key, 123)
    assert info["hash"] == hashlib.sha256(data).hexdigest()
    assert info["size"] == len(data)
    assert Path(info["path"]).read_bytes() == data
    assert skins.display_name(key, 123) == "<My skin>"
    assert store(storage, 123, data) == key
    assert skins.described("../upload", 123) is None


def test_new_upload_does_not_replace_the_archive_of_a_queued_job(storage):
    first, second = archive(value=b"first"), archive(value=b"second")
    before = store(storage, 7, first)
    line = RenderQueue()
    job = line.offer("replay.osr", "Player", beatmap_md5="a" * 32, skin=skins.described(before, 7))
    after = store(storage, 7, second)
    assert before != after
    assert Path(job.skin["path"]).read_bytes() == first
    assert skins.described(after, 7)["hash"] != job.handed()["skin"]["hash"]


@pytest.mark.parametrize("data", [b"not zip", archive(extra=("../escape.png", b"bad")), archive(extra=("Other/skin.ini", b"other"))])
def test_invalid_upload_does_not_enter_the_personal_catalogue(storage, data):
    with pytest.raises(skins.SkinError, match="invalid"):
        store(storage, 7, data)
    assert skins.available(7) == []


def test_a_skin_without_ini_is_accepted_when_three_render_sprites_exist(storage):
    into = io.BytesIO()
    with zipfile.ZipFile(into, "w") as made:
        for name in ("cursortrail.png", "hit300.png", "scorebar-bg.png"):
            made.writestr("Player/" + name, b"sprite")
    key = store(storage, 7, into.getvalue())
    assert skins.described(key, 7)["size"] == len(into.getvalue())


def test_archive_and_storage_limits_allow_existing_uploads(storage, monkeypatch):
    data = archive()
    monkeypatch.setattr(skins.settings, "RENDER_SKIN_MOST", len(data) - 1)
    with pytest.raises(skins.SkinError, match="too_big"):
        store(storage, 7, data)
    monkeypatch.setattr(skins.settings, "RENDER_SKIN_MOST", len(data) * 2)
    key = store(storage, 7, data)
    monkeypatch.setattr(skins.settings, "RENDER_SKIN_STORAGE_MOST", len(data))
    assert store(storage, 7, data) == key
    with pytest.raises(skins.SkinError, match="storage_full"):
        store(storage, 7, archive(value=b"new"))
    assert skins.available(7) == [key]


def test_same_size_same_second_archive_replacement_refreshes_the_digest(storage):
    import os
    path = storage / "Rafis.osk"
    path.write_bytes(b"old")
    os.utime(path, ns=(1000000001, 1000000001))
    before = skins._digest(str(path))
    path.write_bytes(b"new")
    os.utime(path, ns=(1000000002, 1000000002))
    assert skins._digest(str(path)) != before


@pytest.mark.asyncio
async def test_bot_selects_upload_and_reports_escaped_name(storage, monkeypatch):
    selected = []
    async def choose(owner, name): selected.append((owner, name))
    monkeypatch.setattr(skins, "choose", choose)
    monkeypatch.setattr(skin_upload.members, "shares_a_group", AsyncMock(return_value=True))
    data = archive()
    async def download(document, destination):
        destination.write(data[:10]); destination.write(data[10:]); destination.seek(0)
    bot = SimpleNamespace(download=download)
    message = SimpleNamespace(from_user=SimpleNamespace(id=123), document=SimpleNamespace(file_name="<skin>.osk", file_size=len(data)), reply=AsyncMock())
    await skin_upload.take(bot, message, "ru")
    assert selected and selected[0][0] == 123
    assert "&lt;skin&gt;" in message.reply.call_args.args[0]
    assert message.reply.call_args.kwargs["parse_mode"] == "HTML"
    assert skins.described(selected[0][1], 123)["hash"] == hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
async def test_bad_and_oversize_downloads_keep_previous_selection(storage, monkeypatch):
    chosen = AsyncMock()
    monkeypatch.setattr(skins, "choose", chosen)
    monkeypatch.setattr(skin_upload.members, "shares_a_group", AsyncMock(return_value=True))
    monkeypatch.setattr(skins.settings, "RENDER_SKIN_MOST", 8)
    for data in (b"bad", b"too long for the allowed limit"):
        async def download(document, destination): destination.write(data)
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), document=SimpleNamespace(file_name="skin.osk", file_size=None), reply=AsyncMock())
        await skin_upload.take(SimpleNamespace(download=download), message, "en")
        assert message.reply.called
    chosen.assert_not_called()
    assert skins.available(123) == []


@pytest.mark.asyncio
async def test_non_members_cannot_upload_skins(storage, monkeypatch):
    monkeypatch.setattr(skin_upload.members, "shares_a_group", AsyncMock(return_value=False))
    bot = SimpleNamespace(download=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=123), document=SimpleNamespace(file_name="skin.osk", file_size=5), reply=AsyncMock())
    await skin_upload.take(bot, message, "ru")
    bot.download.assert_not_called()
    assert message.reply.called


def test_skin_document_filter_and_menu_show_upload_names(storage):
    from bot.handlers.profile.settings_menu.skin import _keyboard
    key = store(storage, 123, archive(), "Player skin.osk")
    keyboard = _keyboard(skins.available(123), key, "ru", 123)
    assert "Player skin" in keyboard.inline_keyboard[1][0].text
    assert _a_skin("Player.OSK") and _a_skin("Player.zip")
    assert not _a_skin("replay.osr") and not _a_skin(None)


@pytest.mark.asyncio
async def test_real_upload_reaches_its_claimed_worker_over_http(storage, monkeypatch):
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "skin-test-token")
    data = archive("Player/")
    key = store(storage, 123, data)
    line = RenderQueue()
    job = line.offer("replay.osr", "Player", beatmap_md5="a" * 32, skin=skins.described(key, 123))
    app = web.Application(); app.add_routes(farm_http.make_routes(line, Roster()))
    headers = {"Authorization": "Bearer skin-test-token", "X-Render-Worker": "Mac"}
    async with TestClient(TestServer(app)) as client:
        claim = await client.post("/render/claim", json={"take": True}, headers=headers)
        body = await claim.json()
        assert body["skin"]["hash"] == hashlib.sha256(data).hexdigest()
        assert "path" not in body["skin"]
        fetched = await client.get(f"/render/job/{job.id}/skin", headers=headers)
        assert fetched.status == 200 and await fetched.read() == data
        other = await client.get(f"/render/job/{job.id}/skin", headers={**headers, "X-Render-Worker": "PC"})
        assert other.status == 409
