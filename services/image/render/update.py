import asyncio
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

from services.image.render.profile import (
    CARD_M, COL_BG, COL_CARD, COL_CARD_BORDER, COL_CORAL, COL_GREEN, COL_MUTED, COL_RED, COL_WHITE, _sp,
)
from services.image.render.recent import _ruleset_icon
from services.image.render.top_plays import ROW_CORNER_R
from services.image.utils import cover_center_crop, load_icon
from utils.osu import rulesets

UP_W = 820
_ROW_H, _ROW_GAP = 79, 9
_NEG = (236, 104, 104)

_STRINGS = {
    "en": {
        "header": "UPDATE", "since": "since {when}", "first_since": "tracking starts now",
        "pp": "PP", "rank": "RANK", "country": "COUNTRY", "acc": "ACCURACY", "plays": "PLAYS",
        "new": "NEW TOP PLAYS", "none": "No new plays in the top since the last update",
        "first": "Tracking started — the next update will show what changed",
        "more": "and {n} more",
    },
    "ru": {
        "header": "ОБНОВЛЕНИЕ", "since": "с {when}", "first_since": "отслеживание начинается сейчас",
        "pp": "PP", "rank": "РАНГ", "country": "В СТРАНЕ", "acc": "ТОЧНОСТЬ", "plays": "ИГРЫ",
        "new": "НОВОЕ В ТОПЕ", "none": "С прошлого обновления в топе ничего нового",
        "first": "Отслеживание начато — следующее обновление покажет изменения",
        "more": "и ещё {n}",
    },
}


def _lang(data) -> dict:
    return _STRINGS.get((data.get("lang") or "en").lower(), _STRINGS["en"])


def _local(when: datetime) -> str:
    from zoneinfo import ZoneInfo

    from config.settings import TIMEZONE
    aware = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(TIMEZONE)).strftime("%d.%m.%Y %H:%M")


def _signed(value: float, digits: int = 0, suffix: str = "") -> str:
    text = f"{abs(value):,.{digits}f}".replace(",", " ")
    return ("+" if value > 0 else "−" if value < 0 else "±") + text + suffix


