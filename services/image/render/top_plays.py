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
    GRADE_COLORS,
)
from services.image.utils import (
    load_flag,
    load_icon,
    _find_font,
    cover_center_crop,
)
from services.image.render.profile import (
    CARD_M,
    COL_BG,
    COL_CARD,
    COL_CARD_BORDER,
    COL_PANEL,
    COL_PANEL_BORDER,
    COL_CORAL,
    COL_WHITE,
    COL_MUTED,
    _sp,
)

# ── Geometry — single wide dashboard, same tone as titles.py/profile.py.
# 2026-07-04 redesign: compact text-only rows (no cover art, no delta
# tracking, no per-row weighted-pp bar) per a user-supplied mockup
# (osu_top_plays_floating_panels.html) — much shorter than the old
# cover-art-driven layout, so the whole card shrank with it.
TP_W = 820                              # narrower than the other 1280px cards —
                                         # no cover art means the content doesn't
                                         # need nearly that much width
TP_H = 660
INNER_L = CARD_M + 28                  # 44
INNER_R = TP_W - CARD_M - 28           # 776

HEAD_Y0 = CARD_M
HEAD_Y1 = 76
STRIP_Y0 = 92
STRIP_Y1 = 176
BODY_Y0 = 192
BODY_Y1 = TP_H - CARD_M - 12            # 628 — no footer bar, rows use the full rest

ROWS_PER_PAGE = 5
ROW_CORNER_R = 12
GRADE_BADGE_R = 26                      # plain outlined circle, not the arc-completion ring


