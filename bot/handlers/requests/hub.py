"""The `reqs` hub and the accept/decline/cancel actions.

Hub sections (Incoming / My tasks / Sent) always render for the tapper, so the
shared group message can't leak one player's requests to another. Accept/decline
verify the tapper is the request's target.
"""

from datetime import datetime, timezone

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, func

from db.database import get_db_session
from db.models.map_request import (
    MapRequest, STATUS_PENDING, STATUS_ACCEPTED, STATUS_DECLINED, STATUS_CANCELLED,
)
from db.models.user import User
from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from services.requests.conditions import parse, describe
from services.requests.progress import request_progress
from services.requests.format import map_label, map_link_html

logger = get_logger("handlers.requests")
router = Router(name="requests_hub")

_LIST_LIMIT = 8


def _label(req: MapRequest) -> str:
    return map_label(req.artist, req.title, req.version, req.beatmap_id)


def _map_html(req: MapRequest) -> str:
    return map_link_html(_label(req), req.beatmap_id, req.beatmapset_id)


def _nav(lang: str, extra: list | None = None) -> list:
    row = list(extra or [])
    row.append(InlineKeyboardButton(text=t("req.kb.back", lang), callback_data="rq:hub"))
    row.append(InlineKeyboardButton(text=t("req.kb.close", lang), callback_data="rq:close"))
    return row


async def _counts(session, uid: int) -> tuple[int, int, int]:
    async def n(*conds):
        return (await session.execute(select(func.count()).select_from(MapRequest).where(*conds))).scalar() or 0
    inbox = await n(MapRequest.target_user_id == uid, MapRequest.status == STATUS_PENDING)
    tasks = await n(MapRequest.target_user_id == uid, MapRequest.status == STATUS_ACCEPTED)
    sent = await n(MapRequest.sender_user_id == uid, MapRequest.status.in_((STATUS_PENDING, STATUS_ACCEPTED)))
    return inbox, tasks, sent


def _home_kb(lang: str, inbox: int, tasks: int, sent: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("req.kb.inbox", lang, n=inbox), callback_data="rq:inbox")],
        [InlineKeyboardButton(text=t("req.kb.tasks", lang, n=tasks), callback_data="rq:tasks")],
        [InlineKeyboardButton(text=t("req.kb.sent", lang, n=sent), callback_data="rq:sent")],
        [InlineKeyboardButton(text=t("req.kb.close", lang), callback_data="rq:close")],
    ])


