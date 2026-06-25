import asyncio
import re
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from services.image.constants import (
    TORUS_BOLD,
    TORUS_SEMI,
    TORUS_REG,
    MPLUS_BOLD,
    MPLUS_REG,
    MOD_COLORS,
)
from services.image.utils import (
    load_flag,
    load_icon,
    load_mod_icon,
    _find_font,
    _none_coro,
    download_image,
)
from services.image.render.profile import (
    CARD_M,
    COL_BG,
    COL_CARD,
    COL_CARD_BORDER,
    COL_PANEL,
    COL_PANEL_BORDER,
    COL_RED,
    COL_CORAL,
    COL_WHITE,
    COL_MUTED,
    COL_TRACK,
    COL_DIVIDER,
    _sp,
    _grade_color,
)
from services.image.render.duel_pool_card import _sr_color
from utils.titles import RARITY_ORDER, RARITY_META

# ── Geometry — landscape "collection" card mirroring the titlescollection mockup ─
TT_W = 1280
TT_H = 900
INNER_L = CARD_M + 28                 # 44
INNER_R = TT_W - CARD_M - 28          # 1236

HEAD_Y0 = CARD_M                       # header band
HEAD_Y1 = 104
LEFT_X0, LEFT_X1 = INNER_L, 408        # left column
RIGHT_X0, RIGHT_X1 = 424, INNER_R      # right column (rows)
BODY_Y0 = 120
BOTTOM_Y0 = 800                        # bottom "latest / next reward" bar
BOTTOM_Y1 = TT_H - CARD_M - 12         # 872
BODY_Y1 = BOTTOM_Y0 - 12               # columns bottom

ROWS_PER_PAGE = 10

# Filter tabs (code, label). "all" first, then rarities ascending.
TT_TABS = [("all", "ALL")] + [(r, RARITY_META[r]["label"].upper()) for r in RARITY_ORDER]


COL_DESC = (224, 222, 228)               # title description text (white, bold)
COL_FC = (88, 204, 108)                  # the word "FC" — green (a full combo)
COL_PASS = (240, 120, 70)                # the word "Pass" — orange-red (a clear)
COL_INK_DARK = (24, 18, 12)              # text on light pills
COL_INK_LIGHT = (250, 248, 252)          # text on dark pills

# Tokens inside a description that render specially: star-rating ("6.5*+") as a
# lazer-spectrum pill, mod clusters ("HDDT") as mod-icon pills, grade letters
# ("SS"/"S") as coloured text. Everything else is plain white.
_DESC_RE = re.compile(
    r"(?P<sr>\d+(?:\.\d+)?\*\+?)"
    r"|(?P<mod>(?<![A-Za-z])(?:HD|HR|DT|NC|FL|EZ|HT|SO|NF|SD|PF)+(?![A-Za-z]))"
    r"|(?P<fc>(?<![A-Za-z])FC(?![A-Za-z]))"
    r"|(?P<pass>(?<![A-Za-z])Pass(?![A-Za-z]))"
    r"|(?P<grade>(?<![A-Za-z])(?:SS|S|A|B|C|D)(?![A-Za-z]))"
)


def _lum(c) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _ink_for(fill) -> tuple:
    """Contrast ink (dark on light fills, light on dark) for pills."""
    return COL_INK_DARK if _lum(fill) > 140 else COL_INK_LIGHT


def _tokenize_desc(text: str):
    """Split a description into (segment, kind) runs: 'sr', 'mod', 'grade', 'text'."""
    out, i = [], 0
    for m in _DESC_RE.finditer(text):
        if m.start() > i:
            out.append((text[i:m.start()], "text"))
        out.append((m.group(), m.lastgroup))
        i = m.end()
    if i < len(text):
        out.append((text[i:], "text"))
    return out


def _fmt_dt(dt) -> str:
    """datetime / ISO string → DD.MM.YYYY, em-dash when missing."""
    if not dt:
        return "—"
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y")
    try:
        d = str(dt).split("T")[0]
        y, m, day = d.split("-")[:3]
        return f"{day}.{m}.{y}"
    except Exception:
        return "—"


