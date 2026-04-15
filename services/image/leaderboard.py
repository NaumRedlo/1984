"""Leaderboard card generators."""

import asyncio
from io import BytesIO
from typing import List, Dict, Optional

from PIL import Image, ImageDraw, ImageChops

from services.image.core import (
    BaseCardRenderer,
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD,
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_RED,
    TOP_COLORS, PANEL_BG,
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
                name_x = 96 if position < 10 else 104
                flag_x = 58 if position < 10 else 66
                if flag:
                    uname_bbox = draw.textbbox((name_x, y_text), username, font=self.font_row)
                    uname_vcenter = (uname_bbox[1] + uname_bbox[3]) // 2
                    flag_y = uname_vcenter - flag.height // 2
                    img.paste(flag, (flag_x, flag_y), flag)
                    draw = ImageDraw.Draw(img)
                else:
                    draw.text((flag_x, y_text), f"[{country}]", font=self.font_small, fill=TEXT_SECONDARY)

                draw.text((name_x, y_text), username, font=self.font_row, fill=text_color)

                val_str = str(value)
                bbox = draw.textbbox((0, 0), val_str, font=self.font_row)
                val_width = bbox[2] - bbox[0]
                value_x_offset = 4 if position >= 10 else 0
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

        payload = dict(data)
        payload["beatmap_cover_data"] = cover if cover else None
        payload["mapper_avatar_data"] = mapper_avatar if mapper_avatar else None
        return await asyncio.to_thread(self.generate_map_leaderboard_card, payload)

    def generate_map_leaderboard_card(self, data: Dict) -> BytesIO:
        map_title = data.get("map_title", "Unknown Map")
        map_version = data.get("map_version", "Unknown")
        beatmap_id = data.get("beatmap_id", 0)
        star_rating = float(data.get("star_rating", 0.0) or 0.0)
        bpm = float(data.get("bpm", 0.0) or 0.0)
        total_length = int(data.get("total_length", 0) or 0)
        rows = data.get("rows", []) or []
        cover_data = data.get("beatmap_cover_data")
        cover = cover_data if isinstance(cover_data, Image.Image) else self._image_from_bytes(cover_data)

        row_h = 46
        stats_h = 44
        podium_h = 172
        header_h = 176
        extra_rows = max(len(rows) - 3, 0)
        card_h = header_h + stats_h + podium_h + extra_rows * row_h + 22
        img = Image.new("RGB", (CARD_WIDTH, card_h), BG_COLOR)
        draw = ImageDraw.Draw(img)

        if cover:
            draw_cover_background(img, cover, 0, header_h, CARD_WIDTH)
            draw = ImageDraw.Draw(img)
            overlay = Image.new("RGBA", (CARD_WIDTH, header_h), (0, 0, 0, 155))
            img.paste(overlay.convert("RGB"), (0, 0), overlay)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, 0), (CARD_WIDTH, header_h)], fill=HEADER_BG)

        id_text = f"ID: {beatmap_id}"
        self._text_right(draw, CARD_WIDTH - PADDING_X, 12, id_text, self.font_small, TEXT_SECONDARY)
        self._text_center(draw, CARD_WIDTH // 2, 42, map_title, self.font_row, TEXT_PRIMARY)
        mapper_name = data.get('mapper_name', 'Unknown')
        mapper_id = data.get('mapper_id', 0)
        if mapper_id:
            mapper_avatar = self._image_from_bytes(data.get("mapper_avatar_data"))
            if mapper_avatar:
                avatar_size = 38
                avatar_x = PADDING_X
                avatar_y = 42
                mav = rounded_rect_crop(mapper_avatar, avatar_size, radius=10)
                img.paste(mav, (avatar_x, avatar_y), mav)
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((avatar_x - 1, avatar_y - 1, avatar_x + avatar_size + 1, avatar_y + avatar_size + 1), radius=10, outline=ACCENT_RED, width=2)
                draw.text((avatar_x + avatar_size + 8, avatar_y + 11), f"mapped by {mapper_name}", font=self.font_small, fill=TEXT_SECONDARY)
            else:
                self._text_center(draw, CARD_WIDTH // 2, 52, f"mapped by {mapper_name}", self.font_small, TEXT_SECONDARY)
        else:
            self._text_center(draw, CARD_WIDTH // 2, 52, f"mapped by {mapper_name}", self.font_small, TEXT_SECONDARY)
        self._text_center(draw, CARD_WIDTH // 2, 82, f"[{map_version}]", self.font_small, TEXT_SECONDARY)

        stats_y = header_h + 8
        stat_w = (CARD_WIDTH - PADDING_X * 2 - 12) // 3
        stat_items = [
            (f"{bpm:.0f}" if bpm else "—", "BPM"),
            (f"{total_length // 60}:{total_length % 60:02d}" if total_length else "—", "TIME"),
            (f"{star_rating:.2f}" if star_rating else "—", "STARS"),
        ]
        icon_map = {
            "BPM": "bpm",
            "TIME": "timer",
            "STARS": "star",
        }
        for i, (value, label) in enumerate(stat_items):
            x = PADDING_X + i * (stat_w + 6)
            self._draw_panel(draw, x, stats_y, stat_w, stats_h)
            icon = load_icon(icon_map[label], size=18)
            if icon:
                icon_x = x + (stat_w - icon.width) // 2
                icon_y = stats_y + 2
                img.paste(icon, (icon_x, icon_y), icon)
                draw = ImageDraw.Draw(img)
            self._text_center(draw, x + stat_w // 2, stats_y + 22, value, self.font_row, TEXT_PRIMARY)

        podium_y = stats_y + stats_h + 38
        top_rows = rows[:3]
        widths = [224, 176, 176]
        heights = [164, 150, 150]
        gaps = 10
        total_w = sum(widths) + gaps * 2
        start_x = (CARD_WIDTH - total_w) // 2
        current_x = start_x
        for idx, row in enumerate(top_rows):
            rank = row.get("position", idx + 1)
            width = widths[idx]
            height = heights[idx]
            x = current_x
            current_x += width + gaps
            y = podium_y + (164 - height)

            panel = Image.new("RGB", (width, height), PANEL_BG)
            panel_draw = ImageDraw.Draw(panel)
            cover_img = self._image_from_bytes(row.get("cover_data")) if row.get("cover_data") else None
            if cover_img:
                draw_cover_background(panel, cover_img, 0, height, width)
                panel_draw = ImageDraw.Draw(panel)
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 160))
                panel.paste(overlay.convert("RGB"), (0, 0), overlay)
                panel_draw = ImageDraw.Draw(panel)
            stripe = TOP_COLORS.get(rank, ACCENT_RED)
            panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=14, outline=stripe, width=2)
            img.paste(panel, (x, y))
            draw = ImageDraw.Draw(img)

            avatar_size = 60 if rank == 1 else 52
            avatar_x = x + (width - avatar_size) // 2
            avatar_y = y + 18
            profile_bg_size = avatar_size + 12
            profile_bg_x = x + (width - profile_bg_size) // 2
            profile_bg_y = avatar_y - 6
            draw.rounded_rectangle((profile_bg_x, profile_bg_y, profile_bg_x + profile_bg_size, profile_bg_y + profile_bg_size), radius=16, fill=(18, 18, 26))
            avatar_img = self._image_from_bytes(row.get("avatar_data"))
            if avatar_img:
                av = rounded_rect_crop(avatar_img, avatar_size, radius=14)
                img.paste(av, (avatar_x, avatar_y), av)
                draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((avatar_x - 1, avatar_y - 1, avatar_x + avatar_size + 1, avatar_y + avatar_size + 1), radius=14, outline=stripe, width=3)

            self._text_center(draw, x + width // 2, avatar_y + avatar_size + 8, f"#{rank}", self.font_row, stripe)
            flag = load_flag(row.get("country", "XX"), height=18)
            name_y = avatar_y + avatar_size + (46 if rank == 1 else 40)
            username = row.get("username", "???")
            if flag:
                name_bbox = draw.textbbox((0, 0), username, font=self.font_row)
                name_w = name_bbox[2] - name_bbox[0]
                total_w_name = flag.width + 4 + name_w
                fx = x + (width - total_w_name) // 2
                fy = name_y + 1
                img.paste(flag, (fx, fy), flag)
                draw = ImageDraw.Draw(img)
                name_color = stripe
                draw.text((fx + flag.width + 4, name_y), username, font=self.font_row, fill=name_color)
            else:
                self._text_center(draw, x + width // 2, name_y, username, self.font_row, stripe)

            value_y = y + height - 28
            self._text_center(draw, x + width // 2, value_y, value, self.font_label, TEXT_PRIMARY)

            parts = [
                f"{float(row.get('pp', 0) or 0):.0f}pp",
                f"{float(row.get('accuracy', 0) or 0):.2f}%",
                f"{int(row.get('combo', 0) or 0)}x",
            ]
            mods = str(row.get("mods", "")).strip()
            if mods and mods != "—":
                parts.append(mods)
            value = " | ".join(part for part in parts if part)
            self._text_center(draw, x + width // 2, y + height - 26, value, self.font_label, TEXT_PRIMARY)

        if not rows:
            draw.text((PADDING_X, podium_y + 18), "No registered users have played this map yet.", font=self.font_row, fill=TEXT_SECONDARY)
            return self._save(img)

        if len(rows) > 3:
            list_y = podium_y + 168
            for idx, row in enumerate(rows[3:], start=4):
                y_top = list_y + (idx - 4) * row_h
                draw.rectangle([(0, y_top), (CARD_WIDTH, y_top + row_h)], fill=ROW_EVEN if idx % 2 == 0 else ROW_ODD)
                if idx <= 6:
                    draw.rectangle([(0, y_top), (4, y_top + row_h)], fill=TOP_COLORS.get(min(idx, 3), ACCENT_RED))
                pos = row.get("position", idx)
                draw.text((16, y_top + 12), f"#{pos}", font=self.font_row, fill=TOP_COLORS.get(pos, TEXT_PRIMARY))
                flag = load_flag(row.get("country", "XX"), height=18)
                if flag:
                    img.paste(flag, (58, y_top + 13), flag)
                    draw = ImageDraw.Draw(img)
                else:
                    draw.text((58, y_top + 12), f"[{row.get('country', 'XX')}]")
                draw.text((96, y_top + 12), row.get("username", "???"), font=self.font_row, fill=TOP_COLORS.get(pos, TEXT_PRIMARY))
                value = str(row.get("value", "—"))
                bbox = draw.textbbox((0, 0), value, font=self.font_row)
                draw.text((VALUE_RIGHT_X - (bbox[2] - bbox[0]), y_top + 12), value, font=self.font_row, fill=TEXT_PRIMARY)

        return self._save(img)

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

