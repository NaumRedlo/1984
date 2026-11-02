import aiohttp
from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.utils.safe_edit import safe_edit_media
from db.database import get_db_session
from services.image import card_renderer
from services.teams import roster_page, team_card_data
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import rulesets
from utils.osu.resolve_user import get_registered_user, get_reply_target_user
from utils.ttl_cache import TTLCache

logger = get_logger("handlers.team")
router = Router(name="team")

_teams = TTLCache(maxsize=64, ttl=300)


async def _load(client, wanted, mode: str):
    key = (str(wanted).lower(), mode)
    kept = _teams.get(key)
    if kept is not None:
        return kept
    team = await client.get_team(wanted, mode)
    if team is None:
        return None
    ids = [(team.get("leader") or {}).get("id")] + [m.get("id") for m in team.get("members") or []]
    loaded = (team, await client.get_users_statistics(ids, mode))
    _teams[key] = loaded
    _teams[(str(team["id"]), mode)] = loaded
    return loaded


def _card_keyboard(team_id: int, ruleset: int, count: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=t("team.kb.members", lang, n=count), callback_data=f"tmm:{team_id}:{ruleset}:0")]])


def _roster_keyboard(team_id: int, ruleset: int, page: int, pages: int, lang: str) -> InlineKeyboardMarkup:
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"tmm:{team_id}:{ruleset}:{page - 1}"))
    if pages > 1:
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=f"tmm:{team_id}:{ruleset}:{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"tmm:{team_id}:{ruleset}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton(text=t("team.kb.back", lang), callback_data=f"tmc:{team_id}:{ruleset}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _team_photo(client, wanted, ruleset: int, chat_id, lang: str):
    loaded = await _load(client, wanted, rulesets.RULESETS.get(ruleset, "osu"))
    if loaded is None:
        return None
    team, statistics = loaded
    async with get_db_session() as session:
        data = await team_card_data(session, team, chat_id, lang, statistics)
    photo = await card_renderer.generate_team_card_async(data)
    keyboard = _card_keyboard(team["id"], ruleset, data["team"]["member_count"], lang)
    return BufferedInputFile(photo.read(), filename="team.png"), keyboard


async def _roster_photo(client, team_id: int, ruleset: int, page: int, chat_id, lang: str):
    loaded = await _load(client, team_id, rulesets.RULESETS.get(ruleset, "osu"))
    if loaded is None:
        return None
    team, statistics = loaded
    async with get_db_session() as session:
        data = await team_card_data(session, team, chat_id, lang, statistics)
    shown = roster_page(data, page)
    photo = await card_renderer.generate_team_roster_async(shown)
    keyboard = _roster_keyboard(team["id"], ruleset, shown["page"], shown["pages"], lang)
    return BufferedInputFile(photo.read(), filename="team_members.png"), keyboard


async def _send_team(message, osu_api_client, tenant_chat_id, raw: str, *, own_only: bool):
    lang = (await get_language(message.from_user.id)).lower()
    ruleset, rest = await rulesets.chosen(raw, message.from_user.id)
    ruleset = ruleset or 0
    query = "" if own_only else (rest or "").strip()
    try:
        if query.startswith("@") and len(query) > 1:
            wanted = query[1:]
        elif query:
            try:
                player = await osu_api_client.get_user_data(query)
            except aiohttp.ClientResponseError as missing:
                if missing.status != 404:
                    raise
                player = None
            if player is None:
                await message.answer(t("team.no_player", lang, name=escape_html(query)))
                return
            wanted = (player.get("team") or {}).get("id")
            if wanted is None:
                await message.answer(t("team.player_none", lang, name=escape_html(player.get("username") or query)))
                return
        else:
            async with get_db_session() as session:
                target = None if own_only else await get_reply_target_user(session, message, chat_id=tenant_chat_id)
                viewer = target or await get_registered_user(session, message.from_user.id, tenant_chat_id)
            if viewer is None:
                await message.answer(t("team.not_linked", lang))
                return
            wanted = viewer.team_id
            if wanted is None and viewer.osu_user_id:
                fresh = await osu_api_client.get_user_data(viewer.osu_user_id)
                wanted = ((fresh or {}).get("team") or {}).get("id")
            if wanted is None:
                if target is not None:
                    await message.answer(t("team.player_none", lang, name=escape_html(target.osu_username or "")))
                else:
                    await message.answer(t("team.none", lang))
                return
        made = await _team_photo(osu_api_client, wanted, ruleset, tenant_chat_id, lang)
        if made is None:
            await message.answer(t("team.not_found", lang, query=escape_html(str(query or wanted))))
            return
        photo, keyboard = made
        await message.answer_photo(photo, reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"team card failed for {raw!r}: {exc}", exc_info=True)
        await message.answer(t("team.error", lang))


@router.message(TextTriggerFilter("mt"))
async def cmd_my_team(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None,
                      tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    await _send_team(message, osu_api_client, tenant_chat_id,
                     ((trigger_args.args if trigger_args else "") or "").strip(), own_only=True)


@router.message(TextTriggerFilter("tm"))
async def cmd_team(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None,
                   tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    await _send_team(message, osu_api_client, tenant_chat_id,
                     ((trigger_args.args if trigger_args else "") or "").strip(), own_only=False)


def _parsed(data: str, size: int):
    try:
        parts = [int(value) for value in data.split(":")[1:]]
    except ValueError:
        return None
    if len(parts) != size or parts[1] not in rulesets.RULESETS:
        return None
    return parts


@router.callback_query(F.data.startswith("tmm:"))
async def team_members(callback: CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    parts = _parsed(callback.data, 3)
    if parts is None:
        await callback.answer()
        return
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    team_id, ruleset, page = parts
    lang = (await get_language(callback.from_user.id)).lower()
    await callback.answer()
    try:
        made = await _roster_photo(osu_api_client, team_id, ruleset, max(0, page), tenant_chat_id, lang)
        if made is not None:
            photo, keyboard = made
            await safe_edit_media(callback.message, media=InputMediaPhoto(media=photo), reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"team {team_id} members page {page} failed: {exc}", exc_info=True)


@router.callback_query(F.data.startswith("tmc:"))
async def team_back(callback: CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    parts = _parsed(callback.data, 2)
    if parts is None:
        await callback.answer()
        return
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    team_id, ruleset = parts
    lang = (await get_language(callback.from_user.id)).lower()
    await callback.answer()
    try:
        made = await _team_photo(osu_api_client, team_id, ruleset, tenant_chat_id, lang)
        if made is not None:
            photo, keyboard = made
            await safe_edit_media(callback.message, media=InputMediaPhoto(media=photo), reply_markup=keyboard)
    except Exception as exc:
        logger.error(f"team {team_id} card failed: {exc}", exc_info=True)


__all__ = ["router"]
