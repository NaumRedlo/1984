"""Admin commands for the autonomous map crawler.

  /crawleron                — flip enabled=1
  /crawleroff               — flip enabled=0
  /crawlerstatus, /crst     — show config + last run
  /crawlertrigger, /crtrig  — run one cycle right now (foreground)
  /crawlerbudget <N>        — set per-cycle budget
  /crawlerinterval <H>      — set hours between cycles

All settings live in BotSettings rows so the background loop picks them up
without a restart.
"""

from __future__ import annotations

import json
from datetime import datetime

from aiogram import Router, types

from bot.filters import TextTriggerFilter, TriggerArgs
from services.map_import.crawler import (
    DEFAULT_BUDGET,
    DEFAULT_INTERVAL,
    SETTING_BUDGET,
    SETTING_ENABLED,
    SETTING_INTERVAL_H,
    SETTING_LAST_REPORT,
    SETTING_LAST_RUN,
    _read_setting,
    _write_setting,
    read_config,
    run_one_cycle,
)
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_map_crawler")
router.message.filter(AdminFilter())


@router.message(TextTriggerFilter("crawleron"))
async def cmd_crawler_on(message: types.Message):
    await _write_setting(SETTING_ENABLED, "1")
    cfg = await read_config()
    await message.answer(
        f"✅ Crawler включен.\n"
        f"Интервал: <b>{cfg.interval_hours}ч</b>   "
        f"Бюджет/цикл: <b>{cfg.budget}</b>",
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("crawleroff"))
async def cmd_crawler_off(message: types.Message):
    await _write_setting(SETTING_ENABLED, "0")
    await message.answer("⏸ Crawler выключен. Фоновая задача дальше спит, можно безопасно оставить.")


@router.message(TextTriggerFilter("crawlerstatus", "crst"))
async def cmd_crawler_status(message: types.Message):
    cfg = await read_config()
    last_run  = await _read_setting(SETTING_LAST_RUN)
    last_raw  = await _read_setting(SETTING_LAST_REPORT)

    lines = [
        "<b>Map crawler — статус</b>",
        f"Состояние: <b>{'включен' if cfg.enabled else 'выключен'}</b>",
        f"Интервал: <b>{cfg.interval_hours}ч</b>   "
        f"Бюджет/цикл: <b>{cfg.budget}</b>",
        f"Зоны SR: <code>{escape_html(json.dumps(cfg.zones))}</code>",
        "",
    ]
    if last_run:
        try:
            dt = datetime.fromisoformat(last_run)
            ago = (datetime.now(dt.tzinfo) - dt).total_seconds() / 3600
            lines.append(
                f"Последний запуск: <b>{dt.strftime('%Y-%m-%d %H:%M UTC')}</b> "
                f"({ago:.1f}ч назад)"
            )
        except Exception:
            lines.append(f"Последний запуск (raw): <code>{escape_html(last_run)}</code>")
    else:
        lines.append("Последний запуск: <i>—</i>")

    if last_raw:
        try:
            r = json.loads(last_raw)
            added   = r.get("added_per_pool") or {}
            skipped = r.get("skipped_per_pool") or {}
            errored = r.get("errors_per_pool") or {}
            lines += [
                f"Кандидатов: <b>{r.get('found_candidates', 0)}</b>",
                f"Залито в пулы: BSK <b>{added.get('bsk', 0)}</b> · "
                f"HPS <b>{added.get('hps', 0)}</b>",
                f"Пропущено: BSK <b>{skipped.get('bsk', 0)}</b> · "
                f"HPS <b>{skipped.get('hps', 0)}</b>",
                f"Ошибок: BSK <b>{errored.get('bsk', 0)}</b> · "
                f"HPS <b>{errored.get('hps', 0)}</b>",
            ]
            notes = r.get("notes") or []
            if notes:
                lines.append("")
                lines.append("<b>Логи цикла:</b>")
                for n in notes[-6:]:
                    lines.append(f"  • <code>{escape_html(str(n)[:140])}</code>")
        except Exception as e:
            lines.append(f"<i>Не удалось разобрать last_report: {e}</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("crawlertrigger", "crtrig"))
async def cmd_crawler_trigger(message: types.Message, osu_api_client):
    """Run one cycle now. Bypasses the enabled flag — useful for testing."""
    wait = await message.answer("Запускаю цикл crawler'а вручную…")
    try:
        cfg = await read_config()
        # Force enabled=True for the trigger path so an "off" crawler can
        # still be tested without flipping the global flag.
        from dataclasses import replace
        cfg = replace(cfg, enabled=True)
        report = await run_one_cycle(osu_api_client, config=cfg)
    except Exception as e:
        logger.error(f"crawler trigger failed: {e}", exc_info=True)
        await wait.edit_text(
            f"Ошибка: <code>{escape_html(str(e)[:200])}</code>",
            parse_mode="HTML",
        )
        return

    added = report.added_per_pool
    lines = [
        "<b>Crawler — ручной цикл</b>",
        f"Кандидатов: <b>{report.found_candidates}</b>",
        f"Залито: BSK <b>{added.get('bsk', 0)}</b> · "
        f"HPS <b>{added.get('hps', 0)}</b>",
    ]
    if report.ingested_ids:
        sample = ", ".join(str(b) for b in report.ingested_ids[:10])
        more = "" if len(report.ingested_ids) <= 10 else f" (+{len(report.ingested_ids) - 10})"
        lines.append(f"ID: <code>{sample}{more}</code>")
    if report.notes:
        lines.append("")
        lines.append("Логи:")
        for n in report.notes[-6:]:
            lines.append(f"  • <code>{escape_html(str(n)[:140])}</code>")
    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("crawlerbudget"))
async def cmd_crawler_budget(message: types.Message, trigger_args: TriggerArgs):
    raw = (trigger_args.args or "").strip()
    if not raw.isdigit():
        await message.answer(
            f"Использование: <code>/crawlerbudget &lt;N&gt;</code>  "
            f"(текущий: {DEFAULT_BUDGET} по умолчанию)",
            parse_mode="HTML",
        )
        return
    n = max(1, min(int(raw), 500))
    await _write_setting(SETTING_BUDGET, str(n))
    await message.answer(f"Бюджет/цикл: <b>{n}</b> карт.", parse_mode="HTML")


@router.message(TextTriggerFilter("crawlerinterval"))
async def cmd_crawler_interval(message: types.Message, trigger_args: TriggerArgs):
    raw = (trigger_args.args or "").strip()
    if not raw.isdigit():
        await message.answer(
            f"Использование: <code>/crawlerinterval &lt;часов&gt;</code>  "
            f"(текущий: {DEFAULT_INTERVAL}ч по умолчанию)",
            parse_mode="HTML",
        )
        return
    h = max(1, min(int(raw), 168))
    await _write_setting(SETTING_INTERVAL_H, str(h))
    await message.answer(
        f"Интервал между циклами: <b>{h}ч</b>. Применится после текущего сна.",
        parse_mode="HTML",
    )
