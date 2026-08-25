"""The handful of pages the bot serves to people who are not using the bot.

One page so far: the guide for anybody who wants to lend a machine to the
render farm. It lived as a private link that had to be shared by hand, which is
the wrong shape for something meant to be pasted into a chat — the first thing
half the readers would meet is "page not found".

Hung on the listener the OAuth callback already uses, for the same reasons the
render endpoints are: one loopback bind, one Caddy in front, one certificate.
Caddy needs the path routed to the same upstream; without that these answer
only from the server itself.

## Why the file is wrapped

`docs/guide.ru.html` is written to be published as an Artifact, which supplies
the surrounding document. Served raw it would be missing two tags and both
matter: without `charset=utf-8` every Cyrillic character arrives as mojibake,
and without a viewport a phone lays the page out at desktop width and scales it
down to unreadable. Neither is worth a second copy of the file to fix, so the
document is put around it here.

Everything else the file carries — its `<title>`, its font `<link>` — is left
where it is. The HTML parser hoists those out of the body itself, which is in
the specification rather than a browser quirk.

## No directory, no path from the request

The page is looked up in a table written here. There is nothing a request can
say that changes which file is read, which is the whole of the defence against
a path a visitor invented.
"""

import os
from typing import Optional

from aiohttp import web

from utils.logger import get_logger

logger = get_logger("services.site")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Path to the file behind it. Adding one means adding a line here — never a
# directory, and never anything assembled out of the request.
PAGES = {
    "/guide": os.path.join(_HERE, "docs", "guide.ru.html"),
    # The mini-app itself. Its data comes from `/app/api/*`, which is a
    # different module with a different question to answer — this only hands
    # over the page.
    "/app": os.path.join(_HERE, "docs", "miniapp.html"),
}

_DOCUMENT = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
</head>
<body>
{body}
</body>
</html>
"""

# Read once and re-read when the file changes, so updating a page is a `git
# pull` rather than a restart of the bot.
_cache: dict[str, tuple[float, str]] = {}


def _page(path: str) -> Optional[str]:
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    held = _cache.get(path)
    if held and held[0] == stamp:
        return held[1]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            page = _DOCUMENT.format(body=handle.read())
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return None
    _cache[path] = (stamp, page)
    return page


def install(app: web.Application) -> int:
    """Add every page that is actually on disk. Returns how many."""
    added = 0
    for route, path in PAGES.items():
        if not os.path.isfile(path):
            logger.warning("no page at %s — %s not served", path, route)
            continue

        async def serve(_request: web.Request, path: str = path) -> web.Response:
            page = _page(path)
            if page is None:
                return web.Response(status=404, text="not here")
            return web.Response(
                text=page,
                content_type="text/html",
                charset="utf-8",
                # Long enough that a chat full of people opening it at once is
                # one read, short enough that a correction is live in minutes.
                headers={"Cache-Control": "public, max-age=300"},
            )

        app.router.add_get(route, serve)
        added += 1
    logger.info("serving %d page(s)", added)
    return added


__all__ = ["install", "PAGES"]
