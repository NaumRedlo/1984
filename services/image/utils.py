"""
Shared utility functions for image card generators:
icon/flag loading, font resolution, image download, PIL helpers.
"""

import asyncio
import os
from io import BytesIO
from typing import List, Dict, Optional

import aiohttp
from PIL import Image, ImageDraw

from utils.logger import get_logger
from services.image.constants import ICONS_DIR, FLAGS_DIR, FALLBACK_CANDIDATES

logger = get_logger("services.image_gen")

# ── Caches ──

_icon_cache: Dict[tuple, Optional[Image.Image]] = {}
_flag_cache: Dict[tuple, Optional[Image.Image]] = {}


def load_icon(name: str, size: int = 20) -> Optional[Image.Image]:
    """Load an icon PNG from assets/icons/, scaled to size x size. Cached."""
    key = (name, size)
    if key in _icon_cache:
        cached = _icon_cache[key]
        return cached.copy() if cached else None
    path = os.path.join(ICONS_DIR, f"{name}.png")
    if not os.path.isfile(path):
        _icon_cache[key] = None
        return None
    try:
        icon = Image.open(path).convert("RGBA")
        result = icon.resize((size, size), Image.LANCZOS)
        _icon_cache[key] = result
        return result.copy()
    except Exception:
        _icon_cache[key] = None
        return None


def load_mod_icon(acronym: str, size: int = 24) -> Optional[Image.Image]:
    """Load a mod glyph from assets/icons/mods/<ACRONYM>.png.

    The source PNGs are white-on-transparent (rendered from osu-web SVG
    badges). Callers typically paste them on a coloured disc — see
    `BountyCardMixin._draw_mod_badge`.
    """
    if not acronym:
        return None
    key = (f"mod:{acronym.upper()}", size)
    if key in _icon_cache:
        cached = _icon_cache[key]
        return cached.copy() if cached else None
    path = os.path.join(ICONS_DIR, "mods", f"{acronym.upper()}.png")
    if not os.path.isfile(path):
        _icon_cache[key] = None
        return None
    try:
        icon = Image.open(path).convert("RGBA")
        result = icon.resize((size, size), Image.LANCZOS)
        _icon_cache[key] = result
        return result.copy()
    except Exception:
        _icon_cache[key] = None
        return None


def load_flag(country_code: str, height: int = 20) -> Optional[Image.Image]:
    """Load a country flag PNG from assets/flags/, scaled to given height. Cached."""
    if not country_code:
        return None
    key = (country_code.lower(), height)
    if key in _flag_cache:
        cached = _flag_cache[key]
        return cached.copy() if cached else None
    path = os.path.join(FLAGS_DIR, f"{country_code.lower()}.png")
    if not os.path.isfile(path):
        _flag_cache[key] = None
        return None
    try:
        flag = Image.open(path).convert("RGBA")
        ratio = height / flag.height
        new_w = int(flag.width * ratio)
        result = flag.resize((new_w, height), Image.LANCZOS)
        _flag_cache[key] = result
        return result.copy()
    except Exception:
        _flag_cache[key] = None
        return None


def _find_font(path: str, fallbacks: Optional[List[str]] = None) -> Optional[str]:
    if os.path.isfile(path):
        return path
    for fb in (fallbacks or FALLBACK_CANDIDATES):
        if os.path.isfile(fb):
            return fb
    return None


# ── Async helpers ──

async def _none_coro():
    """Async noop returning None — used as placeholder in gather()."""
    return None


_shared_session: Optional[aiohttp.ClientSession] = None
_download_semaphore = asyncio.Semaphore(5)


async def _get_shared_session() -> aiohttp.ClientSession:
    """Get or create a shared aiohttp session for image downloads."""
    global _shared_session
    if _shared_session is None or _shared_session.closed:
        _shared_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )
    return _shared_session


async def close_shared_session():
    """Close the shared session (call on app shutdown)."""
    global _shared_session
    if _shared_session and not _shared_session.closed:
        await _shared_session.close()
        _shared_session = None


MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


async def download_image(url: str, timeout: float = 5.0) -> Optional[Image.Image]:
    """Download image from URL, return as RGBA PIL Image or None."""
    if not url:
        return None
    try:
        async with _download_semaphore:
            sess = await _get_shared_session()
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return None
                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_IMAGE_BYTES:
                    logger.warning(f"Image too large ({content_length} bytes): {url}")
                    return None
                chunks = []
                total = 0
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        logger.warning(f"Image exceeded {MAX_IMAGE_BYTES} bytes during download: {url}")
                        return None
                    chunks.append(chunk)
                data = b"".join(chunks)
        img = Image.open(BytesIO(data))
        return img.convert("RGBA")
    except Exception as e:
        logger.debug(f"Failed to download image {url}: {e}")
        return None


# ── PIL helpers ──

def rounded_rect_crop(img: Image.Image, size: int, radius: int = 16) -> Image.Image:
    """Resize image to size*size with rounded corners, return RGBA."""
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def cover_center_crop(cover: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop cover to target_w x target_h preserving original pixel density."""
    cw, ch = cover.size
    scale = max(target_w / cw, target_h / ch)
    new_w = int(cw * scale)
    new_h = int(ch * scale)
    resized = cover.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h)).convert("RGBA")


def draw_cover_background(img: Image.Image, cover: Image.Image, y: int, h: int, w: int, x: int = 0):
    """Center-crop cover to w*h, apply dark overlay, paste onto img."""
    cropped = cover_center_crop(cover, w, h)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 128))
    cropped = Image.alpha_composite(cropped, overlay)
    img.paste(cropped.convert("RGB"), (x, y))
