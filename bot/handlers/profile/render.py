import time
from typing import Optional, Dict

from aiogram import Router, types
from sqlalchemy import select

from db.database import get_db_session
from db.models.user import User
from db.models.render_settings import UserRenderSettings
from config.settings import ORDR_API_KEY
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import resolve_osu_user, get_registered_user
from utils.osu.helpers import get_message_context
from utils.osu import ordr_client
from bot.filters import TextTriggerFilter, TriggerArgs

logger = get_logger("handlers.render")
router = Router(name="render")

# Cooldown: tg_id -> last render timestamp
_cooldowns: Dict[int, float] = {}
COOLDOWN_SECONDS = 60

# Max video size for Telegram bot API (50 MB)
MAX_VIDEO_BYTES = 50 * 1024 * 1024


def _check_cooldown(tg_id: int) -> Optional[int]:
    """Returns remaining cooldown seconds, or None if ready."""
    last = _cooldowns.get(tg_id)
    if last is None:
        return None
    elapsed = time.time() - last
    if elapsed >= COOLDOWN_SECONDS:
        return None
    return int(COOLDOWN_SECONDS - elapsed)


async def _get_or_create_settings(session, user_id: int) -> UserRenderSettings:
    """Get user render settings from DB, or return defaults."""
    stmt = select(UserRenderSettings).where(UserRenderSettings.user_id == user_id)
    result = await session.execute(stmt)
    settings = result.scalar_one_or_none()
    if settings:
        return settings
    settings = UserRenderSettings(user_id=user_id)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


def _settings_to_ordr_kwargs(settings: UserRenderSettings) -> dict:
    """Convert DB settings to ordr_client.submit_render kwargs."""
    return {
        "skin": settings.skin,
        "resolution": settings.resolution,
        "cursor_size": settings.cursor_size,
        "cursor_trail": settings.cursor_trail,
        "show_pp_counter": settings.show_pp_counter,
        "show_scoreboard": settings.show_scoreboard,
        "show_key_overlay": settings.show_key_overlay,
        "show_hit_error_meter": settings.show_hit_error_meter,
        "show_mods": settings.show_mods,
        "show_result_screen": settings.show_result_screen,
        "bg_dim": settings.bg_dim,
    }


# ── render ──

