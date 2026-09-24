import re

from aiogram import Router, types
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.profile.targets import resolve_target, token_for
from db.database import get_db_session
from services.image import card_renderer
from services.image.render.recent import build_recent_card_data
from utils.formatting.text import escape_html, format_error
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import rulesets
from utils.osu.helpers import remember_message_context
from utils.osu.resolve_user import get_registered_user_by_osu

logger = get_logger("handlers.top_play")
router = Router(name="top_play")

_PLACE = re.compile(r"^[\\#]?(\d{1,3})$")


def split_place(text: str) -> tuple[int | None, str]:
    """The place asked for ("3", "\\3" or "#3", first or last word) and the rest of the line."""
    words = (text or "").split()
    for i in (0, len(words) - 1) if words else ():
        found = _PLACE.match(words[i])
        if found:
            return int(found.group(1)), " ".join(words[:i] + words[i + 1:])
    return None, " ".join(words)


@router.message(TextTriggerFilter("tp"))
async def cmd_top_play(message: types.Message, trigger_args: TriggerArgs, osu_api_client, tenant_chat_id=None):
    lang = (await get_language(message.from_user.id)).lower()
    if not osu_api_client:
        await message.answer(t("common.api_not_ready", lang))
        return

    ruleset, rest = rulesets.split_args(trigger_args.args or "")
    place, query = split_place(rest)
    place = place or 1
    if not 1 <= place <= 100:
        await message.answer(format_error(t("tp.bad_place", lang), lang), parse_mode="HTML")
        return

    target = await resolve_target(message, query, tenant_chat_id, osu_api_client, lang)
    if not target:
        return
    wait = await message.answer(t("tp.loading", lang, n=place, name=escape_html(target.name)), parse_mode="HTML")
    try:
        # with no mode named, osu! answers with the player's main one
        scores = await osu_api_client.get_user_best_scores(
            target.osu_id, limit=place, oauth_token=await token_for(target),
            mode=rulesets.RULESETS[ruleset] if ruleset is not None else None)
        if len(scores) < place:
            mode = rulesets.TITLES.get(ruleset if ruleset is not None else 0)
            await wait.edit_text(t("tp.no_such_play", lang, n=place, name=escape_html(target.name), mode=mode),
                                 parse_mode="HTML")
            return
        score = scores[place - 1]

        cover = ""
        async with get_db_session() as session:
            registered = await get_registered_user_by_osu(session, tenant_chat_id, osu_user_id=target.osu_id)
            cover = (registered.cover_url if registered else "") or ""
        if not cover:
            found = await osu_api_client.get_user_data(target.osu_id)
            cover = (found or {}).get("cover_url") or ""

        data = await build_recent_card_data(
            score, username=target.name, player_id=target.osu_id, player_cover_url=cover,
            requester_name=message.from_user.first_name or message.from_user.username or "???",
            lang=lang, card_mode="top", client=osu_api_client,
        )
        data["top_place"] = place
        buf = await card_renderer.generate_recent_card_async(data)

        played_in = data.get("ruleset") or 0
        beatmap = score.get("beatmap") or {}
        url = (f"https://osu.ppy.sh/beatmapsets/{(score.get('beatmapset') or {}).get('id', 0)}"
               f"#{rulesets.RULESETS[played_in]}/{beatmap.get('id', 0)}")
        buttons = [InlineKeyboardButton(text=t("common.kb.beatmap", lang), url=url)]
        if beatmap.get("id") and played_in == 0:
            buttons.append(InlineKeyboardButton(text=t("common.kb.leaderboard", lang),
                                                callback_data=f"lbm:{beatmap['id']}"))
        await wait.delete()
        sent = await message.answer_photo(photo=BufferedInputFile(buf.read(), filename="top.png"),
                                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))
        remember_message_context(sent.chat.id, sent.message_id, data)
    except Exception as exc:
        logger.error(f"top play {place} of {target.osu_id} failed: {exc}", exc_info=True)
        await wait.edit_text(format_error(t("rs.fetch_failed", lang), lang), parse_mode="HTML")


__all__ = ["router", "split_place"]
