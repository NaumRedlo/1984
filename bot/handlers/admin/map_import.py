"""Unified /import command — ingest maps into BSK and/or HPS pools.

Forms supported (all admin-only):

    /import 123456
    /import https://osu.ppy.sh/b/123456
    /import https://osu.ppy.sh/beatmaps/123456
    /import https://osu.ppy.sh/beatmapsets/789       — whole set, all diffs
    /import https://osu.ppy.sh/beatmapsets/789#osu/123 — specific diff
    /import 123456 789012 https://...                — multiple tokens
    /import bsk <args>                               — BSK pool only
    /import hps <args>                               — HPS pool only

With a `.zip`/`.osz` attachment and caption `import` (optionally `import bsk`
or `import hps`), defers to the existing bulk-import queue.

Internally:
    parse_import_target → ImportTarget
    resolve_target      → list[beatmap_id]
    ingest_many         → list[IngestReport]
"""

from __future__ import annotations

import asyncio
from typing import Iterable

from aiogram import F, Router, types
from sqlalchemy import select

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.bsk_map_pool import BskMapPool
from db.models.hps_map_pool import HpsMapPool
from services.map_import import (
    IngestReport,
    PoolName,
    ImportTarget,
    TargetKind,
    ingest_beatmap,
    parse_import_target,
    resolve_target,
)
from services.map_import.ingest import DEFAULT_POOLS, ingest_many
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_map_import")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


# Bounded concurrency for batch ingest — keeps us polite to osu! API.
_BATCH_CONCURRENCY = 2


def _parse_pool_flag(args: str) -> tuple[tuple[PoolName, ...], str]:
    """Strip a leading `bsk`/`hps` token from args. Returns (pools, remaining_args)."""
    parts = args.strip().split(None, 1)
    if not parts:
        return DEFAULT_POOLS, ""
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if head == "bsk":
        return ("bsk",), rest
    if head == "hps":
        return ("hps",), rest
    return DEFAULT_POOLS, args


def _tokens(text: str) -> list[str]:
    return [t for t in text.replace(",", " ").split() if t]


def _format_targets(targets: list[ImportTarget]) -> str:
    parts = []
    for t in targets:
        if t.kind == TargetKind.BEATMAP:
            parts.append(f"b/{t.id}")
        elif t.kind == TargetKind.BEATMAPSET:
            parts.append(f"set/{t.id}")
        else:
            parts.append(f"?{t.raw}")
    return ", ".join(parts) or "—"


def _summarise(reports: Iterable[IngestReport]) -> dict:
    added_per_pool: dict[str, int] = {}
    skipped_per_pool: dict[str, int] = {}
    errored_per_pool: dict[str, int] = {}
    errs: list[str] = []
    for r in reports:
        for o in r.outcomes:
            if o.status == "added":
                added_per_pool[o.pool] = added_per_pool.get(o.pool, 0) + 1
            elif o.status == "skipped":
                skipped_per_pool[o.pool] = skipped_per_pool.get(o.pool, 0) + 1
            else:
                errored_per_pool[o.pool] = errored_per_pool.get(o.pool, 0) + 1
                if len(errs) < 5:
                    errs.append(f"{r.beatmap_id}/{o.pool}: {o.message}")
    return {
        "added":   added_per_pool,
        "skipped": skipped_per_pool,
        "errored": errored_per_pool,
        "errors":  errs,
    }


def _fmt_pool_dict(d: dict[str, int], pools: tuple[PoolName, ...]) -> str:
    return ", ".join(f"{p}=<b>{d.get(p, 0)}</b>" for p in pools)


