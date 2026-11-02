from aiogram import Router, types
from aiogram.types import BufferedInputFile

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant
from db.database import get_db_session
from services.image import card_renderer
from services.teams import team_card_data
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import rulesets
from utils.osu.resolve_user import get_registered_user

logger = get_logger("handlers.team")
router = Router(name="team")


@router.message(TextTriggerFilter("team", "tm"))
async def cmd_team(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None,
                   tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower()
    raw = ((trigger_args.args if trigger_args else "") or "").strip()
    ruleset, rest = await rulesets.chosen(raw, message.from_user.id)
    query = (rest or "").strip()
    mode = rulesets.RULESETS.get(ruleset or 0, "osu")
    try:
        async with get_db_session() as session:
            if query:
                wanted = int(query) if query.isdigit() else query
            else:
                viewer = await get_registered_user(session, message.from_user.id, tenant_chat_id)
                if viewer is None:
                    await message.answer(t("team.not_linked", lang))
                    return
                wanted = viewer.team_id
                if wanted is None and viewer.osu_user_id:
                    fresh = await osu_api_client.get_user_data(viewer.osu_user_id)
                    wanted = ((fresh or {}).get("team") or {}).get("id")
                if wanted is None:
                    await message.answer(t("team.none", lang))
                    return
            team = await osu_api_client.get_team(wanted, mode)
            if team is None:
                await message.answer(t("team.not_found", lang, query=escape_html(str(query or wanted))))
                return
            ids = [(team.get("leader") or {}).get("id")] + [m.get("id") for m in team.get("members") or []]
            statistics = await osu_api_client.get_users_statistics(ids, mode)
            data = await team_card_data(session, team, tenant_chat_id, lang, statistics)
        photo = await card_renderer.generate_team_card_async(data)
        await message.answer_photo(BufferedInputFile(photo.read(), filename="team.png"))
    except Exception as exc:
        logger.error(f"team card failed for {raw!r}: {exc}", exc_info=True)
        await message.answer(t("team.error", lang))


__all__ = ["router"]
