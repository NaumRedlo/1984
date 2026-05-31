"""Admin commands for the HPS map pool.

Plan: unified-giggling-tiger (step 9/9, follow-up).

Counterpart to bot/handlers/admin/bsk_pool.py for hps_map_pool. Five
commands, all admin-gated:

  hpsaddmap    <beatmap_id>          — ingest a single ranked map.
  hpsrefreshmap <beatmap_id>         — re-fetch metadata + re-profile.
  hpspoollist  [page]                — paginated list of pool entries.
  hpsdelmap    <beatmap_id>          — disable a pool entry (soft-delete).
  hpspoolstats                       — distribution by genre / length /
                                       bpm bucket / ranked_status.

All heavy lifting lives in services/hps/hps_pool.py; this module only
handles argument parsing, formatting, and the inline keyboard for the
list pager.
"""

from __future__ import annotations

from collections import Counter

from aiogram import F, Router, types
from sqlalchemy import func, select

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.hps_map_pool import HpsMapPool
from services.hps.hps_pool import (
    add_map_to_hps_pool,
    hps_map_is_broken,
    refresh_hps_map,
)
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_hps_pool")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# Page size for hpspoollist — kept aligned with bskpool for a familiar feel.
_HPS_POOL_PER_PAGE = 15


# ─── hpsaddmap ───────────────────────────────────────────────────────────────


@router.message(TextTriggerFilter("hpsaddmap"))
async def cmd_hps_add_map(
    message: types.Message, trigger_args: TriggerArgs, osu_api_client,
):
    """hpsaddmap <beatmap_id> — fetch + profile + insert into hps_map_pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>hpsaddmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    wait = await message.answer(f"Загружаю карту {beatmap_id} в HPS пул…")

    try:
        entry = await add_map_to_hps_pool(osu_api_client, beatmap_id)
    except Exception as e:
        logger.exception(f"hpsaddmap({beatmap_id}) failed")
        await wait.edit_text(f"Ошибка: <code>{escape_html(str(e))}</code>", parse_mode="HTML")
        return

    if entry is None:
        # Either already in pool or osu! API returned nothing — disambiguate.
        async with get_db_session() as session:
            existing = (await session.execute(
                select(HpsMapPool).where(HpsMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()
        if existing:
            await wait.edit_text(
                f"Карта <b>{beatmap_id}</b> уже в HPS пуле: "
                f"{escape_html(existing.artist)} - {escape_html(existing.title)} "
                f"[{escape_html(existing.version)}] "
                f"({existing.star_rating:.2f}★, "
                f"{existing.length_bucket}/{existing.bpm_bucket}/{existing.genre_tag})",
                parse_mode="HTML",
            )
        else:
            await wait.edit_text(f"Карта {beatmap_id} не найдена в osu! API.")
        return

    await wait.edit_text(
        f"✅ Добавлена в HPS пул:\n"
        f"<b>{escape_html(entry.artist)} - {escape_html(entry.title)} "
        f"[{escape_html(entry.version)}]</b>\n"
        f"{entry.star_rating:.2f}★ · {entry.bpm:.0f} BPM · {entry.length}s\n"
        f"genre=<b>{entry.genre_tag}</b> · length=<b>{entry.length_bucket}</b> · "
        f"bpm=<b>{entry.bpm_bucket}</b> · status=<b>{entry.ranked_status}</b>",
        parse_mode="HTML",
    )


# ─── hpsrefreshmap ───────────────────────────────────────────────────────────


@router.message(TextTriggerFilter("hpsrefreshmap"))
async def cmd_hps_refresh_map(
    message: types.Message, trigger_args: TriggerArgs, osu_api_client,
):
    """hpsrefreshmap <beatmap_id> — re-pull metadata + re-profile an entry."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>hpsrefreshmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    wait = await message.answer(f"Обновляю карту {beatmap_id}…")

    try:
        result = await refresh_hps_map(osu_api_client, beatmap_id)
    except Exception as e:
        logger.exception(f"hpsrefreshmap({beatmap_id}) failed")
        await wait.edit_text(f"Ошибка: <code>{escape_html(str(e))}</code>", parse_mode="HTML")
        return

    status_emoji = {
        "ok":        "✅",
        "partial":   "⚠️",
        "not_found": "❌",
        "no_data":   "🚫",
        "error":     "💥",
    }.get(result["status"], "❓")

    reasons   = ", ".join(result.get("reasons") or []) or "—"
    updated   = ", ".join(result.get("updated") or []) or "—"
    await wait.edit_text(
        f"{status_emoji} <b>{result['status']}</b> — {escape_html(result['message'])}\n"
        f"Жалобы до: <code>{escape_html(reasons)}</code>\n"
        f"Обновлено: <code>{escape_html(updated)}</code>",
        parse_mode="HTML",
    )


# ─── hpsdelmap ───────────────────────────────────────────────────────────────