@router.message(TextTriggerFilter("reqs"))
async def cmd_reqs(message: types.Message, trigger_args=None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower()
    async with get_db_session() as session:
        user = await get_registered_user(session, message.from_user.id, tenant_chat_id)
        if not user:
            await message.reply(t("req.not_registered", lang), parse_mode="HTML")
            return
        inbox, tasks, sent = await _counts(session, user.id)
    await message.answer(t("req.hub.title", lang), reply_markup=_home_kb(lang, inbox, tasks, sent), parse_mode="HTML")


async def _resolve_presser(session, callback, tenant_chat_id):
    return await get_registered_user(session, callback.from_user.id, tenant_chat_id)


@router.callback_query(F.data == "rq:hub")
async def cb_hub(callback: types.CallbackQuery, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    async with get_db_session() as session:
        user = await _resolve_presser(session, callback, tenant_chat_id)
        if not user:
            await callback.answer(t("req.not_registered", lang), show_alert=True)
            return
        inbox, tasks, sent = await _counts(session, user.id)
    try:
        await callback.message.edit_text(t("req.hub.title", lang), reply_markup=_home_kb(lang, inbox, tasks, sent), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "rq:close")
async def cb_close(callback: types.CallbackQuery, tenant_chat_id=None):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "rq:inbox")
async def cb_inbox(callback: types.CallbackQuery, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    async with get_db_session() as session:
        user = await _resolve_presser(session, callback, tenant_chat_id)
        if not user:
            await callback.answer(t("req.not_registered", lang), show_alert=True)
            return
        rows = (await session.execute(
            select(MapRequest).where(
                MapRequest.target_user_id == user.id, MapRequest.status == STATUS_PENDING,
            ).order_by(MapRequest.created_at.desc()).limit(_LIST_LIMIT)
        )).scalars().all()
        senders = await _names(session, [r.sender_user_id for r in rows])
    text = t("req.hub.inbox_title", lang)
    kb: list = []
    if not rows:
        text += t("req.hub.inbox_empty", lang)
    else:
        for r in rows:
            text += t("req.inbox.item", lang, map=_map_html(r),
                      sender=escape_html(senders.get(r.sender_user_id, "?")),
                      conditions=describe(parse(r.conditions), t, lang))
            kb.append([
                InlineKeyboardButton(text=t("req.kb.accept_n", lang, n=r.id), callback_data=f"rq:acc:{r.id}"),
                InlineKeyboardButton(text=t("req.kb.decline_n", lang, n=r.id), callback_data=f"rq:dec:{r.id}"),
            ])
    kb.append(_nav(lang))
    await _edit(callback, text, InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "rq:tasks")
async def cb_tasks(callback: types.CallbackQuery, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    async with get_db_session() as session:
        user = await _resolve_presser(session, callback, tenant_chat_id)
        if not user:
            await callback.answer(t("req.not_registered", lang), show_alert=True)
            return
        rows = (await session.execute(
            select(MapRequest).where(
                MapRequest.target_user_id == user.id, MapRequest.status == STATUS_ACCEPTED,
            ).order_by(MapRequest.responded_at.desc()).limit(_LIST_LIMIT)
        )).scalars().all()
        progresses = {r.id: await request_progress(r, session) for r in rows}
    text = t("req.hub.tasks_title", lang)
    kb: list = []
    if not rows:
        text += t("req.hub.tasks_empty", lang)
    else:
        for r in rows:
            text += t("req.task.item", lang, map=_map_html(r),
                      conditions=describe(parse(r.conditions), t, lang),
                      progress=_progress_line(progresses[r.id], lang))
            kb.append([InlineKeyboardButton(text=t("req.kb.cancel_task_n", lang, n=r.id), callback_data=f"rq:cancel:{r.id}")])
    kb.append(_nav(lang))
    await _edit(callback, text, InlineKeyboardMarkup(inline_keyboard=kb))


@router.callback_query(F.data == "rq:sent")
async def cb_sent(callback: types.CallbackQuery, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    async with get_db_session() as session:
        user = await _resolve_presser(session, callback, tenant_chat_id)
        if not user:
            await callback.answer(t("req.not_registered", lang), show_alert=True)
            return
        rows = (await session.execute(
            select(MapRequest).where(MapRequest.sender_user_id == user.id)
            .order_by(MapRequest.created_at.desc()).limit(_LIST_LIMIT)
        )).scalars().all()
        targets = await _names(session, [r.target_user_id for r in rows])
    text = t("req.hub.sent_title", lang)
    if not rows:
        text += t("req.hub.sent_empty", lang)
    else:
        for r in rows:
            text += t("req.sent.item", lang, map=_map_html(r),
                      target=escape_html(targets.get(r.target_user_id, "?")),
                      status=t(f"req.status.{r.status}", lang))
    await _edit(callback, text, InlineKeyboardMarkup(inline_keyboard=[_nav(lang)]))


# ── accept / decline / cancel ────────────────────────────────────────────

@router.callback_query(F.data.startswith("rq:acc:"))
async def cb_accept(callback: types.CallbackQuery, tenant_chat_id=None):
    await _respond(callback, accept=True)


@router.callback_query(F.data.startswith("rq:dec:"))
async def cb_decline(callback: types.CallbackQuery, tenant_chat_id=None):
    await _respond(callback, accept=False)


async def _respond(callback: types.CallbackQuery, *, accept: bool) -> None:
    lang = (await get_language(callback.from_user.id)).lower()
    try:
        req_id = int(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    async with get_db_session() as session:
        req = await session.get(MapRequest, req_id)
        if not req:
            await callback.answer(t("req.request_gone", lang), show_alert=True)
            return
        # Only the addressed target (in the request's tenant) may respond.
        presser = await get_registered_user(session, callback.from_user.id, req.tenant_chat_id)
        if not presser or presser.id != req.target_user_id:
            await callback.answer(t("req.not_your_request", lang), show_alert=True)
            return
        if req.status != STATUS_PENDING:
            await callback.answer(t("req.already_answered", lang), show_alert=True)
            return
        req.status = STATUS_ACCEPTED if accept else STATUS_DECLINED
        req.responded_at = datetime.now(timezone.utc)
        await session.commit()
        label = _label(req)
    await callback.answer(t("req.accepted_alert" if accept else "req.declined_alert", lang))
    try:
        marker = t("req.status.accepted" if accept else "req.status.declined", lang)
        await callback.message.edit_text(f"<b>{escape_html(label)}</b> — {marker}", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("rq:cancel:"))
async def cb_cancel_task(callback: types.CallbackQuery, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    try:
        req_id = int(callback.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await callback.answer()
        return
    async with get_db_session() as session:
        req = await session.get(MapRequest, req_id)
        if not req:
            await callback.answer(t("req.request_gone", lang), show_alert=True)
            return
        presser = await get_registered_user(session, callback.from_user.id, req.tenant_chat_id)
        if not presser or presser.id != req.target_user_id:
            await callback.answer(t("req.not_your_request", lang), show_alert=True)
            return
        if req.status != STATUS_ACCEPTED:
            await callback.answer(t("req.already_answered", lang), show_alert=True)
            return
        req.status = STATUS_CANCELLED
        await session.commit()
    await callback.answer(t("req.cancelled_alert", lang))
    await cb_tasks(callback, tenant_chat_id=tenant_chat_id)


async def _names(session, user_ids: list[int]) -> dict:
    ids = [i for i in set(user_ids) if i]
    if not ids:
        return {}
    rows = (await session.execute(select(User.id, User.osu_username).where(User.id.in_(ids)))).all()
    return {rid: name for rid, name in rows}


def _progress_line(prog: dict, lang: str) -> str:
    if not prog or prog.get("attempt_count", 0) == 0:
        return t("req.task.no_attempts", lang)
    line = t("req.task.progress", lang, pct=f"{prog['max_completion_pct']:g}", attempts=prog["attempt_count"])
    if prog.get("modal_fail_bucket"):
        line += t("req.task.fails", lang, bucket=prog["modal_fail_bucket"])
    return line


async def _edit(callback: types.CallbackQuery, text: str, kb: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()
