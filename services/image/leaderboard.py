"""Leaderboard card generators."""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD,
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_RED,
    TOP_COLORS, PANEL_BG, GRADE_COLORS, CARD_WIDTH, PADDING_X, VALUE_RIGHT_X,
)
from services.image.utils import (
    load_icon, load_flag,
    _none_coro, download_image,
    cover_center_crop, draw_cover_background, rounded_rect_crop,
)
from services.image.base import BaseCardRenderer



class LeaderboardCardGenerator(BaseCardRenderer):
    """Leaderboard-specific PNG card."""




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
        header_h = 28
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
        self._draw_header(draw, "PROJECT 1984 — MAP LEADERBOARD", "", W)

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
                self._aa_rounded_outline(img, (PADDING_X - 1, mav_y - 1, PADDING_X + 29, mav_y + 29), radius=6, outline=TEXT_SECONDARY, width=2)
                draw = ImageDraw.Draw(img)
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
                self._aa_rounded_fill(img, (status_x, ver_y + 1, status_x + sb_w, ver_y + 19), radius=4, fill=status_color)
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
                    # Round the panel's corners to match the frame, otherwise the
                    # square background pokes out past the rounded outline.
                    img.paste(panel, (x, y), self._rounded_mask((width, height), radius=14))
                    # AA podium-panel border on top of the pasted panel.
                    self._aa_rounded_outline(img, (x, y, x + width, y + height), radius=14, outline=stripe, width=2)
                    draw = ImageDraw.Draw(img)

                    av_x = x + (width - avatar_size) // 2
                    av_y = y + 14
                    avatar_img = self._image_from_bytes(row.get("avatar_data"))
                    if avatar_img:
                        av = rounded_rect_crop(avatar_img, avatar_size, radius=14)
                        img.paste(av, (av_x, av_y), av)
                        draw = ImageDraw.Draw(img)
                    else:
                        self._aa_rounded_fill(img, (av_x, av_y, av_x + avatar_size, av_y + avatar_size), radius=14, fill=(40, 40, 58))
                    self._aa_rounded_outline(img, (av_x - 1, av_y - 1, av_x + avatar_size + 1, av_y + avatar_size + 1), radius=14, outline=stripe, width=3)
                    draw = ImageDraw.Draw(img)

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
                    primary_str = row.get("primary_str") or f"{pp_val:.0f}pp"
                    self._text_center(draw, x + width // 2, pp_y, primary_str, pp_font, pp_color)

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
                    # Supersample the small grade badge so the outer disc edge
                    # and the inner outline ring both come out cleanly.
                    ss = 4
                    big = badge_r * 2 * ss
                    badge_img = Image.new('RGBA', (big, big), (0, 0, 0, 0))
                    badge_draw = ImageDraw.Draw(badge_img)
                    badge_draw.ellipse((0, 0, big - 1, big - 1), fill=(glow_r, glow_g, glow_b, 200))
                    outline_c = (min(grade_color[0], 255), min(grade_color[1], 255), min(grade_color[2], 255), 160)
                    badge_draw.ellipse((2 * ss, 2 * ss, big - 3 * ss, big - 3 * ss), outline=outline_c, width=2 * ss)
                    badge_img = badge_img.resize((badge_r * 2, badge_r * 2), Image.LANCZOS)
                    img.paste(badge_img, (badge_cx - badge_r, badge_cy - badge_r), badge_img)
                    draw = ImageDraw.Draw(img)
                    grade_font = self.font_stat_label
                    gb = draw.textbbox((0, 0), grade, font=grade_font)
                    gtw = gb[2] - gb[0]
                    gth = gb[3] - gb[1]
                    gtx = badge_cx - gtw // 2
                    gty = badge_cy - gth // 2 - gb[1]
                    draw.text((gtx, gty), grade, font=grade_font, fill=grade_color)

                    # Mod badges vertical — circular disc with osu-web SVG glyph.
                    # Stack from top-left of the podium cell; cap at half cell height
                    # so we never overlap the score row below.
                    mods_raw = self._filter_mods(str(row.get("mods", "")).strip())
                    if mods_raw and mods_raw != "—":
                        mod_x = x + 6
                        mod_cur_y = y + 6
                        badge_sz = 18
                        for mod_name in [m.strip() for m in mods_raw.split(",") if m.strip()]:
                            if mod_cur_y + badge_sz > y + height // 2:
                                break
                            self._draw_mod_badge(img, mod_x, mod_cur_y, mod_name, size=badge_sz)
                            mod_cur_y += badge_sz + 3
                        draw = ImageDraw.Draw(img)

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
            self._aa_rounded_outline(
                img,
                (av_x - 1, av_y - 1, av_x + av_sz, av_y + av_sz),
                radius=av_r, outline=TEXT_SECONDARY, width=2,
            )
            draw = ImageDraw.Draw(img)

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

            # Stats: pp/score bold, then acc combo smaller, then mod badges
            pp_str = row.get("primary_str") or f"{pp_val:.0f}pp"
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



# Backward-compatible singleton; imported by services.image.__init__
leaderboard_gen = LeaderboardCardGenerator()
