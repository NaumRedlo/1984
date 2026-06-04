import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from aiogram import Router, types, F
from sqlalchemy import select, func, or_

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_duel_pool")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

# ─── DUEL Map Pool Admin Commands ─────────────────────────────────────────────

@router.message(TextTriggerFilter("dueladdmap"))
async def cmd_duel_add_map(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """dueladdmap <beatmap_id> — fetch objective metadata and add to DUEL pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>dueladdmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    wait = await message.answer(f"Загружаю карту {beatmap_id}...")

    try:
        from db.models.duel_map_pool import DuelMapPool
        from services.duel.map_pool import add_map_to_pool

        # Check if already in pool
        async with get_db_session() as session:
            existing = (await session.execute(
                select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()

        if existing:
            await wait.edit_text(
                f"Карта <b>{beatmap_id}</b> уже в пуле: "
                f"{escape_html(existing.artist)} - {escape_html(existing.title)} "
                f"[{escape_html(existing.version)}] ({existing.star_rating:.2f}★)",
                parse_mode="HTML",
            )
            return

        entry = await add_map_to_pool(osu_api_client, beatmap_id)
        if not entry:
            await wait.edit_text(f"Карта {beatmap_id} не найдена в osu! API.")
            return

        mins, secs = divmod(int(entry.length or 0), 60)
        await wait.edit_text(
            f"✅ <b>Карта добавлена в DUEL пул</b>\n\n"
            f"<b>{escape_html(entry.artist)} - {escape_html(entry.title)}</b> "
            f"[{escape_html(entry.version)}]\n"
            f"⭐ {entry.star_rating:.2f}  ·  {(entry.bpm or 0):.0f} BPM  ·  "
            f"{mins}:{secs:02d}  ·  combo {entry.max_combo or 0}×\n"
            f"CS {entry.cs or 0:g}  ·  AR {entry.ar or 0:g}  ·  "
            f"OD {entry.od or 0:g}  ·  HP {entry.hp_drain or 0:g}",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"dueladdmap error for {beatmap_id}: {e}", exc_info=True)
        await wait.edit_text(f"Ошибка: {e}")


@router.message(TextTriggerFilter("duelremovemap"))
async def cmd_duel_remove_map(message: types.Message, trigger_args: TriggerArgs):
    """duelremovemap <beatmap_id> — disable map in DUEL pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer("Использование: <code>duelremovemap &lt;beatmap_id&gt;</code>", parse_mode="HTML")
        return

    beatmap_id = int(raw)
    from db.models.duel_map_pool import DuelMapPool
    async with get_db_session() as session:
        entry = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            await message.answer(f"Карта {beatmap_id} не найдена в пуле.")
            return
        entry.enabled = False
        await session.commit()

    await message.answer(f"Карта {beatmap_id} отключена из DUEL пула.")


@router.message(TextTriggerFilter("whois"))
async def cmd_whois(message: types.Message, trigger_args: TriggerArgs):
    """whois <user_id_or_tg_id> — show user info by internal User.id or telegram_id.

    Useful when the OAuth/token logs print '_id=N' and you need to figure out
    who that is and how to message them.
    """
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>whois &lt;user_id или telegram_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target = int(raw)
    from db.models.oauth_token import OAuthToken

    async with get_db_session() as session:
        # Try by User.id first; fall back to telegram_id (large positive numbers)
        user = (await session.execute(
            select(User).where(User.id == target)
        )).scalar_one_or_none()
        if not user:
            user = (await session.execute(
                select(User).where(User.telegram_id == target)
            )).scalar_one_or_none()

        if not user:
            await message.answer(f"Пользователь с id={target} не найден ни в User.id, ни в telegram_id.")
            return

        token = (await session.execute(
            select(OAuthToken).where(OAuthToken.user_id == user.id)
        )).scalar_one_or_none()

    last_seen = user.last_seen_at.strftime("%Y-%m-%d %H:%M") if getattr(user, "last_seen_at", None) else "—"
    if token:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        exp = token.token_expiry
        if exp and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)

        if not exp:
            oauth_line = "⚠️ Привязан, срок неизвестен"
        elif now > exp:
            oauth_line = f"🔴 Истёк: <code>{exp.strftime('%Y-%m-%d %H:%M')}</code> — нужен relink"
        else:
            oauth_line = f"✅ Привязан, истекает: <code>{exp.strftime('%Y-%m-%d %H:%M')}</code>"
    else:
        oauth_line = "❌ <b>Нет токена</b> — нужен relink"

    text = (
        f"<b>User.id:</b>      <code>{user.id}</code>\n"
        f"<b>telegram_id:</b>  <code>{user.telegram_id}</code>\n"
        f"<b>osu! ник:</b>     <b>{escape_html(user.osu_username or '—')}</b> "
        f"(osu_id <code>{user.osu_user_id or '—'}</code>)\n"
        f"<b>OAuth:</b>        {oauth_line}\n"
        f"<b>Last seen:</b>    <code>{last_seen}</code>\n\n"
        f"📨 Написать: <a href=\"tg://user?id={user.telegram_id}\">открыть профиль</a>\n"
        f"🔁 Прислать DM с просьбой relink: <code>notifyrelink {user.id}</code>"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


@router.message(TextTriggerFilter("notifyrelink"))
async def cmd_notify_relink(message: types.Message, trigger_args: TriggerArgs):
    """notifyrelink <user_id_or_tg_id> — DM the user and ask them to re-link osu!.

    Used after OAuth permanent failures (duel-ml token_manager logs
    'Refresh token rejected for user_id=N — deleting row, user must re-link').
    """
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>notifyrelink &lt;user_id или telegram_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target = int(raw)

    async with get_db_session() as session:
        user = (await session.execute(
            select(User).where(User.id == target)
        )).scalar_one_or_none()
        if not user:
            user = (await session.execute(
                select(User).where(User.telegram_id == target)
            )).scalar_one_or_none()
        if not user:
            await message.answer(f"Пользователь с id={target} не найден.")
            return
        if not user.telegram_id:
            await message.answer(f"У {user.osu_username} нет telegram_id — невозможно написать в личку.")
            return

    dm_text = (
        f"⚠️ <b>Привязка osu! аккаунта истекла</b>\n\n"
        f"Привет, <b>{escape_html(user.osu_username)}</b>! "
        f"Похоже, твой osu! токен был отозван (например, ты разлогинился на osu.ppy.sh "
        f"или сменил пароль), и бот больше не может получать твои скоры.\n\n"
        f"Перепривяжи аккаунт командой:\n"
        f"<code>relink</code>\n\n"
        f"Бот пришлёт ссылку для авторизации в osu!. "
        f"<b>Прогресс, рейтинги и история сохранятся</b> — это не unlink, "
        f"всё что было — останется. После этого всё снова заработает: дуэли, "
        f"профиль, recent."
    )

    try:
        await message.bot.send_message(
            user.telegram_id, dm_text, parse_mode="HTML", disable_web_page_preview=True,
        )
        await message.answer(
            f"✅ DM отправлен <b>{escape_html(user.osu_username)}</b> "
            f"(tg <code>{user.telegram_id}</code>).",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.answer(
            f"❌ Не удалось написать в личку <b>{escape_html(user.osu_username)}</b>: "
            f"<code>{escape_html(str(e))}</code>\n\n"
            f"Скорее всего, пользователь не начинал диалог с ботом или заблокировал его. "
            f"Напиши вручную: <a href=\"tg://user?id={user.telegram_id}\">открыть профиль</a>",
            parse_mode="HTML",
        )


@router.message(TextTriggerFilter("whereami"))
async def cmd_whereami(message: types.Message):
    """whereami — print chat_id and message_thread_id of the current location.

    Useful for picking the value to set in DUEL_THREAD_ID env var.
    """
    chat_id   = message.chat.id
    thread_id = message.message_thread_id
    is_topic  = bool(getattr(message, "is_topic_message", False))
    lines = [
        f"<b>chat_id:</b>          <code>{chat_id}</code>",
        f"<b>message_thread_id:</b> <code>{thread_id if thread_id is not None else '— (General / non-forum)'}</code>",
        f"<b>is_topic_message:</b>  <code>{is_topic}</code>",
    ]
    if thread_id is not None:
        lines.append(
            f"\nЧтобы дуэли всегда публиковались сюда, добавь в <code>.env</code>:\n"
            f"<code>DUEL_THREAD_ID={thread_id}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("duelenable"))
async def cmd_duel_enable_map(message: types.Message, trigger_args: TriggerArgs):
    """duelenable <beatmap_id> — re-enable a previously disabled DUEL pool map."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>duelenable &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    from db.models.duel_map_pool import DuelMapPool
    async with get_db_session() as session:
        entry = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            await message.answer(f"Карта {beatmap_id} не найдена в пуле.")
            return
        was_enabled = entry.enabled
        entry.enabled = True
        await session.commit()

    if was_enabled:
        await message.answer(f"Карта {beatmap_id} уже была включена.")
    else:
        await message.answer(f"✅ Карта {beatmap_id} снова в пуле.")


_DUEL_BROKEN_PER_PAGE = 15


async def _duel_broken_collect() -> tuple[
    list[tuple["DuelMapPool", list[str]]],  # type: ignore  # noqa: F821
    list["DuelMapPool"],                     # type: ignore  # noqa: F821
]:
    """Scan the pool and split entries into (broken, disabled-but-clean)."""
    from db.models.duel_map_pool import DuelMapPool
    from services.duel.map_pool import map_is_broken
    from sqlalchemy import or_

    async with get_db_session() as session:
        candidates = (await session.execute(
            select(DuelMapPool).where(
                or_(
                    DuelMapPool.star_rating <= 0,
                    DuelMapPool.api_aim_diff.is_(None),
                    DuelMapPool.f_note_count.is_(None),
                    DuelMapPool.enabled == False,  # noqa: E712
                )
            ).order_by(DuelMapPool.star_rating)
        )).scalars().all()

    broken: list[tuple[DuelMapPool, list[str]]] = []
    disabled_only: list[DuelMapPool] = []
    for m in candidates:
        is_b, reasons = map_is_broken(m)
        if is_b:
            broken.append((m, reasons))
        elif not m.enabled:
            disabled_only.append(m)
    return broken, disabled_only


async def _duel_broken_render(
    page: int, section: str = "broken"
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render one page of `duelbroken` for the given section ('broken'|'disabled')."""
    broken, disabled_only = await _duel_broken_collect()

    if section not in ("broken", "disabled"):
        section = "broken"

    items_broken = broken
    items_disabled = disabled_only

    if section == "broken":
        items: list = items_broken
        per = _DUEL_BROKEN_PER_PAGE
        header_emoji = "⚠️"
        header_label = "Битые карты"
    else:
        items = items_disabled
        per = _DUEL_BROKEN_PER_PAGE
        header_emoji = "❌"
        header_label = "Отключённые, но целые"

    total_items = len(items)
    pages = max(1, (total_items + per - 1) // per)
    page = max(1, min(page, pages))
    start = (page - 1) * per
    chunk = items[start:start + per]

    lines = [
        "<b>DUEL — диагностика пула</b>",
        f"⚠️ Битых: <b>{len(items_broken)}</b>   "
        f"❌ Отключённых: <b>{len(items_disabled)}</b>",
        "",
        f"{header_emoji} <b>{header_label} ({total_items}):</b>"
        + (f"  стр. {page}/{pages}" if total_items else ""),
    ]

    if not chunk:
        lines.append("<i>— пусто —</i>")
    else:
        if section == "broken":
            for m, reasons in chunk:
                tag = ", ".join(reasons)
                lines.append(
                    f"<code>{m.beatmap_id}</code> {escape_html(m.artist)} - "
                    f"{escape_html(m.title)} [{escape_html(m.version)}] · {tag}"
                )
        else:
            for m in chunk:
                lines.append(
                    f"<code>{m.beatmap_id}</code> {escape_html(m.artist)} - "
                    f"{escape_html(m.title)} [{escape_html(m.version)}]"
                )

    if section == "broken" and items_broken:
        lines += [
            "",
            "Чинить: <code>duelrefresh &lt;id&gt;</code> "
            "или <code>duelrefresh broken</code> для пакетной починки.",
        ]
    elif section == "disabled" and items_disabled:
        lines += ["", "Включить: <code>duelenable &lt;id&gt;</code>"]

    # ── Keyboard ─────────────────────────────────────────────────────────────
    nav_row: list[types.InlineKeyboardButton] = []
    if pages > 1:
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(
                text="◀", callback_data=f"duelbroken:page:{section}:{page - 1}"
            ))
        nav_row.append(types.InlineKeyboardButton(
            text=f"{page}/{pages}", callback_data="duelbroken:noop"
        ))
        if page < pages:
            nav_row.append(types.InlineKeyboardButton(
                text="▶", callback_data=f"duelbroken:page:{section}:{page + 1}"
            ))

    other = "disabled" if section == "broken" else "broken"
    other_count = len(items_disabled) if section == "broken" else len(items_broken)
    other_label = (
        f"❌ Отключённые ({other_count})"
        if section == "broken" else f"⚠️ Битые ({other_count})"
    )
    switch_row = [types.InlineKeyboardButton(
        text=other_label, callback_data=f"duelbroken:page:{other}:1"
    )]

    rows: list[list[types.InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.append(switch_row)
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)

    return "\n".join(lines), kb


@router.message(TextTriggerFilter("duelbroken"))
async def cmd_duel_broken(message: types.Message, trigger_args: TriggerArgs):
    """duelbroken [page] — list broken / disabled DUEL pool maps with pagination."""
    args = (trigger_args.args or "").strip().lower()
    section = "broken"
    page = 1
    if args:
        for token in args.split():
            if token in ("broken", "disabled"):
                section = token
            elif token.isdigit():
                page = max(1, int(token))

    broken, disabled_only = await _duel_broken_collect()
    if not broken and not disabled_only:
        await message.answer("✅ В пуле нет карт с проблемами.")
        return

    text, kb = await _duel_broken_render(page, section)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("duelbroken:"))
async def on_duel_broken_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) >= 2 and parts[1] == "noop":
        await callback.answer()
        return
    # Format: duelbroken:page:<section>:<n>
    if len(parts) >= 4 and parts[1] == "page":
        section = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            page = 1
        text, kb = await _duel_broken_render(page, section)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("duelbroken: edit_text failed", exc_info=True)
        await callback.answer()
        return
    await callback.answer()


# ── Post-refresh actions ─────────────────────────────────────────────────────
# After a `duelrefresh broken` batch, give the admin a chance to disable or
# delete the maps that are still broken, instead of leaving them dangling.

# slot_id -> {tg_id: int, bad_ids: list[int], created_at: datetime}
_refresh_slots: dict[str, dict] = {}


def _register_refresh_slot(tg_id: int, bad_ids: list[int]) -> str:
    """Stash the post-refresh bad_ids list under a short slot id."""
    slot_id = uuid4().hex[:8]
    _refresh_slots[slot_id] = {
        "tg_id": tg_id,
        "bad_ids": list(bad_ids),
        "created_at": datetime.utcnow(),
    }
    # Lazy cleanup: drop slots older than 1h to avoid unbounded growth.
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for sid, data in list(_refresh_slots.items()):
        if data.get("created_at") and data["created_at"] < cutoff:
            _refresh_slots.pop(sid, None)
    return slot_id


def _confirm_fix_keyboard(slot: str, n: int) -> types.InlineKeyboardMarkup:
    """disable / delete / cancel keyboard, backed by the duelrefresh:fix handler.

    Shared by the post-refresh prompt and the duelpool bulk-action prompt so
    both go through the same vetted apply path (`on_duel_refresh_fix`).
    """
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=f"❌ Отключить {n}", callback_data=f"duelrefresh:fix:disable:{slot}"),
            types.InlineKeyboardButton(
                text=f"🗑 Удалить {n}", callback_data=f"duelrefresh:fix:delete:{slot}"),
        ],
        [types.InlineKeyboardButton(
            text="Оставить как есть", callback_data=f"duelrefresh:fix:cancel:{slot}")],
    ])


