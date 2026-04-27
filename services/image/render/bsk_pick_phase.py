"""
BSK Pick Phase card renderers — new pick phase design.

Two cards:
  generate_bsk_player_pick_card  — DM to each player (ban + pick phase)
  generate_bsk_group_pick_card   — group chat (face-down tiles)
"""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PADDING_X, CARD_WIDTH,
)
from services.image.utils import (
    load_icon, load_flag, download_image, cover_center_crop, _none_coro,
)
from services.image.render.bsk_duel import (
    P1_COLOR, P2_COLOR, GOLD,
    SKILL_COLORS, MTYPE_BG, MTYPE_FULL,
    _sr_color, _paste_icon, _draw_name_with_flag,
)

# ─── Layout constants ──────────────────────────────────────────────────────────
_GPAD      = 12      # grid left/right padding (tighter than PADDING_X for wider cells)
_GCOLS     = 3
_GCELL_GAP = 8
_CELL_W    = (CARD_WIDTH - 2 * _GPAD - (_GCOLS - 1) * _GCELL_GAP) // _GCOLS   # ≈ 253
_CELL_H    = 168
_GRID_H    = 2 * _CELL_H + _GCELL_GAP + _GCELL_GAP * 2    # 2 rows + gaps + pad


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _make_rounded_cell(w: int, h: int, bg: tuple, stripe: tuple, radius: int = 10) -> Image.Image:
    """Return a w×h RGBA card-back image with diagonal stripe pattern + rounded mask."""
    surf = Image.new("RGB", (w, h), bg)
    drw  = ImageDraw.Draw(surf)
    step = 18
    for off in range(-h, w + h, step):
        drw.line([(off, 0), (off + h, h)], fill=stripe, width=1)

    # Rounded mask to clip stripes to rounded corners
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    result = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    result.paste(surf.convert("RGBA"), mask=mask)
    return result


def _draw_score_bar(draw: ImageDraw.Draw, x: int, y: int, w: int, h: int,
                    score_a: int, score_b: int, target: int,
                    col_a=P1_COLOR, col_b=P2_COLOR):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=3, fill=(40, 40, 62))
    if target > 0:
        fa = int(w * min(score_a / target, 1.0))
        fb = int(w * min(score_b / target, 1.0))
        if fa + fb > w:
            fa = int(w * fa / (fa + fb))
            fb = w - fa
        if fa > 0:
            draw.rounded_rectangle((x, y, x + fa, y + h), radius=3, fill=col_a)
        if fb > 0:
            draw.rounded_rectangle((x + w - fb, y, x + w, y + h), radius=3, fill=col_b)


