"""Admin commands to inspect and wipe map pools.

  /poolhealth     — BSK pool diagnostic: counts, missing fields, type
                    distribution, alarm flags
  /poolwipe       — show counts + confirmation button (BSK + HPS)
  /poolwipebsk    — wipe only bsk_map_pool
  /poolwipehps    — wipe only hps_map_pool

Both pools were primarily filled by the (now-removed) autonomous crawler.
After removal the operator typically wants a clean slate to start fresh
with curated maps via `/import` or `/hpsaddmap`.

Wipes are destructive but SAFE: they only touch the *pool* tables
(map metadata stash), never `bounties`, `bsk_duels`, or anything that
references beatmap_id. Active bounties keep working; their snapshots
of map metadata live on the Bounty row itself.
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import delete, func, select

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool
from db.models.hps_map_pool import HpsMapPool
from services.bsk.map_selector import log_pool_health
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_pool_wipe")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


async def _pool_counts() -> tuple[int, int]:
    async with get_db_session() as session:
        bsk = (await session.execute(select(func.count(BskMapPool.beatmap_id)))).scalar() or 0
        try:
            hps = (await session.execute(select(func.count(HpsMapPool.beatmap_id)))).scalar() or 0
        except Exception:
            # hps_map_pool migration may not have run yet on this DB
            hps = 0
    return int(bsk), int(hps)


def _confirm_kb(scope: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"poolwipe:yes:{scope}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="poolwipe:no"),
    ]])


@router.message(TextTriggerFilter("poolwipe"))
async def cmd_poolwipe(message: types.Message) -> None:
    bsk, hps = await _pool_counts()
    await message.answer(
        f"<b>Очистка карт-пулов</b>\n\n"
        f"BSK pool: <b>{bsk}</b> карт\n"
        f"HPS pool: <b>{hps}</b> карт\n\n"
        f"Это удалит ВСЕ записи из обеих таблиц. Активные баунти и дуэли "
        f"продолжат работать — у них собственные снимки метаданных карт.\n\n"
        f"Подтвердить?",
        parse_mode="HTML",
        reply_markup=_confirm_kb("both"),
    )


@router.message(TextTriggerFilter("poolwipebsk"))
async def cmd_poolwipe_bsk(message: types.Message) -> None:
    bsk, _ = await _pool_counts()
    await message.answer(
        f"<b>Очистка BSK pool</b>\n\nУдалить <b>{bsk}</b> карт?",
        parse_mode="HTML",
        reply_markup=_confirm_kb("bsk"),
    )


@router.message(TextTriggerFilter("poolwipehps"))
async def cmd_poolwipe_hps(message: types.Message) -> None:
    _, hps = await _pool_counts()
    await message.answer(
        f"<b>Очистка HPS pool</b>\n\nУдалить <b>{hps}</b> карт?",
        parse_mode="HTML",
        reply_markup=_confirm_kb("hps"),
    )


@router.callback_query(F.data == "poolwipe:no")
async def cb_poolwipe_no(call: types.CallbackQuery) -> None:
    await call.message.edit_text("Отменено.")
    await call.answer()


@router.callback_query(F.data.startswith("poolwipe:yes:"))
async def cb_poolwipe_yes(call: types.CallbackQuery) -> None:
    scope = call.data.split(":", 2)[2]  # "both" | "bsk" | "hps"

    deleted = {"bsk": 0, "hps": 0}
    async with get_db_session() as session:
        if scope in ("both", "bsk"):
            res = await session.execute(delete(BskMapPool))
            deleted["bsk"] = res.rowcount or 0
        if scope in ("both", "hps"):
            try:
                res = await session.execute(delete(HpsMapPool))
                deleted["hps"] = res.rowcount or 0
            except Exception as e:
                logger.warning(f"poolwipe: HPS pool delete failed (likely table missing): {e}")
        await session.commit()

    logger.info(
        f"poolwipe: admin={call.from_user.id} scope={scope} "
        f"deleted_bsk={deleted['bsk']} deleted_hps={deleted['hps']}"
    )
    await call.message.edit_text(
        f"✅ Удалено\nBSK: <b>{deleted['bsk']}</b>\nHPS: <b>{deleted['hps']}</b>",
        parse_mode="HTML",
    )
    await call.answer("Готово")


# ── /poolhealth ─────────────────────────────────────────────────────────────


async def build_poolhealth_report() -> str:
    """Build the BSK pool-health summary text (HTML). Shared by the
    `poolhealth` command handler and the admin panel's execute button."""
    h = await log_pool_health()

    types_str = ", ".join(
        f"{k}=<b>{v}</b>" for k, v in sorted(
            h["type_distribution"].items(), key=lambda kv: -kv[1]
        )
    ) or "—"

    components = {"aim", "speed", "acc", "cons", "mixed"}
    present = set(h["type_distribution"].keys()) & components
    missing_components = sorted(components - present)

    flags: list[str] = []
    if h["enabled"] < 30:
        flags.append(f"⚠ ТОНКИЙ ПУЛ ({h['enabled']} < 30)")
    if h["total"] and h["missing_axis_stars"] / max(h["total"], 1) > 0.3:
        pct = h["missing_axis_stars"] * 100 // max(h["total"], 1)
        flags.append(f"⚠ {pct}% без axis-stars (BSK = SR для них)")
    if h["total"] and h["missing_map_type"] / max(h["total"], 1) > 0.3:
        pct = h["missing_map_type"] * 100 // max(h["total"], 1)
        flags.append(f"⚠ {pct}% без map_type (балансировка слепая)")
    if missing_components:
        flags.append(f"⚠ Нет карт компонента: {', '.join(missing_components)}")

    lines = [
        "<b>BSK Pool Health</b>",
        f"Всего: <b>{h['total']}</b>   Включено: <b>{h['enabled']}</b>",
        f"Без axis-stars: <b>{h['missing_axis_stars']}</b>",
        f"Без map_type: <b>{h['missing_map_type']}</b>",
        f"Без length: <b>{h['missing_length']}</b>",
        "",
        f"<b>Распределение map_type</b> (только enabled):",
        types_str,
    ]
    if flags:
        lines.append("")
        lines.append("<b>Сигналы</b>:")
        lines.extend(escape_html(f) for f in flags)
    else:
        lines.append("")
        lines.append("✅ Пул здоров.")

    return "\n".join(lines)


@router.message(TextTriggerFilter("poolhealth"))
async def cmd_poolhealth(message: types.Message) -> None:
    """Send a one-message summary of BSK pool state. Useful for triaging
    'duels feel weird' reports without SSHing to the box."""
    await message.answer(await build_poolhealth_report(), parse_mode="HTML")
