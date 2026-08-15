"""The map leaderboard card — who in the chat has played this map, and how.

Two columns and a footer. The left is the board itself: the map it belongs to,
the standing, and — only when the viewer is not already on the page in front of
them — their own row pulled out beneath it. The right is what the board *says*:
the titles worth naming, and the map's totals. The strip along the bottom is the
record changing hands over time, which is the one thing a table of current bests
cannot show.

It wears the bot's own palette rather than one of its own: the near-black warm
ground and the red the recent-score card set, so a leaderboard and a score card
posted in the same chat read as the same program talking. The SR pill and the
avatars' glow ring are borrowed outright from `recent.py` and `profile.py` for
the same reason.
"""

import asyncio
from io import BytesIO
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFilter

from services.image.constants import (
    RECENT_ACCENT, RECENT_BG, RECENT_LINE, RECENT_PANEL, RECENT_PILL, RECENT_TRACK,
    TEXT_PRIMARY, TEXT_SECONDARY, TOP_COLORS,
)
from services.image.utils import (
    _none_coro, download_image, load_icon, rounded_rect_crop,
)

BG = RECENT_BG
PANEL = RECENT_PANEL
PANEL_EDGE = (46, 38, 44)
ROW = (34, 29, 35)
ROW_ALT = (30, 26, 32)
TEXT = TEXT_PRIMARY
MUTED = TEXT_SECONDARY
# The viewer's own things — their row, their placing, their pp — in the bot's
# red, the way the recent card highlights the number you came to see.
MINE = RECENT_LINE
MINE_DIM = RECENT_PILL
MINE_BG = (36, 24, 28)

RADIUS = 16

# One tint per named title so the eye can tell them apart without reading the
# labels, all drawn from the palette rather than invented beside it.
TITLE_TINTS = {
    "best": TOP_COLORS[1],
    "accuracy": (120, 200, 140),
    "combo": TOP_COLORS[3],
    "score": (196, 176, 200),
    "mods": RECENT_LINE,
}


def _panel(draw: ImageDraw.ImageDraw, box, fill=PANEL, edge=PANEL_EDGE, radius=RADIUS, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=edge, width=width)


