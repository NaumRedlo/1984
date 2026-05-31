"""Public file-hosting resolvers used by /import.

Most of the work is done by the existing `_download_url_to_import_file`
helper (it already handles HTTP redirects, 1GB cap, SSRF guard). This
module only does the pre-step: turn an indirect/page URL into the direct
binary URL the downloader actually streams from.

Currently:
  - Google Drive: handled entirely by the parser (URL rewrite, no IO).
  - MediaFire `/file/<id>/<name>` page: download the HTML once, extract
    the binary URL from the `<a id="downloadButton">` element.
  - GoFile `/d/<code>` page: create a guest account, compute the
    `X-Website-Token` header the API now requires, list the folder, and
    return the largest archive's direct link together with the
    `accountToken` cookie the CDN requires.

Mega is rejected at the parser level — the encrypted streams need their
SDK and we deliberately don't depend on it.

`resolve_file_url` returns `(direct_url, extra_headers)`. `extra_headers`
is empty for hosts whose direct links need no auth; GoFile fills it with
the `Cookie: accountToken=…` header that its download servers require.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import time
from urllib.parse import parse_qs, urlparse

import aiohttp


# Headers picked to look like an ordinary browser. MediaFire serves a
# stripped page (no downloadButton) to obvious bots.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FileUrlResolveError(RuntimeError):
    pass


# MediaFire's download button. Attribute order is not stable across their
# A/B variants, so locate the whole <a id="downloadButton" …> open tag and
# pull the URL out of it afterwards, rather than assuming href follows id.
_MEDIAFIRE_BUTTON_TAG_RE = re.compile(
    r"""<a\b[^>]*\bid=["']downloadButton["'][^>]*>""",
    re.IGNORECASE,
)
# Newer MediaFire pages hide the real URL behind a base64 `data-scrambled-url`
# (their JS does atob() on it) to defeat naive scrapers. Prefer this.
_MEDIAFIRE_SCRAMBLED_RE = re.compile(
    r"""data-scrambled-url=["']([^"']+)["']""",
    re.IGNORECASE,
)
# A plain http(s) href anywhere inside the button tag.
_MEDIAFIRE_HREF_RE = re.compile(
    r"""\bhref=["'](https?://[^"']+)["']""",
    re.IGNORECASE,
)
# Last resort: a bare CDN link sitting anywhere in the page source.
_MEDIAFIRE_DIRECT_RE = re.compile(
    r"""https?://download\d*\.mediafire\.com/[^\s"'<>\\]+""",
    re.IGNORECASE,
)


def _mediafire_unscramble(raw: str) -> str | None:
    """Decode a `data-scrambled-url` value (base64) to a direct URL."""
    try:
        decoded = base64.b64decode(raw).decode("utf-8", "replace").strip()
    except (binascii.Error, ValueError):
        return None
    return decoded if decoded.lower().startswith(("http://", "https://")) else None


def _mediafire_extract(html: str) -> str | None:
    """Pull the direct download URL out of a MediaFire file page.

    Order of preference: scrambled (base64) URL on the button → scrambled
    URL anywhere → plain href on the button → any CDN link in the page.
    Returns None if nothing usable is found.
    """
    tag_m = _MEDIAFIRE_BUTTON_TAG_RE.search(html)
    tag = tag_m.group(0) if tag_m else None

    for scope in ([tag] if tag else []) + [html]:
        sm = _MEDIAFIRE_SCRAMBLED_RE.search(scope)
        if sm:
            url = _mediafire_unscramble(sm.group(1).strip())
            if url:
                return url

    if tag:
        hm = _MEDIAFIRE_HREF_RE.search(tag)
        if hm:
            return hm.group(1).strip()

    dm = _MEDIAFIRE_DIRECT_RE.search(html)
    if dm:
        return dm.group(0).strip()

    return None