@router.message(TextTriggerFilter("render"))
async def cmd_render(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id

    # Cooldown check
    remaining = _check_cooldown(tg_id)
    if remaining:
        await message.answer(f"Подождите <b>{remaining} сек.</b> перед следующим рендером.", parse_mode="HTML")
        return

    user_input = (trigger_args.args or "").strip() if trigger_args else ""
    score_id = None
    display_name = ""

    if user_input:
        # Resolve user, fetch their latest score
        wait_msg = await message.answer(f"Поиск игрока <b>{escape_html(user_input)}</b>...", parse_mode="HTML")
        try:
            user_data = await resolve_osu_user(osu_api_client, user_input)
            if not user_data:
                await wait_msg.edit_text(f"Игрок <b>{escape_html(user_input)}</b> не найден.", parse_mode="HTML")
                return
            target_id = user_data.get("id")
            display_name = user_data.get("username", user_input)
            recent = await osu_api_client.get_user_recent_scores(target_id, limit=1)
            if not recent:
                await wait_msg.edit_text(f"У <b>{escape_html(display_name)}</b> нет недавних игр.", parse_mode="HTML")
                return
            score_id = recent[0].get("id")
        except Exception as e:
            logger.error(f"Error resolving user for render: {e}")
            await wait_msg.edit_text("Ошибка при поиске игрока.", parse_mode="HTML")
            return
    else:
        # Try to get score_id from recent card context
        ctx = get_message_context(message.chat.id, message.message_id)
        if ctx and ctx.get("score_id"):
            score_id = ctx["score_id"]
            display_name = ctx.get("username", "")
        else:
            await message.answer(
                "Нет контекста для рендера.\n"
                "Сначала используйте <code>sr</code> или укажите ник: <code>render [никнейм]</code>",
                parse_mode="HTML",
            )
            return

    if not score_id:
        await message.answer("Не удалось определить скор для рендера.")
        return

    # Status message
    if not user_input:
        wait_msg = await message.answer(
            f"Загрузка реплея <b>{escape_html(display_name)}</b>...",
            parse_mode="HTML",
        )

    # Download replay
    try:
        replay_data = await osu_api_client.download_replay(score_id)
    except Exception as e:
        logger.error(f"Replay download error: {e}")
        replay_data = None

    if not replay_data:
        await wait_msg.edit_text(
            "Не удалось скачать реплей.\n"
            "Возможно, реплей недоступен (старый скор или фейл без сохранения).",
            parse_mode="HTML",
        )
        return

    # Load user render settings
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if user:
            settings = await _get_or_create_settings(session, user.id)
            ordr_kwargs = _settings_to_ordr_kwargs(settings)
        else:
            ordr_kwargs = {}

    # Submit to o!rdr
    await wait_msg.edit_text("Отправка реплея в o!rdr...", parse_mode="HTML")

    try:
        render_id = await ordr_client.submit_render(
            replay_data,
            api_key=ORDR_API_KEY or "",
            **ordr_kwargs,
        )
    except ordr_client.OrdrError as e:
        await wait_msg.edit_text(f"Ошибка o!rdr: {e.message}", parse_mode="HTML")
        return
    except Exception as e:
        logger.error(f"o!rdr submit error: {e}")
        await wait_msg.edit_text("Ошибка при отправке реплея в o!rdr.")
        return

    # Wait for render with progress updates
    last_status = [""]

    async def on_progress(progress_text: str):
        if progress_text != last_status[0] and progress_text != "Done.":
            last_status[0] = progress_text
            try:
                await wait_msg.edit_text(
                    f"Рендеринг видео...\n<i>{escape_html(progress_text)}</i>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await wait_msg.edit_text("Рендеринг видео...", parse_mode="HTML")

    try:
        video_url = await ordr_client.wait_for_render(
            render_id, timeout=300, on_progress=on_progress,
        )
    except ordr_client.OrdrError as e:
        await wait_msg.edit_text(f"Ошибка рендеринга: {e.message}", parse_mode="HTML")
        return
    except ordr_client.OrdrTimeoutError:
        await wait_msg.edit_text("Рендеринг занял слишком много времени. Попробуйте позже.")
        return
    except Exception as e:
        logger.error(f"o!rdr wait error: {e}")
        await wait_msg.edit_text("Ошибка при ожидании рендера.")
        return

    # Download video
    await wait_msg.edit_text("Загрузка видео...", parse_mode="HTML")

    try:
        video_bytes = await ordr_client.download_video(video_url)
    except Exception as e:
        logger.error(f"Video download error: {e}")
        await wait_msg.edit_text(
            f"Не удалось скачать видео. Ссылка:\n{video_url}",
            parse_mode="HTML",
        )
        return

    # Send video or link
    _cooldowns[tg_id] = time.time()

    if len(video_bytes) <= MAX_VIDEO_BYTES:
        try:
            await wait_msg.delete()
            from aiogram.types import BufferedInputFile
            video_file = BufferedInputFile(video_bytes, filename="render.mp4")
            await message.answer_video(video=video_file)
        except Exception as e:
            logger.error(f"Failed to send video: {e}")
            await message.answer(f"Не удалось отправить видео. Ссылка:\n{video_url}")
    else:
        await wait_msg.edit_text(
            f"Видео слишком большое для Telegram ({len(video_bytes) // (1024*1024)} МБ).\n"
            f"Ссылка: {video_url}",
            parse_mode="HTML",
        )


# ── ordr (change settings) ──

SETTING_ALIASES = {
    "skin": "skin",
    "resolution": "resolution",
    "res": "resolution",
    "cursor": "cursor_size",
    "trail": "cursor_trail",
    "pp": "show_pp_counter",
    "scoreboard": "show_scoreboard",
    "sb": "show_scoreboard",
    "keys": "show_key_overlay",
    "hiterror": "show_hit_error_meter",
    "he": "show_hit_error_meter",
    "mods": "show_mods",
    "result": "show_result_screen",
    "bgdim": "bg_dim",
    "dim": "bg_dim",
}

BOOL_TRUE = {"on", "true", "1", "yes", "да", "вкл"}
BOOL_FALSE = {"off", "false", "0", "no", "нет", "выкл"}


@router.message(TextTriggerFilter("ordr"))
async def cmd_ordr(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    tg_id = message.from_user.id
    args = (trigger_args.args or "").strip() if trigger_args else ""

    if not args:
        lines = [
            "<b>Настройки рендера o!rdr</b>",
            "",
            "Формат: <code>ordr [параметр] [значение]</code>",
            "",
            "<b>Параметры:</b>",
            "  <code>skin [название/ID]</code> — скин",
            "  <code>resolution 720/540</code> — разрешение",
            "  <code>cursor [0.5-2.0]</code> — размер курсора",
            "  <code>trail on/off</code> — трейл курсора",
            "  <code>pp on/off</code> — PP-счётчик",
            "  <code>scoreboard on/off</code> — скорборд",
            "  <code>keys on/off</code> — оверлей клавиш",
            "  <code>hiterror on/off</code> — хит-ошибки",
            "  <code>mods on/off</code> — моды",
            "  <code>result on/off</code> — экран результата",
            "  <code>bgdim [0-100]</code> — затемнение BG",
            "",
            "Используйте <code>renderset</code> для просмотра текущих настроек.",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    parts = args.split(maxsplit=1)
    param_name = parts[0].lower()
    param_value = parts[1].strip() if len(parts) > 1 else ""

    field = SETTING_ALIASES.get(param_name)
    if not field:
        await message.answer(
            f"Неизвестный параметр <code>{escape_html(param_name)}</code>.\n"
            f"Используйте <code>ordr</code> для списка параметров.",
            parse_mode="HTML",
        )
        return

    if not param_value:
        await message.answer(f"Укажите значение для <code>{param_name}</code>.", parse_mode="HTML")
        return

    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Вы не зарегистрированы.\n"
                "Используйте <code>register [osu_nickname]</code>",
                parse_mode="HTML",
            )
            return

        settings = await _get_or_create_settings(session, user.id)

        # Apply the setting
        try:
            if field == "skin":
                settings.skin = param_value
                display_val = param_value

            elif field == "resolution":
                val = param_value.replace("p", "")
                if val == "720":
                    settings.resolution = "1280x720"
                    display_val = "1280x720 (720p)"
                elif val == "540":
                    settings.resolution = "960x540"
                    display_val = "960x540 (540p)"
                else:
                    await message.answer("Доступные разрешения: <code>720</code> или <code>540</code>.", parse_mode="HTML")
                    return

            elif field == "cursor_size":
                fval = float(param_value)
                if not 0.5 <= fval <= 2.0:
                    await message.answer("Размер курсора: от 0.5 до 2.0.", parse_mode="HTML")
                    return
                settings.cursor_size = fval
                display_val = str(fval)

            elif field == "bg_dim":
                ival = int(param_value)
                if not 0 <= ival <= 100:
                    await message.answer("Затемнение BG: от 0 до 100.", parse_mode="HTML")
                    return
                settings.bg_dim = ival
                display_val = f"{ival}%"

            elif field in ("cursor_trail", "show_pp_counter", "show_scoreboard",
                           "show_key_overlay", "show_hit_error_meter", "show_mods",
                           "show_result_screen"):
                val_lower = param_value.lower()
                if val_lower in BOOL_TRUE:
                    setattr(settings, field, True)
                    display_val = "вкл"
                elif val_lower in BOOL_FALSE:
                    setattr(settings, field, False)
                    display_val = "выкл"
                else:
                    await message.answer("Используйте <code>on</code> или <code>off</code>.", parse_mode="HTML")
                    return
            else:
                await message.answer("Ошибка обработки параметра.")
                return

            await session.commit()
            await message.answer(
                f"Настройка <b>{param_name}</b> обновлена: <b>{escape_html(display_val)}</b>",
                parse_mode="HTML",
            )

        except ValueError:
            await message.answer("Неверный формат значения.", parse_mode="HTML")


# ── renderset / rdrs (view settings) ──

@router.message(TextTriggerFilter("renderset", "rdrs"))
async def cmd_renderset(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None):
    tg_id = message.from_user.id

    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id)
        if not user:
            await message.answer(
                "Вы не зарегистрированы.\n"
                "Используйте <code>register [osu_nickname]</code>",
                parse_mode="HTML",
            )
            return

        settings = await _get_or_create_settings(session, user.id)

        on_off = lambda v: "вкл" if v else "выкл"
        res_label = "720p" if settings.resolution == "1280x720" else "540p"

        lines = [
            "<b>Настройки рендера o!rdr</b>",
            "",
            f"  Скин: <b>{escape_html(settings.skin)}</b>",
            f"  Разрешение: <b>{settings.resolution} ({res_label})</b>",
            f"  Курсор: <b>{settings.cursor_size}</b>",
            f"  Трейл: <b>{on_off(settings.cursor_trail)}</b>",
            f"  PP-счётчик: <b>{on_off(settings.show_pp_counter)}</b>",
            f"  Скорборд: <b>{on_off(settings.show_scoreboard)}</b>",
            f"  Клавиши: <b>{on_off(settings.show_key_overlay)}</b>",
            f"  Хит-ошибки: <b>{on_off(settings.show_hit_error_meter)}</b>",
            f"  Моды: <b>{on_off(settings.show_mods)}</b>",
            f"  Экран результата: <b>{on_off(settings.show_result_screen)}</b>",
            f"  Затемнение BG: <b>{settings.bg_dim}%</b>",
            "",
            "Изменить: <code>ordr [параметр] [значение]</code>",
        ]
        await message.answer("\n".join(lines), parse_mode="HTML")


__all__ = ["router"]
