"""Public file-hosting resolvers used by /import.

Most of the work is done by the existing `_download_url_to_import_file`
helper (it already handles HTTP redirects, 1GB cap, SSRF guard). This
module only does the pre-step: turn an indirect/page URL into the direct
binary URL the downloader actually streams from.

Currently:
  - Google Drive: handled entirely by the parser (URL rewrite, no IO).
  - MediaFire `/file/<id>/<name>` page: download the HTML once, extract
    the binary URL from the `<a id="downloadButton">` element.

Mega is rejected at the parser level — the encrypted streams need their
SDK and we deliberately don't depend on it.
"""

from __future__ import annotations

import re

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


# The downloadButton href is a direct URL on download####.mediafire.com.
# Both single- and double-quoted attribute forms occur in their HTML.
_MEDIAFIRE_RE = re.compile(
    r"""<a[^>]*id=["']downloadButton["'][^>]*href=["']([^"']+)["']""",
    re.IGNORECASE,
)


class FileUrlResolveError(RuntimeError):
    pass


async def resolve_mediafire(page_url: str) -> str:
    """Scrape a MediaFire file page and return the direct download URL.

    Raises FileUrlResolveError on any non-200 / missing-button case so the
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

    m = _MEDIAFIRE_RE.search(html)
    if not m:
        raise FileUrlResolveError(
            "Не нашёл кнопку загрузки на странице MediaFire. "
            "Возможно файл удалён, страница изменилась, или это папка."
        )
    direct = m.group(1).strip()
    if not direct.lower().startswith(("http://", "https://")):
        raise FileUrlResolveError(
            f"MediaFire вернул нестандартную ссылку: {direct[:80]}"
        )
    return direct


async def resolve_file_url(target_kind_scrape: str | None, raw_url: str) -> str:
    """Dispatch: turn the parser's `scrape` hint into a direct URL.

    `target_kind_scrape` is `ImportTarget.scrape` from parser.py. Currently:
      None        → return `raw_url` as-is (already direct).
      'mediafire' → scrape page, return direct.
    """
    if target_kind_scrape is None:
        return raw_url
    if target_kind_scrape == "mediafire":
        return await resolve_mediafire(raw_url)
    raise FileUrlResolveError(f"Неизвестный resolver: {target_kind_scrape}")


__all__ = ["resolve_file_url", "resolve_mediafire", "FileUrlResolveError"]