@router.callback_query(F.data.startswith("duelrefresh:fix:"))
async def on_duel_refresh_fix(callback: types.CallbackQuery):
    """Handle 'disable / delete / cancel' actions for the post-refresh prompt."""
    from db.models.duel_map_pool import DuelMapPool

    parts = callback.data.split(":")
    # duelrefresh:fix:<action>:<slot>
    if len(parts) != 4:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[2]
    slot_id = parts[3]

    slot = _refresh_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла — запусти duelrefresh broken заново.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("duelrefresh:fix expired slot — edit_reply_markup failed", exc_info=True)
        return

    if callback.from_user.id != slot["tg_id"]:
        await callback.answer("Это не твой запрос.", show_alert=True)
        return

    bad_ids: list[int] = slot["bad_ids"]

    if action == "cancel":
        _refresh_slots.pop(slot_id, None)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("duelrefresh:fix cancel — edit_reply_markup failed", exc_info=True)
        await callback.answer("Оставлено как есть.")
        return

    if action not in ("disable", "delete"):
        await callback.answer("Неизвестное действие.", show_alert=True)
        return

    if not bad_ids:
        _refresh_slots.pop(slot_id, None)
        await callback.answer("Нечего обрабатывать — список пуст.", show_alert=True)
        return

    # ── Apply the action ────────────────────────────────────────────────────
    affected = 0
    async with get_db_session() as session:
        rows = (await session.execute(
            select(DuelMapPool).where(DuelMapPool.beatmap_id.in_(bad_ids))
        )).scalars().all()
        if action == "disable":
            for entry in rows:
                if entry.enabled:
                    entry.enabled = False
                    affected += 1
        else:  # delete
            for entry in rows:
                await session.delete(entry)
                affected += 1
        await session.commit()

    _refresh_slots.pop(slot_id, None)

    verb = "отключено" if action == "disable" else "удалено"
    suffix_lines = [
        "",
        f"<b>Действие применено:</b> {verb} <b>{affected}</b> карт.",
    ]
    new_text = (callback.message.html_text or callback.message.text or "") + "\n" + "\n".join(suffix_lines)
    try:
        await callback.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
    except Exception:
        # Fallback: just drop the keyboard and post a follow-up.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("duelrefresh:fix — edit_reply_markup failed", exc_info=True)
        await callback.message.answer("\n".join(suffix_lines), parse_mode="HTML")
    logger.info(f"duelrefresh:fix admin={callback.from_user.id} action={action} affected={affected}")
    await callback.answer(f"{verb}: {affected}")


