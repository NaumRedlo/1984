"""Rendered card for a map request notification.

Layout: a header (sender avatar + name + a "new request!" tag, SR badge on the
right); a bordered horizontal frame around the map block (artist, title, a
difficulty pill, and icon chips for BPM / length / max-combo); then the pass
conditions as individual pills with the required mods as badges. Accept/decline
stay as Telegram inline buttons under the photo.
"""

from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw

from services.image.base import BaseCardRenderer
from services.image import colors
from services.image.utils import load_icon
from services.image.render.recent import _sr_color
from utils.i18n import t
from utils.formatting.text import format_length

_W = 760
_M = 20                     # outer margin (dark border around the card)
_PAD = 44                   # content padding from the image edge
_GOLD = (255, 202, 40)
_PILL_BG = (36, 30, 38)
_FRAME_INSET = 16           # frame sits this far outside the content
_PILL_H = 34
_BADGE = 30
_GAP = 8
_ROW_GAP = 10


class RequestCardRenderer(BaseCardRenderer):
    # ── small drawing helpers ────────────────────────────────────────────
    def _circle(self, src: Image.Image, size: int) -> Image.Image:
        ss = 4
        sq = src.convert("RGBA").resize((size * ss, size * ss), Image.LANCZOS)
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
        sq.putalpha(mask)
        return sq.resize((size, size), Image.LANCZOS)

    def _tint(self, icon: Image.Image, color: tuple) -> Image.Image:
        solid = Image.new("RGBA", icon.size, (*color, 255))
        solid.putalpha(icon.convert("RGBA").split()[-1])
        return solid

    def _avatar(self, img, x, y, size, avatar_bytes: Optional[bytes], name: str) -> None:
        av = None
        if avatar_bytes:
            try:
                av = self._circle(Image.open(BytesIO(avatar_bytes)), size)
            except Exception:
                av = None
        if av is not None:
            img.paste(av, (x, y), av)
        else:
            self._aa_ellipse_fill(img, (x, y, x + size, y + size), fill=colors.PANEL)
            d = ImageDraw.Draw(img)
            initial = (name or "?").strip()[:1].upper() or "?"
            tw, th = self._text_size(d, initial, self.font_row)
            self._draw_text(d, (x + (size - tw) // 2, y + (size - th) // 2 - 2),
                            initial, self.font_row, colors.TEXT_MUTED)
        self._aa_ellipse_outline(img, (x - 1, y - 1, x + size + 1, y + size + 1),
                                 outline=colors.CARD_BORDER, width=2)

    def _sr_badge(self, img, x_right, y, sr: float) -> None:
        col = _sr_color(sr)
        lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
        fg = (20, 20, 24) if lum > 150 else (255, 255, 255)
        star_fg = _GOLD if lum <= 150 else (20, 20, 24)
        text = f"{sr:.2f}"
        star = load_icon("star", size=16)
        if star:
            star = self._tint(star, star_fg)
        d = ImageDraw.Draw(img)
        tw, th = self._text_size(d, text, self.font_row)
        sw = (star.width + 5) if star else 0
        pad_x, pad_y = 14, 8
        w, h = sw + tw + pad_x * 2, th + pad_y * 2
        x = x_right - w
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=14, fill=col)
        ix = x + pad_x
        if star:
            img.paste(star, (ix, y + (h - star.height) // 2), star)
            ix += star.width + 5
        self._draw_text(d, (ix, y + pad_y - 2), text, self.font_row, fg)

    def _icon_chip(self, img, x, y, icon_name: str, value: str, h=36) -> int:
        d = ImageDraw.Draw(img)
        icon = load_icon(icon_name, size=18)
        if icon:
            icon = self._tint(icon, colors.TEXT_MUTED)
        tw, th = self._text_size(d, value, self.font_label)
        pad_x = 14
        iw = (icon.width + 8) if icon else 0
        w = pad_x * 2 + iw + tw
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=12, fill=_PILL_BG)
        ix = x + pad_x
        if icon:
            img.paste(icon, (ix, y + (h - icon.height) // 2), icon)
            ix += icon.width + 8
        self._draw_text(d, (ix, y + (h - th) // 2 - 2), value, self.font_label, colors.TEXT_PRIMARY)
        return x + w

    def _text_pill(self, img, x, y, text: str, *, bg, fg, h=_PILL_H) -> int:
        d = ImageDraw.Draw(img)
        tw, th = self._text_size(d, text, self.font_label)
        pad_x = 14
        w = pad_x * 2 + tw
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=h // 2, fill=bg)
        self._draw_text(d, (x + pad_x, y + (h - th) // 2 - 2), text, self.font_label, fg)
        return x + w

    def _pill_width(self, draw, text: str) -> int:
        return self._text_size(draw, text, self.font_label)[0] + 28

    # ── main render ──────────────────────────────────────────────────────
    def render(self, data: dict) -> BytesIO:
        lang = str(data.get("lang", "en")).lower()
        pills = list(data.get("condition_pills") or [])
        mods = [m for m in (data.get("mods") or [])]

        # Measure the conditions flow (pills + mod badges) to size the canvas.
        scratch = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        items = [("pill", p, self._pill_width(scratch, p)) for p in pills]
        items += [("mod", m, _BADGE) for m in mods]
        max_w = (_W - _PAD) - _PAD
        rows, cur, curw = [], [], 0
        for it in items:
            w = it[2]
            if cur and curw + _GAP + w > max_w:
                rows.append(cur)
                cur, curw = [], 0
            cur.append(it)
            curw += (_GAP if curw else 0) + w
        if cur:
            rows.append(cur)
        n_rows = max(1, len(rows))

        # Vertical plan.
        frame_top = 116
        artist_y = frame_top + 18
        title_y = artist_y + 26
        diff_y = title_y + 46
        chips_y = diff_y + 44
        frame_bottom = chips_y + 36 + 18
        cond_y = frame_bottom + 22
        cond_bottom = cond_y + n_rows * _PILL_H + (n_rows - 1) * _ROW_GAP
        h = cond_bottom + _PAD

        img = Image.new("RGB", (_W, h), colors.BG)
        self._aa_rounded_fill(img, (_M, _M, _W - _M, h - _M), radius=22, fill=colors.CARD)
        self._aa_rounded_outline(img, (_M, _M, _W - _M, h - _M), radius=22,
                                 outline=colors.CARD_BORDER, width=2)
        draw = ImageDraw.Draw(img)

        # ── Header: avatar + sender + "new request!" tag ─────────────────
        av = 56
        self._avatar(img, _PAD, 40, av, data.get("avatar_bytes"), data.get("sender_name", ""))
        hx = _PAD + av + 16
        sender = str(data.get("sender_name") or "?")
        self._draw_text(draw, (hx, 46), sender, self.font_row, colors.TEXT_PRIMARY)
        sw = self._text_size(draw, sender, self.font_row)[0]
        tag = "  ·  " + t("req.card.new", lang)
        self._draw_text(draw, (hx + sw, 50), tag, self.font_label, colors.POSITIVE)

        sr = float(data.get("star_rating") or 0.0)
        if sr > 0:
            self._sr_badge(img, _W - _PAD, 42, sr)

        # ── Framed map block ─────────────────────────────────────────────
        self._aa_rounded_outline(img, (_PAD - _FRAME_INSET, frame_top,
                                       _W - _PAD + _FRAME_INSET, frame_bottom),
                                 radius=18, outline=colors.PANEL_BORDER, width=2)

        artist = str(data.get("artist") or "")
        title = str(data.get("title") or "???")
        version = str(data.get("version") or "")
        if artist:
            self._draw_text(draw, (_PAD, artist_y), artist, self.font_subtitle, colors.TEXT_MUTED)
        self._draw_text(draw, (_PAD, title_y), title, self.font_big, colors.TEXT_PRIMARY)
        if version:
            self._text_pill(img, _PAD, diff_y, f"[{version}]", bg=_PILL_BG, fg=_GOLD, h=32)

        # Icon chips: BPM · length · max-combo.
        x = _PAD
        bpm = data.get("bpm")
        if bpm:
            x = self._icon_chip(img, x, chips_y, "bpm", f"{int(round(float(bpm)))}") + 10
        length = data.get("length")
        if length:
            x = self._icon_chip(img, x, chips_y, "timer", format_length(length)) + 10
        combo = data.get("max_combo")
        if combo:
            x = self._icon_chip(img, x, chips_y, "combo", f"{int(combo)}x") + 10

        # ── Conditions: pills + mod badges (flowed) ──────────────────────
        y = cond_y
        for row in rows:
            rx = _PAD
            for kind, val, w in row:
                if kind == "pill":
                    self._text_pill(img, rx, y, val, bg=_PILL_BG, fg=colors.POSITIVE)
                    rx += w + _GAP
                else:
                    self._draw_mod_badge(img, rx, y + (_PILL_H - _BADGE) // 2, val, size=_BADGE)
                    rx += w + _GAP
            y += _PILL_H + _ROW_GAP

        return self._save(img)


_renderer = RequestCardRenderer()


def render_request_card(data: dict) -> bytes:
    """Render a request card and return PNG bytes."""
    return _renderer.render(data).getvalue()
