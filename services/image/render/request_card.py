"""Rendered card for a map request notification.

A flat dark card (no cover) matching the app's card language: sender avatar +
name + relative time, an SR badge top-right, the map's artist / title /
[difficulty], BPM and length pills, a conditions line, and the sender's optional
note. Accept/decline stay as Telegram inline buttons under the photo.
"""

from io import BytesIO
from datetime import datetime, timezone
from typing import Optional

from PIL import Image, ImageDraw

from services.image.base import BaseCardRenderer
from services.image import colors
from services.image.utils import load_icon
from services.image.render.recent import _sr_color
from utils.formatting.text import format_length

_W = 760
_M = 20                     # outer margin (dark border around the card)
_PAD = 44                   # content padding from the image edge
_GOLD = (255, 202, 40)      # difficulty name / star
_PILL_BG = (36, 30, 38)


def _reltime(dt: Optional[datetime], ru: bool) -> str:
    if not dt:
        return "только что" if ru else "just now"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 60:
        return "только что" if ru else "just now"
    if secs < 3600:
        return f"{int(secs // 60)} мин назад" if ru else f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)} ч назад" if ru else f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)} д назад" if ru else f"{int(secs // 86400)}d ago"


class RequestCardRenderer(BaseCardRenderer):
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

    def _avatar(self, img: Image.Image, x: int, y: int, size: int,
                avatar_bytes: Optional[bytes], name: str) -> None:
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

    def _sr_badge(self, img: Image.Image, x_right: int, y: int, sr: float) -> None:
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
        w = sw + tw + pad_x * 2
        h = th + pad_y * 2
        x = x_right - w
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=14, fill=col)
        ix = x + pad_x
        if star:
            img.paste(star, (ix, y + (h - star.height) // 2), star)
            ix += star.width + 5
        self._draw_text(d, (ix, y + pad_y - 2), text, self.font_row, fg)

    def _pill(self, img: Image.Image, x: int, y: int, text: str) -> int:
        d = ImageDraw.Draw(img)
        tw, th = self._text_size(d, text, self.font_label)
        pad_x, pad_y = 16, 9
        w, h = tw + pad_x * 2, th + pad_y * 2
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=12, fill=_PILL_BG)
        self._draw_text(d, (x + pad_x, y + pad_y - 2), text, self.font_label, colors.TEXT_PRIMARY)
        return x + w + 10

    def render(self, data: dict) -> BytesIO:
        ru = str(data.get("lang", "en")).lower().startswith("ru")
        note = (data.get("note") or "").strip()

        # Vertical plan: header (avatar row) → artist/title/diff → pills →
        # conditions → optional note. Height depends on the note.
        pills_y = 250
        cond_y = pills_y + 58
        note_y = cond_y + 44
        content_bottom = (note_y + 30) if note else (cond_y + 30)
        h = content_bottom + _PAD

        img = Image.new("RGB", (_W, h), colors.BG)
        # Card panel.
        self._aa_rounded_fill(img, (_M, _M, _W - _M, h - _M), radius=22, fill=colors.CARD)
        self._aa_rounded_outline(img, (_M, _M, _W - _M, h - _M), radius=22,
                                 outline=colors.CARD_BORDER, width=2)
        draw = ImageDraw.Draw(img)

        # ── Header: avatar + sender + relative time ──────────────────────
        av_size = 56
        self._avatar(img, _PAD, _PAD, av_size, data.get("avatar_bytes"), data.get("sender_name", ""))
        hx = _PAD + av_size + 16
        sender = str(data.get("sender_name") or "?")
        self._draw_text(draw, (hx, _PAD + 6), sender, self.font_row, colors.TEXT_PRIMARY)
        sw = self._text_size(draw, sender, self.font_row)[0]
        rel = "  ·  " + _reltime(data.get("created_at"), ru)
        self._draw_text(draw, (hx + sw, _PAD + 10), rel, self.font_label, colors.TEXT_MUTED)

        # ── SR badge (top-right) ─────────────────────────────────────────
        sr = float(data.get("star_rating") or 0.0)
        if sr > 0:
            self._sr_badge(img, _W - _PAD, _PAD, sr)

        # ── Artist / Title / Difficulty ──────────────────────────────────
        artist = str(data.get("artist") or "")
        title = str(data.get("title") or "???")
        version = str(data.get("version") or "")
        if artist:
            self._draw_text(draw, (_PAD, 138), artist, self.font_subtitle, colors.TEXT_MUTED)
        self._draw_text(draw, (_PAD, 166), title, self.font_big, colors.TEXT_PRIMARY)
        if version:
            self._draw_text(draw, (_PAD, 208), f"[{version}]", self.font_row, _GOLD)

        # ── Pills: BPM · length ──────────────────────────────────────────
        x = _PAD
        bpm = data.get("bpm")
        if bpm:
            x = self._pill(img, x, pills_y, f"{int(round(float(bpm)))} BPM")
        length = data.get("length")
        if length:
            x = self._pill(img, x, pills_y, format_length(length))

        # ── Conditions ───────────────────────────────────────────────────
        cond = str(data.get("conditions_text") or "").strip()
        if cond:
            self._draw_text(draw, (_PAD, cond_y), f"📋 {cond}", self.font_label, colors.POSITIVE)

        # ── Note (optional) ──────────────────────────────────────────────
        if note:
            self._draw_text(draw, (_PAD, note_y), f"«{note}»", self.font_subtitle, colors.TEXT_MUTED)

        return self._save(img)


_renderer = RequestCardRenderer()


def render_request_card(data: dict) -> bytes:
    """Render a request card and return PNG bytes."""
    return _renderer.render(data).getvalue()
