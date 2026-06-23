import asyncio
import math
from io import BytesIO
from typing import Dict, Optional, List

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont

from services.image.constants import (
    PADDING_X,
    TORUS_BOLD,
    TORUS_SEMI,
    TORUS_REG,
    MPLUS_BOLD,
    MPLUS_REG,
    GRADE_COLORS,
)
from services.image.utils import (
    load_flag,
    load_icon,
    _find_font,
    _none_coro,
    download_image,
    cover_center_crop,
)


# ── Dashboard geometry — one wide landscape card mirroring the osu! mockup ──
DASH_W = 1280
DASH_H = 900
CARD_M = 16                       # black margin around the main card
CARD_PAD = 28
INNER_L = CARD_M + CARD_PAD       # 44
INNER_R = DASH_W - CARD_M - CARD_PAD   # 1236

# Band coordinates (top-down) chosen to match the mockup's proportions.
HERO_BOTTOM = 300
STATS_Y0, STATS_Y1 = 316, 430
MID_Y0 = 446
PANEL_Y1 = 846                    # both big panels share this bottom edge
SPLIT_X = 680                     # divider between left/right big panels
LEFT_X0, LEFT_X1 = INNER_L, SPLIT_X - 8      # 44 .. 672
RIGHT_X0, RIGHT_X1 = SPLIT_X + 8, INNER_R    # 688 .. 1236

# ── Palette (red 1984 theme) ──
COL_BG = (14, 12, 16)
COL_CARD = (23, 19, 24)
COL_CARD_BORDER = (74, 52, 56)
COL_PANEL = (30, 24, 30)
COL_PANEL_BORDER = (64, 46, 50)
COL_RED = (226, 72, 72)           # section titles / accents
COL_CORAL = (240, 104, 104)       # pp value, country rank
COL_WHITE = (236, 234, 238)
COL_MUTED = (156, 144, 150)
COL_GREEN = (122, 222, 142)
COL_TRACK = (62, 48, 52)
COL_DIVIDER = (68, 50, 54)
COL_HEART = (255, 110, 178)       # osu!supporter pink heart

# Six grade circles, in mockup order, using osu!'s canonical grade colours
# (gold SS/S, green A, blue B, orange C, red D). osu! API only reports
# ss/ssh/s/sh/a; b/c/d come from `grade_counts` when present (else 0), so the
# layout matches the mockup while staying honest for real data.
GRADES = [
    ("SS", ("ss", "ssh"), GRADE_COLORS["X"]),
    ("S", ("s", "sh"), GRADE_COLORS["S"]),
    ("A", ("a",), GRADE_COLORS["A"]),
    ("B", ("b",), GRADE_COLORS["B"]),
    ("C", ("c",), GRADE_COLORS["C"]),
    ("D", ("d",), GRADE_COLORS["D"]),
]

# Grade-distribution bar stops (SS→D), the osu! grade spectrum — no purple.
RAINBOW = [
    GRADE_COLORS["X"], GRADE_COLORS["A"], GRADE_COLORS["B"],
    GRADE_COLORS["C"], GRADE_COLORS["D"],
]


def _sp(n) -> str:
    """Thousands-separated with a thin space, like the mockup (15 392)."""
    try:
        return f"{int(n):,}".replace(",", " ")
    except Exception:
        return str(n)


def _fmt_date(iso: Optional[str]) -> str:
    """ISO timestamp → DD.MM.YYYY, or em-dash when missing/unparseable."""
    if not iso:
        return "—"
    try:
        date_part = str(iso).split("T")[0]
        y, m, d = date_part.split("-")[:3]
        return f"{d}.{m}.{y}"
    except Exception:
        return "—"


def _grade_color(g: str):
    """Poster grade letter → osu! canonical colour (SS→X, SSH→XH)."""
    g = (g or "").upper()
    key = {"SS": "X", "SSH": "XH"}.get(g, g)
    return GRADE_COLORS.get(key, GRADE_COLORS["F"])


