"""My renders section (`st:rnd`): the per-player replay library — browse, view
detail, instant re-send by file_id, delete, and re-render a broken entry.
"""

import json

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html
from db.database import get_db_session
from bot.handlers.dm_tenant import ensure_dm_tenant
from utils.osu.resolve_user import get_registered_user
from bot.handlers.profile.render import (
    get_user_renders, get_user_render, delete_user_render,
    run_guarded_render, render_gate,
)
from bot.handlers.profile.settings_menu.common import _nav_row

logger = get_logger("handlers.settings")
router = Router(name="settings_renders")

_RENDERS_PER_PAGE = 5


def _fmt(v, suffix=""):
    return f"{v}{suffix}" if v not in (None, "", 0) else None


async def _resolve_uid(callback: types.CallbackQuery, tenant_chat_id, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return None
        return user.id


async def _renders_view(uid, page: int = 0, lang: str = "en"):
    rows = await get_user_renders(uid)
    text = t("sts.renders.header", lang)
    kb = []
    if not rows:
        text += t("sts.renders.empty", lang)
    else:
        total_pages = (len(rows) + _RENDERS_PER_PAGE - 1) // _RENDERS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        text += t("sts.total", lang, n=len(rows))
        if total_pages > 1:
            text += t("sts.page_suffix", lang, page=page + 1, total=total_pages)
        text += t("sts.renders.pick", lang)
        start = page * _RENDERS_PER_PAGE
        for r in rows[start:start + _RENDERS_PER_PAGE]:
            kb.append([InlineKeyboardButton(
                text=(r.label or t("sts.renders.fallback_label", lang))[:60], callback_data=f"st:rnd:v:{page}:{r.id}")])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="‹", callback_data=f"st:rnd:pg:{page - 1}"))
            nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="st:rnd:nop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="›", callback_data=f"st:rnd:pg:{page + 1}"))
            kb.append(nav)
    kb.append(_nav_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _show_renders_page(callback: types.CallbackQuery, tenant_chat_id, page: int, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    text, kb = await _renders_view(uid, page, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rnd")
async def cb_renders(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _show_renders_page(callback, tenant_chat_id, 0, lang)


@router.callback_query(F.data == "st:rnd:nop")
async def cb_renders_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:rnd:pg:"))
async def cb_renders_page(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_renders_page(callback, tenant_chat_id, page, lang)


def _render_detail_text(r, lang: str = "en") -> str:
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    head = r.label or t("sts.renders.fallback_label", lang)
    lines = [f"📼 <b>{escape_html(head)}</b>"]
    sub = []
    if meta.get("version"):
        sub.append(f"[{escape_html(str(meta['version']))}]")
    if meta.get("stars"):
        try:
            sub.append(f"★{float(meta['stars']):.2f}")
        except (TypeError, ValueError):
            pass
    if sub:
        lines.append(" ".join(sub))
    lines.append("")
    detail = [
        (t("sts.field.player", lang), _fmt(meta.get("player"))),
        (t("sts.field.mods", lang), _fmt(meta.get("mods"))),
        (t("sts.field.rank", lang), _fmt(meta.get("rank"))),
        (t("sts.field.pp", lang), _fmt(meta.get("pp"))),
        (t("sts.field.accuracy", lang), _fmt(f"{meta['acc']:.2f}", "%") if isinstance(meta.get("acc"), (int, float)) else None),
        (t("sts.field.combo", lang), _fmt(meta.get("combo"), "x")),
        (t("sts.field.misses", lang), _fmt(meta.get("misses"))),
    ]
    for label, val in detail:
        if val is not None:
            lines.append(f"{label}: <b>{escape_html(str(val))}</b>")
    if r.created_at:
        lines.append(t("sts.renders.rendered_at", lang, date=f"{r.created_at:%Y-%m-%d %H:%M}"))
    return "\n".join(lines)


@router.callback_query(F.data.startswith("st:rnd:v:"))
async def cb_render_detail(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    parts = callback.data.split(":", 4)  # st:rnd:v:<page>:<id>
    if len(parts) != 5:
        await callback.answer()
        return
    page = parts[3]
    try:
        render_id = int(parts[4])
    except ValueError:
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r:
        await callback.answer(t("sts.renders.not_found", lang), show_alert=True)
        await _show_renders_page(callback, tenant_chat_id, 0, lang)
        return
    kb = _render_detail_kb(r, page, lang)
    try:
        await callback.message.edit_text(_render_detail_text(r, lang), reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


def _render_detail_kb(r, page, lang: str = "en") -> InlineKeyboardMarkup:
    """A working render's detail screen: send / delete / back. (A BROKEN
    render — stale file_id — gets `_broken_view`'s screen instead, with a
    re-render option in place of "send".)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.send_video", lang), callback_data=f"st:rnd:send:{r.id}")],
        [InlineKeyboardButton(text=t("sts.kb.delete", lang), callback_data=f"st:rnd:del:{r.id}")],
        [
            InlineKeyboardButton(text=t("sts.kb.back_to_list", lang), callback_data=f"st:rnd:pg:{page}"),
            InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
        ],
    ])


def _broken_view(r, lang: str = "en"):
    """A 'broken replay' screen offering delete / re-render (re-render only when we
    can reconstruct the inputs — a score entry with a known beatmapset)."""
    can_rerender = False
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    if str(r.ref).startswith("score:") and meta.get("beatmapset_id"):
        can_rerender = True
    text = (
        t("sts.renders.broken_header", lang)
        + f"<b>{escape_html(r.label or t('sts.renders.fallback_label', lang))}</b>\n"
        + t("sts.renders.broken_body", lang)
    )
    rows = []
    if can_rerender:
        rows.append([InlineKeyboardButton(text=t("sts.kb.rerender", lang), callback_data=f"st:rnd:re:{r.id}")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.delete", lang), callback_data=f"st:rnd:del:{r.id}")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.back_to_list", lang), callback_data="st:rnd:pg:0")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("st:rnd:send:"))
async def cb_render_send(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r:
        await callback.answer(t("sts.renders.not_found", lang), show_alert=True)
        return
    try:
        await callback.message.answer_video(video=r.file_id, supports_streaming=True)
        await callback.answer(t("sts.renders.sent", lang))
    except Exception as e:
        # Stale/broken file_id — surface a choice instead of a dead end.
        logger.info(f"render library re-send failed: {e}")
        await callback.answer(t("sts.renders.unavailable", lang), show_alert=True)
        text, kb = _broken_view(r, lang)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("st:rnd:del:"))
async def cb_render_delete(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    await delete_user_render(uid, render_id)
    await _show_renders_page(callback, tenant_chat_id, 0, lang)
    await callback.answer(t("sts.renders.deleted", lang))


@router.callback_query(F.data.startswith("st:rnd:re:"))
async def cb_render_rerender(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r or not str(r.ref).startswith("score:"):
        await callback.answer(t("sts.renders.rerender_unavailable", lang), show_alert=True)
        return
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    beatmapset_id = meta.get("beatmapset_id")
    if not beatmapset_id:
        await callback.answer(t("sts.renders.rerender_missing_data", lang), show_alert=True)
        return
    try:
        score_id = int(str(r.ref).split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    tg_id = callback.from_user.id
    gate = render_gate(tg_id)
    if gate == "busy":
        await callback.answer(t("render.busy", lang), show_alert=True)
        return
    if gate and gate.startswith("cooldown:"):
        await callback.answer(t("render.cooldown_short", lang, sec=gate.split(':')[1]), show_alert=True)
        return

    await callback.answer(t("sts.renders.rerender_started", lang))
    await run_guarded_render(
        callback.message, score_id=score_id, beatmapset_id=beatmapset_id,
        display_name=meta.get("player") or "", length_seconds=meta.get("length"),
        meta=meta, tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client,
    )