@router.message(TextTriggerFilter("duelrefresh"))
async def cmd_duel_refresh(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """
    duelrefresh <beatmap_id>   — re-pull a single map from the osu! API + CDN.
    duelrefresh broken          — refresh every map flagged by duelbroken.
    duelrefresh disabled        — re-enable every disabled map and re-pull.
    duelrefresh missingcombo    — backfill every map with no max_combo.

    Useful when maps were imported while the API was misbehaving and ended
    up with star_rating=0, missing parsed features, missing max_combo, etc.
    Retries each network call up to 3 times before giving up.
    """
    raw = (trigger_args.args or "").strip().lower()
    if not raw:
        await message.answer(
            "Использование:\n"
            "<code>duelrefresh &lt;id&gt;</code> — одна карта\n"
            "<code>duelrefresh broken</code> — все битые\n"
            "<code>duelrefresh disabled</code> — все отключённые\n"
            "<code>duelrefresh missingcombo</code> — добить max_combo",
            parse_mode="HTML",
        )
        return

    from db.models.duel_map_pool import DuelMapPool
    from services.duel.map_pool import refresh_map, map_is_broken
    from sqlalchemy import or_
    import asyncio

    # ── Single-map mode ──────────────────────────────────────────────────────
    if raw.isdigit():
        beatmap_id = int(raw)
        wait = await message.answer(f"🔄 Обновляю карту {beatmap_id}…")
        result = await refresh_map(osu_api_client, beatmap_id, re_enable=True)

        st = result["status"]
        emoji = {"ok": "✅", "partial": "⚠️", "no_data": "❌", "not_found": "🚫", "error": "❌"}.get(st, "❓")
        reasons = ", ".join(result["reasons"]) or "—"
        updated = ", ".join(result["updated"]) or "—"
        text = (
            f"{emoji} <b>Карта {beatmap_id}</b>: {result['message']}\n\n"
            f"Было битым: <code>{reasons}</code>\n"
            f"Обновлено:  <code>{updated}</code>"
        )
        try:
            await wait.edit_text(text, parse_mode="HTML")
        except Exception:
            await message.answer(text, parse_mode="HTML")
        return

    # ── Batch modes ──────────────────────────────────────────────────────────
    if raw not in ("broken", "disabled", "missingcombo"):
        await message.answer(
            "Неизвестный режим. Доступно: <id>, broken, disabled, missingcombo",
            parse_mode="HTML",
        )
        return

    async with get_db_session() as session:
        if raw == "disabled":
            candidates = (await session.execute(
                select(DuelMapPool).where(DuelMapPool.enabled == False)
            )).scalars().all()
        elif raw == "missingcombo":
            candidates = (await session.execute(
                select(DuelMapPool).where(
                    or_(DuelMapPool.max_combo.is_(None), DuelMapPool.max_combo == 0)
                )
            )).scalars().all()
        else:  # broken
            candidates = (await session.execute(
                select(DuelMapPool).where(
                    or_(
                        DuelMapPool.star_rating <= 0,
                        DuelMapPool.api_aim_diff.is_(None),
                        DuelMapPool.f_note_count.is_(None),
                    )
                )
            )).scalars().all()

    if raw == "broken":
        # Verify against map_is_broken (the SQL filter is permissive).
        candidates = [m for m, in [(m,) for m in candidates] if map_is_broken(m)[0]]

    if not candidates:
        await message.answer("Нечего обновлять — пул чистый.")
        return

    # Backfilling combo shouldn't silently re-enable maps an admin disabled.
    re_enable = raw != "missingcombo"

    wait = await message.answer(f"🔄 Обновляю {len(candidates)} карт…")

    counts = {"ok": 0, "partial": 0, "no_data": 0, "not_found": 0, "error": 0}
    bad_ids: list[int] = []

    for idx, m in enumerate(candidates, 1):
        if idx % 10 == 0:
            try:
                await wait.edit_text(
                    f"🔄 {idx}/{len(candidates)}…\n"
                    f"✅ {counts['ok']}  ⚠️ {counts['partial']}  ❌ {counts['no_data'] + counts['error']}"
                )
            except Exception:
                logger.debug("duelrefresh: progress edit_text failed", exc_info=True)
        try:
            r = await refresh_map(osu_api_client, m.beatmap_id, re_enable=re_enable)
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            if r["status"] in ("no_data", "not_found", "error", "partial"):
                bad_ids.append(m.beatmap_id)
        except Exception as e:
            logger.error(f"duelrefresh batch error for {m.beatmap_id}: {e}", exc_info=True)
            counts["error"] += 1
            bad_ids.append(m.beatmap_id)
        await asyncio.sleep(0.2)

    text_lines = [
        "<b>Обновление завершено</b>\n",
        f"✅ Полностью:  <b>{counts['ok']}</b>",
        f"⚠️ Частично:    <b>{counts['partial']}</b>",
        f"❌ Без данных: <b>{counts['no_data']}</b>",
        f"🚫 Не найдено: <b>{counts['not_found']}</b>",
        f"❌ Ошибок:      <b>{counts['error']}</b>",
    ]

    kb: types.InlineKeyboardMarkup | None = None
    if bad_ids:
        sample = ", ".join(f"<code>{i}</code>" for i in bad_ids[:10])
        more = f" (+{len(bad_ids) - 10})" if len(bad_ids) > 10 else ""
        text_lines.append(f"\nПроблемные ({len(bad_ids)}): {sample}{more}")
        text_lines.append(
            "\nЧто сделать с картами, которые остались битыми?"
        )
        slot = _register_refresh_slot(message.from_user.id, bad_ids)
        kb = _confirm_fix_keyboard(slot, len(bad_ids))

    try:
        await wait.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)


