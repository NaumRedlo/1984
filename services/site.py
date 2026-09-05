import os
from typing import Optional

from aiohttp import web

from utils.logger import get_logger

from config.settings import MINIAPP_ENABLED

logger = get_logger("services.site")

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PAGES = {
    "/guide": os.path.join(_HERE, "docs", "guide.ru.html"),
}

if MINIAPP_ENABLED:
    PAGES["/app"] = os.path.join(_HERE, "docs", "miniapp.html")

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

                headers={"Cache-Control": "public, max-age=300"},
            )

        app.router.add_get(route, serve)
        added += 1
    logger.info("serving %d page(s)", added)
    return added

__all__ = ["install", "PAGES"]