async def resolve_mediafire(page_url: str) -> str:
    """Scrape a MediaFire file page and return the direct download URL.

    Raises FileUrlResolveError on any non-200 / missing-link case so the
    caller can show a useful message.
    """
    async with aiohttp.ClientSession(headers=_BROWSER_HEADERS) as sess:
        try:
            async with sess.get(
                page_url,
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    raise FileUrlResolveError(
                        f"MediaFire вернул HTTP {resp.status}"
                    )
                html = await resp.text(errors="replace")
        except aiohttp.ClientError as e:
            raise FileUrlResolveError(f"MediaFire: сеть — {e}") from e

    direct = _mediafire_extract(html)
    if not direct:
        raise FileUrlResolveError(
            "Не нашёл кнопку загрузки на странице MediaFire. Возможно файл "
            "удалён, это папка, или MediaFire отдал JS/капчу. Дайте прямую "
            "ссылку `download####.mediafire.com/.../...zip` или прикрепите файл."
        )
    return direct


# ── GoFile ────────────────────────────────────────────────────────────────
# GoFile is not a plain direct-link host: every download needs a guest
# account token (used both as an API Bearer and as the CDN's accountToken
# cookie) plus an `X-Website-Token` header that the /contents endpoint
# validates server-side. That header used to be a static `wt` string lifted
# from global.js, but GoFile (2026-05) moved to a per-request hash computed
# client-side by wt.obf.js::generateWT — see _gofile_wt.

_GOFILE_API = "https://api.gofile.io"

# X-Website-Token formula, reverse-engineered from wt.obf.js::generateWT:
#     wt = sha256(f"{UA}::{lang}::{token}::{bucket}::{salt}")
#     bucket = floor(Date.now() / 14_400_000)        # 4-hour window
# The server re-derives this from the request's User-Agent and X-BL headers,
# so the UA/lang used here MUST equal the headers we actually send — hence we
# reuse _BROWSER_HEADERS' UA verbatim. `salt` and the bucket size are build
# constants: if GoFile rotates them the API answers `error-notPremium` and
# they must be re-derived (re-run a node probe over the live wt.obf.js).
_GOFILE_UA = _BROWSER_HEADERS["User-Agent"]
_GOFILE_LANG = "en-US"
_GOFILE_WT_SALT = "g4f8fd9f12h14g"
_GOFILE_WT_BUCKET_MS = 14_400_000

# Archive shapes we prefer when a GoFile folder holds several files.
_GOFILE_ARCHIVE_EXTS = (".zip", ".osz", ".7z", ".rar")

_GOFILE_STATUS_MESSAGES = {
    "error-notFound": "GoFile: контент не найден (ссылка протухла или удалена).",
    "error-notPublic": "GoFile: контент не публичный.",
    "error-passwordRequired": "GoFile: папка защищена паролем — не поддерживается.",
    "error-password": "GoFile: неверный пароль для папки.",
    "error-notPremium": (
        "GoFile отклонил гостевой доступ к листингу. Вероятно GoFile сменил "
        "алгоритм website-token (соль/окно в wt.obf.js) — нужно пере-вывести "
        "_GOFILE_WT_SALT / _GOFILE_WT_BUCKET_MS."
    ),
    "error-wrongToken": "GoFile: токен отклонён (website-token устарел?).",
}


def _gofile_content_id(page_url: str) -> str:
    """Pull the content code out of a gofile.io/d/<code> or ?c=<code> URL."""
    parsed = urlparse(page_url)
    # Codes are short alnum (atEagC) OR a full folder UUID with hyphens.
    m = re.match(r"/d/([A-Za-z0-9_-]+)", parsed.path or "")
    if m:
        return m.group(1)
    qs = parse_qs(parsed.query or "")
    if qs.get("c"):
        return qs["c"][0]
    raise FileUrlResolveError("GoFile: не смог извлечь код из ссылки.")


async def _gofile_guest_token(sess: aiohttp.ClientSession) -> str:
    try:
        async with sess.post(
            f"{_GOFILE_API}/accounts",
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                raise FileUrlResolveError(
                    f"GoFile: создание гостевого аккаунта вернуло HTTP {resp.status}."
                )
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as e:
        raise FileUrlResolveError(f"GoFile: сеть — {e}") from e

    if data.get("status") != "ok":
        raise FileUrlResolveError(
            f"GoFile: API вернул статус {data.get('status')!r} при создании аккаунта."
        )
    token = (data.get("data") or {}).get("token")
    if not token:
        raise FileUrlResolveError("GoFile: API не вернул токен гостевого аккаунта.")
    return token


def _gofile_wt(token: str) -> str:
    """Compute the `X-Website-Token` GoFile's /contents endpoint requires.

    Replicates wt.obf.js::generateWT (see the constants block above). Pure
    function — no IO. The 4-hour bucket gives the server enough tolerance
    that clock skew between us and GoFile never straddles a boundary.
    """
    bucket = int(time.time() * 1000) // _GOFILE_WT_BUCKET_MS
    msg = f"{_GOFILE_UA}::{_GOFILE_LANG}::{token}::{bucket}::{_GOFILE_WT_SALT}"
    return hashlib.sha256(msg.encode()).hexdigest()


def _gofile_files(data: dict) -> list[dict]:
    """Flatten a /contents payload into the list of file nodes it holds."""
    children = data.get("children")
    if isinstance(children, dict):
        return [
            c for c in children.values()
            if isinstance(c, dict) and c.get("type") == "file"
        ]
    if data.get("type") == "file":
        return [data]
    return []


def _gofile_pick_file(files: list[dict]) -> dict:
    """Choose the file to download: largest archive if any, else largest file.

    GoFile shares are almost always a single .osz/.zip; when a folder holds
    several files we bias toward archive extensions and pick the biggest.
    Multi-volume split archives (`*.zip.001`) can't be served through the
    single-file download path and are out of scope here.
    """
    def _is_archive(f: dict) -> bool:
        return (f.get("name") or "").lower().endswith(_GOFILE_ARCHIVE_EXTS)

    archives = [f for f in files if _is_archive(f)]
    pool = archives or files
    return max(pool, key=lambda f: f.get("size") or 0)


async def resolve_gofile(page_url: str) -> tuple[str, dict[str, str]]:
    """Resolve a GoFile folder link to a direct download URL + auth header.

    Returns `(direct_url, {"Cookie": "accountToken=…"})`. The cookie is
    mandatory: GoFile's `store*.gofile.io` servers 401 without it.
    """
    content_id = _gofile_content_id(page_url)
    async with aiohttp.ClientSession(headers=_BROWSER_HEADERS) as sess:
        token = await _gofile_guest_token(sess)
        wt = _gofile_wt(token)
        # The website token moved from a `?wt=` query param to the
        # X-Website-Token header; X-BL carries the language the hash is
        # bound to. pageSize is generous so the whole folder lists in one go.
        api_url = (
            f"{_GOFILE_API}/contents/{content_id}"
            "?contentFilter=&page=1&pageSize=1000"
            "&sortField=createTime&sortDirection=-1"
        )
        try:
            async with sess.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Website-Token": wt,
                    "X-BL": _GOFILE_LANG,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise FileUrlResolveError(
                        f"GoFile: листинг вернул HTTP {resp.status}."
                    )
                payload = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            raise FileUrlResolveError(f"GoFile: сеть — {e}") from e

    if payload.get("status") != "ok":
        status = payload.get("status")
        raise FileUrlResolveError(
            _GOFILE_STATUS_MESSAGES.get(status, f"GoFile: API вернул статус {status!r}.")
        )

    files = _gofile_files(payload.get("data") or {})
    if not files:
        raise FileUrlResolveError("GoFile: в папке нет файлов (или они недоступны).")

    chosen = _gofile_pick_file(files)
    link = chosen.get("link")
    if not link or not str(link).lower().startswith(("http://", "https://")):
        raise FileUrlResolveError("GoFile: у файла нет прямой ссылки на скачивание.")
    return str(link), {"Cookie": f"accountToken={token}"}


async def resolve_file_url(
    target_kind_scrape: str | None, raw_url: str,
) -> tuple[str, dict[str, str]]:
    """Dispatch: turn the parser's `scrape` hint into `(direct_url, headers)`.

    `target_kind_scrape` is `ImportTarget.scrape` from parser.py. Currently:
      None        → return `(raw_url, {})` — already direct, no auth.
      'mediafire' → scrape page, return `(direct, {})`.
      'gofile'    → API flow, return `(direct, {"Cookie": "accountToken=…"})`.

    `headers` is merged into the downloader's request by the caller.
    """
    if target_kind_scrape is None:
        return raw_url, {}
    if target_kind_scrape == "mediafire":
        return await resolve_mediafire(raw_url), {}
    if target_kind_scrape == "gofile":
        return await resolve_gofile(raw_url)
    raise FileUrlResolveError(f"Неизвестный resolver: {target_kind_scrape}")


__all__ = [
    "resolve_file_url",
    "resolve_mediafire",
    "resolve_gofile",
    "FileUrlResolveError",
]