_DUEL_POOL_PER_PAGE = 15


# duelpool view state — a sort key + filter encoded compactly into callback_data
# (duelpool:nav:<sort>:<filt>:<arg>:<page>) so sort/filter/paging buttons survive
# message edits with no server-side session to expire.
_POOL_SORTS = ("sr", "nm", "ln")        # star rating | name (artist/title) | length
_POOL_FILTERS = ("all", "tv", "sh")     # all | TV-size markers | short (length < arg)
_SHORT_DEFAULT_SECS = 90                 # anime "TV size" cuts run ~1:30

# Title/version substrings that flag a TV-size / short-cut diff. Matched as
# case-insensitive LIKE so the filter stays in SQL (paged, no full table scan).
_TV_MARKERS = (
    "tv size", "tv-size", "tvsize", "tv. size", "tv ver", "tv version",
    "(tv)", "short ver", "short version", "short edit", "short cut",
)


def _fmt_duration(secs) -> str:
    s = int(secs or 0)
    return f"{s // 60}:{s % 60:02d}" if s > 0 else "—"


def _pool_order_by(sort: str):
    from db.models.duel_map_pool import DuelMapPool
    if sort == "nm":
        return (func.lower(DuelMapPool.artist), func.lower(DuelMapPool.title))
    if sort == "ln":
        # nulls last, then shortest first
        return (DuelMapPool.length.is_(None), DuelMapPool.length.asc())
    return (DuelMapPool.star_rating.asc(),)


