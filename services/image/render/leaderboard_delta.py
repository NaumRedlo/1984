"""Leaderboard card — shared by the all-time and weekly-growth modes.

One screen: header with the period, the ranked rows, then the viewer's own row
pinned below a divider when they're outside the visible top. Each row is wrapped
in a frame whose colour encodes the place — gold/silver/bronze for the podium,
the bot's coral accent for "this is you" (which wins over a medal when both
apply). Avatars carry a softly-glowing red ring.
"""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

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
_RING = (226, 72, 72)           # avatar ring + its glow
_MV_COL_W = 58                  # movement column, centred within
_POS_COL_X = 12                 # place column: offset from the row's left edge
_POS_COL_W = 34                 # ...and its width, so 1 and 14 share a centre


class LeaderboardDeltaRenderer(BaseCardRenderer):
    def _circle(self, src: Image.Image, size: int) -> Image.Image:
        ss = 4
        sq = src.convert("RGBA").resize((size * ss, size * ss), Image.LANCZOS)
        mask = Image.new("L", (size * ss, size * ss), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * ss - 1, size * ss - 1), fill=255)
        sq.putalpha(mask)
        return sq.resize((size, size), Image.LANCZOS)

    def _avatar_glow(self, img: Image.Image, boxes: list[tuple[int, int, int]]) -> Image.Image:
        """Blurred red halo behind every avatar, drawn in one pass."""
        if not boxes:
            return img
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for x, y, size in boxes:
            gd.ellipse((x - 3, y - 3, x + size + 3, y + size + 3),
                       outline=_RING + (255,), width=4)
        glow = glow.filter(ImageFilter.GaussianBlur(5))
        return Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    def _avatar(self, img, x, y, size, avatar_data, name) -> None:
        av = None
        if avatar_data:
            try:
                av = self._circle(Image.open(BytesIO(avatar_data)), size)
            except Exception:
                av = None
        if av is not None:
            img.paste(av, (x, y), av)
        else:
            self._aa_ellipse_fill(img, (x, y, x + size, y + size), fill=colors.PANEL)
            d = ImageDraw.Draw(img)
            initials = (name or "?").strip()[:2].upper() or "?"
            tw, th = self._text_size(d, initials, self.font_small)
            self._draw_text(d, (x + (size - tw) // 2, y + (size - th) // 2 - 1),
                            initials, self.font_small, colors.TEXT_MUTED)
        # Crisp ring on top of the (already composited) glow.
        self._aa_ellipse_outline(img, (x - 1, y - 1, x + size + 1, y + size + 1),
                                 outline=_RING, width=2)

    def _frame_color(self, position, is_self: bool):
        """Own row wins over a medal — you should always be able to find yourself."""
        if is_self:
            return _SELF
        return TOP_COLORS.get(position)

    def _text_centered(self, img, cx: int, cy: int, text: str, font, fill) -> None:
        """Draw `text` centred on (cx, cy).

        Width comes from the multifont measurement, not a raw textbbox:
        `_draw_text` swaps in a Cyrillic face per glyph, so measuring Russian
        text against the primary font alone reports the wrong width and the
        "centre" drifts. Vertical placement uses the ink box — Torus sits low
        in the em box, so centring on the nominal height leaves text high.
        """
        d = ImageDraw.Draw(img)
        tw, _ = self._text_size(d, text, font)
        bb = d.textbbox((0, 0), text, font=font)
        y = cy - (bb[1] + bb[3]) / 2
        self._draw_text(d, (int(round(cx - tw / 2)), int(round(y))), text, font, fill)

    def _movement_pill(self, img, cx: int, cy: int, label: str) -> None:
        """`NEW` as a green pill, with the word centred inside it."""
        d = ImageDraw.Draw(img)
        tw, th = self._text_size(d, label, self.font_small)
        w, h = tw + 20, th + 8
        x, y = cx - w // 2, cy - h // 2
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=h // 2, fill=(38, 62, 44))
        self._text_centered(img, cx, cy, label, self.font_small, colors.POSITIVE)

    def _draw_row(self, img, y: int, row: dict, fmt: dict, *, show_movement: bool,
                  value_color) -> tuple[int, int, int]:
        """Draw one row; returns the avatar box so the caller can glow it."""
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

        # Place number: centred in its own column both ways, so it sits square
        # on the avatar's axis whether it's "1" or "14".
        pos_txt = str(pos) if pos else "—"
        self._text_centered(img, x0 + _POS_COL_X + _POS_COL_W // 2, cy,
                            pos_txt, self.font_row, frame or colors.TEXT_MUTED)
        draw = ImageDraw.Draw(img)

        av_x, av_y = x0 + _POS_COL_X + _POS_COL_W + 10, cy - _AVATAR // 2
        self._avatar(img, av_x, av_y, _AVATAR, row.get("avatar_data"), row.get("username", ""))
        draw = ImageDraw.Draw(img)

        # Name + optional subtitle. With no subtitle the name centres on the
        # avatar's axis instead of sitting high with empty space under it.
        name_x = av_x + _AVATAR + 14
        name = str(row.get("username") or "???")
        subtitle = row.get("title_label") or ""
        if row.get("gap_label"):
            subtitle = f"{subtitle} · {row['gap_label']}" if subtitle else row["gap_label"]
        if subtitle:
            self._draw_text(draw, (name_x, cy - 18), name, self.font_label, colors.TEXT_PRIMARY)
            self._draw_text(draw, (name_x, cy + 3), subtitle, self.font_small,
                            row.get("title_color") or colors.TEXT_MUTED)
        else:
            _, nh = self._text_size(draw, name, self.font_label)
            self._draw_text(draw, (name_x, cy - nh // 2 - 2), name, self.font_label,
                            colors.TEXT_PRIMARY)

        # Movement column (far right) — omitted entirely in all-time mode.
        val_right = x1 - 20
        if show_movement:
            mv_cx = x1 - 20 - _MV_COL_W // 2
            mv = row.get("movement")
            if mv is None:
                self._movement_pill(img, mv_cx, cy, fmt.get("new", "NEW"))
                draw = ImageDraw.Draw(img)
            else:
                if mv > 0:
                    mv_txt, mv_col = f"▲ {mv}", colors.POSITIVE
                elif mv < 0:
                    mv_txt, mv_col = f"▼ {abs(mv)}", colors.TEXT_MUTED
                else:
                    mv_txt, mv_col = "— 0", colors.TEXT_MUTED
                mw, mh = self._text_size(draw, mv_txt, self.font_small)
                self._draw_text(draw, (mv_cx - mw // 2, cy - mh // 2 - 1),
                                mv_txt, self.font_small, mv_col)
            val_right = x1 - 20 - _MV_COL_W - 12

        # Main value + the smaller secondary line under it.
        main = row.get("value_label", "")
        dw, dh = self._text_size(draw, main, self.font_label)
        sub = row.get("sub_label", "")
        if sub:
            self._draw_text(draw, (val_right - dw, cy - 18), main, self.font_label, value_color)
            aw, _ = self._text_size(draw, sub, self.font_small)
            self._draw_text(draw, (val_right - aw, cy + 4), sub, self.font_small, colors.TEXT_MUTED)
        else:
            self._draw_text(draw, (val_right - dw, cy - dh // 2 - 2), main, self.font_label, value_color)

        return av_x, av_y, _AVATAR

    def render(self, data: dict) -> BytesIO:
        rows = list(data.get("rows") or [])
        self_row = data.get("self_row")
        fmt = data.get("fmt") or {"new": "NEW"}
        show_movement = bool(data.get("show_movement", True))
        value_color = colors.POSITIVE if data.get("value_positive", True) else colors.TEXT_PRIMARY
        empty_label = data.get("empty_label", "")

        # Pin the viewer's row only when it isn't already visible above; when it
        # is, just mark it so it picks up the coral frame.
        pinned = None
        if self_row:
            match = next((r for r in rows if r.get("user_id") == self_row.get("user_id")), None)
            if match is not None:
                match["is_self"] = True
            else:
                pinned = self_row

        # A viewer who hasn't played at all this period gets a plain line
        # instead of a "+0" row — there's nothing to show them yet.
        self_note = data.get("self_note") if not pinned else None
        if self_note:
            pinned = None

        head_h = 96
        body_h = len(rows) * (_ROW_H + _ROW_GAP)
        empty_h = 56 if (not rows and empty_label) else 0
        pin_h = (18 + _ROW_H) if pinned else 0
        note_h = 44 if self_note else 0
        foot_h = 34
        h = head_h + body_h + empty_h + pin_h + note_h + foot_h + _PAD

        img = Image.new("RGB", (_W, h), colors.BG)
        draw = ImageDraw.Draw(img)

        # ── Header ───────────────────────────────────────────────────────
        self._draw_text(draw, (_PAD, 28), data.get("title", ""), self.font_title, colors.TEXT_PRIMARY)
        self._draw_text(draw, (_PAD, 62), data.get("subtitle", ""), self.font_small, colors.TEXT_MUTED)
        for text_val, ty, col in ((data.get("meta_right", ""), 30, colors.TEXT_MUTED),
                                  (data.get("meta_right_sub", ""), 50, (92, 90, 104))):
            if text_val:
                tw, _ = self._text_size(draw, text_val, self.font_small)
                self._draw_text(draw, (_W - _PAD - tw, ty), text_val, self.font_small, col)

        # ── Rows ─────────────────────────────────────────────────────────
        avatar_boxes = []
        y = head_h
        for row in rows:
            avatar_boxes.append(
                self._draw_row(img, y, row, fmt, show_movement=show_movement, value_color=value_color))
            y += _ROW_H + _ROW_GAP

        if empty_h:
            d = ImageDraw.Draw(img)
            mw, _ = self._text_size(d, empty_label, self.font_label)
            self._draw_text(d, ((_W - mw) // 2, y + 14), empty_label, self.font_label, colors.TEXT_MUTED)
            y += empty_h

        # ── Pinned own row ───────────────────────────────────────────────
        if pinned:
            sep_y = y + 8
            ImageDraw.Draw(img).line([(_PAD, sep_y), (_W - _PAD, sep_y)],
                                     fill=colors.DIVIDER, width=1)
            y = sep_y + 10
            avatar_boxes.append(
                self._draw_row(img, y, pinned, fmt, show_movement=show_movement, value_color=value_color))
            y += _ROW_H

        if self_note:
            sep_y = y + 8
            ImageDraw.Draw(img).line([(_PAD, sep_y), (_W - _PAD, sep_y)],
                                     fill=colors.DIVIDER, width=1)
            d = ImageDraw.Draw(img)
            nw, _ = self._text_size(d, self_note, self.font_label)
            self._draw_text(d, ((_W - nw) // 2, sep_y + 14), self_note,
                            self.font_label, colors.TEXT_MUTED)
            y = sep_y + note_h

        img = self._avatar_glow(img, avatar_boxes)
        # The glow pass flattens the image, so re-draw the avatars' crisp pixels.
        for row, (ax, ay, size) in zip(rows + ([pinned] if pinned else []), avatar_boxes):
            self._avatar(img, ax, ay, size, row.get("avatar_data"), row.get("username", ""))

        # ── Footer (centred) ─────────────────────────────────────────────
        fr = data.get("footer_right", "")
        if fr:
            self._text_centered(img, _W // 2, h - _PAD + 2, fr, self.font_small, (92, 90, 104))

        return self._save(img)


_renderer = LeaderboardDeltaRenderer()


def render_delta_leaderboard(data: dict) -> bytes:
    """Render a leaderboard card and return PNG bytes."""
    return _renderer.render(data).getvalue()