_TP_STRINGS = {
    "en": {
        "header": "TOP PLAYS",
        "no_scores": "No ranked plays yet",
    },
    "ru": {
        "header": "ЛУЧШИЕ РЕЗУЛЬТАТЫ",
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
            "h_title": mk(b, 24, self.font_row),
            "name": mk(b, 28, self.font_big),
            "handle": mk(r, 16, self.font_subtitle),
            "row_title": mk(b, 17, self.font_row),
            "row_artist": mk(r, 14, self.font_small),
            "version_pill": mk(b, 12, self.font_stat_label),
            "row_meta": mk(s, 14, self.font_stat_label),
            "sr_chip": mk(b, 14, self.font_label),         # consumed by self._draw_sr_pill
            "grade_badge": mk(b, 24, self.font_label),
            "pp_big": mk(b, 24, self.font_row),
            "pp_lbl": mk(s, 13, self.font_stat_label),
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
            "h_title": (mpb, pxb, 24),
            "name": (mpb, pxb, 28), "handle": (mpr, pxr, 16),
            "row_title": (mpb, pxb, 17), "row_artist": (mpr, pxr, 14),
            "version_pill": (mpb, pxs, 12), "row_meta": (mpb, pxs, 14),
            "sr_chip": (mpb, pxb, 14),
            "grade_badge": (mpb, pxb, 24),
            "pp_big": (mpb, pxb, 24), "pp_lbl": (mpb, pxs, 13),
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
                                 covers: Optional[List[Optional[Image.Image]]] = None,
                                 player_cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = TP_W, TP_H
        img, draw = self._create_canvas(W, H)
        draw.rectangle([(0, 0), (W, H)], fill=COL_BG)
        self._pf_panel(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M),
                       radius=24, fill=COL_CARD, border=COL_CARD_BORDER)
        fonts = self._tp_fonts()

        self._tp_header(img, data, fonts)
        self._tp_strip(img, data, avatar, player_cover, fonts)
        self._tp_rows(img, data, fonts, covers or [])
        return self._save(img)

    async def generate_top_plays_card_async(self, data: Dict) -> BytesIO:
        from services.image.utils import download_image, _none_coro
        avatar_url = data.get("avatar_url")
        cover_url = data.get("cover_url")
        rows = data.get("rows", []) or []
        cover_urls = [
            f"https://assets.ppy.sh/beatmaps/{r['beatmapset_id']}/covers/cover.jpg" if r.get("beatmapset_id") else None
            for r in rows
        ]
        tasks = [
            download_image(avatar_url) if avatar_url else _none_coro(),
            download_image(cover_url) if cover_url else _none_coro(),
        ] + [download_image(u) if u else _none_coro() for u in cover_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _ok(r):
            return r if not isinstance(r, Exception) and r is not None else None

        avatar = _ok(results[0])
        player_cover = _ok(results[1])
        covers = [_ok(r) for r in results[2:]]
        return await asyncio.to_thread(self.generate_top_plays_card, data, avatar, covers, player_cover)

    # ── Header — icon + title centred on the full width, a small label
    # pinned to the right (mockup: 3-column grid, empty / centred / right).

    def _tp_header(self, img, data, fonts):
        draw = ImageDraw.Draw(img)
        S = _tp_lang(data)
        title = S["header"]
        sparkle = load_icon("startpp", 20)
        title_w = self._text_size(draw, title, fonts["h_title"])[0]
        gap = (sparkle.width + 10) if sparkle else 0
        total_w = gap + title_w
        start_x = (TP_W - total_w) // 2
        head_y = HEAD_Y0 + 10
        cy = head_y + 18
        if sparkle:
            tinted = Image.new("RGBA", sparkle.size, (228, 76, 76, 255))
            tinted.putalpha(sparkle.split()[3])
            img.paste(tinted, (start_x, cy - sparkle.height // 2 + 2), tinted)
            draw = ImageDraw.Draw(img)
        self._draw_text(draw, (start_x + gap, head_y + 10), title, fonts["h_title"], COL_WHITE)

    def _tp_bg_wash(self, img, cover, x, y, w, h, *, radius, darken):
        """A cover image, centre-cropped to (w, h), darkened, and masked to
        the same rounded corners as the panel it sits behind — used for both
        the player strip (their own profile cover) and each row (the map's
        cover), a much subtler version of the old cover-thumbnail layout.
        No-op if there's no cover to show."""
        if not cover or w <= 0 or h <= 0:
            return
        try:
            bg = cover_center_crop(cover, w, h).convert("RGBA")
        except Exception:
            return
        bg = Image.alpha_composite(bg, Image.new("RGBA", (w, h), (16, 12, 14, darken)))
        mask = self._rounded_mask((w, h), radius)
        img.paste(bg.convert("RGB"), (x, y), mask)

    # ── Player summary strip ──

    def _tp_strip(self, img, data, avatar, player_cover, fonts):
        self._pf_panel(img, (INNER_L, STRIP_Y0, INNER_R, STRIP_Y1), radius=14)
        self._tp_bg_wash(img, player_cover, INNER_L, STRIP_Y0, INNER_R - INNER_L, STRIP_Y1 - STRIP_Y0, radius=14, darken=190)
        draw = ImageDraw.Draw(img)

        d = 56
        ax, ay = INNER_L + 18, STRIP_Y0 + (STRIP_Y1 - STRIP_Y0 - d) // 2
        glow = Image.new("RGBA", (d + 40, d + 40), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((20 - 8, 20 - 8, 20 + d + 8, 20 + d + 8), fill=(228, 72, 72, 120))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        img.paste(glow, (ax - 20, ay - 20), glow)
        if avatar:
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            cmask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(cmask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (ax, ay), cmask)
        else:
            self._aa_ellipse_fill(img, (ax, ay, ax + d, ay + d), fill=(52, 40, 42))
        self._aa_ellipse_outline(img, (ax, ay, ax + d, ay + d), outline=(228, 76, 76), width=3)
        draw = ImageDraw.Draw(img)

        tx = ax + d + 16
        name = str(data.get("username", "???"))
        self._draw_text(draw, (tx, STRIP_Y0 + 18), name, fonts["name"], COL_WHITE)
        yy = STRIP_Y0 + 50
        handle = data.get("handle")
        label_x = tx
        if handle:
            self._draw_text(draw, (tx, yy), handle, fonts["handle"], (188, 150, 152))
            label_x = tx + self._text_size(draw, handle, fonts["handle"])[0] + 10
        flag = load_flag(str(data.get("country", "") or ""), height=16)
        if flag:
            img.paste(flag, (label_x, yy + 2), flag)
            draw = ImageDraw.Draw(img)

    # ── Paged rows ──

    def _tp_rows(self, img, data, fonts, covers: List[Optional[Image.Image]]):
        rows = data.get("rows", []) or []
        S = _tp_lang(data)
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
            self._tp_row(img, INNER_L, ry, INNER_R - INNER_L, int(rh) - 9, rows[i], fonts, cover)

    def _tp_row(self, img, x, y, w, h, t, fonts, cover=None):
        """One row = one score, compact single-block layout: grade badge,
        title/artist/diff + chips, pp value — no rank number, no pp-delta
        (dropped per the mockup this redesign follows); the map's own cover
        is back as a subtle darkened background wash (2026-07-05), not the
        prominent square thumbnail the pre-redesign layout had."""
        self._pf_panel(img, (x, y, x + w, y + h), radius=ROW_CORNER_R, fill=COL_PANEL, border=COL_PANEL_BORDER)
        self._tp_bg_wash(img, cover, x, y, w, h, radius=ROW_CORNER_R, darken=200)
        mid = y + h // 2

        badge_cx = x + 16 + GRADE_BADGE_R
        self._tp_grade_badge(img, badge_cx, mid, t.get("rank", "F") or "F", fonts)

        pp_txt = f"{int(round(t.get('pp', 0.0)))}"
        draw = ImageDraw.Draw(img)
        pp_right = x + w - 18
        pp_w = self._text_size(draw, pp_txt, fonts["pp_big"])[0]
        suffix_w = self._text_size(draw, "pp", fonts["pp_lbl"])[0]
        # "pp" pinned to the value's own bottom-right corner (its bottom edge
        # matches the big number's bottom edge) rather than floating centred
        # beside it.
        pp_top = self._tp_cy(pp_txt, fonts["pp_big"], mid)
        _, pa, _, pb = fonts["pp_big"].getbbox(pp_txt)
        pp_bottom = pp_top + pb
        _, sa, _, sb = fonts["pp_lbl"].getbbox("pp")
        suffix_top = pp_bottom - sb
        self._draw_text(draw, (pp_right - suffix_w, suffix_top), "pp", fonts["pp_lbl"], COL_MUTED)
        self._draw_text(draw, (pp_right - suffix_w - 4 - pp_w, pp_top), pp_txt, fonts["pp_big"], COL_CORAL)

        text_x = badge_cx + GRADE_BADGE_R + 18
        text_right = pp_right - suffix_w - 4 - pp_w - 16
        self._tp_row_text_and_chips(img, text_x, y, h, text_right, t, fonts)

    def _tp_grade_badge(self, img, cx, cy, grade, fonts):
        """Plain outlined circle (not the arc-completion ring used elsewhere)
        — colour keyed off GRADE_COLORS same as everywhere else, just a
        simpler badge to match the mockup's compact rows. A soft colour glow
        behind it (same GaussianBlur technique as the avatar's) plus a
        darkened interior fill make the ring itself the accent, not just an
        outline sitting on the row's own background."""
        col = GRADE_COLORS.get(grade, GRADE_COLORS.get("F", COL_MUTED))
        r = GRADE_BADGE_R
        pad = 10
        gs = (r + pad) * 2
        glow = Image.new("RGBA", (gs, gs), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((pad - 4, pad - 4, gs - pad + 4, gs - pad + 4), fill=col + (110,))
        glow = glow.filter(ImageFilter.GaussianBlur(6))
        img.paste(glow, (cx - gs // 2, cy - gs // 2), glow)
        self._aa_ellipse_fill(img, (cx - r, cy - r, cx + r, cy + r), fill=(22, 17, 19))
        self._aa_ellipse_outline(img, (cx - r, cy - r, cx + r, cy + r), outline=col, width=3)
        draw = ImageDraw.Draw(img)
        label = "SS" if grade in ("X", "XH") else ("S" if grade in ("S", "SH") else grade)
        self._text_center(draw, cx, self._tp_cy(label, fonts["grade_badge"], cy), label, fonts["grade_badge"], col)

    def _tp_row_text_and_chips(self, img, x, y, h, right_limit, t, fonts):
        """Title + a difficulty-name pill right after it (same pill style as
        recent.py's, but placed next to the TITLE here rather than the
        artist), then "— Artist" on its own line, then a chip row: SR pill
        (rectangular here, an explicit exception — see _draw_sr_pill's radius
        note), mods, accuracy, combo."""
        draw = ImageDraw.Draw(img)
        max_w = right_limit - x
        t_y = y + 6
        title = t.get("title") or "?"
        version = t.get("version") or ""
        vlabel = ""
        vpw = 0
        if version:
            vlabel = version if len(version) <= 18 else version[:17] + "…"
            vpw = self._text_size(draw, vlabel, fonts["version_pill"])[0] + 16
        title_max_w = max_w - (vpw + 10 if vpw else 0)
        title = self._tp_ellipsize(draw, title, fonts["row_title"], title_max_w)
        self._draw_text(draw, (x, t_y), title, fonts["row_title"], COL_WHITE)
        draw = ImageDraw.Draw(img)
        if vlabel:
            tpx = x + self._text_size(draw, title, fonts["row_title"])[0] + 10
            if tpx + vpw <= x + max_w:
                pill_top, pill_bot = t_y, t_y + 17
                self._aa_rounded_fill(img, (tpx, pill_top, tpx + vpw, pill_bot), radius=8, fill=(70, 90, 150))
                draw = ImageDraw.Draw(img)
                vcy = (pill_top + pill_bot) // 2
                self._draw_text(draw, (tpx + 8, self._tp_cy(vlabel, fonts["version_pill"], vcy)), vlabel, fonts["version_pill"], (235, 240, 255))

        a_y = y + 27
        artist = t.get("artist") or ""
        if artist:
            art_txt = self._tp_ellipsize(draw, artist, fonts["row_artist"], max_w)
            self._draw_text(draw, (x, a_y), art_txt, fonts["row_artist"], COL_WHITE)
            draw = ImageDraw.Draw(img)

        chip_y = y + h - 28
        chip_cy = chip_y + 10
        # Exception to the canonical (fully-rounded, text-sized) SR pill: a
        # small fixed radius + the same fixed height as the mod pills beside
        # it, so the two read as one family instead of two different shapes.
        # center_y=chip_cy lines its vertical centre up EXACTLY with the mod
        # pills beside it, rather than the two independently-derived centres
        # coming out a pixel or two apart.
        cxp = self._draw_sr_pill(img, x, chip_y, t.get("star_rating", 0.0), fonts["sr_chip"],
                                 radius=6, height=24, center_y=chip_cy)
        draw = ImageDraw.Draw(img)
        for m in t.get("mods", []):
            cxp = self._tt_mod_pill(img, cxp, chip_cy, m, dim=False) + 6
            draw = ImageDraw.Draw(img)
        acc_txt = f"{t.get('accuracy', 0.0):.2f}%"
        self._draw_text(draw, (cxp + 4, self._tp_cy(acc_txt, fonts["row_meta"], chip_cy)), acc_txt, fonts["row_meta"], (208, 206, 214))
        cxp += self._text_size(draw, acc_txt, fonts["row_meta"])[0] + 20
        combo_txt = f"{_sp(t.get('max_combo', 0))}x"
        self._draw_text(draw, (cxp, self._tp_cy(combo_txt, fonts["row_meta"], chip_cy)), combo_txt, fonts["row_meta"], COL_MUTED)

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


def build_top_plays_card_data(
    username: str,
    handle: Optional[str],
    country: Optional[str],
    built_list: List[Dict],
    *,
    page: int = 0,
    avatar_url: Optional[str] = None,
    cover_url: Optional[str] = None,
    lang: str = "en",
    global_rank: Optional[int] = None,
    player_pp: Optional[float] = None,
    accuracy: Optional[float] = None,
) -> Dict:
    """Assemble the dict consumed by generate_top_plays_card from an already
    pp-sorted/weighted list (utils.best_scores.build_top_plays_list).

    global_rank/player_pp/accuracy are the player's OVERALL profile numbers
    (same ones /pf shows) — the summary strip shows these, not stats derived
    from this list, per the 2026-07-04 redesign. cover_url is the player's
    own profile cover/banner image, used as a background wash on the strip."""
    total_pages = max(1, (len(built_list) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    rows = built_list[page * ROWS_PER_PAGE:(page + 1) * ROWS_PER_PAGE]
    return {
        "username": username,
        "handle": handle,
        "country": country,
        "avatar_url": avatar_url,
        "cover_url": cover_url,
        "global_rank": global_rank,
        "player_pp": player_pp,
        "accuracy": accuracy,
        "updated_at": datetime.now(timezone.utc),
        "lang": lang,
        "page": page,
        "total_pages": total_pages,
        "rows": rows,
    }
