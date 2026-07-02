import asyncio
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from services.image.constants import (
    TORUS_BOLD,
    TORUS_SEMI,
    TORUS_REG,
    MPLUS_BOLD,
    MPLUS_REG,
    PROXIMA_BOLD,
    PROXIMA_SEMI,
    PROXIMA_REG,
    TOP_COLORS,
)
from services.image.utils import (
    load_flag,
    load_icon,
    _find_font,
    _none_coro,
    download_image,
    cover_center_crop,
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
    COL_GREEN,
    COL_TRACK,
    _sp,
    _fmt_date,
    _grade_color,
    _grade_letter,
)
from services.image.render.titles import _mix

# ── Geometry — single wide dashboard, same tone as titles.py/profile.py ──
TP_W = 1280
TP_H = 900
INNER_L = CARD_M + 28                  # 44
INNER_R = TP_W - CARD_M - 28           # 1236

HEAD_Y0 = CARD_M
HEAD_Y1 = 100
STRIP_Y0 = 116
STRIP_Y1 = 232
BODY_Y0 = 248
BOTTOM_Y0 = 800
BOTTOM_Y1 = TP_H - CARD_M - 12          # 872
BODY_Y1 = BOTTOM_Y0 - 12                # 788

ROWS_PER_PAGE = 5

COL_UP = COL_GREEN
COL_DOWN = (232, 96, 96)
COL_NEW = (171, 133, 235)               # violet — distinct from the red delta colours


