import asyncio
from io import BytesIO
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFilter

from services.image.constants import (
    RECENT_ACCENT, RECENT_BG, RECENT_LINE, RECENT_PANEL, RECENT_PILL, RECENT_TRACK,
    TEXT_PRIMARY, TEXT_SECONDARY, TOP_COLORS,
)
from services.image.utils import (
    _none_coro, cover_center_crop, download_image, load_icon,
)

BG = RECENT_BG
PANEL = RECENT_PANEL
PANEL_EDGE = (46, 38, 44)
ROW = (34, 29, 35)
ROW_ALT = (30, 26, 32)
TEXT = TEXT_PRIMARY
MUTED = TEXT_SECONDARY

MINE = RECENT_LINE
MINE_DIM = RECENT_PILL
MINE_BG = (36, 24, 28)

VERSION_PILL = (70, 90, 150)
VERSION_INK = (235, 240, 255)

RADIUS = 16

TITLE_TINTS = {
    "best": TOP_COLORS[1],
    "accuracy": (120, 200, 140),
    "combo": TOP_COLORS[3],
    "score": (196, 176, 200),
    "mods": RECENT_LINE,
}

_MLB_STRINGS = {
    "en": {
        "board": "CHAT LEADERBOARD",
        "leaders": "MAP LEADERS",
        "stats": "MAP STATISTICS",
        "history": "RECORD HISTORY",
        "updated": "Last updated",
        "yours": "YOUR RESULT",
        "no_result": "no result yet",
        "place": "place {n}",
        "player": "Player",
        "accuracy": "Accuracy",
        "combo": "Combo",
        "pp": "PP",
        "score": "Score",
        "plays": "Times played",
        "players": "Players",
        "average": "Average result",
        "t.best": "Best result",
        "t.accuracy": "Best accuracy",
        "t.combo": "Best combo",
        "t.score": "Highest score",
        "t.mods": "Hardest mods",
    },
    "ru": {
        "board": "ЛИДЕРБОРД УЧАСТНИКОВ",
        "leaders": "ЛИДЕРЫ КАРТЫ",
        "stats": "СТАТИСТИКА КАРТЫ",
        "history": "ИСТОРИЯ РЕКОРДА",
        "updated": "Последнее обновление",
        "yours": "ТВОЙ РЕЗУЛЬТАТ",
        "no_result": "результата пока нет",
        "place": "{n} место",
        "player": "Игрок",
        "accuracy": "Точность",
        "combo": "Комбо",
        "pp": "PP",
        "score": "Рекорд",
        "plays": "Сыграно раз",
        "players": "Участников",
        "average": "Средний результат",
        "t.best": "Лучший результат",
        "t.accuracy": "Лучшая точность",
        "t.combo": "Лучшее комбо",
        "t.score": "Самый большой рекорд",
        "t.mods": "Самые сложные моды",
    },
}

def _panel(draw: ImageDraw.ImageDraw, box, fill=PANEL, edge=PANEL_EDGE, radius=RADIUS, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=width)

def _pp_text(row: dict) -> str:
    value = f"{float(row.get('pp') or 0):.1f}"
    return f"~{value}" if row.get("pp_estimated") else value

