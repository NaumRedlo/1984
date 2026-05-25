from datetime import datetime, timedelta
from uuid import uuid4

from aiogram import Router, types, F
from sqlalchemy import select, func, desc

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bsk_misc")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

# ─── BSK pool diagnostic dump  (Phase 1 of skill metric overhaul) ────────────

def _percentiles(values: list[float], pcts: list[float]) -> list[float]:
    """Return values at requested percentiles (0..1) from a sample."""
    if not values:
        return [0.0] * len(pcts)
    s = sorted(values)
    out = []
    for p in pcts:
        idx = max(0, min(len(s) - 1, int(round(p * (len(s) - 1)))))
        out.append(s[idx])
    return out


def _fmt_pct(values: list[float]) -> str:
    """Format a sample as `min / p25 / p50 / p75 / max  (mean ± std)`."""
    if not values:
        return "—"
    import math
    p = _percentiles(values, [0.0, 0.25, 0.50, 0.75, 1.0])
    mean = sum(values) / len(values)
    std  = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return (f"{p[0]:.3f} / {p[1]:.3f} / <b>{p[2]:.3f}</b> / {p[3]:.3f} / {p[4]:.3f}"
            f"   μ={mean:.3f} σ={std:.3f}")


# ─── BSK rating reset (admin-only, double-confirm) ───────────────────────────
# Hard-resets every player's BSK rating components. There used to be a
# migration `bsk_reset_calibration` that ran on every bot start and silently
# wiped progress; it was removed. This explicit command replaces it as the
# *only* way to do a global reset, and it requires a confirmation tap.

# slot_id -> {tg_id: int, mode: str, seed: str, created_at: datetime}
_bskreset_slots: dict[str, dict] = {}