def _fmt_delta_time(dt, lang: str) -> str:
    """A pp-change timestamp -> a short relative phrase ("2 days ago"/"2 дня назад")."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    ru = (lang or "en").lower() == "ru"
    days = int(secs // 86400)
    if days <= 0:
        return "сегодня" if ru else "today"
    if days == 1:
        return "вчера" if ru else "yesterday"
    if ru:
        return f"{days} дн. назад"
    return f"{days}d ago"


_TP_STRINGS = {
    "en": {
        "header": "TOP PLAYS", "subheader": "Your best results by PP",
        "mode_label": "Standard",
        "plays_label": "plays", "weighted_pp_label": "weighted pp", "avg_pp_label": "average pp",
        "weighted": "weighted {pct:.0f}%",
        "new_badge": "NEW",
        "footer": "PP is calculated with weighting applied  •  Max weight: 100%",
        "no_scores": "No ranked plays yet",
    },
    "ru": {
        "header": "ТОП-ПЛЕИ", "subheader": "Ваши лучшие результаты по PP",
        "mode_label": "Standard",
        "plays_label": "плея", "weighted_pp_label": "weighted pp", "avg_pp_label": "средний pp",
        "weighted": "weighted {pct:.0f}%",
        "new_badge": "НОВОЕ",
        "footer": "PP рассчитывается с учётом весов  •  Максимальный вес: 100%",
        "no_scores": "Пока нет ранкнутых плеев",
    },
}


def _tp_lang(data) -> dict:
    lang = (data.get("lang") or "en").lower()
    return _TP_STRINGS.get(lang, _TP_STRINGS["en"])


class TopPlaysCardMixin:
    """TOP PLAYS dashboard — ranked list of the player's best scores by pp,
    weighted the same way osu!'s own profile does (rank N counts 0.95**(N-1)).
    Layout mirrors titles.py (paged rows, same red 1984 theme, same font-cache
    + Cyrillic-fallback pattern)."""

    # ── Fonts (lazy, cached; every slot gets a Cyrillic fallback) ──

    def _tp_fonts(self) -> dict:
        cache = getattr(self, "_tp_font_cache", None)
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
            "h_title": mk(b, 38, self.font_big),
            "h_sub": mk(r, 16, self.font_small),
            "meta": mk(r, 15, self.font_small),
            "mode_pill": mk(s, 15, self.font_label),
            "name": mk(b, 28, self.font_big),
            "handle": mk(r, 16, self.font_subtitle),
            "strip_lbl": mk(r, 15, self.font_label),
            "strip_val": mk(b, 30, self.font_big),
            "row_title": mk(b, 21, self.font_row),
            "row_sub": mk(r, 15, self.font_label),
            "row_meta": mk(s, 14, self.font_stat_label),
            "pill_sr": mk(b, 13, self.font_stat_label),   # consumed by self._tt_sr_pill
            "grade": mk(b, 22, self.font_row),
            "badge_num": mk(b, 18, self.font_row),
            "pp_big": mk(b, 30, self.font_big),
            "pp_lbl": mk(s, 13, self.font_stat_label),
            "delta_val": mk(b, 15, self.font_label),
            "delta_time": mk(r, 12, self.font_small),
            "footer": mk(r, 14, self.font_small),
        }

        mpb = _find_font(MPLUS_BOLD)
        mpr = _find_font(MPLUS_REG) or mpb

        def mfb(path, size):
            try:
                return ImageFont.truetype(path, size) if path else None
            except Exception:
                return None

        fb_map = getattr(self, "_fb_map", None)
        pxb = _find_font(PROXIMA_BOLD)
        pxs = _find_font(PROXIMA_SEMI) or pxb
        pxr = _find_font(PROXIMA_REG) or pxb
        fb_cy_map = getattr(self, "_fb_cyrillic_map", None)

        sizes = {
            "h_title": (mpb, pxb, 38), "h_sub": (mpr, pxr, 16), "meta": (mpr, pxr, 15),
            "mode_pill": (mpr, pxs, 15), "name": (mpb, pxb, 28), "handle": (mpr, pxr, 16),
            "strip_lbl": (mpr, pxr, 15), "strip_val": (mpb, pxb, 30),
            "row_title": (mpb, pxb, 21), "row_sub": (mpr, pxr, 15), "row_meta": (mpb, pxs, 14),
            "pill_sr": (mpb, pxb, 13),
            "grade": (mpb, pxb, 22), "badge_num": (mpb, pxb, 18),
            "pp_big": (mpb, pxb, 30), "pp_lbl": (mpb, pxs, 13),
            "delta_val": (mpb, pxb, 15), "delta_time": (mpr, pxr, 12), "footer": (mpr, pxr, 14),
        }
        if isinstance(fb_map, dict):
            for key, (mp_path, _, size) in sizes.items():
                fb_map[id(f[key])] = mfb(mp_path, size)
        if isinstance(fb_cy_map, dict):
            for key, (_, px_path, size) in sizes.items():
                fb_cy_map[id(f[key])] = mfb(px_path, size)

        self._tp_font_cache = f
        return f

    # ── Public entrypoints ──

    def generate_top_plays_card(self, data: Dict, avatar: Optional[Image.Image] = None,
                                 covers: Optional[List[Optional[Image.Image]]] = None) -> BytesIO:
        W, H = TP_W, TP_H
        img, draw = self._create_canvas(W, H)
        draw.rectangle([(0, 0), (W, H)], fill=COL_BG)
        self._pf_panel(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M),
                       radius=24, fill=COL_CARD, border=COL_CARD_BORDER)
        fonts = self._tp_fonts()

        self._tp_header(img, data, fonts)
        self._tp_strip(img, data, avatar, fonts)
        self._tp_rows(img, data, fonts, covers or [])
        self._tp_footer(img, data, fonts)
        return self._save(img)

    async def generate_top_plays_card_async(self, data: Dict) -> BytesIO:
        avatar_url = data.get("avatar_url")
        rows = data.get("rows", []) or []
        cover_urls = [
            f"https://assets.ppy.sh/beatmaps/{r['beatmapset_id']}/covers/cover.jpg" if r.get("beatmapset_id") else None
            for r in rows
        ]
        tasks = [download_image(avatar_url) if avatar_url else _none_coro()] + [
            download_image(u) if u else _none_coro() for u in cover_urls
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _ok(r):
            return r if not isinstance(r, Exception) and r is not None else None

        avatar = _ok(results[0])
        covers = [_ok(r) for r in results[1:]]
        return await asyncio.to_thread(self.generate_top_plays_card, data, avatar, covers)

    # ── Header ──

    def _tp_header(self, img, data, fonts):
        draw = ImageDraw.Draw(img)
        S = _tp_lang(data)
        title_x = INNER_L
        sparkle = load_icon("startpp", 26)
        if sparkle:
            tinted = Image.new("RGBA", sparkle.size, COL_RED + (255,))
            tinted.putalpha(sparkle.split()[3])
            img.paste(tinted, (INNER_L, HEAD_Y0 + 16), tinted)
            draw = ImageDraw.Draw(img)
            title_x = INNER_L + sparkle.width + 10
        self._draw_text(draw, (title_x, HEAD_Y0 + 14), S["header"], fonts["h_title"], COL_WHITE)
        self._draw_text(draw, (INNER_L, HEAD_Y0 + 56), S["subheader"], fonts["h_sub"], COL_MUTED)

        # Updated timestamp, top-right.
        updated = data.get("updated_at")
        ts = _fmt_datetime(updated)
        if ts:
            clock = load_icon("timer", 16)
            tw, _ = self._text_size(draw, ts, fonts["meta"])
            tx = INNER_R - tw
            if clock:
                tx -= clock.width + 6
                img.paste(clock, (tx, HEAD_Y0 + 14), clock)
                draw = ImageDraw.Draw(img)
                self._draw_text(draw, (tx + clock.width + 6, HEAD_Y0 + 12), ts, fonts["meta"], COL_MUTED)
            else:
                self._draw_text(draw, (tx, HEAD_Y0 + 12), ts, fonts["meta"], COL_MUTED)

        # Mode pill — decorative, osu!standard only for now.
        label = S["mode_label"]
        lw, lh = self._text_size(draw, label, fonts["mode_pill"])
        pad_x, ph = 14, 32
        pw = lw + pad_x * 2
        px0 = INNER_R - pw
        py0 = HEAD_Y0 + 40
        self._aa_rounded_outline(img, (px0, py0, px0 + pw, py0 + ph), radius=ph // 2,
                                 outline=COL_PANEL_BORDER, width=1)
        draw = ImageDraw.Draw(img)
        self._text_center(draw, px0 + pw // 2, py0 + ph // 2, label, fonts["mode_pill"], (200, 196, 206))

    # ── Player summary strip ──

    def _tp_strip(self, img, data, avatar, fonts):
        self._pf_panel(img, (INNER_L, STRIP_Y0, INNER_R, STRIP_Y1), radius=14)
        draw = ImageDraw.Draw(img)
        S = _tp_lang(data)

        # Avatar + name, left.
        d = 74
        ax, ay = INNER_L + 20, STRIP_Y0 + (STRIP_Y1 - STRIP_Y0 - d) // 2
        glow = Image.new("RGBA", (d + 50, d + 50), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((25 - 10, 25 - 10, 25 + d + 10, 25 + d + 10), fill=(228, 72, 72, 130))
        glow = glow.filter(ImageFilter.GaussianBlur(10))
        img.paste(glow, (ax - 25, ay - 25), glow)
        if avatar:
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            cmask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(cmask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (ax, ay), cmask)
        else:
            self._aa_ellipse_fill(img, (ax, ay, ax + d, ay + d), fill=(52, 40, 42))
        self._aa_ellipse_outline(img, (ax, ay, ax + d, ay + d), outline=(228, 76, 76), width=3)
        draw = ImageDraw.Draw(img)

        tx = ax + d + 18
        name = str(data.get("username", "???"))
        self._draw_text(draw, (tx, ay + 4), name, fonts["name"], COL_WHITE)
        handle = data.get("handle")
        yy = ay + 38
        if handle:
            self._draw_text(draw, (tx, yy), handle, fonts["handle"], (188, 150, 152))
        flag = load_flag(str(data.get("country", "") or ""), height=18)
        if flag:
            fx = tx + (self._text_size(draw, handle, fonts["handle"])[0] + 12 if handle else 0)
            img.paste(flag, (fx, yy + 1), flag)
            draw = ImageDraw.Draw(img)

        # Three stat cells, right.
        plays = data.get("play_count", 0) or 0
        total_wpp = data.get("total_weighted_pp", 0.0) or 0.0
        avg_pp = data.get("avg_pp", 0.0) or 0.0
        cells = [
            (str(plays), S["plays_label"]),
            (_sp(int(round(total_wpp))), S["weighted_pp_label"]),
            (f"{int(round(avg_pp))}pp", S["avg_pp_label"]),
        ]
        cell_w = 190
        cx = INNER_R - 24 - cell_w * len(cells)
        for val, lbl in cells:
            self._draw_text(draw, (cx, STRIP_Y0 + 24), val, fonts["strip_val"], COL_CORAL)
            self._draw_text(draw, (cx, STRIP_Y0 + 62), lbl, fonts["strip_lbl"], COL_MUTED)
            cx += cell_w

    # ── Paged rows ──

    def _tp_rows(self, img, data, fonts, covers: List[Optional[Image.Image]]):
        rows = data.get("rows", []) or []
        S = _tp_lang(data)
        lang = data.get("lang") or "en"
        draw = ImageDraw.Draw(img)
        if not rows:
            self._text_center(draw, (INNER_L + INNER_R) // 2, (BODY_Y0 + BODY_Y1) // 2,
                              S["no_scores"], fonts["row_title"], COL_MUTED)
            return
        avail = BODY_Y1 - BODY_Y0
        rh = avail / ROWS_PER_PAGE
        for i in range(min(ROWS_PER_PAGE, len(rows))):
            ry = int(BODY_Y0 + i * rh)
            cover = covers[i] if i < len(covers) else None
            self._tp_row(img, INNER_L, ry, INNER_R - INNER_L, int(rh) - 10, rows[i], fonts, S, cover, lang)

    def _tp_row(self, img, x, y, w, h, t, fonts, S, cover, lang):
        self._pf_panel(img, (x, y, x + w, y + h), radius=12, fill=COL_PANEL, border=COL_PANEL_BORDER)
        draw = ImageDraw.Draw(img)
        mid = y + h // 2
        pos = t["position"]
        podium = TOP_COLORS.get(pos, COL_RED)

        # Accent bar, left edge.
        draw.rectangle((x, y + 8, x + 5, y + h - 8), fill=podium)

        # Rank badge — a medal glyph for the podium (1/2/3), a plain outlined
        # circle with the number for everyone else.
        bd = 40
        bx, by = x + 18, mid - bd // 2
        medal_icon = {1: "thirstplace", 2: "secondplace", 3: "thirdplace"}.get(pos)
        medal = load_icon(medal_icon, 46) if medal_icon else None
        if medal:
            tinted = Image.new("RGBA", medal.size, podium + (255,))
            tinted.putalpha(medal.split()[3])
            img.paste(tinted, (bx + (bd - medal.width) // 2, mid - medal.height // 2), tinted)
        else:
            self._aa_ellipse_outline(img, (bx, by, bx + bd, by + bd), outline=podium, width=2)
            draw = ImageDraw.Draw(img)
            num = str(pos)
            self._text_center(draw, bx + bd // 2, self._tp_cy(num, fonts["badge_num"], mid), num, fonts["badge_num"], podium)

        # Cover thumbnail.
        cs = h - 20
        cx0 = bx + bd + 14
        cy0 = y + (h - cs) // 2
        if cover:
            try:
                crop = cover_center_crop(cover, cs, cs)
            except Exception:
                crop = Image.new("RGBA", (cs, cs), (46, 36, 38, 255))
        else:
            crop = Image.new("RGBA", (cs, cs), (44, 34, 36, 255))
        mask = self._rounded_mask((cs, cs), 10)
        img.paste(crop.convert("RGB"), (cx0, cy0), mask)
        self._aa_rounded_outline(img, (cx0, cy0, cx0 + cs, cy0 + cs), radius=10, outline=COL_PANEL_BORDER, width=1)
        draw = ImageDraw.Draw(img)

        # PP block width, computed early so title/artist know how much room
        # they have before it (long beatmap titles are common — must not run
        # into the pp value).
        delta = t.get("delta")
        delta_w = 118 if delta else 0
        pp_right = x + w - 20 - delta_w
        pp_bar_w = 130
        text_limit = pp_right - pp_bar_w - 24

        # Title / artist (ellipsized to fit), then a chip row (SR, mods,
        # accuracy, combo, grade).
        tx = cx0 + cs + 18
        max_w = text_limit - tx
        title = self._tp_ellipsize(draw, t.get("title") or "?", fonts["row_title"], max_w)
        self._draw_text(draw, (tx, y + 12), title, fonts["row_title"], COL_WHITE)
        artist = self._tp_ellipsize(draw, t.get("artist") or "", fonts["row_sub"], max_w)
        self._draw_text(draw, (tx, y + 40), artist, fonts["row_sub"], COL_MUTED)

        chip_y = y + h - 30
        cxp = self._tt_sr_pill(img, tx, chip_y, f"{t.get('star_rating', 0.0):.2f}*", fonts, dim=False) + 6
        draw = ImageDraw.Draw(img)
        for m in t.get("mods", []):
            cxp = self._tt_mod_pill(img, cxp, chip_y, m, dim=False) + 6
            draw = ImageDraw.Draw(img)
        acc_txt = f"{t.get('accuracy', 0.0):.2f}%"
        self._draw_text(draw, (cxp + 4, self._tp_cy(acc_txt, fonts["row_meta"], chip_y)), acc_txt, fonts["row_meta"], (208, 206, 214))
        cxp += self._text_size(draw, acc_txt, fonts["row_meta"])[0] + 20
        combo_txt = f"{_sp(t.get('max_combo', 0))}x"
        self._draw_text(draw, (cxp, self._tp_cy(combo_txt, fonts["row_meta"], chip_y)), combo_txt, fonts["row_meta"], (208, 206, 214))
        cxp += self._text_size(draw, combo_txt, fonts["row_meta"])[0] + 20
        grade = _grade_letter(t.get("rank", "F"))
        self._draw_text(draw, (cxp, self._tp_cy(grade, fonts["grade"], chip_y)), grade, fonts["grade"], _grade_color(t.get("rank", "F")))

        # PP block + delta badge, right-aligned.
        pp_txt = f"{int(round(t.get('pp', 0.0)))}pp"
        self._text_right(draw, pp_right, y + 12, pp_txt, fonts["pp_big"], COL_CORAL)
        wpct = S["weighted"].format(pct=t.get("weight_pct", 0.0))
        self._text_right(draw, pp_right, y + 46, wpct, fonts["pp_lbl"], COL_MUTED)
        bar_w = pp_bar_w
        bar_y = y + 64
        self._aa_rounded_fill(img, (pp_right - bar_w, bar_y, pp_right, bar_y + 6), radius=3, fill=COL_TRACK)
        inner = int(bar_w * max(0.0, min(100.0, t.get("weight_pct", 0.0))) / 100)
        if inner > 4:
            self._aa_rounded_fill(img, (pp_right - bar_w, bar_y, pp_right - bar_w + inner, bar_y + 6),
                                  radius=3, fill=podium)
        draw = ImageDraw.Draw(img)

        if delta:
            dxr = x + w - 16
            if delta.kind == "new":
                lbl = S["new_badge"]
                lw = self._text_size(draw, lbl, fonts["delta_val"])[0] + 20
                bx1 = dxr
                bx0 = bx1 - lw
                bh = 24
                byy = mid - bh // 2
                self._aa_rounded_fill(img, (bx0, byy, bx1, byy + bh), radius=bh // 2, fill=_mix(COL_NEW, COL_PANEL, 0.35))
                draw = ImageDraw.Draw(img)
                self._text_center(draw, (bx0 + bx1) // 2, byy + bh // 2, lbl, fonts["delta_val"], COL_NEW)
            else:
                amount = delta.amount
                up = amount >= 0
                col = COL_UP if up else COL_DOWN
                val_txt = f"{abs(int(round(amount)))}pp"
                val_y = mid - 20
                tw = self._text_size(draw, val_txt, fonts["delta_val"])[0]
                vx = dxr - tw
                arrow = load_icon("arrowup" if up else "arrowdown", 13)
                if arrow:
                    tinted = Image.new("RGBA", arrow.size, col + (255,))
                    tinted.putalpha(arrow.split()[3])
                    vx -= arrow.width + 4
                    img.paste(tinted, (vx, val_y + 3), tinted)
                    draw = ImageDraw.Draw(img)
                    vx += arrow.width + 4
                self._draw_text(draw, (vx, val_y), val_txt, fonts["delta_val"], col)
                time_txt = _fmt_delta_time(delta.at, lang)
                self._text_right(draw, dxr, mid + 3, time_txt, fonts["delta_time"], COL_MUTED)

    def _tp_cy(self, text, font, yc):
        """Top-y that vertically centres `text`'s ink box on the line `yc`."""
        try:
            _, a, _, b = font.getbbox(text)
        except Exception:
            a, b = 0, getattr(font, "size", 16)
        return int(yc - (a + b) / 2)

    def _tp_ellipsize(self, draw, text, font, max_w):
        """Shrink `text` character by character (with a trailing "…") until it
        fits `max_w`. Long beatmap titles/artists are common; the pp block to
        the right must never be run into."""
        if max_w <= 0 or self._text_size(draw, text, font)[0] <= max_w:
            return text
        disp = text
        while len(disp) > 1 and self._text_size(draw, disp + "…", font)[0] > max_w:
            disp = disp[:-1]
        return disp + "…"

    # ── Footer ──

    def _tp_footer(self, img, data, fonts):
        self._pf_panel(img, (INNER_L, BOTTOM_Y0, INNER_R, BOTTOM_Y1), radius=14)
        draw = ImageDraw.Draw(img)
        S = _tp_lang(data)
        cy = (BOTTOM_Y0 + BOTTOM_Y1) // 2
        self._text_center(draw, (INNER_L + INNER_R) // 2, self._tp_cy(S["footer"], fonts["footer"], cy),
                          S["footer"], fonts["footer"], COL_MUTED)


