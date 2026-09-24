import asyncio
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.settings import TIMEZONE
from services.image.constants import (
    GRADE_COLORS, MOD_ACRONYMS, MONO_BOLD, RECENT_ACCENT, RECENT_BG, RECENT_LINE, RECENT_PANEL, RECENT_PILL,
    RECENT_TRACK, TEXT_PRIMARY, TEXT_SECONDARY, TOP_COLORS, status_colours, status_name,
)
from services.image.utils import (
    _find_font, _none_coro, cover_center_crop, download_image, load_icon,
)
from utils.formatting.text import plural

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
        "mapped_by": "mapped by",
        "leaders": "MAP LEADERS",
        "stats": "MAP STATISTICS",
        "history": "RECORD HISTORY",
        "updated": "Last updated",
        "yours": "YOUR RESULT",
        "no_result": "No result yet",
        "place": "place {n}",
        "player": "Player",
        "accuracy": "Accuracy",
        "combo": "Combo",
        "pp": "PP",
        "score": "Score",
        "plays": "Plays",
        "players": "Players",
        "average": "Average result",
        "t.best": "Best result",
        "t.accuracy": "Best accuracy",
        "t.combo": "Best combo",
        "t.score": "Highest score",
        "t.mods": "Hardest mods",
    },
    "ru": {
        "board": "ЛИДЕРБОРД ЧАТА",
        "mapped_by": "автор карты",
        "leaders": "ЛИДЕРЫ КАРТЫ",
        "stats": "СТАТИСТИКА КАРТЫ",
        "history": "ИСТОРИЯ РЕКОРДА",
        "updated": "Последнее обновление",
        "yours": "ТВОЙ РЕЗУЛЬТАТ",
        "no_result": "Результата пока нет",
        "place": "{n} место",
        "player": "Игрок",
        "accuracy": "Точность",
        "combo": "Комбо",
        "pp": "PP",
        "score": "Очки",
        "plays": "Попыток",
        "players": "Игроков",
        "average": "Средний результат",
        "t.best": "Лучший результат",
        "t.accuracy": "Лучшая точность",
        "t.combo": "Лучшее комбо",
        "t.score": "Больше всего очков",
        "t.mods": "Самые сложные моды",
    },
}

def _panel(draw: ImageDraw.ImageDraw, box, fill=PANEL, edge=PANEL_EDGE, radius=RADIUS, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=width)

def _pp_text(row: dict) -> str:
    value = f"{float(row.get('pp') or 0):.1f}"
    return f"~{value}" if row.get("pp_estimated") else value

_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ru": ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"),
}

def _local(at: datetime) -> datetime:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    try:
        return at.astimezone(ZoneInfo(TIMEZONE))
    except Exception:
        return at

def _when(at: Optional[datetime], lang: str, now: Optional[datetime] = None) -> str:
    if at is None:
        return ""
    ru = (lang or "en").lower() == "ru"
    at = _local(at)
    now = _local(now or datetime.now(timezone.utc))
    days = (now.date() - at.date()).days
    if days <= 0:
        return "сегодня" if ru else "today"
    if days == 1:
        return "вчера" if ru else "yesterday"
    if days < 7:
        return f"{days} {plural(days, 'день', 'дня', 'дней')} назад" if ru else f"{days} days ago"
    month = _MONTHS["ru" if ru else "en"][at.month - 1]
    day = f"{at.day} {month}" if ru else f"{month} {at.day}"
    return day if at.year == now.year else f"{day} {at.year}"

def _grade_label(rank: str) -> str:
    rank = (rank or "").upper()
    return {"X": "SS", "XH": "SS", "SH": "S"}.get(rank, rank or "F")