def _register_bskreset_slot(tg_id: int, mode: str, seed: str) -> str:
    slot_id = uuid4().hex[:8]
    _bskreset_slots[slot_id] = {
        "tg_id": tg_id,
        "mode": mode,
        "seed": seed,
        "created_at": datetime.utcnow(),
    }
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for sid, data in list(_bskreset_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _bskreset_slots.pop(sid, None)
    return slot_id


@router.message(TextTriggerFilter("bskreset"))
async def cmd_bsk_reset(message: types.Message, trigger_args: TriggerArgs):
    """bskreset [casual|ranked|all] [pp|flat] — reset every player's BSK rating.

    Modes (default `all`):
      - <code>casual</code>  — only casual ratings
      - <code>ranked</code>  — only ranked ratings
      - <code>all</code>     — both modes

    Seed (default `pp`):
      - <code>pp</code>    — re-seed each player from their current osu! pp
                            via <code>starting_mu_from_pp()</code>
      - <code>flat</code>  — hard reset to 250/250/250/250 (raw model defaults)

    Both wins/losses, sigma, peak_mu and placement_matches_left are reset too.
    Requires a confirmation tap; nothing is written until you press the button.
    """
    from db.models.bsk_rating import BskRating
    from sqlalchemy import func as _f

    raw = (trigger_args.args or "").strip().lower().split()
    mode = "all"
    seed = "pp"
    for tok in raw:
        if tok in ("casual", "ranked", "all"):
            mode = tok
        elif tok in ("pp", "flat"):
            seed = tok

    # Count what would be affected so the admin sees the blast radius.
    async with get_db_session() as session:
        if mode == "all":
            total = (await session.execute(
                select(_f.count()).select_from(BskRating)
            )).scalar() or 0
        else:
            total = (await session.execute(
                select(_f.count()).select_from(BskRating).where(BskRating.mode == mode)
            )).scalar() or 0

    if total == 0:
        await message.answer("Нечего сбрасывать — таблица BSK-рейтингов пуста.")
        return

    seed_label = (
        "по pp игроков (через <code>starting_mu_from_pp</code>)"
        if seed == "pp" else "плоский (250/250/250/250)"
    )
    mode_label = {"all": "обоих режимов (casual + ranked)",
                  "casual": "casual", "ranked": "ranked"}[mode]

    slot = _register_bskreset_slot(message.from_user.id, mode, seed)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=f"⚠️ Сбросить {total}",
                callback_data=f"bskreset:apply:{slot}",
            ),
            types.InlineKeyboardButton(
                text="Отмена",
                callback_data=f"bskreset:cancel:{slot}",
            ),
        ],
    ])

    await message.answer(
        "<b>Сброс рейтингов BSK</b>\n\n"
        f"Будет сброшено: <b>{total}</b> рейтинг(ов).\n"
        f"Режим: <b>{mode_label}</b>\n"
        f"Seed: {seed_label}\n\n"
        "Это <b>необратимо</b>. Будут затёрты:\n"
        " • <code>mu_aim / mu_speed / mu_acc / mu_cons</code>\n"
        " • <code>sigma_*</code> → 100\n"
        " • <code>placement_matches_left</code> → 10\n"
        " • <code>wins / losses</code> → 0\n"
        " • <code>peak_mu</code> → стартовое значение\n\n"
        "История дуэлей в <code>bsk_duels</code> и раунды останутся нетронутыми.\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("bskreset:"))
async def on_bsk_reset_callback(callback: types.CallbackQuery):
    """Confirm/cancel for `bskreset`. Performs the destructive UPDATE."""
    from db.models.bsk_rating import BskRating
    from services.bsk.rating import starting_mu_from_pp

    parts = callback.data.split(":")
    # bskreset:<action>:<slot>
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[1]
    slot_id = parts[2]

    slot = _bskreset_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskreset: edit_reply_markup failed (expired slot)", exc_info=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    if action == "cancel":
        _bskreset_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            logger.debug("bskreset: cancel edit_text failed", exc_info=True)
        await callback.answer("Отменено.")
        return

    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    mode: str = slot["mode"]
    seed: str = slot["seed"]
    _bskreset_slots.pop(slot_id, None)

    # ── Apply ───────────────────────────────────────────────────────────────
    affected = 0
    async with get_db_session() as session:
        stmt = select(BskRating)
        if mode != "all":
            stmt = stmt.where(BskRating.mode == mode)
        ratings = (await session.execute(stmt)).scalars().all()

        # Pre-fetch player_pp for the pp-seed mode in a single query to avoid
        # N round-trips when the pool is large.
        pp_by_user: dict[int, float] = {}
        if seed == "pp" and ratings:
            user_ids = list({r.user_id for r in ratings})
            from db.models.user import User
            urows = (await session.execute(
                select(User.id, User.player_pp).where(User.id.in_(user_ids))
            )).all()
            pp_by_user = {uid: float(pp or 0.0) for uid, pp in urows}

        for r in ratings:
            if seed == "pp":
                start_mu = starting_mu_from_pp(pp_by_user.get(r.user_id, 0.0))
            else:  # flat
                start_mu = 1000.0
            per_comp = start_mu / 4.0

            r.mu_aim   = per_comp
            r.mu_speed = per_comp
            r.mu_acc   = per_comp
            r.mu_cons  = per_comp
            r.sigma_aim   = 100.0
            r.sigma_speed = 100.0
            r.sigma_acc   = 100.0
            r.sigma_cons  = 100.0
            r.placement_matches_left = 10
            r.wins = 0
            r.losses = 0
            r.peak_mu = start_mu
            r.updated_at = datetime.utcnow()
            affected += 1

        await session.commit()

    logger.warning(
        f"bskreset applied by admin tg_id={callback.from_user.id} "
        f"mode={mode} seed={seed} affected={affected}"
    )

    try:
        new_text = (
            (callback.message.html_text or "")
            + f"\n\n<b>✅ Сброшено: {affected}</b>"
            + f"\nseed=<code>{seed}</code>, mode=<code>{mode}</code>"
        )
        await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskreset: post-apply edit_reply_markup failed", exc_info=True)
        await callback.message.answer(
            f"✅ Сброшено: <b>{affected}</b> рейтинг(ов).",
            parse_mode="HTML",
        )

    await callback.answer(f"Сброшено: {affected}")


@router.message(TextTriggerFilter("recalcranks"))
async def cmd_recalc_ranks(message: types.Message):
    from utils.hp_calculator import get_rank_for_hp
    from db.models.user import User

    wait = await message.answer("Пересчитываю ранги…")
    async with get_db_session() as session:
        users = (await session.execute(select(User))).scalars().all()
        updated = 0
        for u in users:
            new_rank = get_rank_for_hp(u.hps_points or 0)
            if u.rank != new_rank:
                u.rank = new_rank
                updated += 1
        await session.commit()
    await wait.edit_text(
        f"✅ Ранги пересчитаны.\nОбновлено: <b>{updated}</b> из {len(users)}.",
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("bskdiag"))
async def cmd_bsk_diag(message: types.Message):
    """bskdiag — diagnostic snapshot of the BSK map pool (post-Phase-2).

    Shows distribution of map_type by stars, percentile ranges of *_stars,
    average parser features per type, and top picks per skill.
    Read-only, no DB writes.
    """
    from db.models.bsk_map_pool import BskMapPool

    wait = await message.answer("Считаю диагностику пула…")

    async with get_db_session() as session:
        maps = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)  # noqa: E712
        )).scalars().all()

    if not maps:
        await wait.edit_text("Пул пуст.", parse_mode="HTML")
        return

    n = len(maps)

    # ── 1. map_type distribution ──────────────────────────────────────────
    type_counts: dict[str, int] = {}
    for m in maps:
        t = m.map_type or "—"
        type_counts[t] = type_counts.get(t, 0) + 1

    # ── 2. star + weight percentiles per skill axis ───────────────────────
    star_buckets = {
        "aim":   [m.aim_stars   for m in maps if m.aim_stars   is not None],
        "speed": [m.speed_stars for m in maps if m.speed_stars is not None],
        "acc":   [m.acc_stars   for m in maps if m.acc_stars   is not None],
        "cons":  [m.cons_stars  for m in maps if m.cons_stars  is not None],
    }
    w_buckets = {
        "aim":   [m.w_aim   or 0.0 for m in maps],
        "speed": [m.w_speed or 0.0 for m in maps],
        "acc":   [m.w_acc   or 0.0 for m in maps],
        "cons":  [m.w_cons  or 0.0 for m in maps],
    }

    # ── 3. argmax sanity check (in case map_type lags stars) ──────────────
    argmax_counts = {"aim": 0, "speed": 0, "acc": 0, "cons": 0}
    has_stars = 0
    for m in maps:
        if m.aim_stars is None and m.speed_stars is None and m.acc_stars is None and m.cons_stars is None:
            continue
        has_stars += 1
        ss = {"aim": m.aim_stars or 0, "speed": m.speed_stars or 0,
              "acc": m.acc_stars or 0, "cons": m.cons_stars or 0}
        argmax_counts[max(ss, key=ss.get)] += 1

    # ── 4. parser feature averages by current map_type ────────────────────
    feat_keys = [
        ("subdiv_ent", "f_subdiv_entropy"),
        ("polyrhy",    "f_polyrhythm_density"),
        ("off_beat",   "f_off_beat_ratio"),
        ("jack",       "f_jack_density"),
        ("od_dem",     "f_od_demand"),
        ("flow_brk",   "f_flow_break"),
        ("jump_dens",  "f_jump_density"),
        ("jump_vel",   "f_jump_vel"),
        ("bpm_rel",    "f_bpm_rel_speed"),
        ("stream",     "f_stream"),
        ("burst",      "f_burst"),
        ("density_v",  "f_density_var"),
        ("int_floor",  "f_intensity_floor"),
        ("repeat",     "f_pattern_repeat"),
    ]
    feat_by_type: dict[str, dict[str, list[float]]] = {}
    for m in maps:
        t = m.map_type or "—"
        d = feat_by_type.setdefault(t, {})
        for label, attr in feat_keys:
            v = getattr(m, attr, None)
            if v is None:
                continue
            d.setdefault(label, []).append(float(v))

    # ── 5. Top-5 per skill by stars ───────────────────────────────────────
    def _top5(attr: str) -> list:
        vals = [m for m in maps if getattr(m, attr, None) is not None]
        return sorted(vals, key=lambda x: getattr(x, attr) or 0.0, reverse=True)[:5]
    top_aim   = _top5("aim_stars")
    top_speed = _top5("speed_stars")
    top_acc   = _top5("acc_stars")
    top_cons  = _top5("cons_stars")

    # ── 6. SR-band distribution × type ────────────────────────────────────
    sr_bands = [(0, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 12)]
    sr_band_counts: dict[tuple, dict[str, int]] = {b: {} for b in sr_bands}
    for m in maps:
        sr = m.star_rating or 0.0
        for lo, hi in sr_bands:
            if lo <= sr < hi:
                bucket = sr_band_counts[(lo, hi)]
                t = m.map_type or "—"
                bucket[t] = bucket.get(t, 0) + 1
                break

    # ── Build output ──────────────────────────────────────────────────────
    lines = [
        f"<b>BSK pool diagnostic</b>  ·  всего: <b>{n}</b> карт"
        f"  ·  со звёздами: <b>{has_stars}</b>",
        "",
        "<b>① map_type:</b>",
    ]
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = c / n * 100
        lines.append(f"  • <code>{t:<6}</code>  {c:>5}  ({pct:5.1f}%)")
    lines.append("")

    if has_stars:
        lines.append("<b>② argmax(*_stars) (sanity):</b>")
        for t in ("aim", "speed", "acc", "cons"):
            c = argmax_counts[t]
            pct = c / max(has_stars, 1) * 100
            lines.append(f"  • <code>{t:<6}</code>  {c:>5}  ({pct:5.1f}%)")
        lines.append("")

    # Star percentiles
    if has_stars:
        lines.append("<b>③ Перцентили *_stars [0..10]</b>:")
        for k in ("aim", "speed", "acc", "cons"):
            if star_buckets[k]:
                lines.append(f"  <code>{k:<6}</code>  {_fmt_pct(star_buckets[k])}")
        lines.append("")

    # Weight percentiles
    lines.append("<b>④ Перцентили w_* [0..1]</b>:")
    for k in ("aim", "speed", "acc", "cons"):
        lines.append(f"  <code>{k:<6}</code>  {_fmt_pct(w_buckets[k])}")
    lines.append("")

    # Feature averages per type
    lines.append("<b>⑤ Средние фичи по типам</b>:")
    for t in ("aim", "speed", "acc", "cons", "—"):
        if t not in feat_by_type:
            continue
        lines.append(f"  <i>{t}</i>  ({type_counts.get(t, 0)} карт):")
        d = feat_by_type[t]
        items = [(label, sum(d[label])/len(d[label]) if d.get(label) else None)
                 for label, _ in feat_keys]
        row = []
        for label, val in items:
            row.append(f"{label}={val:.3f}" if val is not None else f"{label}=—")
            if len(row) == 4:
                lines.append("    " + "  ".join(row))
                row = []
        if row:
            lines.append("    " + "  ".join(row))
    lines.append("")

    # SR band breakdown
    lines.append("<b>⑥ Типы по SR-полосам</b>:")
    for (lo, hi) in sr_bands:
        bucket = sr_band_counts[(lo, hi)]
        total_b = sum(bucket.values())
        if total_b == 0:
            continue
        parts = ", ".join(f"{t}:{c}" for t, c in sorted(bucket.items(), key=lambda x: -x[1]))
        lines.append(f"  <code>{lo}–{hi}★</code>  ({total_b}):  {parts}")
    lines.append("")

    # Top maps per skill
    def _fmt_top(label: str, attr: str, top: list) -> list:
        out = [f"<b>{label}:</b>"]
        for i, m in enumerate(top, 1):
            title = (m.title or "?")[:28]
            ver   = (m.version or "")[:18]
            v     = getattr(m, attr) or 0.0
            sr    = m.star_rating or 0.0
            out.append(
                f"  {i}. <code>{v:5.2f}</code>  "
                f"{escape_html(title)} [{escape_html(ver)}]  {sr:.2f}★"
            )
        return out

    lines.append("<b>⑦ Топ карт по каждой шкале</b>")
    lines.extend(_fmt_top("AIM", "aim_stars", top_aim))
    lines.extend(_fmt_top("SPEED", "speed_stars", top_speed))
    lines.extend(_fmt_top("ACC", "acc_stars", top_acc))
    lines.extend(_fmt_top("CONS", "cons_stars", top_cons))

    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > 3900 and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += len(line) + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    if chunks:
        await wait.edit_text(chunks[0], parse_mode="HTML")
        for chunk in chunks[1:]:
            await message.answer(chunk, parse_mode="HTML")


