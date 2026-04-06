"""
Pillow-based card generators (1984 dystopia theme).

BaseCardRenderer — shared primitives (fonts, header, footer, separators).
LeaderboardCardGenerator — leaderboard-specific card.
+ 5-page profile cards, compare card with avatars, recent/hps/bounty cards.
"""

import asyncio
import os
from io import BytesIO
from typing import List, Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger("services.image_gen")

# ── Theme colours ────────────────────────────────────────────
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

# ── Layout constants ─────────────────────────────────────────
CARD_WIDTH = 800
HEADER_HEIGHT = 36
ROW_HEIGHT = 60
FOOTER_HEIGHT = 30
PADDING_X = 30
VALUE_RIGHT_X = CARD_WIDTH - PADDING_X

# ── Font paths ───────────────────────────────────────────────
ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")

TORUS_BOLD = os.path.join(FONT_DIR, "TorusNotched-Bold.ttf")
TORUS_SEMI = os.path.join(FONT_DIR, "TorusNotched-SemiBold.ttf")
TORUS_REG = os.path.join(FONT_DIR, "TorusNotched-Regular.ttf")

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


# ── Image helpers ────────────────────────────────────────────

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


# ═══════════════════════════════════════════════════════════════
# BaseCardRenderer
# ═══════════════════════════════════════════════════════════════

