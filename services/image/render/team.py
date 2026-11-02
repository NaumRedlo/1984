import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from services.image import colors, tint
from services.image.constants import SANS_BOLD, SANS_REG, SANS_SEMI
from services.image.utils import _find_font, cover_center_crop, download_image, load_flag, load_icon

W = 1280
M = 20
BANNER = 330

_STRINGS = {
    "en": {
        "team_rank": "Team Ranking", "performance": "Performance", "ranked_score": "Ranked Score",
        "play_count": "Play Count", "members": "Members", "roster": "Roster",
        "open": "Open to applications", "closed": "Closed", "slots": "{n} free", "more": "+{n} more",
    },
    "ru": {
        "team_rank": "Место команды", "performance": "PP", "ranked_score": "Ranked Score",
        "play_count": "Игр", "members": "Участники", "roster": "Состав",
        "open": "Набор открыт", "closed": "Набор закрыт", "slots": "свободно {n}", "more": "ещё {n}",
    },
}


def _sp(value) -> str:
    return f"{int(value or 0):,}".replace(",", " ")


async def _none():
    return None


class TeamCardMixin:

    def _tm_fonts(self):
        cached = getattr(self, "_tm_font_cache", None)
        if cached:
            return cached
        bold, semi, reg = _find_font(SANS_BOLD), _find_font(SANS_SEMI), _find_font(SANS_REG)

        def f(path, size):
            return ImageFont.truetype(path or bold, size)

        cached = {
            "name": f(bold, 54), "tag": f(semi, 26), "rank": f(bold, 56), "rank_lbl": f(semi, 20),
            "stat_lbl": f(semi, 16), "stat": f(bold, 30), "section": f(bold, 18), "member": f(bold, 21),
            "member_sub": f(semi, 16), "chip": f(semi, 17),
        }
        self._tm_font_cache = cached
        return cached

    async def generate_team_card_async(self, data: Dict) -> BytesIO:
        team = data.get("team") or {}
        members = data.get("members") or []
        tasks = [
            download_image(team["flag_url"]) if team.get("flag_url") else _none(),
            download_image(team["cover_url"]) if team.get("cover_url") else _none(),
        ] + [download_image(m["avatar_url"]) if m.get("avatar_url") else _none() for m in members]
        got = await asyncio.gather(*tasks, return_exceptions=True)
        got = [None if isinstance(r, Exception) else r for r in got]
        pictures = {"flag": got[0], "cover": got[1], "avatars": got[2:]}
        return await asyncio.to_thread(self.generate_team_card, data, pictures)

    def generate_team_card(self, data: Dict, pictures: Optional[Dict] = None) -> BytesIO:
        img = self._tm_card(data, pictures or {})
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        out.seek(0)
        return out

    def _tm_strings(self, data):
        return _STRINGS["ru" if (data.get("lang") or "en").lower() == "ru" else "en"]

    def _tm_panel(self, img, box):
        self._aa_rounded_fill(img, box, radius=16, fill=colors.PANEL)
        self._aa_rounded_outline(img, box, radius=16, outline=colors.PANEL_BORDER, width=1)

    def _tm_stats(self, img, data, box, S, F):
        team = data.get("team") or {}
        stats = team.get("statistics") or {}
        x0, y0, x1, y1 = box
        self._tm_panel(img, box)
        ink = tint.Plain(colors.TEXT_PRIMARY)
        cells = [
            (S["performance"], f"{_sp(stats.get('performance'))}pp"),
            (S["ranked_score"], _sp(stats.get("ranked_score"))),
            (S["play_count"], _sp(stats.get("play_count"))),
            (S["members"], _sp(team.get("member_count"))),
        ]
        weights = (1.0, 1.5, 1.1, 0.7)
        unit = (x1 - x0 - 56) / sum(weights)
        draw = ImageDraw.Draw(img)
        x = x0 + 28
        for (label, value), weight in zip(cells, weights):
            self._draw_text(draw, (int(x), y0 + 18), label, F["stat_lbl"], colors.TEXT_MUTED)
            self._draw_text(draw, (int(x), y0 + 42), value, F["stat"], ink)
            x += unit * weight

    def _tm_rank(self, img, cx, cy, team, S, F):
        rank = (team.get("statistics") or {}).get("rank")
        if not rank:
            return
        draw = ImageDraw.Draw(img)
        value = f"#{_sp(rank)}"
        w = max(self._text_size(draw, value, F["rank"])[0], self._text_size(draw, S["team_rank"], F["rank_lbl"])[0])
        shade = Image.new("RGBA", (w + 160, 220), (0, 0, 0, 0))
        ImageDraw.Draw(shade).ellipse((20, 30, w + 140, 190), fill=colors.CARD + (190,))
        shade = shade.filter(ImageFilter.GaussianBlur(30))
        img.paste(shade, (int(cx - w / 2 - 80), int(cy - 110)), shade)
        draw = ImageDraw.Draw(img)
        self._text_mid(draw, cx, cy - 30, S["team_rank"], F["rank_lbl"], colors.TEXT_PRIMARY, align="center")
        self._text_mid(draw, cx, cy + 14, value, F["rank"], tint.tone(colors.ACCENT_PP), align="center")

    def _tm_recruiting(self, img, x, cy, team, S, F):
        if team.get("is_open") is None:
            return x
        is_open = bool(team.get("is_open"))
        label = S["open"] if is_open else S["closed"]
        draw = ImageDraw.Draw(img)
        w = self._text_size(draw, label, F["chip"])[0] + 26
        fill = (38, 62, 44) if is_open else colors.PANEL
        ink = colors.POSITIVE if is_open else colors.TEXT_MUTED
        self._aa_rounded_fill(img, (int(x), int(cy - 15), int(x + w), int(cy + 15)), radius=15, fill=fill)
        self._text_mid(ImageDraw.Draw(img), x + w / 2, cy, label, F["chip"], ink, align="center")
        x += w
        slots = team.get("empty_slots")
        if is_open and slots:
            x = self._text_mid(ImageDraw.Draw(img), x + 12, cy, S["slots"].format(n=slots), F["chip"], colors.TEXT_MUTED)
        return x

    def _tm_face(self, img, picture, x, y, d, ring, width=2):
        if picture is not None:
            face = cover_center_crop(picture.convert("RGB"), d, d)
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
            img.paste(face, (int(x), int(y)), mask)
        else:
            self._aa_ellipse_fill(img, (int(x), int(y), int(x + d), int(y + d)), fill=colors.PANEL)
        if ring is not None:
            self._aa_ellipse_outline(img, (int(x), int(y), int(x + d), int(y + d)), outline=ring, width=width)

    def _tm_crowned(self, img, draw, x, cy, name, font, ink):
        crown = load_icon("crown", 20, colour=(255, 204, 77))
        if crown:
            img.paste(crown, (int(x), int(cy - crown.height / 2 - 1)), crown)
            x += crown.width + 7
            draw = ImageDraw.Draw(img)
        return self._text_mid(draw, x, cy, name, font, ink)

    def _tm_ordered(self, data, pictures):
        avatars = pictures.get("avatars") or []
        members = [dict(m, picture=avatars[i] if i < len(avatars) else None) for i, m in enumerate(data.get("members") or [])]
        members.sort(key=lambda m: (not m.get("is_leader"), not m.get("in_chat"), -(m.get("pp") or 0)))
        return members

    def _tm_member_row(self, img, member, x, y, F, here_ring):
        ring = here_ring if member.get("in_chat") else colors.PANEL_BORDER
        self._tm_face(img, member.get("picture"), x, y, 46, ring)
        draw = ImageDraw.Draw(img)
        cy = y + 23
        name = member.get("username") or "—"
        if member.get("is_leader"):
            self._tm_crowned(img, draw, x + 60, cy - 10, name, F["member"], colors.TEXT_PRIMARY)
        else:
            self._text_mid(draw, x + 60, cy - 10, name, F["member"], colors.TEXT_PRIMARY)
        sx = x + 60
        flag = load_flag(member.get("country") or "", height=14)
        if flag:
            img.paste(flag, (int(sx), int(cy + 13 - flag.height / 2)), flag)
            sx += flag.width + 8
        if member.get("pp"):
            self._text_mid(ImageDraw.Draw(img), sx, cy + 13, f"{_sp(member['pp'])}pp", F["member_sub"], colors.TEXT_MUTED)

    @staticmethod
    def _tm_roster_height(data):
        return 58 + max(1, -(-min(len(data.get("members") or []), 12) // 3)) * 62 + 4

    def _tm_roster(self, img, data, pictures, box, S, F):
        x0, y0, x1, y1 = box
        self._tm_panel(img, box)
        draw = ImageDraw.Draw(img)
        team = data.get("team") or {}
        members = self._tm_ordered(data, pictures)
        total = int(team.get("member_count") or len(members))
        self._draw_text(draw, (x0 + 24, y0 + 18), S["roster"].upper(), F["section"], colors.TEXT_PRIMARY)
        shown = members[:12]
        col_w = (x1 - x0 - 48 - 48) / 3
        here_ring = tint.tone((228, 76, 76))
        for i, member in enumerate(shown):
            self._tm_member_row(img, member, x0 + 24 + (i % 3) * (col_w + 24), y0 + 58 + (i // 3) * 62, F, here_ring)
        if total > len(shown):
            self._text_mid(ImageDraw.Draw(img), x1 - 24, y0 + 28, S["more"].format(n=_sp(total - len(shown))),
                           F["member_sub"], colors.TEXT_MUTED, align="right")

    def _tm_card(self, data, pictures):
        S, F = self._tm_strings(data), self._tm_fonts()
        team = data.get("team") or {}
        H = M + BANNER + 24 + 96 + 20 + self._tm_roster_height(data) + 24 + M
        img = Image.new("RGB", (W, H), colors.BG)
        card = (M, M, W - M, H - M)
        self._aa_rounded_fill(img, card, radius=24, fill=colors.CARD)

        bw, bh = W - 2 * M, BANNER
        cover = pictures.get("cover")
        if cover is not None:
            art = cover_center_crop(cover.convert("RGB"), bw, bh).convert("RGBA")
            shade = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            sd = ImageDraw.Draw(shade)
            for y in range(bh):
                sd.line((0, y, bw, y), fill=colors.CARD + (int(60 + 195 * (y / bh) ** 1.6),))
            art = Image.alpha_composite(art, shade)
            mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh + 40), radius=24, fill=255)
            img.paste(art.convert("RGB"), (M, M), mask)

        flag_h = 96
        fx, fy = M + 36, M + bh - 176
        glow = Image.new("RGBA", (flag_h * 2 + 80, flag_h + 80), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((40, 40, 40 + flag_h * 2, 40 + flag_h), radius=16, fill=(0, 0, 0, 170))
        glow = glow.filter(ImageFilter.GaussianBlur(14))
        img.paste(glow, (fx - 40, fy - 40 + 6), glow)
        right = self._team_flag(img, fx, fy + flag_h / 2, pictures.get("flag"), flag_h)
        draw = ImageDraw.Draw(img)
        tx = right + 30
        name_right = self._text_mid(draw, tx, fy + 28, team.get("name") or "—", F["name"], colors.TEXT_PRIMARY)
        tag = team.get("short_name")
        if tag:
            self._text_mid(draw, name_right + 16, fy + 32, tag, F["tag"], colors.TEXT_MUTED)
        self._tm_recruiting(img, tx, fy + 82, team, S, F)
        self._tm_rank(img, W - M - 230, M + bh / 2, team, S, F)

        sy = M + bh + 24
        self._tm_stats(img, data, (M + 24, sy, W - M - 24, sy + 96), S, F)
        ry = sy + 96 + 20
        self._tm_roster(img, data, pictures, (M + 24, ry, W - M - 24, H - M - 24), S, F)
        self._aa_rounded_outline(img, card, radius=24, outline=colors.CARD_BORDER, width=1)
        return img
