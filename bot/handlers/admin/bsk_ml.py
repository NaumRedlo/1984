import asyncio
from datetime import datetime, timedelta

from aiogram import Router, types, F
from sqlalchemy import select, desc

from bot.filters import TextTriggerFilter
from db.database import get_db_session
from utils.admin_check import AdminFilter
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bsk_ml")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

def _ml_monitor_keyboard(running: bool, paused: bool) -> types.InlineKeyboardMarkup:
    if not running:
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="🔄 Запустить снова", callback_data="bskml:start"),
        ]])
    if paused:
        return types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="▶️ Продолжить", callback_data="bskml:resume"),
            types.InlineKeyboardButton(text="❌ Отменить", callback_data="bskml:cancel"),
        ]])
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="⏸ Пауза", callback_data="bskml:pause"),
        types.InlineKeyboardButton(text="❌ Отменить", callback_data="bskml:cancel"),
        types.InlineKeyboardButton(text="🔃 Обновить", callback_data="bskml:refresh"),
    ]])


@router.message(TextTriggerFilter("bsktrainml"))
async def cmd_bsk_train_ml(message: types.Message):
    from tasks.bsk_ml_trainer import is_running

    if is_running():
        await message.answer("Обучение уже запущено. Используйте <code>bskmlmonitor</code> для наблюдения.", parse_mode="HTML")
        return

    import asyncio
    wait = await message.answer(
        "<b>ML обучение запущено...</b>",
        parse_mode="HTML",
        reply_markup=_ml_monitor_keyboard(True, False),
    )

    async def _run_and_update():
        from tasks.bsk_ml_trainer import run_nightly_training
        result = await run_nightly_training(triggered_by=f"admin:{message.from_user.id}")
        status = result.get("status", "?")
        if status == "skipped":
            text = f"Недостаточно данных.\nРаундов: <b>{result.get('rounds_used', 0)}</b> (нужно ≥50)"
        elif status == "ok":
            rf_trained = bool(result.get("global_model_trained"))
            rf_samples = result.get("global_model_samples", 0)
            oob = result.get("oob_r2")
            if rf_trained:
                oob_str = f", OOB R²={oob:.3f}" if oob is not None else ""
                rf_line = f"🌲 Глобальный RF: <b>обучен</b> ({rf_samples} карт{oob_str})"
            else:
                rf_line = f"🌲 Глобальный RF: <b>не обучен</b> (мало карт с данными: {rf_samples})"

            # Top-3 features by importance, if model produced them.
            top_str = ""
            fi_json = result.get("feature_importances")
            if fi_json:
                try:
                    import json as _json
                    fi = _json.loads(fi_json)
                    top = fi.get("top", [])[:3]
                    if top:
                        top_str = "\n📊 Top фичи: " + ", ".join(
                            f"<code>{t['name']}</code> ({t['imp']:.2f})" for t in top
                        )
                except Exception:
                    logger.debug("bsktrainml: feature_importances JSON parse failed", exc_info=True)

            text = (
                f"<b>ML обучение завершено</b>\n\n"
                f"Раундов: <b>{result.get('rounds_used', 0)}</b>\n"
                f"{rf_line}{top_str}\n\n"
                f"💪 От данных: <b>{result.get('maps_data_driven', 0)}</b>\n"
                f"🌲 От RF-приора: <b>{result.get('maps_rf_prior', 0)}</b>\n"
                f"📐 От эвристики: <b>{result.get('maps_heuristic', 0)}</b>\n"
                f"⏭ Пропущено (мало раундов на карту): <b>{result.get('maps_skipped', 0)}</b>"
            )
        elif status == "cancelled":
            text = f"<b>Обучение отменено.</b>\nКарт обновлено до отмены: <b>{result.get('maps_updated', 0)}</b>"
        elif status == "timeout":
            text = f"Обучение прервано по таймауту (3 часа).\nОбновлено: <b>{result.get('maps_updated', 0)}</b>"
        else:
            text = f"Ошибка: {result.get('error', '?')}"
        try:
            await wait.edit_text(text, parse_mode="HTML", reply_markup=_ml_monitor_keyboard(False, False))
        except Exception:
            logger.debug("bsktrainml: result edit_text failed", exc_info=True)

    asyncio.create_task(_run_and_update())


@router.message(TextTriggerFilter("bskmlmonitor", "bskmlm"))
async def cmd_bsk_ml_monitor(message: types.Message):
    from tasks.bsk_ml_trainer import is_running, is_paused, get_progress

    if not is_running():
        await message.answer("Модель в данный момент не обучается.")
        return

    p = get_progress()
    paused = is_paused()
    status_text = "на паузе" if paused else "идёт"
    done = p.get("maps_done", 0)
    total = p.get("maps_total", "?")
    updated = p.get("maps_updated", 0)
    skipped = p.get("maps_skipped", 0)
    rounds = p.get("rounds_used", 0)

    await message.answer(
        f"<b>ML обучение {status_text}</b>\n\n"
        f"Раундов: <b>{rounds}</b>\n"
        f"Прогресс: <b>{done}/{total}</b> карт\n"
        f"Обновлено: <b>{updated}</b>  Пропущено: <b>{skipped}</b>",
        parse_mode="HTML",
        reply_markup=_ml_monitor_keyboard(True, paused),
    )


