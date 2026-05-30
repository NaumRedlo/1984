"""Unit tests for services.map_import.file_url.resolve_mediafire.

Hits no network; we patch aiohttp.ClientSession.get to return a canned
HTML body. The MediaFire-page scraper is the only thing that needs
testing — Google Drive is resolved at parser level (pure rewrite, no IO)
and direct URLs pass through resolve_file_url unchanged.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from services.map_import.file_url import (
    FileUrlResolveError,
    resolve_file_url,
    resolve_mediafire,
)


class _FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self, errors: str = "strict") -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def get(self, *args, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_session(response: _FakeResponse):
    @asynccontextmanager
    async def _cm(*args, **kwargs):
        sess = _FakeSession(response)
        try:
            yield sess
        finally:
            pass
    return patch("services.map_import.file_url.aiohttp.ClientSession",
                 lambda *a, **kw: _FakeSession(response))


_MEDIAFIRE_OK_HTML = """
<html>
  <body>
    <div class="download_link">
      <a id="downloadButton" href="https://download1234.mediafire.com/abc/maps.zip" aria-label="Download">
        Download (50 MB)
      </a>
    </div>
  </body>
</html>
"""

_MEDIAFIRE_OK_SINGLE_QUOTE = """
<a id='downloadButton' class='input popsok' href='https://download9999.mediafire.com/xyz/dump.osz'>Download</a>
"""

_MEDIAFIRE_NO_BUTTON_HTML = """
<html><body><p>File not found</p></body></html>
"""


@pytest.mark.asyncio
async def test_mediafire_extracts_direct_url():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_OK_HTML)):
        direct = await resolve_mediafire(
            "https://www.mediafire.com/file/abc/maps.zip/file",
        )
    assert direct == "https://download1234.mediafire.com/abc/maps.zip"


@pytest.mark.asyncio
async def test_mediafire_handles_single_quoted_attrs():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_OK_SINGLE_QUOTE)):
        direct = await resolve_mediafire(
            "https://www.mediafire.com/file/xyz/dump.osz/file",
        )
    assert direct == "https://download9999.mediafire.com/xyz/dump.osz"


@pytest.mark.asyncio
async def test_mediafire_missing_button_raises():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_NO_BUTTON_HTML)):
        with pytest.raises(FileUrlResolveError, match="кнопку"):
            await resolve_mediafire(
                "https://www.mediafire.com/file/zzz/missing.zip/file",
            )


@pytest.mark.asyncio
async def test_mediafire_non_200_raises():
    with _patch_session(_FakeResponse(404, "Not found")):
        with pytest.raises(FileUrlResolveError, match="HTTP 404"):
            await resolve_mediafire(
                "https://www.mediafire.com/file/zzz/deleted.zip/file",
            )


# ── Dispatch through resolve_file_url ─────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_file_url_no_scrape_passes_through():
    raw = "https://cdn.example.com/dump.zip"
    out = await resolve_file_url(None, raw)
    assert out == raw


@pytest.mark.asyncio
async def test_resolve_file_url_mediafire_routes_to_scraper():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_OK_HTML)):
        out = await resolve_file_url(
            "mediafire", "https://www.mediafire.com/file/abc/maps.zip/file",
        )
    assert out == "https://download1234.mediafire.com/abc/maps.zip"


@pytest.mark.asyncio
async def test_resolve_file_url_unknown_scrape_raises():
    with pytest.raises(FileUrlResolveError):
        await resolve_file_url("dropbox", "https://example.com/x.zip")
