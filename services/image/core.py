"""
Pillow-based card generators (1984 dystopia theme).

BaseCardRenderer — shared primitives (fonts, header, footer, separators).
LeaderboardCardGenerator — leaderboard-specific card.
+ 5-page profile cards, compare card with avatars, recent/hps/bounty cards.
"""

import asyncio
import os
from io import BytesIO
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger("services.image_gen")

# Theme colours
BG_COLOR = (20, 20, 30)
HEADER_BG = (35, 35, 50)
ROW_EVEN = (25, 25, 38)
ROW_ODD = (30, 30, 44)
TEXT_PRIMARY = (220, 220, 230)
TEXT_SECONDARY = (140, 140, 160)
ACCENT_RED = (200, 50, 50)
ACCENT_GREEN = (80, 200, 80)
SECTION_BG = (28, 28, 42)
PANEL_BG = (30, 30, 48)

TOP_COLORS = {
    1: (255, 215, 0),
    2: (192, 192, 210),
    3: (205, 150, 80),
}

GRADE_COLORS = {
    "XH": (220, 220, 240),
    "X": (255, 215, 0),
    "SH": (220, 220, 240),
    "S": (255, 215, 0),
    "A": (80, 200, 80),
    "B": (80, 140, 220),
    "C": (200, 150, 50),
    "D": (200, 50, 50),
    "F": (100, 100, 100),
}

MOD_COLORS = {
    "HR": (200, 50, 50),        # red
    "DT": (160, 80, 200),       # purple
    "NC": (160, 80, 200),       # purple (same as DT)
    "HD": (200, 140, 50),       # dark orange
    "SD": (140, 100, 60),       # brown
    "PF": (140, 100, 60),       # brown (same as SD)
    "NF": (60, 120, 200),       # blue
    "EZ": (100, 200, 180),      # mint
    "FL": (220, 200, 60),       # yellow
    "HT": (100, 160, 100),      # green
    "SO": (180, 180, 180),      # gray
    "CL": (180, 180, 200),      # light gray
}

MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Layout constants
CARD_WIDTH = 800
HEADER_HEIGHT = 36
ROW_HEIGHT = 60
FOOTER_HEIGHT = 30
PADDING_X = 30
VALUE_RIGHT_X = CARD_WIDTH - PADDING_X

# Font paths
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

TORUS_BOLD = os.path.join(FONT_DIR, "TorusNotched-Bold.ttf")
TORUS_SEMI = os.path.join(FONT_DIR, "TorusNotched-SemiBold.ttf")
TORUS_REG = os.path.join(FONT_DIR, "TorusNotched-Regular.ttf")
HUNINN = os.path.join(FONT_DIR, "Huninn-Regular.ttf")

FLAGS_DIR = os.path.join(ASSETS_DIR, "flags")
ICONS_DIR = os.path.join(ASSETS_DIR, "icons")


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

FALLBACK_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_font(path: str, fallbacks: Optional[List[str]] = None) -> Optional[str]:
    if os.path.isfile(path):
        return path
    for fb in (fallbacks or FALLBACK_CANDIDATES):
        if os.path.isfile(fb):
            return fb
    return None


# Image helpers

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
                data = await resp.read()
        img = Image.open(BytesIO(data))
        return img.convert("RGBA")
    except Exception as e:
        logger.debug(f"Failed to download image {url}: {e}")
        return None