class UpdateCardMixin:

    def generate_update_card(self, data: Dict, avatar: Optional[Image.Image] = None,
                             covers: Optional[List[Optional[Image.Image]]] = None) -> BytesIO:
        S = _lang(data)
        rows = data.get("rows") or []
        body_rows = max(1, len(rows))
        head_h, strip_h, stats_h = 64, 84, 104
        body_y = CARD_M + head_h + strip_h + stats_h + 3 * 14 + 30
        H = body_y + body_rows * (_ROW_H + _ROW_GAP) + (26 if data.get("more") else 0) + CARD_M + 12
        W = UP_W
        img, draw = self._create_canvas(W, H)
        draw.rectangle([(0, 0), (W, H)], fill=COL_BG)
        self._pf_panel(img, (CARD_M, CARD_M, W - CARD_M, H - CARD_M), radius=24, fill=COL_CARD, border=COL_CARD_BORDER)
        fonts = self._tp_fonts()
        x0, x1 = CARD_M + 28, W - CARD_M - 28

        # header
        cy = CARD_M + head_h // 2 + 6
        draw = ImageDraw.Draw(img)
        arrow = load_icon("arrowup", 20, colour=COL_RED)
        title_w = self._text_size(draw, S["header"], fonts["h_title"])[0]
        start = (W - title_w - (30 if arrow else 0)) // 2
        if arrow:
            img.paste(arrow, (start, int(round(cy - arrow.height / 2))), arrow)
            start += 30
        self._text_mid(ImageDraw.Draw(img), start, cy, S["header"], fonts["h_title"], COL_WHITE)
        ruleset = int(data.get("ruleset") or 0)
        icon = _ruleset_icon(ruleset, 22, COL_RED)
        img.paste(icon, (x0, int(cy - 11)), icon)
        self._text_mid(ImageDraw.Draw(img), x0 + 30, cy, rulesets.TITLES[ruleset], fonts["row_meta"], COL_MUTED)

        # the player
        sy0 = CARD_M + head_h + 14
        self._pf_panel(img, (x0, sy0, x1, sy0 + strip_h), radius=14)
        d = 52
        ax, ay = x0 + 16, sy0 + (strip_h - d) // 2
        glow = Image.new("RGBA", (d + 40, d + 40), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((12, 12, 28 + d, 28 + d), fill=(228, 72, 72, 110))
        glow = glow.filter(ImageFilter.GaussianBlur(8))
        img.paste(glow, (ax - 20, ay - 20), glow)
        if avatar:
            av = avatar.resize((d, d), Image.LANCZOS).convert("RGBA")
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(av, (ax, ay), mask)
        else:
            self._aa_ellipse_fill(img, (ax, ay, ax + d, ay + d), fill=(52, 40, 42))
        self._aa_ellipse_outline(img, (ax, ay, ax + d, ay + d), outline=(228, 76, 76), width=3)
        draw = ImageDraw.Draw(img)
        mid = sy0 + strip_h / 2
        self._text_mid(draw, ax + d + 16, mid - 12, str(data.get("username") or "?"), fonts["name"], COL_WHITE)
        since = data.get("since")
        when = S["since"].format(when=_local(since)) if isinstance(since, datetime) else S["first_since"]
        self._text_mid(draw, ax + d + 16, mid + 17, when, fonts["handle"], (188, 150, 152))

        # the numbers
        ty0 = sy0 + strip_h + 14
        self._pf_panel(img, (x0, ty0, x1, ty0 + stats_h), radius=14)
        first = bool(data.get("first"))
        now = data.get("now") or {}
        delta = data.get("delta") or {}
        cols = [
            (S["pp"], f"{_sp(int(round(now.get('pp') or 0)))}pp", delta.get("pp", 0.0), 1, "pp"),
            (S["rank"], f"#{_sp(now['global_rank'])}" if now.get("global_rank") else "—", delta.get("global_rank", 0), 0, ""),
            (S["country"], f"#{_sp(now['country_rank'])}" if now.get("country_rank") else "—", delta.get("country_rank", 0), 0, ""),
            (S["acc"], f"{(now.get('accuracy') or 0):.2f}%", delta.get("accuracy", 0.0), 2, "%"),
            (S["plays"], _sp(now.get("play_count") or 0), delta.get("play_count", 0), 0, ""),
        ]
        width = (x1 - x0) / len(cols)
        for i, (label, value, change, digits, suffix) in enumerate(cols):
            cx = x0 + width * (i + 0.5)
            draw = ImageDraw.Draw(img)
            self._text_mid(draw, cx, ty0 + 24, label, fonts["pp_lbl"], COL_MUTED, align="center")
            self._text_mid(draw, cx, ty0 + 52, value, fonts["pp_big"] if i == 0 else fonts["row_title"],
                           COL_CORAL if i == 0 else COL_WHITE, align="center")
            if first:
                text, colour = "—", COL_MUTED
            else:
                text = _signed(change, digits, suffix)
                colour = COL_GREEN if change > 0 else _NEG if change < 0 else COL_MUTED
            self._text_mid(draw, cx, ty0 + 80, text, fonts["row_meta"], colour, align="center")

        # the new plays
        ny = ty0 + stats_h + 14
        draw = ImageDraw.Draw(img)
        head = S["new"] + (f" · {data.get('new_count')}" if data.get("new_count") else "")
        self._text_mid(draw, x0 + 4, ny + 10, head, fonts["row_meta"], COL_RED)
        ry = body_y
        if not rows:
            self._pf_panel(img, (x0, ry, x1, ry + _ROW_H), radius=ROW_CORNER_R)
            self._text_mid(ImageDraw.Draw(img), (x0 + x1) / 2, ry + _ROW_H / 2,
                           S["first"] if first else S["none"], fonts["row_meta"], COL_MUTED, align="center")
        covers = covers or []
        for i, row in enumerate(rows):
            self._tp_row(img, x0, ry, x1 - x0, _ROW_H, row, fonts, covers[i] if i < len(covers) else None)
            ry += _ROW_H + _ROW_GAP
        if data.get("more"):
            self._text_mid(ImageDraw.Draw(img), (x0 + x1) / 2, ry + 6, S["more"].format(n=data["more"]),
                           fonts["row_meta"], COL_MUTED, align="center")
        return self._save(img)

    async def generate_update_card_async(self, data: Dict) -> BytesIO:
        from services.image.utils import _none_coro, download_image
        urls = [f"https://assets.ppy.sh/beatmaps/{r['beatmapset_id']}/covers/cover.jpg" if r.get("beatmapset_id") else None
                for r in data.get("rows") or []]
        got = await asyncio.gather(
            download_image(data["avatar_url"]) if data.get("avatar_url") else _none_coro(),
            *(download_image(u) if u else _none_coro() for u in urls), return_exceptions=True)
        ok = [g if not isinstance(g, Exception) else None for g in got]
        return await asyncio.to_thread(self.generate_update_card, data, ok[0], ok[1:])
