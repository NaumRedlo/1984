"""The pages the bot serves to people who are not using the bot.

The guide was a private link that had to be shared by hand, which is the wrong
shape for something meant to be pasted into a chat: half the readers would meet
"page not found" as their first impression of the project.

Two things are worth a test here and neither is the routing. The file is
written to be published as an Artifact, which supplies the document around it —
so serving it raw would lose the two tags that make it readable at all. And the
page must be looked up in a table rather than out of the request.
"""

import os
import sys

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import site  # noqa: E402


@pytest_asyncio.fixture
async def served():
    """The pages, over a real listener — the same shape the farm's tests use."""
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

    # The two the Artifact wrapper supplied and a raw file would not. Without
    # the first every Cyrillic character arrives as mojibake; without the
    # second a phone lays the page out at desktop width.
    assert 'charset="utf-8"' in page, "the guide would arrive as mojibake"
    assert "width=device-width" in page, "the guide would be unreadable on a phone"
    assert page.lstrip().startswith("<!doctype html>")


async def test_the_page_is_declared_utf8_in_the_header_too(served):
    """A meta tag is the fallback. The header is what a browser reads first."""
    client, _ = served

    reply = await client.get("/guide")
    assert reply.headers["Content-Type"].lower().replace(" ", "") == "text/html;charset=utf-8"


async def test_the_guides_own_content_survives_the_wrapping(served):
    client, _ = served

    page = await (await client.get("/guide")).text()
    assert "Dossier в беседе" in page
    # The name of the program somebody downloads. It used to be the path to a
    # script in a checkout, which is what the page told people to run before
    # there was a release to hand them instead.
    assert "dossier-worker" in page, "the half the page exists for is missing"


async def test_nothing_a_request_says_chooses_a_file(served):
    """The whole of the defence against a path a visitor invented: there is no
    path in the request that reaches the filesystem."""
    client, _ = served

    for tried in ("/guide/../../etc/passwd", "/etc/passwd", "/guide/index.html"):
        assert (await client.get(tried)).status == 404, tried


def test_a_page_whose_file_is_missing_is_not_routed(monkeypatch):
    """A deployment without the docs should answer 404 rather than 500."""
    monkeypatch.setattr(site, "PAGES", {"/nowhere": "/does/not/exist.html"})
    app = web.Application()
    assert site.install(app) == 0