class MapLeaderboardCardMixin:

    MLB_ROWS_PER_PAGE = 9

    W = 1180
    PAD = 22
    GAP = 18
    LEFT_W = 740
    ROW_H = 54
    RANK_W = 52
    HEAD_H = 150

    HISTORY_CELL_MAX = 320

    def _mlb_strings(self, data: Dict) -> Dict[str, str]:
        return _MLB_STRINGS.get((data.get("lang") or "en").lower(), _MLB_STRINGS["en"])

    def _fit(self, draw, text: str, font, limit: int) -> str:
        if self._text_size(draw, text, font)[0] <= limit:
            return text
        while text and self._text_size(draw, text + "…", font)[0] > limit:
            text = text[:-1]
        return text + "…"

    async def generate_map_leaderboard_v2_async(self, data: Dict) -> BytesIO:
        rows: List[Dict] = data.get("rows") or []
        per = self.MLB_ROWS_PER_PAGE
        pages = max(1, -(-len(rows) // per))
        page = min(max(0, int(data.get("page") or 0)), pages - 1)
        wanted = list(rows[page * per:(page + 1) * per]) + list(data.get("history") or [])

        cover = None
        if data.get("beatmap_cover_data"):
            cover = self._image_from_bytes(data["beatmap_cover_data"])
        elif data.get("beatmap_cover_url"):
            cover = await download_image(data["beatmap_cover_url"])

        pending = [
            _none_coro() if item.get("avatar_data") or not item.get("osu_user_id")
            else download_image(f"https://a.ppy.sh/{item['osu_user_id']}")
            for item in wanted
        ]
        fetched = await asyncio.gather(*pending, return_exceptions=True)
        for item, got in zip(wanted, fetched):
            if item.get("avatar_data"):
                item["avatar"] = self._image_from_bytes(item["avatar_data"])
            else:
                item["avatar"] = got if not isinstance(got, Exception) else None

        payload = dict(data)
        payload["cover"] = cover
        payload["page"] = page
        return await asyncio.to_thread(self.generate_map_leaderboard_v2, payload)

    def generate_map_leaderboard_v2(self, data: Dict) -> BytesIO:
        S = self._mlb_strings(data)
        rows: List[Dict] = data.get("rows") or []
        per = self.MLB_ROWS_PER_PAGE
        pages = max(1, -(-len(rows) // per))
        page = min(max(0, int(data.get("page") or 0)), pages - 1)
        shown = rows[page * per:(page + 1) * per]

        viewer = data.get("viewer") or {}
        viewer_name = viewer.get("username")

        on_page = any(r.get("username") == viewer_name for r in shown) if viewer_name else False
        yours_h = 0 if (on_page or not viewer_name) else 86

        head_h = self.HEAD_H
        board_h = 62 + len(shown) * self.ROW_H + 16
        left_h = head_h + self.GAP + board_h + (self.GAP + yours_h if yours_h else 0)

        titles = data.get("titles") or []
        titles_h = 56 + len(titles) * 74 + 12

        stats_h = 56 + 3 * 46 + 12
        updated_h = 64 if data.get("updated") else 0
        right_h = titles_h + self.GAP + stats_h + (self.GAP + updated_h if updated_h else 0)

        history = data.get("history") or []
        history_h = 156 if history else 0

        body_h = max(left_h, right_h)
        H = self.PAD + body_h + (self.GAP + history_h if history_h else 0) + 46 + self.PAD

        img = Image.new("RGB", (self.W, H), BG)
        draw = ImageDraw.Draw(img)

        left_x = self.PAD
        right_x = self.PAD + self.LEFT_W + self.GAP
        right_w = self.W - right_x - self.PAD

        y = self.PAD
        self._mlb_header(img, draw, (left_x, y, left_x + self.LEFT_W, y + head_h), data)
        board_y = y + head_h + self.GAP
        self._mlb_board(img, draw, (left_x, board_y, left_x + self.LEFT_W, board_y + board_h),
                        shown, viewer_name, page, pages, S)
        if yours_h:
            yy = board_y + board_h + self.GAP
            self._mlb_viewer(img, draw, (left_x, yy, left_x + self.LEFT_W, yy + yours_h),
                             viewer, S)

        self._mlb_titles(img, draw, (right_x, y, right_x + right_w, y + titles_h), titles, S)
        sy = y + titles_h + self.GAP
        self._mlb_stats(img, draw, (right_x, sy, right_x + right_w, sy + stats_h), data, S)
        if updated_h:
            uy = sy + stats_h + self.GAP
            self._mlb_updated(img, draw, (right_x, uy, right_x + right_w, uy + updated_h),
                              data, S)

        if history_h:
            hy = self.PAD + body_h + self.GAP
            self._mlb_history(img, draw, (left_x, hy, self.W - self.PAD, hy + history_h),
                              history, S)

        foot = data.get("footer") or ""
        if foot:
            w = self._text_size(draw, foot, self.font_small)[0]
            self._draw_text(draw, ((self.W - w) / 2, H - self.PAD - 24), foot,
                            self.font_small, MUTED)

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    def _mlb_header(self, img, draw, box, data):
        x0, y0, x1, y1 = box
        cover = data.get("cover")
        if isinstance(cover, Image.Image):
            self._mlb_cover(img, box, cover)
            draw.rounded_rectangle(box, radius=RADIUS, outline=PANEL_EDGE, width=1)
        else:
            _panel(draw, box)

        tx = x0 + 26
        limit = (x1 - 26) - tx
        self._draw_text(draw, (tx, y0 + 20),
                        self._fit(draw, data.get("artist") or "—", self.font_label, limit),
                        self.font_label, MUTED)
        self._draw_text(draw, (tx, y0 + 48),
                        self._fit(draw, data.get("title") or "—", self.font_big, limit),
                        self.font_big, TEXT)

        py = y0 + 102
        f_sr = self.font_label
        px = self._draw_sr_pill(img, tx, py, float(data.get("star_rating") or 0.0),
                                f_sr, star_size=13)
        version = (data.get("version") or "").strip()
        if version:

            bb = draw.textbbox((0, 0), "0", font=f_sr)
            cy = py + (bb[1] + bb[3]) / 2
            h = self._text_size(draw, "0", f_sr)[1] + 8
            f_v = self.font_stat_label
            label = self._fit(draw, version, f_v, x1 - 26 - px - 24)
            w = self._text_size(draw, label, f_v)[0] + 24
            self._aa_rounded_fill(img, (px, int(cy - h / 2), px + w, int(cy + h / 2)),
                                  radius=int(h // 2), fill=VERSION_PILL)
            d = ImageDraw.Draw(img)
            vh = self._text_size(d, label, f_v)[1]
            self._text_center(d, px + w // 2, int(cy - vh / 2) - 1, label, f_v, VERSION_INK)

    def _mlb_cover(self, img, box, cover):
        x0, y0, x1, y1 = box
        w, h = int(x1 - x0), int(y1 - y0)
        art = cover_center_crop(cover, w, h)

        ramp = Image.new("L", (w, 1))
        for i in range(w):
            ramp.putpixel((i, 0), int(232 - 150 * (i / max(1, w - 1))))
        scrim = Image.new("RGBA", (w, h), (12, 9, 13, 255))
        scrim.putalpha(ramp.resize((w, h)))
        art = Image.alpha_composite(art, scrim)

        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=RADIUS, fill=255)
        img.paste(art, (int(x0), int(y0)), mask)

    def _mlb_centred(self, draw, box, text, font, fill, dy=0):
        x0, y0, x1, _ = box
        w = self._text_size(draw, text, font)[0]
        self._draw_text(draw, (x0 + (x1 - x0 - w) / 2, y0 + dy), text, font, fill)

    def _mlb_board(self, img, draw, box, shown, viewer_name, page, pages, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("trophy", 22, TOP_COLORS[1])
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), S["board"], self.font_label, TEXT)
        if pages > 1:
            label = f"{page + 1}/{pages}"
            w = self._text_size(draw, label, self.font_small)[0]
            self._draw_text(draw, (x1 - 16 - w, y0 + 20), label, self.font_small, MUTED)

        cols = self._mlb_columns(x0, x1)
        hy = y0 + 50
        self._mlb_centred(draw, (x0 + 10, hy, x0 + 10 + self.RANK_W, hy),
                          "#", self.font_small, MUTED)
        self._draw_text(draw, (cols["name"], hy), S["player"], self.font_small, MUTED)
        for key in ("acc", "combo", "pp", "score"):
            label = S["accuracy"] if key == "acc" else S[key]
            w = self._text_size(draw, label, self.font_small)[0]
            self._draw_text(draw, (cols[key] - w, hy), label, self.font_small, MUTED)

        ry = y0 + 76
        for i, row in enumerate(shown):
            self._mlb_row(img, draw, (x0 + 10, ry, x1 - 10, ry + 48), row, cols,
                          alt=i % 2 == 1, is_viewer=row.get("username") == viewer_name)
            ry += self.ROW_H

    def _mlb_columns(self, x0, x1):
        return {
            "name": x0 + 10 + self.RANK_W + 52,
            "acc": x1 - 360,
            "combo": x1 - 250,
            "pp": x1 - 150,
            "score": x1 - 22,
        }

    def _mlb_row(self, img, draw, box, row, cols, alt, is_viewer):
        x0, y0, x1, y1 = box
        if is_viewer:
            draw.rounded_rectangle(box, radius=12, fill=MINE_BG, outline=MINE, width=2)
        else:
            draw.rounded_rectangle(box, radius=12, fill=ROW_ALT if alt else ROW)

        mid = (y0 + y1) // 2

        place = int(row.get("position") or 0)
        rank_box = (x0, mid, x0 + self.RANK_W, mid)
        crown = TOP_COLORS.get(place)
        if crown:
            icon = load_icon("trophy", 22, crown)
            if icon:
                img.paste(icon, (x0 + (self.RANK_W - icon.width) // 2, mid - 11), icon)
        else:
            self._mlb_centred(draw, rank_box, str(place), self.font_label, MUTED, dy=-11)

        self._mlb_avatar(img, draw, row.get("avatar"), x0 + self.RANK_W + 8, mid, 36)

        name_limit = cols["acc"] - cols["name"] - 90
        self._draw_text(draw, (cols["name"], mid - 12),
                        self._fit(draw, row.get("username") or "—", self.font_row, name_limit),
                        self.font_row, TEXT)

        pp_colour = MINE if is_viewer else RECENT_ACCENT
        for key, text, font, colour in (
            ("acc", f"{float(row.get('accuracy') or 0):.2f}%", self.font_label, TEXT),
            ("combo", f"{int(row.get('combo') or 0):,}x", self.font_label, TEXT),
            ("pp", _pp_text(row), self.font_label, pp_colour),
            ("score", f"{int(row.get('score') or 0):,}", self.font_label, MUTED),
        ):
            w = self._text_size(draw, text, font)[0]
            self._draw_text(draw, (cols[key] - w, mid - 11), text, font, colour)

    def _mlb_avatar(self, img, draw, avatar, x, mid, d):
        pad = 10
        glow = Image.new("RGBA", (d + pad * 2, d + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((pad - 3, pad - 3, pad + d + 3, pad + d + 3),
                                     fill=(228, 72, 72, 130))
        glow = glow.filter(ImageFilter.GaussianBlur(5))
        img.paste(glow, (x - pad, mid - d // 2 - pad), glow)
        if isinstance(avatar, Image.Image):
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (x, mid - d // 2), mask)
        else:
            draw.ellipse((x, mid - d // 2, x + d, mid + d // 2), fill=RECENT_TRACK)
        draw.ellipse((x, mid - d // 2, x + d, mid + d // 2), outline=(228, 76, 76), width=2)

    def _mlb_viewer(self, img, draw, box, viewer, S):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=RADIUS, fill=MINE_BG, outline=MINE_DIM, width=2)
        self._draw_text(draw, (x0 + 20, y0 + 14), S["yours"], self.font_stat_label, MINE)
        place = viewer.get("position")
        self._draw_text(draw, (x0 + 20, y0 + 40),
                        S["place"].format(n=place) if place else S["no_result"],
                        self.font_row, TEXT)

        stats = (
            (S["accuracy"], f"{float(viewer.get('accuracy') or 0):.2f}%", TEXT),
            (S["combo"], f"{int(viewer.get('combo') or 0):,}x", TEXT),
            (S["pp"], _pp_text(viewer), MINE),
            (S["score"], f"{int(viewer.get('score') or 0):,}", TEXT),
        )
        span = (x1 - x0 - 260) // len(stats)
        for i, (label, value, colour) in enumerate(stats):
            cx = x0 + 250 + span * i + span // 2
            vw = self._text_size(draw, value, self.font_label)[0]
            lw = self._text_size(draw, label, self.font_stat_label)[0]
            self._draw_text(draw, (cx - vw / 2, y0 + 26), value, self.font_label, colour)
            self._draw_text(draw, (cx - lw / 2, y0 + 52), label, self.font_stat_label, MUTED)

    def _mlb_titles(self, img, draw, box, titles, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("stars", 22, TOP_COLORS[1])
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), S["leaders"], self.font_label, TEXT)

        ty = y0 + 52
        for title in titles:
            row = (x0 + 12, ty, x1 - 12, ty + 64)
            _panel(draw, row, fill=ROW, radius=12)
            kind = title.get("kind")
            tint = TITLE_TINTS.get(kind, RECENT_ACCENT)
            icon = load_icon(title.get("icon") or "trophy", 24, tint)
            if icon:
                img.paste(icon, (x0 + 26, ty + 20), icon)
            value = title.get("value") or ""
            vw = self._text_size(draw, value, self.font_label)[0]
            limit = (x1 - 26 - vw) - (x0 + 64) - 12

            label = S.get(f"t.{kind}", "")
            self._draw_text(draw, (x0 + 64, ty + 12),
                            self._fit(draw, label, self.font_stat_label, limit),
                            self.font_stat_label, MUTED)
            self._draw_text(draw, (x0 + 64, ty + 34),
                            self._fit(draw, title.get("who") or "—", self.font_label, limit),
                            self.font_label, TEXT)
            self._draw_text(draw, (x1 - 26 - vw, ty + 22), value, self.font_label, tint)
            ty += 74

    def _mlb_stats(self, img, draw, box, data, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("column-chart", 22, RECENT_ACCENT)
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), S["stats"], self.font_label, TEXT)

        lines = (
            (S["plays"], f"{int(data.get('total_plays') or 0):,}"),
            (S["players"], f"{int(data.get('unique_players') or 0):,}"),
            (S["average"], data.get("average") or "—"),
        )
        ly = y0 + 54
        for label, value in lines:
            self._draw_text(draw, (x0 + 20, ly + 3), label, self.font_small, MUTED)
            vw = self._text_size(draw, value, self.font_label)[0]
            self._draw_text(draw, (x1 - 20 - vw, ly), value, self.font_label, TEXT)
            ly += 46

    def _mlb_updated(self, img, draw, box, data, S):
        x0, y0, x1, y1 = box
        _panel(draw, box, fill=ROW)
        icon = load_icon("clock", 20, MUTED)
        if icon:
            img.paste(icon, (x0 + 18, y0 + 22), icon)
        self._draw_text(draw, (x0 + 48, y0 + 12), S["updated"], self.font_small, MUTED)
        self._draw_text(draw, (x0 + 48, y0 + 32), data.get("updated") or "",
                        self.font_stat_label, TEXT)

    def _mlb_history(self, img, draw, box, history, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("clock", 22, RECENT_ACCENT)
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), S["history"], self.font_label, TEXT)

        count = len(history)
        inner = (x1 - x0) - 32
        gap = 26
        cell_w = min((inner - gap * (count - 1)) // max(count, 1), self.HISTORY_CELL_MAX)
        hy = y0 + 54
        for i, entry in enumerate(history):
            cx = x0 + 16 + (cell_w + gap) * i
            cell = (cx, hy, cx + cell_w, hy + 76)
            if i == 0:
                draw.rounded_rectangle(cell, radius=12, fill=MINE_BG, outline=MINE, width=2)
            else:
                _panel(draw, cell, fill=ROW, radius=12)

            self._draw_text(draw, (cx + 14, hy + 10), entry.get("date") or "",
                            self.font_small, MUTED)
            self._mlb_avatar(img, draw, entry.get("avatar"), cx + 14, hy + 50, 26)
            limit = cell_w - 60
            self._draw_text(draw, (cx + 50, hy + 32),
                            self._fit(draw, entry.get("username") or "—",
                                      self.font_stat_label, limit),
                            self.font_stat_label, TEXT)
            self._draw_text(draw, (cx + 50, hy + 52), f"{float(entry.get('pp') or 0):.1f} PP",
                            self.font_small, MINE if i == 0 else MUTED)

            if i < count - 1:
                chev = "›"
                w = self._text_size(draw, chev, self.font_row)[0]
                self._draw_text(draw, (cx + cell_w + (gap - w) / 2, hy + 28),
                                chev, self.font_row, PANEL_EDGE)
