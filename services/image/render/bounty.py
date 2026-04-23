import asyncio
from io import BytesIO
from typing import Dict, List

from services.image.constants import (
    CARD_WIDTH,
    ROW_EVEN,
    ROW_ODD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
    PADDING_X,
)


class BountyCardMixin:
    def generate_bountylist_card(self, entries: List[Dict]) -> BytesIO:
        num_rows = max(len(entries), 1)
        header_h = 36
        row_h = 60
        footer_h = 40
        H = header_h + num_rows * row_h + 8 + footer_h

        img, draw = self._create_canvas(CARD_WIDTH, H)
        count_str = f"{len(entries)}" if entries else "0"
        self._draw_header(draw, "PROJECT 1984 — ACTIVE BOUNTIES", count_str, CARD_WIDTH)

        if not entries:
            y = header_h + (row_h - 24) // 2
            draw.text((PADDING_X, y), "No active bounties", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            for i, entry in enumerate(entries):
                y_top = header_h + i * row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y_top), (CARD_WIDTH, y_top + row_h)], fill=row_bg)

                y_text = y_top + 10
                y_sub = y_top + 34

                bid = entry.get("bounty_id", "?")
                draw.text((PADDING_X, y_text), f"#{bid}", font=self.font_row, fill=ACCENT_RED)
                bid_bbox = draw.textbbox((0, 0), f"#{bid}", font=self.font_row)
                bid_w = bid_bbox[2] - bid_bbox[0]

                title = entry.get("title", "—")
                if len(title) > 40:
                    title = title[:37] + "..."
                draw.text((PADDING_X + bid_w + 12, y_text), title, font=self.font_row, fill=TEXT_PRIMARY)

                stars = entry.get("star_rating", 0.0)
                deadline = entry.get("deadline", "—")
                p_count = entry.get("participant_count", 0)
                max_p = entry.get("max_participants")
                p_str = f"{p_count}/{max_p}" if max_p else str(p_count)

                sub_text = f"{stars:.2f}★  |  {deadline}  |  {p_str}"
                draw.text((PADDING_X + bid_w + 12, y_sub), sub_text, font=self.font_small, fill=TEXT_SECONDARY)

        return self._save(img)

    async def generate_bountylist_card_async(self, entries: List[Dict]) -> BytesIO:
        return await asyncio.to_thread(self.generate_bountylist_card, entries)

    def generate_bounty_card(self, data: Dict) -> BytesIO:
        conditions = data.get("conditions", [])
        num_cond = max(len(conditions), 1)
        cond_block = num_cond * 22 + 40
        H = 36 + 155 + cond_block + 90 + 50
        H = max(H, 396)
        W = 800
        img, draw = self._create_canvas(W, H)

        bounty_id = data.get("bounty_id", "?")
        self._draw_header(draw, "PROJECT 1984 — BOUNTY DIRECTIVE", f"#{bounty_id}", W)

        y = 44
        self._draw_section_title(draw, y, "MISSION BRIEFING")
        y += 28

        for label, key, fmt in [
            ("Type", "bounty_type", None),
            ("Title", "title", None),
            ("Map", "beatmap_title", None),
            ("Difficulty", "star_rating", ".2f★"),
            ("Duration", "duration", "time"),
            ("Status", "status", None),
        ]:
            val = data.get(key, "—")
            if fmt == ".2f★" and isinstance(val, (int, float)):
                val_str = f"{val:.2f}★"
            elif fmt == "time" and isinstance(val, (int, float)):
                val_str = f"{int(val) // 60}:{int(val) % 60:02d}"
            else:
                val_str = str(val)

            if key == "status":
                status_color = ACCENT_GREEN if val_str.lower() == "active" else ACCENT_RED
                self._draw_kv_row(draw, y, label, val_str, value_fill=status_color)
            else:
                self._draw_kv_row(draw, y, label, val_str)
            y += 22

        y += 8
        self._draw_separator(draw, y, W)
        y += 8
        self._draw_section_title(draw, y, "REQUIREMENTS")
        y += 28

        if conditions:
            for cond in conditions:
                draw.text((PADDING_X + 10, y), f"• {cond}", font=self.font_label, fill=TEXT_PRIMARY)
                y += 22
        else:
            draw.text((PADDING_X + 10, y), "• None", font=self.font_label, fill=TEXT_SECONDARY)
            y += 22

        y += 8
        self._draw_separator(draw, y, W)
        y += 8
        self._draw_section_title(draw, y, "FIELD REPORT")
        y += 28

        participant_count = data.get("participant_count", 0)
        max_participants = data.get("max_participants")
        p_str = str(participant_count)
        if max_participants:
            p_str += f"/{max_participants}"
        self._draw_kv_row(draw, y, "Participants", p_str)
        y += 22

        deadline = data.get("deadline", "—")
        self._draw_kv_row(draw, y, "Deadline", str(deadline))
        y += 22

        hps_preview = data.get("hps_preview_hp")
        if hps_preview is not None:
            self._draw_kv_row(draw, y, "HPS Preview (Win)", f"~{hps_preview} HP", value_fill=ACCENT_GREEN)
            y += 22

        return self._save(img)

    async def generate_bounty_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bounty_card, data)