def rounded_rect_crop(img: Image.Image, size: int, radius: int = 16) -> Image.Image:
    """Resize image to size×size with rounded corners, return RGBA."""
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def cover_center_crop(cover: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop cover to target_w×target_h preserving original pixel density."""
    cw, ch = cover.size
    # Scale so that the image fills the target area
    scale = max(target_w / cw, target_h / ch)
    new_w = int(cw * scale)
    new_h = int(ch * scale)
    resized = cover.resize((new_w, new_h), Image.LANCZOS)
    # Center crop
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h)).convert("RGBA")


def draw_cover_background(img: Image.Image, cover: Image.Image, y: int, h: int, w: int, x: int = 0):
    """Center-crop cover to w×h, apply dark overlay, paste onto img."""
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
    # Add 5% padding
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

    # X-axis labels if provided — only draw non-empty labels
    if labels:
        for i, lbl in enumerate(labels):
            if lbl:
                lx = int(x + i * step)
                draw.text((lx, y + h + 4), lbl, font=font, fill=TEXT_SECONDARY)

    return draw


# BaseCardRenderer

class BaseCardRenderer:
    """Shared drawing primitives for all card types."""

    def __init__(self):
        bold = _find_font(TORUS_BOLD)
        semi = _find_font(TORUS_SEMI)
        reg = _find_font(TORUS_REG)

        huninn = _find_font(HUNINN)

        if bold:
            self.font_title = ImageFont.truetype(bold, 28)
            self.font_big = ImageFont.truetype(bold, 34)
            self.font_subtitle = ImageFont.truetype(semi or bold, 20)
            self.font_row = ImageFont.truetype(bold, 22)
            self.font_grade = ImageFont.truetype(bold, 40)
            self.font_label = ImageFont.truetype(semi or bold, 18)
            self.font_small = ImageFont.truetype(reg or bold, 16)
            self.font_vs = ImageFont.truetype(bold, 48)
            self.font_stat_value = ImageFont.truetype(bold, 26)
            self.font_stat_label = ImageFont.truetype(semi or bold, 14)
        else:
            logger.warning("No TTF fonts found, falling back to default bitmap font")
            default = ImageFont.load_default()
            self.font_title = default
            self.font_big = default
            self.font_subtitle = default
            self.font_row = default
            self.font_grade = default
            self.font_label = default
            self.font_small = default
            self.font_vs = default
            self.font_stat_value = default
            self.font_stat_label = default

        # Huninn (Cyrillic-capable) for labels and section titles
        if huninn:
            self.font_ru_section = ImageFont.truetype(huninn, 20)
            self.font_ru_label = ImageFont.truetype(huninn, 18)
            self.font_ru_stat_label = ImageFont.truetype(huninn, 14)
        else:
            self.font_ru_section = self.font_subtitle
            self.font_ru_label = self.font_label
            self.font_ru_stat_label = self.font_stat_label

    # Canvas

    def _create_canvas(self, w: int, h: int):
        img = Image.new("RGB", (w, h), BG_COLOR)
        draw = ImageDraw.Draw(img)
        return img, draw

    # Header

    def _draw_header(self, draw: ImageDraw.Draw, title: str, subtitle: str, w: int):
        """Compact 36px header: title centered, username right-aligned gray."""
        h = 36
        draw.rectangle([(0, 0), (w, h)], fill=HEADER_BG)
        self._text_center(draw, w // 2, 8, title, self.font_subtitle, ACCENT_RED)
        if subtitle:
            self._text_right(draw, w - PADDING_X, 10, subtitle, self.font_small, TEXT_SECONDARY)
        draw.line([(0, h - 2), (w, h - 2)], fill=ACCENT_RED, width=2)

    # Footer

    def _draw_footer(self, draw: ImageDraw.Draw, img: Image.Image, text: str, y: int, w: int):
        draw.line([(0, y), (w, y)], fill=ACCENT_RED, width=1)
        draw.text((PADDING_X, y + 6), text, font=self.font_small, fill=TEXT_SECONDARY)

    # Separator

    def _draw_separator(self, draw: ImageDraw.Draw, y: int, w: int):
        draw.line([(PADDING_X, y), (w - PADDING_X, y)], fill=ACCENT_RED, width=1)

    # Key-Value row

    def _draw_kv_row(
        self, draw: ImageDraw.Draw, y: int,
        label: str, value: str,
        label_font=None, value_font=None,
        label_fill=None, value_fill=None,
        x: int = PADDING_X,
    ):
        lf = label_font or self.font_ru_label
        vf = value_font or self.font_row
        lc = label_fill or TEXT_SECONDARY
        vc = value_fill or TEXT_PRIMARY
        draw.text((x, y), f"{label}:", font=lf, fill=lc)
        bbox = draw.textbbox((0, 0), f"{label}:", font=lf)
        lw = bbox[2] - bbox[0]
        draw.text((x + lw + 8, y), value, font=vf, fill=vc)

    # Section title

    def _draw_section_title(self, draw: ImageDraw.Draw, y: int, text: str):
        draw.text((PADDING_X, y), text, font=self.font_ru_section, fill=ACCENT_RED)

    # Right-aligned text

    def _text_right(self, draw: ImageDraw.Draw, x_right: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((x_right - tw, y), text, font=font, fill=fill)

    # Center-aligned text

    def _text_center(self, draw: ImageDraw.Draw, cx: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, y), text, font=font, fill=fill)

    # Panel (rounded rect bg)

    def _draw_panel(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, bg=PANEL_BG):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=bg)

    # Stat cell (value on top, label below)

    def _draw_stat_cell(self, draw: ImageDraw.Draw, cx: int, y: int, value: str, label: str):
        self._text_center(draw, cx, y, value, self.font_stat_value, TEXT_PRIMARY)
        self._text_center(draw, cx, y + 30, label, self.font_ru_stat_label, TEXT_SECONDARY)

    def _draw_mini_badge(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, value: str, label: str):
        self._draw_panel(draw, x, y, w, h)
        self._text_center(draw, x + w // 2, y + 2, value, self.font_small, TEXT_PRIMARY)
        self._text_center(draw, x + w // 2, y + h - 12, label, self.font_stat_label, TEXT_SECONDARY)

    # Save helper

    @staticmethod
    def _save(img: Image.Image) -> BytesIO:
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    # Profile Page 0 — Info  (800 × 620)

    def generate_profile_info_card(self, data: Dict, avatar: Optional[Image.Image] = None, cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = 800, 576
        img, draw = self._create_canvas(W, H)

        hero_h = 188
        if cover:
            cropped = cover_center_crop(cover, W, hero_h)
            overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, 96))
            cropped = Image.alpha_composite(cropped, overlay)
            fade_h = 52
            fade_overlay = Image.new("RGBA", (W, hero_h), (*BG_COLOR[:3], 0))
            fade_mask = Image.new("L", (W, hero_h), 0)
            fade_draw = ImageDraw.Draw(fade_mask)
            for fy in range(fade_h):
                alpha = int(fy / max(fade_h - 1, 1) * 255)
                fade_draw.line([(0, hero_h - fade_h + fy), (W, hero_h - fade_h + fy)], fill=alpha)
            fade_overlay.putalpha(fade_mask)
            cropped = Image.alpha_composite(cropped, fade_overlay)
            img.paste(cropped.convert("RGB"), (0, 0))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, 0), (W, hero_h)], fill=HEADER_BG)
            draw.line([(0, hero_h - 2), (W, hero_h - 2)], fill=ACCENT_RED, width=2)

        avatar_size = 104
        avatar_x = (W - avatar_size) // 2
        avatar_y = hero_h - avatar_size // 2 - 18
        if avatar:
            cropped = rounded_rect_crop(avatar, avatar_size, radius=16)
            img.paste(cropped, (avatar_x, avatar_y), cropped)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), radius=16, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size), radius=16, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        username = data.get('username', '???')
        country = data.get('country', '')
        name_y = avatar_y + avatar_size + 2
        flag_img = load_flag(country, height=20)
        username_bbox = draw.textbbox((0, 0), username, font=self.font_big)
        username_w = username_bbox[2] - username_bbox[0]
        username_h = username_bbox[3] - username_bbox[1]
        flag_w = flag_img.width if flag_img else 0
        flag_h = flag_img.height if flag_img else 0
        gap = 8 if flag_img else 0
        total_w = username_w + flag_w + gap
        text_x = (W - total_w) // 2 + (flag_w + gap if flag_img else 0)
        if flag_img:
            text_center_y = name_y + username_h // 2
            flag_y = text_center_y - flag_h // 2
            img.paste(flag_img, (text_x - flag_w - gap, flag_y + 4), flag_img)
            draw = ImageDraw.Draw(img)
        draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        level = data.get('level', 0)
        level_progress = data.get('level_progress', 0)
        bar_w = 340
        bar_x = (W - bar_w) // 2
        y_bar = name_y + 46
        draw.text((bar_x, y_bar - 18), f'Lv{level}', font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, bar_x + bar_w, y_bar - 18, f'Lv{level + 1}', self.font_small, TEXT_SECONDARY)
        draw.rounded_rectangle((bar_x, y_bar, bar_x + bar_w, y_bar + 14), radius=7, fill=TEXT_PRIMARY)
        inner_w = max(10, int((bar_w - 4) * level_progress / 100))
        draw.rounded_rectangle((bar_x + 2, y_bar + 2, bar_x + 2 + inner_w, y_bar + 12), radius=6, fill=ACCENT_RED)
        self._text_center(draw, bar_x + bar_w // 2, y_bar + 0, f'{level_progress}%', self.font_small, BG_COLOR)
        play_count = data.get('play_count', 0) or 0
        total_hits = data.get('total_hits', 0) or 0
        hpp = total_hits / play_count if play_count > 0 else 0.0

        top_stats_y = y_bar + 34
        top_gap = 10
        top_panel_h = 44
        top_panel_w = (W - 2 * PADDING_X - 2 * top_gap) // 3
        top_stats = [
            (f"{data.get('pp', 0.0):.0f}pp" if data.get('pp', 0.0) else '—', 'PP'),
            (f"{data.get('global_rank', 0):,}" if data.get('global_rank', 0) else '—', 'GLOBAL RANK'),
            (f"{data.get('accuracy', 0):.2f}%", 'ACCURACY'),
        ]
        for idx, (val, label) in enumerate(top_stats):
            x = PADDING_X + idx * (top_panel_w + top_gap)
            self._draw_panel(draw, x, top_stats_y, top_panel_w, top_panel_h)
            self._text_center(draw, x + top_panel_w // 2, top_stats_y + 4, val, self.font_row, TEXT_PRIMARY)
            self._text_center(draw, x + top_panel_w // 2, top_stats_y + 24, label, self.font_stat_label, TEXT_SECONDARY)

        lower_top = top_stats_y + top_panel_h + 14
        lower_gap_x = 10
        lower_gap_y = 6
        lower_panel_h = 46
        lower_panel_w = (W - 2 * PADDING_X - lower_gap_x) // 2
        left_x = PADDING_X
        right_x = PADDING_X + lower_panel_w + lower_gap_x

        hp_points = data.get('hp_points', 0)
        left_stats = [
            (f"{hp_points} HP", 'HP'),
            (f"{play_count:,}", 'PLAY COUNT'),
            (f"{data.get('ranked_score', 0):,}", 'RANKED SCORE'),
            (f"{total_hits:,}", 'TOTAL HITS'),
        ]
        right_stats = [
            (str(data.get('hp_rank', '—')), 'HPS'),
            (str(data.get('play_time', '—')), 'PLAY TIME'),
            (f"{data.get('total_score', 0):,}", 'TOTAL SCORE'),
            (f"{hpp:.2f}" if play_count > 0 else '—', 'HITS / PLAY'),
        ]

        for row_idx in range(4):
            y = lower_top + row_idx * (lower_panel_h + lower_gap_y)
            val_l, label_l = left_stats[row_idx]
            val_r, label_r = right_stats[row_idx]
            self._draw_panel(draw, left_x, y, lower_panel_w, lower_panel_h)
            self._draw_panel(draw, right_x, y, lower_panel_w, lower_panel_h)
            self._text_center(draw, left_x + lower_panel_w // 2, y + 4, val_l, self.font_row, TEXT_PRIMARY)
            self._text_center(draw, left_x + lower_panel_w // 2, y + 24, label_l, self.font_stat_label, TEXT_SECONDARY)
            self._text_center(draw, right_x + lower_panel_w // 2, y + 4, val_r, self.font_row, TEXT_PRIMARY)
            self._text_center(draw, right_x + lower_panel_w // 2, y + 24, label_r, self.font_stat_label, TEXT_SECONDARY)

        # keep lower stat labels neutral

        return self._save(img)

    # Profile Page 1 — Rank History  (800 × 500)

    def generate_profile_rank_card(self, data: Dict) -> BytesIO:
        W, H = 800, 516
        img, draw = self._create_canvas(W, H)

        username = data.get("username", "???")
        self._draw_header(draw, "PROJECT 1984 — RANK HISTORY", username, W)

        # Stat panels row
        pp = data.get("pp", 0)
        rank = data.get("global_rank", 0)
        country_rank = data.get("country_rank", 0)
        panel_y = 44
        panel_h = 50
        gap = 8
        panel_w = (W - PADDING_X * 2 - gap * 2) // 3
        panels = [
            (f"{pp:,}", "PP"),
            (f"#{rank:,}", "GLOBAL RANK"),
            (f"#{country_rank:,}" if country_rank else "—", "COUNTRY RANK"),
        ]
        for col_idx, (val, label) in enumerate(panels):
            px = PADDING_X + col_idx * (panel_w + gap)
            self._draw_panel(draw, px, panel_y, panel_w, panel_h)
            cell_cx = px + panel_w // 2
            # Bold white values
            self._text_center(draw, cell_cx, panel_y + 6, val, self.font_label, TEXT_PRIMARY)
            self._text_center(draw, cell_cx, panel_y + 28, label, self.font_stat_label, TEXT_SECONDARY)

        rank_history = data.get("rank_history", [])
        graph_top = panel_y + panel_h + 20
        if len(rank_history) >= 2:
            graph_margin = 40
            graph_w = W - 2 * graph_margin
            graph_x = graph_margin
            graph_h = H - graph_top - 80  # smaller graph, more room for labels
            new_draw = draw_line_graph(
                draw, img, rank_history,
                x=graph_x, y=graph_top, w=graph_w, h=graph_h,
                color=ACCENT_RED, font=self.font_small, invert=True,
                show_current_label=False,
                show_axis_labels=False,
            )
            if new_draw:
                draw = new_draw

            # Left/right endpoint values
            left_val = rank_history[0]
            right_val = rank_history[-1]
            bottom_label_y = graph_top + graph_h + 8
            draw.text((graph_x, bottom_label_y), f"#{int(left_val):,}", font=self.font_small, fill=TEXT_SECONDARY)
            self._text_right(draw, graph_x + graph_w, bottom_label_y, f"#{int(right_val):,}", self.font_small, TEXT_SECONDARY)

            self._text_center(draw, W // 2, bottom_label_y, "Last 90 days", self.font_small, TEXT_SECONDARY)
        else:
            self._text_center(draw, W // 2, 280, "Not enough data", self.font_row, TEXT_SECONDARY)

        return self._save(img)

    # Profile Page 2 — Play Count History  (800 × 500)

    def generate_profile_playcount_card(self, data: Dict) -> BytesIO:
        W, H = 800, 436
        img, draw = self._create_canvas(W, H)

        username = data.get("username", "???")
        self._draw_header(draw, "PROJECT 1984 — PLAY COUNT HISTORY", username, W)

        pc = data.get("play_count", 0)
        monthly = data.get("monthly_playcounts", [])
        this_month = 0
        if monthly:
            this_month = monthly[-1].get("count", 0) or 0

        # Stats row: total plays, min, max, this month — panels
        info_y = 44
        gap = 8
        pw = (W - PADDING_X * 2 - gap * 4) // 5
        ph = 42

        counts_all = []
        if monthly and len(monthly) >= 2:
            counts_all = [int(entry.get("count", 0) or 0) for entry in monthly]

        min_c = min(counts_all) if counts_all else 0
        max_c = max(counts_all) if counts_all else 0

        avg_c = int(sum(counts_all) / len(counts_all)) if counts_all else 0

        # Most active month
        best_month_str = "—"
        if monthly:
            best_entry = max(monthly, key=lambda e: e.get("count", 0) or 0)
            sd = best_entry.get("start_date") or ""
            try:
                sd_parts = str(sd).split("-")
                yr = sd_parts[0]
                mo = int(sd_parts[1])
                best_month_str = f"{MONTH_NAMES[mo]} {yr}"
            except Exception:
                pass

        stat_panels = [
            (f"{pc:,}", "TOTAL PLAYS"),
            (f"{avg_c:,}", "AVG / MONTH"),
            (f"{max_c:,}", "MAX / MONTH"),
            (f"+{this_month:,}", "THIS MONTH"),
            (best_month_str, "MOST ACTIVE"),
        ]
        for col_idx, (val, label) in enumerate(stat_panels):
            px = PADDING_X + col_idx * (pw + gap)
            self._draw_panel(draw, px, info_y, pw, ph)
            cell_cx = px + pw // 2
            self._text_center(draw, cell_cx, info_y + 4, val, self.font_label, TEXT_PRIMARY)
            self._text_center(draw, cell_cx, info_y + 24, label, self.font_stat_label, TEXT_SECONDARY)

        graph_top = info_y + ph + 12
        if counts_all:
            # Yearly labels (January of each year + first entry)
            labels = []
            seen_years = set()
            for i, entry in enumerate(monthly):
                sd = entry.get("start_date") or ""
                try:
                    parts = str(sd).split("-")
                    yr = parts[0][2:]  # "23" from "2023"
                    mo = int(parts[1])
                    if mo == 1 or (i == 0 and yr not in seen_years):
                        labels.append(f"'{yr}")
                        seen_years.add(yr)
                    else:
                        labels.append("")
                except Exception:
                    labels.append("")

            graph_w = W - 2 * PADDING_X
            graph_h = H - graph_top - 30
            new_draw = draw_line_graph(
                draw, img, counts_all,
                x=PADDING_X, y=graph_top, w=graph_w, h=graph_h,
                color=ACCENT_RED, font=self.font_small, invert=False,
                labels=labels,
                show_current_label=False,
                show_axis_labels=False,
            )
            if new_draw:
                draw = new_draw
        else:
            self._text_center(draw, W // 2, 250, "Not enough data", self.font_row, TEXT_SECONDARY)

        return self._save(img)

    # Profile Page 3 — Top Scores  (800 × 520)

    def generate_profile_top_card(self, data: Dict, bg_images: Optional[List[Optional[Image.Image]]] = None) -> BytesIO:
        W, H = 800, 456
        img, draw = self._create_canvas(W, H)

        username = data.get("username", "???")
        self._draw_header(draw, "PROJECT 1984 — TOP SCORES", username, W)

        scores = data.get("top_scores", [])
        if not scores:
            self._text_center(draw, W // 2, 200, "No top scores available", self.font_row, TEXT_SECONDARY)
            return self._save(img)

        y = 44
        row_h = 82
        grade_w = 54      # space for grade letter
        info_x = 8 + grade_w

        for i, sc in enumerate(scores[:5]):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            # Top-3 color line at left edge
            rank_pos = i + 1
            if rank_pos <= 3:
                bar_color = TOP_COLORS.get(rank_pos, TEXT_PRIMARY)
                draw.rectangle([(0, ry), (4, ry + row_h)], fill=bar_color)

            # BG image fade on right side
            bg_img = bg_images[i] if bg_images and i < len(bg_images) else None
            if bg_img:
                try:
                    bg_w = W // 2
                    bg_crop = cover_center_crop(bg_img, bg_w, row_h)
                    grad_mask = Image.new("L", (bg_w, row_h), 0)
                    for gx in range(bg_w):
                        alpha = int(gx / bg_w * 120)
                        ImageDraw.Draw(grad_mask).line([(gx, 0), (gx, row_h)], fill=alpha)
                    dark = Image.new("RGBA", (bg_w, row_h), (0, 0, 0, 80))
                    bg_crop = Image.alpha_composite(bg_crop, dark)
                    img.paste(bg_crop.convert("RGB"), (W - bg_w, ry), grad_mask)
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass

            # Grade — centered in grade_w zone
            grade = sc.get("rank", "F")
            grade_color = GRADE_COLORS.get(grade, TEXT_PRIMARY)
            grade_cx = 8 + grade_w // 2
            grade_font = self.font_row if grade == "SH" else self.font_grade
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, grade_font, grade_color)

            # Map title (truncated)
            artist = sc.get("artist", "")
            title = sc.get("title", "")
            map_str = f"{artist} - {title}"
            if len(map_str) > 40:
                map_str = map_str[:37] + "..."
            draw.text((info_x, ry + 8), map_str, font=self.font_label, fill=TEXT_PRIMARY)

            # Difficulty | mapper below title
            version = sc.get("version", "")
            creator = sc.get("creator", "")
            sub_x = info_x
            if version:
                version_str = f"[{version}]"
                draw.text((sub_x, ry + 28), version_str, font=self.font_small, fill=TEXT_SECONDARY)
                if creator:
                    vbox = draw.textbbox((0, 0), version_str + " | ", font=self.font_small)
                    sep_w = vbox[2] - vbox[0]
                    draw.text((sub_x, ry + 28), version_str + " | ", font=self.font_small, fill=TEXT_SECONDARY)
                    draw.text((sub_x + sep_w, ry + 28), creator, font=self.font_label, fill=TEXT_PRIMARY)
            elif creator:
                draw.text((sub_x, ry + 28), creator, font=self.font_label, fill=TEXT_PRIMARY)

            # Third line: accuracy, combo, mods (colored individually)
            acc = sc.get("accuracy", 0)
            combo = sc.get("max_combo", 0)
            mods = sc.get("mods", "")
            detail = f"{acc:.2f}% | {combo}x"
            detail_x = info_x
            draw.text((detail_x, ry + 48), detail, font=self.font_small, fill=TEXT_SECONDARY)

            # Draw mods bold with individual colors
            if mods:
                detail_bbox = draw.textbbox((0, 0), detail + "  ", font=self.font_small)
                mod_x = detail_x + (detail_bbox[2] - detail_bbox[0])
                mod_list = [m.strip() for m in mods.split(",") if m.strip()]
                for mi, mod in enumerate(mod_list):
                    mod_color = MOD_COLORS.get(mod, TEXT_SECONDARY)
                    draw.text((mod_x, ry + 48), mod, font=self.font_label, fill=mod_color)
                    mod_bbox = draw.textbbox((0, 0), mod, font=self.font_label)
                    mod_x += mod_bbox[2] - mod_bbox[0] + 4

            pp = sc.get("pp") or 0
            pp_str = f"{pp:.0f}pp" if pp else "—"
            self._text_right(draw, W - PADDING_X, ry + (row_h - 22) // 2, pp_str, self.font_row, ACCENT_RED)

        return self._save(img)

    # Profile Page 4 — Recent Plays  (800 × 520)

    def generate_profile_recent_card(self, data: Dict, bg_images: Optional[List[Optional[Image.Image]]] = None) -> BytesIO:
        W, H = 800, 456
        img, draw = self._create_canvas(W, H)

        username = data.get("username", "???")
        self._draw_header(draw, "PROJECT 1984 — RECENT PLAYS", username, W)

        scores = data.get("recent_scores", [])
        if not scores:
            self._text_center(draw, W // 2, 200, "No recent plays", self.font_row, TEXT_SECONDARY)
            return self._save(img)

        y = 44
        row_h = 82
        grade_w = 54
        info_x = 8 + grade_w

        for i, sc in enumerate(scores[:5]):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            # BG image fade on right side
            bg_img = bg_images[i] if bg_images and i < len(bg_images) else None
            if bg_img:
                try:
                    bg_w = W // 2
                    bg_crop = cover_center_crop(bg_img, bg_w, row_h)
                    grad_mask = Image.new("L", (bg_w, row_h), 0)
                    for gx in range(bg_w):
                        alpha = int(gx / bg_w * 120)
                        ImageDraw.Draw(grad_mask).line([(gx, 0), (gx, row_h)], fill=alpha)
                    dark = Image.new("RGBA", (bg_w, row_h), (0, 0, 0, 80))
                    bg_crop = Image.alpha_composite(bg_crop, dark)
                    img.paste(bg_crop.convert("RGB"), (W - bg_w, ry), grad_mask)
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass

            # Grade — left side
            grade = sc.get("rank", "F")
            grade_color = GRADE_COLORS.get(grade, TEXT_PRIMARY)
            grade_cx = 8 + grade_w // 2
            grade_font = self.font_row if grade == "SH" else self.font_grade
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, grade_font, grade_color)

            # Map title
            beatmapset = sc.get("beatmapset") or {}
            beatmap = sc.get("beatmap") or {}
            artist = beatmapset.get("artist", "")
            title = beatmapset.get("title", "")
            map_str = f"{artist} - {title}"
            if len(map_str) > 40:
                map_str = map_str[:37] + "..."
            draw.text((info_x, ry + 8), map_str, font=self.font_label, fill=TEXT_PRIMARY)

            # Second line: [version] | mapper
            version = beatmap.get("version", "")
            creator = beatmapset.get("creator", "")
            sub_x = info_x
            if version:
                version_str = f"[{version}]"
                draw.text((sub_x, ry + 28), version_str, font=self.font_small, fill=TEXT_SECONDARY)
                if creator:
                    vbox = draw.textbbox((0, 0), version_str + " | ", font=self.font_small)
                    sep_w = vbox[2] - vbox[0]
                    draw.text((sub_x, ry + 28), version_str + " | ", font=self.font_small, fill=TEXT_SECONDARY)
                    draw.text((sub_x + sep_w, ry + 28), creator, font=self.font_label, fill=TEXT_PRIMARY)
            elif creator:
                draw.text((sub_x, ry + 28), creator, font=self.font_label, fill=TEXT_PRIMARY)

            # Third line: accuracy, combo, then mods bold with colors
            acc_raw = sc.get("accuracy", 0)
            acc = acc_raw * 100 if acc_raw <= 1.0 else acc_raw
            combo = sc.get("max_combo", 0)
            mods_list = sc.get("mods", [])
            detail = f"{acc:.2f}% | {combo}x"
            draw.text((info_x, ry + 48), detail, font=self.font_small, fill=TEXT_SECONDARY)

            if mods_list:
                detail_bbox = draw.textbbox((0, 0), detail + "  ", font=self.font_small)
                mod_x = info_x + (detail_bbox[2] - detail_bbox[0])
                for mod_raw in mods_list:
                    mod_name = str(mod_raw) if isinstance(mod_raw, str) else str(mod_raw.get("acronym", ""))
                    if not mod_name:
                        continue
                    mod_color = MOD_COLORS.get(mod_name, TEXT_SECONDARY)
                    draw.text((mod_x, ry + 48), mod_name, font=self.font_label, fill=mod_color)
                    mb = draw.textbbox((0, 0), mod_name, font=self.font_label)
                    mod_x += mb[2] - mb[0] + 4

            pp = sc.get("pp") or 0
            pp_str = f"{pp:.0f}pp" if pp else "—"
            self._text_right(draw, W - PADDING_X, ry + (row_h - 22) // 2, pp_str, self.font_row, ACCENT_RED)

        return self._save(img)

    # Profile Dispatcher — async, downloads images

    async def generate_profile_page_async(self, page: int, data: Dict) -> BytesIO:
        """Generate a profile page. Downloads avatar/cover for page 0."""
        avatar = None
        cover = None

        if page == 0:
            avatar_url = data.get("avatar_url")
            cover_url = data.get("cover_url")
            results = await asyncio.gather(
                download_image(avatar_url),
                download_image(cover_url),
                return_exceptions=True,
            )
            avatar = results[0] if not isinstance(results[0], Exception) else None
            cover = results[1] if not isinstance(results[1], Exception) else None

        if page == 0:
            return await asyncio.to_thread(self.generate_profile_info_card, data, avatar, cover)
        elif page == 1:
            return await asyncio.to_thread(self.generate_profile_rank_card, data)
        elif page == 2:
            return await asyncio.to_thread(self.generate_profile_playcount_card, data)
        elif page == 3:
            # Download beatmap BG images for top scores
            bg_images = None
            scores = data.get("top_scores", [])
            if scores:
                bg_urls = []
                for sc in scores[:5]:
                    bsid = sc.get("beatmapset_id", 0)
                    if bsid:
                        bg_urls.append(f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg")
                    else:
                        bg_urls.append(None)
                tasks = [download_image(u) if u else _none_coro() for u in bg_urls]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                bg_images = [
                    r if not isinstance(r, Exception) and r is not None else None
                    for r in results
                ]
            return await asyncio.to_thread(self.generate_profile_top_card, data, bg_images)
        elif page == 4:
            # Download beatmap BG images for recent scores
            bg_images = None
            recent = data.get("recent_scores", [])
            if recent:
                bg_urls = []
                for sc in recent[:5]:
                    bset = (sc.get("beatmapset") or {})
                    bsid = bset.get("id", 0)
                    if bsid:
                        bg_urls.append(f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg")
                    else:
                        bg_urls.append(None)
                tasks = [download_image(u) if u else _none_coro() for u in bg_urls]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                bg_images = [
                    r if not isinstance(r, Exception) and r is not None else None
                    for r in results
                ]
            return await asyncio.to_thread(self.generate_profile_recent_card, data, bg_images)
        else:
            return await asyncio.to_thread(self.generate_profile_info_card, data, avatar, cover)

    # Compare Card  (800 × 620) — with avatars and covers

    def generate_compare_card(
        self, data: Dict,
        avatar1: Optional[Image.Image] = None, cover1: Optional[Image.Image] = None,
        avatar2: Optional[Image.Image] = None, cover2: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 580
        img, draw = self._create_canvas(W, H)

        u1 = data.get("user1", {})
        u2 = data.get("user2", {})
        diffs = data.get("diffs", {})

        half_w = W // 2
        header_h = 36
        cover_h = 180
        cover_top = header_h

        # Header
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — COMPARISON", self.font_subtitle, ACCENT_RED)

        # Cover backgrounds with fade to center
        if cover1:
            cropped1 = cover_center_crop(cover1, half_w, cover_h)
            overlay1 = Image.new("RGBA", (half_w, cover_h), (0, 0, 0, 100))
            cropped1 = Image.alpha_composite(cropped1, overlay1)
            # Right-edge fade: cover fades to BG_COLOR at center
            fade1 = Image.new("L", (half_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = 255 - int(fx / fade_zone * 255)
                ImageDraw.Draw(fade1).line([(half_w - fade_zone + fx, 0), (half_w - fade_zone + fx, cover_h)], fill=alpha)
            img.paste(cropped1.convert("RGB"), (0, cover_top), fade1)
        else:
            draw.rectangle([(0, cover_top), (half_w, cover_top + cover_h)], fill=HEADER_BG)

        if cover2:
            cropped2 = cover_center_crop(cover2, half_w, cover_h)
            overlay2 = Image.new("RGBA", (half_w, cover_h), (0, 0, 0, 100))
            cropped2 = Image.alpha_composite(cropped2, overlay2)
            # Left-edge fade
            fade2 = Image.new("L", (half_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = 255 - int((fade_zone - fx) / fade_zone * 255)
                ImageDraw.Draw(fade2).line([(fx, 0), (fx, cover_h)], fill=alpha)
            img.paste(cropped2.convert("RGB"), (half_w, cover_top), fade2)
        else:
            draw.rectangle([(half_w, cover_top), (W, cover_top + cover_h)], fill=(40, 35, 55))

        draw = ImageDraw.Draw(img)

        # VS text — white, centered in cover area
        vs_y = cover_top + (cover_h - 48) // 2
        self._text_center(draw, W // 2, vs_y, "VS", self.font_vs, TEXT_PRIMARY)

        # Avatars (90×90 rounded rect)
        av_size = 90
        av_y = cover_top + 20
        av1_x = half_w // 2 - av_size // 2
        av2_x = half_w + half_w // 2 - av_size // 2

        if avatar1:
            a1 = rounded_rect_crop(avatar1, av_size, radius=14)
            img.paste(a1, (av1_x, av_y), a1)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (av1_x, av_y, av1_x + av_size, av_y + av_size),
                radius=14, outline=ACCENT_RED, width=2
            )
        else:
            draw.rounded_rectangle(
                (av1_x, av_y, av1_x + av_size, av_y + av_size),
                radius=14, fill=(50, 50, 70), outline=ACCENT_RED, width=2
            )

        if avatar2:
            a2 = rounded_rect_crop(avatar2, av_size, radius=14)
            img.paste(a2, (av2_x, av_y), a2)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (av2_x, av_y, av2_x + av_size, av_y + av_size),
                radius=14, outline=ACCENT_RED, width=2
            )
        else:
            draw.rounded_rectangle(
                (av2_x, av_y, av2_x + av_size, av_y + av_size),
                radius=14, fill=(50, 50, 70), outline=ACCENT_RED, width=2
            )

        draw = ImageDraw.Draw(img)

        # Usernames centered under avatars
        name1 = u1.get("username", "?")
        name2 = u2.get("username", "?")
        name_y = av_y + av_size + 8
        self._text_center(draw, half_w // 2, name_y, name1, self.font_subtitle, TEXT_PRIMARY)
        self._text_center(draw, half_w + half_w // 2, name_y, name2, self.font_subtitle, TEXT_PRIMARY)

        # Comparison table
        metrics = [
            ("PP", f"{u1.get('pp', 0):,}", f"{u2.get('pp', 0):,}", diffs.get("pp", 0), False),
            ("Rank", f"#{u1.get('rank', 0):,}", f"#{u2.get('rank', 0):,}", diffs.get("rank", 0), True),
            ("Accuracy", f"{u1.get('accuracy', 0):.2f}%", f"{u2.get('accuracy', 0):.2f}%", diffs.get("accuracy", 0), False),
            ("Play Count", f"{u1.get('play_count', 0):,}", f"{u2.get('play_count', 0):,}", diffs.get("play_count", 0), False),
            ("Play Time", str(u1.get("play_time", "—")), str(u2.get("play_time", "—")), diffs.get("play_time", 0), False),
            ("Ranked Score", f"{u1.get('ranked_score', 0):,}", f"{u2.get('ranked_score', 0):,}", diffs.get("ranked_score", 0), False),
        ]

        y = cover_top + cover_h + 10
        row_h = 58
        col_left = PADDING_X + 10
        col_center = W // 2
        col_right = W - PADDING_X - 10

        for i, (metric_name, v1, v2, diff_val, invert) in enumerate(metrics):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            ty = ry + (row_h - 22) // 2

            win1 = False
            win2 = False
            if diff_val != 0:
                positive = diff_val > 0
                if invert:
                    positive = not positive
                win1 = positive
                win2 = not positive

            c1 = ACCENT_GREEN if win1 else (ACCENT_RED if win2 else TEXT_PRIMARY)
            c2 = ACCENT_GREEN if win2 else (ACCENT_RED if win1 else TEXT_PRIMARY)

            draw.text((col_left, ty), v1, font=self.font_row, fill=c1)
            self._text_center(draw, col_center, ty, metric_name, self.font_label, TEXT_SECONDARY)
            self._text_right(draw, col_right, ty, v2, self.font_row, c2)

        return self._save(img)

    async def generate_compare_card_async(self, data: Dict) -> BytesIO:
        """Download all 4 images in parallel, then generate the card."""
        u1 = data.get("user1", {})
        u2 = data.get("user2", {})

        results = await asyncio.gather(
            download_image(u1.get("avatar_url")),
            download_image(u1.get("cover_url")),
            download_image(u2.get("avatar_url")),
            download_image(u2.get("cover_url")),
            return_exceptions=True,
        )
        imgs = [r if not isinstance(r, Exception) else None for r in results]

        return await asyncio.to_thread(
            self.generate_compare_card, data,
            imgs[0], imgs[1], imgs[2], imgs[3],
        )

    # Recent Score Card  (800 × 320)

    def generate_recent_card(
        self, data: Dict,
        cover: Optional[Image.Image] = None,
        mapper_avatar: Optional[Image.Image] = None,
        player_avatar: Optional[Image.Image] = None,
        player_cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 470
        img, draw = self._create_canvas(W, H)
        icon_sz = 14
        username = data.get('username', '???')

        bold_path = _find_font(TORUS_BOLD)
        font_pp = ImageFont.truetype(bold_path, 32) if bold_path else self.font_big
        font_grade_xl = ImageFont.truetype(bold_path, 72) if bold_path else self.font_vs

        # Helper: draw text with dark shadow for readability on covers
        def _shadow_text(draw_obj, xy, text, font, fill):
            sx, sy = xy
            draw_obj.text((sx + 1, sy + 1), text, font=font, fill=(0, 0, 0))
            draw_obj.text((sx, sy), text, font=font, fill=fill)

        def _shadow_text_center(draw_obj, cx, y, text, font, fill):
            bbox = draw_obj.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            _shadow_text(draw_obj, (cx - tw // 2, y), text, font, fill)

        # ── 1. HEADER (y=0..36) ──
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, 'PROJECT 1984 — SCORE REPORT', self.font_subtitle, ACCENT_RED)
        self._text_right(draw, W - PADDING_X, 10, username, self.font_small, TEXT_SECONDARY)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        # ── 2. HERO COVER (y=36..176, 140px) ──
        hero_y = header_h
        hero_h = 140

        hero_src = cover or player_cover
        if hero_src:
            cropped = cover_center_crop(hero_src, W, hero_h)
            darkness = 110 if cover else 140
            overlay = Image.new('RGBA', (W, hero_h), (0, 0, 0, darkness))
            cropped = Image.alpha_composite(cropped, overlay)
            # Left-side extra darkening gradient for text readability
            left_shade = Image.new('RGBA', (W, hero_h), (0, 0, 0, 0))
            for lx in range(360):
                alpha = int(80 * (1 - lx / 360))
                ImageDraw.Draw(left_shade).line([(lx, 0), (lx, hero_h)], fill=(0, 0, 0, alpha))
            cropped = Image.alpha_composite(cropped, left_shade)
            # No bottom fade — paste directly flush against header
            img.paste(cropped.convert('RGB'), (0, hero_y))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, hero_y), (W, hero_y + hero_h)], fill=HEADER_BG)

        # Hero overlay: map info (left side) — with shadows
        artist = data.get('artist', 'Unknown')
        title = data.get('title', 'Unknown')
        map_title = f'{title} — {artist}'
        max_tw = 540
        full_title = map_title
        mt_bbox = draw.textbbox((0, 0), map_title, font=self.font_row)
        while mt_bbox[2] - mt_bbox[0] > max_tw and len(map_title) > 4:
            map_title = map_title[:-1]
            mt_bbox = draw.textbbox((0, 0), map_title + '...', font=self.font_row)
        if len(map_title) < len(full_title):
            map_title += '...'
        _shadow_text(draw, (PADDING_X, hero_y + 8), map_title, self.font_row, TEXT_PRIMARY)

        # Mapper avatar + name (with shadows)
        mapper_name = data.get('mapper_name', 'Unknown')
        mav_x, mav_y, mav_sz = PADDING_X, hero_y + 34, 28
        if mapper_avatar:
            mav = rounded_rect_crop(mapper_avatar, mav_sz, radius=6)
            img.paste(mav, (mav_x, mav_y), mav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((mav_x, mav_y, mav_x + mav_sz, mav_y + mav_sz), radius=6, outline=TEXT_SECONDARY, width=2)
        else:
            draw.rounded_rectangle((mav_x, mav_y, mav_x + mav_sz, mav_y + mav_sz), radius=6, fill=(50, 50, 70), outline=TEXT_SECONDARY, width=2)
        mtx = mav_x + mav_sz + 8
        _shadow_text(draw, (mtx, mav_y), 'mapped by', self.font_stat_label, TEXT_SECONDARY)
        _shadow_text(draw, (mtx, mav_y + 14), mapper_name, self.font_small, (200, 200, 210))

        # Star / BPM / Length icons row
        stars = data.get('star_rating', 0.0)
        bpm = data.get('bpm', 0)
        total_length = data.get('total_length', 0)
        row3_y = hero_y + 70
        cur_x = PADDING_X
        star_icon = load_icon('star', size=icon_sz)
        if star_icon:
            img.paste(star_icon, (cur_x, row3_y + 2), star_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), f'{stars:.2f}', self.font_label, TEXT_PRIMARY)
        cur_x += draw.textbbox((0, 0), f'{stars:.2f}', font=self.font_label)[2] + 16
        bpm_icon = load_icon('bpm', size=icon_sz)
        if bpm_icon:
            img.paste(bpm_icon, (cur_x, row3_y + 2), bpm_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), str(bpm), self.font_label, TEXT_PRIMARY)
        cur_x += draw.textbbox((0, 0), str(bpm), font=self.font_label)[2] + 16
        minutes = total_length // 60
        seconds = total_length % 60
        length_str = f'{minutes}:{seconds:02d}'
        timer_icon = load_icon('timer', size=icon_sz)
        if timer_icon:
            img.paste(timer_icon, (cur_x, row3_y + 2), timer_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), length_str, self.font_label, TEXT_PRIMARY)

        # [version] + beatmap status badge
        version = data.get('version', 'Unknown')
        ver_y = hero_y + 94
        ver_text = f'[{version}]'
        ver_bbox = draw.textbbox((0, 0), ver_text, font=self.font_small)
        if ver_bbox[2] - ver_bbox[0] > 260:
            while ver_bbox[2] - ver_bbox[0] > 256 and len(version) > 4:
                version = version[:-1]
                ver_bbox = draw.textbbox((0, 0), f'[{version}...]', font=self.font_small)
            ver_text = f'[{version}...]'
        _shadow_text(draw, (PADDING_X, ver_y), ver_text, self.font_small, TEXT_SECONDARY)

        # Beatmap status badge (Ranked, Loved, Graveyard, etc.)
        STATUS_COLORS = {
            'ranked': (80, 180, 80),
            'approved': (80, 180, 80),
            'qualified': (80, 140, 220),
            'loved': (220, 100, 160),
            'pending': (200, 180, 50),
            'wip': (200, 180, 50),
            'graveyard': (100, 100, 100),
        }
        STATUS_INT_MAP = {
            4: 'loved', 3: 'qualified', 2: 'approved', 1: 'ranked',
            0: 'pending', -1: 'wip', -2: 'graveyard',
        }
        raw_status = data.get('beatmap_status', '')
        if isinstance(raw_status, int):
            beatmap_status = STATUS_INT_MAP.get(raw_status, '')
        else:
            beatmap_status = str(raw_status) if raw_status else ''
        if beatmap_status:
            status_label = beatmap_status.upper()
            status_color = STATUS_COLORS.get(beatmap_status.lower(), (100, 100, 120))
            ver_end_bbox = draw.textbbox((0, 0), ver_text, font=self.font_small)
            status_x = PADDING_X + ver_end_bbox[2] - ver_end_bbox[0] + 10
            sb_bbox = draw.textbbox((0, 0), status_label, font=self.font_stat_label)
            sb_w = sb_bbox[2] - sb_bbox[0] + 12
            sb_h = 18
            draw.rounded_rectangle((status_x, ver_y + 1, status_x + sb_w, ver_y + 1 + sb_h), radius=4, fill=status_color)
            self._text_center(draw, status_x + sb_w // 2, ver_y + 2, status_label, self.font_stat_label, (255, 255, 255))

        # Mod badges (right-aligned colored pills, more rounded)
        mods = data.get('mods', '')
        if mods:
            mod_cur_x = W - PADDING_X
            mod_y = hero_y + 10
            mod_list = [mods[i:i + 2] for i in range(0, len(mods), 2) if mods[i:i + 2]]
            for mod_name in reversed(mod_list):
                mod_color = MOD_COLORS.get(mod_name, (100, 100, 120))
                badge_w = 42
                badge_h = 22
                bx = mod_cur_x - badge_w
                draw.rounded_rectangle((bx, mod_y, bx + badge_w, mod_y + badge_h), radius=11, fill=mod_color)
                self._text_center(draw, bx + badge_w // 2, mod_y + 3, mod_name, self.font_stat_label, (255, 255, 255))
                mod_cur_x = bx - 4

        # Accent line under hero — colored by beatmap status if available
        hero_line_color = status_color if beatmap_status else ACCENT_RED
        draw.line([(0, hero_y + hero_h), (W, hero_y + hero_h)], fill=hero_line_color, width=2)

        # ── 3. SCORE ZONE (y=178..342, 164px) ──
        score_y = hero_y + hero_h + 2
        acc = data.get('accuracy', 0.0)
        combo = data.get('combo', 0)
        max_combo = data.get('max_combo', 0)
        misses = data.get('misses', 0)
        pp = data.get('pp', 0.0)
        pp_if_fc = data.get('pp_if_fc', 0.0)
        rank_grade = data.get('rank_grade', 'F')
        total_score = data.get('total_score', 0)
        count_300 = data.get('count_300', 0)
        count_100 = data.get('count_100', 0)
        count_50 = data.get('count_50', 0)
        total_objects = data.get('total_objects', 0)
        is_passed = data.get('passed', rank_grade != 'F')

        # Completion percentage
        hit_objects = count_300 + count_100 + count_50 + misses
        if total_objects and total_objects > 0:
            completion = min(hit_objects / total_objects * 100, 100.0)
        else:
            completion = 100.0 if is_passed else 0.0

        is_fc = misses == 0 and is_passed
        is_ss = rank_grade in ('X', 'XH') or (acc >= 100.0 and is_passed)

        # Grade circle (left, x center=90) — with tinted glow background and thick outline
        grade_cx = 90
        grade_cy = score_y + 68
        circle_r = 56
        grade_color = GRADE_COLORS.get(rank_grade, TEXT_PRIMARY)
        # Dimmed grade color glow
        glow_r = int(grade_color[0] * 0.15)
        glow_g = int(grade_color[1] * 0.15)
        glow_b = int(grade_color[2] * 0.15)
        circle_img = Image.new('RGBA', (circle_r * 2, circle_r * 2), (0, 0, 0, 0))
        circle_draw = ImageDraw.Draw(circle_img)
        circle_draw.ellipse((0, 0, circle_r * 2 - 1, circle_r * 2 - 1), fill=(glow_r, glow_g, glow_b, 200))
        # Thick outline in grade color (dimmed)
        outline_color = (min(grade_color[0], 255), min(grade_color[1], 255), min(grade_color[2], 255), 160)
        circle_draw.ellipse((2, 2, circle_r * 2 - 3, circle_r * 2 - 3), outline=outline_color, width=4)
        img.paste(circle_img, (grade_cx - circle_r, grade_cy - circle_r), circle_img)
        draw = ImageDraw.Draw(img)
        # Center grade text precisely using full bbox
        grade_bbox = draw.textbbox((0, 0), rank_grade, font=font_grade_xl)
        grade_tw = grade_bbox[2] - grade_bbox[0]
        grade_th = grade_bbox[3] - grade_bbox[1]
        grade_tx = grade_cx - grade_tw // 2
        grade_ty = grade_cy - grade_th // 2 - grade_bbox[1]
        draw.text((grade_tx, grade_ty), rank_grade, font=font_grade_xl, fill=grade_color)

        # Badges under grade circle: FC, SS, Completion + PP if FC / PP if SS
        pp_if_fc = data.get('pp_if_fc', 0.0)
        pp_if_ss = data.get('pp_if_ss', 0.0)

        badge_y = grade_cy + circle_r + 4
        badge_h = 16
        badge_gap = 3

        # Row 1: status badges (FC, SS, completion %)
        row1_badges = []
        if is_fc:
            row1_badges.append(('FC', ACCENT_GREEN))
        if is_ss:
            row1_badges.append(('SS', (255, 215, 0)))
        if not is_passed or completion < 100.0:
            row1_badges.append((f'{completion:.0f}%', ACCENT_RED if completion < 50 else (200, 180, 50)))

        def _draw_badge_row(badges, y):
            if not badges:
                return
            specs = []
            tw = 0
            for label, color in badges:
                bb = draw.textbbox((0, 0), label, font=self.font_stat_label)
                bw = bb[2] - bb[0] + 10
                specs.append((label, color, bw))
                tw += bw
            tw += badge_gap * (len(specs) - 1)
            bx = grade_cx - tw // 2
            for label, color, bw in specs:
                draw.rounded_rectangle((bx, y, bx + bw, y + badge_h), radius=4, fill=color)
                self._text_center(draw, bx + bw // 2, y + 1, label, self.font_stat_label, (255, 255, 255))
                bx += bw + badge_gap

        _draw_badge_row(row1_badges, badge_y)

        # Row 2: PP if FC / PP if SS
        row2_badges = []
        if pp_if_fc and not is_fc:
            row2_badges.append((f'FC: {pp_if_fc:.0f}pp', (60, 140, 60)))
        if pp_if_ss and not is_ss:
            row2_badges.append((f'SS: {pp_if_ss:.0f}pp', (180, 155, 20)))

        _draw_badge_row(row2_badges, badge_y + badge_h + 3)

        # Top row: PP, Accuracy, Combo (3 panels)
        stats_x = 170
        stats_w = W - PADDING_X - stats_x
        panel_gap = 12
        panel_w = (stats_w - 2 * panel_gap) // 3
        panel_h = 68
        top_row_y = score_y + 6

        # PP panel
        pp_x = stats_x
        self._draw_panel(draw, pp_x, top_row_y, panel_w, panel_h)
        draw.text((pp_x + 10, top_row_y + 6), 'PP', font=self.font_stat_label, fill=TEXT_SECONDARY)
        pp_str = f'{pp:.0f}' if pp > 0 else '—'
        self._text_center(draw, pp_x + panel_w // 2, top_row_y + 20, pp_str, font_pp, TEXT_PRIMARY)
        if pp_if_fc and misses > 0:
            self._text_center(draw, pp_x + panel_w // 2, top_row_y + 52, f'({pp_if_fc:.0f} if FC)', self.font_stat_label, TEXT_SECONDARY)

        # Accuracy panel
        acc_x = stats_x + panel_w + panel_gap
        self._draw_panel(draw, acc_x, top_row_y, panel_w, panel_h)
        draw.text((acc_x + 10, top_row_y + 6), 'ACCURACY', font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_center(draw, acc_x + panel_w // 2, top_row_y + 24, f'{acc:.2f}%', self.font_stat_value, TEXT_PRIMARY)

        # Combo panel
        combo_x = stats_x + 2 * (panel_w + panel_gap)
        self._draw_panel(draw, combo_x, top_row_y, panel_w, panel_h)
        draw.text((combo_x + 10, top_row_y + 6), 'COMBO', font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_center(draw, combo_x + panel_w // 2, top_row_y + 24, f'{combo}x', self.font_stat_value, TEXT_PRIMARY)
        if max_combo:
            combo_sub = f'/ {max_combo}x'
            if combo == max_combo:
                combo_sub = 'FULL COMBO'
                self._text_center(draw, combo_x + panel_w // 2, top_row_y + 52, combo_sub, self.font_stat_label, ACCENT_GREEN)
            else:
                self._text_center(draw, combo_x + panel_w // 2, top_row_y + 52, combo_sub, self.font_stat_label, TEXT_SECONDARY)

        # Bottom row: Score, 300, 100, 50, Misses (5 panels — misses last/rightmost)
        bot_row_y = top_row_y + panel_h + 8
        bot_h = 68
        bot_gap = 5
        score_w, hit_w, miss_w = 180, 100, 100
        bx = stats_x

        # Score
        self._draw_panel(draw, bx, bot_row_y, score_w, bot_h)
        self._text_center(draw, bx + score_w // 2, bot_row_y + 8, 'SCORE', self.font_stat_label, TEXT_SECONDARY)
        self._text_center(draw, bx + score_w // 2, bot_row_y + 28, f'{total_score:,}', self.font_label, TEXT_PRIMARY)
        bx += score_w + bot_gap

        # 300 / 100 / 50
        hit_colors = {'300': (80, 200, 80), '100': (200, 180, 50), '50': (200, 100, 50)}
        for hit_label, hit_val in [('300', count_300), ('100', count_100), ('50', count_50)]:
            self._draw_panel(draw, bx, bot_row_y, hit_w, bot_h)
            hc = hit_colors[hit_label]
            self._text_center(draw, bx + hit_w // 2, bot_row_y + 8, hit_label, self.font_stat_label, hc)
            self._text_center(draw, bx + hit_w // 2, bot_row_y + 28, str(hit_val), self.font_label, hc)
            bx += hit_w + bot_gap

        # Misses (rightmost)
        self._draw_panel(draw, bx, bot_row_y, miss_w, bot_h)
        miss_val = str(misses) if misses > 0 else 'FC'
        miss_val_color = ACCENT_RED if misses > 0 else ACCENT_GREEN
        self._text_center(draw, bx + miss_w // 2, bot_row_y + 8, 'MISSES', self.font_stat_label, ACCENT_RED)
        self._text_center(draw, bx + miss_w // 2, bot_row_y + 28, miss_val, self.font_label, miss_val_color)

        # Red accent line
        line_y = bot_row_y + bot_h + 8
        draw.line([(0, line_y), (W, line_y)], fill=ACCENT_RED, width=1)

        # ── 4. DIFFICULTY + PLAYER (y after line..470) ──
        band4_y = line_y + 2
        band4_h = H - band4_y

        # Player cover background — only right side (player corner), not over difficulty
        player_zone_x = 400
        player_zone_w = W - player_zone_x
        player_bg = player_cover or cover
        if player_bg:
            pcrop = cover_center_crop(player_bg, player_zone_w, band4_h)
            p_overlay = Image.new('RGBA', (player_zone_w, band4_h), (0, 0, 0, 160))
            pcrop = Image.alpha_composite(pcrop, p_overlay)
            # Left fade: blends into BG_COLOR
            pfade = Image.new('L', (player_zone_w, band4_h), 255)
            fade_w = 80
            for fx in range(fade_w):
                alpha = int(fx / fade_w * 255)
                ImageDraw.Draw(pfade).line([(fx, 0), (fx, band4_h)], fill=alpha)
            # Top fade: blends into score zone above
            top_fade = 14
            fade_draw = ImageDraw.Draw(pfade)
            for fy in range(top_fade):
                alpha_row = int(fy / top_fade * 255)
                fade_draw.line([(0, fy), (player_zone_w, fy)], fill=min(alpha_row, alpha_row))
            # Combine top fade with left fade (take minimum)
            pfade_data = pfade.load()
            for fy in range(top_fade):
                alpha_row = int(fy / top_fade * 255)
                for px_i in range(player_zone_w):
                    pfade_data[px_i, fy] = min(pfade_data[px_i, fy], alpha_row)
            img.paste(pcrop.convert('RGB'), (player_zone_x, band4_y), pfade)
            draw = ImageDraw.Draw(img)

        # Difficulty section (left)
        draw.text((PADDING_X, band4_y + 4), 'DIFFICULTY', font=self.font_label, fill=ACCENT_RED)
        diff_grid_y = band4_y + 26
        diff_pw, diff_ph = 170, 40
        diff_col_gap, diff_row_gap = 14, 6
        params = [
            ('CS', data.get('cs', 0.0), 10.0),
            ('AR', data.get('ar', 0.0), 11.0),
            ('OD', data.get('od', 0.0), 11.0),
            ('HP', data.get('hp', 0.0), 10.0),
        ]
        for i, (label, val, max_val) in enumerate(params):
            col = i % 2
            row = i // 2
            px = PADDING_X + col * (diff_pw + diff_col_gap)
            py = diff_grid_y + row * (diff_ph + diff_row_gap)
            self._draw_panel(draw, px, py, diff_pw, diff_ph)
            draw.text((px + 10, py + 10), label, font=self.font_label, fill=TEXT_SECONDARY)
            val_str = f'{val:.1f}' if isinstance(val, float) else str(val)
            self._text_right(draw, px + diff_pw - 10, py + 10, val_str, self.font_label, TEXT_PRIMARY)
            # Proportion bar
            proportion = min(float(val) / max_val, 1.0) if max_val else 0
            bar_max_w = diff_pw - 8
            bar_w = int(bar_max_w * proportion)
            if bar_w > 0:
                t = proportion
                bar_r = int(ACCENT_GREEN[0] * (1 - t) + ACCENT_RED[0] * t)
                bar_g = int(ACCENT_GREEN[1] * (1 - t) + ACCENT_RED[1] * t)
                bar_b = int(ACCENT_GREEN[2] * (1 - t) + ACCENT_RED[2] * t)
                draw.line([(px + 4, py + diff_ph - 4), (px + 4 + bar_w, py + diff_ph - 4)], fill=(bar_r, bar_g, bar_b), width=2)

        # Player section (right, centered in the player zone corner)
        pav_sz = 56
        player_cx = player_zone_x + player_zone_w // 2
        pav_x = player_cx - pav_sz // 2
        pav_y = band4_y + 10
        if player_avatar:
            pav = rounded_rect_crop(player_avatar, pav_sz, radius=12)
            img.paste(pav, (pav_x, pav_y), pav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((pav_x - 1, pav_y - 1, pav_x + pav_sz + 1, pav_y + pav_sz + 1), radius=12, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((pav_x, pav_y, pav_x + pav_sz, pav_y + pav_sz), radius=12, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        self._text_center(draw, player_cx, pav_y + pav_sz + 6, 'Played by', self.font_stat_label, TEXT_SECONDARY)
        uname_display = username
        uname_max_w = player_zone_w - 20
        uname_bbox = draw.textbbox((0, 0), uname_display, font=self.font_label)
        while uname_bbox[2] - uname_bbox[0] > uname_max_w and len(uname_display) > 3:
            uname_display = uname_display[:-1]
            uname_bbox = draw.textbbox((0, 0), uname_display + '..', font=self.font_label)
        if len(uname_display) < len(username):
            uname_display += '..'
        self._text_center(draw, player_cx, pav_y + pav_sz + 20, uname_display, self.font_label, TEXT_PRIMARY)

        return self._save(img)

    async def generate_recent_card_async(self, data: Dict) -> BytesIO:
        bsid = data.get("beatmapset_id", 0)
        mapper_id = data.get("mapper_id", 0)
        player_id = data.get("player_id", 0)
        player_cover_url = data.get("player_cover_url") or None

        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        mapper_avatar_url = f"https://a.ppy.sh/{mapper_id}" if mapper_id else None
        player_avatar_url = f"https://a.ppy.sh/{player_id}" if player_id else None

        cover, mapper_avatar, player_avatar, player_cover = await asyncio.gather(
            download_image(cover_url) if cover_url else _none_coro(),
            download_image(mapper_avatar_url) if mapper_avatar_url else _none_coro(),
            download_image(player_avatar_url) if player_avatar_url else _none_coro(),
            download_image(player_cover_url) if player_cover_url else _none_coro(),
        )
        return await asyncio.to_thread(
            self.generate_recent_card, data, cover, mapper_avatar, player_avatar, player_cover
        )

    # HPS Card  (800 × 520)

    def generate_hps_card(self, data: Dict, cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = 800, 520
        img, draw = self._create_canvas(W, H)

        # Compact header bar (like compare)
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — HPS ANALYSIS", self.font_subtitle, ACCENT_RED)

        # Cover zone: dark left panel + cover BG right
        cover_top = header_h
        cover_h = 150
        left_w = 360  # dark info zone

        # Right side: cover image
        if cover:
            right_w = W - left_w
            cropped = cover_center_crop(cover, right_w, cover_h)
            overlay = Image.new("RGBA", (right_w, cover_h), (0, 0, 0, 100))
            cropped = Image.alpha_composite(cropped, overlay)
            # Left-edge fade so it blends into the dark zone
            fade = Image.new("L", (right_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = int(fx / fade_zone * 255)
                ImageDraw.Draw(fade).line([(fx, 0), (fx, cover_h)], fill=alpha)
            img.paste(cropped.convert("RGB"), (left_w, cover_top), fade)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(left_w, cover_top), (W, cover_top + cover_h)], fill=(40, 35, 55))

        # Left side: dark zone (already BG_COLOR from canvas)
        draw.rectangle([(0, cover_top), (left_w, cover_top + cover_h)], fill=BG_COLOR)

        # Map title (left zone) — title first, then artist; truncate to fit
        text_x = PADDING_X
        max_title_w = left_w - text_x - 10
        raw_title = data.get("map_title", "???")
        # Rearrange: show "title - artist" if original is "artist - title"
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            map_title = f"{parts[1]} - {parts[0]}"
        else:
            map_title = raw_title
        # Truncate to fit available width
        bbox_t = draw.textbbox((0, 0), map_title, font=self.font_row)
        while bbox_t[2] - bbox_t[0] > max_title_w and len(map_title) > 4:
            map_title = map_title[:-1]
            bbox_t = draw.textbbox((0, 0), map_title + "...", font=self.font_row)
        if bbox_t[2] - bbox_t[0] > max_title_w:
            map_title = map_title + "..."
        elif len(map_title) < len(raw_title):
            map_title = map_title + "..."
        draw.text((text_x, cover_top + 12), map_title, font=self.font_row, fill=TEXT_PRIMARY)

        # Mapper avatar + [version] | mapper name
        version = data.get("map_version", "")
        creator = data.get("creator", "")
        av_size = 48
        av_y = cover_top + 40
        mapper_avatar = data.get("_mapper_avatar")  # injected by async method
        if mapper_avatar:
            cropped_av = rounded_rect_crop(mapper_avatar, av_size, radius=10)
            img.paste(cropped_av, (text_x, av_y), cropped_av)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (text_x, av_y, text_x + av_size, av_y + av_size),
                radius=10, outline=ACCENT_RED, width=2
            )
        else:
            draw.rounded_rectangle(
                (text_x, av_y, text_x + av_size, av_y + av_size),
                radius=10, fill=(50, 50, 70), outline=ACCENT_RED, width=2
            )

        # Version and mapper name to the right of avatar
        info_x = text_x + av_size + 10
        max_info_w = left_w - info_x - 5
        if version:
            version_str = f"[{version}]"
            # Truncate version if too long
            vbbox = draw.textbbox((0, 0), version_str, font=self.font_small)
            if vbbox[2] - vbbox[0] > max_info_w:
                while len(version_str) > 4 and draw.textbbox((0, 0), version_str + "...]", font=self.font_small)[2] > max_info_w:
                    version_str = version_str[:-1]
                version_str = version_str + "...]"
            draw.text((info_x, av_y + 4), version_str, font=self.font_small, fill=TEXT_SECONDARY)
            if creator:
                draw.text((info_x, av_y + 24), creator, font=self.font_label, fill=TEXT_PRIMARY)
        elif creator:
            draw.text((info_x, av_y + 12), creator, font=self.font_label, fill=TEXT_PRIMARY)

        # Multiplier below avatar row
        multiplier = data.get("total_multiplier", 1.0)
        mult_y = av_y + av_size + 10
        draw.text((text_x, mult_y), "MULTIPLIER:", font=self.font_stat_label, fill=TEXT_SECONDARY)
        mult_bbox = draw.textbbox((0, 0), "MULTIPLIER:", font=self.font_stat_label)
        label_w = mult_bbox[2] - mult_bbox[0]
        draw.text((text_x + label_w + 5, mult_y - 2), f"x{multiplier:.2f}", font=self.font_label, fill=ACCENT_RED)

        # Red accent line under cover
        draw.line([(0, cover_top + cover_h), (W, cover_top + cover_h)], fill=ACCENT_RED, width=2)

        # Body: left = map params, right = map info
        body_top = cover_top + cover_h + 12
        half_w = W // 2

        # Left side: MAP PARAMETERS
        draw.text((PADDING_X, body_top), "MAP PARAMETERS", font=self.font_label, fill=ACCENT_RED)
        param_y = body_top + 26
        param_keys = [("cs", "CS"), ("od", "OD"), ("ar", "AR"), ("hp", "HP")]
        # Two columns of params
        col1_x = PADDING_X
        col2_x = PADDING_X + 110
        for idx, (key, label) in enumerate(param_keys):
            val = data.get(key, 0)
            val_str = f"{val:.1f}" if isinstance(val, float) else str(val)
            px = col1_x if idx % 2 == 0 else col2_x
            py = param_y + (idx // 2) * 40
            pw = 90
            self._draw_panel(draw, px, py, pw, 32)
            draw.text((px + 8, py + 6), label, font=self.font_label, fill=TEXT_SECONDARY)
            self._text_right(draw, px + pw - 8, py + 6, val_str, self.font_label, TEXT_PRIMARY)

        # Right side: MAP INFORMATION
        stars = data.get("star_rating", 0.0)
        duration = data.get("duration", 0)
        bpm = data.get("bpm", 0.0)
        dur_str = f"{duration // 60}:{duration % 60:02d}"
        max_combo = data.get("max_combo", 0)

        draw.text((half_w + 20, body_top), "MAP INFORMATION", font=self.font_label, fill=ACCENT_RED)
        info_y = body_top + 26
        info_items = [
            ("Stars", f"{stars:.2f}", True),   # True = show star icon
            ("Duration", dur_str, False),
            ("BPM", f"{bpm:.0f}", False),
            ("Combo", f"{max_combo:,}x" if max_combo else "—", False),
        ]
        star_icon = load_icon("star", size=14)
        for idx, (label, val, has_star) in enumerate(info_items):
            px = half_w + 20 if idx % 2 == 0 else half_w + 20 + 180
            py = info_y + (idx // 2) * 40
            pw = 165
            self._draw_panel(draw, px, py, pw, 32)
            draw.text((px + 8, py + 6), label, font=self.font_small, fill=TEXT_SECONDARY)
            if has_star and star_icon:
                # Value + star icon
                val_bbox = draw.textbbox((0, 0), val, font=self.font_label)
                val_w = val_bbox[2] - val_bbox[0]
                icon_gap = 4
                total = val_w + icon_gap + star_icon.width
                vx = px + pw - 8 - total
                draw.text((vx, py + 6), val, font=self.font_label, fill=TEXT_PRIMARY)
                img.paste(star_icon, (vx + val_w + icon_gap, py + 6), star_icon)
                draw = ImageDraw.Draw(img)
            else:
                self._text_right(draw, px + pw - 8, py + 6, val, self.font_label, TEXT_PRIMARY)

        # HP Scenarios — 4 panels in a row
        scenarios = data.get("scenarios", [])
        panel_y = body_top + 132
        panel_h = 64
        gap = 10
        n_panels = max(len(scenarios), 1)
        panel_w = (W - PADDING_X * 2 - gap * (n_panels - 1)) // n_panels

        draw.text((PADDING_X, panel_y - 22), "POTENTIAL HP REWARDS", font=self.font_label, fill=ACCENT_RED)

        for i, sc in enumerate(scenarios[:4]):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panel_y, panel_w, panel_h)

            hp_reward = sc.get("hp_reward", 0)
            name = sc.get("name", "?")

            hp_str = f"{hp_reward} HP"
            self._text_center(draw, px + panel_w // 2, panel_y + 8, hp_str, self.font_stat_value, ACCENT_GREEN)
            self._text_center(draw, px + panel_w // 2, panel_y + 42, name, self.font_stat_label, TEXT_SECONDARY)

        # Agent data — 3 panels
        agent_y = panel_y + panel_h + 18
        agent_h = 50
        agent_gap = 10
        agent_pw = (W - PADDING_X * 2 - agent_gap * 2) // 3

        player_pp = data.get("player_pp", 0)
        rf_value = data.get("rf_value", 1.0)
        rf_cat = data.get("rf_category", "")
        tsf_value = data.get("tsf_value", 1.0)

        agent_items = [
            (f"{player_pp:,} PP", "YOUR PP"),
            (f"x{rf_value:.2f} ({rf_cat})", "PROGRESS"),
            (f"x{tsf_value:.2f}", "TECH SKILL"),
        ]

        for i, (val, label) in enumerate(agent_items):
            px = PADDING_X + i * (agent_pw + agent_gap)
            self._draw_panel(draw, px, agent_y, agent_pw, agent_h)
            cell_cx = px + agent_pw // 2
            self._text_center(draw, cell_cx, agent_y + 6, val, self.font_label, TEXT_PRIMARY)
            self._text_center(draw, cell_cx, agent_y + 28, label, self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_hps_card_async(self, data: Dict) -> BytesIO:
        # Download cover and mapper avatar in parallel
        bsid = data.get("beatmapset_id", 0)
        creator_id = data.get("creator_id", 0)
        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        avatar_url = f"https://a.ppy.sh/{creator_id}" if creator_id else None
        results = await asyncio.gather(
            download_image(cover_url) if cover_url else _none_coro(),
            download_image(avatar_url) if avatar_url else _none_coro(),
            return_exceptions=True,
        )
        cover = results[0] if not isinstance(results[0], Exception) else None
        mapper_avatar = results[1] if not isinstance(results[1], Exception) else None
        data["_mapper_avatar"] = mapper_avatar
        return await asyncio.to_thread(self.generate_hps_card, data, cover)

    # Bounty List Card  (800 × dynamic)

    def generate_bountylist_card(self, entries: List[Dict]) -> BytesIO:
        """PNG card showing a list of active bounties (compact row-based)."""
        num_rows = max(len(entries), 1)
        header_h = 36
        row_h = 60
        footer_h = 40
        H = header_h + num_rows * row_h + 8 + footer_h

        img, draw = self._create_canvas(CARD_WIDTH, H)

        count_str = f"{len(entries)}" if entries else "0"
        self._draw_header(draw, "PROJECT 1984 — ACTIVE BOUNTIES", count_str, CARD_WIDTH)

        if not entries:
            y = header_h + (row_h - 24) // 2
            draw.text(
                (PADDING_X, y), "No active bounties",
                font=self.font_row, fill=TEXT_SECONDARY,
            )
        else:
            for i, entry in enumerate(entries):
                y_top = header_h + i * row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y_top), (CARD_WIDTH, y_top + row_h)], fill=row_bg)

                y_text = y_top + 10
                y_sub = y_top + 34

                # Left side: bounty ID + title
                bid = entry.get("bounty_id", "?")
                draw.text((PADDING_X, y_text), f"#{bid}", font=self.font_row, fill=ACCENT_RED)

                bid_bbox = draw.textbbox((0, 0), f"#{bid}", font=self.font_row)
                bid_w = bid_bbox[2] - bid_bbox[0]

                title = entry.get("title", "—")
                if len(title) > 40:
                    title = title[:37] + "..."
                draw.text(
                    (PADDING_X + bid_w + 12, y_text), title,
                    font=self.font_row, fill=TEXT_PRIMARY,
                )

                # Bottom sub-row: stars | deadline | participants
                stars = entry.get("star_rating", 0.0)
                deadline = entry.get("deadline", "—")
                p_count = entry.get("participant_count", 0)
                max_p = entry.get("max_participants")
                p_str = f"{p_count}/{max_p}" if max_p else str(p_count)

                sub_text = f"{stars:.2f}\u2605  |  {deadline}  |  {p_str}"
                draw.text(
                    (PADDING_X + bid_w + 12, y_sub), sub_text,
                    font=self.font_small, fill=TEXT_SECONDARY,
                )

        return self._save(img)

    async def generate_bountylist_card_async(self, entries: List[Dict]) -> BytesIO:
        return await asyncio.to_thread(self.generate_bountylist_card, entries)

    # Bounty Card  (800 × dynamic)

    def generate_bounty_card(self, data: Dict) -> BytesIO:
        conditions = data.get("conditions", [])
        num_cond = max(len(conditions), 1)
        cond_block = num_cond * 22 + 40
        H = 36 + 155 + cond_block + 90 + 50
        H = max(H, 396)
        W = 800
        img, draw = self._create_canvas(W, H)

        bounty_id = data.get("bounty_id", "?")
        self._draw_header(draw, "PROJECT 1984 — BOUNTY DIRECTIVE", f"#{bounty_id}", W)

        y = 44
        self._draw_section_title(draw, y, "MISSION BRIEFING")
        y += 28

        for label, key, fmt in [
            ("Type", "bounty_type", None),
            ("Title", "title", None),
            ("Map", "beatmap_title", None),
            ("Difficulty", "star_rating", ".2f★"),
            ("Duration", "duration", "time"),
            ("Status", "status", None),
        ]:
            val = data.get(key, "—")
            if fmt == ".2f★" and isinstance(val, (int, float)):
                val_str = f"{val:.2f}★"
            elif fmt == "time" and isinstance(val, (int, float)):
                val_str = f"{int(val) // 60}:{int(val) % 60:02d}"
            else:
                val_str = str(val)

            if key == "status":
                status_color = ACCENT_GREEN if val_str.lower() == "active" else ACCENT_RED
                self._draw_kv_row(draw, y, label, val_str, value_fill=status_color)
            else:
                self._draw_kv_row(draw, y, label, val_str)
            y += 22

        y += 8
        self._draw_separator(draw, y, W)
        y += 8
        self._draw_section_title(draw, y, "REQUIREMENTS")
        y += 28

        if conditions:
            for cond in conditions:
                draw.text((PADDING_X + 10, y), f"• {cond}", font=self.font_label, fill=TEXT_PRIMARY)
                y += 22
        else:
            draw.text((PADDING_X + 10, y), "• None", font=self.font_label, fill=TEXT_SECONDARY)
            y += 22

        y += 8
        self._draw_separator(draw, y, W)
        y += 8
        self._draw_section_title(draw, y, "FIELD REPORT")
        y += 28

        participant_count = data.get("participant_count", 0)
        max_participants = data.get("max_participants")
        p_str = str(participant_count)
        if max_participants:
            p_str += f"/{max_participants}"
        self._draw_kv_row(draw, y, "Participants", p_str)
        y += 22

        deadline = data.get("deadline", "—")
        self._draw_kv_row(draw, y, "Deadline", str(deadline))
        y += 22

        hps_preview = data.get("hps_preview_hp")
        if hps_preview is not None:
            self._draw_kv_row(draw, y, "HPS Preview (Win)", f"~{hps_preview} HP", value_fill=ACCENT_GREEN)
            y += 22

        return self._save(img)

    async def generate_bounty_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bounty_card, data)

    # Help Cards

    # Category definitions for help cards
    HELP_CATEGORIES = {
        "osu": {
            "title": "osu! COMMANDS",
            "commands": [
                {"name": "profile, pf", "desc": "Your stats and rank"},
                {"name": "rs, recent", "desc": "Last played map"},
                {"name": "lb, leaderboard, top", "desc": "Leaderboard (9 categories)"},
                {"name": "leaderboardmap, lbm", "desc": "Map leaderboard (disabled)"},
                {"name": "unlink, unregister, unreg", "desc": "Remove osu! link once per month"},
                {"name": "compare [username]", "desc": "Compare with another player"},
                {"name": "refresh", "desc": "Force sync with osu!"},
            ],
        },
        "duel": {
            "title": "DUEL SYSTEM",
            "commands": [
                {"name": "duel [player] [bo3/bo5/bo7]", "desc": "Challenge a registered player"},
                {"name": "duelhistory, dh", "desc": "View recent completed duels"},
                {"name": "duelresult, dr", "desc": "Check the current round result"},
                {"name": "duelstats, ds", "desc": "View your duel record"},
                {"name": "duelcancel, dc", "desc": "Cancel your active duel"},
            ],
        },
        "hps": {
            "title": "HPS SYSTEM",
            "commands": [
                {"name": "hps [link/id]", "desc": "Analyze map potential"},
            ],
        },
        "bounty": {
            "title": "BOUNTY SYSTEM",
            "commands": [
                {"name": "bountylist, bli", "desc": "Active bounties list"},
                {"name": "bountydetails, bde [id]", "desc": "Bounty details"},
                {"name": "submit [id]", "desc": "Submit bounty entry"},
            ],
        },
        "account": {
            "title": "ACCOUNT",
            "commands": [
                {"name": "register, reg [username]", "desc": "Register in the system"},
                {"name": "start", "desc": "Welcome message"},
            ],
        },
        "about": {
            "title": "ABOUT PROJECT",
            "text": (
                "Project 1984 is an automated bounty management\n"
                "system built for the osu! community.\n\n"
                "Designed to track, calculate, and reward\n"
                "outstanding player achievements."
            ),
        },
    }

    def generate_help_main_card(self) -> BytesIO:
        """Overview help card with category panels."""
        W = CARD_WIDTH
        header_h = 36
        panel_h = 56
        gap = 8
        cats = list(self.HELP_CATEGORIES.items())
        content_h = len(cats) * (panel_h + gap) + 78  # extra space for disabled note
        footer_h = 40
        H = header_h + 20 + content_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — HELP", "", W)

        disabled_note = "lbm / leaderboardmap are temporarily disabled"
        y = header_h + 16
        self._text_center(draw, W // 2, y, "Select a category below", self.font_label, TEXT_SECONDARY)
        y += 28
        self._text_center(draw, W // 2, y, disabled_note, self.font_small, ACCENT_RED)
        y += 28

        cat_icon_names = {
            "osu": "osulogo",
            "hps": "hpssystem",
            "duel": "versus",
            "bounty": "bounty",
            "account": "account",
            "about": "information",
        }

        cat_descriptions = {
            "osu": "Profile, recent scores, leaderboards",
            "hps": "Map potential analysis",
            "duel": "Challenges, history, results, stats, cancel",
            "bounty": "Bounty list, details, submissions",
            "account": "Registration and settings",
            "about": "About this project",
        }

        disabled_note = "lbm / leaderboardmap are temporarily disabled"


        icon_sz_help = 28
        for code, cat_def in cats:
            self._draw_panel(draw, PADDING_X, y, W - 2 * PADDING_X, panel_h)

            icon_name = cat_icon_names.get(code)
            icon_img = load_icon(icon_name, size=icon_sz_help) if icon_name else None
            text_offset = PADDING_X + 14
            if icon_img:
                icon_y = y + (panel_h - icon_sz_help) // 2
                img.paste(icon_img, (PADDING_X + 12, icon_y), icon_img)
                draw = ImageDraw.Draw(img)
                text_offset = PADDING_X + 12 + icon_sz_help + 10

            title = cat_def["title"]
            draw.text((text_offset, y + 8), title, font=self.font_row, fill=TEXT_PRIMARY)

            desc = cat_descriptions.get(code, "")
            draw.text((text_offset, y + 32), desc, font=self.font_small, fill=TEXT_SECONDARY)

            y += panel_h + gap

        return self._save(img)

    def generate_help_card(self, category: str) -> BytesIO:
        """Category-specific help card with command list or text block."""
        cat_def = self.HELP_CATEGORIES.get(category)
        if not cat_def:
            return self.generate_help_main_card()

        W = CARD_WIDTH
        header_h = 36
        footer_h = 40

        # "about" category — text block instead of command rows
        if "text" in cat_def:
            lines = cat_def["text"].split("\n")
            content_h = len(lines) * 24 + 30
            H = header_h + content_h + footer_h

            img, draw = self._create_canvas(W, H)
            self._draw_header(draw, "PROJECT 1984 — HELP", cat_def["title"], W)

            y = header_h + 16
            for line in lines:
                draw.text((PADDING_X, y), line, font=self.font_label, fill=TEXT_PRIMARY if line.strip() else TEXT_SECONDARY)
                y += 24

            footer_y = H - footer_h
            self._draw_footer(draw, img, "BIG BROTHER IS WATCHING YOUR RANK", footer_y, W)
            return self._save(img)

        # Command list layout
        commands = cat_def.get("commands", [])
        row_h = 44
        content_h = max(len(commands), 1) * row_h + 16
        H = header_h + content_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — HELP", cat_def["title"], W)

        y = header_h + 8
        for i, cmd in enumerate(commands):
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

            name = cmd["name"]
            desc = cmd["desc"]

            draw.text((PADDING_X, y + 11), name, font=self.font_row, fill=ACCENT_RED)

            name_bbox = draw.textbbox((0, 0), name, font=self.font_row)
            name_w = name_bbox[2] - name_bbox[0]
            desc_x = max(PADDING_X + name_w + 20, 340)

            draw.text((desc_x, y + 13), desc, font=self.font_label, fill=TEXT_SECONDARY)

            y += row_h

        return self._save(img)

    async def generate_help_main_card_async(self) -> BytesIO:
        return await asyncio.to_thread(self.generate_help_main_card)

    async def generate_help_card_async(self, category: str) -> BytesIO:
        return await asyncio.to_thread(self.generate_help_card, category)

    # Duel Cards

    def _draw_score_comparison_bar(
        self, draw: ImageDraw.Draw, y: int, w: int,
        p1_val: float, p2_val: float,
        bar_h: int = 6, color1=(200, 80, 80), color2=(80, 120, 200),
    ):
        """Draw a horizontal score comparison bar — wider side = higher value."""
        total = p1_val + p2_val
        if total <= 0:
            ratio = 0.5
        else:
            ratio = p1_val / total
        bar_x = PADDING_X
        bar_w = w - 2 * PADDING_X
        split = int(bar_w * ratio)

        # Left side (player 1)
        if split > 0:
            draw.rounded_rectangle(
                (bar_x, y, bar_x + split - 1, y + bar_h),
                radius=3, fill=color1,
            )
        # Right side (player 2)
        if split < bar_w:
            draw.rounded_rectangle(
                (bar_x + split + 1, y, bar_x + bar_w, y + bar_h),
                radius=3, fill=color2,
            )

    def _draw_win_dots(self, draw: ImageDraw.Draw, cx: int, y: int, wins: int, needed: int, color):
        """Draw filled/empty circles representing round wins (like tennis sets)."""
        dot_r = 6
        gap = 20
        total_w = (needed - 1) * gap
        start_x = cx - total_w // 2
        for i in range(needed):
            dx = start_x + i * gap
            if i < wins:
                draw.ellipse((dx - dot_r, y - dot_r, dx + dot_r, y + dot_r), fill=color)
            else:
                draw.ellipse((dx - dot_r, y - dot_r, dx + dot_r, y + dot_r), outline=color, width=2)

    def generate_duel_round_card(self, data: Dict) -> BytesIO:
        """PNG card for a single duel round result — polished layout."""
        W = CARD_WIDTH
        header_h = 36
        map_section_h = 54
        player_section_h = 120
        bar_section_h = 20
        score_section_h = 70
        footer_h = 34
        H = header_h + map_section_h + player_section_h + bar_section_h + score_section_h + footer_h

        img, draw = self._create_canvas(W, H)

        round_num = data.get("round_number", 1)
        best_of = data.get("best_of", 5)
        self._draw_header(draw, "PROJECT 1984 — DUEL", f"Round {round_num} / Bo{best_of}", W)

        # Map info bar
        y = header_h
        draw.rectangle([(0, y), (W, y + map_section_h)], fill=HEADER_BG)
        beatmap_title = data.get("beatmap_title", "Unknown Map")
        if len(beatmap_title) > 55:
            beatmap_title = beatmap_title[:52] + "..."
        self._text_center(draw, W // 2, y + 8, beatmap_title, self.font_label, TEXT_PRIMARY)
        star_rating = data.get("star_rating", 0.0)
        star_icon = load_icon("star", size=14)
        star_text = f"{star_rating:.2f}"
        if star_icon:
            star_bbox = draw.textbbox((0, 0), star_text, font=self.font_small)
            total_w = star_icon.width + 4 + (star_bbox[2] - star_bbox[0])
            sx = W // 2 - total_w // 2
            img.paste(star_icon, (sx, y + 31), star_icon)
            draw = ImageDraw.Draw(img)
            draw.text((sx + star_icon.width + 4, y + 30), star_text, font=self.font_small, fill=(255, 204, 50))
        else:
            self._text_center(draw, W // 2, y + 30, f"★ {star_text}", self.font_small, (255, 204, 50))

        # Player blocks (side by side)
        y += map_section_h
        half_w = W // 2
        round_winner = data.get("round_winner", 0)

        p1_name = data.get("player1_name", "Player 1")
        p2_name = data.get("player2_name", "Player 2")
        p1_score_val = data.get("player1_score", 0)
        p2_score_val = data.get("player2_score", 0)
        p1_acc = data.get("player1_accuracy", 0.0)
        p2_acc = data.get("player2_accuracy", 0.0)
        p1_combo = data.get("player1_combo", 0)
        p2_combo = data.get("player2_combo", 0)

        # Winner highlight colors
        p1_accent = ACCENT_GREEN if round_winner == 1 else ACCENT_RED if round_winner == 2 else TEXT_SECONDARY
        p2_accent = ACCENT_GREEN if round_winner == 2 else ACCENT_RED if round_winner == 1 else TEXT_SECONDARY

        # Panel backgrounds — winner gets a subtle green tint
        p1_bg = (28, 42, 28) if round_winner == 1 else PANEL_BG
        p2_bg = (28, 42, 28) if round_winner == 2 else PANEL_BG

        # Left panel (P1)
        self._draw_panel(draw, 8, y + 6, half_w - 16, player_section_h - 12, p1_bg)
        # Winner/loser indicator stripe at top of panel
        draw.rectangle([(8, y + 6), (half_w - 8, y + 9)], fill=p1_accent)

        draw.text((24, y + 18), p1_name, font=self.font_subtitle, fill=TEXT_PRIMARY)
        draw.text((24, y + 44), f"{p1_score_val:,}", font=self.font_big, fill=p1_accent)
        draw.text((24, y + 82), f"{p1_acc:.2f}%", font=self.font_label, fill=TEXT_SECONDARY)
        p1_acc_bbox = draw.textbbox((0, 0), f"{p1_acc:.2f}%", font=self.font_label)
        p1_acc_w = p1_acc_bbox[2] - p1_acc_bbox[0]
        draw.text((24 + p1_acc_w + 16, y + 82), f"{p1_combo:,}x", font=self.font_label, fill=TEXT_SECONDARY)

        # Right panel (P2) — mirrored
        self._draw_panel(draw, half_w + 8, y + 6, half_w - 16, player_section_h - 12, p2_bg)
        draw.rectangle([(half_w + 8, y + 6), (W - 8, y + 9)], fill=p2_accent)

        self._text_right(draw, W - 24, y + 18, p2_name, self.font_subtitle, TEXT_PRIMARY)
        self._text_right(draw, W - 24, y + 44, f"{p2_score_val:,}", self.font_big, p2_accent)
        combo_str = f"{p2_combo:,}x"
        combo_bbox = draw.textbbox((0, 0), combo_str, font=self.font_label)
        combo_w = combo_bbox[2] - combo_bbox[0]
        p2_acc_str = f"{p2_acc:.2f}%"
        p2_acc_bbox = draw.textbbox((0, 0), p2_acc_str, font=self.font_label)
        p2_acc_w = p2_acc_bbox[2] - p2_acc_bbox[0]
        self._text_right(draw, W - 24, y + 82, p2_acc_str, self.font_label, TEXT_SECONDARY)
        self._text_right(draw, W - 24 - combo_w - 16 - p2_acc_w, y + 82, combo_str, self.font_label, TEXT_SECONDARY)

        # "VS" diamond in center
        vs_cy = y + player_section_h // 2
        diamond_r = 22
        diamond = [
            (half_w, vs_cy - diamond_r),
            (half_w + diamond_r, vs_cy),
            (half_w, vs_cy + diamond_r),
            (half_w - diamond_r, vs_cy),
        ]
        draw.polygon(diamond, fill=ACCENT_RED)
        self._text_center(draw, half_w, vs_cy - 10, "VS", self.font_label, TEXT_PRIMARY)

        # Score comparison bar
        y += player_section_h
        self._draw_score_comparison_bar(
            draw, y + 7, W,
            float(p1_score_val), float(p2_score_val),
            bar_h=6,
            color1=p1_accent, color2=p2_accent,
        )

        # Duel series score
        y += bar_section_h
        draw.rectangle([(0, y), (W, y + score_section_h)], fill=HEADER_BG)

        p1_wins = data.get("player1_wins", 0)
        p2_wins = data.get("player2_wins", 0)
        wins_needed = best_of // 2 + 1

        # Win dots for P1 (left of center)
        self._draw_win_dots(draw, half_w // 2, y + 20, p1_wins, wins_needed, p1_accent)
        # Win dots for P2 (right of center)
        self._draw_win_dots(draw, half_w + half_w // 2, y + 20, p2_wins, wins_needed, p2_accent)

        # Score text
        score_text = f"{p1_wins}  :  {p2_wins}"
        self._text_center(draw, half_w, y + 14, score_text, self.font_big, TEXT_PRIMARY)
        self._text_center(draw, half_w, y + 48, f"Best of {best_of}", self.font_small, TEXT_SECONDARY)

        # Names under dots
        draw.text((24, y + 44), p1_name, font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, W - 24, y + 44, p2_name, self.font_small, TEXT_SECONDARY)

        return self._save(img)

    def generate_duel_result_card(self, data: Dict) -> BytesIO:
        """PNG card for final duel result — polished layout."""
        W = CARD_WIDTH
        header_h = 36
        winner_section_h = 100
        score_section_h = 50
        rounds_row_h = 52
        rounds = data.get("rounds", [])
        rounds_section_h = len(rounds) * rounds_row_h + 16 if rounds else 0
        footer_h = 34
        H = header_h + winner_section_h + score_section_h + rounds_section_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL RESULT", "", W)

        p1_name = data.get("player1_name", "Player 1")
        p2_name = data.get("player2_name", "Player 2")
        p1_wins = data.get("player1_wins", 0)
        p2_wins = data.get("player2_wins", 0)
        winner_name = data.get("winner_name", "DRAW")
        best_of = data.get("best_of", 5)

        # Winner banner
        y = header_h
        if winner_name == "DRAW":
            draw.rectangle([(0, y), (W, y + winner_section_h)], fill=HEADER_BG)
            self._text_center(draw, W // 2, y + 20, "DRAW", self.font_big, TEXT_SECONDARY)
            self._text_center(draw, W // 2, y + 60, f"{p1_name}  vs  {p2_name}", self.font_label, TEXT_SECONDARY)
        else:
            # Gradient-ish winner bg
            winner_bg = (25, 45, 25)
            draw.rectangle([(0, y), (W, y + winner_section_h)], fill=winner_bg)
            # Green accent line at top
            draw.rectangle([(0, y), (W, y + 3)], fill=ACCENT_GREEN)

            self._text_center(draw, W // 2, y + 10, "WINNER", self.font_small, ACCENT_GREEN)
            self._text_center(draw, W // 2, y + 30, winner_name, self.font_big, TEXT_PRIMARY)

            # Loser name smaller below
            loser_name = p2_name if winner_name == p1_name else p1_name
            self._text_center(draw, W // 2, y + 68, f"defeated {loser_name}", self.font_label, TEXT_SECONDARY)

        # Series score with dots
        y += winner_section_h
        draw.rectangle([(0, y), (W, y + score_section_h)], fill=HEADER_BG)

        half_w = W // 2
        wins_needed = best_of // 2 + 1

        p1_color = ACCENT_GREEN if p1_wins > p2_wins else ACCENT_RED if p2_wins > p1_wins else TEXT_SECONDARY
        p2_color = ACCENT_GREEN if p2_wins > p1_wins else ACCENT_RED if p1_wins > p2_wins else TEXT_SECONDARY

        score_text = f"{p1_wins}  :  {p2_wins}"
        self._text_center(draw, half_w, y + 6, score_text, self.font_big, TEXT_PRIMARY)

        self._draw_win_dots(draw, half_w // 2, y + 38, p1_wins, wins_needed, p1_color)
        self._draw_win_dots(draw, half_w + half_w // 2, y + 38, p2_wins, wins_needed, p2_color)

        draw.text((24, y + 30), p1_name, font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, W - 24, y + 30, p2_name, self.font_small, TEXT_SECONDARY)

        # Round list
        y += score_section_h
        if rounds:
            draw.line([(PADDING_X, y), (W - PADDING_X, y)], fill=ACCENT_RED, width=1)
            y += 8
            for i, rnd in enumerate(rounds):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + rounds_row_h)], fill=row_bg)

                r_num = rnd.get("round_number", i + 1)
                r_map = rnd.get("beatmap_title", "Unknown")
                if len(r_map) > 35:
                    r_map = r_map[:32] + "..."
                r_stars = rnd.get("star_rating", 0.0)
                r_winner = rnd.get("winner_name", "—")
                winner_player = rnd.get("winner_player", 0)
                p1_sc = rnd.get("player1_score", 0)
                p2_sc = rnd.get("player2_score", 0)

                # Round number badge
                badge_x = PADDING_X
                badge_w = 30
                draw.rounded_rectangle(
                    (badge_x, y + 4, badge_x + badge_w, y + badge_w + 4),
                    radius=4, fill=ACCENT_RED,
                )
                self._text_center(draw, badge_x + badge_w // 2, y + 7, str(r_num), self.font_small, TEXT_PRIMARY)

                # Map name + star rating (top line)
                info_x = badge_x + badge_w + 10
                star_icon = load_icon("star", size=16)
                if r_stars > 0 and star_icon:
                    draw.text((info_x, y + 4), f"{r_stars:.1f}", font=self.font_label, fill=TEXT_PRIMARY)
                    val_bbox = draw.textbbox((0, 0), f"{r_stars:.1f}", font=self.font_label)
                    val_w = val_bbox[2] - val_bbox[0]
                    img.paste(star_icon, (info_x + val_w + 4, y + 7), star_icon)
                    draw = ImageDraw.Draw(img)
                    map_x = info_x + val_w + 4 + star_icon.width + 6
                else:
                    star_prefix = f"{r_stars:.1f}★ " if r_stars > 0 else ""
                    draw.text((info_x, y + 4), f"{star_prefix}{r_map}", font=self.font_label, fill=TEXT_PRIMARY)
                    map_x = None
                if map_x is not None:
                    draw.text((map_x, y + 4), r_map, font=self.font_label, fill=TEXT_PRIMARY)

                # Scores (bottom line): "1,234,567 vs 987,654"
                p1_sc_str = f"{p1_sc:,}" if p1_sc > 0 else "—"
                p2_sc_str = f"{p2_sc:,}" if p2_sc > 0 else "—"
                p1_sc_color = ACCENT_GREEN if winner_player == 1 else TEXT_SECONDARY
                p2_sc_color = ACCENT_GREEN if winner_player == 2 else TEXT_SECONDARY
                draw.text((info_x, y + 26), p1_sc_str, font=self.font_small, fill=p1_sc_color)
                vs_bbox = draw.textbbox((0, 0), p1_sc_str, font=self.font_small)
                vs_x = info_x + vs_bbox[2] - vs_bbox[0] + 4
                draw.text((vs_x, y + 26), "vs", font=self.font_stat_label, fill=TEXT_SECONDARY)
                vs2_bbox = draw.textbbox((0, 0), "vs", font=self.font_stat_label)
                p2_x = vs_x + vs2_bbox[2] - vs2_bbox[0] + 4
                draw.text((p2_x, y + 26), p2_sc_str, font=self.font_small, fill=p2_sc_color)

                # Winner indicator on right
                r_color = ACCENT_GREEN if winner_player == 1 else ACCENT_RED if winner_player == 2 else TEXT_SECONDARY
                self._text_right(draw, W - PADDING_X, y + 12, r_winner, self.font_label, r_color)

                y += rounds_row_h
            y += 8

        return self._save(img)

    def generate_duel_history_card(self, data: Dict) -> BytesIO:
        """PNG card for recent completed duel history."""
        entries = data.get("duels", [])
        header_h = 36
        row_h = 58
        H = header_h + max(len(entries), 1) * row_h + 12
        W = CARD_WIDTH

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL HISTORY", "Recent completed duels", W)

        if not entries:
            draw.text((PADDING_X, header_h + 24), "No completed duels yet.", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            y = header_h + 8
            for i, duel in enumerate(entries):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

                opponent = duel.get("opponent_name", "—")
                result = duel.get("result", "—")
                best_of = duel.get("best_of", 0)
                completed_at = duel.get("completed_at")
                score_line = duel.get("score_line", "")

                if isinstance(completed_at, datetime):
                    if completed_at.tzinfo is None:
                        completed_at = completed_at.replace(tzinfo=timezone.utc)
                    when = completed_at.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
                elif completed_at:
                    when = str(completed_at)
                else:
                    when = "—"

                result_color = ACCENT_GREEN if result == "Win" else ACCENT_RED if result == "Loss" else TEXT_SECONDARY
                draw.text((PADDING_X, y + 10), opponent, font=self.font_row, fill=TEXT_PRIMARY)
                draw.text((PADDING_X, y + 32), f"{result} • BO{best_of} • {when}", font=self.font_small, fill=result_color)
                self._text_right(draw, W - PADDING_X, y + 12, score_line, self.font_row, TEXT_PRIMARY)
                y += row_h

        return self._save(img)

    def generate_duel_stats_card(self, data: Dict) -> BytesIO:
        """PNG card for duel summary stats and recent results."""
        summary = data.get("summary", {})
        entries = data.get("duels", [])
        header_h = 36
        panel_h = 64
        gap = 10
        summary_y = header_h + 10
        rows_y = summary_y + panel_h + 16
        row_h = 54
        H = rows_y + max(len(entries), 1) * row_h + 14
        W = CARD_WIDTH

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL STATS", "Wins, formats, and recent duels", W)

        panel_w = (W - 2 * PADDING_X - 2 * gap) // 3
        panels = [
            (f"{summary.get('wins', 0):,}", "WINS"),
            (f"{summary.get('losses', 0):,}", "LOSSES"),
            (f"{summary.get('draws', 0):,}", "DRAWS"),
        ]
        for idx, (value, label) in enumerate(panels):
            px = PADDING_X + idx * (panel_w + gap)
            self._draw_panel(draw, px, summary_y, panel_w, panel_h)
            self._draw_stat_cell(draw, px + panel_w // 2, summary_y + 8, value, label)

        extra_y = summary_y + panel_h + 14
        win_rate = summary.get("win_rate")
        formats = ", ".join(summary.get("formats", [])) or "—"
        self._draw_panel(draw, PADDING_X, extra_y, W - 2 * PADDING_X, 52)
        win_rate_text = f"{win_rate:.1f}%" if win_rate is not None else "—"
        self._draw_kv_row(draw, extra_y + 8, "Win rate", win_rate_text, label_font=self.font_ru_label, value_font=self.font_row)
        self._text_right(draw, W - PADDING_X - 2, extra_y + 8, formats, self.font_small, TEXT_SECONDARY)

        y = rows_y
        if not entries:
            draw.text((PADDING_X, y + 12), "No completed duels yet.", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            for i, duel in enumerate(entries):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

                opponent = duel.get("opponent_name", "—")
                result = duel.get("result", "—")
                best_of = duel.get("best_of", 0)
                completed_at = duel.get("completed_at")
                score_line = duel.get("score_line", "")

                if isinstance(completed_at, datetime):
                    if completed_at.tzinfo is None:
                        completed_at = completed_at.replace(tzinfo=timezone.utc)
                    when = completed_at.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
                elif completed_at:
                    when = str(completed_at)
                else:
                    when = "—"

                result_color = ACCENT_GREEN if result == "Win" else ACCENT_RED if result == "Loss" else TEXT_SECONDARY
                draw.text((PADDING_X, y + 10), opponent, font=self.font_row, fill=TEXT_PRIMARY)
                draw.text((PADDING_X, y + 30), f"{result} • BO{best_of} • {when}", font=self.font_small, fill=result_color)
                self._text_right(draw, W - PADDING_X, y + 10, score_line, self.font_row, TEXT_PRIMARY)
                y += row_h

        return self._save(img)

    def generate_duel_pick_card(self, data: Dict) -> BytesIO:
        W, H = 800, 360
        img, draw = self._create_canvas(W, H)
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, 'PROJECT 1984 — DUEL MAP PICK', self.font_subtitle, ACCENT_RED)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        pick_turn = data.get('pick_turn', '—')
        round_no = data.get('round_number', 1)
        self._text_center(draw, W // 2, 48, f'Раунд {round_no} — выбирает {pick_turn}', self.font_label, TEXT_PRIMARY)

        suggestions = data.get('suggestions', [])
        start_y = 82
        row_h = 44
        for idx, s in enumerate(suggestions[:5]):
            y = start_y + idx * (row_h + 6)
            draw.rounded_rectangle((PADDING_X, y, W - PADDING_X, y + row_h), radius=10, fill=ROW_EVEN if idx % 2 == 0 else ROW_ODD)
            self._text_center(draw, PADDING_X + 20, y + 11, str(idx + 1), self.font_label, ACCENT_RED)
            title = s.get('title', 'Unknown')
            stars = s.get('star_rating', 0.0)
            if len(title) > 38:
                title = title[:35] + '...'
            draw.text((PADDING_X + 44, y + 10), title, font=self.font_label, fill=TEXT_PRIMARY)
            self._text_right(draw, W - PADDING_X - 12, y + 10, f'{stars:.1f}★', self.font_label, TEXT_SECONDARY)
            self._text_center(draw, W - PADDING_X - 54, y + 12, 'PICK', self.font_stat_label, ACCENT_RED)

        return self._save(img)

    async def generate_duel_pick_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_pick_card, data)

    async def generate_duel_pick_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_pick_card, data)

    async def generate_duel_round_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_round_card, data)

    async def generate_duel_result_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_result_card, data)

    async def generate_duel_history_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_history_card, data)

    async def generate_duel_stats_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_stats_card, data)


from services.image.leaderboard import LeaderboardCardGenerator  # noqa: E402

card_renderer = BaseCardRenderer()
leaderboard_gen = LeaderboardCardGenerator()
