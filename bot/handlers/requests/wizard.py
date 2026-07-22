"""The `req` wizard: challenge another player to pass a map on set conditions.

FSM flow: pick target (reply or osu! username) → paste map → tune conditions on
an inline menu → send. On send a MapRequest(status=pending) is created and the
target is notified with accept/decline buttons.
"""

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.map_request import MapRequest, OPEN_STATUSES
from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.language import get_language
from utils.osu.beatmap_link import extract_beatmap_ref
from utils.osu.resolve_user import (
    get_registered_user, get_reply_target_user, get_registered_user_by_osu,
)
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.maplink.resolve import _resolve_card
from services.requests.conditions import (
    default_conditions, serialize, describe, parse_mods, format_mods,
)
from services.requests.format import map_label
from services.requests.notify import notify_new_request

logger = get_logger("handlers.requests")
router = Router(name="requests_wizard")


class RequestWizard(StatesGroup):
    waiting_target = State()
    waiting_map = State()
    setting_conditions = State()
    custom_input = State()      # typing a custom acc / combo / mods value


_ACC_CYCLE = [None, 90.0, 95.0, 97.0, 99.0]
_MODS_CYCLE = [None, "HD", "HR", "DT", "HDDT", "HDHR"]
_RANK_CYCLE = [None, "S", "SS"]
# Combo requirement, merged FC + min-combo. Fractions are of the map's max combo;
# "FC" = full combo. Only off/FC are offered when the map's max combo is unknown.
_COMBO_PCTS = [0.50, 0.75, 0.90, 0.95]


def _combo_cycle(map_max):
    steps = [("off", None)]
    if map_max:
        steps += [(f"{int(p * 100)}%", p) for p in _COMBO_PCTS]
    steps.append(("FC", "FC"))
    return steps


def _apply_combo(cond: dict, choice, map_max) -> None:
    """Translate a combo cycle choice into (require_fc, min_combo)."""
    if choice == "FC":
        cond["require_fc"], cond["min_combo"] = True, None
    elif choice is None:
        cond["require_fc"], cond["min_combo"] = False, None
    else:
        cond["require_fc"] = False
        cond["min_combo"] = round(map_max * choice) if map_max else None


def _combo_label(cond: dict, lang: str) -> str:
    if cond.get("require_fc"):
        return "FC"
    if cond.get("min_combo"):
        return f"≥{cond['min_combo']}"
    return t("req.val.off", lang)


def _next(cycle, current):
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


def _mark(on: bool) -> str:
    return "✅" if on else "❌"


def _cond_kb(data: dict, lang: str) -> InlineKeyboardMarkup:
    cond = data["conditions"]
    off = t("req.val.off", lang)
    acc = f"{cond['min_accuracy']:g}%" if cond.get("min_accuracy") is not None else off
    mods = cond.get("mods") or off
    rank = cond.get("min_rank") or off
    edit = t("req.kb.edit", lang)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("req.kb.pass", lang, mark=_mark(cond.get("pass", True))), callback_data="rq:c:pass")],
        [
            InlineKeyboardButton(text=t("req.kb.acc", lang, value=acc), callback_data="rq:c:acc"),
            InlineKeyboardButton(text=edit, callback_data="rq:c:acc_edit"),
        ],
        [
            InlineKeyboardButton(text=t("req.kb.combo", lang, value=_combo_label(cond, lang)), callback_data="rq:c:combo"),
            InlineKeyboardButton(text=edit, callback_data="rq:c:combo_edit"),
        ],
        [
            InlineKeyboardButton(text=t("req.kb.mods", lang, value=mods), callback_data="rq:c:mods"),
            InlineKeyboardButton(text=edit, callback_data="rq:c:mods_edit"),
        ],
        [InlineKeyboardButton(text=t("req.kb.rank", lang, value=rank), callback_data="rq:c:rank")],
        [
            InlineKeyboardButton(text=t("req.kb.send", lang), callback_data="rq:c:send"),
            InlineKeyboardButton(text=t("req.kb.cancel", lang), callback_data="rq:c:cancel"),
        ],
    ])


def _back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("req.kb.back", lang), callback_data="rq:c:back")],
    ])


def _parse_acc(text: str):
    """Parse a custom accuracy. Returns (value, None) or (None, error_key)."""
    try:
        v = float(text.replace(",", ".").rstrip("%"))
    except ValueError:
        return None, "req.custom.bad_number"
    if not (0.0 <= v <= 100.0):
        return None, "req.custom.bad_acc"
    return (int(v) if v == int(v) else round(v, 2)), None