def _pool_filter_clauses(filt: str, arg: int) -> list:
    from db.models.duel_map_pool import DuelMapPool
    if filt == "sh":
        secs = arg if arg > 0 else _SHORT_DEFAULT_SECS
        return [DuelMapPool.length.isnot(None), DuelMapPool.length < secs]
    if filt == "tv":
        title_l = func.lower(DuelMapPool.title)
        ver_l = func.lower(DuelMapPool.version)
        ors = []
        for mk in _TV_MARKERS:
            ors.append(title_l.like(f"%{mk}%"))
            ors.append(ver_l.like(f"%{mk}%"))
        return [or_(*ors)]
    return []


def _parse_pool_args(raw: str) -> tuple[str, str, int, int]:
    """Map free-form duelpool args → (sort, filt, arg, page). Last token wins
    per dimension; a bare number is the page. `tv`/`short` imply length sort."""
    sort, filt, arg, page = "sr", "all", 0, 1
    toks = (raw or "").lower().split()
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.isdigit():
            page = max(1, int(t))
        elif t in ("sr", "stars", "star"):
            sort = "sr"
        elif t in ("name", "имя", "nm", "alpha", "az", "abc"):
            sort = "nm"
        elif t in ("len", "length", "длина", "ln", "time"):
            sort = "ln"
        elif t in ("tv", "tvsize", "tv-size"):
            filt, sort = "tv", "ln"
        elif t in ("short", "короткие", "sh", "shorts"):
            filt, sort = "sh", "ln"
            if i + 1 < len(toks) and toks[i + 1].isdigit():
                arg = int(toks[i + 1]); i += 1
        i += 1
    return sort, filt, arg, page