class BaseCardRenderer:
    """Shared drawing primitives for all card types."""

    def __init__(self):
        bold = _find_font(TORUS_BOLD)
        semi = _find_font(TORUS_SEMI)
        reg = _find_font(TORUS_REG)

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

    # ── Canvas ────────────────────────────────────────────────

    def _create_canvas(self, w: int, h: int):
        img = Image.new("RGB", (w, h), BG_COLOR)
        draw = ImageDraw.Draw(img)
        return img, draw

    # ── Header ────────────────────────────────────────────────

    def _draw_header(self, draw: ImageDraw.Draw, title: str, subtitle: str, w: int):
        """Compact 36px header: title centered, username right-aligned gray."""
        h = 36
        draw.rectangle([(0, 0), (w, h)], fill=HEADER_BG)
        self._text_center(draw, w // 2, 8, title, self.font_subtitle, ACCENT_RED)
        if subtitle:
            self._text_right(draw, w - PADDING_X, 10, subtitle, self.font_small, TEXT_SECONDARY)
        draw.line([(0, h - 2), (w, h - 2)], fill=ACCENT_RED, width=2)

    # ── Footer ────────────────────────────────────────────────

    def _draw_footer(self, draw: ImageDraw.Draw, img: Image.Image, text: str, y: int, w: int):
        draw.line([(0, y), (w, y)], fill=ACCENT_RED, width=1)
        draw.text((PADDING_X, y + 6), text, font=self.font_small, fill=TEXT_SECONDARY)

    # ── Separator ─────────────────────────────────────────────

    def _draw_separator(self, draw: ImageDraw.Draw, y: int, w: int):
        draw.line([(PADDING_X, y), (w - PADDING_X, y)], fill=ACCENT_RED, width=1)

    # ── Key-Value row ─────────────────────────────────────────

    def _draw_kv_row(
        self, draw: ImageDraw.Draw, y: int,
        label: str, value: str,
        label_font=None, value_font=None,
        label_fill=None, value_fill=None,
        x: int = PADDING_X,
    ):
        lf = label_font or self.font_label
        vf = value_font or self.font_row
        lc = label_fill or TEXT_SECONDARY
        vc = value_fill or TEXT_PRIMARY
        draw.text((x, y), f"{label}:", font=lf, fill=lc)
        bbox = draw.textbbox((0, 0), f"{label}:", font=lf)
        lw = bbox[2] - bbox[0]
        draw.text((x + lw + 8, y), value, font=vf, fill=vc)

    # ── Section title ─────────────────────────────────────────

    def _draw_section_title(self, draw: ImageDraw.Draw, y: int, text: str):
        draw.text((PADDING_X, y), text, font=self.font_subtitle, fill=ACCENT_RED)

    # ── Right-aligned text ────────────────────────────────────

    def _text_right(self, draw: ImageDraw.Draw, x_right: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((x_right - tw, y), text, font=font, fill=fill)

    # ── Center-aligned text ───────────────────────────────────

    def _text_center(self, draw: ImageDraw.Draw, cx: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, y), text, font=font, fill=fill)

    # ── Panel (rounded rect bg) ──────────────────────────────

    def _draw_panel(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, bg=PANEL_BG):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=bg)

    # ── Stat cell (value on top, label below) ────────────────

    def _draw_stat_cell(self, draw: ImageDraw.Draw, cx: int, y: int, value: str, label: str):
        self._text_center(draw, cx, y, value, self.font_stat_value, TEXT_PRIMARY)
        self._text_center(draw, cx, y + 30, label, self.font_stat_label, TEXT_SECONDARY)

    # ── Save helper ───────────────────────────────────────────

    @staticmethod
    def _save(img: Image.Image) -> BytesIO:
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    # ──────────────────────────────────────────────────────────
    # Profile Page 0 — Info  (800 × 620)
    # ──────────────────────────────────────────────────────────

    def generate_profile_info_card(self, data: Dict, avatar: Optional[Image.Image] = None, cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = 800, 760
        img, draw = self._create_canvas(W, H)

        # Cover background (top 200px)
        if cover:
            draw_cover_background(img, cover, 0, 200, W)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, 0), (W, 200)], fill=HEADER_BG)
            draw.line([(0, 198), (W, 198)], fill=ACCENT_RED, width=2)

        # Avatar (120×120 rounded rect) centered
        avatar_size = 120
        avatar_x = W // 2 - avatar_size // 2
        avatar_y = 120
        if avatar:
            cropped = rounded_rect_crop(avatar, avatar_size, radius=16)
            img.paste(cropped, (avatar_x, avatar_y), cropped)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=16, outline=ACCENT_RED, width=2
            )
        else:
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=16, fill=(50, 50, 70), outline=ACCENT_RED, width=2
            )

        # Username + flag icon below avatar
        username = data.get("username", "???")
        country = data.get("country", "")
        name_y = avatar_y + avatar_size + 10
        flag_img = load_flag(country, height=22)
        if flag_img:
            # Measure username text width to center username+flag together
            name_bbox = draw.textbbox((0, 0), username, font=self.font_big)
            name_w = name_bbox[2] - name_bbox[0]
            flag_gap = 8
            total_w = flag_img.width + flag_gap + name_w
            start_x = W // 2 - total_w // 2
            img.paste(flag_img, (start_x, name_y + 7), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((start_x + flag_img.width + flag_gap, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            self._text_center(draw, W // 2, name_y, username, self.font_big, TEXT_PRIMARY)

        # Level progress bar under username (shorter, centered)
        y_bar = avatar_y + avatar_size + 48
        level = data.get("level", 0)
        level_progress = data.get("level_progress", 0)

        bar_w = 300
        bar_x = W // 2 - bar_w // 2
        bar_h = 10

        lvl_str = f"Lv{level}"
        next_lvl_str = f"Lv{level + 1}"
        draw.text((bar_x - 50, y_bar - 2), lvl_str, font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, bar_x + bar_w + 50, y_bar - 2, next_lvl_str, self.font_small, TEXT_SECONDARY)
        draw.rounded_rectangle((bar_x, y_bar, bar_x + bar_w, y_bar + bar_h), radius=5, fill=(40, 40, 60))
        if level_progress > 0:
            fill_w = max(10, int(bar_w * level_progress / 100))
            draw.rounded_rectangle((bar_x, y_bar, bar_x + fill_w, y_bar + bar_h), radius=5, fill=ACCENT_RED)
        pct_str = f"{level_progress}%"
        self._text_center(draw, W // 2, y_bar + bar_h + 3, pct_str, self.font_small, TEXT_SECONDARY)

        # Stats panels — two rows of 3
        panel_y = y_bar + bar_h + 24
        panel_w = (W - PADDING_X * 2 - 20) // 3
        panel_h = 60
        gap = 10

        stats_row1 = [
            (f"{data.get('pp', 0):,}", "PP"),
            (f"#{data.get('global_rank', 0):,}", "GLOBAL RANK"),
            (f"{data.get('accuracy', 0):.2f}%", "ACCURACY"),
        ]
        stats_row2 = [
            (f"{data.get('play_count', 0):,}", "PLAY COUNT"),
            (str(data.get("play_time", "—")), "PLAY TIME"),
            (f"{data.get('ranked_score', 0):,}", "RANKED SCORE"),
        ]

        # Compute extra stats
        total_hits = data.get("total_hits", 0)
        play_count = data.get("play_count", 0)
        avg_hits = int(total_hits / play_count) if play_count > 0 else 0
        best_pp = data.get("best_pp", 0)
        hp = data.get("hp_points", 0)

        hp_rank = data.get("hp_rank", "—")
        bounties = data.get("bounties_participated", 0)

        stats_row3 = [
            (str(hp_rank), "HPS RANK"),
            (str(bounties), "BOUNTIES"),
            (f"{hp} HP", "HUNTER POINTS"),
        ]

        # Rows 1-3: three columns each
        for row_idx, stats_row in enumerate([stats_row1, stats_row2, stats_row3]):
            ry = panel_y + row_idx * (panel_h + gap)
            for col_idx, (val, label) in enumerate(stats_row):
                px = PADDING_X + col_idx * (panel_w + gap)
                self._draw_panel(draw, px, ry, panel_w, panel_h)
                cell_cx = px + panel_w // 2
                self._draw_stat_cell(draw, cell_cx, ry + 8, val, label)

        # Row 4: two wider panels (Avg Hits, Best PP)
        row4_y = panel_y + 3 * (panel_h + gap)
        wide_panel_w = (W - PADDING_X * 2 - gap) // 2
        for col_idx, (val, label) in enumerate([
            (f"{avg_hits:,}", "AVG HITS/PLAY"),
            (f"{best_pp:.0f}pp" if best_pp else "—", "BEST PP"),
        ]):
            px = PADDING_X + col_idx * (wide_panel_w + gap)
            self._draw_panel(draw, px, row4_y, wide_panel_w, panel_h)
            cell_cx = px + wide_panel_w // 2
            self._draw_stat_cell(draw, cell_cx, row4_y + 8, val, label)

        # Total Hits full-width row
        y_hits = row4_y + panel_h + gap + 4
        self._draw_panel(draw, PADDING_X, y_hits, W - 2 * PADDING_X, 40)
        hits_str = f"{total_hits:,}"
        self._text_center(draw, W // 2, y_hits + 6, f"TOTAL HITS: {hits_str}", self.font_row, TEXT_PRIMARY)

        # Total Score full-width row
        total_score = data.get("total_score", 0)
        y_ts = y_hits + 50
        self._draw_panel(draw, PADDING_X, y_ts, W - 2 * PADDING_X, 40)
        ts_str = f"{total_score:,}"
        self._text_center(draw, W // 2, y_ts + 6, f"TOTAL SCORE: {ts_str}", self.font_row, TEXT_PRIMARY)

        return self._save(img)

    # ──────────────────────────────────────────────────────────
    # Profile Page 1 — Rank History  (800 × 500)
    # ──────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────
    # Profile Page 2 — Play Count History  (800 × 500)
    # ──────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────
    # Profile Page 3 — Top Scores  (800 × 520)
    # ──────────────────────────────────────────────────────────

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
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, self.font_grade, grade_color)

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

            # PP right-aligned, vertically centered in row
            pp = sc.get("pp", 0)
            pp_str = f"{pp:.0f}pp"
            self._text_right(draw, W - PADDING_X, ry + (row_h - 22) // 2, pp_str, self.font_row, ACCENT_RED)

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

        return self._save(img)

    # ──────────────────────────────────────────────────────────
    # Profile Page 4 — Recent Plays  (800 × 520)
    # ──────────────────────────────────────────────────────────

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
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, self.font_grade, grade_color)

            # Map title
            beatmapset = sc.get("beatmapset") or {}
            beatmap = sc.get("beatmap") or {}
            artist = beatmapset.get("artist", "")
            title = beatmapset.get("title", "")
            map_str = f"{artist} - {title}"
            if len(map_str) > 40:
                map_str = map_str[:37] + "..."
            draw.text((info_x, ry + 8), map_str, font=self.font_label, fill=TEXT_PRIMARY)

            # PP right-aligned
            pp = sc.get("pp") or 0
            pp_str = f"{pp:.0f}pp" if pp else "—"
            self._text_right(draw, W - PADDING_X, ry + (row_h - 22) // 2, pp_str, self.font_row, ACCENT_RED)

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

        return self._save(img)

    # ──────────────────────────────────────────────────────────
    # Profile Dispatcher — async, downloads images
    # ──────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────
    # Compare Card  (800 × 620) — with avatars and covers
    # ──────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────
    # Recent Score Card  (800 × 320)
    # ──────────────────────────────────────────────────────────

    def generate_recent_card(
        self, data: Dict,
        cover: Optional[Image.Image] = None,
        mapper_avatar: Optional[Image.Image] = None,
        player_avatar: Optional[Image.Image] = None,
        player_cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 480
        img, draw = self._create_canvas(W, H)
        icon_sz = 16

        # ── Compact header bar ──
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — SCORE REPORT", self.font_subtitle, ACCENT_RED)
        username = data.get("username", "???")
        self._text_right(draw, W - PADDING_X, 10, username, self.font_small, TEXT_SECONDARY)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        # ═══════════════════════════════════════════════════════════
        # UPPER ZONE  ~160px
        # ═══════════════════════════════════════════════════════════
        upper_top = header_h
        upper_h = 160
        left_w = 380

        # Right side: beatmap cover with left-edge fade
        if cover:
            right_w = W - left_w
            cropped = cover_center_crop(cover, right_w, upper_h)
            overlay = Image.new("RGBA", (right_w, upper_h), (0, 0, 0, 100))
            cropped = Image.alpha_composite(cropped, overlay)
            fade = Image.new("L", (right_w, upper_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = int(fx / fade_zone * 255)
                ImageDraw.Draw(fade).line([(fx, 0), (fx, upper_h)], fill=alpha)
            img.paste(cropped.convert("RGB"), (left_w, upper_top), fade)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(left_w, upper_top), (W, upper_top + upper_h)], fill=(40, 35, 55))

        # Left side: dark zone
        draw.rectangle([(0, upper_top), (left_w, upper_top + upper_h)], fill=BG_COLOR)

        # ── Map title: "title — artist" ──
        artist = data.get("artist", "Unknown")
        title = data.get("title", "Unknown")
        map_title = f"{title} — {artist}"
        max_tw = left_w - PADDING_X - 10
        mt_bbox = draw.textbbox((0, 0), map_title, font=self.font_row)
        while mt_bbox[2] - mt_bbox[0] > max_tw and len(map_title) > 4:
            map_title = map_title[:-1]
            mt_bbox = draw.textbbox((0, 0), map_title + "...", font=self.font_row)
        if len(map_title) < len(f"{title} — {artist}"):
            map_title += "..."
        draw.text((PADDING_X, upper_top + 10), map_title, font=self.font_row, fill=TEXT_PRIMARY)

        # ── Mapper avatar (40×40) + mapper name ──
        mapper_name = data.get("mapper_name", "Unknown")
        mapper_av_x = PADDING_X
        mapper_av_y = upper_top + 38
        mapper_av_size = 40
        if mapper_avatar:
            mav = rounded_rect_crop(mapper_avatar, mapper_av_size, radius=8)
            img.paste(mav, (mapper_av_x, mapper_av_y), mav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (mapper_av_x, mapper_av_y, mapper_av_x + mapper_av_size, mapper_av_y + mapper_av_size),
                radius=8, outline=TEXT_SECONDARY, width=2,
            )
        else:
            draw.rounded_rectangle(
                (mapper_av_x, mapper_av_y, mapper_av_x + mapper_av_size, mapper_av_y + mapper_av_size),
                radius=8, fill=(50, 50, 70), outline=TEXT_SECONDARY, width=2,
            )
        # "mapped by" + mapper name to the right of avatar
        mapper_text_x = mapper_av_x + mapper_av_size + 8
        draw.text((mapper_text_x, mapper_av_y + 2), "mapped by", font=self.font_stat_label, fill=TEXT_SECONDARY)
        draw.text((mapper_text_x, mapper_av_y + 18), mapper_name, font=self.font_small, fill=TEXT_SECONDARY)

        # ── BPM icon + value  |  Timer icon + length — row below mapper ──
        bpm = data.get("bpm", 0)
        total_length = data.get("total_length", 0)
        row3_y = upper_top + 86
        cur_x = PADDING_X
        bpm_icon = load_icon("pngwing.com", size=icon_sz)
        if bpm_icon:
            img.paste(bpm_icon, (cur_x, row3_y + 2), bpm_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        draw.text((cur_x, row3_y), str(bpm), font=self.font_label, fill=TEXT_PRIMARY)
        cur_x += draw.textbbox((0, 0), str(bpm), font=self.font_label)[2] + 14

        minutes = total_length // 60
        seconds = total_length % 60
        length_str = f"{minutes}:{seconds:02d}"
        timer_icon = load_icon("free-icon-timer-6834351", size=icon_sz)
        if timer_icon:
            img.paste(timer_icon, (cur_x, row3_y + 2), timer_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        draw.text((cur_x, row3_y), length_str, font=self.font_label, fill=TEXT_PRIMARY)

        # ── Star rating + [version] + mods — aligned with star icon baseline ──
        version = data.get("version", "Unknown")
        mods = data.get("mods", "")
        stars = data.get("star_rating", 0.0)
        ver_y = upper_top + 110
        cur_x = PADDING_X
        star_icon = load_icon("star", size=icon_sz)
        if star_icon:
            img.paste(star_icon, (cur_x, ver_y), star_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        draw.text((cur_x, ver_y), f"{stars:.2f}", font=self.font_label, fill=TEXT_PRIMARY)
        sr_bbox = draw.textbbox((0, 0), f"{stars:.2f}", font=self.font_label)
        cur_x += sr_bbox[2] - sr_bbox[0] + 10
        version_str = f"[{version}]"
        draw.text((cur_x, ver_y), version_str, font=self.font_small, fill=TEXT_SECONDARY)

        # ── Mods row — colored, bold, below SR line ──
        if mods:
            mod_y = ver_y + 18
            mod_x = PADDING_X
            for mod_char_i in range(0, len(mods), 2):
                mod_name = mods[mod_char_i:mod_char_i + 2]
                if not mod_name:
                    break
                mod_color = MOD_COLORS.get(mod_name, TEXT_PRIMARY)
                draw.text((mod_x, mod_y), mod_name, font=self.font_label, fill=mod_color)
                mb = draw.textbbox((0, 0), mod_name, font=self.font_label)
                mod_x += mb[2] - mb[0] + 6

        # ── PP and PP if FC — horizontal, under map title ──
        pp = data.get("pp", 0.0)
        pp_if_fc = data.get("pp_if_fc", 0)
        pp_panel_w = 90
        pp_panel_h = 32
        pp_panel_gap = 8
        pp_panel_x1 = mapper_text_x + 90
        pp_panel_x2 = pp_panel_x1 + pp_panel_w + pp_panel_gap
        pp_panel_y = upper_top + 40

        self._draw_panel(draw, pp_panel_x1, pp_panel_y, pp_panel_w, pp_panel_h)
        pp_str = f"{pp:.2f}pp" if pp > 0 else "—"
        self._text_center(draw, pp_panel_x1 + pp_panel_w // 2, pp_panel_y + 2, pp_str, self.font_stat_label, TEXT_PRIMARY)
        self._text_center(draw, pp_panel_x1 + pp_panel_w // 2, pp_panel_y + 16, "PP", self.font_stat_label, TEXT_SECONDARY)

        self._draw_panel(draw, pp_panel_x2, pp_panel_y, pp_panel_w, pp_panel_h)
        ppfc_str = f"{pp_if_fc:.0f}pp" if pp_if_fc > 0 else "—"
        self._text_center(draw, pp_panel_x2 + pp_panel_w // 2, pp_panel_y + 2, ppfc_str, self.font_stat_label, TEXT_PRIMARY)
        self._text_center(draw, pp_panel_x2 + pp_panel_w // 2, pp_panel_y + 16, "IF FC", self.font_stat_label, TEXT_SECONDARY)

        # ── Red accent divider ──
        accent_y = upper_top + upper_h
        draw.line([(0, accent_y), (W, accent_y + 1)], fill=ACCENT_RED, width=2)

        # ═══════════════════════════════════════════════════════════
        # LOWER ZONE
        # ═══════════════════════════════════════════════════════════
        lower_top = accent_y + 4
        lower_h = H - lower_top
        player_zone_x = 570
        player_zone_w = W - player_zone_x

        # ── Right side: PLAYER COVER BG (not beatmap cover!) ──
        player_bg = player_cover or cover  # fallback to beatmap cover if no player cover
        if player_bg:
            pcrop = cover_center_crop(player_bg, player_zone_w, lower_h)
            p_overlay = Image.new("RGBA", (player_zone_w, lower_h), (0, 0, 0, 180))
            pcrop = Image.alpha_composite(pcrop, p_overlay)
            pfade = Image.new("L", (player_zone_w, lower_h), 255)
            pfade_zone = 40
            for fx in range(pfade_zone):
                alpha = int(fx / pfade_zone * 255)
                ImageDraw.Draw(pfade).line([(fx, 0), (fx, lower_h)], fill=alpha)
            img.paste(pcrop.convert("RGB"), (player_zone_x, lower_top), pfade)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(player_zone_x, lower_top), (W, H)], fill=(25, 25, 40))

        # Player avatar (90×90) centered
        pav_size = 90
        pav_x = player_zone_x + (player_zone_w - pav_size) // 2
        pav_y = lower_top + (lower_h - pav_size - 50) // 2
        if player_avatar:
            pav = rounded_rect_crop(player_avatar, pav_size, radius=16)
            img.paste(pav, (pav_x, pav_y), pav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (pav_x, pav_y, pav_x + pav_size, pav_y + pav_size),
                radius=16, outline=ACCENT_RED, width=2,
            )
        else:
            draw.rounded_rectangle(
                (pav_x, pav_y, pav_x + pav_size, pav_y + pav_size),
                radius=16, fill=(50, 50, 70), outline=ACCENT_RED, width=2,
            )

        # "Played by" + username
        pcx = player_zone_x + player_zone_w // 2
        self._text_center(draw, pcx, pav_y + pav_size + 10, "Played by", self.font_stat_label, TEXT_SECONDARY)
        uname_display = username
        uname_bbox = draw.textbbox((0, 0), uname_display, font=self.font_label)
        while uname_bbox[2] - uname_bbox[0] > player_zone_w - 16 and len(uname_display) > 3:
            uname_display = uname_display[:-1]
            uname_bbox = draw.textbbox((0, 0), uname_display + "..", font=self.font_label)
        if len(uname_display) < len(username):
            uname_display += ".."
        self._text_center(draw, pcx, pav_y + pav_size + 26, uname_display, self.font_label, TEXT_PRIMARY)

        # ── Left side: Stats ──
        acc = data.get("accuracy", 0.0)
        combo = data.get("combo", 0)
        misses = data.get("misses", 0)
        total_score = data.get("total_score", 0)
        count_300 = data.get("count_300", 0)
        count_100 = data.get("count_100", 0)
        count_50 = data.get("count_50", 0)
        rank_grade = data.get("rank_grade", "F")
        grade_color = GRADE_COLORS.get(rank_grade, TEXT_PRIMARY)

        # ── Total Score bar (horizontal: label left, value centered) ──
        ts_x = PADDING_X
        ts_y = lower_top + 6
        ts_w = player_zone_x - PADDING_X - 6  # right edge = player_zone_x - 6
        ts_h = 32
        self._draw_panel(draw, ts_x, ts_y, ts_w, ts_h)
        draw.text((ts_x + 10, ts_y + 8), "TOTAL SCORE", font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_center(draw, ts_x + ts_w // 2 + 30, ts_y + 6, f"{total_score:,}", self.font_label, TEXT_PRIMARY)

        # ── CS/AR/OD/HP — spread across full width, centered "LABEL VALUE" ──
        diff_labels = ["CS", "AR", "OD", "HP"]
        diff_values = [
            f"{data.get('cs', 0):.1f}",
            f"{data.get('ar', 0):.1f}",
            f"{data.get('od', 0):.1f}",
            f"{data.get('hp', 0):.1f}",
        ]
        dp_gap = 6
        dp_count = len(diff_labels)
        dp_total_w = ts_w
        dp_w = (dp_total_w - (dp_count - 1) * dp_gap) // dp_count
        dp_h = 28
        dp_start_x = ts_x
        dp_y = ts_y + ts_h + 4
        for i, (dl, dv) in enumerate(zip(diff_labels, diff_values)):
            dpx = dp_start_x + i * (dp_w + dp_gap)
            self._draw_panel(draw, dpx, dp_y, dp_w, dp_h)
            combined = f"{dl}: {dv}"
            self._text_center(draw, dpx + dp_w // 2, dp_y + 6, combined, self.font_label, TEXT_PRIMARY)

        # ── Layout: left column | grade center | right column ──
        left_col_x = PADDING_X
        col_w = 100
        col_gap = 4
        right_col_x = player_zone_x - col_w - 6  # align right edge flush with player zone
        grade_cx = (left_col_x + col_w + right_col_x) // 2

        # Distribute 3 panels vertically — tight
        col_top = dp_y + dp_h + 6
        col_bottom = H - 6
        available_h = col_bottom - col_top
        col_h = (available_h - 2 * col_gap) // 3

        # Value/label vertically centered inside panels
        # font_label ~14px, font_stat_label ~10px, spacing 2px between them → block ~26px
        # center block: val_off = (col_h - 26) // 2, lbl_off = val_off + 16

        def _vert_center(ch):
            """Return (val_y_off, lbl_y_off) to center value+label block in panel of height ch."""
            block_h = 26
            top_pad = max((ch - block_h) // 2, 1)
            return top_pad, top_pad + 16

        # ── Left column: Combo, Accuracy, Misses ──
        v_off, l_off = _vert_center(col_h)

        self._draw_panel(draw, left_col_x, col_top, col_w, col_h)
        self._text_center(draw, left_col_x + col_w // 2, col_top + v_off, f"{combo}x", self.font_label, TEXT_PRIMARY)
        self._text_center(draw, left_col_x + col_w // 2, col_top + l_off, "COMBO", self.font_stat_label, TEXT_SECONDARY)

        acc_y = col_top + col_h + col_gap
        self._draw_panel(draw, left_col_x, acc_y, col_w, col_h)
        self._text_center(draw, left_col_x + col_w // 2, acc_y + v_off, f"{acc:.2f}%", self.font_label, TEXT_PRIMARY)
        self._text_center(draw, left_col_x + col_w // 2, acc_y + l_off, "ACCURACY", self.font_stat_label, TEXT_SECONDARY)

        miss_y = acc_y + col_h + col_gap
        self._draw_panel(draw, left_col_x, miss_y, col_w, col_h)
        miss_val = "FC" if misses == 0 else str(misses)
        self._text_center(draw, left_col_x + col_w // 2, miss_y + v_off, miss_val, self.font_label, TEXT_PRIMARY)
        self._text_center(draw, left_col_x + col_w // 2, miss_y + l_off, "MISSES", self.font_stat_label, TEXT_SECONDARY)

        # ── Right column: GREAT, GOOD, MEH ──
        hit_colors = {
            "GREAT": (80, 200, 80),
            "GOOD":  (200, 180, 50),
            "MEH":   (200, 100, 50),
        }
        hit_data = [
            ("GREAT", str(count_300)),
            ("GOOD", str(count_100)),
            ("MEH", str(count_50)),
        ]
        for i, (hit_label, hit_val) in enumerate(hit_data):
            hy = col_top + i * (col_h + col_gap)
            self._draw_panel(draw, right_col_x, hy, col_w, col_h)
            self._text_center(draw, right_col_x + col_w // 2, hy + v_off, hit_val, self.font_label, hit_colors[hit_label])
            self._text_center(draw, right_col_x + col_w // 2, hy + l_off, hit_label, self.font_stat_label, hit_colors[hit_label])

        # ── Grade: very large, centered between columns ──
        bold_path = _find_font(TORUS_BOLD)
        font_grade_xl = ImageFont.truetype(bold_path, 86) if bold_path else self.font_vs

        circle_r = 60
        grade_vert_center = col_top + (3 * col_h + 2 * col_gap) // 2
        circle_img = Image.new("RGBA", (circle_r * 2, circle_r * 2), (0, 0, 0, 0))
        circle_draw = ImageDraw.Draw(circle_img)
        circle_draw.ellipse((0, 0, circle_r * 2 - 1, circle_r * 2 - 1), fill=(20, 20, 30, 180))
        img.paste(circle_img, (grade_cx - circle_r, grade_vert_center - circle_r), circle_img)
        draw = ImageDraw.Draw(img)

        grade_bbox = draw.textbbox((0, 0), rank_grade, font=font_grade_xl)
        grade_th = grade_bbox[3] - grade_bbox[1]
        self._text_center(draw, grade_cx, grade_vert_center - grade_th // 2 - 4, rank_grade, font_grade_xl, grade_color)

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

    # ──────────────────────────────────────────────────────────
    # HPS Card  (800 × 520)
    # ──────────────────────────────────────────────────────────

    def generate_hps_card(self, data: Dict, cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = 800, 520
        img, draw = self._create_canvas(W, H)

        # ── Compact header bar (like compare) ──
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — HPS ANALYSIS", self.font_subtitle, ACCENT_RED)

        # ── Cover zone: dark left panel + cover BG right ──
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

        # ── Body: left = map params, right = map info ──
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
        star_icon = load_icon("star", size=16)
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

        # ── HP Scenarios — 4 panels in a row ──
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

        # ── Agent data — 3 panels ──
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

    # ──────────────────────────────────────────────────────────
    # Bounty Card  (800 × dynamic)
    # ──────────────────────────────────────────────────────────

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

        footer_y = H - 40
        self._draw_footer(draw, img, "COMPLETE THE MISSION FOR THE PARTY", footer_y, W)

        return self._save(img)

    async def generate_bounty_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bounty_card, data)


# ═══════════════════════════════════════════════════════════════
# LeaderboardCardGenerator (inherits BaseCardRenderer)
# ═══════════════════════════════════════════════════════════════

class LeaderboardCardGenerator(BaseCardRenderer):
    """Leaderboard-specific PNG card."""

    # ── Podium column specs (order: #4, #2, #1, #3, #5) ──
    # x is computed dynamically; these are template specs
    PODIUM_COLS = [
        {"rank": 4, "w": 140, "h": 260, "cover_h": 70, "avatar_sz": 50},
        {"rank": 2, "w": 150, "h": 300, "cover_h": 85, "avatar_sz": 58},
        {"rank": 1, "w": 180, "h": 340, "cover_h": 100, "avatar_sz": 70},
        {"rank": 3, "w": 150, "h": 300, "cover_h": 85, "avatar_sz": 58},
        {"rank": 5, "w": 140, "h": 260, "cover_h": 70, "avatar_sz": 50},
    ]
    PODIUM_Y_BOTTOM = 428
    PODIUM_COL_GAP = 8

    def generate_leaderboard_card(
        self, category_label: str, entries: List[Dict],
        avatars: Optional[List[Optional[Image.Image]]] = None,
        covers: Optional[List[Optional[Image.Image]]] = None,
    ) -> BytesIO:
        if avatars is not None:
            return self._draw_podium(category_label, entries, avatars, covers)
        return self._draw_compact(category_label, entries)

    def _draw_compact(self, category_label: str, entries: List[Dict]) -> BytesIO:
        """Original compact row-based leaderboard."""
        num_rows = max(len(entries), 1)
        header_h = 36
        row_h = 60
        card_height = header_h + num_rows * row_h + 8

        img = Image.new("RGB", (CARD_WIDTH, card_height), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── Compact header ──
        draw.rectangle([(0, 0), (CARD_WIDTH, header_h)], fill=HEADER_BG)
        self._text_center(
            draw, CARD_WIDTH // 2, 8,
            f"PROJECT 1984 — {category_label.upper()}",
            self.font_subtitle, ACCENT_RED,
        )
        draw.line([(0, header_h - 2), (CARD_WIDTH, header_h - 2)], fill=ACCENT_RED, width=2)

        if not entries:
            draw.text((PADDING_X, header_h + 15), "No data available", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            for i, entry in enumerate(entries):
                y_top = header_h + i * row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y_top), (CARD_WIDTH, y_top + row_h)], fill=row_bg)

                position = entry.get("position", i + 1)
                country = entry.get("country", "XX")
                username = entry.get("username", "???")
                value = entry.get("value", "—")

                text_color = TOP_COLORS.get(position, TEXT_PRIMARY)
                y_text = y_top + (row_h - 24) // 2

                if position <= 3:
                    bar_color = TOP_COLORS.get(position, TEXT_PRIMARY)
                    draw.rectangle([(0, y_top), (4, y_top + row_h)], fill=bar_color)

                draw.text((16, y_text), f"#{position}", font=self.font_row, fill=text_color)

                flag = load_flag(country, height=20)
                if flag:
                    # Place flag at bottom half of row, aligned with text baseline
                    flag_y = y_top + row_h - flag.height - 10
                    img.paste(flag, (58, flag_y), flag)
                    draw = ImageDraw.Draw(img)
                else:
                    draw.text((58, y_text), f"[{country}]", font=self.font_small, fill=TEXT_SECONDARY)

                draw.text((96, y_text), username, font=self.font_row, fill=text_color)

                val_str = str(value)
                bbox = draw.textbbox((0, 0), val_str, font=self.font_row)
                val_width = bbox[2] - bbox[0]
                draw.text((VALUE_RIGHT_X - val_width, y_text), val_str, font=self.font_row, fill=text_color)

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def _draw_podium(
        self, category_label: str, entries: List[Dict],
        avatars: List[Optional[Image.Image]],
        covers: Optional[List[Optional[Image.Image]]],
    ) -> BytesIO:
        """Podium-style card for top-5 (page 0)."""
        W, H = 800, 440
        img = Image.new("RGB", (W, H), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── Header (0..36) ──
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(
            draw, W // 2, 8,
            f"PROJECT 1984 — {category_label.upper()}",
            self.font_subtitle, ACCENT_RED,
        )
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        # Build rank→entry index mapping
        rank_to_idx = {}
        for idx, e in enumerate(entries):
            rank_to_idx[e.get("position", idx + 1)] = idx

        # Filter columns to only those with data, compute dynamic X positions
        active_cols = [col for col in self.PODIUM_COLS if col["rank"] in rank_to_idx]
        if not active_cols:
            return self._save(img)

        gap = self.PODIUM_COL_GAP
        total_w = sum(c["w"] for c in active_cols) + gap * (len(active_cols) - 1)
        start_x = (W - total_w) // 2
        cur_x = start_x

        for col in active_cols:
            rank = col["rank"]
            idx = rank_to_idx[rank]
            entry = entries[idx]

            cx = cur_x
            cw = col["w"]
            ch = col["h"]
            y_top = self.PODIUM_Y_BOTTOM - ch
            cover_h = col["cover_h"]
            avatar_sz = col["avatar_sz"]
            cur_x += cw + gap

            # Panel background with rounded corners
            draw.rounded_rectangle((cx, y_top, cx + cw, y_top + ch), radius=14, fill=PANEL_BG)

            # Cover background (clipped inside rounded top, with bottom fade)
            cover_img = covers[idx] if covers and idx < len(covers) else None
            if cover_img:
                cropped = cover_center_crop(cover_img, cw - 2, cover_h)
                overlay = Image.new("RGBA", cropped.size, (0, 0, 0, 80))
                cropped = Image.alpha_composite(cropped, overlay)
                # Bottom fade: cover fades into PANEL_BG
                fade_zone = min(24, cover_h // 3)
                fade_mask = Image.new("L", (cw - 2, cover_h), 255)
                for fy in range(fade_zone):
                    alpha = 255 - int(fy / fade_zone * 255)
                    ImageDraw.Draw(fade_mask).line(
                        [(0, cover_h - fade_zone + fy), (cw - 2, cover_h - fade_zone + fy)],
                        fill=alpha,
                    )
                # Rounded top corners mask
                top_mask = Image.new("L", (cw - 2, cover_h), 0)
                cm_draw = ImageDraw.Draw(top_mask)
                cm_draw.rounded_rectangle((0, 0, cw - 3, cover_h + 14), radius=14, fill=255)
                # Combine: both masks (min = intersection)
                from PIL import ImageChops
                final_mask = ImageChops.darker(top_mask, fade_mask)
                img.paste(cropped.convert("RGB"), (cx + 1, y_top + 1), final_mask)
                draw = ImageDraw.Draw(img)

            # Avatar (square with rounded corners) — overlaps cover bottom
            avatar_img = avatars[idx] if idx < len(avatars) else None
            avatar_y = y_top + cover_h - avatar_sz // 3
            ax = cx + (cw - avatar_sz) // 2
            av_radius = 12
            if avatar_img:
                av = rounded_rect_crop(avatar_img, avatar_sz, radius=av_radius)
                img.paste(av, (ax, avatar_y), av)
                draw = ImageDraw.Draw(img)

            # Avatar outline (rounded rectangle)
            outline_color = TOP_COLORS.get(rank, TEXT_SECONDARY)
            draw.rounded_rectangle(
                (ax - 1, avatar_y - 1, ax + avatar_sz, avatar_y + avatar_sz),
                radius=av_radius, outline=outline_color, width=3,
            )

            # Current Y cursor after avatar
            cur_y = avatar_y + avatar_sz + 4

            # Position "#N"
            pos_color = TOP_COLORS.get(rank, TEXT_PRIMARY)
            col_cx = cx + cw // 2
            self._text_center(draw, col_cx, cur_y, f"#{rank}", self.font_row, pos_color)
            cur_y += 18 if rank == 1 else 22

            # Flag + username (colored by top rank, auto-scaled to fit)
            country = entry.get("country", "XX")
            username = entry.get("username", "???")
            flag_h = 16
            flag = load_flag(country, height=flag_h)
            name_color = TOP_COLORS.get(rank, TEXT_PRIMARY)

            name_font = self.font_row if rank == 1 else self.font_label
            max_name_w = cw - 10
            display_name = username
            if flag:
                max_name_w -= flag.width + 4
            bbox = draw.textbbox((0, 0), display_name, font=name_font)
            while bbox[2] - bbox[0] > max_name_w and len(display_name) > 3:
                display_name = display_name[:-1]
                bbox = draw.textbbox((0, 0), display_name + "..", font=name_font)
            if display_name != username:
                display_name += ".."

            name_bbox = draw.textbbox((0, 0), display_name, font=name_font)
            name_w = name_bbox[2] - name_bbox[0]
            name_h = name_bbox[3] - name_bbox[1]
            # Move flag+name down a bit more for rank 1
            if rank == 1:
                cur_y += 4
            if flag:
                total_fw = flag.width + 4 + name_w
                fx = col_cx - total_fw // 2
                # Align flag to bottom of text line
                flag_y = cur_y + name_h - flag.height + 2
                img.paste(flag, (fx, flag_y), flag)
                draw = ImageDraw.Draw(img)
                draw.text((fx + flag.width + 4, cur_y), display_name, font=name_font, fill=name_color)
            else:
                self._text_center(draw, col_cx, cur_y, display_name, name_font, name_color)

            # ── Category value — bottom of column, auto-scaled ──
            value_str = str(entry.get("value", "—"))
            # Strip map name from best_pp values on podium
            if " — " in value_str:
                value_str = value_str.split(" — ")[0]

            val_color = TOP_COLORS.get(rank, TEXT_PRIMARY)
            if rank == 1:
                val_font = self.font_stat_value
                val_y = y_top + ch - 38
            else:
                val_font = self.font_label
                val_y = y_top + ch - 32

            # Auto-scale: if value text too wide, use smaller font
            vbbox = draw.textbbox((0, 0), value_str, font=val_font)
            if vbbox[2] - vbbox[0] > cw - 8:
                val_font = self.font_label if rank == 1 else self.font_small
            self._text_center(draw, col_cx, val_y, value_str, val_font, val_color)

            # Accent stripe at bottom — full width of column, for top-3
            if rank in TOP_COLORS:
                stripe_y = y_top + ch - 2
                draw.rounded_rectangle(
                    (cx, stripe_y - 2, cx + cw, stripe_y + 1),
                    radius=1, fill=TOP_COLORS[rank],
                )

        return self._save(img)

    @staticmethod
    def _image_from_bytes(data: Optional[bytes]) -> Optional[Image.Image]:
        """Open an Image from raw bytes, or return None."""
        if not data:
            return None
        try:
            return Image.open(BytesIO(data)).convert("RGBA")
        except Exception:
            return None

    async def generate_leaderboard_card_async(
        self, category_label: str, entries: List[Dict]
    ) -> BytesIO:
        is_first_page = entries and entries[0].get("position", 1) == 1
        if is_first_page:
            # Try cached bytes first, fall back to URL download
            avatar_tasks = []
            cover_tasks = []
            for e in entries:
                # Avatar: prefer cached bytes → download from URL
                if e.get("avatar_data"):
                    avatar_tasks.append(_none_coro())  # placeholder; handled below
                else:
                    uid = e.get("osu_user_id")
                    avatar_tasks.append(download_image(f"https://a.ppy.sh/{uid}") if uid else _none_coro())

                # Cover: prefer cached bytes → download from URL
                if e.get("cover_data"):
                    cover_tasks.append(_none_coro())
                else:
                    cover_tasks.append(download_image(e.get("cover_url")) if e.get("cover_url") else _none_coro())

            n = len(avatar_tasks)
            results = await asyncio.gather(*avatar_tasks, *cover_tasks, return_exceptions=True)

            avatars = []
            covers = []
            for i, e in enumerate(entries):
                # Avatar
                if e.get("avatar_data"):
                    avatars.append(self._image_from_bytes(e["avatar_data"]))
                else:
                    r = results[i]
                    avatars.append(r if not isinstance(r, Exception) else None)
                # Cover
                if e.get("cover_data"):
                    covers.append(self._image_from_bytes(e["cover_data"]))
                else:
                    r = results[n + i]
                    covers.append(r if not isinstance(r, Exception) else None)

            return await asyncio.to_thread(
                self.generate_leaderboard_card, category_label, entries, avatars, covers
            )
        return await asyncio.to_thread(
            self.generate_leaderboard_card, category_label, entries
        )


# ── Module-level instances ────────────────────────────────────
card_renderer = BaseCardRenderer()
leaderboard_gen = LeaderboardCardGenerator()
