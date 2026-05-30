"""Unit tests for services.map_import.file_url.resolve_mediafire.

Hits no network; we patch aiohttp.ClientSession.get to return a canned
HTML body. The MediaFire-page scraper is the only thing that needs
testing — Google Drive is resolved at parser level (pure rewrite, no IO)
and direct URLs pass through resolve_file_url unchanged.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest

from services.map_import.file_url import (
    FileUrlResolveError,
    resolve_file_url,
    resolve_gofile,
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

# href BEFORE id — the old regex (id…href) missed this ordering entirely.
_MEDIAFIRE_HREF_BEFORE_ID = """
<a class="input popsok" href="https://download5555.mediafire.com/qwe/pack.zip" id="downloadButton">Download</a>
"""

# Scrambled (base64) URL — MediaFire's anti-scrape form; href is a dead "#".
_MEDIAFIRE_SCRAMBLED_DIRECT = "https://download7777.mediafire.com/scr/pack.osz"
_MEDIAFIRE_SCRAMBLED_HTML = (
    '<a id="downloadButton" class="input popsok" href="#" '
    'data-scrambled-url="'
    + base64.b64encode(_MEDIAFIRE_SCRAMBLED_DIRECT.encode()).decode()
    + '">Download</a>'
)

# No recognizable button, but a bare CDN link sits elsewhere in the page.
_MEDIAFIRE_BARE_CDN_HTML = """
<html><body>
  <script>var x = "https://download8888.mediafire.com/bare/loose.zip";</script>
</body></html>
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


@pytest.mark.asyncio
async def test_mediafire_href_before_id():
    # Attribute order independence — this is the case the old regex missed.
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_HREF_BEFORE_ID)):
        direct = await resolve_mediafire(
            "https://www.mediafire.com/file/qwe/pack.zip/file",
        )
    assert direct == "https://download5555.mediafire.com/qwe/pack.zip"


@pytest.mark.asyncio
async def test_mediafire_scrambled_url_decoded():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_SCRAMBLED_HTML)):
        direct = await resolve_mediafire(
            "https://www.mediafire.com/file/scr/pack.osz/file",
        )
    assert direct == _MEDIAFIRE_SCRAMBLED_DIRECT


@pytest.mark.asyncio
async def test_mediafire_bare_cdn_link_fallback():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_BARE_CDN_HTML)):
        direct = await resolve_mediafire(
            "https://www.mediafire.com/file/bare/loose.zip/file",
        )
    assert direct == "https://download8888.mediafire.com/bare/loose.zip"


# ── Dispatch through resolve_file_url ─────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_file_url_no_scrape_passes_through():
    raw = "https://cdn.example.com/dump.zip"
    url, headers = await resolve_file_url(None, raw)
    assert url == raw
    assert headers == {}


@pytest.mark.asyncio
async def test_resolve_file_url_mediafire_routes_to_scraper():
    with _patch_session(_FakeResponse(200, _MEDIAFIRE_OK_HTML)):
        url, headers = await resolve_file_url(
            "mediafire", "https://www.mediafire.com/file/abc/maps.zip/file",
        )
    assert url == "https://download1234.mediafire.com/abc/maps.zip"
    assert headers == {}


@pytest.mark.asyncio
async def test_resolve_file_url_unknown_scrape_raises():
    with pytest.raises(FileUrlResolveError):
        await resolve_file_url("dropbox", "https://example.com/x.zip")


# ── GoFile ────────────────────────────────────────────────────────────────
#
# resolve_gofile makes three calls: POST /accounts, GET global.js, then
# GET /contents/<id>. This fake routes by method + URL so a single patched
# session can serve the whole flow.