@router.message(TextTriggerFilter("import", "imp"))
async def cmd_import(
    message: types.Message, trigger_args: TriggerArgs, osu_api_client,
):
    """Single entry point. Empty args + no document = print usage."""
    raw_args = (trigger_args.args or "").strip()

    # File path is handled by the document handler below — `cmd_import` is
    # for text-only invocations.
    if not raw_args and not message.document:
        await message.answer(
            "<b>Использование /import</b>\n\n"
            "Текстовые цели (id или ссылки):\n"
            "<code>/import 123456</code>\n"
            "<code>/import https://osu.ppy.sh/beatmapsets/789</code>\n"
            "<code>/import https://osu.ppy.sh/beatmapsets/789#osu/123</code>\n"
            "<code>/import 123 456 789</code>   — несколько за раз\n\n"
            "Только в один пул:\n"
            "<code>/import bsk 123</code>     "
            "<code>/import hps https://osu.ppy.sh/beatmapsets/789</code>\n\n"
            "Файлы:\n"
            "Прикрепите <code>.zip</code>/<code>.osz</code> с подписью "
            "<code>import</code> (можно <code>import bsk</code> / "
            "<code>import hps</code>).",
            parse_mode="HTML",
        )
        return

    pools, args = _parse_pool_flag(raw_args)
    tokens = _tokens(args)
    if not tokens:
        await message.answer(
            "Не вижу beatmap_id или ссылок. Пример: <code>/import 123456</code>",
            parse_mode="HTML",
        )
        return

    targets = [parse_import_target(t) for t in tokens]
    unknown = [t for t in targets if t.kind == TargetKind.UNKNOWN]
    valid   = [t for t in targets if t.kind != TargetKind.UNKNOWN]

    if not valid:
        await message.answer(
            "Ни одна цель не распознана.\n"
            f"Сырые токены: <code>{escape_html(' '.join(tokens))}</code>",
            parse_mode="HTML",
        )
        return

    wait = await message.answer(
        f"Разворачиваю цели: {_format_targets(valid)}…",
        parse_mode="HTML",
    )

    # Expand sets → flat list of beatmap_ids, dedup while preserving order.
    seen: set[int] = set()
    ids: list[int] = []
    expansion_errors: list[str] = []
    for t in valid:
        try:
            for bid in await resolve_target(osu_api_client, t):
                if bid not in seen:
                    seen.add(bid)
                    ids.append(bid)
        except Exception as e:
            logger.warning(f"resolve_target({t}) raised: {e}", exc_info=True)
            expansion_errors.append(f"{t.raw}: {e}")

    if not ids:
        msg = "Развернуть цели не удалось — ни одного beatmap_id."
        if expansion_errors:
            msg += "\nПервые ошибки:\n" + "\n".join(
                f"  • <code>{escape_html(e[:120])}</code>"
                for e in expansion_errors[:3]
            )
        await wait.edit_text(msg, parse_mode="HTML")
        return

    await wait.edit_text(
        f"Импортирую <b>{len(ids)}</b> карт(ы) в "
        f"<b>{'+'.join(pools)}</b>… (concurrency {_BATCH_CONCURRENCY})",
        parse_mode="HTML",
    )

    reports = await ingest_many(
        osu_api_client, ids, pools=pools, concurrency=_BATCH_CONCURRENCY,
    )
    summary = _summarise(reports)

    lines = [
        "<b>Импорт завершён</b>",
        f"Цели: <code>{escape_html(_format_targets(valid))}</code>",
        f"Карт обработано: <b>{len(ids)}</b>   "
        f"Пулы: <b>{'+'.join(pools)}</b>",
        "",
        f"✅ Добавлено   {_fmt_pool_dict(summary['added'], pools)}",
        f"⏭ Пропущено   {_fmt_pool_dict(summary['skipped'], pools)}",
        f"❌ Ошибки     {_fmt_pool_dict(summary['errored'], pools)}",
    ]
    if unknown:
        lines.append("")
        lines.append("Не распознано: " + ", ".join(
            f"<code>{escape_html(t.raw)}</code>" for t in unknown[:5]
        ))
    if summary["errors"]:
        lines.append("")
        lines.append("Первые ошибки:")
        for e in summary["errors"]:
            lines.append(f"  • <code>{escape_html(e[:140])}</code>")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(F.document & F.caption.func(
    lambda c: bool(c) and c.strip().lower().split()[0] in ("import", "imp")
))
async def cmd_import_document(message: types.Message, osu_api_client):
    """File upload with caption `import [bsk|hps]`.

    Delegates to the existing bsk_pool bulk-import queue infrastructure but
    routes the post-processing through `services.map_import.ingest` so both
    pools receive the maps.
    """
    # Lift the caption detection here and call into the queue/semaphore
    # plumbing housed in bsk_pool. The queue itself is pool-agnostic.
    from bot.handlers.admin.bsk_pool import (
        MAX_IMPORT_FILE_SIZE,
        MAX_IMPORT_SLOTS,
        _cleanup_stale_imports,
        _count_osu_files,
        _download_url_to_import_file,
        _fmt_bytes,
        _import_queue,
        _queue_position,
        _register_import,
    )
    from config.settings import TELEGRAM_BOT_TOKEN

    _cleanup_stale_imports()
    doc = message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".zip") or fname.endswith(".osz")):
        await message.answer(
            "Поддерживаются только файлы <b>.zip</b> или <b>.osz</b>.",
            parse_mode="HTML",
        )
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(
            f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS})."
        )
        return

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE:
        await message.answer("Файл слишком большой (макс. 1 GB).")
        return

    caption = (message.caption or "").strip().lower()
    parts = caption.split()
    pools: tuple[PoolName, ...] = DEFAULT_POOLS
    if len(parts) >= 2 and parts[1] in ("bsk", "hps"):
        pools = (parts[1],)  # type: ignore[assignment]

    wait = await message.answer("Скачиваю файл в очередь импорта…")
    try:
        file = await message.bot.get_file(doc.file_id)
        file_url = (
            f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/"
            f"{file.file_path}"
        )
        tmp_path, size = await _download_url_to_import_file(
            file_url, max_bytes=MAX_IMPORT_FILE_SIZE,
        )
    except Exception as e:
        await wait.edit_text(
            f"Не удалось скачать файл: {escape_html(str(e))}",
            parse_mode="HTML",
        )
        return

    slot_id = _register_import(
        message.from_user.id, tmp_path, doc.file_name or "upload.zip", size,
    )
    osz_count, osu_count = _count_osu_files(tmp_path)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(
            text="✅ Импортировать",
            callback_data=f"import:confirm:{slot_id}:{'+'.join(pools)}",
        ),
        types.InlineKeyboardButton(
            text="❌ Отмена",
            callback_data=f"import:cancel:{slot_id}",
        ),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{escape_html(doc.file_name)}</b>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Пулы: <b>{'+'.join(pools)}</b>\n\n"
        f"Подтвердить импорт?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("import:"))