def _dim(color, k=0.34):
    """Desaturate a rarity colour toward the panel tone (locked emblems)."""
    base = (44, 38, 44)
    return tuple(int(color[i] * k + base[i] * (1 - k)) for i in range(3))


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class TitlesCardMixin:
    """TITLES COLLECTION dashboard — one wide card, red 1984 theme.

    Layout follows the titlescollection mockup: a profile/stats column on the
    left, a paged list of title rows on the right, and a latest/next-reward bar
    along the bottom. Rarity filtering and pagination are driven by Telegram
    inline buttons; the card draws the *current* filter/page state.
    """

    # ── Fonts (lazy, cached; every slot gets a Cyrillic fallback) ──

    def _tt_fonts(self) -> dict:
        cache = getattr(self, "_tt_font_cache", None)
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
            "h_title":   mk(b, 38, self.font_big),
            "h_sub":     mk(r, 16, self.font_small),
            "tab":       mk(s, 15, self.font_label),
            "name":      mk(b, 30, self.font_big),
            "handle":    mk(r, 18, self.font_subtitle),
            "sec":       mk(s, 18, self.font_stat_label),
            "big_num":   mk(b, 44, self.font_big),
            "big_den":   mk(s, 24, self.font_subtitle),
            "pct":       mk(b, 21, self.font_label),
            "stat_lbl":  mk(r, 17, self.font_label),
            "stat_val":  mk(b, 19, self.font_row),
            "rare_name": mk(b, 20, self.font_row),
            "rare_sub":  mk(r, 13, self.font_small),
            "row_name":  mk(b, 21, self.font_row),
            "row_desc":  mk(s, 15, self.font_label),
            "pill_sr":   mk(b, 13, self.font_stat_label),
            "badge":     mk(b, 12, self.font_stat_label),
            "st_lbl":    mk(r, 13, self.font_small),
            "st_val":    mk(b, 16, self.font_label),
            "emb_q":     mk(b, 26, self.font_grade),
            "bot_lbl":   mk(s, 13, self.font_stat_label),
            "bot_val":   mk(b, 22, self.font_row),
            "note":      mk(r, 14, self.font_small),
        }

        # Cyrillic fallback for every slot — the whole card is Russian.
        mpb = _find_font(MPLUS_BOLD)
        mpr = _find_font(MPLUS_REG) or mpb

        def mfb(path, size):
            try:
                return ImageFont.truetype(path, size) if path else None
            except Exception:
                return None

        fb_map = getattr(self, "_fb_map", None)
        if isinstance(fb_map, dict):
            sizes = {
                "h_title": (mpb, 38), "h_sub": (mpr, 16), "tab": (mpr, 15),
                "name": (mpb, 30), "handle": (mpr, 18), "sec": (mpr, 18),
                "big_num": (mpb, 44), "big_den": (mpr, 24), "pct": (mpb, 21),
                "stat_lbl": (mpr, 17), "stat_val": (mpb, 19),
                "rare_name": (mpb, 20), "rare_sub": (mpr, 13),
                "row_name": (mpb, 21), "row_desc": (mpb, 15), "pill_sr": (mpb, 13), "badge": (mpb, 12),
                "st_lbl": (mpr, 13), "st_val": (mpb, 16), "emb_q": (mpb, 26),
                "bot_lbl": (mpr, 13), "bot_val": (mpb, 22), "note": (mpr, 14),
            }
            for key, (path, size) in sizes.items():
                fb_map[id(f[key])] = mfb(path, size)

        self._tt_font_cache = f
        return f

    # ── Public entrypoints ──

    def generate_titles_card(self, data: Dict, avatar: Optional[Image.Image] = None) -> BytesIO:
        W, H = TT_W, TT_H
        img, draw = self._create_canvas(W, H)
        draw.rectangle([(0, 0), (W, H)], fill=COL_BG)
        self._pf_panel(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M),
                       radius=24, fill=COL_CARD, border=COL_CARD_BORDER)
        fonts = self._tt_fonts()

        self._tt_header(img, data, fonts)
        self._tt_left(img, data, avatar, fonts)
        self._tt_rows(img, data, fonts)
        self._tt_bottom(img, data, fonts)
        return self._save(img)

    async def generate_titles_card_async(self, data: Dict) -> BytesIO:
        avatar_url = data.get("avatar_url")
        tasks = [download_image(avatar_url) if avatar_url else _none_coro()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        avatar = results[0] if results and not isinstance(results[0], Exception) else None
        return await asyncio.to_thread(self.generate_titles_card, data, avatar)

    # ── Header ──

    def _tt_header(self, img, data, fonts):
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (INNER_L, HEAD_Y0 + 18), "TITLES COLLECTION", fonts["h_title"], COL_WHITE)
        self._draw_text(draw, (INNER_L, HEAD_Y0 + 62), "Show off your achievements", fonts["h_sub"], COL_MUTED)

        # Filter tabs, right-aligned. Active tab filled with its rarity colour
        # (red for "all"); the rest are faint outlines. These mirror the inline
        # buttons under the photo — the image shows the current selection.
        active = data.get("filter", "all")
        pad_x, gap, th = 12, 8, 34
        ty = HEAD_Y0 + 30
        widths = [(code, lbl, self._text_size(draw, lbl, fonts["tab"])[0] + pad_x * 2) for code, lbl in TT_TABS]
        total_w = sum(w for _, _, w in widths) + gap * (len(widths) - 1)
        tx = INNER_R - total_w
        for code, lbl, w in widths:
            col = COL_RED if code == "all" else RARITY_META[code]["color"]
            box = (tx, ty, tx + w, ty + th)
            # Every tab carries its category's rarity colour; the active one fills.
            if code == active:
                self._aa_rounded_fill(img, box, radius=th // 2, fill=_mix(col, COL_CARD, 0.30))
                self._aa_rounded_outline(img, box, radius=th // 2, outline=col, width=1)
                tcol = COL_WHITE
            else:
                self._aa_rounded_outline(img, box, radius=th // 2, outline=_mix(col, COL_CARD, 0.55), width=1)
                tcol = _mix(col, COL_MUTED, 0.35)
            draw = ImageDraw.Draw(img)
            self._text_center(draw, tx + w // 2, self._tt_cy(lbl, fonts["tab"], ty + th // 2),
                              lbl, fonts["tab"], tcol)
            tx += w + gap

    # ── Left column ──

    def _tt_left(self, img, data, avatar, fonts):
        self._pf_panel(img, (LEFT_X0, BODY_Y0, LEFT_X1, BODY_Y1), radius=16)
        draw = ImageDraw.Draw(img)
        cx0 = LEFT_X0 + 22
        cx1 = LEFT_X1 - 22
        y = BODY_Y0 + 24

        # Profile mini — circular avatar with red ring, name, handle, flag.
        d = 86
        ax, ay = cx0, y
        glow = Image.new("RGBA", (d + 60, d + 60), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((30 - 10, 30 - 10, 30 + d + 10, 30 + d + 10), fill=(228, 72, 72, 130))
        glow = glow.filter(ImageFilter.GaussianBlur(12))
        img.paste(glow, (ax - 30, ay - 30), glow)
        if avatar:
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            cmask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(cmask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (ax, ay), cmask)
        else:
            self._aa_ellipse_fill(img, (ax, ay, ax + d, ay + d), fill=(52, 40, 42))
        self._aa_ellipse_outline(img, (ax, ay, ax + d, ay + d), outline=(228, 76, 76), width=4)
        draw = ImageDraw.Draw(img)

        tx = ax + d + 16
        name = str(data.get("username", "???"))
        self._draw_text(draw, (tx, ay + 6), name, fonts["name"], COL_WHITE)
        handle = data.get("handle")
        if handle:
            self._draw_text(draw, (tx, ay + 42), handle, fonts["handle"], (188, 150, 152))
        flag = load_flag(str(data.get("country", "") or ""), height=22)
        if flag:
            img.paste(flag, (tx, ay + 66), flag)
            draw = ImageDraw.Draw(img)

        # TITLES UNLOCKED — big count + progress bar.
        y = ay + d + 30
        s = data.get("summary", {}) or {}
        unlocked = s.get("unlocked", 0)
        total = s.get("total", 0)
        pct = s.get("overall_pct", 0.0)
        self._draw_text(draw, (cx0, y), "TITLES UNLOCKED", fonts["sec"], COL_RED)
        y += 28
        num = str(unlocked)
        self._draw_text(draw, (cx0, y), num, fonts["big_num"], COL_CORAL)
        nw, _ = self._text_size(draw, num, fonts["big_num"])
        self._draw_text(draw, (cx0 + nw + 8, y + 18), f"/ {total}", fonts["big_den"], COL_MUTED)
        self._text_right(draw, cx1, y + 21, f"{int(pct)}%", fonts["pct"], COL_CORAL)
        y += 58
        bar_h = 10
        self._aa_rounded_fill(img, (cx0, y, cx1, y + bar_h), radius=5, fill=COL_TRACK)
        inner = int((cx1 - cx0) * max(0, min(100, pct)) / 100)
        if inner > 6:
            self._pf_hgrad(img, cx0, y, inner, bar_h, (200, 52, 52), (240, 124, 96), radius=5)
        draw = ImageDraw.Draw(img)

        # RAREST TITLE — hardest-tier unlocked title in a small framed sub-card.
        y += 34
        self._draw_text(draw, (cx0, y), "RAREST TITLE", fonts["sec"], COL_RED)
        y += 26
        rarest = s.get("rarest")
        rh = 64
        self._pf_panel(img, (cx0, y, cx1, y + rh), radius=12,
                       fill=(34, 27, 32), border=COL_PANEL_BORDER)
        if rarest:
            self._tt_emblem(img, cx0 + 12, y + (rh - 44) // 2, 44, rarest["color"], unlocked=True, secret=False)
            draw = ImageDraw.Draw(img)
            ex = cx0 + 12 + 44 + 14
            self._draw_text(draw, (ex, y + 16), rarest["name"], fonts["rare_name"], COL_WHITE)
            rp = data.get("rarest_global_pct")
            sub = f"Owned by {rp}% of players" if rp is not None else rarest["rarity_label"]
            self._draw_text(draw, (ex, y + 38), sub, fonts["rare_sub"], COL_MUTED)
        else:
            self._text_center(draw, (cx0 + cx1) // 2, y + rh // 2 - 8, "None yet", fonts["rare_sub"], COL_MUTED)

        # STATISTICS — per-rarity counts.
        y += rh + 26
        self._draw_text(draw, (cx0, y), "STATISTICS", fonts["sec"], COL_RED)
        y += 30
        by = s.get("by_rarity", {}) or {}
        rows = [("All titles", COL_CORAL, unlocked, total)]
        for r in RARITY_ORDER:
            b = by.get(r, {"unlocked": 0, "total": 0})
            rows.append((RARITY_META[r]["label"], RARITY_META[r]["color"], b["unlocked"], b["total"]))
        sw = 13
        # Spread the eight rows evenly across the column's remaining height.
        step = max(26.0, ((BODY_Y1 - 14) - y) / len(rows))
        for i, (label, col, u, t) in enumerate(rows):
            yc = int(y + step * i + step / 2)
            self._aa_rounded_fill(img, (cx0, yc - sw // 2, cx0 + sw, yc - sw // 2 + sw), radius=3, fill=col)
            val = f"{u} / {t}"
            self._draw_text(draw, (cx0 + 22, self._tt_cy(label, fonts["stat_lbl"], yc)), label, fonts["stat_lbl"], (208, 206, 214))
            self._text_right(draw, cx1, self._tt_cy(val, fonts["stat_val"], yc), val, fonts["stat_val"], COL_WHITE)

    def _tt_cy(self, text, font, yc):
        """Top-y that vertically centres `text`'s ink box on the line `yc`."""
        try:
            _, a, _, b = font.getbbox(text)
        except Exception:
            a, b = 0, getattr(font, "size", 16)
        return int(yc - (a + b) / 2)

    def _tt_desc(self, img, x, dcy, text, fonts, *, dim=False):
        """Draw a title description as a single line centred on `dcy`: star-rating
        tokens become lazer pills, mod clusters become mod-icon discs, grades are
        coloured text, the rest plain white. Returns the end x."""
        draw = ImageDraw.Draw(img)
        font = fonts["row_desc"]
        for seg, kind in _tokenize_desc(text):
            if kind == "sr":
                x = self._tt_sr_pill(img, x, dcy, seg, fonts, dim=dim) + 3
                draw = ImageDraw.Draw(img)
            elif kind == "mod":
                for m in (seg[i:i + 2] for i in range(0, len(seg), 2)):
                    x = self._tt_mod_pill(img, x, dcy, m, dim=dim) + 3
                draw = ImageDraw.Draw(img)
            else:
                if kind == "grade":
                    col = _grade_color(seg)
                elif kind == "fc":
                    col = COL_FC
                elif kind == "pass":
                    col = COL_PASS
                else:
                    col = COL_DESC
                if dim:
                    col = _mix(col, (96, 92, 98), 0.5)
                x = self._draw_text(draw, (x, self._tt_cy(seg, font, dcy)), seg, font, col)
        return x

    def _tt_sr_pill(self, img, x, dcy, token, fonts, *, dim=False):
        """Lazer-style star-rating pill, contrast-aware: fill is the SR-spectrum
        colour; star (left) + value + optional "+" use dark ink on light fills,
        light ink on dark. Dark fills also get a faint outline for edge definition."""
        body = token.rstrip("+")
        plus = "+" if token.endswith("+") else ""
        try:
            sr = float(body[:-1])
        except ValueError:
            return self._draw_text(ImageDraw.Draw(img), (x, self._tt_cy(token, fonts["row_desc"], dcy)),
                                   token, fonts["row_desc"], COL_DESC)
        fill = _sr_color(sr)
        ink = _ink_for(fill)
        outline = _mix(fill, COL_WHITE, 0.40) if _lum(fill) < 95 else None
        if dim:
            fill = _mix(fill, COL_PANEL, 0.45)
            ink = _mix(ink, (120, 116, 120), 0.35)

        font = fonts["pill_sr"]
        label = f"{body[:-1]}{plus}"           # "6.5" or "6.5+"
        draw = ImageDraw.Draw(img)
        tw = self._text_size(draw, label, font)[0]
        star = load_icon("star", 13)
        sw = star.width if star else 0
        pad, gap, h = 8, 4, 20
        w = pad + (sw + gap if star else 0) + tw + pad
        y0 = int(dcy - h / 2)
        self._aa_rounded_fill(img, (x, y0, x + w, y0 + h), radius=h // 2, fill=fill)
        if outline:
            self._aa_rounded_outline(img, (x, y0, x + w, y0 + h), radius=h // 2, outline=outline, width=1)
        draw = ImageDraw.Draw(img)
        if star:
            tinted = Image.new("RGBA", star.size, tuple(ink) + (255,))
            tinted.putalpha(star.split()[3])
            img.paste(tinted, (x + pad, int(dcy - star.height / 2)), tinted)
        self._draw_text(draw, (x + pad + (sw + gap if star else 0), self._tt_cy(label, font, dcy)), label, font, ink)
        return x + w

    def _tt_mod_pill(self, img, x, dcy, mod, *, dim=False):
        """A plain rounded mod pill: the mod's colour fill with its glyph inside,
        glyph inverted (dark) on light fills so it stays readable. Returns end x."""
        col = MOD_COLORS.get(mod, (110, 110, 130))
        ink = _ink_for(col)
        if dim:
            col = _mix(col, COL_PANEL, 0.45)
            ink = _mix(ink, (120, 116, 120), 0.35)
        h, gly = 20, 18
        w = gly + 10
        y0 = int(dcy - h / 2)
        self._aa_rounded_fill(img, (x, y0, x + w, y0 + h), radius=6, fill=col)
        glyph = load_mod_icon(mod, size=gly)
        if glyph:
            tinted = Image.new("RGBA", glyph.size, tuple(ink) + (255,))
            tinted.putalpha(glyph.split()[3])
            img.paste(tinted, (x + (w - glyph.width) // 2, int(dcy - glyph.height / 2)), tinted)
        else:
            draw = ImageDraw.Draw(img)
            f = self.font_stat_label
            self._text_center(draw, x + w // 2, self._tt_cy(mod, f, dcy), mod, f, ink)
        return x + w

    # ── Right column: paged title rows ──

    def _tt_rows(self, img, data, fonts):
        rows = data.get("rows", []) or []
        x0, x1 = RIGHT_X0, RIGHT_X1
        avail = BODY_Y1 - BODY_Y0
        rh = avail / ROWS_PER_PAGE
        for i in range(min(ROWS_PER_PAGE, len(rows))):
            ry = int(BODY_Y0 + i * rh)
            self._tt_row(img, x0, ry, x1 - x0, int(rh) - 8, rows[i], fonts)

    def _tt_row(self, img, x, y, w, h, t, fonts):
        unlocked = t["unlocked"]
        color = t["color"]
        # Row plate — unlocked high tiers (legendary+) get a colour-tinted plate
        # and accent; everything else a faint neutral panel.
        high = unlocked and RARITY_ORDER.index(t["rarity"]) >= RARITY_ORDER.index("legendary")
        fill = _mix(COL_PANEL, color, 0.16) if high else (COL_PANEL if unlocked else (26, 22, 27))
        border = color if high else COL_PANEL_BORDER
        self._pf_panel(img, (x, y, x + w, y + h), radius=12, fill=fill, border=border)
        draw = ImageDraw.Draw(img)

        # Emblem.
        sz = h - 16
        ex = x + 10
        ey = y + (h - sz) // 2
        self._tt_emblem(img, ex, ey, sz, color, unlocked=unlocked, secret=t["secret"], fonts=fonts)
        draw = ImageDraw.Draw(img)

        # Name + description (secret locked titles stay masked).
        tx = ex + sz + 16
        masked = t["secret"] and not unlocked
        name = "Hidden Title" if masked else t["name"]
        desc = "Surfaces on its own, in time" if masked else t["description"]
        ncol = COL_WHITE if unlocked else (150, 142, 150)
        mid = y + h // 2
        self._draw_text(draw, (tx, mid - 24), name, fonts["row_name"], ncol)
        if masked:
            self._draw_text(draw, (tx, self._tt_cy(desc, fonts["row_desc"], mid + 11)), desc, fonts["row_desc"], COL_MUTED)
        else:
            self._tt_desc(img, tx, mid + 11, desc, fonts, dim=not unlocked)

        # Rarity badge pill (right-of-centre).
        label = ("SECRET" if masked else t["rarity_label"]).upper()
        bw = self._text_size(draw, label, fonts["badge"])[0] + 22
        bx1 = x + w - 168
        bx0 = bx1 - bw
        bh = 24
        by = mid - bh // 2
        self._aa_rounded_outline(img, (bx0, by, bx1, by + bh), radius=bh // 2, outline=color, width=1)
        draw = ImageDraw.Draw(img)
        self._text_center(draw, (bx0 + bx1) // 2, self._tt_cy(label, fonts["badge"], by + bh // 2),
                          label, fonts["badge"], color if unlocked else _dim(color, 0.6))

        # Status column (far right): получено+дата, прогресс N/M, or lock.
        sxr = x + w - 16
        if unlocked:
            self._text_right(draw, sxr, mid - 18, "Unlocked", fonts["st_lbl"], COL_MUTED)
            self._text_right(draw, sxr, mid + 1, _fmt_dt(t.get("unlocked_at")), fonts["st_val"], COL_WHITE)
        elif t["target"] > 1:
            self._text_right(draw, sxr, mid - 18, "Progress", fonts["st_lbl"], COL_MUTED)
            self._text_right(draw, sxr, mid + 1, f"{int(t['current'])} / {t['target']}", fonts["st_val"], (200, 196, 206))
        else:
            self._text_right(draw, sxr, mid - 8, "Locked", fonts["st_lbl"], COL_MUTED)

    def _tt_emblem(self, img, x, y, sz, color, *, unlocked, secret, fonts=None):
        """Rarity gem tile — vertical gradient of the rarity colour, rounded,
        with a white star. Locked tiles are desaturated; locked secrets show a
        question mark instead of the star."""
        top = color if unlocked else _dim(color)
        bot = _mix(top, (0, 0, 0), 0.45)
        tile = Image.new("RGB", (sz, sz), top)
        td = ImageDraw.Draw(tile)
        for yy in range(sz):
            k = yy / max(1, sz - 1)
            td.line([(0, yy), (sz, yy)], fill=_mix(top, bot, k))
        if unlocked:
            glow = Image.new("RGBA", (sz + 24, sz + 24), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle((12, 12, 12 + sz, 12 + sz), radius=12,
                                                   fill=(*color, 120))
            glow = glow.filter(ImageFilter.GaussianBlur(8))
            img.paste(glow, (x - 12, y - 12), glow)
        mask = self._rounded_mask((sz, sz), radius=12)
        img.paste(tile, (x, y), mask)
        self._aa_rounded_outline(img, (x, y, x + sz, y + sz), radius=12,
                                 outline=_mix(color, COL_WHITE, 0.25) if unlocked else COL_PANEL_BORDER, width=1)

        if secret and not unlocked and fonts:
            d = ImageDraw.Draw(img)
            self._text_center(d, x + sz // 2, self._tt_cy("?", fonts["emb_q"], y + sz // 2),
                              "?", fonts["emb_q"], (150, 142, 150))
            return
        star = load_icon("star", int(sz * 0.5))
        if star:
            tint = (255, 255, 255, 255) if unlocked else (170, 162, 170, 255)
            white = Image.new("RGBA", star.size, tint)
            white.putalpha(star.split()[3])
            img.paste(white, (x + (sz - star.width) // 2, y + (sz - star.height) // 2), white)

    # ── Bottom bar: recently unlocked + next reward ──

    def _tt_bottom(self, img, data, fonts):
        self._pf_panel(img, (INNER_L, BOTTOM_Y0, INNER_R, BOTTOM_Y1), radius=14)
        draw = ImageDraw.Draw(img)
        s = data.get("summary", {}) or {}
        cy = (BOTTOM_Y0 + BOTTOM_Y1) // 2

        # Two divider lines split the bar into three zones.
        zx0 = INNER_L + 360
        zx1 = INNER_L + 720
        for zx in (zx0, zx1):
            draw.line([(zx, BOTTOM_Y0 + 16), (zx, BOTTOM_Y1 - 16)], fill=COL_DIVIDER, width=1)

        # Emblem top-y and ink-centre line shared by zones 1 & 2.
        emb = 28
        emb_y = cy - 3
        emb_c = emb_y + emb // 2

        # Zone 1 — latest unlocked. Name + date ride the emblem's centre line.
        latest = s.get("latest")
        x = INNER_L + 22
        self._draw_text(draw, (x, BOTTOM_Y0 + 16), "RECENTLY UNLOCKED", fonts["bot_lbl"], COL_MUTED)
        if latest:
            self._tt_emblem(img, x, emb_y, emb, latest["color"], unlocked=True, secret=False)
            draw = ImageDraw.Draw(img)
            nx = x + emb + 10
            self._draw_text(draw, (nx, self._tt_cy(latest["name"], fonts["bot_val"], emb_c)),
                            latest["name"], fonts["bot_val"], COL_WHITE)
            nw, _ = self._text_size(draw, latest["name"], fonts["bot_val"])
            date = _fmt_dt(latest.get("unlocked_at"))
            self._draw_text(draw, (nx + nw + 12, self._tt_cy(date, fonts["st_lbl"], emb_c)),
                            date, fonts["st_lbl"], COL_MUTED)
        else:
            self._draw_text(draw, (x, self._tt_cy("—", fonts["bot_val"], emb_c)), "—", fonts["bot_val"], COL_MUTED)

        # Zone 2 — next reward (title closest to unlocking). Emblem + name.
        nxt = s.get("next_up")
        x = zx0 + 22
        self._draw_text(draw, (x, BOTTOM_Y0 + 16), "NEXT REWARD", fonts["bot_lbl"], COL_MUTED)
        if nxt:
            nmask = nxt["secret"]
            self._tt_emblem(img, x, emb_y, emb, nxt["color"], unlocked=not nmask, secret=nmask, fonts=fonts)
            draw = ImageDraw.Draw(img)
            nm = "Hidden Title" if nmask else nxt["name"]
            self._draw_text(draw, (x + emb + 10, self._tt_cy(nm, fonts["bot_val"], emb_c)),
                            nm, fonts["bot_val"], _mix(nxt["color"], COL_WHITE, 0.2))
        else:
            self._draw_text(draw, (x, self._tt_cy("All unlocked!", fonts["bot_val"], emb_c)),
                            "All unlocked!", fonts["bot_val"], COL_CORAL)

        # Zone 3 — progress to that next reward.
        x = zx1 + 22
        rx = INNER_R - 22
        self._draw_text(draw, (x, BOTTOM_Y0 + 16), "PROGRESS TO UNLOCK", fonts["bot_lbl"], COL_MUTED)
        if nxt:
            prog = nxt.get("progress_pct", 0.0)
            cur, tgt = int(nxt["current"]), nxt["target"]
            txt = f"{_sp(cur)} / {_sp(tgt)}" if tgt > 1 else f"{int(prog)}%"
            self._draw_text(draw, (x, self._tt_cy(txt, fonts["st_val"], emb_c)), txt, fonts["st_val"], COL_WHITE)
            bar_y = cy + 20
            self._aa_rounded_fill(img, (x, bar_y, rx, bar_y + 8), radius=4, fill=COL_TRACK)
            inner = int((rx - x) * max(0, min(100, prog)) / 100)
            if inner > 4:
                self._pf_hgrad(img, x, bar_y, inner, 8, (200, 52, 52), (240, 124, 96), radius=4)
            draw = ImageDraw.Draw(img)


def build_titles_card_data(
    username: str,
    handle: Optional[str],
    country: Optional[str],
    progress_list: List[Dict],
    summary: Dict,
    *,
    filter: str = "all",
    page: int = 0,
    avatar_url: Optional[str] = None,
    rarest_global_pct: Optional[float] = None,
) -> Dict:
    """Assemble the dict consumed by generate_titles_card from a refresh result.

    Applies the active rarity filter and pagination to the row list; the summary
    (left column + bottom bar) always reflects the full collection.
    """
    items = progress_list if filter == "all" else [p for p in progress_list if p["rarity"] == filter]
    total_pages = max(1, (len(items) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = items[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
    return {
        "username": username,
        "handle": handle,
        "country": country,
        "avatar_url": avatar_url,
        "summary": summary,
        "rarest_global_pct": rarest_global_pct,
        "filter": filter,
        "page": page,
        "total_pages": total_pages,
        "rows": rows,
    }