class MapLeaderboardCardMixin:

    MLB_ROWS_PER_PAGE = 9

    W = 1300
    PAD = 22
    GAP = 18
    LEFT_W = 860
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
        viewer = data.get("viewer")
        if isinstance(viewer, dict) and not any(viewer is r for r in wanted):
            wanted.append(viewer)

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

        mapper = None
        if data.get("mapper_id"):
            try:
                mapper = await download_image(f"https://a.ppy.sh/{int(data['mapper_id'])}")
            except Exception:
                mapper = None

        payload = dict(data)
        payload["cover"] = cover
        payload["mapper_avatar"] = mapper
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
        yours_h = 0 if (on_page or not viewer_name) else 92

        head_h = self.HEAD_H
        board_h = 62 + len(shown) * self.ROW_H + 16
        left_h = head_h + self.GAP + board_h + (self.GAP + yours_h if yours_h else 0)

        titles = data.get("titles") or []
        titles_h = 56 + len(titles) * 78 + 12

        stats_h = 56 + 3 * 46 + 12
        updated_h = 64 if data.get("updated") else 0
        right_h = titles_h + self.GAP + stats_h + (self.GAP + updated_h if updated_h else 0)

        history = data.get("history") or []
        history_h = 172 if history else 0

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
                              history, S, data)

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
        S = self._mlb_strings(data)

        tx = x0 + 26
        right = x1 - 26
        mapper_name = (data.get("mapper_name") or "").strip()
        if mapper_name:
            av = 44
            ax = right - av
            draw = self._paste_ringed_avatar(img, data.get("mapper_avatar"), ax, y0 + 22, av)
            name = self._fit(draw, mapper_name, self.font_label, 220)
            nw = self._text_size(draw, name, self.font_label)[0]
            lw = self._text_size(draw, S["mapped_by"], self.font_stat_label)[0]
            self._draw_text(draw, (ax - 14 - lw, y0 + 22), S["mapped_by"], self.font_stat_label, MUTED)
            self._draw_text(draw, (ax - 14 - nw, y0 + 41), name, self.font_label, TEXT)
            right = ax - 30 - max(nw, lw)

        limit = right - tx
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
        bb = draw.textbbox((0, 0), "0", font=f_sr)
        cy = py + (bb[1] + bb[3]) / 2
        h = self._text_size(draw, "0", f_sr)[1] + 8
        f_v = self.font_stat_label

        def pill(x, label, fill, ink):
            w = self._text_size(draw, label, f_v)[0] + 24
            self._aa_rounded_fill(img, (x, int(cy - h / 2), x + w, int(cy + h / 2)),
                                  radius=int(h // 2), fill=fill)
            d = ImageDraw.Draw(img)
            vh = self._text_size(d, label, f_v)[1]
            self._text_center(d, x + w // 2, int(cy - vh / 2) - 1, label, f_v, ink)
            return x + w + 10

        version = (data.get("version") or "").strip()
        if version:
            px = pill(px, self._fit(draw, version, f_v, 240), VERSION_PILL, VERSION_INK)

        def chip(x, icon_name, label):
            icon = load_icon(icon_name, 16, TEXT)
            if icon:
                img.paste(icon, (x, int(cy - 8)), icon)
                x += 20
            d = ImageDraw.Draw(img)
            th = self._text_size(d, label, self.font_label)[1]
            self._draw_text(d, (x, int(cy - th / 2) - 2), label, self.font_label, TEXT)
            return x + self._text_size(d, label, self.font_label)[0] + 16

        bpm = float(data.get("bpm") or 0)
        if bpm:
            px = chip(px + 4, "bpm", f"{bpm:g}")
        length = int(data.get("total_length") or 0)
        if length:
            px = chip(px, "clock", f"{length // 60}:{length % 60:02d}")
        status = status_name(data.get("beatmap_status"))
        if status:
            fill, ink = status_colours(status)
            pill(px, status.upper(), fill, ink)

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
        self._draw_text(draw, (x0 + 20, y0 + 16), S["board"], self.font_label, TEXT)
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
            "grade": x1 - 585,
            "mods": x1 - 556,
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

        name_limit = cols["grade"] - 18 - cols["name"]
        self._draw_text(draw, (cols["name"], mid - 12),
                        self._fit(draw, row.get("username") or "—", self.font_row, name_limit),
                        self.font_row, TEXT)

        rank = (row.get("rank") or "").upper()
        if rank:
            label = _grade_label(rank)
            font = self._mlb_grade_font()
            gw, gh = self._text_size(draw, label, font)
            self._draw_text(draw, (cols["grade"] - gw / 2, mid - gh / 2 - 4), label, font,
                            GRADE_COLORS.get(rank, GRADE_COLORS["F"]))

        mods = [m for m in self._normalize_mods(row.get("mods") or "") if m in MOD_ACRONYMS and m != "NM"][:4]
        mx = cols["mods"]
        for mod in mods:
            mx = self._draw_mod_badge(img, mx, mid - 11, mod, size=22) + 3
        draw = ImageDraw.Draw(img)

        pp_colour = MINE if is_viewer else RECENT_ACCENT
        for key, text, font, colour in (
            ("acc", f"{float(row.get('accuracy') or 0):.2f}%", self.font_label, TEXT),
            ("combo", f"{int(row.get('combo') or 0):,}x", self.font_label, TEXT),
            ("pp", _pp_text(row), self.font_label, pp_colour),
            ("score", f"{int(row.get('score') or 0):,}", self.font_label, MUTED),
        ):
            w = self._text_size(draw, text, font)[0]
            self._draw_text(draw, (cols[key] - w, mid - 11), text, font, colour)

    def _mlb_grade_font(self):
        font = getattr(self, "_mlb_grade_cache", None)
        if font is None:
            path = _find_font(MONO_BOLD)
            font = ImageFont.truetype(path, 20) if path else self.font_label
            self._mlb_grade_cache = font
        return font

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
        mid = (y0 + y1) // 2
        self._mlb_avatar(img, draw, viewer.get("avatar"), x0 + 20, mid, 44)
        tx = x0 + 80
        self._draw_text(draw, (tx, y0 + 18), S["yours"], self.font_stat_label, MINE)
        place = viewer.get("position")
        self._draw_text(draw, (tx, y0 + 42),
                        S["place"].format(n=place) if place else S["no_result"],
                        self.font_row, TEXT)
        if not place:
            return

        stats = (
            (S["accuracy"], f"{float(viewer.get('accuracy') or 0):.2f}%", TEXT),
            (S["combo"], f"{int(viewer.get('combo') or 0):,}x", TEXT),
            (S["pp"], _pp_text(viewer), MINE),
            (S["score"], f"{int(viewer.get('score') or 0):,}", TEXT),
        )
        left = x0 + 300
        span = (x1 - 20 - left) // len(stats)
        for i, (label, value, colour) in enumerate(stats):
            cx = left + span * i + span // 2
            vw = self._text_size(draw, value, self.font_label)[0]
            lw = self._text_size(draw, label, self.font_stat_label)[0]
            self._draw_text(draw, (cx - vw / 2, y0 + 28), value, self.font_label, colour)
            self._draw_text(draw, (cx - lw / 2, y0 + 54), label, self.font_stat_label, MUTED)

    def _mlb_titles(self, img, draw, box, titles, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        self._draw_text(draw, (x0 + 20, y0 + 16), S["leaders"], self.font_label, TEXT)

        ty = y0 + 52
        for title in titles:
            row = (x0 + 12, ty, x1 - 12, ty + 68)
            _panel(draw, row, fill=ROW, radius=12)
            kind = title.get("kind")
            tint = TITLE_TINTS.get(kind, RECENT_ACCENT)
            icon = load_icon(title.get("icon") or "trophy", 34, tint)
            if icon:
                img.paste(icon, (x0 + 26, ty + 17), icon)
            tx = x0 + 76

            if kind == "mods":
                mods = [m for m in self._normalize_mods(title.get("value") or "") if m in MOD_ACRONYMS and m != "NM"]
                size = 26
                vw = len(mods) * (size + 4) - 4 if mods else 0
                mx = x1 - 26 - vw
                for mod in mods:
                    mx = self._draw_mod_badge(img, mx, ty + 21, mod, size=size) + 4
                draw = ImageDraw.Draw(img)
            else:
                value = title.get("value") or ""
                vw = self._text_size(draw, value, self.font_label)[0]
                self._draw_text(draw, (x1 - 26 - vw, ty + 24), value, self.font_label, tint)
            limit = (x1 - 26 - vw) - tx - 12

            label = S.get(f"t.{kind}", "")
            self._draw_text(draw, (tx, ty + 13),
                            self._fit(draw, label, self.font_stat_label, limit),
                            self.font_stat_label, MUTED)
            self._draw_text(draw, (tx, ty + 35),
                            self._fit(draw, title.get("who") or "—", self.font_label, limit),
                            self.font_label, TEXT)
            ty += 78

    def _mlb_stats(self, img, draw, box, data, S):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        self._draw_text(draw, (x0 + 20, y0 + 16), S["stats"], self.font_label, TEXT)

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

    def _mlb_history(self, img, draw, box, history, S, data=None):
        x0, y0, x1, y1 = box
        data = data or {}
        _panel(draw, box)
        self._draw_text(draw, (x0 + 20, y0 + 16), S["history"], self.font_label, TEXT)

        lang = data.get("lang") or "en"
        by_score = data.get("metric") == "score"
        count = len(history)
        inner = (x1 - x0) - 32
        gap = 26
        cell_w = min((inner - gap * (count - 1)) // max(count, 1), self.HISTORY_CELL_MAX)
        cell_h = 92
        hy = y0 + 58
        for i, entry in enumerate(history):
            cx = x0 + 16 + (cell_w + gap) * i
            cell = (cx, hy, cx + cell_w, hy + cell_h)
            if i == 0:
                draw.rounded_rectangle(cell, radius=12, fill=MINE_BG, outline=MINE, width=2)
            else:
                _panel(draw, cell, fill=ROW, radius=12)

            when = _when(entry.get("at"), lang) or entry.get("date") or ""
            self._draw_text(draw, (cx + 14, hy + 10),
                            self._fit(draw, when, self.font_stat_label, cell_w - 28),
                            self.font_stat_label, MINE if i == 0 else MUTED)

            av = 32
            mid = hy + 58
            self._mlb_avatar(img, draw, entry.get("avatar"), cx + 14, mid, av)
            tx = cx + 14 + av + 12
            limit = cell_w - (tx - cx) - 12
            self._draw_text(draw, (tx, mid - 21),
                            self._fit(draw, entry.get("username") or "—", self.font_stat_label, limit),
                            self.font_stat_label, TEXT)
            value = f"{int(entry.get('score') or 0):,}" if by_score else f"{_pp_text(entry)} PP"
            self._draw_text(draw, (tx, mid + 1),
                            self._fit(draw, value, self.font_small, limit),
                            self.font_small, MINE if i == 0 else MUTED)

            if i < count - 1:
                chev = "›"
                w = self._text_size(draw, chev, self.font_row)[0]
                self._draw_text(draw, (cx + cell_w + (gap - w) / 2, hy + cell_h / 2 - 14),
                                chev, self.font_row, PANEL_EDGE)