@router.callback_query(F.data.startswith("bskml:"))
async def on_bskml_control(callback: types.CallbackQuery):
    from tasks.bsk_ml_trainer import (
        is_running, is_paused, pause_training, resume_training,
        cancel_training, get_progress, run_nightly_training
    )
    action = callback.data.split(":")[1]

    if action == "pause":
        if is_running() and not is_paused():
            pause_training()
            await callback.answer("Пауза")
            p = get_progress()
            await callback.message.edit_text(
                f"<b>ML обучение на паузе</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт\n"
                f"Обновлено: <b>{p.get('maps_updated', 0)}</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, True),
            )
        else:
            await callback.answer("Нечего ставить на паузу.", show_alert=True)

    elif action == "resume":
        if is_paused():
            resume_training()
            await callback.answer("Продолжаю")
            p = get_progress()
            await callback.message.edit_text(
                f"<b>ML обучение продолжается...</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, False),
            )
        else:
            await callback.answer("Обучение не на паузе.", show_alert=True)

    elif action == "cancel":
        if is_running():
            cancel_training()
            await callback.answer("Отменяю...")
            await callback.message.edit_text(
                "<b>Обучение отменено.</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(False, False),
            )
        else:
            await callback.answer("Обучение не запущено.", show_alert=True)

    elif action == "refresh":
        if is_running():
            p = get_progress()
            status_text = "на паузе" if is_paused() else "идёт"
            await callback.answer("Обновлено")
            await callback.message.edit_text(
                f"<b>ML обучение {status_text}</b>\n\n"
                f"Прогресс: <b>{p.get('maps_done', 0)}/{p.get('maps_total', '?')}</b> карт\n"
                f"Обновлено: <b>{p.get('maps_updated', 0)}</b>\n"
                f"Пропущено: <b>{p.get('maps_skipped', 0)}</b>",
                parse_mode="HTML",
                reply_markup=_ml_monitor_keyboard(True, is_paused()),
            )
        else:
            await callback.answer("Обучение завершено.", show_alert=True)

    elif action == "start":
        if is_running():
            await callback.answer("Уже запущено.", show_alert=True)
            return
        await callback.answer("Запускаю...")
        import asyncio
        async def _run():
            result = await run_nightly_training(triggered_by=f"admin:{callback.from_user.id}")
            status = result.get("status", "?")
            text = (f"<b>ML завершено</b> — {status}\n"
                    f"Обновлено: <b>{result.get('maps_updated', 0)}</b>")
            try:
                await callback.message.edit_text(text, parse_mode="HTML",
                                                 reply_markup=_ml_monitor_keyboard(False, False))
            except Exception:
                logger.debug("bskml control: result edit_text failed", exc_info=True)
        asyncio.create_task(_run())
        await callback.message.edit_text(
            "<b>ML обучение запущено...</b>",
            parse_mode="HTML",
            reply_markup=_ml_monitor_keyboard(True, False),
        )



@router.message(TextTriggerFilter("bskmlstats"))
async def cmd_bsk_ml_stats(message: types.Message):
    """bskmlstats — show BSK ML training history."""
    from db.models.bsk_ml_run import BskMlRun
    from db.models.bsk_duel_round import BskDuelRound

    async with get_db_session() as session:
        runs = (await session.execute(
            select(BskMlRun).order_by(desc(BskMlRun.ran_at)).limit(5)
        )).scalars().all()

        total_rounds = (await session.execute(
            select(func.count()).select_from(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.player1_composite.isnot(None),
            )
        )).scalar() or 0

    # Next scheduled run (in configured local timezone — must match scheduler)
    from zoneinfo import ZoneInfo
    from config.settings import TIMEZONE
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= next_run:
        next_run += timedelta(days=1)
    hours_until = (next_run - now).total_seconds() / 3600

    lines = [
        "<b>BSK ML — статистика</b>\n",
        f"Раундов в БД: <b>{total_rounds}</b> (нужно ≥50 для обучения)",
        f"Следующий запуск: <b>{next_run.strftime('%d.%m %H:%M')}</b> (через {hours_until:.1f}ч)\n",
    ]

    if runs:
        lines.append("<b>Последние запуски:</b>")
        for r in runs:
            ts = r.ran_at.strftime("%d.%m %H:%M") if r.ran_at else "?"
            trigger = r.triggered_by or "scheduler"
            acc_str = ""
            if r.prediction_accuracy is not None:
                acc_str = f"  ·  🎯 {r.prediction_accuracy*100:.1f}% ({r.predictions_correct}/{r.predictions_total})"

            if r.status == "ok":
                # New honest breakdown — fall back to legacy single counter for old rows.
                if r.maps_data_driven is not None:
                    if r.global_model_trained:
                        oob = getattr(r, "oob_r2", None)
                        oob_str = f", OOB R²={oob:.2f}" if oob is not None else ""
                        rf_state = f"🌲 RF✓ ({r.global_model_samples} карт{oob_str})"
                    else:
                        rf_state = "🌲 RF✗"
                    breakdown = (
                        f"💪 {r.maps_data_driven} от данных · "
                        f"🌲 {r.maps_rf_prior or 0} от RF · "
                        f"📐 {r.maps_heuristic or 0} от эвристики"
                    )
                    lines.append(
                        f"✅ {ts} [{trigger}] · {r.rounds_used} раундов · {rf_state}{acc_str}\n"
                        f"   {breakdown}"
                    )
                else:
                    lines.append(
                        f"✅ {ts} [{trigger}] — обновлено {r.maps_updated} карт "
                        f"из {r.rounds_used} раундов{acc_str}"
                    )
            elif r.status == "skipped":
                lines.append(f"⏭ {ts} [{trigger}] — мало данных ({r.rounds_used} раундов)")
            elif r.status == "timeout":
                lines.append(f"⏰ {ts} [{trigger}] — таймаут{acc_str}")
            else:
                lines.append(f"❌ {ts} [{trigger}] — ошибка: {r.notes or '?'}")
    else:
        lines.append("Запусков ещё не было.")

    await message.answer("\n".join(lines), parse_mode="HTML")
