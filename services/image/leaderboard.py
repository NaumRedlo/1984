"""Leaderboard card generators."""

import asyncio
from io import BytesIO
from typing import List, Dict, Optional

from PIL import Image, ImageDraw, ImageChops

from services.image.core import (
    BaseCardRenderer,
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD,
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_RED,
    TOP_COLORS, PANEL_BG, GRADE_COLORS, MOD_COLORS,
    CARD_WIDTH, PADDING_X, VALUE_RIGHT_X,
    load_icon, load_flag,
    _none_coro, download_image,
    cover_center_crop, draw_cover_background, rounded_rect_crop,
)



class LeaderboardCardGenerator(BaseCardRenderer):
    """Leaderboard-specific PNG card."""

    # Podium column specs (order: #4, #2, #1, #3, #5)
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

        # Compact header
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
            av_sz = 40
            av_r = 10
            for i, entry in enumerate(entries):
                y_top = header_h + i * row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD

                # Cover background (dimmed)
                cover_img = self._image_from_bytes(entry.get("cover_data")) if entry.get("cover_data") else None
                if cover_img:
                    rc = cover_center_crop(cover_img, CARD_WIDTH, row_h)
                    rc_rgba = rc.convert("RGBA")
                    ov = Image.new("RGBA", (CARD_WIDTH, row_h), (0, 0, 0, 180))
                    rc_rgba = Image.alpha_composite(rc_rgba, ov)
                    img.paste(rc_rgba.convert("RGB"), (0, y_top))
                    draw = ImageDraw.Draw(img)
                else:
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

                # Avatar
                pos_text = f"#{position}"
                pos_bbox = draw.textbbox((0, 0), pos_text, font=self.font_row)
                pos_w = pos_bbox[2] - pos_bbox[0]
                av_x = 16 + pos_w + 8
                av_y = y_top + (row_h - av_sz) // 2
                avatar_img = entry.get("_avatar_img")
                if avatar_img:
                    av = rounded_rect_crop(avatar_img, av_sz, radius=av_r)
                    img.paste(av, (av_x, av_y), av)
                    draw = ImageDraw.Draw(img)
                outline_color = TOP_COLORS.get(position, TEXT_SECONDARY) if position <= 3 else TEXT_SECONDARY
                draw.rounded_rectangle(
                    (av_x - 1, av_y - 1, av_x + av_sz, av_y + av_sz),
                    radius=av_r, outline=outline_color, width=2,
                )

                flag_x = av_x + av_sz + 6
                flag = load_flag(country, height=20)
                if flag:
                    name_x = flag_x + flag.width + 6
                    uname_bbox = draw.textbbox((name_x, y_text), username, font=self.font_row)
                    uname_vcenter = (uname_bbox[1] + uname_bbox[3]) // 2
                    flag_y = uname_vcenter - flag.height // 2
                    img.paste(flag, (flag_x, flag_y), flag)
                    draw = ImageDraw.Draw(img)
                else:
                    name_x = flag_x + 6
                    draw.text((flag_x, y_text), f"[{country}]", font=self.font_small, fill=TEXT_SECONDARY)

                draw.text((name_x, y_text), username, font=self.font_row, fill=text_color)

                val_str = str(value)
                sub_val = entry.get("sub_value")
                value_x_offset = 4 if position >= 10 else 0

                if sub_val:
                    bbox = draw.textbbox((0, 0), val_str, font=self.font_row)
                    val_width = bbox[2] - bbox[0]
                    sub_bbox = draw.textbbox((0, 0), sub_val, font=self.font_small)
                    sub_width = sub_bbox[2] - sub_bbox[0]
                    val_x = VALUE_RIGHT_X - val_width - value_x_offset
                    sub_x = VALUE_RIGHT_X - sub_width - value_x_offset
                    draw.text((sub_x, y_top + 8), sub_val, font=self.font_small, fill=TEXT_SECONDARY)
                    draw.text((val_x, y_top + 26), val_str, font=self.font_row, fill=text_color)
                else:
                    bbox = draw.textbbox((0, 0), val_str, font=self.font_row)
                    val_width = bbox[2] - bbox[0]
                    draw.text((VALUE_RIGHT_X - val_width - value_x_offset, y_text), val_str, font=self.font_row, fill=text_color)

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

        # Header (0..36)
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
            else:
                # Fallback: gradient from rank color (dim) to panel bg
                rank_color = TOP_COLORS.get(rank, (100, 100, 120))
                cropped = Image.new("RGBA", (cw - 2, cover_h), (0, 0, 0, 0))
                for gy in range(cover_h):
                    t = gy / max(cover_h - 1, 1)
                    r = int(rank_color[0] * 0.3 * (1 - t) + PANEL_BG[0] * t)
                    g = int(rank_color[1] * 0.3 * (1 - t) + PANEL_BG[1] * t)
                    b = int(rank_color[2] * 0.3 * (1 - t) + PANEL_BG[2] * t)
                    ImageDraw.Draw(cropped).line([(0, gy), (cw - 2, gy)], fill=(r, g, b, 255))

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
                text_x = fx + flag.width + 4
                # Align flag to visual center of rendered text
                real_bbox = draw.textbbox((text_x, cur_y), display_name, font=name_font)
                text_vcenter = (real_bbox[1] + real_bbox[3]) // 2
                flag_y = text_vcenter - flag.height // 2
                img.paste(flag, (fx, flag_y), flag)
                draw = ImageDraw.Draw(img)
                draw.text((text_x, cur_y), display_name, font=name_font, fill=name_color)
            else:
                self._text_center(draw, col_cx, cur_y, display_name, name_font, name_color)

            # Category value — bottom of column, auto-scaled
            value_str = str(entry.get("value", "—"))
            sub_val = entry.get("sub_value")
            # Strip map name from best_pp values on podium
            if " — " in value_str:
                value_str = value_str.split(" — ")[0]

            val_color = TOP_COLORS.get(rank, TEXT_PRIMARY)

            if sub_val:
                # Two-line: sub_value (PP, smaller) above value (rank, main)
                if rank == 1:
                    val_font = self.font_stat_value
                    sub_font = self.font_label
                    val_y = y_top + ch - 36
                    sub_y = val_y - 20
                else:
                    val_font = self.font_row
                    sub_font = self.font_small
                    val_y = y_top + ch - 30
                    sub_y = val_y - 18
                self._text_center(draw, col_cx, sub_y, sub_val, sub_font, TEXT_SECONDARY)
                self._text_center(draw, col_cx, val_y, value_str, val_font, val_color)
            else:
                if rank == 1:
                    val_font = self.font_stat_value
                    val_y = y_top + ch - 38
                else:
                    val_font = self.font_label
                    val_y = y_top + ch - 32

                # Auto-scale: cascading font reduction to fit column width
                if rank == 1:
                    for fallback in (self.font_stat_value, self.font_row, self.font_label):
                        val_font = fallback
                        vbbox = draw.textbbox((0, 0), value_str, font=val_font)
                        if vbbox[2] - vbbox[0] <= cw - 8:
                            break
                else:
                    for fallback in (self.font_label, self.font_small):
                        val_font = fallback
                        vbbox = draw.textbbox((0, 0), value_str, font=val_font)
                        if vbbox[2] - vbbox[0] <= cw - 8:
                            break
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

    async def generate_map_leaderboard_card_async(self, data: Dict) -> BytesIO:
        cover = None
        cover_data = data.get("beatmap_cover_data")
        cover_url = data.get("beatmap_cover_url")
        if cover_data:
            cover = self._image_from_bytes(cover_data)
        elif cover_url:
            cover = await download_image(cover_url)

        mapper_avatar = None
        mapper_avatar_data = data.get("mapper_avatar_data")
        mapper_id = data.get("mapper_id")
        if mapper_avatar_data:
            mapper_avatar = self._image_from_bytes(mapper_avatar_data)
        elif mapper_id:
            mapper_avatar = await download_image(f"https://a.ppy.sh/{mapper_id}")

        # Pre-download avatars for extended rows
        all_rows = data.get("rows") or []
        page = int(data.get("page", 0) or 0)
        if page == 0:
            ext_rows = all_rows[3:3 + self.LBM_FIRST_PAGE_ROWS]
        else:
            offset = 3 + self.LBM_FIRST_PAGE_ROWS + (page - 1) * self.LBM_PAGE_ROWS
            ext_rows = all_rows[offset:offset + self.LBM_PAGE_ROWS]

        avatar_tasks = []
        for row in ext_rows:
            if row.get("avatar_data"):
                avatar_tasks.append(_none_coro())
            else:
                uid = row.get("osu_user_id")
                avatar_tasks.append(download_image(f"https://a.ppy.sh/{uid}") if uid else _none_coro())

        avatar_results = await asyncio.gather(*avatar_tasks, return_exceptions=True)
        for i, row in enumerate(ext_rows):
            if row.get("avatar_data"):
                row["_avatar_img"] = self._image_from_bytes(row["avatar_data"])
            else:
                r = avatar_results[i]
                row["_avatar_img"] = r if not isinstance(r, Exception) else None

        payload = dict(data)
        payload["beatmap_cover_data"] = cover if cover else None
        payload["mapper_avatar_data"] = mapper_avatar if mapper_avatar else None
        return await asyncio.to_thread(self.generate_map_leaderboard_card, payload)

    @staticmethod
    def _filter_mods(mods_str: str) -> str:
        """Filter out CL (Classic) mod from display — it's auto-added by lazer."""
        if not mods_str or mods_str == "—":
            return mods_str
        parts = [m.strip() for m in mods_str.replace("+", "").split(",") if m.strip() and m.strip() != "CL"]
        return ",".join(parts) if parts else "—"

    # Pagination constants for map leaderboard
    LBM_FIRST_PAGE_ROWS = 6   # positions 4-9 on page 0 (with podium)
    LBM_PAGE_ROWS = 5         # rows per subsequent page

    def generate_map_leaderboard_card(self, data: Dict) -> BytesIO:
        map_title = data.get("map_title", "Unknown Map")
        map_version = data.get("map_version", "Unknown")
        beatmap_id = data.get("beatmap_id", 0)
        star_rating = float(data.get("star_rating", 0.0) or 0.0)
        bpm = float(data.get("bpm", 0.0) or 0.0)
        total_length = int(data.get("total_length", 0) or 0)
        total_plays = int(data.get("total_plays", 0) or 0)
        unique_players = int(data.get("unique_players", 0) or 0)
        all_rows = data.get("rows", []) or []
        page = int(data.get("page", 0) or 0)
        cover_data = data.get("beatmap_cover_data")
        cover = cover_data if isinstance(cover_data, Image.Image) else self._image_from_bytes(cover_data)

        def _shadow_text(draw_obj, xy, text, font, fill):
            sx, sy = xy
            draw_obj.text((sx + 1, sy + 1), text, font=font, fill=(0, 0, 0))
            draw_obj.text((sx, sy), text, font=font, fill=fill)

        # Determine which rows to show on this page
        is_first_page = page == 0
        if is_first_page:
            show_podium = True
            extended_rows = all_rows[3:3 + self.LBM_FIRST_PAGE_ROWS]
        else:
            show_podium = False
            offset = 3 + self.LBM_FIRST_PAGE_ROWS + (page - 1) * self.LBM_PAGE_ROWS
            extended_rows = all_rows[offset:offset + self.LBM_PAGE_ROWS]

        W = CARD_WIDTH
        header_h = 36
        hero_h = 120 if is_first_page else 0
        podium_h = (200 if all_rows else 48) if show_podium else 0
        row_h = 44
        footer_h = 32
        sep_h = 2 if (show_podium and extended_rows) else 0
        card_h = header_h + hero_h + podium_h + sep_h + len(extended_rows) * row_h + footer_h
        if not is_first_page and not extended_rows:
            card_h = header_h + footer_h + 48  # empty page

        img = Image.new("RGB", (W, card_h), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── HEADER ──
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — MAP LEADERBOARD", self.font_subtitle, ACCENT_RED)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        status_color = ACCENT_RED
        content_y = header_h

        # ── HERO / COVER (only page 0) ──
        if is_first_page:
            hero_y = content_y
            if cover:
                cropped = cover_center_crop(cover, W, hero_h)
                hero_rgba = cropped.convert("RGBA")
                overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, 120))
                hero_rgba = Image.alpha_composite(hero_rgba, overlay)
                img.paste(hero_rgba.convert("RGB"), (0, hero_y))
                draw = ImageDraw.Draw(img)
            else:
                draw.rectangle([(0, hero_y), (W, hero_y + hero_h)], fill=HEADER_BG)

            # Map title
            title_text = map_title
            title_bbox = draw.textbbox((0, 0), title_text, font=self.font_row)
            if title_bbox[2] - title_bbox[0] > 540:
                while title_bbox[2] - title_bbox[0] > 536 and len(title_text) > 6:
                    title_text = title_text[:-1]
                    title_bbox = draw.textbbox((0, 0), title_text + "...", font=self.font_row)
                title_text += "..."
            _shadow_text(draw, (PADDING_X, hero_y + 8), title_text, self.font_row, TEXT_PRIMARY)

            # Mapper avatar + name
            mapper_name = data.get("mapper_name", "Unknown")
            mapper_avatar = data.get("mapper_avatar_data")
            mav = mapper_avatar if isinstance(mapper_avatar, Image.Image) else self._image_from_bytes(mapper_avatar)
            mav_y = hero_y + 34
            if mav:
                av = rounded_rect_crop(mav, 28, radius=6)
                img.paste(av, (PADDING_X, mav_y), av)
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((PADDING_X - 1, mav_y - 1, PADDING_X + 29, mav_y + 29), radius=6, outline=TEXT_SECONDARY, width=2)
                draw.text((PADDING_X + 36, mav_y), "mapped by", font=self.font_stat_label, fill=TEXT_SECONDARY)
                draw.text((PADDING_X + 36, mav_y + 14), mapper_name, font=self.font_small, fill=(200, 200, 210))
            else:
                _shadow_text(draw, (PADDING_X, mav_y + 4), f"mapped by {mapper_name}", self.font_small, TEXT_SECONDARY)

            # Stars / BPM / Length
            info_y = hero_y + 70
            info_x = PADDING_X
            for icon_name, val in [
                ("star", f"{star_rating:.2f}" if star_rating else "—"),
                ("bpm", f"{bpm:.0f}" if bpm else "—"),
                ("timer", f"{total_length // 60}:{total_length % 60:02d}" if total_length else "—"),
            ]:
                icon = load_icon(icon_name, size=14)
                if icon:
                    img.paste(icon, (info_x, info_y + 2), icon)
                    draw = ImageDraw.Draw(img)
                    info_x += icon.width + 4
                _shadow_text(draw, (info_x, info_y), val, self.font_label, TEXT_PRIMARY)
                val_bbox = draw.textbbox((0, 0), val, font=self.font_label)
                info_x += val_bbox[2] - val_bbox[0] + 16

            # [version] + status badge
            version = map_version
            ver_y = hero_y + 94
            ver_text = f"[{version}]"
            ver_bbox = draw.textbbox((0, 0), ver_text, font=self.font_small)
            if ver_bbox[2] - ver_bbox[0] > 260:
                while ver_bbox[2] - ver_bbox[0] > 256 and len(version) > 4:
                    version = version[:-1]
                    ver_bbox = draw.textbbox((0, 0), f"[{version}...]", font=self.font_small)
                ver_text = f"[{version}...]"
            _shadow_text(draw, (PADDING_X, ver_y), ver_text, self.font_small, TEXT_SECONDARY)

            STATUS_COLORS = {
                'ranked': (80, 180, 80), 'approved': (80, 180, 80),
                'qualified': (80, 140, 220), 'loved': (220, 100, 160),
                'pending': (200, 180, 50), 'wip': (200, 180, 50),
                'graveyard': (100, 100, 100),
            }
            STATUS_INT_MAP = {
                4: 'loved', 3: 'qualified', 2: 'approved', 1: 'ranked',
                0: 'pending', -1: 'wip', -2: 'graveyard',
            }
            raw_status = data.get("beatmap_status", "")
            beatmap_status = STATUS_INT_MAP.get(raw_status, "") if isinstance(raw_status, int) else (str(raw_status) if raw_status else "")
            if beatmap_status:
                status_label = beatmap_status.upper()
                status_color = STATUS_COLORS.get(beatmap_status.lower(), (100, 100, 120))
                ver_end_bbox = draw.textbbox((0, 0), ver_text, font=self.font_small)
                status_x = PADDING_X + ver_end_bbox[2] - ver_end_bbox[0] + 10
                sb_bbox = draw.textbbox((0, 0), status_label, font=self.font_stat_label)
                sb_w = sb_bbox[2] - sb_bbox[0] + 12
                draw.rounded_rectangle((status_x, ver_y + 1, status_x + sb_w, ver_y + 19), radius=4, fill=status_color)
                self._text_center(draw, status_x + sb_w // 2, ver_y + 2, status_label, self.font_stat_label, (255, 255, 255))

            # Beatmap ID top-right
            id_text = f"ID: {beatmap_id}"
            id_bbox = draw.textbbox((0, 0), id_text, font=self.font_small)
            _shadow_text(draw, (W - PADDING_X - (id_bbox[2] - id_bbox[0]), hero_y + 8), id_text, self.font_small, TEXT_SECONDARY)

            draw.line([(0, hero_y + hero_h), (W, hero_y + hero_h)], fill=status_color, width=2)
            content_y = hero_y + hero_h

        # ── PODIUM (page 0 only) ──
        if show_podium:
            podium_y = content_y + 6
            top_rows = all_rows[:3]

            if not all_rows:
                self._text_center(draw, W // 2, podium_y + 14, "No plays from registered users yet.", self.font_row, TEXT_SECONDARY)
            else:
                col_order = []
                if len(top_rows) >= 2:
                    col_order.append((top_rows[1], 176, 160, 52))
                if len(top_rows) >= 1:
                    col_order.append((top_rows[0], 224, 180, 60))
                if len(top_rows) >= 3:
                    col_order.append((top_rows[2], 176, 160, 52))

                gaps = 10
                total_w = sum(c[1] for c in col_order) + gaps * (len(col_order) - 1)
                start_x = (W - total_w) // 2
                cur_x = start_x

                for row, width, height, avatar_size in col_order:
                    rank = row.get("position", 1)
                    x = cur_x
                    cur_x += width + gaps
                    y = podium_y + (180 - height)
                    stripe = TOP_COLORS.get(rank, ACCENT_RED)

                    panel = Image.new("RGB", (width, height), PANEL_BG)
                    cover_img = self._image_from_bytes(row.get("cover_data")) if row.get("cover_data") else None
                    if cover_img:
                        draw_cover_background(panel, cover_img, 0, height, width)
                        ov = Image.new("RGBA", (width, height), (0, 0, 0, 160))
                        panel.paste(ov.convert("RGB"), (0, 0), ov)
                    ImageDraw.Draw(panel).rounded_rectangle((0, 0, width - 1, height - 1), radius=14, outline=stripe, width=2)
                    img.paste(panel, (x, y))
                    draw = ImageDraw.Draw(img)

                    av_x = x + (width - avatar_size) // 2
                    av_y = y + 14
                    bg_sz = avatar_size + 12
                    bg_x = x + (width - bg_sz) // 2
                    draw.rounded_rectangle((bg_x, av_y - 6, bg_x + bg_sz, av_y - 6 + bg_sz), radius=16, fill=(18, 18, 26))
                    avatar_img = self._image_from_bytes(row.get("avatar_data"))
                    if avatar_img:
                        av = rounded_rect_crop(avatar_img, avatar_size, radius=14)
                        img.paste(av, (av_x, av_y), av)
                        draw = ImageDraw.Draw(img)
                    draw.rounded_rectangle((av_x - 1, av_y - 1, av_x + avatar_size + 1, av_y + avatar_size + 1), radius=14, outline=stripe, width=3)

                    cur_text_y = av_y + avatar_size + 6
                    self._text_center(draw, x + width // 2, cur_text_y, f"#{rank}", self.font_row, stripe)
                    cur_text_y += 22

                    country = row.get("country", "XX")
                    username = row.get("username", "???")
                    flag = load_flag(country, height=16)
                    name_font = self.font_row if rank == 1 else self.font_label
                    max_name_w = width - 12
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
                    if flag:
                        total_fw = flag.width + 4 + name_w
                        fx = x + (width - total_fw) // 2
                        real_bbox = draw.textbbox((fx + flag.width + 4, cur_text_y), display_name, font=name_font)
                        text_vc = (real_bbox[1] + real_bbox[3]) // 2
                        img.paste(flag, (fx, text_vc - flag.height // 2), flag)
                        draw = ImageDraw.Draw(img)
                        draw.text((fx + flag.width + 4, cur_text_y), display_name, font=name_font, fill=stripe)
                    else:
                        self._text_center(draw, x + width // 2, cur_text_y, display_name, name_font, stripe)

                    # PP centered between name and bottom detail — same layout for all ranks
                    pp_val = float(row.get("pp", 0) or 0)
                    pp_color = TOP_COLORS.get(rank, TEXT_PRIMARY)
                    pp_font = self.font_stat_value if rank == 1 else self.font_row
                    name_bottom = cur_text_y + 20
                    detail_top = y + height - 24
                    pp_y = (name_bottom + detail_top) // 2 - 10
                    if rank in (2, 3):
                        pp_y -= 4
                    self._text_center(draw, x + width // 2, pp_y, f"{pp_val:.0f}pp", pp_font, pp_color)

                    # Accuracy + combo — same position for all ranks
                    acc_val = float(row.get("accuracy", 0) or 0)
                    combo_val = int(row.get("combo", 0) or 0)
                    detail_y = y + height - 26
                    self._text_center(draw, x + width // 2, detail_y, f"{acc_val:.2f}%  {combo_val}x", self.font_stat_label, TEXT_SECONDARY)

                    # Grade badge at top-right (mini version of rs card badge)
                    grade = row.get("rank", "F")
                    grade_color = GRADE_COLORS.get(grade, TEXT_SECONDARY)
                    badge_r = 14
                    badge_cx = x + width - badge_r - 5
                    badge_cy = y + badge_r + 5
                    glow_r = int(grade_color[0] * 0.15)
                    glow_g = int(grade_color[1] * 0.15)
                    glow_b = int(grade_color[2] * 0.15)
                    badge_img = Image.new('RGBA', (badge_r * 2, badge_r * 2), (0, 0, 0, 0))
                    badge_draw = ImageDraw.Draw(badge_img)
                    badge_draw.ellipse((0, 0, badge_r * 2 - 1, badge_r * 2 - 1), fill=(glow_r, glow_g, glow_b, 200))
                    outline_c = (min(grade_color[0], 255), min(grade_color[1], 255), min(grade_color[2], 255), 160)
                    badge_draw.ellipse((2, 2, badge_r * 2 - 3, badge_r * 2 - 3), outline=outline_c, width=2)
                    img.paste(badge_img, (badge_cx - badge_r, badge_cy - badge_r), badge_img)
                    draw = ImageDraw.Draw(img)
                    grade_font = self.font_stat_label
                    gb = draw.textbbox((0, 0), grade, font=grade_font)
                    gtw = gb[2] - gb[0]
                    gth = gb[3] - gb[1]
                    gtx = badge_cx - gtw // 2
                    gty = badge_cy - gth // 2 - gb[1]
                    draw.text((gtx, gty), grade, font=grade_font, fill=grade_color)

                    # Mod badges vertical
                    mods_raw = self._filter_mods(str(row.get("mods", "")).strip())
                    if mods_raw and mods_raw != "—":
                        mod_x = x + 6
                        mod_cur_y = y + 6
                        for mod_name in [m.strip() for m in mods_raw.split(",") if m.strip()]:
                            if mod_cur_y + 16 > y + height // 2:
                                break
                            mc = MOD_COLORS.get(mod_name, (100, 100, 120))
                            draw.rounded_rectangle((mod_x, mod_cur_y, mod_x + 32, mod_cur_y + 16), radius=10, fill=mc)
                            self._text_center(draw, mod_x + 16, mod_cur_y + 1, mod_name, self.font_stat_label, (255, 255, 255))
                            mod_cur_y += 20

            content_y = podium_y + podium_h

        # Red separator between podium and extended rows
        if show_podium and extended_rows:
            draw.line([(0, content_y), (W, content_y)], fill=ACCENT_RED, width=2)
            content_y += 2

        # ── EXTENDED ROWS ──
        av_sz = 30  # avatar size for extended rows
        av_r = 8    # avatar corner radius
        list_y = content_y
        for i, row in enumerate(extended_rows):
            y_top = list_y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD

            # Cover background behind row (dimmed)
            row_cover_img = self._image_from_bytes(row.get("cover_data")) if row.get("cover_data") else None
            if row_cover_img:
                rc = cover_center_crop(row_cover_img, W, row_h)
                rc_rgba = rc.convert("RGBA")
                ov = Image.new("RGBA", (W, row_h), (0, 0, 0, 180))
                rc_rgba = Image.alpha_composite(rc_rgba, ov)
                img.paste(rc_rgba.convert("RGB"), (0, y_top))
                draw = ImageDraw.Draw(img)
            else:
                draw.rectangle([(0, y_top), (W, y_top + row_h)], fill=row_bg)

            draw.rectangle([(0, y_top), (4, y_top + row_h)], fill=ACCENT_RED)

            pos = row.get("position", i + 4)
            y_text = y_top + (row_h - 22) // 2

            # Position number
            pos_text = f"#{pos}"
            pos_bbox = draw.textbbox((0, 0), pos_text, font=self.font_row)
            pos_w = pos_bbox[2] - pos_bbox[0]
            draw.text((12, y_text), pos_text, font=self.font_row, fill=TEXT_PRIMARY)

            # Avatar (small, rounded, red outline)
            av_x = 12 + pos_w + 8
            av_y = y_top + (row_h - av_sz) // 2
            avatar_img = row.get("_avatar_img")
            if avatar_img:
                av = rounded_rect_crop(avatar_img, av_sz, radius=av_r)
                img.paste(av, (av_x, av_y), av)
                draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (av_x - 1, av_y - 1, av_x + av_sz, av_y + av_sz),
                radius=av_r, outline=TEXT_SECONDARY, width=2,
            )

            flag_x = av_x + av_sz + 6
            flag = load_flag(row.get("country", "XX"), height=18)
            if flag:
                flag_y = y_text + (22 - flag.height) // 2
                img.paste(flag, (flag_x, flag_y), flag)
                draw = ImageDraw.Draw(img)
                name_x = flag_x + flag.width + 6
            else:
                name_x = flag_x + 6

            draw.text((name_x, y_text), row.get("username", "???"), font=self.font_row, fill=TEXT_PRIMARY)

            # Right-aligned: grade | pp acc combo [mod badges]
            pp_val = float(row.get("pp", 0) or 0)
            acc_val = float(row.get("accuracy", 0) or 0)
            combo_val = int(row.get("combo", 0) or 0)
            mods_raw = self._filter_mods(str(row.get("mods", "")).strip())

            # Grade
            grade = row.get("rank", "F")
            grade_color = GRADE_COLORS.get(grade, TEXT_SECONDARY)
            gb = draw.textbbox((0, 0), grade, font=self.font_row)
            grade_w = gb[2] - gb[0]
            draw.text((VALUE_RIGHT_X - grade_w, y_text), grade, font=self.font_row, fill=grade_color)

            # Stats: pp bold, then acc combo smaller, then mod badges
            pp_str = f"{pp_val:.0f}pp"
            detail_str = f"{acc_val:.2f}%  {combo_val}x"

            # Measure mod badges width to lay out right-to-left
            mod_badges_w = 0
            mod_names = []
            if mods_raw and mods_raw != "—":
                mod_names = [m.strip() for m in mods_raw.split(",") if m.strip()]
                for mn in mod_names:
                    mb = draw.textbbox((0, 0), mn, font=self.font_stat_label)
                    mod_badges_w += (mb[2] - mb[0] + 10) + 4
                if mod_badges_w > 0:
                    mod_badges_w += 4  # gap before badges

            pp_bbox = draw.textbbox((0, 0), pp_str, font=self.font_row)
            pp_w = pp_bbox[2] - pp_bbox[0]
            detail_bbox = draw.textbbox((0, 0), detail_str, font=self.font_small)
            detail_w = detail_bbox[2] - detail_bbox[0]

            total_val_w = pp_w + 8 + detail_w + mod_badges_w
            val_start_x = VALUE_RIGHT_X - grade_w - 12 - total_val_w
            draw.text((val_start_x, y_text), pp_str, font=self.font_row, fill=TEXT_PRIMARY)
            detail_x = val_start_x + pp_w + 8
            draw.text((detail_x, y_text + 4), detail_str, font=self.font_small, fill=TEXT_SECONDARY)

            if mod_names:
                badge_x = detail_x + detail_w + 4
                draw = self._draw_mod_badges(img, draw, badge_x, y_text + 3, ",".join(mod_names))

        rows_bottom_y = list_y + len(extended_rows) * row_h

        # ── FOOTER ──
        footer_y = rows_bottom_y
        draw.line([(0, footer_y), (W, footer_y)], fill=ACCENT_RED, width=1)
        footer_text = f"{unique_players} players \u00b7 {total_plays} plays"
        self._text_center(draw, W // 2, footer_y + 6, footer_text, self.font_small, TEXT_SECONDARY)

        return self._save(img)

    async def generate_leaderboard_card_async(
        self, category_label: str, entries: List[Dict]
    ) -> BytesIO:
        is_first_page = entries and entries[0].get("position", 1) == 1

        # Download avatars for all entries (podium + compact rows)
        avatar_tasks = []
        for e in entries:
            if e.get("avatar_data"):
                avatar_tasks.append(_none_coro())
            else:
                uid = e.get("osu_user_id")
                avatar_tasks.append(download_image(f"https://a.ppy.sh/{uid}") if uid else _none_coro())

        avatar_results = await asyncio.gather(*avatar_tasks, return_exceptions=True)
        for i, e in enumerate(entries):
            if e.get("avatar_data"):
                e["_avatar_img"] = self._image_from_bytes(e["avatar_data"])
            else:
                r = avatar_results[i]
                e["_avatar_img"] = r if not isinstance(r, Exception) else None

        if is_first_page:
            # Download covers for podium entries
            cover_tasks = []
            for e in entries:
                if e.get("cover_data"):
                    cover_tasks.append(_none_coro())
                else:
                    cover_tasks.append(download_image(e.get("cover_url")) if e.get("cover_url") else _none_coro())

            cover_results = await asyncio.gather(*cover_tasks, return_exceptions=True)

            avatars = []
            covers = []
            for i, e in enumerate(entries):
                avatars.append(e.get("_avatar_img"))
                if e.get("cover_data"):
                    covers.append(self._image_from_bytes(e["cover_data"]))
                else:
                    r = cover_results[i]
                    covers.append(r if not isinstance(r, Exception) else None)

            return await asyncio.to_thread(
                self.generate_leaderboard_card, category_label, entries, avatars, covers
            )
        return await asyncio.to_thread(
            self.generate_leaderboard_card, category_label, entries
        )

