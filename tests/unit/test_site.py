import os
import sys

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import site

@pytest_asyncio.fixture
async def served():
    app = web.Application()
    installed = site.install(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    yield client, installed
    await client.close()

async def test_the_guide_is_served_as_a_whole_document(served):
    client, installed = served
    assert installed >= 1, "no page was installed at all"

    reply = await client.get("/guide")
    assert reply.status == 200
    page = await reply.text()

    assert 'charset="utf-8"' in page, "the guide would arrive as mojibake"
    assert "width=device-width" in page, "the guide would be unreadable on a phone"
    assert page.lstrip().startswith("<!doctype html>")

async def test_the_page_is_declared_utf8_in_the_header_too(served):
    client, _ = served

    reply = await client.get("/guide")
    assert reply.headers["Content-Type"].lower().replace(" ", "") == "text/html;charset=utf-8"

async def test_the_guides_own_content_survives_the_wrapping(served):
    client, _ = served

    page = await (await client.get("/guide")).text()
    assert "Dossier в беседе" in page

    assert "dossier-worker" in page, "the half the page exists for is missing"

async def test_nothing_a_request_says_chooses_a_file(served):
    client, _ = served

    for tried in ("/guide/../../etc/passwd", "/etc/passwd", "/guide/index.html"):
        assert (await client.get(tried)).status == 404, tried

def test_a_page_whose_file_is_missing_is_not_routed(monkeypatch):
    monkeypatch.setattr(site, "PAGES", {"/nowhere": "/does/not/exist.html"})
    app = web.Application()
    assert site.install(app) == 0
