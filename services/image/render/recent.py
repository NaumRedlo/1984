import asyncio
import math
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from services.image.constants import (
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
    RECENT_ACCENT,
    RECENT_LINE,
    RECENT_TRACK,
    RECENT_BG,
    RECENT_PANEL,
    GRADE_COLORS,
    MOD_COLORS,
    TORUS_BOLD,
    TORUS_SEMI,
    TORUS_REG,
)
from services.image.utils import (
    _none_coro,
    _find_font,
    download_image,
    load_icon,
    load_mod_icon,
    cover_center_crop,
    rounded_rect_crop,
)
from services.image.render.duel_pool_card import _sr_color
from utils.osu.pp_calculator import calculate_strains

# Beatmap status → (label colour). Ints are the osu! API ranked_status codes.
_STATUS_COLORS = {
    "ranked": (80, 190, 90), "approved": (80, 190, 90), "qualified": (80, 150, 230),
    "loved": (230, 110, 170), "pending": (210, 190, 60), "wip": (210, 190, 60),
    "graveyard": (120, 120, 135),
}
_STATUS_INT = {4: "loved", 3: "qualified", 2: "approved", 1: "ranked",
               0: "pending", -1: "wip", -2: "graveyard"}


def _fnt(path, size, fallback):
    p = _find_font(path)
    return ImageFont.truetype(p, size) if p else fallback


