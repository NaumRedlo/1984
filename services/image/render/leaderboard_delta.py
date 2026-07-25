"""Weekly "growth" leaderboard card.

One screen: header with the period, the top rows ranked by growth, then the
viewer's own row pinned below a divider. Each row is wrapped in a frame whose
colour encodes the place — gold/silver/bronze for the podium, the bot's coral
accent for "this is you" (which wins over a medal when both apply).
"""

from io import BytesIO

from PIL import Image, ImageDraw

from services.image.base import BaseCardRenderer
from services.image import colors
from services.image.constants import TOP_COLORS

_W = 860
_PAD = 28
_ROW_H = 62
_ROW_GAP = 6
_AVATAR = 38
_RADIUS = 12
_SELF = colors.ACCENT           # coral — "this is you"
_ROW_BG = (27, 25, 34)


class LeaderboardDeltaRenderer(BaseCardRenderer):
    def _circle(self, src: Image.Image, size: int) -> Image.Image:
        ss = 4
        sq = src.convert("RGBA").resize((size * ss, size * ss), Image.LANCZOS)
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
        sq.putalpha(mask)
        return sq.resize((size, size), Image.LANCZOS)

    def _avatar(self, img, x, y, size, avatar_data, name) -> None:
        av = None
        if avatar_data:
            try:
                av = self._circle(Image.open(BytesIO(avatar_data)), size)
            except Exception:
                av = None
        if av is not None:
            img.paste(av, (x, y), av)
            return
        self._aa_ellipse_fill(img, (x, y, x + size, y + size), fill=colors.PANEL)
        d = ImageDraw.Draw(img)
        initials = (name or "?").strip()[:2].upper() or "?"
        tw, th = self._text_size(d, initials, self.font_small)
        self._draw_text(d, (x + (size - tw) // 2, y + (size - th) // 2 - 1),
                        initials, self.font_small, colors.TEXT_MUTED)

    def _frame_color(self, position, is_self: bool):
        """Own row wins over a medal — you should always be able to find yourself."""
        if is_self:
            return _SELF
        return TOP_COLORS.get(position)

    def _draw_row(self, img, y: int, row: dict, fmt, lang: str) -> None:
        is_self = bool(row.get("is_self"))
        pos = row.get("position")
        frame = self._frame_color(pos, is_self)
        draw = ImageDraw.Draw(img)

        x0, x1 = _PAD, _W - _PAD
        self._aa_rounded_fill(img, (x0, y, x1, y + _ROW_H), radius=_RADIUS, fill=_ROW_BG)
        if frame:
            self._aa_rounded_outline(img, (x0, y, x1, y + _ROW_H), radius=_RADIUS,
                                     outline=frame, width=2)
        draw = ImageDraw.Draw(img)

        cy = y + _ROW_H // 2

        # Place — medal-tinted for the podium, muted otherwise.
        pos_txt = str(pos) if pos else "—"
        pos_col = frame or colors.TEXT_MUTED
        pw, ph = self._text_size(draw, pos_txt, self.font_row)
        self._draw_text(draw, (x0 + 20, cy - ph // 2 - 2), pos_txt, self.font_row, pos_col)

        av_x = x0 + 56
        self._avatar(img, av_x, cy - _AVATAR // 2, _AVATAR, row.get("avatar_data"), row.get("username", ""))
        draw = ImageDraw.Draw(img)

        # Name + rank title.
        name_x = av_x + _AVATAR + 14
        name = str(row.get("username") or "???")
        self._draw_text(draw, (name_x, cy - 18), name, self.font_label, colors.TEXT_PRIMARY)
        title = row.get("rank_title_label") or ""
        if row.get("gap_label"):
            title = f"{title} · {row['gap_label']}" if title else row["gap_label"]
        if title:
            self._draw_text(draw, (name_x, cy + 3), title, self.font_small, colors.TEXT_MUTED)

        # Movement column (far right).
        mv = row.get("movement")
        mv_x = x1 - 20
        if mv is None:
            mv_txt, mv_col = fmt["new"], colors.TEXT_MUTED
        elif mv > 0:
            mv_txt, mv_col = f"▲ {mv}", colors.POSITIVE
        elif mv < 0:
            mv_txt, mv_col = f"▼ {abs(mv)}", colors.TEXT_MUTED
        else:
            mv_txt, mv_col = "— 0", colors.TEXT_MUTED
        mw, mh = self._text_size(draw, mv_txt, self.font_small)
        self._draw_text(draw, (mv_x - mw, cy - mh // 2 - 1), mv_txt, self.font_small, mv_col)

        # Delta + lifetime total, right-aligned before the movement column.
        val_right = mv_x - 62
        delta_txt = row.get("delta_label", "")
        dw, dh = self._text_size(draw, delta_txt, self.font_label)
        self._draw_text(draw, (val_right - dw, cy - 18), delta_txt, self.font_label, colors.POSITIVE)
        abs_txt = row.get("absolute_label", "")
        if abs_txt:
            aw, _ = self._text_size(draw, abs_txt, self.font_small)
            self._draw_text(draw, (val_right - aw, cy + 4), abs_txt, self.font_small, colors.TEXT_MUTED)

    def render(self, data: dict) -> BytesIO:
        rows = list(data.get("rows") or [])
        self_row = data.get("self_row")
        fmt = data.get("fmt") or {"new": "new"}
        lang = str(data.get("lang", "en"))

        # Pin the viewer's row only when it isn't already visible above.
        pinned = self_row if (self_row and not any(r.get("user_id") == self_row.get("user_id") for r in rows)) else None
        # If they ARE in the top, mark that row as self so it gets the coral frame.
        if self_row and pinned is None:
            for r in rows:
                if r.get("user_id") == self_row.get("user_id"):
                    r["is_self"] = True

        head_h = 96
        body_h = len(rows) * (_ROW_H + _ROW_GAP)
        pin_h = (18 + _ROW_H) if pinned else 0
        foot_h = 40
        h = head_h + body_h + pin_h + foot_h + _PAD

        img = Image.new("RGB", (_W, h), colors.BG)
        draw = ImageDraw.Draw(img)

        # ── Header ───────────────────────────────────────────────────────
        self._draw_text(draw, (_PAD, 28), data.get("title", ""), self.font_title, colors.TEXT_PRIMARY)
        self._draw_text(draw, (_PAD, 62), data.get("subtitle", ""), self.font_small, colors.TEXT_MUTED)
        right = data.get("meta_right", "")
        if right:
            rw, _ = self._text_size(draw, right, self.font_small)
            self._draw_text(draw, (_W - _PAD - rw, 30), right, self.font_small, colors.TEXT_MUTED)
        sub_right = data.get("meta_right_sub", "")
        if sub_right:
            sw, _ = self._text_size(draw, sub_right, self.font_small)
            self._draw_text(draw, (_W - _PAD - sw, 50), sub_right, self.font_small, (92, 90, 104))

        # ── Rows ─────────────────────────────────────────────────────────
        y = head_h
        for row in rows:
            self._draw_row(img, y, row, fmt, lang)
            draw = ImageDraw.Draw(img)
            y += _ROW_H + _ROW_GAP

        if not rows:
            msg = data.get("empty_label", "")
            mw, _ = self._text_size(draw, msg, self.font_label)
            self._draw_text(draw, ((_W - mw) // 2, y + 20), msg, self.font_label, colors.TEXT_MUTED)
            y += 60

        # ── Pinned own row ───────────────────────────────────────────────
        if pinned:
            sep_y = y + 8
            draw.line([(_PAD, sep_y), (_W - _PAD, sep_y)], fill=colors.DIVIDER, width=1)
            y = sep_y + 10
            self._draw_row(img, y, pinned, fmt, lang)
            draw = ImageDraw.Draw(img)
            y += _ROW_H

        # ── Footer ───────────────────────────────────────────────────────
        foot_y = h - _PAD - 6
        self._draw_text(draw, (_PAD, foot_y), data.get("footer_left", ""), self.font_small, (92, 90, 104))
        fr = data.get("footer_right", "")
        if fr:
            fw, _ = self._text_size(draw, fr, self.font_small)
            self._draw_text(draw, (_W - _PAD - fw, foot_y), fr, self.font_small, (92, 90, 104))

        return self._save(img)


_renderer = LeaderboardDeltaRenderer()


def render_delta_leaderboard(data: dict) -> bytes:
    """Render the weekly growth leaderboard and return PNG bytes."""
    return _renderer.render(data).getvalue()
