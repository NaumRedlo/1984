"""Active-duel status card.

A head-to-head "VS" card for a live duel: both players (avatar, player cover,
flag, name, division / rating), the round score, a pip strip visualising every
map of the auto-built pool (won by p1 / p2 / void / now-playing / pending), and
a panel for the map currently being played.  Replaces the text-only
``/duelstatus``.
"""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_GREEN, PADDING_X, PANEL_BG, TORUS_BOLD,
)
from services.image.utils import (
    download_image, cover_center_crop, rounded_rect_crop, load_flag,
)

# Two-corner identity colours: a warm red (player 1) vs a cool blue (player 2).
P1_COLOR = (224, 90, 80)
P2_COLOR = (80, 150, 235)
GOLD = (255, 215, 0)
AMBER = (220, 180, 60)
VOID_COLOR = (78, 78, 98)
PENDING_COLOR = (44, 44, 62)


class DuelStatusCardMixin:

    def generate_duel_status_card(
        self,
        data: Dict,
        p1_avatar: Optional[Image.Image] = None,
        p2_avatar: Optional[Image.Image] = None,
        p1_cover: Optional[Image.Image] = None,
        p2_cover: Optional[Image.Image] = None,
        map_cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W = 800
        header_h = 28
        vs_h = 168
        pips_h = 56
        map_h = 92
        bottom_pad = 14
        H = header_h + vs_h + pips_h + map_h + bottom_pad
        cx = W // 2

        mode = str(data.get("mode", "casual"))
        mode_label = "RANKED" if mode == "ranked" else "CASUAL"
        status = str(data.get("status", "round_active"))

        total_rounds = int(data.get("total_rounds", 0) or 0)
        win_target = int(data.get("win_target", 0) or 0)
        current_round = int(data.get("current_round", 0) or 0)
        p1won, p2won = data.get("score", (0, 0))
        rounds = data.get("rounds", []) or []
        cur_map = data.get("current_map")

        p1 = data.get("p1", {})
        p2 = data.get("p2", {})

        img, draw = self._create_canvas(W, H)

        # ── Header — title left, format centred ──────────────────────────────
        self._draw_header(draw, f"PROJECT 1984 — DUEL · {mode_label}", "", W)
        fmt = f"Bo{total_rounds} · TO {win_target}" if total_rounds else ""
        if fmt:
            _, fh = self._text_size(draw, fmt, self.font_stat_label)
            self._text_center(draw, cx, (header_h - fh) // 2, fmt, self.font_stat_label, TEXT_SECONDARY)

        # ── VS band — the two player covers, blended smoothly in the centre ──
        vs_top = header_h
        blended = self._blend_covers(p1_cover, p2_cover, W, vs_h)
        if blended is not None:
            rgba = blended.convert("RGBA")
            overlay = Image.new("RGBA", (W, vs_h), (0, 0, 0, 172))
            rgba = Image.alpha_composite(rgba, overlay)
            img.paste(rgba.convert("RGB"), (0, vs_top))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, vs_top), (W, vs_top + vs_h)], fill=HEADER_BG)

        av_sz = 88
        av_y = vs_top + 20
        av_cy = av_y + av_sz // 2

        p1_av_x = PADDING_X
        self._paste_avatar(img, p1_avatar, p1_av_x, av_y, av_sz, P1_COLOR)
        p2_av_x = W - PADDING_X - av_sz
        self._paste_avatar(img, p2_avatar, p2_av_x, av_y, av_sz, P2_COLOR)
        draw = ImageDraw.Draw(img)

        # Player text regions must stop short of the centre score zone.
        score_half = 66
        p1_text_x = p1_av_x + av_sz + 16
        p2_text_r = p2_av_x - 16
        p1_max_w = (cx - score_half - 12) - p1_text_x
        p2_max_w = p2_text_r - (cx + score_half + 12)
        self._draw_player_side(draw, img, p1, p1_text_x, av_y, P1_COLOR, mode, align="left", max_w=p1_max_w)
        self._draw_player_side(draw, img, p2, p2_text_r, av_y, P2_COLOR, mode, align="right", max_w=p2_max_w)

        # ── Centre score ─────────────────────────────────────────────────────
        s1, sep, s2 = str(p1won), " : ", str(p2won)
        w1, _ = self._text_size(draw, s1, self.font_vs)
        ws, _ = self._text_size(draw, sep, self.font_vs)
        w2, _ = self._text_size(draw, s2, self.font_vs)
        sx = cx - (w1 + ws + w2) // 2
        score_y = av_cy - 26
        self._draw_text_shadow(draw, (sx, score_y), s1, self.font_vs, P1_COLOR)
        self._draw_text_shadow(draw, (sx + w1, score_y), sep, self.font_vs, TEXT_SECONDARY)
        self._draw_text_shadow(draw, (sx + w1 + ws, score_y), s2, self.font_vs, P2_COLOR)

        state_label = {
            "pending": "AWAITING ACCEPT",
            "accepted": "BUILDING POOL",
            "round_active": f"ROUND {current_round + 1}",
        }.get(status, status.upper())
        state_color = ACCENT_GREEN if status == "round_active" else AMBER
        self._text_center(draw, cx, score_y + 56, state_label, self.font_label, state_color, shadow=True)

        # ── Pip strip — one cell per map in the pool ─────────────────────────
        pips_top = vs_top + vs_h
        self._text_center(draw, cx, pips_top + 6, "MATCH FLOW", self.font_stat_label, TEXT_SECONDARY)

        n = max(total_rounds, len(rounds), 1)
        pip = 24
        gap = 10
        strip_w = n * pip + (n - 1) * gap
        start_x = cx - strip_w // 2
        pip_y = pips_top + 26
        for i in range(n):
            px = start_x + i * (pip + gap)
            state = self._pip_state(i, rounds, status, current_round)
            self._draw_pip(draw, img, px, pip_y, pip, i + 1, state)
            draw = ImageDraw.Draw(img)

        # ── Current-map panel — the map cover behind it ──────────────────────
        map_top = pips_top + pips_h
        mx0, mx1 = PADDING_X, W - PADDING_X
        panel_w = mx1 - mx0
        panel = Image.new("RGB", (panel_w, map_h), PANEL_BG)
        if map_cover and cur_map:
            mc = cover_center_crop(map_cover, panel_w, map_h).convert("RGBA")
            ov = Image.new("RGBA", (panel_w, map_h), (0, 0, 0, 165))
            mc = Image.alpha_composite(mc, ov)
            panel = mc.convert("RGB")
        img.paste(panel, (mx0, map_top), self._rounded_mask((panel_w, map_h), radius=14))
        self._aa_rounded_outline(
            img, (mx0, map_top, mx1, map_top + map_h),
            radius=14, outline=(70, 70, 92), width=2,
        )
        draw = ImageDraw.Draw(img)

        if cur_map:
            tx = mx0 + 18
            self._draw_text_shadow(
                draw, (tx, map_top + 16),
                "NOW PLAYING",
                self.font_stat_label, ACCENT_GREEN,
            )
            # Artist + song, large (SR dropped per request).
            title = self._fit(draw, str(cur_map.get("title", "???")), self.font_title, panel_w - 36)
            self._draw_text_shadow(draw, (tx, map_top + 40), title, self.font_title, TEXT_PRIMARY)
        else:
            msg = {
                "pending": "Waiting for opponent to accept…",
                "accepted": "Building map pool…",
            }.get(status, "Preparing next map…")
            self._text_center(draw, cx, map_top + map_h // 2 - 11, msg, self.font_label, TEXT_SECONDARY)

        # ── Bottom accent — split p1 / p2 ────────────────────────────────────
        draw.rectangle([(0, H - 3), (cx, H)], fill=P1_COLOR)
        draw.rectangle([(cx, H - 3), (W, H)], fill=P2_COLOR)

        return self._save(img)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _blend_covers(self, p1_cover, p2_cover, w: int, h: int) -> Optional[Image.Image]:
        """Compose the two player covers side by side, blending smoothly through
        the centre.  Falls back to whichever single cover exists, or None."""
        c1 = cover_center_crop(p1_cover, w, h).convert("RGB") if p1_cover else None
        c2 = cover_center_crop(p2_cover, w, h).convert("RGB") if p2_cover else None
        if c1 and not c2:
            return c1
        if c2 and not c1:
            return c2
        if not c1 and not c2:
            return None
        # Horizontal mask: 0 (keep c1) on the left → 255 (use c2) on the right,
        # with a soft linear transition across the centre.
        mask = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(mask)
        blend = 280
        x0 = w // 2 - blend // 2
        x1 = w // 2 + blend // 2
        for x in range(w):
            if x <= x0:
                v = 0
            elif x >= x1:
                v = 255
            else:
                v = int((x - x0) / (x1 - x0) * 255)
            md.line([(x, 0), (x, h)], fill=v)
        return Image.composite(c2, c1, mask)

    def _paste_avatar(self, img, avatar, x, y, size, color) -> None:
        if avatar:
            av = rounded_rect_crop(avatar, size, radius=16)
            img.paste(av, (x, y), av)
            self._aa_rounded_outline(img, (x, y, x + size, y + size), radius=16, outline=color, width=3)
        else:
            self._aa_rounded_outline(
                img, (x, y, x + size, y + size),
                radius=16, outline=color, width=3, fill=(50, 50, 70),
            )

    def _name_font(self, draw, text: str, max_w: int):
        """Pick the largest font that fits `text` in `max_w` — scaling the name
        down instead of truncating it.  Prefers the multifont-capable slots so
        cyrillic / CJK names keep their fallback; only the last-resort custom
        Torus sizes drop it (rare — names are short)."""
        for f in (self.font_title, self.font_row, self.font_label):
            if self._text_size(draw, text, f)[0] <= max_w:
                return f
        font = self.font_label
        for size in range(17, 11, -1):
            try:
                font = ImageFont.truetype(TORUS_BOLD, size)
            except Exception:
                break
            if draw.textlength(text, font=font) <= max_w:
                break
        return font

    def _draw_player_side(self, draw, img, player, anchor_x, av_y, color, mode, *, align, max_w) -> None:
        """Draw flag + name + division/rating for one player.

        ``anchor_x`` is the left edge for ``align="left"`` (P1) or the right edge
        for ``align="right"`` (P2).  P1 reads flag-then-name; P2 reads name-then-
        flag (flag on the right, nearest its avatar).  The name is *scaled* to
        fit ``max_w`` rather than truncated.
        """
        name = str(player.get("username", "???"))
        country = str(player.get("country", "") or "")
        flag = load_flag(country, height=18)
        division = str(player.get("division", "") or "")
        mu = float(player.get("mu", 0.0) or 0.0)

        flag_w = (flag.width + 8) if flag else 0
        name_font = self._name_font(draw, name, max(40, max_w - flag_w))
        name_y = av_y + 6
        name_w, name_h = self._text_size(draw, name, name_font)
        flag_y = name_y + (name_h - (flag.height if flag else 0)) // 2 + 5  # 3px lower than centred

        if align == "left":
            # [flag][name]
            if flag:
                img.paste(flag, (anchor_x, flag_y), flag)
                draw = ImageDraw.Draw(img)
            self._draw_text_shadow(draw, (anchor_x + flag_w, name_y), name, name_font, TEXT_PRIMARY)
        else:
            # [name][flag] right-aligned — flag sits to the right of the nick.
            flag_x = anchor_x - (flag.width if flag else 0)
            name_x = anchor_x - flag_w - name_w
            self._draw_text_shadow(draw, (name_x, name_y), name, name_font, TEXT_PRIMARY)
            if flag:
                img.paste(flag, (flag_x, flag_y), flag)
                draw = ImageDraw.Draw(img)

        # Division (ranked) or mode tag, then the rating value. While a ranked
        # player is still in placement the division is uncertainty-deflated, so
        # show a CALIBRATING badge instead of a misleading rank.
        calibrating = bool(player.get("calibrating"))
        placement_left = int(player.get("placement_left", 0) or 0)
        if mode == "ranked" and calibrating:
            sub1 = f"CALIBRATING · {placement_left}"
            sub1_color = AMBER
        elif mode == "ranked" and division:
            sub1 = division
            sub1_color = GOLD
        else:
            sub1 = mode.upper()
            sub1_color = TEXT_SECONDARY
        sub2 = f"RATING {mu:.0f}" if mu else ""

        div_y = name_y + name_h + 8
        rate_y = div_y + 22
        if align == "left":
            self._draw_text_shadow(draw, (anchor_x, div_y), sub1, self.font_label, sub1_color)
            if sub2:
                self._draw_text_shadow(draw, (anchor_x, rate_y), sub2, self.font_stat_label, TEXT_SECONDARY)
        else:
            self._text_right(draw, anchor_x, div_y, sub1, self.font_label, sub1_color, shadow=True)
            if sub2:
                self._text_right(draw, anchor_x, rate_y, sub2, self.font_stat_label, TEXT_SECONDARY, shadow=True)

    @staticmethod
    def _pip_state(i: int, rounds: list, status: str, current_round: int) -> str:
        """Classify pool slot ``i`` → p1 | p2 | void | current | pending."""
        if i < len(rounds):
            r = rounds[i]
            st = r.get("status")
            w = r.get("winner")
            if st == "playing":
                return "current"
            if st in ("void", "forfeit"):
                return "void"
            if st == "completed":
                return "p1" if w == 1 else ("p2" if w == 2 else "void")
        if status == "round_active" and i == current_round:
            return "current"
        return "pending"

    def _draw_pip(self, draw, img, x, y, size, number, state) -> None:
        fill = {
            "p1": P1_COLOR, "p2": P2_COLOR, "void": VOID_COLOR,
            "current": (28, 28, 42), "pending": PENDING_COLOR,
        }[state]
        self._aa_rounded_fill(img, (x, y, x + size, y + size), radius=7, fill=fill)
        if state == "current":
            self._aa_rounded_outline(
                img, (x, y, x + size, y + size), radius=7, outline=AMBER, width=2,
            )
            draw = ImageDraw.Draw(img)
        num_color = (255, 255, 255) if state in ("p1", "p2") else (
            AMBER if state == "current" else TEXT_SECONDARY
        )
        self._text_center(draw, x + size // 2, y + size // 2 - 8, str(number),
                          self.font_stat_label, num_color)

    def _fit(self, draw, text, font, max_w) -> str:
        if self._text_size(draw, text, font)[0] <= max_w:
            return text
        t = text
        while t and self._text_size(draw, t + "...", font)[0] > max_w:
            t = t[:-1]
        return (t + "...") if t else text

    async def generate_duel_status_card_async(self, data: Dict) -> BytesIO:
        p1 = data.get("p1", {})
        p2 = data.get("p2", {})
        cur_map = data.get("current_map") or {}

        async def _dl(url):
            if not url:
                return None
            r = await download_image(url)
            return r if (r and not isinstance(r, Exception)) else None

        async def _cover(player):
            cd = player.get("cover_data")
            if cd:
                try:
                    return Image.open(BytesIO(cd)).convert("RGBA")
                except Exception:
                    pass
            return await _dl(player.get("cover_url"))

        map_url = None
        bsid = cur_map.get("beatmapset_id")
        if bsid:
            map_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg"

        p1_av, p2_av, p1_cov, p2_cov, map_cov = await asyncio.gather(
            _dl(p1.get("avatar_url")),
            _dl(p2.get("avatar_url")),
            _cover(p1),
            _cover(p2),
            _dl(map_url),
        )
        return await asyncio.to_thread(
            self.generate_duel_status_card, data, p1_av, p2_av, p1_cov, p2_cov, map_cov,
        )
