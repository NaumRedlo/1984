from aiogram import Router, types
from aiogram.types import BufferedInputFile

from db.database import get_db_session
from services.image_generator import card_renderer
from utils.logger import get_logger
from utils.text_utils import escape_html
from utils.resolve_user import resolve_osu_user, get_registered_user
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="compare")
logger = get_logger("handlers.compare")


def _format_play_time(seconds) -> str:
    if not seconds or int(seconds) <= 0:
        return "—"
    s = int(seconds)
    days = s // 86400
    hours = (s % 86400) // 3600
    return f"{days}d {hours}h"


@router.message(TextTriggerFilter("compare"))
async def compare_users(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    target_username = trigger_args.args
    if not target_username:
        await message.answer(
            "Использование: <code>compare &lt;никнейм или id&gt;</code>\n"
            "Примеры: <code>compare Cookiezi</code> или <code>compare id:12345</code>",
            parse_mode="HTML"
        )
        return

    target_username = target_username.strip()
    tg_id = message.from_user.id

    async with get_db_session() as session:
        try:
            user1 = await get_registered_user(session, tg_id)

            if not user1:
                await message.answer("Сначала зарегистрируйтесь! Используйте register")
                return

            wait_msg = await message.answer("Загрузка данных...")

            target_data = await resolve_osu_user(osu_api_client, target_username)

            if not target_data:
                await wait_msg.edit_text(f"Пользователь <b>{escape_html(target_username)}</b> не найден!", parse_mode="HTML")
                return

            # Fetch fresh avatar/cover for user1 if missing in DB
            u1_avatar = user1.avatar_url
            u1_cover = user1.cover_url
            if not u1_avatar or not u1_cover:
                fresh = await osu_api_client.get_user_data(user1.osu_user_id)
                if fresh:
                    u1_avatar = fresh.get("avatar_url") or u1_avatar
                    u1_cover = fresh.get("cover_url") or u1_cover

            pp_diff = (user1.player_pp or 0) - (target_data['pp'] or 0)
            rank_diff = (user1.global_rank or 0) - (target_data['global_rank'] or 0) if target_data['global_rank'] and user1.global_rank else 0
            acc_diff = (user1.accuracy or 0.0) - (target_data['accuracy'] or 0.0)
            pc_diff = (user1.play_count or 0) - (target_data["play_count"] or 0)
            pt_diff = (user1.play_time or 0) - (target_data.get("play_time") or 0)
            rs_diff = (user1.ranked_score or 0) - (target_data.get("ranked_score") or 0)

            compare_text = (
                f"<b>Сравнение: {user1.osu_username} vs {target_data['username']}</b>\n"
                f"{'═' * 40}\n\n"
                f"<b>PP:</b>\n"
                f"  • Вы: <code>{user1.player_pp:,}</code> ({_format_diff(pp_diff)} PP)\n"
                f"  • Оппонент: <code>{target_data['pp']:,}</code>\n\n"
                f"<b>Глобальный ранг:</b>\n"
                f"  • Вы: <code>#{user1.global_rank:,}</code> ({_format_diff(rank_diff)} позиций)\n"
                f"  • Оппонент: <code>#{target_data['global_rank']:,}</code>\n\n"
                f"<b>Точность:</b>\n"
                f"  • Вы: <code>{user1.accuracy:.2f}%</code> ({_format_diff(acc_diff, suffix='%')})\n"
                f"  • Оппонент: <code>{target_data['accuracy']:.2f}%</code>\n\n"
                f"<b>Количество игр:</b>\n"
                f"  • Вы: <code>{user1.play_count:,}</code>\n"
                f"  • Оппонент: <code>{target_data['play_count']:,}</code>"
            )

            # Try PNG card, fallback to text
            try:
                compare_data = {
                    "user1": {
                        "username": user1.osu_username,
                        "pp": user1.player_pp or 0,
                        "rank": user1.global_rank or 0,
                        "accuracy": user1.accuracy or 0.0,
                        "play_count": user1.play_count or 0,
                        "play_time": _format_play_time(user1.play_time),
                        "ranked_score": user1.ranked_score or 0,
                        "avatar_url": u1_avatar,
                        "cover_url": u1_cover,
                    },
                    "user2": {
                        "username": target_data["username"],
                        "pp": target_data["pp"] or 0,
                        "rank": target_data["global_rank"] or 0,
                        "accuracy": target_data["accuracy"] or 0.0,
                        "play_count": target_data["play_count"] or 0,
                        "play_time": _format_play_time(target_data.get("play_time")),
                        "ranked_score": target_data.get("ranked_score") or 0,
                        "avatar_url": target_data.get("avatar_url"),
                        "cover_url": target_data.get("cover_url"),
                    },
                    "diffs": {
                        "pp": pp_diff,
                        "rank": rank_diff,
                        "accuracy": acc_diff,
                        "play_count": pc_diff,
                        "play_time": pt_diff,
                        "ranked_score": rs_diff,
                    },
                }
                buf = await card_renderer.generate_compare_card_async(compare_data)
                photo = BufferedInputFile(buf.read(), filename="compare.png")
                await wait_msg.delete()
                await message.answer_photo(photo=photo)
            except Exception as img_err:
                logger.warning(f"Compare card generation failed: {img_err}")
                await wait_msg.edit_text(compare_text, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error in /compare: {e}", exc_info=True)
            await message.answer("Произошла ошибка при сравнении.")


def _format_diff(value: float, suffix: str = '') -> str:
    if value == 0:
        return "±0" + suffix

    symbol = "+" if value > 0 else ""
    emoji = "🟢" if value > 0 else "🔴"

    return f"{emoji} {symbol}{value:,.2f}{suffix}"

__all__ = ["router"]