def _draw_number_circle(draw: ImageDraw.Draw, cx: int, cy: int,
                         num: int, font,
                         bg=(50, 50, 72), outline=(90, 90, 120)):
    r = 11
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=bg, outline=outline, width=1)
    nb = draw.textbbox((0, 0), str(num), font=font)
    draw.text(
        (cx - (nb[2] - nb[0]) // 2 - nb[0], cy - (nb[3] - nb[1]) // 2 - nb[1]),
        str(num), font=font, fill=(220, 220, 240),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  DM PLAYER CARD  (ban phase  OR  pick phase)
# ══════════════════════════════════════════════════════════════════════════════

class BskPickPhaseMixin:

    def generate_bsk_player_pick_card(self, data: Dict) -> BytesIO:
        """
        Card sent to each player privately.
        Works for both 'ban' and 'pick' phase.

        data keys:
          round_number, phase ('ban'|'pick')
          player_name, player_country, player_cover (PIL Image|None)
          opponent_name, opponent_country
          score_player, score_opponent, target_score
          candidates    list[dict]: beatmap_id, beatmapset_id, title, artist,
                                    version, star_rating, bpm, length_seconds,
                                    map_type, is_new
          covers        list[PIL Image|None]  same order as candidates
          banned_ids    list[int]
          picked_id     int|None
          priority      bool  — True = this player picks first
        """
        W = CARD_WIDTH
        header_h  = 36
        status_h  = 52
        phase_h   = 44
        footer_h  = 40
        H = header_h + status_h + phase_h + _GRID_H + footer_h

        img, draw = self._create_canvas(W, H)

        phase        = data.get('phase', 'ban')
        round_num    = data.get('round_number', 1)
        player_name  = data.get('player_name', 'P1')
        player_cc    = data.get('player_country', '')
        opp_name     = data.get('opponent_name', 'P2')
        opp_cc       = data.get('opponent_country', '')
        p_cover      = data.get('player_cover')
        candidates   = data.get('candidates', [])
        covers       = data.get('covers', [])
        banned_ids   = set(data.get('banned_ids', []))
        picked_id    = data.get('picked_id')
        priority     = data.get('priority', False)
        score_p      = int(data.get('score_player', 0))
        score_o      = int(data.get('score_opponent', 0))
        target       = int(data.get('target_score', 1_000_000))

        phase_lbl   = 'Фаза бана'    if phase == 'ban' else 'Фаза выбора'
        phase_col   = (210, 80, 80)  if phase == 'ban' else (ACCENT_GREEN if priority else (100, 160, 220))

        # ── Header ────────────────────────────────────────────────────────────
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_lbl}', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_s = header_h
        half_w = W // 2

        # Player cover left half
        if p_cover:
            try:
                cr = cover_center_crop(p_cover.convert("RGBA"), half_w, status_h)
                ov = Image.new("RGBA", (half_w, status_h), (0, 0, 0, 160))
                bl = Image.alpha_composite(cr, ov)
                ti = Image.new("RGBA", (half_w, status_h), (*P1_COLOR, 55))
                img.paste(Image.alpha_composite(bl, ti).convert("RGB"), (0, y_s))
            except Exception:
                draw.rectangle([(0, y_s), (half_w, y_s + status_h)], fill=HEADER_BG)
        else:
            draw.rectangle([(0, y_s), (half_w, y_s + status_h)], fill=HEADER_BG)
        draw.rectangle([(half_w, y_s), (W, y_s + status_h)], fill=HEADER_BG)
        draw = ImageDraw.Draw(img)

        name_y = y_s + 6
        draw = _draw_name_with_flag(img, draw, PADDING_X, name_y,
                                    player_name, player_cc, self.font_label,
                                    (235, 235, 245), align='left', flag_h=16)
        draw = _draw_name_with_flag(img, draw, W - PADDING_X, name_y,
                                    opp_name, opp_cc, self.font_label,
                                    TEXT_SECONDARY, align='right', flag_h=16)
        self._text_center(draw, W // 2, name_y + 2, 'vs', self.font_stat_label, TEXT_SECONDARY)

        # Score bar
        bar_y = y_s + 30
        bar_w = W - 2 * PADDING_X
        _draw_score_bar(draw, PADDING_X, bar_y, bar_w, 6,
                        score_p, score_o, target, P1_COLOR, P2_COLOR)
        draw.text((PADDING_X, bar_y + 9),
                  f'{score_p:,}', font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_y + 9,
                         f'{score_o:,}', self.font_stat_label, P2_COLOR)

        # ── Phase banner ──────────────────────────────────────────────────────
        y_ph = y_s + status_h
        draw.rectangle([(0, y_ph), (W, y_ph + phase_h)], fill=(24, 24, 40))
        draw.line([(0, y_ph), (W, y_ph)], fill=(44, 44, 68), width=1)
        draw.line([(0, y_ph + phase_h - 1), (W, y_ph + phase_h - 1)],
                  fill=(44, 44, 68), width=1)

        if phase == 'ban':
            main_text = '☠  ФАЗА БАНА — выбери до 3 карт для удаления'
            hint_text = 'Выбранные слоты заменятся случайными картами'
        else:
            prio_txt = 'твой выбор в приоритете' if priority else f'приоритет у {opp_name}'
            main_text = '⚔  ФАЗА ВЫБОРА — выбери карту для раунда'
            hint_text = f'Нажми номер карты  ·  {prio_txt}'

        self._text_center(draw, W // 2, y_ph + 7, main_text, self.font_label, phase_col)
        self._text_center(draw, W // 2, y_ph + 27, hint_text, self.font_stat_label, TEXT_SECONDARY)

        # ── Map grid ──────────────────────────────────────────────────────────
        y_grid = y_ph + phase_h + _GCELL_GAP
        star_icon = load_icon('star', size=12)

        for idx in range(6):
            col = idx % _GCOLS
            row = idx // _GCOLS
            cx = _GPAD + col * (_CELL_W + _GCELL_GAP)
            cy = y_grid + row * (_CELL_H + _GCELL_GAP)

            m          = candidates[idx] if idx < len(candidates) else None
            cover_img  = covers[idx]     if idx < len(covers)     else None

            mtype    = (m.get('map_type', '') or '') if m else ''
            cell_bg  = MTYPE_BG.get(mtype, (28, 28, 44))
            draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + _CELL_H),
                                   radius=10, fill=cell_bg)

            # Cover background
            if cover_img:
                try:
                    cropped = cover_center_crop(cover_img.convert("RGBA"), _CELL_W, _CELL_H)
                    ov_a = 185 if (m and m.get('beatmap_id') in banned_ids) else 165
                    ov   = Image.new("RGBA", (_CELL_W, _CELL_H), (0, 0, 0, ov_a))
                    bl   = Image.alpha_composite(cropped, ov)
                    tc   = MTYPE_BG.get(mtype, (0, 0, 0))
                    bl   = Image.alpha_composite(bl, Image.new("RGBA", (_CELL_W, _CELL_H), (*tc, 65)))
                    img.paste(bl.convert("RGB"), (cx, cy))
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass

            if not m:
                draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + _CELL_H),
                                       radius=10, outline=(50, 50, 72), width=1)
                continue

            bid       = m.get('beatmap_id')
            is_banned = bid in banned_ids
            is_picked = (picked_id == bid) and phase == 'pick'
            is_new    = m.get('is_new', False)
            stars     = float(m.get('star_rating', 0))
            sr_col    = _sr_color(stars)
            title     = m.get('title', 'Unknown') or 'Unknown'
            artist    = m.get('artist', '') or ''
            version   = m.get('version', '') or ''
            bpm       = m.get('bpm')
            length    = m.get('length_seconds')
            type_col  = SKILL_COLORS.get(mtype)

            # ── Border ────────────────────────────────────────────────────────
            if is_picked:
                b_col, b_w = ACCENT_GREEN, 3
            elif is_banned:
                b_col, b_w = (180, 50, 50), 2
            elif is_new:
                b_col, b_w = (80, 190, 100), 2
            else:
                b_col, b_w = (52, 52, 74), 1
            draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + _CELL_H),
                                   radius=10, outline=b_col, width=b_w)

            # ── Type accent strip (top) ────────────────────────────────────────
            if type_col:
                draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + 4),
                                       radius=2, fill=type_col)

            if not is_banned:
                # Title
                dt = title if len(title) <= 20 else title[:19] + '…'
                draw.text((cx + 8, cy + 10), dt, font=self.font_label, fill=TEXT_PRIMARY)

                # Artist
                da = artist if len(artist) <= 24 else artist[:23] + '…'
                draw.text((cx + 8, cy + 32), da, font=self.font_small, fill=TEXT_SECONDARY)

                # Version
                dv = f'[{version}]' if version else ''
                if len(dv) > 26: dv = dv[:25] + '…'
                draw.text((cx + 8, cy + 52), dv,
                          font=self.font_stat_label, fill=(110, 135, 185))

                # Map-type badge
                if type_col:
                    lbl   = MTYPE_FULL.get(mtype, mtype.upper())
                    lbb   = draw.textbbox((0, 0), lbl, font=self.font_stat_label)
                    lbl_w = lbb[2] - lbb[0]
                    bx, by = cx + 7, cy + 72
                    draw.rounded_rectangle((bx, by, bx + lbl_w + 10, by + 17),
                                           radius=4, fill=type_col)
                    draw.text((bx + 5, by + 1), lbl,
                              font=self.font_stat_label, fill=(18, 18, 28))

                # BPM · Length
                meta = []
                if bpm:    meta.append(f'{bpm:.0f} BPM')
                if length:
                    mm, ss = divmod(length, 60)
                    meta.append(f'{mm}:{ss:02d}')
                if meta:
                    draw.text((cx + 8, cy + 96), '  ·  '.join(meta),
                              font=self.font_stat_label, fill=TEXT_SECONDARY)

                # SR badge (top-right)
                sr_str = f'{stars:.2f}'
                sr_bb  = draw.textbbox((0, 0), sr_str, font=self.font_stat_label)
                sr_tw  = sr_bb[2] - sr_bb[0]
                iz     = star_icon.width if star_icon else 0
                px, py = 5, 3
                bw_sr  = px + iz + 3 + sr_tw + px
                bh_sr  = (sr_bb[3] - sr_bb[1]) + py * 2 + 2
                sbx    = cx + _CELL_W - 7 - bw_sr
                sby    = cy + 7
                draw.rounded_rectangle((sbx, sby, sbx + bw_sr, sby + bh_sr),
                                       radius=4, fill=sr_col)
                if star_icon:
                    draw = _paste_icon(img, star_icon, sbx + px, sby + (bh_sr - iz) // 2)
                draw.text((sbx + px + iz + 3, sby + py - sr_bb[1]),
                          sr_str, font=self.font_stat_label, fill=(255, 255, 255))

                # NEW badge (replacement card)
                if is_new:
                    nlbl = 'НОВАЯ'
                    nbb  = draw.textbbox((0, 0), nlbl, font=self.font_stat_label)
                    nw   = nbb[2] - nbb[0]
                    nx   = cx + _CELL_W - 7 - nw - 10
                    ny   = sby + bh_sr + 4
                    draw.rounded_rectangle((nx, ny, nx + nw + 10, ny + 15),
                                           radius=4, fill=(55, 155, 75))
                    draw.text((nx + 5, ny + 1), nlbl,
                              font=self.font_stat_label, fill=(255, 255, 255))

                # PICKED stripe (bottom)
                if is_picked:
                    stripe_y = cy + _CELL_H - 26
                    draw.rounded_rectangle(
                        (cx + 6, stripe_y, cx + _CELL_W - 6, cy + _CELL_H - 6),
                        radius=4, fill=ACCENT_GREEN,
                    )
                    self._text_center(draw, cx + _CELL_W // 2, stripe_y + 5,
                                      'ВЫБРАНО', self.font_stat_label, (18, 18, 28))

            else:
                # ── Banned overlay ─────────────────────────────────────────────
                ban_ov = Image.new("RGBA", (_CELL_W, _CELL_H), (170, 25, 25, 145))
                mask   = Image.new("L", (_CELL_W, _CELL_H), 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, _CELL_W - 1, _CELL_H - 1), radius=10, fill=255)
                region = img.crop((cx, cy, cx + _CELL_W, cy + _CELL_H)).convert("RGBA")
                merged = Image.alpha_composite(region, ban_ov)
                img.paste(merged.convert("RGB"), (cx, cy), mask)
                draw = ImageDraw.Draw(img)

                # Re-draw border after overlay
                draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + _CELL_H),
                                       radius=10, outline=(190, 55, 55), width=2)

                # ✕ and БАН label
                x_str = '✕'
                xbb   = draw.textbbox((0, 0), x_str, font=self.font_big)
                xw    = xbb[2] - xbb[0]
                draw.text((cx + (_CELL_W - xw) // 2 - xbb[0],
                            cy + _CELL_H // 2 - 30 - xbb[1]),
                           x_str, font=self.font_big, fill=(255, 255, 255))
                self._text_center(draw, cx + _CELL_W // 2, cy + _CELL_H // 2 + 8,
                                  'БАН', self.font_label, (255, 190, 190))

            # ── Number circle ──────────────────────────────────────────────────
            stripe_offset = 26 if (is_picked or is_banned) else 0
            num_cx = cx + _CELL_W - 8 - 11
            num_cy = cy + _CELL_H - 8 - 11 - stripe_offset
            _draw_number_circle(draw, num_cx, num_cy, idx + 1, self.font_stat_label)

        # ── Footer ────────────────────────────────────────────────────────────
        y_foot = y_grid + 2 * (_CELL_H + _GCELL_GAP)
        draw.rectangle([(0, y_foot), (W, y_foot + footer_h)], fill=(22, 22, 38))
        draw.line([(0, y_foot), (W, y_foot)], fill=(42, 42, 66), width=1)

        ban_count = len(banned_ids)
        if phase == 'ban':
            status_txt = f'Забанено: {ban_count} / 3'
            hint_txt   = 'Нажми номер карты · ✅ Готово чтобы закончить бан'
            status_col = (200, 90, 90) if ban_count else TEXT_SECONDARY
        else:
            if picked_id:
                status_txt = '✅ Выбор сделан'
                hint_txt   = 'Ожидаем соперника…'
                status_col = ACCENT_GREEN
            else:
                prio_who = 'Твой приоритет' if priority else f'Приоритет: {opp_name}'
                status_txt = 'Выбери 1 карту'
                hint_txt   = f'Нажми номер карты  ·  {prio_who}'
                status_col = phase_col

        draw.text((PADDING_X, y_foot + 12),
                  status_txt, font=self.font_label, fill=status_col)
        self._text_right(draw, W - PADDING_X, y_foot + 13,
                         hint_txt, self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_player_pick_card_async(self, data: Dict) -> BytesIO:
        from io import BytesIO as _BytesIO
        candidates = data.get('candidates', [])

        cover_tasks = [
            download_image(f"https://assets.ppy.sh/beatmaps/{m['beatmapset_id']}/covers/list.jpg")
            if m.get('beatmapset_id') else _none_coro()
            for m in candidates
        ]

        async def _load_player_cover(raw, url):
            if raw:
                try:    return Image.open(_BytesIO(raw)).convert("RGBA")
                except: pass
            if url:
                r = await download_image(url)
                return r.convert("RGBA") if r else None
            return None

        map_results, p_cover = await asyncio.gather(
            asyncio.gather(*cover_tasks, return_exceptions=True),
            _load_player_cover(data.get('player_cover_data'), data.get('player_cover_url')),
        )
        covers = [None if isinstance(r, Exception) or r is None else r for r in map_results]
        return await asyncio.to_thread(
            self.generate_bsk_player_pick_card,
            {**data, 'covers': covers, 'player_cover': p_cover},
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  GROUP CHAT CARD  (face-down tiles)
    # ══════════════════════════════════════════════════════════════════════════

    def generate_bsk_group_pick_card(self, data: Dict) -> BytesIO:
        """
        Card shown in the group chat during pick phase.
        All tiles face-down (osu! logo). When a player picks, their tile gains
        a colored border + player name stripe.

        data keys:
          round_number, phase ('ban'|'pick')
          p1_name, p1_country, p1_cover (PIL|None)
          p2_name, p2_country, p2_cover (PIL|None)
          p1_done bool, p2_done bool
          p1_picked_idx int|None (0-5), p2_picked_idx int|None
          p1_bans_count int, p2_bans_count int   (for ban phase sub-status)
          score_p1, score_p2, target_score
        """
        W = CARD_WIDTH
        header_h   = 36
        status_h   = 52
        phase_h    = 28
        score_h    = 52
        H = header_h + status_h + phase_h + _GRID_H + score_h

        img, draw = self._create_canvas(W, H)

        round_num    = data.get('round_number', 1)
        phase        = data.get('phase', 'pick')
        p1_name      = data.get('p1_name', 'P1')
        p2_name      = data.get('p2_name', 'P2')
        p1_cc        = data.get('p1_country', '')
        p2_cc        = data.get('p2_country', '')
        p1_cover     = data.get('p1_cover')
        p2_cover     = data.get('p2_cover')
        p1_done      = data.get('p1_done', False)
        p2_done      = data.get('p2_done', False)
        p1_pick_idx  = data.get('p1_picked_idx')
        p2_pick_idx  = data.get('p2_picked_idx')
        p1_bans      = data.get('p1_bans_count', 0)
        p2_bans      = data.get('p2_bans_count', 0)
        score_p1     = int(data.get('score_p1', 0))
        score_p2     = int(data.get('score_p2', 0))
        target       = int(data.get('target_score', 1_000_000))

        phase_lbl = 'Фаза бана' if phase == 'ban' else 'Фаза выбора'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_lbl}', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_s    = header_h
        half_w = W // 2

        for cover, tint, x0, w in [
            (p1_cover, P1_COLOR, 0, half_w),
            (p2_cover, P2_COLOR, half_w, W - half_w),
        ]:
            if cover:
                try:
                    cr = cover_center_crop(cover.convert("RGBA"), w, status_h)
                    ov = Image.new("RGBA", (w, status_h), (0, 0, 0, 160))
                    bl = Image.alpha_composite(cr, ov)
                    ti = Image.new("RGBA", (w, status_h), (*tint, 55))
                    img.paste(Image.alpha_composite(bl, ti).convert("RGB"), (x0, y_s))
                except Exception:
                    draw.rectangle([(x0, y_s), (x0 + w, y_s + status_h)], fill=HEADER_BG)
            else:
                draw.rectangle([(x0, y_s), (x0 + w, y_s + status_h)], fill=HEADER_BG)
        draw = ImageDraw.Draw(img)

        # Names + readiness
        name_y = y_s + 6
        p1_ncol = ACCENT_GREEN if p1_done else (235, 235, 245)
        p2_ncol = ACCENT_GREEN if p2_done else (235, 235, 245)
        draw = _draw_name_with_flag(img, draw, PADDING_X, name_y,
                                    p1_name, p1_cc, self.font_label, p1_ncol,
                                    align='left', flag_h=16)
        draw = _draw_name_with_flag(img, draw, W - PADDING_X, name_y,
                                    p2_name, p2_cc, self.font_label, p2_ncol,
                                    align='right', flag_h=16)
        draw.line([(W // 2, y_s + 6), (W // 2, y_s + status_h - 6)],
                  fill=(70, 70, 95), width=1)

        # Sub-status
        sub_y = y_s + 30
        if phase == 'ban':
            p1_sub  = f'⏳ банит… ({p1_bans}/3)' if not p1_done else '✅ забанил'
            p2_sub  = f'⏳ банит… ({p2_bans}/3)' if not p2_done else '✅ забанил'
        else:
            p1_sub  = '✅ выбрал' if p1_done else '⏳ выбирает…'
            p2_sub  = '✅ выбрал' if p2_done else '⏳ выбирает…'
        p1_sc = ACCENT_GREEN if p1_done else TEXT_SECONDARY
        p2_sc = ACCENT_GREEN if p2_done else TEXT_SECONDARY
        draw.text((PADDING_X, sub_y), p1_sub, font=self.font_stat_label, fill=p1_sc)
        self._text_right(draw, W - PADDING_X, sub_y, p2_sub, self.font_stat_label, p2_sc)

        # ── Phase indicator ───────────────────────────────────────────────────
        y_ph = y_s + status_h
        draw.rectangle([(0, y_ph), (W, y_ph + phase_h)], fill=(22, 22, 38))
        draw.line([(0, y_ph), (W, y_ph)], fill=(40, 40, 64), width=1)

        if p1_done and p2_done:
            wait_txt = '✅ Оба сделали выбор — определяем карту…'
            wait_col = ACCENT_GREEN
        else:
            wait_txt = f'⏳ Идёт {phase_lbl.lower()}…  отправлено в личные сообщения'
            wait_col = TEXT_SECONDARY
        self._text_center(draw, W // 2, y_ph + 7, wait_txt, self.font_small, wait_col)

        # ── Face-down card grid ───────────────────────────────────────────────
        y_grid   = y_ph + phase_h + _GCELL_GAP
        osu_logo = load_icon('osulogo', size=74)

        for idx in range(6):
            col = idx % _GCOLS
            row = idx // _GCOLS
            cx = _GPAD + col * (_CELL_W + _GCELL_GAP)
            cy = y_grid + row * (_CELL_H + _GCELL_GAP)

            chosen_by = (1 if p1_pick_idx == idx else
                         2 if p2_pick_idx == idx else None)

            # Card back colors
            if chosen_by == 1:
                bg_col     = (40, 18, 18)
                stripe_col = (52, 26, 26)
                b_col, b_w = P1_COLOR, 3
            elif chosen_by == 2:
                bg_col     = (16, 24, 52)
                stripe_col = (22, 34, 68)
                b_col, b_w = P2_COLOR, 3
            else:
                bg_col     = (20, 26, 50)
                stripe_col = (28, 34, 62)
                b_col, b_w = (40, 48, 82), 1

            # Masked card back with stripe pattern
            card_surf = _make_rounded_cell(_CELL_W, _CELL_H, bg_col, stripe_col, radius=10)
            mask      = Image.new("L", (_CELL_W, _CELL_H), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, _CELL_W - 1, _CELL_H - 1), radius=10, fill=255)
            img.paste(card_surf.convert("RGB"), (cx, cy), mask)
            draw = ImageDraw.Draw(img)

            # osu! logo — full opacity if chosen, dim otherwise
            if osu_logo:
                lx = cx + (_CELL_W - osu_logo.width)  // 2
                ly = cy + (_CELL_H - osu_logo.height) // 2
                logo_rgba = osu_logo.convert("RGBA")
                if not chosen_by:
                    r, g, b_ch, a = logo_rgba.split()
                    a = a.point(lambda v: int(v * 0.30))
                    logo_rgba = Image.merge("RGBA", (r, g, b_ch, a))
                img.paste(logo_rgba, (lx, ly), logo_rgba)
                draw = ImageDraw.Draw(img)

            # Border
            draw.rounded_rectangle((cx, cy, cx + _CELL_W, cy + _CELL_H),
                                   radius=10, outline=b_col, width=b_w)

            # Player name stripe at bottom (if chosen)
            if chosen_by:
                stripe_y   = cy + _CELL_H - 28
                stripe_col = P1_COLOR if chosen_by == 1 else P2_COLOR
                pname      = p1_name  if chosen_by == 1 else p2_name
                draw.rounded_rectangle(
                    (cx + 5, stripe_y, cx + _CELL_W - 5, cy + _CELL_H - 5),
                    radius=5, fill=stripe_col,
                )
                self._text_center(draw, cx + _CELL_W // 2, stripe_y + 5,
                                  pname[:14], self.font_stat_label, (255, 255, 255))
                # Number circle raised above stripe
                _draw_number_circle(draw, cx + _CELL_W - 8 - 11,
                                    stripe_y - 11 - 5, idx + 1, self.font_stat_label,
                                    bg=(35, 40, 65), outline=(60, 68, 96))
            else:
                _draw_number_circle(draw, cx + _CELL_W - 8 - 11,
                                    cy + _CELL_H - 8 - 11, idx + 1, self.font_stat_label,
                                    bg=(35, 40, 65), outline=(60, 68, 96))

        # ── Score bar ─────────────────────────────────────────────────────────
        y_sc = y_grid + 2 * (_CELL_H + _GCELL_GAP)
        draw.rectangle([(0, y_sc), (W, y_sc + score_h)], fill=HEADER_BG)
        draw.line([(0, y_sc), (W, y_sc)], fill=(44, 44, 70), width=1)

        self._text_center(draw, W // 2, y_sc + 6,
                          f'{score_p1:,}  :  {score_p2:,}', self.font_label, TEXT_PRIMARY)

        bar_y  = y_sc + 26
        bar_w  = W - 2 * PADDING_X
        bar_th = 8
        _draw_score_bar(draw, PADDING_X, bar_y, bar_w, bar_th,
                        score_p1, score_p2, target)
        draw.text((PADDING_X, bar_y + bar_th + 4),
                  p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_y + bar_th + 4,
                         p2_name, self.font_stat_label, P2_COLOR)
        self._text_center(draw, W // 2, bar_y + bar_th + 4,
                          f'/ {target:,}', self.font_stat_label, (75, 75, 100))

        return self._save(img)

    async def generate_bsk_group_pick_card_async(self, data: Dict) -> BytesIO:
        from io import BytesIO as _BytesIO

        async def _load(raw, url):
            if raw:
                try:    return Image.open(_BytesIO(raw)).convert("RGBA")
                except: pass
            return await download_image(url) if url else None

        p1c, p2c = await asyncio.gather(
            _load(data.get('p1_cover_data'), data.get('p1_cover_url')),
            _load(data.get('p2_cover_data'), data.get('p2_cover_url')),
        )
        return await asyncio.to_thread(
            self.generate_bsk_group_pick_card,
            {**data, 'p1_cover': p1c, 'p2_cover': p2c},
        )