# ─── Season management ────────────────────────────────────────────────────────

_seasonstart_slots: dict[str, dict] = {}


def _register_seasonstart_slot(tg_id: int, new_number: int, user_count: int) -> str:
    from uuid import uuid4
    slot_id = uuid4().hex[:8]
    _seasonstart_slots[slot_id] = {
        "tg_id": tg_id,
        "new_number": new_number,
        "user_count": user_count,
        "created_at": datetime.utcnow(),
    }
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for sid, data in list(_seasonstart_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _seasonstart_slots.pop(sid, None)
    return slot_id


@router.message(TextTriggerFilter("seasonstart"))
async def cmd_season_start(message: types.Message):
    """seasonstart — завершить текущий сезон и начать новый."""
    from db.models.season import Season
    from db.models.user import User
    from sqlalchemy import func as _f

    async with get_db_session() as session:
        current = (await session.execute(
            select(Season).where(Season.is_active == 1)
        )).scalar_one_or_none()
        old_number = current.number if current else 0
        new_number = old_number + 1
        user_count = (await session.execute(
            select(_f.count()).select_from(User)
        )).scalar() or 0

    slot = _register_seasonstart_slot(message.from_user.id, new_number, user_count)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text=f"🚀 Запустить сезон {new_number}",
            callback_data=f"seasonstart:apply:{slot}",
        ),
        types.InlineKeyboardButton(
            text="Отмена",
            callback_data=f"seasonstart:cancel:{slot}",
        ),
    ]])

    await message.answer(
        f"<b>Старт нового сезона</b>\n\n"
        f"Текущий сезон: <b>{old_number}</b>\n"
        f"Новый сезон: <b>{new_number}</b>\n"
        f"Затронуто игроков: <b>{user_count}</b>\n\n"
        "Это <b>необратимо</b>. Будут:\n"
        " • Сохранены снапшоты всех игроков\n"
        " • Сброшены HPS-очки (с бонусом за прошлый сезон)\n"
        " • Сброшены BSK-рейтинги (soft reset)\n\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("seasonstart:"))
async def on_seasonstart_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    action = parts[1]
    slot_id = parts[2]
    slot = _seasonstart_slots.get(slot_id)

    if not slot:
        await callback.answer("Сессия истекла.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    if action == "cancel":
        _seasonstart_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
        await callback.answer("Отменено.")
        return

    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    new_number = slot["new_number"]
    _seasonstart_slots.pop(slot_id, None)

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    wait_msg = await callback.message.answer("⏳ Запускаю новый сезон…")

    try:
        from services.season import start_new_season
        season = await start_new_season()
        await wait_msg.edit_text(
            f"✅ <b>Сезон {season.number} запущен!</b>\n"
            "HPS-очки сброшены, BSK-рейтинги обновлены.",
            parse_mode="HTML",
        )
        logger.warning(
            f"seasonstart applied by admin tg_id={callback.from_user.id} "
            f"new_season={season.number}"
        )
    except Exception as e:
        logger.error(f"seasonstart failed: {e}", exc_info=True)
        await wait_msg.edit_text(
            f"❌ Ошибка при запуске сезона: <code>{e}</code>",
            parse_mode="HTML",
        )

    await callback.answer()