class MapLeaderboardCardMixin:
    """Draws the map leaderboard. Mixed into the shared card renderer."""

    # The board is paginated rather than scrolled: a chat leaderboard runs to
    # dozens of players and a card that grew with it would arrive as a strip
    # nobody can read on a phone.
    MLB_ROWS_PER_PAGE = 7

    W = 1180
    PAD = 22
    GAP = 18
    LEFT_W = 740
    ROW_H = 54
    RANK_W = 52

    def _fit(self, draw, text: str, font, limit: int) -> str:
        """Cut `text` to `limit` pixels, with an ellipsis if anything was lost.

        Measured with the fallback-aware sizer, not `textlength`: a Cyrillic
        name is drawn from a different face than the Latin around it, and
        measuring it in the wrong one cuts the name in the wrong place.
        """
        if self._text_size(draw, text, font)[0] <= limit:
            return text
        while text and self._text_size(draw, text + "…", font)[0] > limit:
            text = text[:-1]
        return text + "…"

    async def generate_map_leaderboard_v2_async(self, data: Dict) -> BytesIO:
        """Fetch the pictures the card needs, then draw it off the event loop.

        Only the pictures actually on this page: a board of forty players is
        forty avatar downloads, and thirty-three of them would be for rows
        nobody is looking at. The record strip is fetched too — those faces are
        drawn whatever page it is.
        """
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
        rows: List[Dict] = data.get("rows") or []
        per = self.MLB_ROWS_PER_PAGE
        pages = max(1, -(-len(rows) // per))
        page = min(max(0, int(data.get("page") or 0)), pages - 1)
        shown = rows[page * per:(page + 1) * per]

        viewer = data.get("viewer") or {}
        viewer_name = viewer.get("username")
        # The viewer's own row is a *fallback for not seeing yourself*, so it is
        # drawn only when this page does not already have them on it. Shown
        # regardless it was the same four numbers twice, a hand's width apart.
        on_page = any(r.get("username") == viewer_name for r in shown) if viewer_name else False
        yours_h = 0 if (on_page or not viewer_name) else 86

        head_h = 132
        board_h = 62 + len(shown) * self.ROW_H + 16
        left_h = head_h + self.GAP + board_h + (self.GAP + yours_h if yours_h else 0)

        titles = data.get("titles") or []
        titles_h = 56 + len(titles) * 74 + 12
        # Two lines now that "played" lives in the header alone.
        stats_h = 56 + 2 * 46 + 12
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
                        shown, viewer_name, page, pages)
        if yours_h:
            yy = board_y + board_h + self.GAP
            self._mlb_viewer(img, draw, (left_x, yy, left_x + self.LEFT_W, yy + yours_h), viewer)

        self._mlb_titles(img, draw, (right_x, y, right_x + right_w, y + titles_h), titles)
        sy = y + titles_h + self.GAP
        self._mlb_stats(img, draw, (right_x, sy, right_x + right_w, sy + stats_h), data)
        if updated_h:
            uy = sy + stats_h + self.GAP
            self._mlb_updated(img, draw, (right_x, uy, right_x + right_w, uy + updated_h), data)

        if history_h:
            hy = self.PAD + body_h + self.GAP
            self._mlb_history(img, draw, (left_x, hy, self.W - self.PAD, hy + history_h), history)

        foot = data.get("footer") or ""
        if foot:
            w = self._text_size(draw, foot, self.font_small)[0]
            self._draw_text(draw, ((self.W - w) / 2, H - self.PAD - 24), foot,
                            self.font_small, MUTED)

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    # ── the map itself ────────────────────────────────────────────────────

    def _mlb_header(self, img, draw, box, data):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        cover = data.get("cover")
        side = y1 - y0 - 28
        if isinstance(cover, Image.Image):
            img.paste(rounded_rect_crop(cover, side, 12), (x0 + 14, y0 + 14))
        else:
            draw.rounded_rectangle((x0 + 14, y0 + 14, x0 + 14 + side, y0 + 14 + side),
                                   radius=12, fill=ROW)

        tx = x0 + 14 + side + 18
        # The one place the play count is stated. It used to be here *and* in
        # the stats panel, which is one fact and two chances to disagree.
        plays = int(data.get("total_plays") or 0)
        tile = (x1 - 14 - 150, y0 + 22, x1 - 14, y0 + 110)
        _panel(draw, tile, fill=ROW, radius=12)
        self._mlb_centred(draw, tile, "Сыграно", self.font_stat_label, MUTED, dy=12)
        self._mlb_centred(draw, tile, f"{plays}", self.font_big, TEXT, dy=32)
        self._mlb_centred(draw, tile, "раз", self.font_stat_label, MUTED, dy=66)

        limit = tile[0] - tx - 20
        self._draw_text(draw, (tx, y0 + 18),
                        self._fit(draw, data.get("title") or "—", self.font_big, limit),
                        self.font_big, TEXT)
        self._draw_text(draw, (tx, y0 + 58),
                        self._fit(draw, f"[{data.get('version') or '—'}]", self.font_row, limit),
                        self.font_row, TEXT)

        # The canonical SR pill, so difficulty reads the same here as on a
        # score card — the colour ramp is the game's own.
        artist = self._fit(draw, data.get("artist") or "—", self.font_label, limit - 130)
        self._draw_text(draw, (tx, y0 + 96), artist, self.font_label, MUTED)
        aw = self._text_size(draw, artist, self.font_label)[0]
        self._draw_sr_pill(img, tx + aw + 14, y0 + 96,
                           float(data.get("star_rating") or 0.0), self.font_stat_label)

    def _mlb_centred(self, draw, box, text, font, fill, dy=0):
        x0, y0, x1, _ = box
        w = self._text_size(draw, text, font)[0]
        self._draw_text(draw, (x0 + (x1 - x0 - w) / 2, y0 + dy), text, font, fill)

    # ── the standing ──────────────────────────────────────────────────────

    def _mlb_board(self, img, draw, box, shown, viewer_name, page, pages):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("trophy", 22, TOP_COLORS[1])
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), "ЛИДЕРБОРД УЧАСТНИКОВ", self.font_label, TEXT)
        if pages > 1:
            label = f"{page + 1}/{pages}"
            w = self._text_size(draw, label, self.font_small)[0]
            self._draw_text(draw, (x1 - 16 - w, y0 + 20), label, self.font_small, MUTED)

        cols = self._mlb_columns(x0, x1)
        hy = y0 + 50
        self._mlb_centred(draw, (x0 + 10, hy, x0 + 10 + self.RANK_W, hy),
                          "#", self.font_small, MUTED)
        self._draw_text(draw, (cols["name"], hy), "Игрок", self.font_small, MUTED)
        for key, label in (("acc", "Acc"), ("combo", "Combo"), ("pp", "PP"), ("score", "Score")):
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
            "acc": x1 - 330,
            "combo": x1 - 220,
            "pp": x1 - 120,
            "score": x1 - 22,
        }

    def _mlb_row(self, img, draw, box, row, cols, alt, is_viewer):
        x0, y0, x1, y1 = box
        if is_viewer:
            draw.rounded_rectangle(box, radius=12, fill=MINE_BG, outline=MINE, width=2)
        else:
            draw.rounded_rectangle(box, radius=12, fill=ROW_ALT if alt else ROW)

        mid = (y0 + y1) // 2
        # Place and crown share one centred column, so a "10" and a trophy sit
        # over each other rather than a few pixels apart down the page.
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
            ("pp", f"{float(row.get('pp') or 0):.1f}", self.font_label, pp_colour),
            ("score", f"{int(row.get('score') or 0):,}", self.font_label, MUTED),
        ):
            w = self._text_size(draw, text, font)[0]
            self._draw_text(draw, (cols[key] - w, mid - 11), text, font, colour)

    def _mlb_avatar(self, img, draw, avatar, x, mid, d):
        """A circular portrait inside the warm red glow ring the profile uses."""
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

    # ── the viewer's own row, when they are not on this page ──────────────

    def _mlb_viewer(self, img, draw, box, viewer):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=RADIUS, fill=MINE_BG, outline=MINE_DIM, width=2)
        self._draw_text(draw, (x0 + 20, y0 + 14), "ТВОЙ РЕЗУЛЬТАТ", self.font_stat_label, MINE)
        place = viewer.get("position")
        self._draw_text(draw, (x0 + 20, y0 + 40),
                        f"{place} место" if place else "нет результата", self.font_row, TEXT)

        stats = (
            ("Acc", f"{float(viewer.get('accuracy') or 0):.2f}%", TEXT),
            ("Combo", f"{int(viewer.get('combo') or 0):,}x", TEXT),
            ("PP", f"{float(viewer.get('pp') or 0):.1f}", MINE),
            ("Score", f"{int(viewer.get('score') or 0):,}", TEXT),
        )
        span = (x1 - x0 - 260) // len(stats)
        for i, (label, value, colour) in enumerate(stats):
            cx = x0 + 250 + span * i + span // 2
            vw = self._text_size(draw, value, self.font_label)[0]
            lw = self._text_size(draw, label, self.font_stat_label)[0]
            self._draw_text(draw, (cx - vw / 2, y0 + 26), value, self.font_label, colour)
            self._draw_text(draw, (cx - lw / 2, y0 + 52), label, self.font_stat_label, MUTED)

    # ── what the board says ───────────────────────────────────────────────

    def _mlb_titles(self, img, draw, box, titles):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("stars", 22, TOP_COLORS[1])
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), "ЛИДЕРЫ КАРТЫ", self.font_label, TEXT)

        ty = y0 + 52
        for title in titles:
            row = (x0 + 12, ty, x1 - 12, ty + 64)
            _panel(draw, row, fill=ROW, radius=12)
            tint = TITLE_TINTS.get(title.get("kind"), RECENT_ACCENT)
            icon = load_icon(title.get("icon") or "trophy", 24, tint)
            if icon:
                img.paste(icon, (x0 + 26, ty + 20), icon)
            value = title.get("value") or ""
            vw = self._text_size(draw, value, self.font_label)[0]
            limit = (x1 - 26 - vw) - (x0 + 64) - 12
            self._draw_text(draw, (x0 + 64, ty + 12),
                            self._fit(draw, title.get("label") or "", self.font_stat_label, limit),
                            self.font_stat_label, MUTED)
            self._draw_text(draw, (x0 + 64, ty + 34),
                            self._fit(draw, title.get("who") or "—", self.font_label, limit),
                            self.font_label, TEXT)
            self._draw_text(draw, (x1 - 26 - vw, ty + 22), value, self.font_label, tint)
            ty += 74

    def _mlb_stats(self, img, draw, box, data):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("column-chart", 22, RECENT_ACCENT)
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), "СТАТИСТИКА КАРТЫ", self.font_label, TEXT)

        # No play count here: the header states it, and once was enough.
        lines = (
            ("Участников", f"{int(data.get('unique_players') or 0)}"),
            ("Средний результат", data.get("average") or "—"),
        )
        ly = y0 + 54
        for label, value in lines:
            self._draw_text(draw, (x0 + 20, ly + 3), label, self.font_small, MUTED)
            vw = self._text_size(draw, value, self.font_label)[0]
            self._draw_text(draw, (x1 - 20 - vw, ly), value, self.font_label, TEXT)
            ly += 46

    def _mlb_updated(self, img, draw, box, data):
        """Its own strip rather than a footnote inside the stats panel, where it
        read as a third statistic."""
        x0, y0, x1, y1 = box
        _panel(draw, box, fill=ROW)
        icon = load_icon("clock", 20, MUTED)
        if icon:
            img.paste(icon, (x0 + 18, y0 + 22), icon)
        self._draw_text(draw, (x0 + 48, y0 + 12), "Последнее обновление",
                        self.font_small, MUTED)
        self._draw_text(draw, (x0 + 48, y0 + 32), data.get("updated") or "",
                        self.font_stat_label, TEXT)

    # ── the record changing hands ─────────────────────────────────────────

    def _mlb_history(self, img, draw, box, history):
        x0, y0, x1, y1 = box
        _panel(draw, box)
        icon = load_icon("clock", 22, RECENT_ACCENT)
        if icon:
            img.paste(icon, (x0 + 16, y0 + 15), icon)
        self._draw_text(draw, (x0 + 46, y0 + 16), "ИСТОРИЯ РЕКОРДА", self.font_label, TEXT)

        # Newest first, left to right, with the current holder marked. The
        # chevrons sit in the gaps between cells rather than against them, so
        # the row reads as a chain instead of as cards with commas.
        count = len(history)
        inner = (x1 - x0) - 32
        gap = 26
        cell_w = (inner - gap * (count - 1)) // max(count, 1)
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