@router.message(TextTriggerFilter("hpsdelmap"))
async def cmd_hps_del_map(message: types.Message, trigger_args: TriggerArgs):
    """hpsdelmap <beatmap_id> — disable an entry (soft-delete, sets enabled=0).

    The row is kept so its anti-repeat history (last_used_at/use_count) is
    preserved across re-enables.
    """
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>hpsdelmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    async with get_db_session() as session:
        entry = (await session.execute(
            select(HpsMapPool).where(HpsMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            await message.answer(f"Карта {beatmap_id} не найдена в HPS пуле.")
            return
        entry.enabled = False
        await session.commit()

    await message.answer(f"Карта {beatmap_id} отключена из HPS пула.")


# ─── hpspoollist ─────────────────────────────────────────────────────────────


async def _hps_pool_render(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render one page of hpspoollist with header stats + nav keyboard."""
    async with get_db_session() as session:
        total    = (await session.execute(
            select(func.count(HpsMapPool.id))
        )).scalar() or 0
        enabled  = (await session.execute(
            select(func.count(HpsMapPool.id)).where(HpsMapPool.enabled == True)  # noqa: E712
        )).scalar() or 0

        pages = max(1, (total + _HPS_POOL_PER_PAGE - 1) // _HPS_POOL_PER_PAGE)
        page = max(1, min(page, pages))
        offset = (page - 1) * _HPS_POOL_PER_PAGE

        rows = (await session.execute(
            select(HpsMapPool)
            .order_by(HpsMapPool.star_rating.asc(), HpsMapPool.id.asc())
            .offset(offset).limit(_HPS_POOL_PER_PAGE)
        )).scalars().all()

    lines = [
        "<b>HPS Map Pool</b>",
        f"Всего: <b>{total}</b>   Включено: <b>{enabled}</b>   "
        f"Откл.: <b>{total - enabled}</b>",
        "",
        f"Страница {page}/{pages}:",
    ]
    if not rows:
        lines.append("<i>— пусто —</i>")
    else:
        for m in rows:
            tag = "" if m.enabled else " ❌"
            lines.append(
                f"<code>{m.beatmap_id}</code> "
                f"{m.star_rating:.1f}★ "
                f"[{m.length_bucket or '?'}/{m.bpm_bucket or '?'}/{m.genre_tag or '?'}] "
                f"{escape_html(m.artist)} - {escape_html(m.title)}{tag}"
            )

    nav: list[types.InlineKeyboardButton] = []
    if pages > 1:
        if page > 1:
            nav.append(types.InlineKeyboardButton(
                text="◀", callback_data=f"hpspool:page:{page - 1}"
            ))
        nav.append(types.InlineKeyboardButton(
            text=f"{page}/{pages}", callback_data="hpspool:noop"
        ))
        if page < pages:
            nav.append(types.InlineKeyboardButton(
                text="▶", callback_data=f"hpspool:page:{page + 1}"
            ))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[nav] if nav else [])
    return "\n".join(lines), kb


@router.message(TextTriggerFilter("hpspoollist", "hpspl"))
async def cmd_hps_pool_list(message: types.Message, trigger_args: TriggerArgs):
    """hpspoollist [page] — list HPS map pool with pagination."""
    args = (trigger_args.args or "").strip()
    page = max(1, int(args)) if args.isdigit() else 1
    text, kb = await _hps_pool_render(page)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("hpspool:page:"))
async def on_hps_pool_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[-1])
    text, kb = await _hps_pool_render(page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


# ─── hpspoolstats ────────────────────────────────────────────────────────────


async def build_hps_pool_stats_report() -> str:
    """Build the HPS pool-stats text (HTML). Shared by the `hpspoolstats`
    handler and the admin panel's execute button."""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(HpsMapPool)
        )).scalars().all()

    if not rows:
        return "HPS пул пуст."

    total = len(rows)
    enabled = sum(1 for m in rows if m.enabled)
    broken = sum(1 for m in rows if hps_map_is_broken(m)[0])
    used   = sum(1 for m in rows if m.last_used_at is not None)

    def _dist(attr: str) -> str:
        counts = Counter(getattr(m, attr) or "?" for m in rows)
        items = sorted(counts.items(), key=lambda x: -x[1])
        return ", ".join(
            f"{k}=<b>{v}</b> ({v / total * 100:.0f}%)" for k, v in items
        ) or "—"

    avg_sr = sum(m.star_rating or 0 for m in rows) / total
    avg_uses = sum(m.use_count or 0 for m in rows) / total

    lines = [
        "<b>HPS Map Pool — статистика</b>",
        f"Всего: <b>{total}</b>   Включено: <b>{enabled}</b>   "
        f"Битых: <b>{broken}</b>   Использовано хотя бы раз: <b>{used}</b>",
        f"Средний SR: <b>{avg_sr:.2f}★</b>   Среднее use_count: <b>{avg_uses:.2f}</b>",
        "",
        f"<b>Жанры:</b> {_dist('genre_tag')}",
        f"<b>Длина:</b> {_dist('length_bucket')}",
        f"<b>BPM:</b> {_dist('bpm_bucket')}",
        f"<b>Статус:</b> {_dist('ranked_status')}",
    ]
    return "\n".join(lines)


@router.message(TextTriggerFilter("hpspoolstats"))
async def cmd_hps_pool_stats(message: types.Message):
    """hpspoolstats — distribution by genre / length_bucket / bpm_bucket /
    ranked_status, plus broken/used summaries."""
    await message.answer(await build_hps_pool_stats_report(), parse_mode="HTML")