class RecentCardMixin:
    def generate_recent_card(
        self,
        data: Dict,
        cover: Optional[Image.Image] = None,
        mapper_avatar: Optional[Image.Image] = None,
        player_avatar: Optional[Image.Image] = None,
        player_cover: Optional[Image.Image] = None,
        strains: Optional[List[float]] = None,
    ) -> BytesIO:
        # ── Canvas + fonts ──────────────────────────────────────────────────
        W, H = 1280, 652
        M = 24            # outer margin
        img = Image.new("RGB", (W, H), RECENT_BG)
        draw = ImageDraw.Draw(img)

        # Custom font set — cached on the instance (stable ids) so the CJK-fallback
        # registration below doesn't leak into _fb_map across repeated renders.
        # Registering an MPLUS fallback per custom font makes JP/CJK titles/artists
        # render instead of tofu (base fonts get this in BaseCardRenderer; ours don't).
        if not hasattr(self, "_rc_fonts"):
            from services.image.constants import MPLUS_BOLD
            mpb = _find_font(MPLUS_BOLD)
            specs = {
                "head": (TORUS_BOLD, 24, self.font_title), "title": (TORUS_BOLD, 34, self.font_big),
                "artist": (TORUS_SEMI, 20, self.font_subtitle), "chip": (TORUS_BOLD, 20, self.font_label),
                "pill": (TORUS_BOLD, 15, self.font_stat_label), "section": (TORUS_BOLD, 15, self.font_stat_label),
                "val": (TORUS_BOLD, 32, self.font_big), "val2": (TORUS_BOLD, 26, self.font_stat_value),
                "lbl": (TORUS_SEMI, 13, self.font_stat_label), "small": (TORUS_REG, 15, self.font_small),
                "grade": (TORUS_BOLD, 76, self.font_vs), "player": (TORUS_BOLD, 20, self.font_label),
            }
            fonts = {}
            for k, (path, size, fb) in specs.items():
                f = _fnt(path, size, fb)
                if mpb:
                    self._fb_map[id(f)] = ImageFont.truetype(mpb, size)
                fonts[k] = f
            self._rc_fonts = fonts
        F = self._rc_fonts
        f_head, f_title, f_artist = F["head"], F["title"], F["artist"]
        f_chip, f_pill, f_section = F["chip"], F["pill"], F["section"]
        f_val, f_val2, f_lbl = F["val"], F["val2"], F["lbl"]
        f_small, f_grade, f_player = F["small"], F["grade"], F["player"]

        def panel(x, y, w, h, r=14, fill=RECENT_PANEL):
            draw.rounded_rectangle((x, y, x + w, y + h), radius=r, fill=fill)

        # ── Data ────────────────────────────────────────────────────────────
        artist = data.get("artist", "Unknown")
        title = data.get("title", "Unknown")
        version = data.get("version", "") or ""
        mapper_name = data.get("mapper_name", "Unknown")
        stars = float(data.get("star_rating", 0.0) or 0.0)
        bpm = float(data.get("bpm", 0.0) or 0.0)
        total_length = int(data.get("total_length", 0) or 0)
        total_objects = int(data.get("total_objects", 0) or 0)
        acc = float(data.get("accuracy", 0.0) or 0.0)
        combo = int(data.get("combo", 0) or 0)
        map_max_combo = int(data.get("max_combo", 0) or 0)
        misses = int(data.get("misses", 0) or 0)
        pp = float(data.get("pp", 0.0) or 0.0)
        pp_if_fc = float(data.get("pp_if_fc", 0.0) or 0.0)
        pp_if_ss = float(data.get("pp_if_ss", 0.0) or 0.0)
        rank_grade = data.get("rank_grade", "F") or "F"
        n300 = int(data.get("count_300", 0) or 0)
        n100 = int(data.get("count_100", 0) or 0)
        n50 = int(data.get("count_50", 0) or 0)
        username = data.get("username", "???")
        is_passed = bool(data.get("passed", rank_grade != "F"))
        is_fc = misses == 0 and is_passed
        is_ss = rank_grade in ("X", "XH") or (acc >= 100.0 and is_passed)

        hit_objects = n300 + n100 + n50 + misses
        completion = min(hit_objects / total_objects, 1.0) if total_objects else (1.0 if is_passed else 0.0)

        raw_status = data.get("beatmap_status", "")
        status = _STATUS_INT.get(raw_status, "") if isinstance(raw_status, int) else (str(raw_status or "").lower())

        # ── Header ──────────────────────────────────────────────────────────
        top = 18
        head_txt = "RECENT SCORE"
        _hicon = load_icon("rsicon", size=24)
        htw = self._text_size(draw, head_txt, f_head)[0]
        icon_w = 32 if _hicon else 0
        hx = (W - (icon_w + htw)) // 2
        if _hicon:
            img.paste(_hicon, (hx, top - 2), _hicon)
            draw = ImageDraw.Draw(img)
            hx += icon_w
        self._draw_text(draw, (hx, top), head_txt, f_head, RECENT_ACCENT)
        played_at = data.get("played_at", "")
        date_str = ""
        if played_at:
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                from config.settings import TIMEZONE
                dt = datetime.fromisoformat(str(played_at).replace("Z", "+00:00"))
                date_str = dt.astimezone(ZoneInfo(TIMEZONE)).strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = str(played_at)[:16]
        if date_str:
            self._text_right(draw, W - M, top + 4, date_str, f_small, TEXT_SECONDARY)

        # ── Hero panel ──────────────────────────────────────────────────────
        hero_y, hero_h = top + 40, 200
        hero_w = W - 2 * M
        panel(M, hero_y, hero_w, hero_h)
        pad = 20

        # Duplicate the map cover, faded in from just right of centre, so the art
        # reads across the hero. Clipped to the panel's rounded corners.
        if cover:
            from PIL import ImageChops
            hbg = cover_center_crop(cover, hero_w, hero_h).convert("RGBA")
            hbg = Image.alpha_composite(hbg, Image.new("RGBA", (hero_w, hero_h), (0, 0, 0, 120)))
            hfade = Image.new("L", (hero_w, hero_h), 0)
            _fd = ImageDraw.Draw(hfade)
            _fs = int(hero_w * 0.55)
            for fx in range(_fs, hero_w):
                _fd.line([(fx, 0), (fx, hero_h)], fill=int(235 * (fx - _fs) / max(1, hero_w - _fs)))
            hfade = ImageChops.multiply(hfade, self._rounded_mask((hero_w, hero_h), 14))
            img.paste(hbg.convert("RGB"), (M, hero_y), hfade)
            draw = ImageDraw.Draw(img)

        # cover thumbnail (left) — wide landscape crop so the art isn't squished
        cov_h = hero_h - 2 * pad            # 160
        cov_w = int(cov_h * 1.85)           # ~296 wide, matches the cover's aspect
        cov_x, cov_y = M + pad, hero_y + pad
        if cover:
            thumb = cover_center_crop(cover, cov_w, cov_h).convert("RGB")
            img.paste(thumb, (cov_x, cov_y), self._rounded_mask((cov_w, cov_h), 12))
        else:
            panel(cov_x, cov_y, cov_w, cov_h, r=12, fill=(40, 40, 58))
        draw = ImageDraw.Draw(img)

        # grade ring (right) — bigger completion arc + letter + inline % badge
        ring_r = 76
        ring_cx = W - M - pad - ring_r
        ring_cy = hero_y + hero_h // 2
        self._draw_grade_ring(img, ring_cx, ring_cy, ring_r, rank_grade, completion, is_passed, f_grade, f_pill)
        draw = ImageDraw.Draw(img)

        # middle column
        mx = cov_x + cov_w + 26
        mid_right = ring_cx - ring_r - 24
        mid_w = mid_right - mx

        # mapper row (shadowed — may sit over the faded cover)
        mav_sz = 28
        mrow_y = hero_y + 22
        if mapper_avatar:
            mav = rounded_rect_crop(mapper_avatar, mav_sz, radius=6)
            img.paste(mav, (mx, mrow_y), mav)
            draw = ImageDraw.Draw(img)
        else:
            panel(mx, mrow_y, mav_sz, mav_sz, r=6, fill=(50, 50, 70))
        self._draw_text_shadow(draw, (mx + mav_sz + 8, mrow_y - 1), "mapped by", f_lbl, TEXT_SECONDARY)
        self._draw_text_shadow(draw, (mx + mav_sz + 8, mrow_y + 13), mapper_name[:26], f_small, (210, 210, 222))

        # title (truncate to mid_w)
        t_y = hero_y + 60
        disp = title
        while self._text_size(draw, disp + "…", f_title)[0] > mid_w and len(disp) > 4:
            disp = disp[:-1]
        if disp != title:
            disp += "…"
        self._draw_text_shadow(draw, (mx, t_y), disp, f_title, TEXT_PRIMARY)

        # artist + difficulty/status pills inline right after it
        art_txt = f"— {artist}"
        while self._text_size(draw, art_txt + "…", f_artist)[0] > mid_w * 0.55 and len(art_txt) > 4:
            art_txt = art_txt[:-1]
        if art_txt != f"— {artist}":
            art_txt += "…"
        a_y = t_y + 46
        self._draw_text_shadow(draw, (mx, a_y), art_txt, f_artist, TEXT_SECONDARY)
        apx = mx + self._text_size(draw, art_txt, f_artist)[0] + 12
        if version:
            vlabel = version if len(version) <= 18 else version[:17] + "…"
            vpw = self._text_size(draw, vlabel, f_pill)[0] + 18
            self._aa_rounded_fill(img, (apx, a_y - 1, apx + vpw, a_y + 23), radius=12, fill=(70, 90, 150))
            self._text_center(draw, apx + vpw // 2, a_y + 3, vlabel, f_pill, (235, 240, 255))
        draw = ImageDraw.Draw(img)

        # chips row: SR / length / BPM / objects
        chip_y = hero_y + hero_h - 48
        cx = mx
        def chip(icon_name, text):
            nonlocal cx
            ic = load_icon(icon_name, size=16)
            if ic:
                img.paste(ic, (cx, chip_y + 3), ic)
                cx += 20
            d = ImageDraw.Draw(img)
            self._draw_text_shadow(d, (cx, chip_y), text, f_chip, TEXT_PRIMARY)
            cx += self._text_size(d, text, f_chip)[0] + 22
        # canonical lazer SR pill — colour ramps with difficulty; star + value
        cx = self._draw_sr_pill(img, cx, chip_y, stars, f_chip)
        chip("timer", f"{total_length // 60}:{total_length % 60:02d}")
        chip("bpm", f"{bpm:g}")
        # map status pill — moved down to the chips row
        if status:
            slabel = status.upper()
            sc = _STATUS_COLORS.get(status, (110, 110, 130))
            spw = self._text_size(draw, slabel, f_pill)[0] + 18
            self._aa_rounded_fill(img, (cx, chip_y - 3, cx + spw, chip_y + 23), radius=13, fill=sc)
            self._text_center(draw, cx + spw // 2, chip_y + 1, slabel, f_pill, (255, 255, 255))
        draw = ImageDraw.Draw(img)

        # mod badges (top-right of hero, left of ring) — rectangular pills w/ big glyphs
        mods = data.get("mods", "")
        mod_list = self._normalize_mods(mods)
        if mod_list:
            mh, mw, gsz = 36, 44, 32
            my = hero_y + pad
            bx = mid_right
            for m in reversed(mod_list):
                bx -= mw
                col = MOD_COLORS.get(m, (100, 100, 120))
                self._aa_rounded_fill(img, (bx, my, bx + mw, my + mh), radius=9, fill=col)
                glyph = load_mod_icon(m, size=gsz)
                if glyph:
                    lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
                    ink = (25, 22, 26) if lum > 140 else (255, 255, 255)
                    tinted = Image.new("RGBA", glyph.size, ink + (255,))
                    tinted.putalpha(glyph.split()[3])
                    img.paste(tinted, (bx + (mw - glyph.width) // 2, my + (mh - glyph.height) // 2), tinted)
                else:
                    self._text_center(ImageDraw.Draw(img), bx + mw // 2, my + mh // 2 - 8, m, f_pill, (255, 255, 255))
                bx -= 6
            draw = ImageDraw.Draw(img)

        # ── Stats bar ───────────────────────────────────────────────────────
        stats_y, stats_h = hero_y + hero_h + 16, 108
        panel(M, stats_y, W - 2 * M, stats_h)
        inner_x = M + 24
        inner_w = W - 2 * M - 48
        # weighted columns: PP, ACC, COMBO wider; then 300/100/50/MISS/MAXCOMBO
        weights = [1.5, 1.7, 1.4, 1.0, 1.0, 1.0, 1.0, 1.35]
        tot = sum(weights)
        xs, acc_x = [], inner_x
        for wgt in weights:
            xs.append(acc_x)
            acc_x += inner_w * wgt / tot
        xs.append(inner_x + inner_w)
        centers = [(xs[i] + xs[i + 1]) / 2 for i in range(8)]
        lbl_y = stats_y + 16
        val_y = stats_y + 42

        def bar(cx_i, frac, color):
            bw = (xs[cx_i + 1] - xs[cx_i]) - 34
            bx0 = int(centers[cx_i] - bw / 2)
            by = stats_y + stats_h - 26
            draw.rounded_rectangle((bx0, by, bx0 + bw, by + 6), radius=3, fill=RECENT_TRACK)
            fw = max(int(bw * max(0.0, min(1.0, frac))), 0)
            if fw > 0:
                draw.rounded_rectangle((bx0, by, bx0 + fw, by + 6), radius=3, fill=color)

        pp_color = (110, 110, 122) if not is_passed else TEXT_PRIMARY
        # PP — big value = current pp; FC / SS badges below (restored old style).
        self._text_center(draw, centers[0], lbl_y, "PP", f_lbl, TEXT_SECONDARY)
        self._text_center(draw, centers[0], val_y - 4, f"{pp:.0f}" if pp else "—", f_val, pp_color)
        pp_badges = []
        if is_fc:
            pp_badges.append(("FC", ACCENT_GREEN))
        elif pp_if_fc:
            pp_badges.append((f"{pp_if_fc:.0f}pp", (60, 140, 60)))
        if is_ss:
            pp_badges.append(("SS", (255, 215, 0)))
        elif pp_if_ss:
            pp_badges.append((f"{pp_if_ss:.0f}pp", (160, 135, 10)))
        if pp_badges:
            specs = [(lbl, col, self._text_size(draw, lbl, f_lbl)[0] + 12) for lbl, col in pp_badges]
            tw = sum(bw for _, _, bw in specs) + 5 * (len(specs) - 1)
            bx = int(centers[0] - tw / 2)
            by = stats_y + stats_h - 26
            for lbl, col, bw in specs:
                self._aa_rounded_fill(img, (bx, by, bx + bw, by + 18), radius=5, fill=col)
                self._text_center(draw, bx + bw // 2, by + 2, lbl, f_lbl, (255, 255, 255))
                bx += bw + 5
        draw = ImageDraw.Draw(img)
        # ACCURACY
        self._text_center(draw, centers[1], lbl_y, "ACCURACY", f_lbl, TEXT_SECONDARY)
        self._text_center(draw, centers[1], val_y - 4, f"{acc:.2f}%", f_val, TEXT_PRIMARY)
        bar(1, acc / 100.0, RECENT_LINE)
        # COMBO
        self._text_center(draw, centers[2], lbl_y, "COMBO", f_lbl, TEXT_SECONDARY)
        self._text_center(draw, centers[2], val_y - 4, f"{combo}x", f_val, RECENT_LINE)
        bar(2, (combo / map_max_combo) if map_max_combo else 0.0, RECENT_LINE)
        # counts
        counts = [
            ("300", n300, (120, 220, 130)),
            ("100", n100, (230, 205, 90)),
            ("50", n50, (210, 150, 90)),
            ("MISS", misses, ACCENT_RED),
            ("MAX COMBO", f"{map_max_combo}x", RECENT_LINE),
        ]
        for i, (lbl, val, col) in enumerate(counts):
            c = centers[3 + i]
            self._text_center(draw, c, lbl_y, lbl, f_lbl, col)
            self._text_center(draw, c, val_y, str(val), f_val2, TEXT_PRIMARY)

        # ── Middle row: PERFORMANCE | DETAILS | PLAYER ──────────────────────
        mid_y = stats_y + stats_h + 16
        mid_h = H - mid_y - 22
        gap = 16
        perf_w = int((W - 2 * M - 2 * gap) * 0.50)
        det_w = int((W - 2 * M - 2 * gap) * 0.26)
        ply_w = (W - 2 * M - 2 * gap) - perf_w - det_w
        perf_x = M
        det_x = perf_x + perf_w + gap
        ply_x = det_x + det_w + gap

        # PERFORMANCE
        panel(perf_x, mid_y, perf_w, mid_h)
        self._draw_text(draw, (perf_x + 18, mid_y + 14), "MAP DIFFICULTY", f_section, RECENT_ACCENT)
        self._draw_perf_graph(img, perf_x + 18, mid_y + 44, perf_w - 36, mid_h - 64,
                              strains, completion, is_passed, f_lbl)
        draw = ImageDraw.Draw(img)

        # DETAILS (CS/AR/OD/HP)
        panel(det_x, mid_y, det_w, mid_h)
        self._draw_text(draw, (det_x + 18, mid_y + 14), "DETAILS", f_section, RECENT_ACCENT)
        params = [("CS", "cs", data.get("cs", 0.0)), ("AR", "ar", data.get("ar", 0.0)),
                  ("OD", "od", data.get("od", 0.0)), ("HP", "hp", data.get("hp", 0.0))]
        drow_y = mid_y + 52
        drow_gap = (mid_h - 64) // 4
        for i, (lbl, icon, val) in enumerate(params):
            ry = drow_y + i * drow_gap
            lx = det_x + 18
            dic = load_icon(icon, size=18)
            if dic:
                img.paste(dic, (lx, ry + 1), dic)
                draw = ImageDraw.Draw(img)
                lx += 24
            self._draw_text(draw, (lx, ry), lbl, f_chip, TEXT_SECONDARY)
            self._text_right(draw, det_x + det_w - 18, ry, f"{float(val):.1f}", f_chip, TEXT_PRIMARY)
            bw = det_w - 36
            by = ry + 26
            frac = min(float(val) / 10.0, 1.0)
            draw.rounded_rectangle((det_x + 18, by, det_x + 18 + bw, by + 5), radius=2, fill=RECENT_TRACK)
            t = frac
            col = (int(90 * (1 - t) + 230 * t), int(200 * (1 - t) + 90 * t), 90)
            if frac > 0:
                draw.rounded_rectangle((det_x + 18, by, det_x + 18 + int(bw * frac), by + 5), radius=2, fill=col)

        # PLAYER
        panel(ply_x, mid_y, ply_w, mid_h)
        if player_cover or cover:
            pc = cover_center_crop(player_cover or cover, ply_w, mid_h)
            ov = Image.new("RGBA", (ply_w, mid_h), (0, 0, 0, 170))
            pc = Image.alpha_composite(pc, ov)
            mask = self._rounded_mask((ply_w, mid_h), 14)
            img.paste(pc.convert("RGB"), (ply_x, mid_y), mask)
            draw = ImageDraw.Draw(img)
        self._draw_text(draw, (ply_x + 18, mid_y + 14), "PLAYER", f_section, RECENT_ACCENT)
        pav = 88
        pcx = ply_x + ply_w // 2
        pav_x, pav_y = pcx - pav // 2, mid_y + 58
        if player_avatar:
            circle = self._circle_crop(player_avatar, pav)
            img.paste(circle, (pav_x, pav_y), circle)
        self._aa_ellipse_outline(img, (pav_x - 2, pav_y - 2, pav_x + pav + 2, pav_y + pav + 2),
                                 outline=RECENT_ACCENT, width=3)
        draw = ImageDraw.Draw(img)
        self._text_center(draw, pcx, pav_y + pav + 12, "played by", f_lbl, TEXT_SECONDARY, shadow=True)
        uname = username
        while self._text_size(draw, uname, f_player)[0] > ply_w - 24 and len(uname) > 3:
            uname = uname[:-1]
        if uname != username:
            uname += ".."
        self._text_center(draw, pcx, pav_y + pav + 30, uname, f_player, RECENT_LINE, shadow=True)

        return self._save(img)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _circle_crop(self, src: Image.Image, size: int) -> Image.Image:
        """Center-crop `src` to a circular avatar of `size` px (anti-aliased)."""
        ss = 4
        sq = cover_center_crop(src, size * ss, size * ss).convert("RGBA")
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
        sq.putalpha(mask)
        return sq.resize((size, size), Image.LANCZOS)

    def _draw_sr_pill(self, img, x, y, stars, f_chip):
        """Canonical lazer star-rating pill: rounded fill coloured by the osu!
        difficulty ramp, a star glyph and the SR value. On bright fills text/
        glyph go dark; on dark fills they go gold. Returns the x cursor just
        past the pill (with a gap)."""
        col = _sr_color(stars)
        lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
        # Gold only at the top end (SR>=6.5); below that plain dark/white by fill.
        if stars >= 6.5:
            fg = (255, 204, 64)
        else:
            fg = (20, 20, 24) if lum > 150 else (255, 255, 255)
        text = f"{stars:.2f}"
        d = ImageDraw.Draw(img)
        tw, th = self._text_size(d, text, f_chip)
        # Torus glyphs sit low in the em box, so the drawn ink centre is not at
        # y+th/2. Measure the real ink bbox and centre the pill on that instead,
        # so it lines up with the plain timer/BPM chips beside it.
        bb = d.textbbox((0, 0), text, font=f_chip)
        ink_cy = y + (bb[1] + bb[3]) / 2
        star = load_icon("star", size=16)
        if star:
            tinted = Image.new("RGBA", star.size, fg + (255,))
            tinted.putalpha(star.split()[3])
            star = tinted
        sw = (star.width + 4) if star else 0
        pad_x, pad_y = 10, 4
        w = sw + tw + pad_x * 2
        h = th + pad_y * 2
        top = int(ink_cy - h / 2)
        self._aa_rounded_fill(img, (x, top, x + w, top + h), radius=h // 2, fill=col)
        ix = x + pad_x
        if star:
            img.paste(star, (ix, int(ink_cy - star.height / 2)), star)
            ix += star.width + 4
        self._draw_text(ImageDraw.Draw(img), (ix, y), text, f_chip, fg)
        return x + w + 12

    def _draw_grade_ring(self, img, cx, cy, r, grade, completion, passed, f_grade, f_pct):
        """Completion-arc ring (red) + centered grade letter + % badge inline on
        the ring's bottom edge."""
        ss = 4
        big = r * 2 * ss
        layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        wdt = 7 * ss
        box = (wdt, wdt, big - wdt, big - wdt)
        # Dark backing disc so the grade stays legible over a busy cover behind it.
        d.ellipse(box, fill=(10, 9, 13, 165))
        d.ellipse(box, outline=RECENT_TRACK + (255,), width=wdt)
        sweep = 360.0 * max(0.0, min(1.0, completion))
        # Arc tinted by the achieved grade; a failed/incomplete run stays red as a
        # "didn't clear it" signal rather than borrowing the grade's colour.
        arc_col = ACCENT_RED if (not passed or grade == "F") else GRADE_COLORS.get(grade, RECENT_ACCENT)
        mid = big / 2
        rad = mid - 1.5 * wdt                    # centreline radius (track's stroke sits inward of box)
        cap = wdt / 2                            # brush radius = half stroke width

        def _stroke(dd):
            # arc() only draws flat butt ends, so paint the stroke as a chain of
            # overlapping round brush dabs — the line itself gets rounded ends.
            fill = arc_col + (255,)
            steps = max(2, int(math.radians(sweep) * rad / cap))
            for i in range(steps + 1):
                a = math.radians(-90 + sweep * i / steps)
                px, py = mid + rad * math.cos(a), mid + rad * math.sin(a)
                dd.ellipse((px - cap, py - cap, px + cap, py + cap), fill=fill)

        if sweep > 0:
            # Soft glow: a blurred copy of the arc on its own layer underneath.
            glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            _stroke(ImageDraw.Draw(glow))
            glow = glow.filter(ImageFilter.GaussianBlur(wdt * 0.9))
            layer = Image.alpha_composite(layer, glow)
            _stroke(ImageDraw.Draw(layer))
        layer = layer.resize((r * 2, r * 2), Image.LANCZOS)
        img.paste(layer, (cx - r, cy - r), layer)
        draw = ImageDraw.Draw(img)
        gcol = GRADE_COLORS.get(grade, TEXT_PRIMARY)
        gb = draw.textbbox((0, 0), grade, font=f_grade)
        draw.text((cx - (gb[2] - gb[0]) // 2 - gb[0], cy - (gb[3] - gb[1]) // 2 - gb[1]),
                  grade, font=f_grade, fill=gcol)
        if not passed or completion < 1.0:
            lbl = f"{completion * 100:.0f}%"
            bw = draw.textbbox((0, 0), lbl, font=f_pct)
            w = (bw[2] - bw[0]) + 16
            bh = 24
            by = cy + r - 7 - bh // 2      # centered exactly on the ring's stroke line
            col = ACCENT_RED if completion < 0.5 else (205, 180, 55)
            self._aa_rounded_fill(img, (cx - w // 2, by, cx + w // 2, by + bh), radius=7, fill=col)
            self._text_center(ImageDraw.Draw(img), cx, by + 4, lbl, f_pct, (255, 255, 255))

    def _draw_perf_graph(self, img, x, y, w, h, series, completion, passed, f_lbl):
        """The map's difficulty (strain) across its timeline. X = song progress
        (0→100%); Y = relative difficulty (no % — strain isn't a percentage).
        For a fail, a marker shows how far through the map the player got."""
        draw = ImageDraw.Draw(img)
        plot_x, plot_w = x, w
        # faint horizontal gridlines only (no misleading Y-axis % labels)
        for gi in range(1, 4):
            gy = y + int(h * gi / 4)
            draw.line([(plot_x, gy), (plot_x + plot_w, gy)], fill=(44, 36, 42), width=1)
        if not series:
            self._text_center(draw, x + w // 2, y + h // 2 - 8, "NO DATA", f_lbl, TEXT_SECONDARY)
            return
        n = len(series)
        pts = [(plot_x + plot_w * i / (n - 1), y + h - h * series[i]) for i in range(n)]
        # gradient fill under the curve
        fill_layer = Image.new("RGBA", (img.width, img.height), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fill_layer)
        poly = pts + [(plot_x + plot_w, y + h), (plot_x, y + h)]
        fd.polygon(poly, fill=RECENT_ACCENT + (70,))
        img.paste(fill_layer, (0, 0), fill_layer)
        draw = ImageDraw.Draw(img)
        draw.line(pts, fill=RECENT_LINE, width=3, joint="curve")
        # X-axis song-progress ticks so the fail marker reads as "% through the map"
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            tx = plot_x + int(plot_w * frac)
            lbl = f"{int(frac * 100)}%"
            lw = self._text_size(draw, lbl, f_lbl)[0]
            lx = plot_x if frac == 0 else (plot_x + plot_w - lw if frac == 1 else tx - lw // 2)
            draw.text((lx, y + h + 4), lbl, font=f_lbl, fill=(120, 108, 116))
        # fail marker — where the player stopped along the map timeline
        if not passed and 0 < completion < 1.0:
            fx = int(plot_x + plot_w * completion)
            for yy in range(y, y + h, 6):
                draw.line([(fx, yy), (fx, min(yy + 3, y + h))], fill=ACCENT_RED, width=2)
            fi = min(int((n - 1) * completion), n - 1)
            fy = int(y + h - h * series[fi])
            draw.ellipse((fx - 4, fy - 4, fx + 4, fy + 4), fill=ACCENT_RED)
            lbl = f"FAILED {completion * 100:.0f}%"
            tw = self._text_size(draw, lbl, f_lbl)[0]
            bw = tw + 12
            bx = min(fx + 6, x + w - bw)
            self._aa_rounded_fill(img, (bx, fy - 22, bx + bw, fy - 3), radius=5, fill=ACCENT_RED)
            self._text_center(ImageDraw.Draw(img), bx + bw // 2, fy - 20, lbl, f_lbl, (255, 255, 255))

    async def generate_recent_card_async(self, data: Dict) -> BytesIO:
        bsid = data.get("beatmapset_id", 0)
        mapper_id = data.get("mapper_id", 0)
        player_id = data.get("player_id", 0)
        beatmap_id = data.get("beatmap_id", 0)
        mods = data.get("mods", "") or ""
        player_cover_url = data.get("player_cover_url") or None

        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        mapper_avatar_url = f"https://a.ppy.sh/{mapper_id}" if mapper_id else None
        player_avatar_url = f"https://a.ppy.sh/{player_id}" if player_id else None

        cover, mapper_avatar, player_avatar, player_cover, strains = await asyncio.gather(
            download_image(cover_url) if cover_url else _none_coro(),
            download_image(mapper_avatar_url) if mapper_avatar_url else _none_coro(),
            download_image(player_avatar_url) if player_avatar_url else _none_coro(),
            download_image(player_cover_url) if player_cover_url else _none_coro(),
            calculate_strains(beatmap_id, self._mods_str(mods)) if beatmap_id else _none_coro(),
        )
        return await asyncio.to_thread(
            self.generate_recent_card, data, cover, mapper_avatar,
            player_avatar, player_cover, strains,
        )

    @staticmethod
    def _mods_str(mods) -> str:
        """Coerce mods (str or list/dicts) into a concatenated 'HDDT' acronym string."""
        if isinstance(mods, str):
            return mods.replace("+", "").replace(",", "").replace(" ", "")
        if isinstance(mods, list):
            out = []
            for m in mods:
                if isinstance(m, str):
                    out.append(m)
                elif isinstance(m, dict):
                    out.append(m.get("acronym", ""))
            return "".join(out)
        return ""
