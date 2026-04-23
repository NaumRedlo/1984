import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from services.image.constants import (
    BG_COLOR,
    HEADER_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
    GRADE_COLORS,
    MOD_COLORS,
    PADDING_X,
    TORUS_BOLD,
)
from services.image.utils import (
    _none_coro,
    _find_font,
    download_image,
    load_icon,
    cover_center_crop,
    rounded_rect_crop,
)


class RecentCardMixin:
    def generate_recent_card(
        self,
        data: Dict,
        cover: Optional[Image.Image] = None,
        mapper_avatar: Optional[Image.Image] = None,
        player_avatar: Optional[Image.Image] = None,
        player_cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 470
        img, draw = self._create_canvas(W, H)
        icon_sz = 14
        username = data.get("username", "???")

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
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — RECENT SCORE", self.font_subtitle, ACCENT_RED)
        # Play date/time in top-right corner
        played_at = data.get("played_at", "")
        if played_at:
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                from config.settings import TIMEZONE

                dt = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
                tz = ZoneInfo(TIMEZONE)
                local_dt = dt.astimezone(tz)
                date_str = local_dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = str(played_at)[:16]
        else:
            date_str = ""
        if date_str:
            self._text_right(draw, W - PADDING_X, 10, date_str, self.font_small, TEXT_SECONDARY)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        # ── 2. HERO COVER (y=36..176, 140px) ──
        hero_y = header_h
        hero_h = 140

        hero_src = cover or player_cover
        if hero_src:
            cropped = cover_center_crop(hero_src, W, hero_h)
            darkness = 110 if cover else 140
            overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, darkness))
            cropped = Image.alpha_composite(cropped, overlay)
            # Left-side extra darkening gradient for text readability
            left_shade = Image.new("RGBA", (W, hero_h), (0, 0, 0, 0))
            for lx in range(360):
                alpha = int(80 * (1 - lx / 360))
                ImageDraw.Draw(left_shade).line([(lx, 0), (lx, hero_h)], fill=(0, 0, 0, alpha))
            cropped = Image.alpha_composite(cropped, left_shade)
            # No bottom fade — paste directly flush against header
            img.paste(cropped.convert("RGB"), (0, hero_y))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, hero_y), (W, hero_y + hero_h)], fill=HEADER_BG)

        # Hero overlay: map info (left side) — with shadows
        artist = data.get("artist", "Unknown")
        title = data.get("title", "Unknown")
        map_title = f"{title} — {artist}"
        max_tw = 540
        full_title = map_title
        mt_bbox = draw.textbbox((0, 0), map_title, font=self.font_row)
        while mt_bbox[2] - mt_bbox[0] > max_tw and len(map_title) > 4:
            map_title = map_title[:-1]
            mt_bbox = draw.textbbox((0, 0), map_title + "...", font=self.font_row)
        if len(map_title) < len(full_title):
            map_title += "..."
        _shadow_text(draw, (PADDING_X, hero_y + 8), map_title, self.font_row, TEXT_PRIMARY)

        # Mapper avatar + name (with shadows)
        mapper_name = data.get("mapper_name", "Unknown")
        mav_x, mav_y, mav_sz = PADDING_X, hero_y + 34, 28
        if mapper_avatar:
            mav = rounded_rect_crop(mapper_avatar, mav_sz, radius=6)
            img.paste(mav, (mav_x, mav_y), mav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((mav_x, mav_y, mav_x + mav_sz, mav_y + mav_sz), radius=6, outline=TEXT_SECONDARY, width=2)
        else:
            draw.rounded_rectangle((mav_x, mav_y, mav_x + mav_sz, mav_y + mav_sz), radius=6, fill=(50, 50, 70), outline=TEXT_SECONDARY, width=2)
        mtx = mav_x + mav_sz + 8
        _shadow_text(draw, (mtx, mav_y), "mapped by", self.font_stat_label, TEXT_SECONDARY)
        _shadow_text(draw, (mtx, mav_y + 14), mapper_name, self.font_small, (200, 200, 210))

        # Star / BPM / Length icons row
        stars = data.get("star_rating", 0.0)
        bpm = data.get("bpm", 0)
        total_length = data.get("total_length", 0)
        row3_y = hero_y + 70
        cur_x = PADDING_X
        star_icon = load_icon("star", size=icon_sz)
        if star_icon:
            img.paste(star_icon, (cur_x, row3_y + 2), star_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), f"{stars:.2f}", self.font_label, TEXT_PRIMARY)
        cur_x += draw.textbbox((0, 0), f"{stars:.2f}", font=self.font_label)[2] + 16
        bpm_icon = load_icon("bpm", size=icon_sz)
        if bpm_icon:
            img.paste(bpm_icon, (cur_x, row3_y + 2), bpm_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), str(bpm), self.font_label, TEXT_PRIMARY)
        cur_x += draw.textbbox((0, 0), str(bpm), font=self.font_label)[2] + 16
        minutes = total_length // 60
        seconds = total_length % 60
        length_str = f"{minutes}:{seconds:02d}"
        timer_icon = load_icon("timer", size=icon_sz)
        if timer_icon:
            img.paste(timer_icon, (cur_x, row3_y + 2), timer_icon)
            draw = ImageDraw.Draw(img)
            cur_x += icon_sz + 4
        _shadow_text(draw, (cur_x, row3_y), length_str, self.font_label, TEXT_PRIMARY)

        # [version] + beatmap status badge
        version = data.get("version", "Unknown")
        ver_y = hero_y + 94
        ver_text = f"[{version}]"
        ver_bbox = draw.textbbox((0, 0), ver_text, font=self.font_small)
        if ver_bbox[2] - ver_bbox[0] > 260:
            while ver_bbox[2] - ver_bbox[0] > 256 and len(version) > 4:
                version = version[:-1]
                ver_bbox = draw.textbbox((0, 0), f"[{version}...]", font=self.font_small)
            ver_text = f"[{version}...]"
        _shadow_text(draw, (PADDING_X, ver_y), ver_text, self.font_small, TEXT_SECONDARY)

        # Beatmap status badge (Ranked, Loved, Graveyard, etc.)
        STATUS_COLORS = {
            "ranked": (80, 180, 80),
            "approved": (80, 180, 80),
            "qualified": (80, 140, 220),
            "loved": (220, 100, 160),
            "pending": (200, 180, 50),
            "wip": (200, 180, 50),
            "graveyard": (100, 100, 100),
        }
        STATUS_INT_MAP = {
            4: "loved", 3: "qualified", 2: "approved", 1: "ranked",
            0: "pending", -1: "wip", -2: "graveyard",
        }
        raw_status = data.get("beatmap_status", "")
        if isinstance(raw_status, int):
            beatmap_status = STATUS_INT_MAP.get(raw_status, "")
        else:
            beatmap_status = str(raw_status) if raw_status else ""
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
        mods = data.get("mods", "")
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
        acc = data.get("accuracy", 0.0)
        combo = data.get("combo", 0)
        max_combo = data.get("max_combo", 0)
        misses = data.get("misses", 0)
        pp = data.get("pp", 0.0)
        pp_if_fc = data.get("pp_if_fc", 0.0)
        rank_grade = data.get("rank_grade", "F")
        total_score = data.get("total_score", 0)
        count_300 = data.get("count_300", 0)
        count_100 = data.get("count_100", 0)
        count_50 = data.get("count_50", 0)
        total_objects = data.get("total_objects", 0)
        is_passed = data.get("passed", rank_grade != "F")

        # Completion percentage
        hit_objects = count_300 + count_100 + count_50 + misses
        if total_objects and total_objects > 0:
            completion = min(hit_objects / total_objects * 100, 100.0)
        else:
            completion = 100.0 if is_passed else 0.0

        is_fc = misses == 0 and is_passed
        is_ss = rank_grade in ("X", "XH") or (acc >= 100.0 and is_passed)

        # Grade circle (left, x center=90) — with tinted glow background and thick outline
        grade_cx = 90
        grade_cy = score_y + 68
        circle_r = 56
        grade_color = GRADE_COLORS.get(rank_grade, TEXT_PRIMARY)
        # Dimmed grade color glow
        glow_r = int(grade_color[0] * 0.15)
        glow_g = int(grade_color[1] * 0.15)
        glow_b = int(grade_color[2] * 0.15)
        circle_img = Image.new("RGBA", (circle_r * 2, circle_r * 2), (0, 0, 0, 0))
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

        # Completion badge under grade circle (only if not passed)
        if not is_passed or completion < 100.0:
            comp_y = grade_cy + circle_r + 5
            comp_label = f"{completion:.0f}%"
            comp_color = ACCENT_RED if completion < 50 else (200, 180, 50)
            cb = draw.textbbox((0, 0), comp_label, font=self.font_stat_label)
            cw = cb[2] - cb[0] + 10
            cx_start = grade_cx - cw // 2
            draw.rounded_rectangle((cx_start, comp_y, cx_start + cw, comp_y + 16), radius=4, fill=comp_color)
            self._text_center(draw, grade_cx, comp_y + 1, comp_label, self.font_stat_label, (255, 255, 255))

        pp_if_fc = data.get("pp_if_fc", 0.0)
        pp_if_ss = data.get("pp_if_ss", 0.0)

        # Top row: PP, Accuracy, Combo (3 panels)
        stats_x = 170
        stats_w = W - PADDING_X - stats_x
        panel_gap = 12
        panel_w = (stats_w - 2 * panel_gap) // 3
        panel_h = 68
        top_row_y = score_y + 6

        # PP panel — with FC/SS badges inside
        pp_x = stats_x
        self._draw_panel(draw, pp_x, top_row_y, panel_w, panel_h)
        draw.text((pp_x + 10, top_row_y + 6), "PP", font=self.font_stat_label, fill=TEXT_SECONDARY)
        pp_str = f"{pp:.0f}" if pp > 0 else "—"
        # Gray out PP value on fail
        pp_color = (100, 100, 110) if not is_passed else TEXT_PRIMARY
        self._text_center(draw, pp_x + panel_w // 2, top_row_y + 14, pp_str, font_pp, pp_color)

        # FC / SS badges at bottom of PP panel
        badge_h = 14
        badge_gap = 4
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
            specs = []
            tw = 0
            for label, color in pp_badges:
                bb = draw.textbbox((0, 0), label, font=self.font_stat_label)
                bw = bb[2] - bb[0] + 8
                specs.append((label, color, bw))
                tw += bw
            tw += badge_gap * (len(specs) - 1)
            bx = pp_x + (panel_w - tw) // 2
            by = top_row_y + panel_h - badge_h - 4
            for label, color, bw in specs:
                draw.rounded_rectangle((bx, by, bx + bw, by + badge_h), radius=3, fill=color)
                self._text_center(draw, bx + bw // 2, by + 1, label, self.font_stat_label, (255, 255, 255))
                bx += bw + badge_gap

        # Accuracy panel
        acc_x = stats_x + panel_w + panel_gap
        self._draw_panel(draw, acc_x, top_row_y, panel_w, panel_h)
        draw.text((acc_x + 10, top_row_y + 6), "ACCURACY", font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_center(draw, acc_x + panel_w // 2, top_row_y + 24, f"{acc:.2f}%", self.font_stat_value, TEXT_PRIMARY)

        # Combo panel
        combo_x = stats_x + 2 * (panel_w + panel_gap)
        self._draw_panel(draw, combo_x, top_row_y, panel_w, panel_h)
        draw.text((combo_x + 10, top_row_y + 6), "COMBO", font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_center(draw, combo_x + panel_w // 2, top_row_y + 24, f"{combo}x", self.font_stat_value, TEXT_PRIMARY)
        if max_combo and max_combo > 0:
            # Small max combo value below player combo
            max_combo_str = f"/ {max_combo}x"
            max_combo_color = ACCENT_GREEN if combo == max_combo else (80, 78, 100)
            self._text_center(draw, combo_x + panel_w // 2, top_row_y + 46, max_combo_str, self.font_stat_label, max_combo_color)

        # Bottom row: Score, 300, 100, 50, Misses (5 panels — misses last/rightmost)
        bot_row_y = top_row_y + panel_h + 8
        bot_h = 68
        bot_gap = 5
        score_w, hit_w, miss_w = 180, 100, 100
        bx = stats_x

        # Score
        score_client = data.get("score_client", "")
        self._draw_panel(draw, bx, bot_row_y, score_w, bot_h)
        self._text_center(draw, bx + score_w // 2, bot_row_y + 8, "SCORE", self.font_stat_label, TEXT_SECONDARY)
        self._text_center(draw, bx + score_w // 2, bot_row_y + 24, f"{total_score:,}", self.font_row, TEXT_PRIMARY)
        if score_client:
            self._text_center(draw, bx + score_w // 2, bot_row_y + 48, score_client, self.font_stat_label, TEXT_SECONDARY)
        bx += score_w + bot_gap

        # 300 / 100 / 50
        hit_colors = {"300": (80, 200, 80), "100": (200, 180, 50), "50": (200, 100, 50)}
        for hit_label, hit_val in [("300", count_300), ("100", count_100), ("50", count_50)]:
            self._draw_panel(draw, bx, bot_row_y, hit_w, bot_h)
            hc = hit_colors[hit_label]
            self._text_center(draw, bx + hit_w // 2, bot_row_y + 8, hit_label, self.font_stat_label, hc)
            self._text_center(draw, bx + hit_w // 2, bot_row_y + 26, str(hit_val), self.font_row, hc)
            bx += hit_w + bot_gap

        # Misses (rightmost)
        self._draw_panel(draw, bx, bot_row_y, miss_w, bot_h)
        miss_val = str(misses) if misses > 0 else "FC"
        miss_val_color = ACCENT_RED if misses > 0 else ACCENT_GREEN
        self._text_center(draw, bx + miss_w // 2, bot_row_y + 8, "MISSES", self.font_stat_label, ACCENT_RED)
        self._text_center(draw, bx + miss_w // 2, bot_row_y + 26, miss_val, self.font_row, miss_val_color)

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
            p_overlay = Image.new("RGBA", (player_zone_w, band4_h), (0, 0, 0, 160))
            pcrop = Image.alpha_composite(pcrop, p_overlay)
            # Left fade: blends into BG_COLOR
            pfade = Image.new("L", (player_zone_w, band4_h), 255)
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
            img.paste(pcrop.convert("RGB"), (player_zone_x, band4_y), pfade)
            draw = ImageDraw.Draw(img)

        # Difficulty section (left)
        draw.text((PADDING_X, band4_y + 4), "DIFFICULTY", font=self.font_label, fill=ACCENT_RED)
        diff_grid_y = band4_y + 26
        diff_pw, diff_ph = 170, 40
        diff_col_gap, diff_row_gap = 14, 6
        params = [
            ("CS", data.get("cs", 0.0), 10.0),
            ("AR", data.get("ar", 0.0), 10.0),
            ("OD", data.get("od", 0.0), 10.0),
            ("HP", data.get("hp", 0.0), 10.0),
        ]
        for i, (label, val, max_val) in enumerate(params):
            col = i % 2
            row = i // 2
            px = PADDING_X + col * (diff_pw + diff_col_gap)
            py = diff_grid_y + row * (diff_ph + diff_row_gap)
            self._draw_panel(draw, px, py, diff_pw, diff_ph)
            draw.text((px + 10, py + 10), label, font=self.font_label, fill=TEXT_SECONDARY)
            val_str = f"{val:.1f}" if isinstance(val, float) else str(val)
            self._text_right(draw, px + diff_pw - 10, py + 10, val_str, self.font_label, TEXT_PRIMARY)
            # Proportion bar — rounded, soft, edge-to-edge within panel
            proportion = min(float(val) / max_val, 1.0) if max_val else 0
            bar_margin = 4
            bar_max_w = diff_pw - bar_margin * 2
            bar_w = max(int(bar_max_w * proportion), 4) if proportion > 0 else 0
            bar_h = 4
            bar_y = py + diff_ph - bar_h - 2
            if bar_w > 0:
                t = proportion
                bar_r = int(60 * (1 - t) + 220 * t)
                bar_g = int(210 * (1 - t) + 60 * t)
                bar_b = int(60 * (1 - t) + 60 * t)
                # Background track
                draw.rounded_rectangle(
                    (px + bar_margin, bar_y, px + bar_margin + bar_max_w, bar_y + bar_h),
                    radius=2,
                    fill=(40, 38, 55),
                )
                # Filled bar
                draw.rounded_rectangle(
                    (px + bar_margin, bar_y, px + bar_margin + bar_w, bar_y + bar_h),
                    radius=2,
                    fill=(bar_r, bar_g, bar_b),
                )

        # Player section (right, centered horizontally and vertically in player zone)
        pav_sz = 56
        player_cx = player_zone_x + player_zone_w // 2
        # Total block height: avatar(56) + gap(6) + "Played by"(~12) + gap(4) + username(~16) = ~94
        player_block_h = pav_sz + 6 + 12 + 4 + 16
        pav_x = player_cx - pav_sz // 2
        pav_y = band4_y + (band4_h - player_block_h) // 2
        if player_avatar:
            pav = rounded_rect_crop(player_avatar, pav_sz, radius=12)
            img.paste(pav, (pav_x, pav_y), pav)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((pav_x - 1, pav_y - 1, pav_x + pav_sz + 1, pav_y + pav_sz + 1), radius=12, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((pav_x, pav_y, pav_x + pav_sz, pav_y + pav_sz), radius=12, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        self._text_center(draw, player_cx, pav_y + pav_sz + 6, "Played by", self.font_stat_label, TEXT_SECONDARY)
        uname_display = username
        uname_max_w = player_zone_w - 20
        uname_bbox = draw.textbbox((0, 0), uname_display, font=self.font_label)
        while uname_bbox[2] - uname_bbox[0] > uname_max_w and len(uname_display) > 3:
            uname_display = uname_display[:-1]
            uname_bbox = draw.textbbox((0, 0), uname_display + "..", font=self.font_label)
        if len(uname_display) < len(username):
            uname_display += ".."
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

