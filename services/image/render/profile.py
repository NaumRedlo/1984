import asyncio
from io import BytesIO
from typing import Dict, Optional, List

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR,
    HEADER_BG,
    ROW_EVEN,
    ROW_ODD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    TOP_COLORS,
    GRADE_COLORS,
    MONTH_NAMES,
    PADDING_X,
)
from services.image.utils import (
    load_flag,
    _none_coro,
    download_image,
    rounded_rect_crop,
    cover_center_crop,
    draw_line_graph,
)


class ProfileCardMixin:
    # Profile Page 0 — Info  (800 × 620)

    def generate_profile_info_card(
        self,
        data: Dict,
        avatar: Optional[Image.Image] = None,
        cover: Optional[Image.Image] = None,
    ) -> BytesIO:
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
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=16,
                outline=ACCENT_RED,
                width=2,
            )
        else:
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=16,
                fill=(50, 50, 70),
                outline=ACCENT_RED,
                width=2,
            )

        username = data.get("username", "???")
        country = data.get("country", "")
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

        level = data.get("level", 0)
        level_progress = data.get("level_progress", 0)
        bar_w = 340
        bar_x = (W - bar_w) // 2
        y_bar = name_y + 46
        draw.text((bar_x, y_bar - 18), f"Lv{level}", font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, bar_x + bar_w, y_bar - 18, f"Lv{level + 1}", self.font_small, TEXT_SECONDARY)
        draw.rounded_rectangle((bar_x, y_bar, bar_x + bar_w, y_bar + 14), radius=7, fill=TEXT_PRIMARY)
        inner_w = max(10, int((bar_w - 4) * level_progress / 100))
        draw.rounded_rectangle((bar_x + 2, y_bar + 2, bar_x + 2 + inner_w, y_bar + 12), radius=6, fill=ACCENT_RED)
        self._text_center(draw, bar_x + bar_w // 2, y_bar + 0, f"{level_progress}%", self.font_small, BG_COLOR)
        play_count = data.get("play_count", 0) or 0
        total_hits = data.get("total_hits", 0) or 0
        hpp = total_hits / play_count if play_count > 0 else 0.0

        top_stats_y = y_bar + 34
        top_gap = 10
        top_panel_h = 44
        top_panel_w = (W - 2 * PADDING_X - 2 * top_gap) // 3
        top_stats = [
            (f"{data.get('pp', 0.0):.0f}pp" if data.get("pp", 0.0) else "—", "PP"),
            (f"{data.get('global_rank', 0):,}" if data.get("global_rank", 0) else "—", "GLOBAL RANK"),
            (f"{data.get('accuracy', 0):.2f}%", "ACCURACY"),
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

        hp_points = data.get("hp_points", 0)
        left_stats = [
            (f"{hp_points} HP", "HP"),
            (f"{play_count:,}", "PLAY COUNT"),
            (f"{data.get('ranked_score', 0):,}", "RANKED SCORE"),
            (f"{total_hits:,}", "TOTAL HITS"),
        ]
        right_stats = [
            (str(data.get("hp_rank", "—")), "HPS"),
            (str(data.get("play_time", "—")), "PLAY TIME"),
            (f"{data.get('total_score', 0):,}", "TOTAL SCORE"),
            (f"{hpp:.2f}" if play_count > 0 else "—", "HITS / PLAY"),
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

        return self._save(img)

    # Profile Page 1 — Rank History  (800 × 500)

    def generate_profile_rank_card(self, data: Dict) -> BytesIO:
        W, H = 800, 516
        img, draw = self._create_canvas(W, H)

        username = data.get("username", "???")
        self._draw_header(draw, "PROJECT 1984 — RANK HISTORY", username, W)

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
            self._text_center(draw, cell_cx, panel_y + 6, val, self.font_label, TEXT_PRIMARY)
            self._text_center(draw, cell_cx, panel_y + 28, label, self.font_stat_label, TEXT_SECONDARY)

        rank_history = data.get("rank_history", [])
        graph_top = panel_y + panel_h + 20
        if len(rank_history) >= 2:
            graph_margin = 40
            graph_w = W - 2 * graph_margin
            graph_x = graph_margin
            graph_h = H - graph_top - 80
            new_draw = draw_line_graph(
                draw,
                img,
                rank_history,
                x=graph_x,
                y=graph_top,
                w=graph_w,
                h=graph_h,
                color=ACCENT_RED,
                font=self.font_small,
                invert=True,
                show_current_label=False,
                show_axis_labels=False,
            )
            if new_draw:
                draw = new_draw

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

        info_y = 44
        gap = 8
        pw = (W - PADDING_X * 2 - gap * 4) // 5
        ph = 42

        counts_all: list[int] = []
        if monthly and len(monthly) >= 2:
            counts_all = [int(entry.get("count", 0) or 0) for entry in monthly]

        min_c = min(counts_all) if counts_all else 0
        max_c = max(counts_all) if counts_all else 0
        avg_c = int(sum(counts_all) / len(counts_all)) if counts_all else 0

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
            labels: list[str] = []
            seen_years = set()
            for i, entry in enumerate(monthly):
                sd = entry.get("start_date") or ""
                try:
                    parts = str(sd).split("-")
                    yr = parts[0][2:]
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
                draw,
                img,
                counts_all,
                x=PADDING_X,
                y=graph_top,
                w=graph_w,
                h=graph_h,
                color=ACCENT_RED,
                font=self.font_small,
                invert=False,
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
        grade_w = 54
        info_x = 8 + grade_w

        for i, sc in enumerate(scores[:5]):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            rank_pos = i + 1
            if rank_pos <= 3:
                bar_color = TOP_COLORS.get(rank_pos, TEXT_PRIMARY)
                draw.rectangle([(0, ry), (4, ry + row_h)], fill=bar_color)

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

            grade = sc.get("rank", "F")
            grade_color = GRADE_COLORS.get(grade, TEXT_PRIMARY)
            grade_cx = 8 + grade_w // 2
            grade_font = self.font_row if grade == "SH" else self.font_grade
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, grade_font, grade_color)

            artist = sc.get("artist", "")
            title = sc.get("title", "")
            map_str = f"{artist} - {title}"
            if len(map_str) > 40:
                map_str = map_str[:37] + "..."
            draw.text((info_x, ry + 8), map_str, font=self.font_label, fill=TEXT_PRIMARY)

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

            acc = sc.get("accuracy", 0)
            combo = sc.get("max_combo", 0)
            mods = sc.get("mods", "")
            detail = f"{acc:.2f}% | {combo}x"
            detail_x = info_x
            draw.text((detail_x, ry + 48), detail, font=self.font_small, fill=TEXT_SECONDARY)

            if mods:
                detail_bbox = draw.textbbox((0, 0), detail + "  ", font=self.font_small)
                mod_x = detail_x + (detail_bbox[2] - detail_bbox[0])
                draw = self._draw_mod_badges(img, draw, mod_x, ry + 49, mods)

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

            grade = sc.get("rank", "F")
            grade_color = GRADE_COLORS.get(grade, TEXT_PRIMARY)
            grade_cx = 8 + grade_w // 2
            grade_font = self.font_row if grade == "SH" else self.font_grade
            self._text_center(draw, grade_cx, ry + (row_h - 40) // 2, grade, grade_font, grade_color)

            beatmapset = sc.get("beatmapset") or {}
            beatmap = sc.get("beatmap") or {}
            artist = beatmapset.get("artist", "")
            title = beatmapset.get("title", "")
            map_str = f"{artist} - {title}"
            if len(map_str) > 40:
                map_str = map_str[:37] + "..."
            draw.text((info_x, ry + 8), map_str, font=self.font_label, fill=TEXT_PRIMARY)

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

            acc_raw = sc.get("accuracy", 0)
            acc = acc_raw * 100 if acc_raw <= 1.0 else acc_raw
            combo = sc.get("max_combo", 0)
            mods_list = sc.get("mods", [])
            detail = f"{acc:.2f}% | {combo}x"
            draw.text((info_x, ry + 48), detail, font=self.font_small, fill=TEXT_SECONDARY)

            if mods_list:
                detail_bbox = draw.textbbox((0, 0), detail + "  ", font=self.font_small)
                mod_x = info_x + (detail_bbox[2] - detail_bbox[0])
                draw = self._draw_mod_badges(img, draw, mod_x, ry + 49, mods_list)

            pp = sc.get("pp") or 0
            pp_str = f"{pp:.0f}pp" if pp else "—"
            self._text_right(draw, W - PADDING_X, ry + (row_h - 22) // 2, pp_str, self.font_row, ACCENT_RED)

        return self._save(img)

    # Profile Dispatcher — async, downloads images

    async def generate_profile_page_async(self, page: int, data: Dict) -> BytesIO:
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
        if page == 1:
            return await asyncio.to_thread(self.generate_profile_rank_card, data)
        if page == 2:
            return await asyncio.to_thread(self.generate_profile_playcount_card, data)
        if page == 3:
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
                bg_images = [r if not isinstance(r, Exception) and r is not None else None for r in results]
            return await asyncio.to_thread(self.generate_profile_top_card, data, bg_images)
        if page == 4:
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
                bg_images = [r if not isinstance(r, Exception) and r is not None else None for r in results]
            return await asyncio.to_thread(self.generate_profile_recent_card, data, bg_images)

        return await asyncio.to_thread(self.generate_profile_info_card, data, avatar, cover)