async def on_import_confirm(callback: types.CallbackQuery, osu_api_client):
    from bot.handlers.admin.bsk_pool import (
        _cleanup_import_file,
        _get_import_semaphore,
        _import_queue,
    )

    parts = callback.data.split(":")
    action  = parts[1]
    slot_id = parts[2] if len(parts) > 2 else None
    pools_token = parts[3] if len(parts) > 3 else "+".join(DEFAULT_POOLS)
    pools: tuple[PoolName, ...] = tuple(
        p for p in pools_token.split("+") if p in ("bsk", "hps")
    ) or DEFAULT_POOLS  # type: ignore[assignment]

    if not slot_id:
        await callback.answer("Слот не найден.", show_alert=True)
        return

    slot = _import_queue.get(slot_id)
    if not slot:
        await callback.answer(
            "Сессия истекла. Загрузите файл заново.", show_alert=True,
        )
        return
    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не ваш импорт.", show_alert=True)
        return

    if action == "cancel":
        _import_queue.pop(slot_id, None)
        _cleanup_import_file(slot.get("file_path"))
        await callback.message.edit_text("Импорт отменён.")
        await callback.answer()
        return

    slot["status"] = "queued"
    await callback.message.edit_text(
        f"<b>Импорт поставлен в очередь</b>\n"
        f"Файл: <b>{escape_html(slot['filename'])}</b>\n"
        f"Пулы: <b>{'+'.join(pools)}</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    msg = callback.message

    async def _run():
        result = None
        try:
            async with _get_import_semaphore():
                if slot_id not in _import_queue:
                    return
                slot["status"] = "running"
                try:
                    await msg.edit_text(
                        f"<b>Импортирую карты…</b>\n"
                        f"Файл: <b>{escape_html(slot['filename'])}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

                from services.bsk.bulk_import import import_from_file
                # BSK ingest first (it parses each .osu locally — fast).
                result = await import_from_file(
                    slot["file_path"], osu_api_client,
                )

                # If hps pool was requested, walk the now-ingested BSK rows
                # and ingest the same beatmap_ids into HPS. This avoids
                # re-parsing the archive — we trust BSK's added list.
                if "hps" in pools:
                    added_ids = await _collect_recently_added_bsk_ids(
                        result.get("added", 0)
                    )
                    if added_ids:
                        reports = await ingest_many(
                            osu_api_client, added_ids,
                            pools=("hps",), concurrency=_BATCH_CONCURRENCY,
                        )
                        hps_added = sum(
                            1 for r in reports
                            for o in r.outcomes
                            if o.pool == "hps" and o.status == "added"
                        )
                        result["hps_added"] = hps_added
        except Exception as e:
            logger.error(f"/import bulk error: {e}", exc_info=True)
            result = {"added": 0, "skipped": 0, "failed": 1, "errors": [str(e)]}
        finally:
            _import_queue.pop(slot_id, None)
            _cleanup_import_file(slot.get("file_path"))

        added   = result.get("added", 0)
        skipped = result.get("skipped", 0)
        failed  = result.get("failed", 0)
        hps_added = result.get("hps_added", 0)
        lines = [
            "<b>Импорт завершён</b>",
            f"Файл: <b>{escape_html(slot['filename'])}</b>",
            f"Пулы: <b>{'+'.join(pools)}</b>",
            f"✅ BSK добавлено: <b>{added}</b>",
        ]
        if "hps" in pools:
            lines.append(f"✅ HPS добавлено: <b>{hps_added}</b>")
        lines += [
            f"⏭ Пропущено (BSK): <b>{skipped}</b>",
            f"❌ Ошибок: <b>{failed}</b>",
        ]
        if result.get("errors"):
            lines.append("\nПервые ошибки:")
            for e in result["errors"]:
                lines.append(f"  • {escape_html(str(e)[:120])}")
        try:
            await msg.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception:
            pass

    asyncio.create_task(_run())


async def _collect_recently_added_bsk_ids(limit: int) -> list[int]:
    """Pull the most recently added BSK pool rows. Used by the bulk path to
    feed the HPS ingest after BSK is done."""
    if limit <= 0:
        return []
    async with get_db_session() as session:
        rows = (await session.execute(
            select(BskMapPool.beatmap_id)
            .order_by(BskMapPool.id.desc())
            .limit(limit)
        )).scalars().all()
    return [int(b) for b in rows]
