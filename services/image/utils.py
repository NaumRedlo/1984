"""
Shared utility functions for image card generators:
icon/flag loading, font resolution, image download, PIL helpers.
"""

import asyncio
import os
from io import BytesIO
from typing import List, Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw

from utils.logger import get_logger
from services.image.constants import (
    ICONS_DIR, FLAGS_DIR, FALLBACK_CANDIDATES, TEXT_PRIMARY, TEXT_SECONDARY,
)

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


def draw_line_graph(
    draw: ImageDraw.Draw,
    img: Image.Image,
    points: List[float],
    x: int, y: int, w: int, h: int,
    color: Tuple[int, ...],
    font,
    invert: bool = False,
    labels: Optional[List[str]] = None,
    show_current_label: bool = True,
    show_axis_labels: bool = True,
):
    """Draw a line graph with thick line, fill, grid lines, and axis labels."""
    if not points or len(points) < 2:
        return draw

    vals = list(points)
    min_v = min(vals)
    max_v = max(vals)
    val_range = max_v - min_v if max_v != min_v else 1.0
    pad = val_range * 0.05
    min_v -= pad
    max_v += pad
    val_range = max_v - min_v

    def _y(v):
        ratio = (v - min_v) / val_range
        if invert:
            return y + int(ratio * h)
        return y + h - int(ratio * h)

    step = w / (len(vals) - 1)
    coords = [(int(x + i * step), _y(v)) for i, v in enumerate(vals)]

    # Grid lines (4 horizontal)
    for gi in range(5):
        gy = y + int(h * gi / 4)
        draw.line([(x, gy), (x + w, gy)], fill=(40, 40, 55), width=1)

    # Fill under graph
    fill_color = color[:3] + (60,)
    fill_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fill_draw = ImageDraw.Draw(fill_img)
    bottom = y + h
    poly = list(coords) + [(coords[-1][0], bottom), (coords[0][0], bottom)]
    fill_draw.polygon(poly, fill=fill_color)
    composite = Image.alpha_composite(img.convert("RGBA"), fill_img)
    img.paste(composite.convert("RGB"))
    draw = ImageDraw.Draw(img)

    # Draw line (thick)
    for i in range(len(coords) - 1):
        draw.line([coords[i], coords[i + 1]], fill=color, width=3)

    # Draw dots at start and end
    for pt in [coords[0], coords[-1]]:
        r = 4
        draw.ellipse((pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r), fill=color)

    # Axis labels (right side)
    if show_axis_labels:
        actual_min = min(list(points))
        actual_max = max(list(points))
        if invert:
            top_label = f"#{int(actual_min):,}"
            bot_label = f"#{int(actual_max):,}"
        else:
            top_label = f"{int(actual_max):,}"
            bot_label = f"{int(actual_min):,}"
        draw.text((x + w + 6, y + 2), top_label, font=font, fill=TEXT_SECONDARY)
        draw.text((x + w + 6, y + h - 16), bot_label, font=font, fill=TEXT_SECONDARY)

    # Current value label (optional)
    if show_current_label:
        current = list(points)[-1]
        if invert:
            cur_label = f"#{int(current):,}"
        else:
            cur_label = f"{int(current):,}"
        cx, cy = coords[-1]
        draw.text((cx - 60, cy - 20), cur_label, font=font, fill=TEXT_PRIMARY)

    # X-axis labels if provided
    if labels:
        for i, lbl in enumerate(labels):
            if lbl:
                lx = int(x + i * step)
                draw.text((lx, y + h + 4), lbl, font=font, fill=TEXT_SECONDARY)

    return draw