class _GofileResponse:
    def __init__(self, status: int, *, text: str = "", payload=None):
        self.status = status
        self._text = text
        self._payload = payload

    async def text(self, errors: str = "strict") -> str:
        return self._text

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _GofileSession:
    def __init__(self, *, token_status="ok", wt_js='appdata.wt = "WEBTOK";',
                 contents_payload=None, contents_status=200):
        self._token_status = token_status
        self._wt_js = wt_js
        self._contents_payload = contents_payload
        self._contents_status = contents_status
        self.requests: list[tuple[str, str, dict]] = []

    def post(self, url, *args, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return _GofileResponse(
            200, payload={"status": self._token_status, "data": {"token": "GUESTTOK"}},
        )

    def get(self, url, *args, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if "global.js" in url:
            return _GofileResponse(200, text=self._wt_js)
        # /contents/<id>
        return _GofileResponse(self._contents_status, payload=self._contents_payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _patch_gofile(session: _GofileSession):
    return patch(
        "services.map_import.file_url.aiohttp.ClientSession",
        lambda *a, **kw: session,
    )


_GOFILE_OK = {
    "status": "ok",
    "data": {
        "type": "folder",
        "children": {
            "fid1": {
                "type": "file", "name": "mappack.osz", "size": 1000,
                "link": "https://store1.gofile.io/download/web/fid1/mappack.osz",
            },
        },
    },
}

_GOFILE_MULTI = {
    "status": "ok",
    "data": {
        "type": "folder",
        "children": {
            "a": {"type": "file", "name": "readme.txt", "size": 10,
                  "link": "https://store1.gofile.io/download/web/a/readme.txt"},
            "b": {"type": "file", "name": "small.zip", "size": 50,
                  "link": "https://store1.gofile.io/download/web/b/small.zip"},
            "c": {"type": "file", "name": "big.zip", "size": 9000,
                  "link": "https://store1.gofile.io/download/web/c/big.zip"},
        },
    },
}


@pytest.mark.asyncio
async def test_gofile_returns_link_and_cookie():
    sess = _GofileSession(contents_payload=_GOFILE_OK)
    with _patch_gofile(sess):
        url, headers = await resolve_gofile("https://gofile.io/d/abc123")
    assert url == "https://store1.gofile.io/download/web/fid1/mappack.osz"
    assert headers == {"Cookie": "accountToken=GUESTTOK"}
    # The contents listing must carry the Bearer token and the website token.
    contents_req = next(r for r in sess.requests if "/contents/" in r[1])
    assert "wt=WEBTOK" in contents_req[1]
    assert contents_req[2]["headers"]["Authorization"] == "Bearer GUESTTOK"


@pytest.mark.asyncio
async def test_gofile_picks_largest_archive():
    sess = _GofileSession(contents_payload=_GOFILE_MULTI)
    with _patch_gofile(sess):
        url, _ = await resolve_gofile("https://gofile.io/d/multi")
    assert url.endswith("/big.zip")


@pytest.mark.asyncio
async def test_gofile_not_found_raises():
    sess = _GofileSession(contents_payload={"status": "error-notFound"})
    with _patch_gofile(sess):
        with pytest.raises(FileUrlResolveError, match="не найден"):
            await resolve_gofile("https://gofile.io/d/dead")


@pytest.mark.asyncio
async def test_gofile_empty_folder_raises():
    sess = _GofileSession(
        contents_payload={"status": "ok", "data": {"type": "folder", "children": {}}},
    )
    with _patch_gofile(sess):
        with pytest.raises(FileUrlResolveError, match="нет файлов"):
            await resolve_gofile("https://gofile.io/d/empty")


@pytest.mark.asyncio
async def test_gofile_missing_website_token_raises():
    sess = _GofileSession(wt_js="// nothing useful here", contents_payload=_GOFILE_OK)
    with _patch_gofile(sess):
        with pytest.raises(FileUrlResolveError, match="website-token"):
            await resolve_gofile("https://gofile.io/d/abc123")


@pytest.mark.asyncio
async def test_resolve_file_url_gofile_routes_and_returns_cookie():
    sess = _GofileSession(contents_payload=_GOFILE_OK)
    with _patch_gofile(sess):
        url, headers = await resolve_file_url("gofile", "https://gofile.io/d/abc123")
    assert url.endswith("/mappack.osz")
    assert headers["Cookie"] == "accountToken=GUESTTOK"
