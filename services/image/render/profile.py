import asyncio
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, Optional, List

from PIL import Image, ImageDraw, ImageFilter, ImageChops, ImageFont

from services.image import colors
from services.image.constants import (
    TORUS_BOLD,
    TORUS_SEMI,
    TORUS_REG,
    MPLUS_BOLD,
    MPLUS_REG,
    PROXIMA_BOLD,
    PROXIMA_SEMI,
    PROXIMA_REG,
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

# ── Palette (red 1984 theme) — sourced from services/image/colors.py, the
# shared design-system module this card's own palette became the basis for.
COL_BG = colors.BG
COL_CARD = colors.CARD
COL_CARD_BORDER = colors.CARD_BORDER
COL_PANEL = colors.PANEL
COL_PANEL_BORDER = colors.PANEL_BORDER
COL_RED = colors.ACCENT            # section titles / accents
COL_CORAL = colors.ACCENT_PP       # pp value, country rank
COL_WHITE = colors.TEXT_PRIMARY
COL_MUTED = colors.TEXT_MUTED
COL_GREEN = colors.POSITIVE
COL_TRACK = colors.TRACK
COL_DIVIDER = colors.DIVIDER
COL_HEART = colors.HEART           # osu!supporter pink heart

# The five grades osu! actually reports, low→high: A, S, silver S (SH), SS,
# silver SS (SSH). Each pulls its own count — gold and silver variants are
# distinguished purely by colour (gold = X/S, silver = XH/SH).
GRADES = [
    ("A", "a", GRADE_COLORS["A"]),
    ("S", "s", GRADE_COLORS["S"]),
    ("S", "sh", GRADE_COLORS["SH"]),
    ("SS", "ss", GRADE_COLORS["X"]),
    ("SS", "ssh", GRADE_COLORS["XH"]),
]

# UI label translations (2026-07-02b — see [[card-language-preference]]).
_PF_STRINGS = {
    "en": {
        "global_ranking": "Global Ranking", "country_ranking": "Country Ranking",
        "unknown_country": "Unknown", "level": "Level", "performance": "Performance",
        "accuracy": "Accuracy", "play_count": "Play Count",
        "join_date": "Join Date", "last_seen": "Last Seen",
        "online": "Online", "hidden": "Hidden",
        "grades": "GRADES", "top_plays": "TOP PLAYS", "player_stats": "PLAYER STATS",
        "rank_history": "RANK HISTORY", "total_maps": "TOTAL MAPS PLAYED:",
        "total_hits": "Total Hits", "avg_hits": "Avg Hits / Play",
        "max_combo": "Maximum Combo", "replays_watched": "Replays Watched",
        "total_score": "Total Score", "hours_played": "Hours Played",
        "not_enough_data": "Not enough data",
        "axis_90d": "90 days ago", "axis_60d": "60 days ago",
        "axis_30d": "30 days ago", "axis_now": "now",
        "hours_suffix": "h",
    },
    "ru": {
        # performance/accuracy/play_count sit in fixed-width columns and
        # join_date/last_seen in a right-aligned block (see `jx` below) — long
        # translations here can overflow; measure against Torus at the actual
        # draw size before widening a label.
        "global_ranking": "Мировой рейтинг", "country_ranking": "Рейтинг страны",
        "unknown_country": "Неизвестно", "level": "Уровень", "performance": "PP",
        "accuracy": "Точность", "play_count": "Игр сыграно",
        "join_date": "Зарегистрирован", "last_seen": "В сети",
        "online": "Сейчас", "hidden": "Скрыто",
        
        "grades": "ОЦЕНКИ", "top_plays": "ТОП ИГР", "player_stats": "СТАТИСТИКА ИГРОКА",
        "rank_history": "ИСТОРИЯ РЕЙТИНГА", "total_maps": "ВСЕГО ПОЛУЧЕНО ОЦЕНОК:",
        "total_hits": "Всего попаданий", "avg_hits": "Ср. попаданий на игру",
        "max_combo": "Макс. комбо", "replays_watched": "Просмотров реплеев",
        "total_score": "Всего очков", "hours_played": "Часов сыграно",
        "not_enough_data": "Недостаточно данных",
        "axis_90d": "90д. назад", "axis_60d": "60д. назад",
        "axis_30d": "30д. назад", "axis_now": "сейчас",
        "hours_suffix": "ч",
    },
}


def _pf_lang(data) -> dict:
    lang = (data.get("lang") or "en").lower()
    return _PF_STRINGS.get(lang, _PF_STRINGS["en"])


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


def _fmt_last_seen(iso: Optional[str], lang: str = "en") -> str:
    """Last-visit timestamp → coarse relative age ("5m ago" … "3w ago"), an
    absolute date once it's months old, or "Hidden" when osu! reports no
    last_visit (the user hides their online presence)."""
    S = _PF_STRINGS.get((lang or "en").lower(), _PF_STRINGS["en"])
    if not iso:
        return S["hidden"]
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return _fmt_date(iso)
    secs = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    ru = (lang or "en").lower() == "ru"
    if secs < 60:
        return f"{int(secs)}с назад" if ru else f"{int(secs)}s ago"
    if secs < 3600:
        return f"{int(secs // 60)}м назад" if ru else f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}ч назад" if ru else f"{int(secs // 3600)}h ago"
    days = secs / 86400
    if days < 7:
        return f"{int(days)}д назад" if ru else f"{int(days)}d ago"
    if days < 35:
        return f"{int(days // 7)}нед назад" if ru else f"{int(days // 7)}w ago"
    return _fmt_date(iso)


def _grade_color(g: str):
    """Grade rank → osu! canonical colour. Silver (XH/SH) and gold (X/S)
    variants keep their own colour; SS/SSH aliases map onto X/XH."""
    g = (g or "").upper()
    key = {"SS": "X", "SSH": "XH"}.get(g, g)
    return GRADE_COLORS.get(key, GRADE_COLORS["F"])


def _grade_letter(g: str) -> str:
    """Display letter for an osu! rank: silver/gold share a letter (the silver
    'H' suffix is dropped, the colour alone marks it). X/XH→SS, S/SH→S."""
    g = (g or "").upper()
    return {"X": "SS", "XH": "SS", "S": "S", "SH": "S"}.get(g, g)


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
            "atitle": mk(b, 33, self.font_big),
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
            "poster_grade": mk(b, 42, self.font_grade),
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

        # Cyrillic-specific fallback (2026-07-02b): ProximaSoft, weight-matched
        # per slot, for every slot that can now carry translated UI text or an
        # RU active-title name — takes priority over the CJK fallback above.
        pxb = _find_font(PROXIMA_BOLD)
        pxs = _find_font(PROXIMA_SEMI) or pxb
        pxr = _find_font(PROXIMA_REG) or pxb
        fb_cy_map = getattr(self, "_fb_cyrillic_map", None)
        if isinstance(fb_cy_map, dict):
            cy_sizes = {
                "name": (pxb, 58), "handle": (pxr, 28), "country": (pxs, 23),
                "atitle": (pxb, 33), "rank_lbl": (pxs, 20), "title": (pxs, 18),
                "stat_lbl": (pxs, 16), "ps_lbl": (pxr, 19), "ps_val": (pxb, 19),
                "axis": (pxr, 14), "total": (pxs, 17),
            }
            for key, (path, size) in cy_sizes.items():
                fb_cy_map[id(f[key])] = mfb(path, size)

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

        # Re-stroke the outer frame last so the hero banner (pasted over the top
        # corners) can't paint over the card's border.
        self._aa_rounded_outline(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M),
                                 radius=24, outline=COL_CARD_BORDER, width=1)

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
        S = _pf_lang(data)
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
            self._pf_supporter_badge(img, nx + nw + 12, 80)
            draw = ImageDraw.Draw(img)
        # Subtitle stack under the name: @handle, then the active title
        # (evenly spaced between the handle and the flag), then flag+country.
        sy = 110
        handle = data.get("handle")  # Telegram @handle, only when known.
        if handle:
            self._draw_text(draw, (nx, sy), handle, fonts["handle"], (188, 150, 152))
            sy += 40
        title = data.get("title")
        if title:
            self._pf_title_text(img, nx, sy, title, data.get("title_color") or COL_RED,
                                 fonts["atitle"])
            sy += 44
            draw = ImageDraw.Draw(img)

        # Flag + country. Rides up close to the name when nothing sits above it.
        flag = load_flag(str(data.get("country", "") or ""), height=30)
        has_subtitle = bool(title) or bool(handle)
        fy = sy + 2 if has_subtitle else 128
        cur = nx
        if flag:
            self._aa_rounded_outline(img, (nx, fy, nx + flag.width, fy + flag.height),
                                     radius=4, outline=(82, 58, 60), width=1)
            img.paste(flag, (nx, fy), flag)
            cur = nx + flag.width + 14
            draw = ImageDraw.Draw(img)
        raw_cc = str(data.get("country", "") or "").strip()
        cname = data.get("country_name") or (
            raw_cc.upper() if raw_cc and raw_cc not in ("—", "__", "--") else ""
        )
        if not cname or cname in ("—", "__", "--"):
            cname = S["unknown_country"]
        _, ch = self._text_size(draw, cname, fonts["country"])
        self._draw_text_shadow(draw, (cur, fy + (30 - ch) // 2), cname, fonts["country"], COL_WHITE)

        # Rankings, LEFT-aligned at ~68% width. Drawn with a drop shadow so they
        # stay legible where they overlap a light cover banner.
        rank_x = 872
        gr = data.get("global_rank", 0) or 0
        cr = data.get("country_rank", 0) or 0
        self._draw_text_shadow(draw, (rank_x, 56), S["global_ranking"], fonts["rank_lbl"], COL_MUTED)
        self._draw_text_shadow(draw, (rank_x, 80), f"#{_sp(gr)}" if gr else "—", fonts["rank_val"], COL_WHITE)
        self._draw_text_shadow(draw, (rank_x, 182), S["country_ranking"], fonts["rank_lbl"], COL_MUTED)
        self._draw_text_shadow(draw, (rank_x, 206), f"#{_sp(cr)}" if cr else "—", fonts["country_val"], COL_CORAL)

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

    def _pf_title_text(self, img, x, y, title, color, font):
        """Active title under the name: flat text in the rarity colour, with only
        the same subtle drop shadow as the name. No outline or glow — the name
        stays clean and readable, so a title reads by its words, not just its tier."""
        if not title:
            return
        draw = ImageDraw.Draw(img)
        self._draw_text_shadow(draw, (x, y), title, font, color)

    # ── Stats strip ──

    def _pf_stats_strip(self, img, data, fonts):
        S = _pf_lang(data)
        self._pf_panel(img, (INNER_L, STATS_Y0, INNER_R, STATS_Y1), radius=14)
        draw = ImageDraw.Draw(img)
        y_lbl = STATS_Y0 + 18
        y_val = STATS_Y0 + 44

        pp = data.get("pp", 0) or 0
        cols = [
            (72, S["performance"], f"{_sp(int(pp))}pp" if pp else "—", COL_CORAL),
            (286, S["accuracy"], f"{(data.get('accuracy', 0) or 0):.2f}%", COL_WHITE),
            (496, S["play_count"], _sp(data.get("play_count", 0) or 0), COL_WHITE),
        ]
        for x, label, value, vcol in cols:
            self._draw_text(draw, (x, y_lbl), label, fonts["stat_lbl"], COL_MUTED)
            self._draw_text(draw, (x, y_val), value, fonts["stat_val"], vcol)

        # Level — number in the accent colour, then the progress bar level with it.
        lx = 700
        level = data.get("level", 0) or 0
        prog = data.get("level_progress", 0) or 0
        self._draw_text(draw, (lx, y_lbl), S["level"], fonts["stat_lbl"], COL_MUTED)
        lvl_str = str(level)
        lvw, lvh = self._text_size(draw, lvl_str, fonts["stat_val"])
        self._draw_text(draw, (lx, y_val), lvl_str, fonts["stat_val"], COL_CORAL)

        bar_x0, bar_x1 = lx + lvw + 18, 968
        bar_h = 10
        # Centre the bar on the number's visual ink mid-line (not the text box,
        # which sits high due to ascent padding) so the two read on one level.
        try:
            _, gy0, _, gy1 = fonts["stat_val"].getbbox(lvl_str)
        except Exception:
            gy0, gy1 = 0, lvh
        bar_y = y_val + (gy0 + gy1) // 2 - bar_h // 2
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
        jx = 1020
        self._draw_text(draw, (jx, STATS_Y0 + 14), S["join_date"], fonts["stat_lbl"], COL_MUTED)
        self._draw_text(draw, (jx, STATS_Y0 + 34), _fmt_date(data.get("join_date")), fonts["ps_val"], COL_WHITE)
        self._draw_text(draw, (jx, STATS_Y0 + 62), S["last_seen"], fonts["stat_lbl"], COL_MUTED)
        lang = data.get("lang") or "en"
        if data.get("is_online"):
            seen_text, seen_col = S["online"], COL_GREEN
        else:
            seen_text = _fmt_last_seen(data.get("last_visit"), lang)
            seen_col = COL_MUTED if seen_text == S["hidden"] else COL_WHITE
        self._draw_text(draw, (jx, STATS_Y0 + 82), seen_text, fonts["ps_val"], seen_col)

    # ── Left big panel: RANKED SCORE + RECENT TOP PLAYS ──

    def _pf_left_panel(self, img, data, top_bg_images, fonts):
        S = _pf_lang(data)
        self._pf_panel(img, (LEFT_X0, MID_Y0, LEFT_X1, PANEL_Y1), radius=16)
        draw = ImageDraw.Draw(img)
        cx0 = LEFT_X0 + 28
        cx1 = LEFT_X1 - 28
        self._pf_section_title(draw, cx0, MID_Y0 + 20, S["grades"], fonts)

        gc = data.get("grade_counts", {}) or {}

        def gcount(key):
            return int(gc.get(key, 0) or 0)

        width = cx1 - cx0
        slot = width / len(GRADES)
        circ_top = MID_Y0 + 52
        cd = 52
        # Big borderless grade letters with a coloured glow. All glow shapes go
        # on one layer that's blurred once and composited twice (brighter halo),
        # then the crisp letters are drawn on top.
        cyl = circ_top + cd // 2
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        letters = []
        for i, (letter, key, color) in enumerate(GRADES):
            cc = int(cx0 + slot * i + slot / 2)
            # Centre each letter on its actual ink bbox (not the text box) so the
            # grades sit level with one another regardless of glyph differences.
            try:
                bx0, by0, bx1, by1 = fonts["grade"].getbbox(letter)
            except Exception:
                lw_, lh_ = self._text_size(gdraw, letter, fonts["grade"])
                bx0, by0, bx1, by1 = 0, 0, lw_, lh_
            lx_ = int(cc - (bx0 + bx1) / 2)
            ly_ = int(cyl - (by0 + by1) / 2)
            self._draw_text(gdraw, (lx_, ly_), letter, fonts["grade"], color)
            letters.append((cc, lx_, ly_, letter, color, gcount(key)))
        glow = glow.filter(ImageFilter.GaussianBlur(9))
        base = Image.alpha_composite(Image.alpha_composite(img.convert("RGBA"), glow), glow)
        img.paste(base.convert("RGB"))
        draw = ImageDraw.Draw(img)
        for cc, lx_, ly_, letter, color, cnt in letters:
            self._draw_text(draw, (lx_, ly_), letter, fonts["grade"], color)
            self._text_center(draw, cc, circ_top + cd + 8, _sp(cnt), fonts["count"], COL_WHITE)

        # Grade-distribution bar — proportional split across the five grades.
        bar_y = circ_top + cd + 40
        segments = [(gcount(key), color) for (letter, key, color) in GRADES]
        self._pf_grade_bar(img, cx0, bar_y, width, 14, segments)
        draw = ImageDraw.Draw(img)

        # Total maps played — label and value are independently positioned
        # (each on its own ink mid-line, not a common top edge) so one can move
        # without dragging the other.
        total = data.get("total_maps", 0) or 0
        label, val = S["total_maps"], _sp(total)
        cy_label = bar_y + 44
        cy_val = cy_label - 2

        def _vtop(text, font, cy):
            try:
                _, a, _, b = font.getbbox(text)
            except Exception:
                a, b = 0, self._text_size(draw, text, font)[1]
            return int(cy - (a + b) / 2)

        self._draw_text(draw, (cx0, _vtop(label, fonts["total"], cy_label)), label, fonts["total"], COL_RED)
        lw, _ = self._text_size(draw, label, fonts["total"])
        self._draw_text(draw, (cx0 + lw + 10, _vtop(val, fonts["ps_val"], cy_val)), val, fonts["ps_val"], COL_WHITE)

        # Divider, then RECENT TOP PLAYS posters.
        div_y = bar_y + 62
        draw.line([(cx0, div_y), (cx1, div_y)], fill=COL_DIVIDER, width=1)
        self._pf_section_title(draw, cx0, div_y + 14, S["top_plays"], fonts)

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
        rank = sc.get("rank", "S") or "S"
        grade = _grade_letter(rank)
        pp = sc.get("pp") or 0
        acc = sc.get("accuracy", 0) or 0
        # Big grade letter in the bottom-left corner, raised slightly off the edge.
        gw, gh = self._text_size(draw, grade, fonts["poster_grade"])
        self._draw_text_shadow(draw, (x + 9, y + h - 14 - gh), grade, fonts["poster_grade"], _grade_color(rank))
        # pp (smaller) over accuracy, both right-aligned to the card.
        self._text_right(draw, x + w - 10, y + h - 40, f"{int(pp)}pp", fonts["poster_pp"], COL_WHITE, shadow=True)
        self._text_right(draw, x + w - 10, y + h - 21, f"{acc:.2f}%", fonts["poster_acc"], (205, 203, 214), shadow=True)

    # ── Right big panel: PLAY STATS + PERFORMANCE HISTORY ──

    def _pf_right_panel(self, img, data, fonts):
        S = _pf_lang(data)
        self._pf_panel(img, (RIGHT_X0, MID_Y0, RIGHT_X1, PANEL_Y1), radius=16)
        draw = ImageDraw.Draw(img)
        cx0 = RIGHT_X0 + 28
        cx1 = RIGHT_X1 - 28
        self._pf_section_title(draw, cx0, MID_Y0 + 20, S["player_stats"], fonts)

        play_count = data.get("play_count", 0) or 0
        total_hits = data.get("total_hits", 0) or 0
        avg_hits = round(total_hits / play_count) if play_count else 0
        rows = [
            ("hiticon", S["total_hits"], _sp(total_hits)),
            ("hpp", S["avg_hits"], _sp(avg_hits) if play_count else "—"),
            ("combo", S["max_combo"], f"{_sp(data.get('maximum_combo', 0) or 0)}x"),
            ("replayicon", S["replays_watched"], _sp(data.get("replays_watched", 0) or 0)),
            ("star", S["total_score"], _sp(data.get("total_score", 0) or 0)),
            ("timer", S["hours_played"], str(data.get("play_time", "—"))),
        ]
        ry = MID_Y0 + 56
        step = 28
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

        # Divider, then RANK HISTORY graph. osu! only exposes a 90-day global
        # rank series (no pp history), so this plots rank — lower is better, so
        # the axis is inverted inside `_pf_graph` (is_rank=True).
        div_y = MID_Y0 + 56 + len(rows) * step + 6
        draw.line([(cx0, div_y), (cx1, div_y)], fill=COL_DIVIDER, width=1)
        self._text_center(draw, (cx0 + cx1) // 2, div_y + 14, S["rank_history"], fonts["title"], COL_RED)

        rank_history = [r for r in (data.get("rank_history") or []) if r]
        gx0 = cx0 + 42                       # leave room for y-axis labels
        gx1 = cx1
        gy0 = div_y + 46
        gy1 = PANEL_Y1 - 48                  # leave room for x-axis labels
        # Width available to a y-axis label: from just inside the panel's left
        # border up to the gap before the plot. Lets `_pf_graph` shrink the axis
        # font so 6–7 digit ranks don't spill past the frame.
        label_w = (gx0 - 10) - (RIGHT_X0 + 6)
        if len(rank_history) >= 2:
            self._pf_graph(img, list(rank_history), gx0, gy0, gx1 - gx0, gy1 - gy0,
                           fonts, is_rank=True, label_w=label_w, S=S)
        else:
            draw = ImageDraw.Draw(img)
            self._text_center(draw, (gx0 + gx1) // 2, (gy0 + gy1) // 2 - 10,
                              S["not_enough_data"], fonts["ps_lbl"], COL_MUTED)

    def _pf_graph(self, img, vals, x, y, w, h, fonts, *, is_rank, label_w=None, S=None):
        S = S or _PF_STRINGS["en"]
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

        # The 4 y-axis label values, then a font sized so the widest of them fits
        # the available gutter (`label_w`) — large ranks (6–7 digits) would
        # otherwise overflow past the frame at the fixed axis size.
        axis_vals = [(lo + rng * gi / 3) if is_rank else (hi - rng * gi / 3) for gi in range(4)]
        axis_labels = [_fmt(v) for v in axis_vals]
        axis_font = fonts["axis"]
        if label_w:
            widest = max(self._text_size(draw, s, axis_font)[0] for s in axis_labels)
            if widest > label_w:
                path = _find_font(TORUS_REG)
                size = 14
                while size > 9 and widest > label_w and path:
                    size -= 1
                    axis_font = ImageFont.truetype(path, size)
                    widest = max(self._text_size(draw, s, axis_font)[0] for s in axis_labels)

        # Horizontal gridlines + y-axis labels. The rank axis is inverted (a
        # better = smaller rank sits higher), so its labels ascend downward to
        # match the plotted line.
        for gi in range(4):
            gy = y + int(h * gi / 3)
            draw.line([(x, gy), (x + w, gy)], fill=(46, 38, 40), width=1)
            self._text_right(draw, x - 10, gy - 8, axis_labels[gi], axis_font, COL_MUTED)

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
        labels = [S["axis_90d"], S["axis_60d"], S["axis_30d"], S["axis_now"]]
        for i, lbl in enumerate(labels):
            lx = x + int(w * i / 3)
            if i == 0:
                self._draw_text(draw, (lx, y + h + 8), lbl, fonts["axis"], COL_MUTED)
            elif i == 3:
                self._text_right(draw, x + w, y + h + 8, lbl, fonts["axis"], COL_MUTED)
            else:
                self._text_center(draw, lx, y + h + 8, lbl, fonts["axis"], COL_MUTED)

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

    def _pf_grade_bar(self, img, x, y, w, h, segments):
        """Proportional grade-distribution bar with smooth colour transitions.

        `segments` is ``[(count, color)]``. Each grade's colour dominates a band
        proportional to its count, but neighbouring colours blend smoothly into
        one another (a colour stop sits at each band's centre) for a rainbow-like
        gradient instead of hard edges. All-zero → flat track."""
        total = sum(max(0, c) for c, _ in segments)
        mask = self._rounded_mask((w, h), h // 2)
        strip = Image.new("RGB", (w, h), COL_TRACK)
        nz = [(c, col) for c, col in segments if c > 0]
        if total > 0 and nz:
            # Each grade holds its solid colour across its proportional band; the
            # blend is confined to a short zone (BLEND px each side) around every
            # boundary, clamped so it never exceeds half of either band.
            BLEND = 9.0
            starts, widths = [], []
            cur = 0.0
            for c, _ in nz:
                seg_w = w * c / total
                starts.append(cur)
                widths.append(seg_w)
                cur += seg_w
            edges = []  # (boundary_x, left_color, right_color, radius)
            for i in range(len(nz) - 1):
                pos = starts[i] + widths[i]
                radius = min(BLEND, widths[i] / 2.0, widths[i + 1] / 2.0)
                edges.append((pos, nz[i][1], nz[i + 1][1], radius))

            def _band_color(px):
                for i in range(len(nz)):
                    if i == len(nz) - 1 or px < starts[i] + widths[i]:
                        return nz[i][1]
                return nz[-1][1]

            sd = ImageDraw.Draw(strip)
            for px in range(w):
                color = _band_color(px)
                for pos, lc, rc, radius in edges:
                    if radius > 0 and pos - radius <= px <= pos + radius:
                        t = (px - (pos - radius)) / (2.0 * radius)
                        color = tuple(int(lc[k] + (rc[k] - lc[k]) * t) for k in range(3))
                        break
                sd.line([(px, 0), (px, h)], fill=color)
        img.paste(strip, (x, y), mask)
