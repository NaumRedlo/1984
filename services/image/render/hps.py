import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR,
    HEADER_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
    PADDING_X,
)
from services.image.utils import (
    _none_coro,
    download_image,
    load_icon,
    cover_center_crop,
    rounded_rect_crop,
)


class HpsCardMixin:
    def generate_hps_card(self, data: Dict, cover: Optional[Image.Image] = None) -> BytesIO:
        W, H = 800, 520
        img, draw = self._create_canvas(W, H)

        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — HPS ANALYSIS", self.font_subtitle, ACCENT_RED)

        cover_top = header_h
        cover_h = 150
        left_w = 360

        if cover:
            right_w = W - left_w
            cropped = cover_center_crop(cover, right_w, cover_h)
            overlay = Image.new("RGBA", (right_w, cover_h), (0, 0, 0, 100))
            cropped = Image.alpha_composite(cropped, overlay)
            fade = Image.new("L", (right_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = int(fx / fade_zone * 255)
                ImageDraw.Draw(fade).line([(fx, 0), (fx, cover_h)], fill=alpha)
            img.paste(cropped.convert("RGB"), (left_w, cover_top), fade)
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(left_w, cover_top), (W, cover_top + cover_h)], fill=(40, 35, 55))

        draw.rectangle([(0, cover_top), (left_w, cover_top + cover_h)], fill=BG_COLOR)

        text_x = PADDING_X
        max_title_w = left_w - text_x - 10
        raw_title = data.get("map_title", "???")
        if " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            map_title = f"{parts[1]} - {parts[0]}"
        else:
            map_title = raw_title
        bbox_t = draw.textbbox((0, 0), map_title, font=self.font_row)
        while bbox_t[2] - bbox_t[0] > max_title_w and len(map_title) > 4:
            map_title = map_title[:-1]
            bbox_t = draw.textbbox((0, 0), map_title + "...", font=self.font_row)
        if bbox_t[2] - bbox_t[0] > max_title_w:
            map_title = map_title + "..."
        elif len(map_title) < len(raw_title):
            map_title = map_title + "..."
        draw.text((text_x, cover_top + 12), map_title, font=self.font_row, fill=TEXT_PRIMARY)

        version = data.get("map_version", "")
        creator = data.get("creator", "")
        av_size = 48
        av_y = cover_top + 40
        mapper_avatar = data.get("_mapper_avatar")
        if mapper_avatar:
            cropped_av = rounded_rect_crop(mapper_avatar, av_size, radius=10)
            img.paste(cropped_av, (text_x, av_y), cropped_av)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((text_x, av_y, text_x + av_size, av_y + av_size), radius=10, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((text_x, av_y, text_x + av_size, av_y + av_size), radius=10, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        info_x = text_x + av_size + 10
        max_info_w = left_w - info_x - 5
        if version:
            version_str = f"[{version}]"
            vbbox = draw.textbbox((0, 0), version_str, font=self.font_small)
            if vbbox[2] - vbbox[0] > max_info_w:
                while len(version_str) > 4 and draw.textbbox((0, 0), version_str + "...]", font=self.font_small)[2] > max_info_w:
                    version_str = version_str[:-1]
                version_str = version_str + "...]"
            draw.text((info_x, av_y + 4), version_str, font=self.font_small, fill=TEXT_SECONDARY)
            if creator:
                draw.text((info_x, av_y + 24), creator, font=self.font_label, fill=TEXT_PRIMARY)
        elif creator:
            draw.text((info_x, av_y + 12), creator, font=self.font_label, fill=TEXT_PRIMARY)

        multiplier = data.get("total_multiplier", 1.0)
        mult_y = av_y + av_size + 10
        draw.text((text_x, mult_y), "MULTIPLIER:", font=self.font_stat_label, fill=TEXT_SECONDARY)
        mult_bbox = draw.textbbox((0, 0), "MULTIPLIER:", font=self.font_stat_label)
        label_w = mult_bbox[2] - mult_bbox[0]
        draw.text((text_x + label_w + 5, mult_y - 2), f"x{multiplier:.2f}", font=self.font_label, fill=ACCENT_RED)

        draw.line([(0, cover_top + cover_h), (W, cover_top + cover_h)], fill=ACCENT_RED, width=2)

        body_top = cover_top + cover_h + 12
        half_w = W // 2

        draw.text((PADDING_X, body_top), "MAP PARAMETERS", font=self.font_label, fill=ACCENT_RED)
        param_y = body_top + 26
        param_keys = [("cs", "CS"), ("od", "OD"), ("ar", "AR"), ("hp", "HP")]
        col1_x = PADDING_X
        col2_x = PADDING_X + 110
        for idx, (key, label) in enumerate(param_keys):
            val = data.get(key, 0)
            val_str = f"{val:.1f}" if isinstance(val, float) else str(val)
            px = col1_x if idx % 2 == 0 else col2_x
            py = param_y + (idx // 2) * 40
            pw = 90
            self._draw_panel(draw, px, py, pw, 32)
            draw.text((px + 8, py + 6), label, font=self.font_label, fill=TEXT_SECONDARY)
            self._text_right(draw, px + pw - 8, py + 6, val_str, self.font_label, TEXT_PRIMARY)

        stars = data.get("star_rating", 0.0)
        duration = data.get("duration", 0)
        bpm = data.get("bpm", 0.0)
        dur_str = f"{duration // 60}:{duration % 60:02d}"
        max_combo = data.get("max_combo", 0)

        draw.text((half_w + 20, body_top), "MAP INFORMATION", font=self.font_label, fill=ACCENT_RED)
        info_y = body_top + 26
        info_items = [
            ("Stars", f"{stars:.2f}", True),
            ("Duration", dur_str, False),
            ("BPM", f"{bpm:.0f}", False),
            ("Combo", f"{max_combo:,}x" if max_combo else "—", False),
        ]
        star_icon = load_icon("star", size=14)
        for idx, (label, val, has_star) in enumerate(info_items):
            px = half_w + 20 if idx % 2 == 0 else half_w + 20 + 180
            py = info_y + (idx // 2) * 40
            pw = 165
            self._draw_panel(draw, px, py, pw, 32)
            draw.text((px + 8, py + 6), label, font=self.font_small, fill=TEXT_SECONDARY)
            if has_star and star_icon:
                val_bbox = draw.textbbox((0, 0), val, font=self.font_label)
                val_w = val_bbox[2] - val_bbox[0]
                icon_gap = 4
                total = val_w + icon_gap + star_icon.width
                vx = px + pw - 8 - total
                draw.text((vx, py + 6), val, font=self.font_label, fill=TEXT_PRIMARY)
                img.paste(star_icon, (vx + val_w + icon_gap, py + 6), star_icon)
                draw = ImageDraw.Draw(img)
            else:
                self._text_right(draw, px + pw - 8, py + 6, val, self.font_label, TEXT_PRIMARY)

        scenarios = data.get("scenarios", [])
        panel_y = body_top + 132
        panel_h = 64
        gap = 10
        n_panels = max(len(scenarios), 1)
        panel_w = (W - PADDING_X * 2 - gap * (n_panels - 1)) // n_panels

        draw.text((PADDING_X, panel_y - 22), "POTENTIAL HP REWARDS", font=self.font_label, fill=ACCENT_RED)
        for i, sc in enumerate(scenarios[:4]):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panel_y, panel_w, panel_h)

            hp_reward = sc.get("hp_reward", 0)
            name = sc.get("name", "?")
            hp_str = f"{hp_reward} HP"
            self._text_center(draw, px + panel_w // 2, panel_y + 8, hp_str, self.font_stat_value, ACCENT_GREEN)
            self._text_center(draw, px + panel_w // 2, panel_y + 42, name, self.font_stat_label, TEXT_SECONDARY)

        agent_y = panel_y + panel_h + 18
        agent_h = 50
        agent_gap = 10
        agent_pw = (W - PADDING_X * 2 - agent_gap * 2) // 3

        player_pp = data.get("player_pp", 0)
        rf_value = data.get("rf_value", 1.0)
        rf_cat = data.get("rf_category", "")
        tsf_value = data.get("tsf_value", 1.0)

        agent_items = [
            (f"{player_pp:,} PP", "YOUR PP"),
            (f"x{rf_value:.2f} ({rf_cat})", "PROGRESS"),
            (f"x{tsf_value:.2f}", "TECH SKILL"),
        ]
        for i, (val, label) in enumerate(agent_items):
            px = PADDING_X + i * (agent_pw + agent_gap)
            self._draw_panel(draw, px, agent_y, agent_pw, agent_h)
            cell_cx = px + agent_pw // 2
            self._text_center(draw, cell_cx, agent_y + 6, val, self.font_label, TEXT_PRIMARY)
            self._text_center(draw, cell_cx, agent_y + 28, label, self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_hps_card_async(self, data: Dict) -> BytesIO:
        bsid = data.get("beatmapset_id", 0)
        creator_id = data.get("creator_id", 0)
        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        avatar_url = f"https://a.ppy.sh/{creator_id}" if creator_id else None
        results = await asyncio.gather(
            download_image(cover_url) if cover_url else _none_coro(),
            download_image(avatar_url) if avatar_url else _none_coro(),
            return_exceptions=True,
        )
        cover = results[0] if not isinstance(results[0], Exception) else None
        mapper_avatar = results[1] if not isinstance(results[1], Exception) else None
        data["_mapper_avatar"] = mapper_avatar
        return await asyncio.to_thread(self.generate_hps_card, data, cover)

