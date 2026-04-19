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

    @staticmethod
    def _filter_mods(mods_str: str) -> str:
        """Filter out CL (Classic) mod from display — it's auto-added by lazer."""
        if not mods_str or mods_str == "—":
            return mods_str
        parts = [m.strip() for m in mods_str.replace("+", "").split(",") if m.strip() and m.strip() != "CL"]
        return ",".join(parts) if parts else "—"

    def generate_map_leaderboard_card(self, data: Dict) -> BytesIO:
        map_title = data.get("map_title", "Unknown Map")
        map_version = data.get("map_version", "Unknown")
        beatmap_id = data.get("beatmap_id", 0)
        star_rating = float(data.get("star_rating", 0.0) or 0.0)
        bpm = float(data.get("bpm", 0.0) or 0.0)
        total_length = int(data.get("total_length", 0) or 0)
        total_plays = int(data.get("total_plays", 0) or 0)
        unique_players = int(data.get("unique_players", 0) or 0)
        rows = data.get("rows", []) or []
        cover_data = data.get("beatmap_cover_data")
        cover = cover_data if isinstance(cover_data, Image.Image) else self._image_from_bytes(cover_data)

        def _shadow_text(draw_obj, xy, text, font, fill):
            sx, sy = xy
            draw_obj.text((sx + 1, sy + 1), text, font=font, fill=(0, 0, 0))
            draw_obj.text((sx, sy), text, font=font, fill=fill)

        W = CARD_WIDTH
        header_h = 36
        hero_h = 120
        podium_h = 200 if rows else 48
        row_h = 44
        extra_rows = max(len(rows) - 3, 0)
        footer_h = 28
        card_h = header_h + hero_h + podium_h + extra_rows * row_h + footer_h

        img = Image.new("RGB", (W, card_h), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── HEADER (0..36) ──
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — MAP LEADERBOARD", self.font_subtitle, ACCENT_RED)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        # ── HERO / COVER (36..156) ──
        hero_y = header_h
        if cover:
            cropped = cover_center_crop(cover, W, hero_h)
            img.paste(cropped.convert("RGB"), (0, hero_y))
            draw = ImageDraw.Draw(img)
            overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, 110))
            img.paste(overlay.convert("RGB"), (0, hero_y), overlay)
            for gx in range(360):
                alpha = int(80 * (1 - gx / 360))
                if alpha > 0:
                    draw.line([(gx, hero_y), (gx, hero_y + hero_h)], fill=(0, 0, 0, alpha))
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
        if isinstance(mapper_avatar, Image.Image):
            mav = mapper_avatar
        else:
            mav = self._image_from_bytes(mapper_avatar)
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

        # Stars / BPM / Length row
        info_y = hero_y + 70
        info_x = PADDING_X
        stat_pairs = [
            ("star", f"{star_rating:.2f}" if star_rating else "—"),
            ("bpm", f"{bpm:.0f}" if bpm else "—"),
            ("timer", f"{total_length // 60}:{total_length % 60:02d}" if total_length else "—"),
        ]
        for icon_name, val in stat_pairs:
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

        # Status badge
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
        if isinstance(raw_status, int):
            beatmap_status = STATUS_INT_MAP.get(raw_status, "")
        else:
            beatmap_status = str(raw_status) if raw_status else ""
        status_color = ACCENT_RED
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

        # Beatmap ID (top-right of hero)
        id_text = f"ID: {beatmap_id}"
        id_bbox = draw.textbbox((0, 0), id_text, font=self.font_small)
        _shadow_text(draw, (W - PADDING_X - (id_bbox[2] - id_bbox[0]), hero_y + 8), id_text, self.font_small, TEXT_SECONDARY)

        # Hero accent line
        draw.line([(0, hero_y + hero_h), (W, hero_y + hero_h)], fill=status_color, width=2)

        # ── PODIUM (top 3) ──
        podium_y = hero_y + hero_h + 6
        top_rows = rows[:3]

        if not rows:
            self._text_center(draw, W // 2, podium_y + 14, "No plays from registered users yet.", self.font_row, TEXT_SECONDARY)
        else:
            # Order: #2, #1, #3 (visually)
            col_order = []
            if len(top_rows) >= 2:
                col_order.append((top_rows[1], 176, 160, 52))  # #2
            if len(top_rows) >= 1:
                col_order.append((top_rows[0], 224, 180, 60))  # #1
            if len(top_rows) >= 3:
                col_order.append((top_rows[2], 176, 160, 52))  # #3

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

                # Panel with cover background
                panel = Image.new("RGB", (width, height), PANEL_BG)
                cover_img = self._image_from_bytes(row.get("cover_data")) if row.get("cover_data") else None
                if cover_img:
                    draw_cover_background(panel, cover_img, 0, height, width)
                    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 160))
                    panel.paste(overlay.convert("RGB"), (0, 0), overlay)
                panel_draw = ImageDraw.Draw(panel)
                panel_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=14, outline=stripe, width=2)
                img.paste(panel, (x, y))
                draw = ImageDraw.Draw(img)

                # Avatar with background circle
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

                # Rank number
                cur_text_y = av_y + avatar_size + 6
                self._text_center(draw, x + width // 2, cur_text_y, f"#{rank}", self.font_row, stripe)
                cur_text_y += 22

                # Flag + username
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

                # PP value (prominent)
                pp_val = float(row.get("pp", 0) or 0)
                pp_text = f"{pp_val:.0f}pp"
                pp_font = self.font_stat_value if rank == 1 else self.font_row
                self._text_center(draw, x + width // 2, y + height - 48, pp_text, pp_font, TEXT_PRIMARY)

                # Accuracy + combo line
                acc_val = float(row.get("accuracy", 0) or 0)
                combo_val = int(row.get("combo", 0) or 0)
                detail_text = f"{acc_val:.2f}%  {combo_val}x"
                self._text_center(draw, x + width // 2, y + height - 28, detail_text, self.font_stat_label, TEXT_SECONDARY)

                # Mod badges (small pills at top-left of panel)
                mods_raw = self._filter_mods(str(row.get("mods", "")).strip())
                if mods_raw and mods_raw != "—":
                    mod_list = [m.strip() for m in mods_raw.split(",") if m.strip()]
                    mod_cur_x = x + 6
                    mod_y_pos = y + 6
                    for mod_name in mod_list:
                        mod_color = MOD_COLORS.get(mod_name, (100, 100, 120))
                        badge_w = 32
                        badge_h = 16
                        draw.rounded_rectangle((mod_cur_x, mod_y_pos, mod_cur_x + badge_w, mod_y_pos + badge_h), radius=8, fill=mod_color)
                        self._text_center(draw, mod_cur_x + badge_w // 2, mod_y_pos + 1, mod_name, self.font_stat_label, (255, 255, 255))
                        mod_cur_x += badge_w + 3
                        if mod_cur_x + badge_w > x + width - 6:
                            break

                # Grade letter at top-right of panel
                grade = row.get("rank", "F")
                grade_color = GRADE_COLORS.get(grade, TEXT_SECONDARY)
                draw.text((x + width - 22, y + 6), grade, font=self.font_label, fill=grade_color)

        # ── EXTENDED ROWS (positions 4+) ──
        if len(rows) > 3:
            list_y = podium_y + podium_h
            for idx, row in enumerate(rows[3:], start=4):
                y_top = list_y + (idx - 4) * row_h
                row_bg = ROW_EVEN if idx % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y_top), (W, y_top + row_h)], fill=row_bg)
                draw.rectangle([(0, y_top), (4, y_top + row_h)], fill=ACCENT_RED)

                pos = row.get("position", idx)
                y_text = y_top + (row_h - 22) // 2
                draw.text((16, y_text), f"#{pos}", font=self.font_row, fill=TEXT_PRIMARY)

                flag = load_flag(row.get("country", "XX"), height=18)
                if flag:
                    flag_y = y_text + (22 - flag.height) // 2
                    img.paste(flag, (58, flag_y), flag)
                    draw = ImageDraw.Draw(img)

                draw.text((82, y_text), row.get("username", "???"), font=self.font_row, fill=TEXT_PRIMARY)

                # Score info right-aligned
                pp_val = float(row.get("pp", 0) or 0)
                acc_val = float(row.get("accuracy", 0) or 0)
                combo_val = int(row.get("combo", 0) or 0)
                mods_raw = self._filter_mods(str(row.get("mods", "")).strip())

                parts = [f"{pp_val:.0f}pp", f"{acc_val:.2f}%", f"{combo_val}x"]
                if mods_raw and mods_raw != "—":
                    parts.append(mods_raw)
                value_str = " | ".join(parts)

                # Grade color text
                grade = row.get("rank", "F")
                grade_color = GRADE_COLORS.get(grade, TEXT_SECONDARY)
                grade_bbox = draw.textbbox((0, 0), grade, font=self.font_label)
                grade_w = grade_bbox[2] - grade_bbox[0]
                draw.text((VALUE_RIGHT_X - grade_w, y_text), grade, font=self.font_label, fill=grade_color)

                val_bbox = draw.textbbox((0, 0), value_str, font=self.font_label)
                val_w = val_bbox[2] - val_bbox[0]
                draw.text((VALUE_RIGHT_X - grade_w - 10 - val_w, y_text + 2), value_str, font=self.font_label, fill=TEXT_SECONDARY)

        # ── FOOTER ──
        footer_y = card_h - footer_h
        draw.line([(0, footer_y), (W, footer_y)], fill=ACCENT_RED, width=1)
        footer_text = f"{unique_players} players \u00b7 {total_plays} plays"
        self._text_center(draw, W // 2, footer_y + 8, footer_text, self.font_small, TEXT_SECONDARY)

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