def _menu_text(data: dict, lang: str) -> str:
    cond = data["conditions"]
    return t(
        "req.wizard.menu", lang,
        target=escape_html(data["target_name"]),
        map=escape_html(data["map_label"]),
        conditions=describe(cond, t, lang),
    )


async def _show_conditions(message: types.Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    await state.set_state(RequestWizard.setting_conditions)
    await message.answer(_menu_text(data, lang), reply_markup=_cond_kb(data, lang), parse_mode="HTML")


@router.message(TextTriggerFilter("req"))
async def cmd_req(message: types.Message, trigger_args=None, osu_api_client=None, tenant_chat_id=None, state: FSMContext = None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower()
    async with get_db_session() as session:
        sender = await get_registered_user(session, message.from_user.id, tenant_chat_id)
        if not sender:
            await message.reply(t("req.not_registered", lang), parse_mode="HTML")
            return
        # Reply-to targets the replied player directly; otherwise ask.
        target = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
        sender_id, sender_name = sender.id, sender.osu_username
        target_snapshot = None
        if target:
            if target.id == sender.id:
                await message.reply(t("req.wizard.target_self", lang))
                return
            target_snapshot = (target.id, target.telegram_id, target.osu_username)

    await state.set_data({
        "tenant_chat_id": tenant_chat_id,
        "sender_user_id": sender_id,
        "sender_name": sender_name,
    })
    if target_snapshot:
        await state.update_data(
            target_user_id=target_snapshot[0], target_tg_id=target_snapshot[1],
            target_name=target_snapshot[2],
        )
        await state.set_state(RequestWizard.waiting_map)
        await message.reply(t("req.wizard.ask_map", lang, target=escape_html(target_snapshot[2])), parse_mode="HTML")
    else:
        await state.set_state(RequestWizard.waiting_target)
        key = "req.wizard.ask_target_dm" if message.chat.type == "private" else "req.wizard.ask_target"
        await message.reply(t(key, lang), parse_mode="HTML")


@router.message(RequestWizard.waiting_target)
async def wiz_target(message: types.Message, tenant_chat_id=None, state: FSMContext = None):
    lang = (await get_language(message.from_user.id)).lower()
    query = (message.text or "").strip().lstrip("@")
    data = await state.get_data()
    async with get_db_session() as session:
        target = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
        if not target and query:
            target = await get_registered_user_by_osu(session, tenant_chat_id, osu_username=query)
        if not target:
            await message.reply(t("req.wizard.target_not_found", lang), parse_mode="HTML")
            return
        if target.id == data["sender_user_id"]:
            await message.reply(t("req.wizard.target_self", lang))
            return
        tid, ttg, tname = target.id, target.telegram_id, target.osu_username
    await state.update_data(target_user_id=tid, target_tg_id=ttg, target_name=tname)
    await state.set_state(RequestWizard.waiting_map)
    await message.reply(t("req.wizard.ask_map", lang, target=escape_html(tname)), parse_mode="HTML")


@router.message(RequestWizard.waiting_map)
async def wiz_map(message: types.Message, osu_api_client=None, state: FSMContext = None):
    lang = (await get_language(message.from_user.id)).lower()
    ref = extract_beatmap_ref(message.text or "")
    if ref is None and (message.text or "").strip().isdigit():
        from utils.osu.beatmap_link import BeatmapRef
        ref = BeatmapRef(int(message.text.strip()), None, None)
    card = await _resolve_card(ref, osu_api_client) if ref else None
    if not card or not card.get("beatmap_id"):
        await message.reply(t("req.wizard.map_not_found", lang), parse_mode="HTML")
        return
    await state.update_data(
        beatmap_id=card["beatmap_id"], beatmapset_id=card.get("beatmapset_id"),
        artist=card.get("artist"), title=card.get("title"), version=card.get("version"),
        star_rating=card.get("star_rating"), map_max_combo=card.get("max_combo"),
        bpm=card.get("bpm"), length=card.get("length"),
        map_label=map_label(card.get("artist"), card.get("title"), card.get("version"), card["beatmap_id"]),
        conditions=default_conditions(), combo_idx=0,
    )
    await _show_conditions(message, state, lang)


@router.callback_query(RequestWizard.setting_conditions, F.data.startswith("rq:c:"))
async def wiz_conditions(callback: types.CallbackQuery, state: FSMContext = None):
    lang = (await get_language(callback.from_user.id)).lower()
    action = callback.data.split(":", 2)[2]
    data = await state.get_data()
    cond = data["conditions"]

    if action == "cancel":
        await state.clear()
        try:
            await callback.message.edit_text(t("req.wizard.cancelled", lang))
        except Exception:
            pass
        await callback.answer()
        return

    if action == "send":
        await _create_request(callback, data, lang)
        await state.clear()
        return

    if action in ("acc_edit", "combo_edit", "mods_edit"):
        field = action.split("_", 1)[0]
        await state.update_data(custom_field=field,
                                menu_chat_id=callback.message.chat.id,
                                menu_message_id=callback.message.message_id)
        await state.set_state(RequestWizard.custom_input)
        try:
            await callback.message.edit_text(t(f"req.custom.{field}", lang),
                                             reply_markup=_back_kb(lang), parse_mode="HTML")
        except Exception:
            pass
        await callback.answer()
        return

    extra = {}
    if action == "pass":
        cond["pass"] = not cond.get("pass", True)
    elif action == "acc":
        cond["min_accuracy"] = _next(_ACC_CYCLE, cond.get("min_accuracy"))
    elif action == "combo":
        cycle = _combo_cycle(data.get("map_max_combo"))
        idx = (int(data.get("combo_idx", 0)) + 1) % len(cycle)
        _apply_combo(cond, cycle[idx][1], data.get("map_max_combo"))
        extra["combo_idx"] = idx
    elif action == "mods":
        cond["mods"] = _next(_MODS_CYCLE, cond.get("mods"))
    elif action == "rank":
        cond["min_rank"] = _next(_RANK_CYCLE, cond.get("min_rank"))

    new_data = {**data, "conditions": cond, **extra}
    await state.update_data(conditions=cond, **extra)
    try:
        await callback.message.edit_text(_menu_text(new_data, lang),
                                         reply_markup=_cond_kb(new_data, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(RequestWizard.custom_input, F.data == "rq:c:back")
async def wiz_custom_back(callback: types.CallbackQuery, state: FSMContext = None):
    lang = (await get_language(callback.from_user.id)).lower()
    data = await state.get_data()
    await state.set_state(RequestWizard.setting_conditions)
    try:
        await callback.message.edit_text(_menu_text(data, lang), reply_markup=_cond_kb(data, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.message(RequestWizard.custom_input)
async def wiz_custom_input(message: types.Message, state: FSMContext = None):
    lang = (await get_language(message.from_user.id)).lower()
    data = await state.get_data()
    field = data.get("custom_field")
    cond = dict(data["conditions"])
    text = (message.text or "").strip()
    updates: dict = {}

    if field == "acc":
        val, err = _parse_acc(text)
        if err:
            await message.reply(t(err, lang))
            return
        cond["min_accuracy"] = val
        updates["conditions"] = cond
    elif field == "combo":
        if not text.lstrip("+").isdigit():
            await message.reply(t("req.custom.bad_number", lang))
            return
        cond["min_combo"], cond["require_fc"] = int(text), False
        updates["conditions"] = cond
    elif field == "mods":
        cond["mods"] = format_mods(parse_mods(text)) or None
        updates["conditions"] = cond
    else:
        await state.set_state(RequestWizard.setting_conditions)
        return

    await state.update_data(**updates)
    await state.set_state(RequestWizard.setting_conditions)
    new_data = {**data, **updates}
    try:
        await message.bot.edit_message_text(
            _menu_text(new_data, lang), chat_id=data["menu_chat_id"],
            message_id=data["menu_message_id"], reply_markup=_cond_kb(new_data, lang),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _create_request(callback: types.CallbackQuery, data: dict, lang: str) -> None:
    async with get_db_session() as session:
        # Guard against a duplicate active request for the same (sender, target, map).
        dup = (await session.execute(
            select(MapRequest).where(
                MapRequest.sender_user_id == data["sender_user_id"],
                MapRequest.target_user_id == data["target_user_id"],
                MapRequest.beatmap_id == data["beatmap_id"],
                MapRequest.status.in_(OPEN_STATUSES),
            )
        )).scalar_one_or_none()
        if dup:
            await callback.answer(t("req.wizard.dup", lang), show_alert=True)
            return
        req = MapRequest(
            tenant_chat_id=data["tenant_chat_id"],
            sender_user_id=data["sender_user_id"],
            target_user_id=data["target_user_id"],
            beatmap_id=data["beatmap_id"],
            beatmapset_id=data.get("beatmapset_id"),
            artist=data.get("artist"), title=data.get("title"),
            version=data.get("version"), star_rating=data.get("star_rating"),
            bpm=data.get("bpm"), length=data.get("length"),
            map_max_combo=data.get("map_max_combo"),
            conditions=serialize(data["conditions"]),
        )
        session.add(req)
        await session.commit()
        req_id = req.id

    try:
        await callback.message.edit_text(
            t("req.wizard.sent", lang, target=escape_html(data["target_name"])), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()
    # Render + deliver the card to the target (in their language) — see notify.
    await notify_new_request(req_id)