class ProfileCardMixin:
    """Single rich profile dashboard — one wide card, no inline pages."""

    # ── Profile-specific fonts (lazy, cached on the instance) ──

    def _pf_fonts(self) -> dict:
        cache = getattr(self, "_pf_font_cache", None)
        if cache is not None:
            return cache
        b = _find_font(TORUS_BOLD)
        s = _find_font(TORUS_SEMI) or b
        r = _find_font(TORUS_REG) or b

        def mk(path, size, fallback):
            try:
                return ImageFont.truetype(path, size) if path else fallback
            except Exception:
                return fallback

        f = {
            "name": mk(b, 58, self.font_big),
            "handle": mk(r, 28, self.font_subtitle),
            "country": mk(s, 23, self.font_subtitle),
            "rank_val": mk(b, 48, self.font_big),
            "rank_lbl": mk(s, 20, self.font_label),
            "country_val": mk(b, 42, self.font_big),
            "title": mk(s, 18, self.font_stat_label),
            "stat_val": mk(b, 36, self.font_stat_value),
            "stat_lbl": mk(s, 16, self.font_stat_label),
            "grade": mk(b, 34, self.font_row),
            "count": mk(b, 20, self.font_label),
            "ps_lbl": mk(r, 19, self.font_label),
            "ps_val": mk(b, 19, self.font_row),
            "poster_pp": mk(b, 16, self.font_row),
            "poster_acc": mk(r, 13, self.font_small),
            "poster_grade": mk(b, 34, self.font_grade),
            "axis": mk(r, 14, self.font_small),
            "pill": mk(b, 18, self.font_label),
            "footer": mk(r, 17, self.font_small),
            "total": mk(s, 17, self.font_stat_label),
        }

        # Register CJK fallbacks for the fonts that render user-supplied text
        # (username / handle), so cyrillic / kana don't tofu.
        mpb = _find_font(MPLUS_BOLD)
        mpr = _find_font(MPLUS_REG) or mpb

        def mfb(path, size):
            try:
                return ImageFont.truetype(path, size) if path else None
            except Exception:
                return None

        fb_map = getattr(self, "_fb_map", None)
        if isinstance(fb_map, dict):
            fb_map[id(f["name"])] = mfb(mpb, 58)
            fb_map[id(f["handle"])] = mfb(mpr, 28)
            fb_map[id(f["country"])] = mfb(mpr, 23)
            # ps_lbl renders the russian "Недостаточно данных" empty-state line.
            fb_map[id(f["ps_lbl"])] = mfb(mpr, 19)

        self._pf_font_cache = f
        return f

    # ── Public entrypoints ──

    def generate_profile_dashboard(
        self,
        data: Dict,
        avatar: Optional[Image.Image] = None,
        cover: Optional[Image.Image] = None,
        top_bg_images: Optional[List[Optional[Image.Image]]] = None,
    ) -> BytesIO:
        W, H = DASH_W, DASH_H
        img, draw = self._create_canvas(W, H)
        draw.rectangle([(0, 0), (W, H)], fill=COL_BG)

        # Outer card frame.
        self._pf_panel(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M),
                       radius=24, fill=COL_CARD, border=COL_CARD_BORDER)
        draw = ImageDraw.Draw(img)
        fonts = self._pf_fonts()

        self._pf_hero(img, data, avatar, cover, fonts)
        self._pf_stats_strip(img, data, fonts)
        self._pf_left_panel(img, data, top_bg_images, fonts)
        self._pf_right_panel(img, data, fonts)
        self._pf_footer(img, data, fonts)

        return self._save(img)

    async def generate_profile_dashboard_async(self, data: Dict) -> BytesIO:
        avatar_url = data.get("avatar_url")
        cover_url = data.get("cover_url")
        scores = (data.get("top_scores", []) or [])[:5]
        cover_urls = []
        for sc in scores:
            bsid = sc.get("beatmapset_id", 0)
            cover_urls.append(
                f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
            )

        tasks = [
            download_image(avatar_url) if avatar_url else _none_coro(),
            download_image(cover_url) if cover_url else _none_coro(),
        ] + [download_image(u) if u else _none_coro() for u in cover_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _ok(r):
            return r if not isinstance(r, Exception) and r is not None else None

        avatar = _ok(results[0])
        cover = _ok(results[1])
        top_bg_images = [_ok(r) for r in results[2:]]

        return await asyncio.to_thread(
            self.generate_profile_dashboard, data, avatar, cover, top_bg_images
        )

    # ── Shared primitives ──

    def _pf_panel(self, img, box, *, radius=16, fill=COL_PANEL, border=COL_PANEL_BORDER):
        self._aa_rounded_fill(img, box, radius=radius, fill=fill)
        if border:
            self._aa_rounded_outline(img, box, radius=radius, outline=border, width=1)

    def _pf_section_title(self, draw, x, y, text, fonts):
        self._draw_text(draw, (x, y), text, fonts["title"], COL_RED)

    # ── Hero band ──

    def _pf_hero(self, img, data, avatar, cover, fonts):
        cw = DASH_W - 2 * CARD_M
        hero_h = HERO_BOTTOM - CARD_M

        # Banner image, top-right, fading into the card on the left, clipped to
        # the card's top rounded corners.
        if cover:
            try:
                banner = cover_center_crop(cover, cw, hero_h)
                banner = Image.alpha_composite(banner, Image.new("RGBA", (cw, hero_h), (0, 0, 0, 96)))
                # Top corners rounded, bottom straight.
                tall = Image.new("L", (cw, hero_h + 60), 0)
                ImageDraw.Draw(tall).rounded_rectangle((0, 0, cw - 1, hero_h + 59), radius=24, fill=255)
                corner_mask = tall.crop((0, 0, cw, hero_h))
                # Left→right fade so the banner only shows on the right half.
                fade = Image.new("L", (cw, hero_h), 0)
                fd = ImageDraw.Draw(fade)
                for gx in range(cw):
                    t = (gx - 0.40 * cw) / (0.42 * cw)
                    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                    fd.line([(gx, 0), (gx, hero_h)], fill=int(t * 235))
                mask = ImageChops.multiply(corner_mask, fade)
                img.paste(banner.convert("RGB"), (CARD_M, CARD_M), mask)
            except Exception:
                pass
        draw = ImageDraw.Draw(img)

        # Avatar — huge circular portrait with a warm red glow ring.
        d = 212
        ax, ay = 46, 46
        glow = Image.new("RGBA", (d + 80, d + 80), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse((40 - 16, 40 - 16, 40 + d + 16, 40 + d + 16), fill=(228, 72, 72, 150))
        glow = glow.filter(ImageFilter.GaussianBlur(18))
        img.paste(glow, (ax - 40, ay - 40), glow)
        if avatar:
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            cmask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(cmask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (ax, ay), cmask)
        else:
            self._aa_ellipse_fill(img, (ax, ay, ax + d, ay + d), fill=(52, 40, 42))
        self._aa_ellipse_outline(img, (ax, ay, ax + d, ay + d), outline=(228, 76, 76), width=7)
        draw = ImageDraw.Draw(img)

        # Name + osu!supporter badge + handle.
        nx = ax + d + 28
        name = str(data.get("username", "???"))
        self._draw_text_shadow(draw, (nx, 50), name, fonts["name"], COL_WHITE)
        nw, _ = self._text_size(draw, name, fonts["name"])
        if data.get("is_supporter"):
            self._pf_supporter_badge(img, nx + nw + 26, 80)
            draw = ImageDraw.Draw(img)
        handle = data.get("handle") or ("@" + name.lower())
        self._draw_text(draw, (nx, 120), handle, fonts["handle"], (188, 150, 152))

        # Faint divider under the name row, spanning to the card's right edge.
        draw.line([(nx, 162), (DASH_W - CARD_M - CARD_PAD, 162)], fill=COL_DIVIDER, width=1)

        # Flag + country below the divider, country name vertically centred on
        # the flag.
        flag = load_flag(str(data.get("country", "") or ""), height=30)
        fy = 192
        cur = nx
        if flag:
            self._aa_rounded_outline(img, (nx, fy, nx + flag.width, fy + flag.height),
                                     radius=4, outline=(82, 58, 60), width=1)
            img.paste(flag, (nx, fy), flag)
            cur = nx + flag.width + 14
            draw = ImageDraw.Draw(img)
        cname = data.get("country_name") or str(data.get("country", "") or "").upper()
        if cname and cname != "—":
            _, ch = self._text_size(draw, cname, fonts["country"])
            self._draw_text(draw, (cur, fy + (30 - ch) // 2), cname, fonts["country"], COL_WHITE)

        # Rankings, LEFT-aligned at the example's column (~68% width): Global
        # well above the divider, Country well below it, so the thin line is
        # cleanly separated from both and the block reads harmoniously.
        rank_x = 872
        gr = data.get("global_rank", 0) or 0
        cr = data.get("country_rank", 0) or 0
        self._draw_text(draw, (rank_x, 56), "Global Ranking", fonts["rank_lbl"], COL_MUTED)
        self._draw_text(draw, (rank_x, 80), f"#{_sp(gr)}" if gr else "—", fonts["rank_val"], COL_WHITE)
        self._draw_text(draw, (rank_x, 182), "Country Ranking", fonts["rank_lbl"], COL_MUTED)
        self._draw_text(draw, (rank_x, 206), f"#{_sp(cr)}" if cr else "—", fonts["country_val"], COL_CORAL)

    def _pf_supporter_badge(self, img, x, cy):
        """osu!supporter badge — a pink capsule with a white heart glyph.

        The heart is `assets/icons/heart.png` (a white silhouette), recoloured
        white over the pink capsule fill, with a soft pink glow behind it.
        """
        ph, pw = 40, 64
        y0 = cy - ph // 2
        glow = Image.new("RGBA", (pw + 40, ph + 40), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(
            (20, 20, 20 + pw, 20 + ph), radius=ph // 2, fill=(255, 110, 178, 130))
        glow = glow.filter(ImageFilter.GaussianBlur(10))
        img.paste(glow, (x - 20, y0 - 20), glow)
        self._aa_rounded_fill(img, (x, y0, x + pw, y0 + ph), radius=ph // 2, fill=COL_HEART)
        heart = load_icon("heart", 26)
        if heart:
            white = Image.new("RGBA", heart.size, (255, 255, 255, 255))
            white.putalpha(heart.split()[3])
            img.paste(white, (x + (pw - heart.width) // 2, y0 + (ph - heart.height) // 2), white)

    # ── Stats strip ──

    def _pf_stats_strip(self, img, data, fonts):
        self._pf_panel(img, (INNER_L, STATS_Y0, INNER_R, STATS_Y1), radius=14)
        draw = ImageDraw.Draw(img)
        y_lbl = STATS_Y0 + 18
        y_val = STATS_Y0 + 44

        pp = data.get("pp", 0) or 0
        cols = [
            (72, "Performance", f"{_sp(int(pp))}pp" if pp else "—", COL_CORAL),
            (286, "Accuracy", f"{(data.get('accuracy', 0) or 0):.2f}%", COL_WHITE),
            (496, "Play Count", _sp(data.get("play_count", 0) or 0), COL_WHITE),
        ]
        for x, label, value, vcol in cols:
            self._draw_text(draw, (x, y_lbl), label, fonts["stat_lbl"], COL_MUTED)
            self._draw_text(draw, (x, y_val), value, fonts["stat_val"], vcol)

        # Level — framed badge with the number in the accent colour, then bar.
        lx = 700
        level = data.get("level", 0) or 0
        prog = data.get("level_progress", 0) or 0
        self._draw_text(draw, (lx, y_lbl), "Level", fonts["stat_lbl"], COL_MUTED)
        lvl_str = str(level)
        lvw, lvh = self._text_size(draw, lvl_str, fonts["stat_val"])
        fx0, fy0 = lx - 12, y_val - 6
        fx1, fy1 = lx + lvw + 12, y_val + lvh + 6
        self._aa_rounded_outline(img, (fx0, fy0, fx1, fy1), radius=10, outline=COL_RED, width=2)
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (lx, y_val), lvl_str, fonts["stat_val"], COL_CORAL)

        bar_x0, bar_x1 = fx1 + 18, 968
        bar_h = 10
        bar_y = (fy0 + fy1) // 2 - bar_h // 2
        self._aa_rounded_fill(img, (bar_x0, bar_y, bar_x1, bar_y + bar_h), radius=5, fill=COL_TRACK)
        inner = int((bar_x1 - bar_x0) * max(0, min(100, prog)) / 100)
        if inner > 6:
            self._pf_hgrad(img, bar_x0, bar_y, inner, bar_h, (200, 52, 52), (240, 124, 96), radius=5)
        draw = ImageDraw.Draw(img)
        # Percent sits just above the right end of the progress bar.
        pct = f"{int(prog)}%"
        pct_w, pct_h = self._text_size(draw, pct, fonts["count"])
        self._draw_text(draw, (bar_x1 - pct_w, bar_y - pct_h - 2), pct, fonts["count"], COL_CORAL)

        # Join Date / Last Seen, right-aligned block.
        jx = 1080
        self._draw_text(draw, (jx, STATS_Y0 + 14), "Join Date", fonts["stat_lbl"], COL_MUTED)
        self._draw_text(draw, (jx, STATS_Y0 + 34), _fmt_date(data.get("join_date")), fonts["ps_val"], COL_WHITE)
        self._draw_text(draw, (jx, STATS_Y0 + 62), "Last Seen", fonts["stat_lbl"], COL_MUTED)
        if data.get("is_online"):
            self._draw_text(draw, (jx, STATS_Y0 + 82), "Online", fonts["ps_val"], COL_GREEN)
        else:
            self._draw_text(draw, (jx, STATS_Y0 + 82), _fmt_date(data.get("last_visit")), fonts["ps_val"], COL_WHITE)

    # ── Left big panel: RANKED SCORE + RECENT TOP PLAYS ──

    def _pf_left_panel(self, img, data, top_bg_images, fonts):
        self._pf_panel(img, (LEFT_X0, MID_Y0, LEFT_X1, PANEL_Y1), radius=16)
        draw = ImageDraw.Draw(img)
        cx0 = LEFT_X0 + 28
        cx1 = LEFT_X1 - 28
        self._pf_section_title(draw, cx0, MID_Y0 + 20, "RANKED SCORE (TOTAL)", fonts)

        gc = data.get("grade_counts", {}) or {}

        def gsum(keys):
            return sum(int(gc.get(k, 0) or 0) for k in keys)

        width = cx1 - cx0
        slot = width / 6
        circ_top = MID_Y0 + 52
        cd = 52
        # Big borderless grade letters with a coloured glow. All six glow shapes
        # go on one layer that's blurred once and composited twice (brighter
        # halo), then the crisp letters are drawn on top.
        cyl = circ_top + cd // 2
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        letters = []
        for i, (letter, keys, color) in enumerate(GRADES):
            cc = int(cx0 + slot * i + slot / 2)
            lw_, lh_ = self._text_size(gdraw, letter, fonts["grade"])
            lx_, ly_ = int(cc - lw_ / 2), int(cyl - lh_ / 2)
            self._draw_text(gdraw, (lx_, ly_), letter, fonts["grade"], color)
            letters.append((cc, lx_, ly_, letter, color, gsum(keys)))
        glow = glow.filter(ImageFilter.GaussianBlur(9))
        base = Image.alpha_composite(Image.alpha_composite(img.convert("RGBA"), glow), glow)
        img.paste(base.convert("RGB"))
        draw = ImageDraw.Draw(img)
        for cc, lx_, ly_, letter, color, cnt in letters:
            self._draw_text(draw, (lx_, ly_), letter, fonts["grade"], color)
            self._text_center(draw, cc, circ_top + cd + 8, _sp(cnt), fonts["count"], COL_WHITE)

        # Rainbow bar.
        bar_y = circ_top + cd + 40
        self._pf_rainbow(img, cx0, bar_y, width, 14)
        draw = ImageDraw.Draw(img)

        # Total maps played — label nudged +3px, value +4px.
        total = data.get("total_maps", 0) or 0
        tmp_y = bar_y + 26
        self._draw_text(draw, (cx0, tmp_y + 3), "TOTAL MAPS PLAYED:", fonts["total"], COL_RED)
        lw, _ = self._text_size(draw, "TOTAL MAPS PLAYED:", fonts["total"])
        self._draw_text(draw, (cx0 + lw + 10, tmp_y + 3), _sp(total), fonts["ps_val"], COL_WHITE)

        # Divider, then RECENT TOP PLAYS posters.
        div_y = tmp_y + 36
        draw.line([(cx0, div_y), (cx1, div_y)], fill=COL_DIVIDER, width=1)
        self._pf_section_title(draw, cx0, div_y + 14, "TOP PLAYS", fonts)

        scores = (data.get("top_scores", []) or [])[:5]
        post_y = div_y + 42
        post_h = PANEL_Y1 - 24 - post_y
        gap = 11
        pw = int((width - 4 * gap) / 5)
        for i in range(5):
            px = cx0 + i * (pw + gap)
            sc = scores[i] if i < len(scores) else None
            bg = top_bg_images[i] if top_bg_images and i < len(top_bg_images) else None
            self._pf_poster(img, px, post_y, pw, post_h, sc, bg, fonts)
            draw = ImageDraw.Draw(img)

    def _pf_poster(self, img, x, y, w, h, sc, cover, fonts):
        if cover:
            try:
                crop = cover_center_crop(cover, w, h)
            except Exception:
                crop = Image.new("RGBA", (w, h), (46, 36, 38, 255))
        else:
            crop = Image.new("RGBA", (w, h), (44, 34, 36, 255))
        # Darken toward the bottom so the grade/pp text reads.
        grad = Image.new("L", (w, h), 0)
        gd = ImageDraw.Draw(grad)
        for gy in range(h):
            t = (gy / h - 0.38) / 0.62
            gd.line([(0, gy), (w, gy)], fill=int(max(0.0, min(1.0, t)) * 235))
        crop = Image.composite(Image.new("RGBA", (w, h), (12, 7, 9, 255)), crop, grad)
        mask = self._rounded_mask((w, h), 12)
        img.paste(crop.convert("RGB"), (x, y), mask)
        self._aa_rounded_outline(img, (x, y, x + w, y + h), radius=12, outline=COL_PANEL_BORDER, width=1)
        draw = ImageDraw.Draw(img)

        if not sc:
            return
        grade = sc.get("rank", "S") or "S"
        pp = sc.get("pp") or 0
        acc = sc.get("accuracy", 0) or 0
        # Big grade letter on the left, vertically bracketing the pp/acc block.
        gw, gh = self._text_size(draw, grade, fonts["poster_grade"])
        self._draw_text_shadow(draw, (x + 10, y + h - 8 - gh), grade, fonts["poster_grade"], _grade_color(grade))
        # pp (smaller) over accuracy, both right-aligned to the card.
        self._text_right(draw, x + w - 10, y + h - 40, f"{int(pp)}pp", fonts["poster_pp"], COL_WHITE, shadow=True)
        self._text_right(draw, x + w - 10, y + h - 21, f"{acc:.2f}%", fonts["poster_acc"], (205, 203, 214), shadow=True)

    # ── Right big panel: PLAY STATS + PERFORMANCE HISTORY ──

    def _pf_right_panel(self, img, data, fonts):
        self._pf_panel(img, (RIGHT_X0, MID_Y0, RIGHT_X1, PANEL_Y1), radius=16)
        draw = ImageDraw.Draw(img)
        cx0 = RIGHT_X0 + 28
        cx1 = RIGHT_X1 - 28
        self._pf_section_title(draw, cx0, MID_Y0 + 20, "PLAY STATS", fonts)

        rows = [
            ("hiticon", "Total Hits", _sp(data.get("total_hits", 0) or 0)),
            ("combo", "Maximum Combo", f"{_sp(data.get('maximum_combo', 0) or 0)}x"),
            ("replayicon", "Replays Watched", _sp(data.get("replays_watched", 0) or 0)),
            ("star", "Total Score", _sp(data.get("total_score", 0) or 0)),
            ("timer", "Hours Played", str(data.get("play_time", "—"))),
        ]
        ry = MID_Y0 + 56
        step = 30
        icon_sz = 22
        for icon_name, label, value in rows:
            icon = load_icon(icon_name, icon_sz)
            tx = cx0
            if icon:
                img.paste(icon, (cx0, ry + 2), icon)
                draw = ImageDraw.Draw(img)
                tx = cx0 + 32
            # Vertically centre both the label and the value on the icon.
            _, lh = self._text_size(draw, label, fonts["ps_lbl"])
            ty = ry + (icon_sz - lh) // 2
            self._draw_text(draw, (tx, ty), label, fonts["ps_lbl"], (208, 206, 222))
            self._text_right(draw, cx1, ty, value, fonts["ps_val"], COL_WHITE)
            ry += step

        # Divider, then PERFORMANCE HISTORY graph.
        div_y = MID_Y0 + 56 + 5 * step + 6
        draw.line([(cx0, div_y), (cx1, div_y)], fill=COL_DIVIDER, width=1)
        self._pf_section_title(draw, cx0, div_y + 14, "PERFORMANCE HISTORY", fonts)

        pp_history = data.get("pp_history") or []
        rank_history = data.get("rank_history") or []
        gx0 = cx0 + 42                       # leave room for y-axis labels
        gx1 = cx1
        gy0 = div_y + 46
        gy1 = PANEL_Y1 - 48                  # leave room for x-axis labels
        if pp_history and len(pp_history) >= 2:
            cur = data.get("pp", 0) or 0
            self._pf_graph(img, list(pp_history), gx0, gy0, gx1 - gx0, gy1 - gy0,
                           fonts, is_rank=False, pill=f"{_sp(int(cur))}pp")
        elif rank_history and len(rank_history) >= 2:
            cur = int(rank_history[-1])
            self._pf_graph(img, list(rank_history), gx0, gy0, gx1 - gx0, gy1 - gy0,
                           fonts, is_rank=True, pill=f"#{_sp(cur)}")
        else:
            draw = ImageDraw.Draw(img)
            self._text_center(draw, (gx0 + gx1) // 2, (gy0 + gy1) // 2 - 10,
                              "Недостаточно данных", fonts["ps_lbl"], COL_MUTED)

    def _pf_graph(self, img, vals, x, y, w, h, fonts, *, is_rank, pill):
        draw = ImageDraw.Draw(img)
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        pad = rng * 0.12
        lo -= pad
        hi += pad
        rng = hi - lo

        def _y(v):
            ratio = (v - lo) / rng
            return y + (int(ratio * h) if is_rank else int(h - ratio * h))

        def _fmt(v):
            if is_rank:
                return f"#{_sp(int(v))}"
            g = v / 1000.0
            return (f"{g:.1f}".rstrip("0").rstrip(".") + "k") if v >= 1000 else str(int(v))

        # Horizontal gridlines + y-axis labels.
        for gi in range(4):
            gy = y + int(h * gi / 3)
            draw.line([(x, gy), (x + w, gy)], fill=(46, 38, 40), width=1)
            v = hi - rng * gi / 3
            self._text_right(draw, x - 10, gy - 8, _fmt(v), fonts["axis"], COL_MUTED)

        step = w / (len(vals) - 1)
        coords = [(int(x + i * step), _y(v)) for i, v in enumerate(vals)]

        # Soft gradient fill under the line.
        fill_img = Image.new("RGBA", img.size, (0, 0, 0, 0))
        fd = ImageDraw.Draw(fill_img)
        poly = list(coords) + [(coords[-1][0], y + h), (coords[0][0], y + h)]
        fd.polygon(poly, fill=(228, 72, 72, 55))
        base = img.convert("RGBA")
        base = Image.alpha_composite(base, fill_img)
        img.paste(base.convert("RGB"))
        draw = ImageDraw.Draw(img)

        for i in range(len(coords) - 1):
            draw.line([coords[i], coords[i + 1]], fill=(236, 92, 92), width=3)
        ex, ey = coords[-1]
        draw.ellipse((ex - 5, ey - 5, ex + 5, ey + 5), fill=(245, 120, 120))

        # X-axis labels.
        labels = ["90 days ago", "60 days ago", "30 days ago", "now"]
        for i, lbl in enumerate(labels):
            lx = x + int(w * i / 3)
            if i == 0:
                self._draw_text(draw, (lx, y + h + 8), lbl, fonts["axis"], COL_MUTED)
            elif i == 3:
                self._text_right(draw, x + w, y + h + 8, lbl, fonts["axis"], COL_MUTED)
            else:
                self._text_center(draw, lx, y + h + 8, lbl, fonts["axis"], COL_MUTED)

        # Pill anchored to the plot's top-right corner — detached from the
        # line's end point, with the value centred inside it.
        pw, ph = self._text_size(draw, pill, fonts["pill"])
        pill_w, pill_h = pw + 28, ph + 14
        bx1 = x + w
        bx0 = bx1 - pill_w
        by0 = y - 6
        self._aa_rounded_fill(img, (bx0, by0, bx1, by0 + pill_h), radius=pill_h // 2, fill=(208, 56, 56))
        d3 = ImageDraw.Draw(img)
        self._text_center(d3, (bx0 + bx1) // 2, by0 + (pill_h - ph) // 2, pill, fonts["pill"], (255, 255, 255))

    # ── Gradient helpers ──

    def _pf_hgrad(self, img, x, y, w, h, c0, c1, *, radius=0):
        if w <= 0:
            return
        strip = Image.new("RGB", (w, h), c0)
        sd = ImageDraw.Draw(strip)
        for px in range(w):
            t = px / max(1, w - 1)
            c = tuple(int(c0[k] + (c1[k] - c0[k]) * t) for k in range(3))
            sd.line([(px, 0), (px, h)], fill=c)
        mask = self._rounded_mask((w, h), radius) if radius else None
        img.paste(strip, (x, y), mask)

    def _pf_rainbow(self, img, x, y, w, h):
        strip = Image.new("RGB", (w, h), RAINBOW[0])
        sd = ImageDraw.Draw(strip)
        n = len(RAINBOW) - 1
        for px in range(w):
            t = px / max(1, w - 1) * n
            i = min(int(t), n - 1)
            f = t - i
            c0, c1 = RAINBOW[i], RAINBOW[i + 1]
            c = tuple(int(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
            sd.line([(px, 0), (px, h)], fill=c)
        mask = self._rounded_mask((w, h), h // 2)
        img.paste(strip, (x, y), mask)

    # ── Footer ──

    def _pf_footer(self, img, data, fonts):
        draw = ImageDraw.Draw(img)
        y = DASH_H - CARD_M - 30
        osu_id = data.get("osu_id", 0)
        self._text_right(draw, INNER_R, y + 1,
                         f"https://osu.ppy.sh/users/{osu_id}", fonts["footer"], COL_RED)
