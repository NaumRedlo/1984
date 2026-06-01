"""Unified /import command — ingest maps into DUEL and/or HPS pools.

Forms supported (all admin-only):

    /import 123456
    /import https://osu.ppy.sh/b/123456
    /import https://osu.ppy.sh/beatmaps/123456
    /import https://osu.ppy.sh/beatmapsets/789       — whole set, all diffs
    /import https://osu.ppy.sh/beatmapsets/789#osu/123 — specific diff
    /import 123456 789012 https://...                — multiple tokens
    /import duel <args>                               — DUEL pool only
    /import hps <args>                               — HPS pool only

With a `.zip`/`.osz` attachment and caption `import` (optionally `import duel`
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
from db.models.duel_map_pool import DuelMapPool
from db.models.hps_map_pool import HpsMapPool
from services.map_import import (
    FileUrlResolveError,
    IngestReport,
    PoolName,
    ImportTarget,
    TargetKind,
    ingest_beatmap,
    parse_import_target,
    resolve_file_url,
    resolve_target,
)
from services.map_import.ingest import DEFAULT_POOLS, ingest_many
from services.map_import.multi_volume import (
    MultiVolumeError,
    assemble_to_archive,
    classify_parts,
)
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
    """Strip a leading `duel`/`hps` token from args. Returns (pools, remaining_args)."""
    parts = args.strip().split(None, 1)
    if not parts:
        return DEFAULT_POOLS, ""
    head = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if head == "duel":
        return ("duel",), rest
    if head == "hps":
        return ("hps",), rest
    return DEFAULT_POOLS, args


def _peel_multi_flag(args: str) -> tuple[bool, str]:
    """If args starts with `multi` token, return (True, rest); else (False, args)."""
    parts = args.strip().split(None, 1)
    if parts and parts[0].lower() == "multi":
        return True, parts[1] if len(parts) > 1 else ""
    return False, args


def _tokens(text: str) -> list[str]:
    return [t for t in text.replace(",", " ").split() if t]


def _format_targets(targets: list[ImportTarget]) -> str:
    parts = []
    for t in targets:
        if t.kind == TargetKind.BEATMAP:
            parts.append(f"b/{t.id}")
        elif t.kind == TargetKind.BEATMAPSET:
            parts.append(f"set/{t.id}")
        elif t.kind == TargetKind.FILE_URL:
            # Truncate the URL to keep the summary line tidy.
            url = t.raw
            short = url if len(url) <= 40 else url[:37] + "…"
            parts.append(f"file:{short}")
        elif t.kind == TargetKind.UNSUPPORTED:
            parts.append(f"unsupported:{t.raw[:30]}")
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
            "<b>osu! ссылки/ID</b>:\n"
            "<code>/import 123456</code>\n"
            "<code>/import https://osu.ppy.sh/beatmapsets/789</code>\n"
            "<code>/import https://osu.ppy.sh/beatmapsets/789#osu/123</code>\n"
            "<code>/import 123 456 789</code>   — несколько за раз\n\n"
            "<b>Ссылки на архивы (.zip / .osz)</b>:\n"
            "• Любой прямой URL: "
            "<code>/import https://example.com/maps.zip</code>\n"
            "• Google Drive: "
            "<code>/import https://drive.google.com/file/d/&lt;id&gt;/view</code>\n"
            "• MediaFire: "
            "<code>/import https://www.mediafire.com/file/&lt;id&gt;/...</code> "
            "или прямая <code>download####.mediafire.com/...</code>\n"
            "• Mega — не поддерживается (зашифрованный поток)\n\n"
            "<b>Только в один пул</b>:\n"
            "<code>/import duel 123</code>     "
            "<code>/import hps https://drive.google.com/...</code>\n\n"
            "<b>Файл-вложение</b>:\n"
            "Прикрепите <code>.zip</code>/<code>.osz</code> с подписью "
            "<code>import</code> (можно <code>import duel</code> / "
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

    targets       = [parse_import_target(t) for t in tokens]
    unknown       = [t for t in targets if t.kind == TargetKind.UNKNOWN]
    unsupported   = [t for t in targets if t.kind == TargetKind.UNSUPPORTED]
    file_url_tgts = [t for t in targets if t.kind == TargetKind.FILE_URL]
    osu_tgts      = [t for t in targets if t.kind in (TargetKind.BEATMAP, TargetKind.BEATMAPSET)]
    valid         = osu_tgts + file_url_tgts

    if not valid:
        lines = ["Ни одна цель не распознана."]
        if unsupported:
            lines.append("")
            for t in unsupported[:3]:
                lines.append(
                    f"  • <code>{escape_html(t.raw[:80])}</code> — "
                    f"{escape_html(t.reason or '')}"
                )
        else:
            lines.append(
                f"Сырые токены: <code>{escape_html(' '.join(tokens))}</code>"
            )
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    wait = await message.answer(
        f"Разворачиваю цели: {_format_targets(valid)}…",
        parse_mode="HTML",
    )

    # ── File-URL targets: download each archive into the bulk-import
    # queue, kick off background processing per file. Their reports are
    # emitted on the worker's own message; the /import response only
    # mentions that they're queued (or failed).
    file_url_started: list[str] = []
    file_url_errors:  list[str] = []
    for t in file_url_tgts:
        try:
            await _queue_file_url_import(
                message=message,
                target=t,
                pools=pools,
                osu_api_client=osu_api_client,
            )
            file_url_started.append(t.raw)
        except Exception as e:
            logger.warning(f"file_url ingest setup failed for {t.raw}: {e}", exc_info=True)
            file_url_errors.append(f"{t.raw}: {e}")

    # ── osu! targets: classic per-beatmap ingest path.
    ids: list[int] = []
    expansion_errors: list[str] = []
    if osu_tgts:
        seen: set[int] = set()
        for t in osu_tgts:
            try:
                for bid in await resolve_target(osu_api_client, t):
                    if bid not in seen:
                        seen.add(bid)
                        ids.append(bid)
            except Exception as e:
                logger.warning(f"resolve_target({t}) raised: {e}", exc_info=True)
                expansion_errors.append(f"{t.raw}: {e}")

    summary = None
    if ids:
        await wait.edit_text(
            f"Импортирую <b>{len(ids)}</b> карт(ы) в "
            f"<b>{'+'.join(pools)}</b>… (concurrency {_BATCH_CONCURRENCY})",
            parse_mode="HTML",
        )
        reports = await ingest_many(
            osu_api_client, ids, pools=pools, concurrency=_BATCH_CONCURRENCY,
        )
        summary = _summarise(reports)

    # ── Final message: assemble per-source results.
    lines = ["<b>Импорт завершён</b>"]
    if osu_tgts:
        lines.append(
            f"Цели: <code>{escape_html(_format_targets(osu_tgts))}</code>"
        )
        lines.append(
            f"Карт обработано: <b>{len(ids)}</b>   "
            f"Пулы: <b>{'+'.join(pools)}</b>"
        )
        if summary:
            lines.extend([
                "",
                f"✅ Добавлено   {_fmt_pool_dict(summary['added'], pools)}",
                f"⏭ Пропущено   {_fmt_pool_dict(summary['skipped'], pools)}",
                f"❌ Ошибки     {_fmt_pool_dict(summary['errored'], pools)}",
            ])
        elif expansion_errors:
            lines.append("")
            lines.append("Развернуть цели не удалось:")
            for e in expansion_errors[:3]:
                lines.append(f"  • <code>{escape_html(e[:140])}</code>")

    if file_url_started:
        lines.append("")
        lines.append(
            f"📦 В очереди файлов: <b>{len(file_url_started)}</b> "
            f"(отдельный отчёт по каждому)"
        )
    if file_url_errors:
        lines.append("")
        lines.append("Файлы не удалось поставить в очередь:")
        for e in file_url_errors[:3]:
            lines.append(f"  • <code>{escape_html(e[:140])}</code>")

    if unsupported:
        lines.append("")
        lines.append("Не поддерживается:")
        for t in unsupported[:3]:
            lines.append(
                f"  • <code>{escape_html(t.raw[:80])}</code> — "
                f"{escape_html(t.reason or '')}"
            )
    if unknown:
        lines.append("")
        lines.append("Не распознано: " + ", ".join(
            f"<code>{escape_html(t.raw)}</code>" for t in unknown[:5]
        ))
    if summary and summary["errors"]:
        lines.append("")
        lines.append("Первые ошибки:")
        for e in summary["errors"]:
            lines.append(f"  • <code>{escape_html(e[:140])}</code>")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(F.document & F.caption.func(
    lambda c: bool(c) and c.strip().lower().split()[0] in ("import", "imp")
))
async def cmd_import_document(message: types.Message, osu_api_client):
    """File upload with caption `import [duel|hps]`.

    Delegates to the existing duel_pool bulk-import queue infrastructure but
    routes the post-processing through `services.map_import.ingest` so both
    pools receive the maps.
    """
    # Lift the caption detection here and call into the queue/semaphore
    # plumbing housed in duel_pool. The queue itself is pool-agnostic.
    from bot.handlers.admin.duel_pool import (
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
    if len(parts) >= 2 and parts[1] in ("duel", "hps"):
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
    from bot.handlers.admin.duel_pool import (
        _cleanup_import_file,
        _import_queue,
    )

    parts = callback.data.split(":")
    action  = parts[1]
    slot_id = parts[2] if len(parts) > 2 else None
    pools_token = parts[3] if len(parts) > 3 else "+".join(DEFAULT_POOLS)
    pools: tuple[PoolName, ...] = tuple(
        p for p in pools_token.split("+") if p in ("duel", "hps")
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

    asyncio.create_task(_run_bulk_import_worker(
        slot_id=slot_id, pools=pools,
        status_msg=callback.message,
        osu_api_client=osu_api_client,
    ))


async def _collect_recently_added_duel_ids(limit: int) -> list[int]:
    """Pull the most recently added DUEL pool rows. Used by the bulk path to
    feed the HPS ingest after DUEL is done."""
    if limit <= 0:
        return []
    async with get_db_session() as session:
        rows = (await session.execute(
            select(DuelMapPool.beatmap_id)
            .order_by(DuelMapPool.id.desc())
            .limit(limit)
        )).scalars().all()
    return [int(b) for b in rows]


async def _queue_file_url_import(
    *,
    message: types.Message,
    target: ImportTarget,
    pools: tuple[PoolName, ...],
    osu_api_client,
) -> None:
    """Download an archive URL and start the bulk-import flow.

    Each /import file-URL emits its own status message — that's the channel
    the worker writes progress / final result into. Raises on download or
    scrape failure so the outer handler can surface it in the summary.
    """
    from bot.handlers.admin.duel_pool import (
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

    _cleanup_stale_imports()
    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        raise RuntimeError(
            f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS})"
        )

    # Page → direct URL (+ any auth headers) if needed: MediaFire scrape,
    # GoFile guest-token flow, etc. Direct links return empty headers.
    try:
        direct_url, dl_headers = await resolve_file_url(
            target.scrape, target.download_url or target.raw,
        )
    except FileUrlResolveError as e:
        raise RuntimeError(str(e)) from e

    # Friendly progress message — one per URL.
    label = target.raw if len(target.raw) <= 60 else target.raw[:57] + "…"
    wait = await message.answer(
        f"Скачиваю по ссылке: <code>{escape_html(label)}</code>",
        parse_mode="HTML",
    )

    try:
        tmp_path, size = await _download_url_to_import_file(
            direct_url, max_bytes=MAX_IMPORT_FILE_SIZE,
            headers=dl_headers or None,
        )
    except Exception as e:
        await wait.edit_text(
            f"Не удалось скачать файл:\n<code>{escape_html(str(e))}</code>",
            parse_mode="HTML",
        )
        # Don't re-raise — error already reported to user; outer summary
        # will still mention the file. We surface via the wait msg here
        # because it carries the most context (URL).
        raise

    # Filename guess for the queue entry — last path segment, fallback to URL.
    from urllib.parse import urlparse as _urlparse
    fname = (_urlparse(direct_url).path.rsplit("/", 1)[-1] or "download.zip")
    if not (fname.lower().endswith(".zip") or fname.lower().endswith(".osz")):
        fname = fname + ".zip"

    slot_id = _register_import(message.from_user.id, tmp_path, fname, size)
    # .7z is opaque to the zip-based counter — it's unpacked at import time.
    from services.map_import.multi_volume import sniff_archive_kind
    if sniff_archive_kind(tmp_path) == "7z":
        counts_line = "Формат: <b>.7z</b> (карты посчитаю после распаковки)"
    else:
        osz_count, osu_count = _count_osu_files(tmp_path)
        counts_line = (
            f"Архивов .osz: <b>{osz_count}</b>   Карт .osu: <b>{osu_count}</b>"
        )

    # Auto-confirm: URL imports skip the "preview + confirm" step because
    # the user already gave us the URL — adding a button just adds latency.
    await wait.edit_text(
        f"<b>Импорт по ссылке стартует</b>\n\n"
        f"Источник: <code>{escape_html(label)}</code>\n"
        f"Файл: <b>{escape_html(fname)}</b>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"{counts_line}\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Пулы: <b>{'+'.join(pools)}</b>",
        parse_mode="HTML",
    )

    # Spawn the worker — same body as the /import-document confirm path.
    _import_queue[slot_id]["status"] = "queued"
    asyncio.create_task(_run_bulk_import_worker(
        slot_id=slot_id, pools=pools, status_msg=wait,
        osu_api_client=osu_api_client,
    ))


async def _run_bulk_import_worker(
    *,
    slot_id: str,
    pools: tuple[PoolName, ...],
    status_msg: types.Message,
    osu_api_client,
) -> None:
    """Pull a queued import slot through the DUEL bulk-import pipeline and
    (optionally) re-feed beatmap_ids into HPS. Shared by the /import URL
    path and the document-confirm callback below."""
    from bot.handlers.admin.duel_pool import (
        _cleanup_import_file,
        _get_import_semaphore,
        _import_queue,
    )

    slot = _import_queue.get(slot_id)
    if not slot:
        return

    result: dict = {"added": 0, "skipped": 0, "failed": 0, "errors": []}
    try:
        async with _get_import_semaphore():
            if slot_id not in _import_queue:
                return
            slot["status"] = "running"
            try:
                await status_msg.edit_text(
                    f"<b>Импортирую карты…</b>\n"
                    f"Файл: <b>{escape_html(slot['filename'])}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

            from services.duel.bulk_import import import_from_file
            from services.map_import.multi_volume import (
                normalize_single_archive,
                sniff_archive_kind,
            )

            # The bulk-importer only reads .zip/.osz. A single .7z download
            # must be extracted + re-zipped first (.zip/.osz pass through).
            if sniff_archive_kind(slot["file_path"]) == "7z":
                try:
                    await status_msg.edit_text(
                        f"<b>Распаковываю .7z…</b> (может занять пару минут)\n"
                        f"Файл: <b>{escape_html(slot['filename'])}</b>",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            import_path, cleanup_dir = await normalize_single_archive(
                slot["file_path"]
            )
            if cleanup_dir:
                # Record so the stale-cleanup safety net won't sweep this
                # extraction while it's in flight; the finally removes it.
                slot["extract_dir"] = cleanup_dir
            try:
                result = await import_from_file(import_path, osu_api_client)
            finally:
                if cleanup_dir:
                    import shutil as _shutil
                    _shutil.rmtree(cleanup_dir, ignore_errors=True)

            if "hps" in pools:
                added_ids = await _collect_recently_added_duel_ids(
                    result.get("added", 0)
                )
                if added_ids:
                    reports = await ingest_many(
                        osu_api_client, added_ids,
                        pools=("hps",), concurrency=_BATCH_CONCURRENCY,
                    )
                    hps_added = sum(
                        1 for r in reports for o in r.outcomes
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
        f"✅ DUEL добавлено: <b>{added}</b>",
    ]
    if "hps" in pools:
        lines.append(f"✅ HPS добавлено: <b>{hps_added}</b>")
    lines += [
        f"⏭ Пропущено (DUEL): <b>{skipped}</b>",
        f"❌ Ошибок: <b>{failed}</b>",
    ]
    if result.get("errors"):
        lines.append("\nПервые ошибки:")
        for e in result["errors"]:
            lines.append(f"  • {escape_html(str(e)[:120])}")
    try:
        await status_msg.edit_text("\n".join(lines), parse_mode="HTML")
    except Exception:
        pass