def _pool_keyboard(
    sort: str, filt: str, arg: int, page: int, pages: int, matched: int,
) -> types.InlineKeyboardMarkup:
    def cd(s=sort, f=filt, a=arg, p=page) -> str:
        return f"duelpool:nav:{s}:{f}:{a}:{p}"

    def mark(active: bool) -> str:
        return "• " if active else ""

    rows = [
        [
            types.InlineKeyboardButton(text=mark(sort == "sr") + "⭐ SR", callback_data=cd(s="sr", p=1)),
            types.InlineKeyboardButton(text=mark(sort == "nm") + "🔤 Имя", callback_data=cd(s="nm", p=1)),
            types.InlineKeyboardButton(text=mark(sort == "ln") + "⏱ Длина", callback_data=cd(s="ln", p=1)),
        ],
        [
            types.InlineKeyboardButton(text=mark(filt == "all") + "Все", callback_data=cd(f="all", a=0, p=1)),
            types.InlineKeyboardButton(text=mark(filt == "tv") + "📺 TV", callback_data=cd(f="tv", a=0, p=1)),
            types.InlineKeyboardButton(
                text=mark(filt == "sh") + "⏱ Короткие",
                callback_data=cd(f="sh", a=arg or _SHORT_DEFAULT_SECS, p=1)),
        ],
    ]

    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton(text="◀", callback_data=cd(p=page - 1)))
    if page < pages:
        nav.append(types.InlineKeyboardButton(text="▶", callback_data=cd(p=page + 1)))
    if nav:
        rows.append(nav)

    # Bulk action only on a narrowing filter — never "wipe everything".
    if filt != "all" and matched > 0:
        rows.append([types.InlineKeyboardButton(
            text=f"🗑 Обработать все ({matched})",
            callback_data=f"duelpool:wipe:{sort}:{filt}:{arg}",
        )])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _duel_pool_render(
    sort: str = "sr", filt: str = "all", arg: int = 0, page: int = 1,
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render one page of the DUEL pool for the given sort+filter view.

    sort ∈ {sr, nm, ln} · filt ∈ {all, tv, sh} · arg = short-threshold seconds.
    All state rides in callback_data, so the buttons stay stateless.
    """
    from db.models.duel_map_pool import DuelMapPool

    sort = sort if sort in _POOL_SORTS else "sr"
    filt = filt if filt in _POOL_FILTERS else "all"
    clauses = _pool_filter_clauses(filt, arg)

    async with get_db_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(DuelMapPool)
        )).scalar() or 0

        count_q = select(func.count()).select_from(DuelMapPool)
        if clauses:
            count_q = count_q.where(*clauses)
        matched = (await session.execute(count_q)).scalar() or 0

        pages = max(1, (matched + _DUEL_POOL_PER_PAGE - 1) // _DUEL_POOL_PER_PAGE)
        page = max(1, min(page, pages))

        q = select(DuelMapPool)
        if clauses:
            q = q.where(*clauses)
        q = q.order_by(*_pool_order_by(sort)).offset(
            (page - 1) * _DUEL_POOL_PER_PAGE
        ).limit(_DUEL_POOL_PER_PAGE)
        maps = (await session.execute(q)).scalars().all()

    from services.duel.map_pool import map_is_broken
    sort_label = {"sr": "⭐SR", "nm": "🔤имя", "ln": "⏱длина"}[sort]
    if filt == "tv":
        scope = "📺 TV-size"
    elif filt == "sh":
        scope = f"⏱ короче {arg or _SHORT_DEFAULT_SECS}s"
    else:
        scope = f"{total} всего"

    lines = [
        f"<b>DUEL пул</b> — {scope} · найдено <b>{matched}</b> · "
        f"сорт {sort_label}  (стр. {page}/{pages})\n"
    ]
    for m in maps:
        broken, _ = map_is_broken(m)
        status = "❌" if not m.enabled else ("⚠️" if broken else "✅")
        sr_str = f"⭐{m.star_rating:.1f}" if (m.star_rating or 0) > 0 else "⭐<i>—</i>"
        lines.append(
            f"{status} <code>{m.beatmap_id}</code> {escape_html(m.artist)} - "
            f"{escape_html(m.title)} [{escape_html(m.version)}] "
            f"{sr_str} · ⏱{_fmt_duration(m.length)}"
        )
    if not maps:
        lines.append("<i>— ничего не найдено —</i>")

    return "\n".join(lines), _pool_keyboard(sort, filt, arg, page, pages, matched)


@router.message(TextTriggerFilter("duelpool", "duelp"))
async def cmd_duel_pool(message: types.Message, trigger_args: TriggerArgs):
    """duelpool [page|sort|filter] — list / sort / filter the DUEL pool.

    Sort:    duelp name | duelp len | duelp sr   (default ⭐SR)
    Filter:  duelp tv               — TV-size / short-cut diffs
             duelp short [secs]      — maps under N seconds (default 90)
    Page:    duelp 3
    Filtered views expose a 🗑 button to disable/delete all matches at once.
    """
    sort, filt, arg, page = _parse_pool_args(trigger_args.args or "")
    text, kb = await _duel_pool_render(sort, filt, arg, page)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("duelpool:nav:"))
async def on_duel_pool_nav(callback: types.CallbackQuery):
    # duelpool:nav:<sort>:<filt>:<arg>:<page>
    parts = callback.data.split(":")
    if len(parts) != 6:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    _, _, sort, filt, arg, page = parts
    text, kb = await _duel_pool_render(sort, filt, int(arg) if arg.isdigit() else 0,
                                       int(page) if page.isdigit() else 1)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        logger.debug("duelpool:nav — edit_text failed", exc_info=True)
    await callback.answer()


@router.callback_query(F.data.startswith("duelpool:page:"))
async def on_duel_pool_page(callback: types.CallbackQuery):
    # Back-compat for pre-upgrade messages still carrying duelpool:page:<n>.
    page = int(callback.data.split(":")[-1])
    text, kb = await _duel_pool_render("sr", "all", 0, page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("duelpool:wipe:"))
async def on_duel_pool_wipe(callback: types.CallbackQuery):
    """Stash every map matching the active filter, then offer disable/delete."""
    from db.models.duel_map_pool import DuelMapPool

    parts = callback.data.split(":")
    # duelpool:wipe:<sort>:<filt>:<arg>
    if len(parts) != 5:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    _, _, _sort, filt, arg = parts
    arg_n = int(arg) if arg.isdigit() else 0
    clauses = _pool_filter_clauses(filt, arg_n)
    if not clauses:
        await callback.answer("Фильтр не задан — отключено только для фильтров.", show_alert=True)
        return

    async with get_db_session() as session:
        ids = [r for (r,) in (await session.execute(
            select(DuelMapPool.beatmap_id).where(*clauses)
        )).all()]
    if not ids:
        await callback.answer("Под фильтр ничего не попало.", show_alert=True)
        return

    scope = "📺 TV-size" if filt == "tv" else f"короче {arg_n or _SHORT_DEFAULT_SECS}s"
    slot = _register_refresh_slot(callback.from_user.id, ids)
    sample = ", ".join(f"<code>{i}</code>" for i in ids[:10])
    more = f" (+{len(ids) - 10})" if len(ids) > 10 else ""
    text = (
        f"<b>Чистка пула</b> — фильтр: {scope}\n"
        f"Под фильтр попало <b>{len(ids)}</b> карт: {sample}{more}\n\n"
        f"• <b>Отключить</b> — убрать из активного пула (обратимо, <code>duelenable</code> вернёт)\n"
        f"• <b>Удалить</b> — стереть из БД совсем"
    )
    kb = _confirm_fix_keyboard(slot, len(ids))
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)
    logger.info(f"duelpool:wipe admin={callback.from_user.id} filt={filt} arg={arg_n} matched={len(ids)}")
    await callback.answer()


# ─── Import queue ─────────────────────────────────────────────────────────────

MAX_IMPORT_SLOTS = 5
MAX_RUNNING_IMPORTS = 1
# 25 GB cap — chosen for multi-volume 7z imports of large mappack archives.
# A single .zip file from Telegram still tops out at the bot-API 2 GB limit;
# this only applies to URL-based imports (direct/page links, multi-volume).
MAX_IMPORT_FILE_SIZE = 25 * 1024 * 1024 * 1024
IMPORT_TMP_DIR = "/tmp/project1984_duel_imports"
IMPORT_PENDING_TTL_SECONDS = 60 * 60
# slot_id -> {tg_id, file_path, filename, status, size, created_at}
_import_queue: dict[str, dict] = {}
# Pending previews: admin_tg_id -> file_path (legacy path)
_pending_imports: dict[int, str] = {}
_import_semaphore = None


def _get_import_semaphore():
    global _import_semaphore
    if _import_semaphore is None:
        import asyncio
        _import_semaphore = asyncio.Semaphore(MAX_RUNNING_IMPORTS)
    return _import_semaphore


def _fmt_bytes(n: int | None) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def _cleanup_import_file(path: str | None) -> None:
    if not path:
        return
    try:
        import os
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("import: temp cleanup failed", exc_info=True)


def _cleanup_stale_imports() -> None:
    """Remove expired pending import previews and orphan temp files."""
    import os
    now = datetime.utcnow()
    expired: list[str] = []
    for slot_id, slot in list(_import_queue.items()):
        if slot.get("status") not in ("pending", "queued"):
            continue
        created_at = slot.get("created_at")
        if not isinstance(created_at, datetime):
            continue
        if (now - created_at).total_seconds() > IMPORT_PENDING_TTL_SECONDS:
            expired.append(slot_id)

    for slot_id in expired:
        slot = _import_queue.pop(slot_id, None)
        if slot:
            _cleanup_import_file(slot.get("file_path"))

    try:
        import shutil
        os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
        cutoff = now.timestamp() - IMPORT_PENDING_TTL_SECONDS
        # Protect both the downloaded file and any in-flight .7z extraction
        # dir of every queued/running slot, so a concurrent /import that
        # triggers this sweep can't delete a job's files out from under it.
        active_paths: set = set()
        for slot in _import_queue.values():
            active_paths.add(slot.get("file_path"))
            active_paths.add(slot.get("extract_dir"))
        for name in os.listdir(IMPORT_TMP_DIR):
            path = os.path.join(IMPORT_TMP_DIR, name)
            if path in active_paths:
                continue
            try:
                # .7z extraction dirs (import7z_*): rmtree the whole tree.
                # A crashed import leaves a multi-GB dir the old file-only
                # sweep never caught (wrong prefix + os.remove on a dir).
                if name.startswith("import7z_") and os.path.isdir(path):
                    if os.path.getmtime(path) < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                    continue
                # Downloaded temp files: new ("import_") / legacy
                # ("duelimport_") prefixes so an in-flight upload during a
                # deploy isn't orphaned.
                if not (name.startswith("import_") or name.startswith("duelimport_")):
                    continue
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except FileNotFoundError:
                pass
    except Exception:
        logger.debug("import: stale temp cleanup failed", exc_info=True)


def _register_import(tg_id: int, file_path: str, filename: str, size: int = 0) -> str:
    import uuid
    slot_id = str(uuid.uuid4())[:8]
    _import_queue[slot_id] = {
        "tg_id": tg_id,
        "file_path": file_path,
        "filename": filename,
        "status": "pending",
        "size": int(size or 0),
        "created_at": datetime.utcnow(),
    }
    return slot_id


def _queue_position(slot_id: str) -> int:
    return list(_import_queue.keys()).index(slot_id) + 1 if slot_id in _import_queue else 0


async def _validate_public_import_url(url: str) -> str:
    """Validate import URL and reject localhost/private-network targets."""
    import asyncio
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("Разрешены только http/https ссылки.")
    if not parsed.hostname:
        raise RuntimeError("Некорректная ссылка: нет hostname.")
    if parsed.username or parsed.password:
        raise RuntimeError("Ссылки с username/password не поддерживаются.")

    host = parsed.hostname.strip().rstrip(".")
    if not host:
        raise RuntimeError("Некорректная ссылка: пустой hostname.")

    def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal and _is_blocked_ip(literal):
        raise RuntimeError("Ссылка ведёт на запрещённый адрес.")

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        raise RuntimeError("Не удалось разрешить hostname ссылки.")

    resolved_ips = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        if not sockaddr:
            continue
        ip_raw = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_raw)
        except ValueError:
            raise RuntimeError("Hostname ссылки разрешился в некорректный адрес.")
        resolved_ips.add(str(ip))
        if _is_blocked_ip(ip):
            raise RuntimeError("Ссылка ведёт во внутреннюю/локальную сеть.")

    if not resolved_ips:
        raise RuntimeError("Hostname ссылки не вернул IP-адресов.")
    return url


async def _download_url_to_import_file(
    url: str,
    max_bytes: int = MAX_IMPORT_FILE_SIZE,
    headers: dict | None = None,
) -> tuple[str, int]:
    import aiohttp as _aiohttp
    import os
    import shutil
    import tempfile
    from urllib.parse import urljoin

    # Keep this much free on the import volume so a download can't fill the disk
    # out from under the rest of the bot.
    DISK_RESERVE = 1024 * 1024 * 1024

    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
    suffix = ".osz" if url.lower().split("?", 1)[0].endswith(".osz") else ".zip"
    fd, tmp_path = tempfile.mkstemp(prefix="import_", suffix=suffix, dir=IMPORT_TMP_DIR)
    size = 0
    current_url = url
    redirects_left = 5
    try:
        with os.fdopen(fd, "wb") as f:
            # Session-level headers (e.g. GoFile's accountToken cookie) ride
            # along on every request, including the manual redirect hops below.
            async with _aiohttp.ClientSession(headers=headers or {}) as sess:
                while True:
                    current_url = await _validate_public_import_url(current_url)
                    async with sess.get(
                        current_url,
                        timeout=_aiohttp.ClientTimeout(total=600),
                        allow_redirects=False,
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308):
                            if redirects_left <= 0:
                                raise RuntimeError("Слишком много редиректов при скачивании.")
                            location = resp.headers.get("Location")
                            if not location:
                                raise RuntimeError("Редирект без Location.")
                            current_url = urljoin(current_url, location)
                            redirects_left -= 1
                            continue

                        if resp.status != 200:
                            raise RuntimeError(f"HTTP {resp.status}")
                        clen = resp.content_length or 0
                        if clen and clen > max_bytes:
                            raise RuntimeError(
                                f"Файл слишком большой (макс. {_fmt_bytes(max_bytes)})."
                            )
                        # Preflight free space when the size is known up front.
                        if clen:
                            free = shutil.disk_usage(IMPORT_TMP_DIR).free
                            if clen + DISK_RESERVE > free:
                                raise RuntimeError(
                                    f"Недостаточно места: файл ~{_fmt_bytes(clen)}, "
                                    f"свободно {_fmt_bytes(free)}. Освободи место "
                                    f"или импортируй меньший архив."
                                )

                        checked = 0
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError(
                                    f"Файл слишком большой (макс. {_fmt_bytes(max_bytes)})."
                                )
                            # Unknown-length sources: watch free space as we stream.
                            checked += len(chunk)
                            if checked >= 256 * 1024 * 1024:
                                checked = 0
                                if shutil.disk_usage(IMPORT_TMP_DIR).free < DISK_RESERVE:
                                    raise RuntimeError(
                                        "Закончилось место на диске во время загрузки."
                                    )
                            f.write(chunk)
                        break
        return tmp_path, size
    except Exception:
        _cleanup_import_file(tmp_path)
        raise


def _count_osu_files(file_path: str) -> tuple[int, int]:
    import zipfile as _zf
    osz_count = osu_count = 0
    try:
        with _zf.ZipFile(file_path) as outer:
            for name in outer.namelist():
                if name.lower().endswith(".osz"):
                    osz_count += 1
                    try:
                        import io as _io
                        with _zf.ZipFile(_io.BytesIO(outer.read(name))) as inner:
                            osu_count += sum(1 for n in inner.namelist() if n.endswith(".osu"))
                    except Exception:
                        logger.debug(f"import: nested zip read failed for {name}", exc_info=True)
                elif name.lower().endswith(".osu"):
                    osu_count += 1
    except _zf.BadZipFile:
        try:
            with _zf.ZipFile(file_path) as inner:
                osz_count = 1
                osu_count = sum(1 for n in inner.namelist() if n.endswith(".osu"))
        except Exception:
            logger.debug("import: zip recovery read failed", exc_info=True)
    return osz_count, osu_count


async def build_import_queue_report() -> str:
    """Build the import-queue summary text (HTML). Shared by the
    `importqueue` command handler and the admin panel's execute button."""
    _cleanup_stale_imports()
    if not _import_queue:
        return "Очередь импорта пуста."
    lines = ["<b>Очередь импорта</b>\n"]
    for i, (_sid, slot) in enumerate(_import_queue.items(), 1):
        status = slot["status"]
        fname = escape_html(slot["filename"])
        icon = "⏳" if status == "pending" else "🔄"
        lines.append(f"{icon} {i}. <b>{fname}</b> [{status}]")
    return "\n".join(lines)


@router.message(TextTriggerFilter("importqueue", "iq"))
async def cmd_import_queue(message: types.Message):
    """Show the shared import queue used by /import."""
    await message.answer(await build_import_queue_report(), parse_mode="HTML")

