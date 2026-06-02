from datetime import datetime, timedelta
from uuid import uuid4

from aiogram import Router, types, F
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.utils.paginator import build_pages, store_pages, nav_keyboard
from db.database import get_db_session
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_duel_misc")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

# ─── DUEL pool diagnostic dump  (Phase 1 of skill metric overhaul) ────────────

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


# ─── DUEL rating reset (admin-only, double-confirm) ───────────────────────────
# Hard-resets every player's DUEL rating components. There used to be a
# migration `duel_reset_calibration` that ran on every bot start and silently
# wiped progress; it was removed. This explicit command replaces it as the
# *only* way to do a global reset, and it requires a confirmation tap.

# slot_id -> {tg_id: int, mode: str, seed: str, created_at: datetime}
_duelreset_slots: dict[str, dict] = {}


def _register_duelreset_slot(tg_id: int, mode: str, seed: str) -> str:
    slot_id = uuid4().hex[:8]
    _duelreset_slots[slot_id] = {
        "tg_id": tg_id,
        "mode": mode,
        "seed": seed,
        "created_at": datetime.utcnow(),
    }
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for sid, data in list(_duelreset_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _duelreset_slots.pop(sid, None)
    return slot_id


@router.message(TextTriggerFilter("duelreset"))
async def cmd_duel_reset(message: types.Message, trigger_args: TriggerArgs):
    """duelreset [casual|ranked|all] [pp|flat] — reset every player's DUEL rating.

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
    from db.models.duel_rating import DuelRating
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
                select(_f.count()).select_from(DuelRating)
            )).scalar() or 0
        else:
            total = (await session.execute(
                select(_f.count()).select_from(DuelRating).where(DuelRating.mode == mode)
            )).scalar() or 0

    if total == 0:
        await message.answer("Нечего сбрасывать — таблица DUEL-рейтингов пуста.")
        return

    seed_label = (
        "по pp игроков (через <code>starting_mu_from_pp</code>)"
        if seed == "pp" else "плоский (250/250/250/250)"
    )
    mode_label = {"all": "обоих режимов (casual + ranked)",
                  "casual": "casual", "ranked": "ranked"}[mode]

    slot = _register_duelreset_slot(message.from_user.id, mode, seed)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=f"⚠️ Сбросить {total}",
                callback_data=f"duelreset:apply:{slot}",
            ),
            types.InlineKeyboardButton(
                text="Отмена",
                callback_data=f"duelreset:cancel:{slot}",
            ),
        ],
    ])

    await message.answer(
        "<b>Сброс рейтингов DUEL</b>\n\n"
        f"Будет сброшено: <b>{total}</b> рейтинг(ов).\n"
        f"Режим: <b>{mode_label}</b>\n"
        f"Seed: {seed_label}\n\n"
        "Это <b>необратимо</b>. Будут затёрты:\n"
        " • <code>mu / sigma</code> → стартовые значения\n"
        " • <code>placement_matches_left</code> → 10\n"
        " • <code>wins / losses / games</code> → 0\n"
        " • <code>peak_mu</code> → стартовое значение\n\n"
        "История дуэлей в <code>duels</code> и раунды останутся нетронутыми.\n"
        "Подтвердить?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("duelreset:"))
async def on_duel_reset_callback(callback: types.CallbackQuery):
    """Confirm/cancel for `duelreset`. Performs the destructive UPDATE."""
    from db.models.duel_rating import DuelRating
    from services.duel.rating import (
        starting_mu_from_pp, DUEL_TS_MU0, DUEL_TS_SIGMA0, PLACEMENT_MATCHES,
    )

    parts = callback.data.split(":")
    # duelreset:<action>:<slot>
    if len(parts) != 3:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[1]
    slot_id = parts[2]

    slot = _duelreset_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("duelreset: edit_reply_markup failed (expired slot)", exc_info=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    if action == "cancel":
        _duelreset_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text(
                (callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            logger.debug("duelreset: cancel edit_text failed", exc_info=True)
        await callback.answer("Отменено.")
        return

    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    mode: str = slot["mode"]
    seed: str = slot["seed"]
    _duelreset_slots.pop(slot_id, None)

    # ── Apply ───────────────────────────────────────────────────────────────
    affected = 0
    async with get_db_session() as session:
        stmt = select(DuelRating)
        if mode != "all":
            stmt = stmt.where(DuelRating.mode == mode)
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
                start_mu = DUEL_TS_MU0

            r.mu = start_mu
            r.sigma = DUEL_TS_SIGMA0
            r.placement_matches_left = PLACEMENT_MATCHES
            r.games = 0
            r.wins = 0
            r.losses = 0
            r.peak_mu = start_mu
            r.updated_at = datetime.utcnow()
            affected += 1

        await session.commit()

    logger.warning(
        f"duelreset applied by admin tg_id={callback.from_user.id} "
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
            logger.debug("duelreset: post-apply edit_reply_markup failed", exc_info=True)
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


@router.message(TextTriggerFilter("dueldiag"))
async def cmd_duel_diag(message: types.Message):
    """dueldiag — diagnostic snapshot of the DUEL map pool.

    Objective stats only (the per-axis skill classifier was removed): SR-band
    distribution, percentiles for SR / length / max_combo / CS / AR / OD / HP,
    and the hardest/easiest picks. Read-only, no DB writes.
    """
    from db.models.duel_map_pool import DuelMapPool

    wait = await message.answer("Считаю диагностику пула…")

    async with get_db_session() as session:
        maps = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.enabled == True)  # noqa: E712
        )).scalars().all()

    if not maps:
        await wait.edit_text("Пул пуст.", parse_mode="HTML")
        return

    n = len(maps)

    # ── SR-band distribution ──────────────────────────────────────────────
    sr_bands = [(0, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 12)]
    sr_band_counts: dict[tuple, int] = {b: 0 for b in sr_bands}
    for m in maps:
        sr = m.star_rating or 0.0
        for lo, hi in sr_bands:
            if lo <= sr < hi:
                sr_band_counts[(lo, hi)] += 1
                break

    # ── Percentiles for the objective stats ───────────────────────────────
    def _vals(attr: str) -> list[float]:
        return [float(getattr(m, attr)) for m in maps if getattr(m, attr, None) is not None]

    stat_rows = [
        ("SR",     "star_rating"),
        ("длина",  "length"),
        ("комбо",  "max_combo"),
        ("CS",     "cs"),
        ("AR",     "ar"),
        ("OD",     "od"),
        ("HP",     "hp_drain"),
        ("BPM",    "bpm"),
    ]

    missing_length = sum(1 for m in maps if not m.length)
    missing_combo  = sum(1 for m in maps if not m.max_combo)

    # ── Build output ──────────────────────────────────────────────────────
    lines = [
        f"<b>DUEL pool diagnostic</b>  ·  активных: <b>{n}</b> карт",
        f"без length: <b>{missing_length}</b>   ·   без max_combo: <b>{missing_combo}</b>",
        "",
        "<b>① Распределение по SR</b>:",
    ]
    for (lo, hi) in sr_bands:
        c = sr_band_counts[(lo, hi)]
        if c == 0:
            continue
        pct = c / n * 100
        lines.append(f"  <code>{lo}–{hi}★</code>  {c:>5}  ({pct:5.1f}%)")
    lines.append("")

    lines.append("<b>② Перцентили (p10 / p50 / p90)</b>:")
    for label, attr in stat_rows:
        vals = _vals(attr)
        if vals:
            lines.append(f"  <code>{label:<6}</code>  {_fmt_pct(vals)}")
    lines.append("")

    # Hardest / easiest by SR
    by_sr = sorted(maps, key=lambda m: m.star_rating or 0.0, reverse=True)

    def _fmt_top(label: str, top: list) -> list:
        out = [f"<b>{label}:</b>"]
        for i, m in enumerate(top, 1):
            title = (m.title or "?")[:28]
            ver   = (m.version or "")[:18]
            sr    = m.star_rating or 0.0
            out.append(
                f"  {i}. <code>{sr:5.2f}★</code>  "
                f"{escape_html(title)} [{escape_html(ver)}]"
            )
        return out

    lines.append("<b>③ Сложнейшие карты</b>")
    lines.extend(_fmt_top("ТОП SR", by_sr[:5]))
    lines.append("")
    lines.append("<b>④ Лёгкие карты</b>")
    lines.extend(_fmt_top("МИН SR", list(reversed(by_sr[-5:]))))

    uid = message.from_user.id
    pages = build_pages(lines)
    store_pages("dueldiag", uid, pages)
    keyboard = nav_keyboard("dueldiag", uid, page=0, total=len(pages))
    await wait.edit_text(pages[0], parse_mode="HTML", reply_markup=keyboard)


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
        " • Сброшены DUEL-рейтинги (soft reset)\n\n"
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
            "HPS-очки сброшены, DUEL-рейтинги обновлены.",
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


def _prune_slots(slots: dict) -> None:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for sid, d in list(slots.items()):
        if d.get("created_at") and d["created_at"] < cutoff:
            slots.pop(sid, None)


def _confirm_kb(prefix: str, slot: str, apply_label: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=apply_label, callback_data=f"{prefix}:apply:{slot}"),
        types.InlineKeyboardButton(text="Отмена", callback_data=f"{prefix}:cancel:{slot}"),
    ]])


def _check_slot(callback: types.CallbackQuery, slots: dict):
    """Return (action, slot_dict) or (None, None) after answering on failure."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return None, None
    action, slot_id = parts[1], parts[2]
    slot = slots.get(slot_id)
    return action, (slot_id, slot)


# ─── HP wipe ──────────────────────────────────────────────────────────────────

_hpwipe_slots: dict[str, dict] = {}


@router.message(TextTriggerFilter("hpwipe", "wipehp"))
async def cmd_hp_wipe(message: types.Message):
    """hpwipe — обнулить HPS-очки всех игроков (DUEL-рейтинги не трогает)."""
    from db.models.user import User
    from sqlalchemy import func as _f
    async with get_db_session() as session:
        user_count = (await session.execute(select(_f.count()).select_from(User))).scalar() or 0

    slot = uuid4().hex[:8]
    _hpwipe_slots[slot] = {"tg_id": message.from_user.id, "created_at": datetime.utcnow()}
    _prune_slots(_hpwipe_slots)

    await message.answer(
        "<b>Вайп HPS-очков</b>\n\n"
        f"Затронуто игроков: <b>{user_count}</b>\n\n"
        "Это <b>необратимо</b>. У всех игроков:\n"
        " • HPS-очки → 0\n"
        " • Сезонный бонус → 0\n"
        " • Ранг → стартовый\n\n"
        "DUEL-рейтинги и снапшоты <b>не меняются</b>.\n\nПодтвердить?",
        parse_mode="HTML",
        reply_markup=_confirm_kb("hpwipe", slot, "🗑 Обнулить HP"),
    )


@router.callback_query(F.data.startswith("hpwipe:"))
async def on_hpwipe_callback(callback: types.CallbackQuery):
    action, found = _check_slot(callback, _hpwipe_slots)
    if found is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    slot_id, slot = found
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
        _hpwipe_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text((callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                                             parse_mode="HTML", reply_markup=None)
        except Exception:
            pass
        await callback.answer("Отменено.")
        return
    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    _hpwipe_slots.pop(slot_id, None)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    wait = await callback.message.answer("⏳ Обнуляю HP…")
    try:
        from services.season import wipe_all_hp
        n = await wipe_all_hp()
        await wait.edit_text(f"✅ HPS-очки обнулены у <b>{n}</b> игроков.", parse_mode="HTML")
        logger.warning(f"hpwipe applied by admin tg_id={callback.from_user.id}, users={n}")
    except Exception as e:
        logger.error(f"hpwipe failed: {e}", exc_info=True)
        await wait.edit_text(f"❌ Ошибка: <code>{escape_html(str(e))}</code>", parse_mode="HTML")
    await callback.answer()


# ─── Season list / void ───────────────────────────────────────────────────────

_seasonvoid_slots: dict[str, dict] = {}


@router.message(TextTriggerFilter("seasons", "seasonlist"))
async def cmd_seasons(message: types.Message):
    """seasons — список сезонов и их статус."""
    from services.season import list_all_seasons
    seasons = await list_all_seasons()
    if not seasons:
        await message.answer("Сезонов пока нет.")
        return
    lines = ["<b>Сезоны</b>"]
    for s in seasons:
        st = "🟢 активен" if s.is_active else "завершён"
        started = s.started_at.strftime("%Y-%m-%d") if s.started_at else "?"
        ended = f" → {s.ended_at.strftime('%Y-%m-%d')}" if s.ended_at else ""
        lines.append(f"#{s.number} — {st} — {started}{ended}")
    lines.append("\nАннулировать: <code>seasonvoid &lt;номер&gt;</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("seasonvoid"))
async def cmd_season_void(message: types.Message, trigger_args: TriggerArgs = None):
    """seasonvoid <номер> — аннулировать (удалить) сезон и его снапшоты."""
    arg = ((trigger_args.args if trigger_args else "") or "").strip()
    if not arg.isdigit():
        await message.answer("Использование: <code>seasonvoid &lt;номер&gt;</code>", parse_mode="HTML")
        return
    number = int(arg)

    from db.models.season import Season
    async with get_db_session() as session:
        season = (await session.execute(
            select(Season).where(Season.number == number)
        )).scalar_one_or_none()
    if not season:
        await message.answer(f"Сезон <b>{number}</b> не найден.", parse_mode="HTML")
        return

    slot = uuid4().hex[:8]
    _seasonvoid_slots[slot] = {"tg_id": message.from_user.id, "number": number, "created_at": datetime.utcnow()}
    _prune_slots(_seasonvoid_slots)

    active_note = " <i>(активен — будет реактивирован предыдущий)</i>" if season.is_active else ""
    await message.answer(
        f"<b>Аннулирование сезона {number}</b>{active_note}\n\n"
        "Будут удалены запись сезона и его снапшоты из БД.\n"
        "HPS-очки и DUEL-рейтинги игроков <b>не меняются</b>.\n\nПодтвердить?",
        parse_mode="HTML",
        reply_markup=_confirm_kb("seasonvoid", slot, f"🗑 Аннулировать сезон {number}"),
    )


@router.callback_query(F.data.startswith("seasonvoid:"))
async def on_seasonvoid_callback(callback: types.CallbackQuery):
    action, found = _check_slot(callback, _seasonvoid_slots)
    if found is None:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    slot_id, slot = found
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
        _seasonvoid_slots.pop(slot_id, None)
        try:
            await callback.message.edit_text((callback.message.html_text or "") + "\n\n<b>Отменено.</b>",
                                             parse_mode="HTML", reply_markup=None)
        except Exception:
            pass
        await callback.answer("Отменено.")
        return
    if action != "apply":
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    number = slot["number"]
    _seasonvoid_slots.pop(slot_id, None)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    wait = await callback.message.answer("⏳ Аннулирую сезон…")
    try:
        from services.season import void_season
        res = await void_season(number)
        if not res.get("ok"):
            await wait.edit_text(f"❌ Сезон {number} не найден.")
            await callback.answer()
            return
        extra = f"\nРеактивирован сезон <b>{res['reactivated']}</b>." if res.get("reactivated") else ""
        await wait.edit_text(
            f"✅ Сезон <b>{number}</b> аннулирован. Удалено снапшотов: <b>{res['snapshots']}</b>.{extra}",
            parse_mode="HTML",
        )
        logger.warning(f"seasonvoid applied by admin tg_id={callback.from_user.id}, season={number}")
    except Exception as e:
        logger.error(f"seasonvoid failed: {e}", exc_info=True)
        await wait.edit_text(f"❌ Ошибка: <code>{escape_html(str(e))}</code>", parse_mode="HTML")
    await callback.answer()