def _fmt_datetime(dt) -> str:
    """datetime / ISO string -> "DD.MM.YYYY HH:MM", empty string when missing."""
    if not dt:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y %H:%M")
    try:
        s = str(dt).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        return parsed.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return ""


def build_top_plays_card_data(
    username: str,
    handle: Optional[str],
    country: Optional[str],
    built_list: List[Dict],
    *,
    page: int = 0,
    avatar_url: Optional[str] = None,
    lang: str = "en",
) -> Dict:
    """Assemble the dict consumed by generate_top_plays_card from an already
    pp-sorted/weighted list (utils.best_scores.build_top_plays_list)."""
    from utils.best_scores import total_weighted_pp

    total_pages = max(1, (len(built_list) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = built_list[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
    play_count = len(built_list)
    total_wpp = total_weighted_pp(built_list)
    avg_pp = (sum(r["pp"] for r in built_list) / play_count) if play_count else 0.0
    return {
        "username": username,
        "handle": handle,
        "country": country,
        "avatar_url": avatar_url,
        "play_count": play_count,
        "total_weighted_pp": total_wpp,
        "avg_pp": avg_pp,
        "updated_at": datetime.now(timezone.utc),
        "lang": lang,
        "page": page,
        "total_pages": total_pages,
        "rows": rows,
    }
