import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

from aiogram import Router, types, F
from sqlalchemy import select, func

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="admin_bsk_pool")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

# ─── BSK Map Pool Admin Commands ─────────────────────────────────────────────

@router.message(TextTriggerFilter("bskaddmap"))
async def cmd_bsk_add_map(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """bskaddmap <beatmap_id> — fetch, parse .osu and add to BSK pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>bskaddmap &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    wait = await message.answer(f"Загружаю карту {beatmap_id}...")

    try:
        from services.bsk.osu_parser import extract_features, weights_from_features, map_type_from_weights
        from db.models.bsk_map_pool import BskMapPool
        import aiohttp

        # Check if already in pool
        async with get_db_session() as session:
            existing = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()

        if existing:
            await wait.edit_text(
                f"Карта <b>{beatmap_id}</b> уже в пуле: "
                f"{existing.artist} - {existing.title} [{existing.version}] "
                f"({existing.star_rating:.2f}★, type={existing.map_type})",
                parse_mode="HTML",
            )
            return

        # Fetch beatmap metadata
        bmap_data = await osu_api_client.get_beatmap(beatmap_id)
        if not bmap_data:
            await wait.edit_text(f"Карта {beatmap_id} не найдена в osu! API.")
            return

        bset = bmap_data.get("beatmapset") or {}
        bpm = float(bmap_data.get("bpm") or bset.get("bpm") or 0)
        ar = float(bmap_data.get("ar") or 0)
        od = float(bmap_data.get("accuracy") or 0)
        cs = float(bmap_data.get("cs") or 0)
        sr = float(bmap_data.get("difficulty_rating") or 0)
        length = int(bmap_data.get("total_length") or 0)
        beatmapset_id = int(bmap_data.get("beatmapset_id") or bset.get("id") or 0)

        # Download .osu file for parsing
        osu_text = None
        osu_url = f"https://osu.ppy.sh/osu/{beatmap_id}"
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(osu_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        raw_bytes = await resp.read()
                        osu_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to download .osu for {beatmap_id}: {e}")

        if osu_text:
            features = extract_features(osu_text)
            weights = weights_from_features(features, bpm=bpm, ar=ar, od=od)
            map_type = map_type_from_weights(weights)
            source = "parsed"
        else:
            # Fallback to heuristic
            from services.bsk.map_pool import _estimate_weights, _map_type
            weights = _estimate_weights(bpm, ar, od, length)
            map_type = _map_type(weights)
            features = {}
            source = "heuristic"

        async with get_db_session() as session:
            entry = BskMapPool(
                beatmap_id=beatmap_id,
                beatmapset_id=beatmapset_id,
                title=bset.get("title") or "Unknown",
                artist=bset.get("artist") or "Unknown",
                version=bmap_data.get("version") or "",
                creator=bset.get("creator"),
                star_rating=sr,
                bpm=bpm,
                length=length,
                ar=ar,
                od=od,
                cs=cs,
                w_aim=weights["aim"],
                w_speed=weights["speed"],
                w_acc=weights["acc"],
                w_cons=weights["cons"],
                map_type=map_type,
                enabled=True,
            )
            session.add(entry)
            await session.commit()

        feat_line = ""
        if features:
            feat_line = (
                f"\nstream: <code>{features.get('stream_density', 0):.3f}</code>  "
                f"jump: <code>{features.get('jump_density', 0):.3f}</code>  "
                f"slider: <code>{features.get('slider_density', 0):.3f}</code>  "
                f"rhythm: <code>{features.get('rhythm_complexity', 0):.3f}</code>"
            )

        await wait.edit_text(
            f"✅ <b>Карта добавлена в BSK пул</b> ({source})\n\n"
            f"<b>{escape_html(bset.get('artist', ''))} - {escape_html(bset.get('title', ''))}</b> "
            f"[{escape_html(bmap_data.get('version', ''))}]\n"
            f"⭐ {sr:.2f}  ·  {bpm:.0f} BPM  ·  AR {ar}  ·  OD {od}\n\n"
            f"Тип: <b>{map_type}</b>\n"
            f"Aim: <code>{weights['aim']:.3f}</code>  "
            f"Speed: <code>{weights['speed']:.3f}</code>  "
            f"Acc: <code>{weights['acc']:.3f}</code>  "
            f"Cons: <code>{weights['cons']:.3f}</code>"
            f"{feat_line}",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"bskaddmap error for {beatmap_id}: {e}", exc_info=True)
        await wait.edit_text(f"Ошибка: {e}")


@router.message(TextTriggerFilter("bskremovemap"))
async def cmd_bsk_remove_map(message: types.Message, trigger_args: TriggerArgs):
    """bskremovemap <beatmap_id> — disable map in BSK pool."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer("Использование: <code>bskremovemap &lt;beatmap_id&gt;</code>", parse_mode="HTML")
        return

    beatmap_id = int(raw)
    from db.models.bsk_map_pool import BskMapPool
    async with get_db_session() as session:
        entry = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
        )).scalar_one_or_none()
        if not entry:
            await message.answer(f"Карта {beatmap_id} не найдена в пуле.")
            return
        entry.enabled = False
        await session.commit()

    await message.answer(f"Карта {beatmap_id} отключена из BSK пула.")


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

    last_seen = user.last_seen.strftime("%Y-%m-%d %H:%M") if getattr(user, "last_seen", None) else "—"
    if token:
        exp = token.token_expiry.strftime("%Y-%m-%d %H:%M") if token.token_expiry else "—"
        oauth_line = f"✅ Привязан, истекает: <code>{exp}</code>"
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

    Used after OAuth permanent failures (bsk-ml token_manager logs
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

    Useful for picking the value to set in BSK_DUEL_THREAD_ID env var.
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
            f"<code>BSK_DUEL_THREAD_ID={thread_id}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskenable"))
async def cmd_bsk_enable_map(message: types.Message, trigger_args: TriggerArgs):
    """bskenable <beatmap_id> — re-enable a previously disabled BSK pool map."""
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.isdigit():
        await message.answer(
            "Использование: <code>bskenable &lt;beatmap_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    beatmap_id = int(raw)
    from db.models.bsk_map_pool import BskMapPool
    async with get_db_session() as session:
        entry = (await session.execute(
            select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
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


_BSK_BROKEN_PER_PAGE = 15


async def _bsk_broken_collect() -> tuple[
    list[tuple["BskMapPool", list[str]]],  # type: ignore  # noqa: F821
    list["BskMapPool"],                     # type: ignore  # noqa: F821
]:
    """Scan the pool and split entries into (broken, disabled-but-clean)."""
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import map_is_broken
    from sqlalchemy import or_

    async with get_db_session() as session:
        candidates = (await session.execute(
            select(BskMapPool).where(
                or_(
                    BskMapPool.star_rating <= 0,
                    BskMapPool.api_aim_diff.is_(None),
                    BskMapPool.f_note_count.is_(None),
                    BskMapPool.enabled == False,  # noqa: E712
                )
            ).order_by(BskMapPool.star_rating)
        )).scalars().all()

    broken: list[tuple[BskMapPool, list[str]]] = []
    disabled_only: list[BskMapPool] = []
    for m in candidates:
        is_b, reasons = map_is_broken(m)
        if is_b:
            broken.append((m, reasons))
        elif not m.enabled:
            disabled_only.append(m)
    return broken, disabled_only


async def _bsk_broken_render(
    page: int, section: str = "broken"
) -> tuple[str, types.InlineKeyboardMarkup]:
    """Render one page of `bskbroken` for the given section ('broken'|'disabled')."""
    broken, disabled_only = await _bsk_broken_collect()

    if section not in ("broken", "disabled"):
        section = "broken"

    items_broken = broken
    items_disabled = disabled_only

    if section == "broken":
        items: list = items_broken
        per = _BSK_BROKEN_PER_PAGE
        header_emoji = "⚠️"
        header_label = "Битые карты"
    else:
        items = items_disabled
        per = _BSK_BROKEN_PER_PAGE
        header_emoji = "❌"
        header_label = "Отключённые, но целые"

    total_items = len(items)
    pages = max(1, (total_items + per - 1) // per)
    page = max(1, min(page, pages))
    start = (page - 1) * per
    chunk = items[start:start + per]

    lines = [
        "<b>BSK — диагностика пула</b>",
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
            "Чинить: <code>bskrefresh &lt;id&gt;</code> "
            "или <code>bskrefresh broken</code> для пакетной починки.",
        ]
    elif section == "disabled" and items_disabled:
        lines += ["", "Включить: <code>bskenable &lt;id&gt;</code>"]

    # ── Keyboard ─────────────────────────────────────────────────────────────
    nav_row: list[types.InlineKeyboardButton] = []
    if pages > 1:
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(
                text="◀", callback_data=f"bskbroken:page:{section}:{page - 1}"
            ))
        nav_row.append(types.InlineKeyboardButton(
            text=f"{page}/{pages}", callback_data="bskbroken:noop"
        ))
        if page < pages:
            nav_row.append(types.InlineKeyboardButton(
                text="▶", callback_data=f"bskbroken:page:{section}:{page + 1}"
            ))

    other = "disabled" if section == "broken" else "broken"
    other_count = len(items_disabled) if section == "broken" else len(items_broken)
    other_label = (
        f"❌ Отключённые ({other_count})"
        if section == "broken" else f"⚠️ Битые ({other_count})"
    )
    switch_row = [types.InlineKeyboardButton(
        text=other_label, callback_data=f"bskbroken:page:{other}:1"
    )]

    rows: list[list[types.InlineKeyboardButton]] = []
    if nav_row:
        rows.append(nav_row)
    rows.append(switch_row)
    kb = types.InlineKeyboardMarkup(inline_keyboard=rows)

    return "\n".join(lines), kb


@router.message(TextTriggerFilter("bskbroken"))
async def cmd_bsk_broken(message: types.Message, trigger_args: TriggerArgs):
    """bskbroken [page] — list broken / disabled BSK pool maps with pagination."""
    args = (trigger_args.args or "").strip().lower()
    section = "broken"
    page = 1
    if args:
        for token in args.split():
            if token in ("broken", "disabled"):
                section = token
            elif token.isdigit():
                page = max(1, int(token))

    broken, disabled_only = await _bsk_broken_collect()
    if not broken and not disabled_only:
        await message.answer("✅ В пуле нет карт с проблемами.")
        return

    text, kb = await _bsk_broken_render(page, section)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("bskbroken:"))
async def on_bsk_broken_callback(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) >= 2 and parts[1] == "noop":
        await callback.answer()
        return
    # Format: bskbroken:page:<section>:<n>
    if len(parts) >= 4 and parts[1] == "page":
        section = parts[2]
        try:
            page = int(parts[3])
        except ValueError:
            page = 1
        text, kb = await _bsk_broken_render(page, section)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            logger.debug("bskbroken: edit_text failed", exc_info=True)
        await callback.answer()
        return
    await callback.answer()


# ── Post-refresh actions ─────────────────────────────────────────────────────
# After a `bskrefresh broken` batch, give the admin a chance to disable or
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


@router.callback_query(F.data.startswith("bskrefresh:fix:"))
async def on_bsk_refresh_fix(callback: types.CallbackQuery):
    """Handle 'disable / delete / cancel' actions for the post-refresh prompt."""
    from db.models.bsk_map_pool import BskMapPool

    parts = callback.data.split(":")
    # bskrefresh:fix:<action>:<slot>
    if len(parts) != 4:
        await callback.answer("Некорректные данные.", show_alert=True)
        return
    action = parts[2]
    slot_id = parts[3]

    slot = _refresh_slots.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла — запусти bskrefresh broken заново.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("bskrefresh:fix expired slot — edit_reply_markup failed", exc_info=True)
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
            logger.debug("bskrefresh:fix cancel — edit_reply_markup failed", exc_info=True)
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
            select(BskMapPool).where(BskMapPool.beatmap_id.in_(bad_ids))
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
            logger.debug("bskrefresh:fix — edit_reply_markup failed", exc_info=True)
        await callback.message.answer("\n".join(suffix_lines), parse_mode="HTML")
    logger.info(f"bskrefresh:fix admin={callback.from_user.id} action={action} affected={affected}")
    await callback.answer(f"{verb}: {affected}")


@router.message(TextTriggerFilter("bskrefresh"))
async def cmd_bsk_refresh(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    """
    bskrefresh <beatmap_id>   — re-pull a single map from the osu! API + CDN.
    bskrefresh broken          — refresh every map flagged by bskbroken.
    bskrefresh disabled        — re-enable every disabled map and re-pull.

    Useful when maps were imported while the API was misbehaving and ended
    up with star_rating=0, missing parsed features, etc. Retries each
    network call up to 3 times before giving up.
    """
    raw = (trigger_args.args or "").strip().lower()
    if not raw:
        await message.answer(
            "Использование:\n"
            "<code>bskrefresh &lt;id&gt;</code> — одна карта\n"
            "<code>bskrefresh broken</code> — все битые\n"
            "<code>bskrefresh disabled</code> — все отключённые",
            parse_mode="HTML",
        )
        return

    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import refresh_map, map_is_broken
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
    if raw not in ("broken", "disabled"):
        await message.answer("Неизвестный режим. Доступно: <id>, broken, disabled", parse_mode="HTML")
        return

    async with get_db_session() as session:
        if raw == "disabled":
            candidates = (await session.execute(
                select(BskMapPool).where(BskMapPool.enabled == False)
            )).scalars().all()
        else:  # broken
            candidates = (await session.execute(
                select(BskMapPool).where(
                    or_(
                        BskMapPool.star_rating <= 0,
                        BskMapPool.api_aim_diff.is_(None),
                        BskMapPool.f_note_count.is_(None),
                    )
                )
            )).scalars().all()

    if raw == "broken":
        # Verify against map_is_broken (the SQL filter is permissive).
        candidates = [m for m, in [(m,) for m in candidates] if map_is_broken(m)[0]]

    if not candidates:
        await message.answer("Нечего обновлять — пул чистый.")
        return

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
                logger.debug("bskrefresh: progress edit_text failed", exc_info=True)
        try:
            r = await refresh_map(osu_api_client, m.beatmap_id, re_enable=True)
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            if r["status"] in ("no_data", "not_found", "error", "partial"):
                bad_ids.append(m.beatmap_id)
        except Exception as e:
            logger.error(f"bskrefresh batch error for {m.beatmap_id}: {e}", exc_info=True)
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
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=f"❌ Отключить {len(bad_ids)}",
                    callback_data=f"bskrefresh:fix:disable:{slot}",
                ),
                types.InlineKeyboardButton(
                    text=f"🗑 Удалить {len(bad_ids)}",
                    callback_data=f"bskrefresh:fix:delete:{slot}",
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="Оставить как есть",
                    callback_data=f"bskrefresh:fix:cancel:{slot}",
                ),
            ],
        ])

    try:
        await wait.edit_text("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.answer("\n".join(text_lines), parse_mode="HTML", reply_markup=kb)


_BSK_POOL_PER_PAGE = 15


async def _bsk_pool_render(page: int) -> tuple[str, types.InlineKeyboardMarkup]:
    """Return (text, keyboard) for the given BSK pool page."""
    from db.models.bsk_map_pool import BskMapPool

    async with get_db_session() as session:
        total = (await session.execute(
            select(func.count()).select_from(BskMapPool)
        )).scalar() or 0
        enabled = (await session.execute(
            select(func.count()).select_from(BskMapPool).where(BskMapPool.enabled == True)
        )).scalar() or 0

        maps = (await session.execute(
            select(BskMapPool)
            .order_by(BskMapPool.star_rating)
            .offset((page - 1) * _BSK_POOL_PER_PAGE)
            .limit(_BSK_POOL_PER_PAGE)
        )).scalars().all()

    pages = max(1, (total + _BSK_POOL_PER_PAGE - 1) // _BSK_POOL_PER_PAGE)
    page = max(1, min(page, pages))

    from services.bsk.map_pool import map_is_broken
    lines = [f"<b>BSK пул</b> — {enabled} активных / {total} всего  (стр. {page}/{pages})\n"]
    for m in maps:
        broken, _ = map_is_broken(m)
        if not m.enabled:
            status = "❌"
        elif broken:
            status = "⚠️"
        else:
            status = "✅"
        sr_str = f"⭐{m.star_rating:.1f}" if (m.star_rating or 0) > 0 else "⭐<i>—</i>"
        lines.append(
            f"{status} <code>{m.beatmap_id}</code> {escape_html(m.artist)} - {escape_html(m.title)} "
            f"[{escape_html(m.version)}] {sr_str} {m.map_type or ''}"
        )

    nav = []
    if page > 1:
        nav.append(types.InlineKeyboardButton(text="◀", callback_data=f"bskpool:page:{page - 1}"))
    if page < pages:
        nav.append(types.InlineKeyboardButton(text="▶", callback_data=f"bskpool:page:{page + 1}"))
    kb = types.InlineKeyboardMarkup(inline_keyboard=[nav]) if nav else types.InlineKeyboardMarkup(inline_keyboard=[])

    return "\n".join(lines), kb


@router.message(TextTriggerFilter("bskpool", "bskp"))
async def cmd_bsk_pool(message: types.Message, trigger_args: TriggerArgs):
    """bskpool [page] — list BSK map pool."""
    args = (trigger_args.args or "").strip()
    page = max(1, int(args)) if args.isdigit() else 1
    text, kb = await _bsk_pool_render(page)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("bskpool:page:"))
async def on_bsk_pool_page(callback: types.CallbackQuery):
    page = int(callback.data.split(":")[-1])
    text, kb = await _bsk_pool_render(page)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.message(TextTriggerFilter("bskrecalc"))
async def cmd_bsk_recalc(message: types.Message):
    """Re-derive skill stars and map_type from stored features without re-downloading.

    Uses the new analyze_map pipeline.  For maps with cached parser features
    (f_burst, f_stream, ...) we feed those back in; for maps without features
    we fall back to metadata (BPM/AR/OD/length) only.
    """
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.osu_parser import (
        compute_skill_stars, stars_to_weights, map_type_from_stars,
    )

    wait = await message.answer("Пересчитываю звёзды и веса карт в пуле…")

    updated = 0
    type_counts: dict[str, int] = {}

    async with get_db_session() as session:
        maps = (await session.execute(select(BskMapPool))).scalars().all()
        for m in maps:
            # Reconstruct feature dict from stored columns
            features = {
                "note_count":            m.f_note_count or 0,
                "duration_seconds":      m.f_duration or m.length or 0,
                "rhythm_complexity":     m.f_rhythm_complexity or 0.0,
                "stream_density":        (m.f_burst or 0) + (m.f_stream or 0) + (m.f_death_stream or 0),
                "jump_density":          m.f_jump_density or 0.0,
                "avg_jump_velocity":     m.f_jump_vel or 0.0,
                "back_forth_ratio":      m.f_back_forth or 0.0,
                "angle_variance":        m.f_angle_var or 0.0,
                "flow_break_density":    m.f_flow_break or 0.0,
                "burst_density":         m.f_burst or 0.0,
                "full_stream_density":   m.f_stream or 0.0,
                "death_stream_density":  m.f_death_stream or 0.0,
                "bpm_rel_speed":         m.f_bpm_rel_speed or 0.0,
                "subdiv_entropy":        m.f_subdiv_entropy or 0.0,
                "polyrhythm_density":    m.f_polyrhythm_density or 0.0,
                "off_beat_ratio":        m.f_off_beat_ratio or 0.0,
                "jack_density":          m.f_jack_density or 0.0,
                "slider_tail_demand":    m.f_slider_tail_demand or 0.0,
                "sv_variance":           m.f_sv_var or 0.0,
                "slider_density":        m.f_slider_density or 0.0,
                "density_variance":      m.f_density_var or 0.0,
                "intensity_floor":       m.f_intensity_floor or 0.0,
                "pattern_repetition":    m.f_pattern_repeat or 0.0,
            }
            stars = compute_skill_stars(
                features,
                bpm=m.bpm or 0, ar=m.ar or 0, od=m.od or 0,
                length_s=m.length or 0,
                star_rating=m.star_rating or 0,
                api_aim=float(m.api_aim_diff or 0.0),
                api_speed=float(m.api_speed_diff or 0.0),
            )
            weights = stars_to_weights(stars)

            m.aim_stars   = stars["aim"]
            m.speed_stars = stars["speed"]
            m.acc_stars   = stars["acc"]
            m.cons_stars  = stars["cons"]
            m.w_aim   = weights["aim"]
            m.w_speed = weights["speed"]
            m.w_acc   = weights["acc"]
            m.w_cons  = weights["cons"]
            m.map_type = map_type_from_stars(stars)
            type_counts[m.map_type] = type_counts.get(m.map_type, 0) + 1
            updated += 1
        await session.commit()

    lines = [f"✅ Пересчитано карт: <b>{updated}</b>\n", "<b>Распределение по типам:</b>"]
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = cnt / max(updated, 1) * 100
        lines.append(f"  • <code>{t:<6}</code>  {cnt}  ({pct:.1f}%)")

    await wait.edit_text("\n".join(lines), parse_mode="HTML")


@router.message(TextTriggerFilter("bskreanalyze"))
async def cmd_bsk_reanalyze(message: types.Message, osu_api_client):
    """
    Re-download every map's .osu file, extract deep features + osu! API attributes,
    write per-skill stars + map_type via the new analyze_map pipeline.
    Takes a few minutes for large pools.
    """
    from db.models.bsk_map_pool import BskMapPool
    from services.bsk.map_pool import analyze_map, apply_to_entry
    import asyncio

    wait = await message.answer("🔍 Глубокий анализ пула карт…\nЭто может занять несколько минут.")

    updated = 0
    failed  = 0
    no_osu  = 0

    async with get_db_session() as session:
        maps = (await session.execute(select(BskMapPool))).scalars().all()
        total = len(maps)

    for idx, m in enumerate(maps, 1):
        if idx % 20 == 0:
            try:
                await wait.edit_text(
                    f"🔍 Анализ: {idx}/{total} карт…\n"
                    f"✅ {updated}  ❌ {failed}  ⏭ {no_osu}"
                )
            except Exception:
                logger.debug("bskreanalyze: progress edit_text failed", exc_info=True)

        # Download .osu
        osu_bytes = None
        try:
            osu_bytes = await osu_api_client.download_osu_file(m.beatmap_id)
        except Exception:
            logger.debug(f"bskreanalyze: .osu download failed for {m.beatmap_id}", exc_info=True)

        # Fetch beatmap data (for hp_drain + chance to repair sr=0 entries).
        hp_drain_val = None
        api_sr = api_bpm = api_length = None
        api_ar = api_od = api_cs = None
        try:
            bmap_data = await osu_api_client.get_beatmap(m.beatmap_id)
            if bmap_data:
                hp_drain_val = float(bmap_data.get("drain") or 0) or None
                api_sr     = float(bmap_data.get("difficulty_rating") or 0) or None
                api_bpm    = float(bmap_data.get("bpm") or 0) or None
                api_length = int(bmap_data.get("total_length") or bmap_data.get("hit_length") or 0) or None
                api_ar     = float(bmap_data.get("ar")       or 0) or None
                api_od     = float(bmap_data.get("accuracy") or 0) or None
                api_cs     = float(bmap_data.get("cs")       or 0) or None
        except Exception:
            logger.debug(f"bskreanalyze: get_beatmap failed for {m.beatmap_id}", exc_info=True)

        # Fetch API attributes (absolute aim/speed difficulties)
        api_aim = api_speed = api_slider = api_speed_notes = None
        try:
            attrs = await osu_api_client.get_beatmap_attributes(m.beatmap_id)
            if attrs:
                api_aim         = attrs.get("aim_difficulty")
                api_speed       = attrs.get("speed_difficulty")
                api_slider      = attrs.get("slider_factor")
                api_speed_notes = attrs.get("speed_note_count")
        except Exception:
            logger.debug(f"bskreanalyze: get_beatmap_attributes failed for {m.beatmap_id}", exc_info=True)

        osu_text = osu_bytes.decode("utf-8", errors="replace") if osu_bytes else None
        if not osu_text:
            no_osu += 1

        try:
            # Prefer fresh API values over stale row data — heals sr=0 entries.
            eff_bpm    = api_bpm    or (m.bpm or 0)
            eff_length = api_length or (m.length or 0)
            eff_sr     = api_sr     or (m.star_rating or 0)
            eff_ar     = api_ar     or (m.ar or 0)
            eff_od     = api_od     or (m.od or 0)
            result = analyze_map(
                osu_text,
                bpm=eff_bpm, ar=eff_ar, od=eff_od,
                length_s=eff_length,
                star_rating=eff_sr,
                api_aim=float(api_aim or 0.0),
                api_speed=float(api_speed or 0.0),
            )
            async with get_db_session() as session:
                entry = (await session.execute(
                    select(BskMapPool).where(BskMapPool.beatmap_id == m.beatmap_id)
                )).scalar_one_or_none()
                if entry:
                    entry.api_aim_diff         = api_aim
                    entry.api_speed_diff       = api_speed
                    entry.api_slider_factor    = api_slider
                    entry.api_speed_note_count = api_speed_notes
                    if hp_drain_val is not None:
                        entry.hp_drain = hp_drain_val
                    # Heal sr=0 / missing-metadata entries when the API now
                    # returns sane values. Don't overwrite with zeros.
                    if api_sr     is not None: entry.star_rating = api_sr
                    if api_bpm    is not None: entry.bpm         = api_bpm
                    if api_length is not None: entry.length      = api_length
                    if api_ar     is not None: entry.ar          = api_ar
                    if api_od     is not None: entry.od          = api_od
                    if api_cs     is not None: entry.cs          = api_cs
                    apply_to_entry(entry, result)
                    await session.commit()
            updated += 1
        except Exception as e:
            logger.warning(f"bskreanalyze: failed for {m.beatmap_id}: {e}")
            failed += 1

        await asyncio.sleep(0.15)  # rate-limit CDN + API calls

    await wait.edit_text(
        f"✅ <b>Глубокий анализ завершён</b>\n\n"
        f"Обновлено:       <b>{updated}</b>\n"
        f"Без .osu файла:  <b>{no_osu}</b>\n"
        f"Ошибок:          <b>{failed}</b>",
        parse_mode="HTML",
    )


@router.message(TextTriggerFilter("bskcleantest"))
async def cmd_bsk_clean_test(message: types.Message):
    """Delete all completed/cancelled/expired test duels and their rounds."""
    from db.models.bsk_duel import BskDuel
    from db.models.bsk_duel_round import BskDuelRound
    from sqlalchemy import delete as sa_delete

    wait = await message.answer("Удаляю тестовые дуэли…")

    async with get_db_session() as session:
        # Find all test duels in a terminal state
        test_duels = (await session.execute(
            select(BskDuel).where(
                BskDuel.is_test == True,
                BskDuel.status.in_(['completed', 'cancelled', 'expired']),
            )
        )).scalars().all()

        duel_ids = [d.id for d in test_duels]
        if not duel_ids:
            await wait.edit_text("Нет завершённых тестовых дуэлей для удаления.")
            return

        # Delete rounds first (FK constraint)
        rounds_del = await session.execute(
            sa_delete(BskDuelRound).where(BskDuelRound.duel_id.in_(duel_ids))
        )
        duels_del = await session.execute(
            sa_delete(BskDuel).where(BskDuel.id.in_(duel_ids))
        )
        await session.commit()

    await wait.edit_text(
        f"✅ Удалено тестовых дуэлей: <b>{duels_del.rowcount}</b>\n"
        f"Удалено раундов: <b>{rounds_del.rowcount}</b>",
        parse_mode="HTML",
    )



@router.message(TextTriggerFilter("bskimport"))
async def cmd_bsk_import_url(message: types.Message, trigger_args: TriggerArgs, osu_api_client):
    _cleanup_stale_imports()
    url = (trigger_args.args or "").strip()
    if not url or not url.startswith("http"):
        await message.answer(
            "Использование:\n"
            "• Файл .zip/.osz с подписью <code>bskimport</code>\n"
            "• <code>bskimport &lt;прямая ссылка&gt;</code>",
            parse_mode="HTML",
        )
        return

    if not (url.lower().endswith(".zip") or url.lower().endswith(".osz")):
        await message.answer("Ссылка должна вести на .zip или .osz файл.")
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS}). Подождите завершения текущих.")
        return

    wait = await message.answer("Скачиваю файл в очередь импорта...")
    try:
        tmp_path, size = await _download_url_to_import_file(url, max_bytes=MAX_IMPORT_FILE_SIZE)
    except Exception as e:
        await wait.edit_text(f"Ошибка при скачивании: {escape_html(str(e))}", parse_mode="HTML")
        return

    slot_id = _register_import(message.from_user.id, tmp_path, url.split("/")[-1], size)
    osz_count, osu_count = _count_osu_files(tmp_path)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Источник: <code>{escape_html(url.split('/')[-1])}</code>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Одновременно выполняется импортов: <b>{MAX_RUNNING_IMPORTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ─── Import queue ─────────────────────────────────────────────────────────────

MAX_IMPORT_SLOTS = 5
MAX_RUNNING_IMPORTS = 1
MAX_IMPORT_FILE_SIZE = 1024 * 1024 * 1024
IMPORT_TMP_DIR = "/tmp/project1984_bsk_imports"
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
        logger.debug("bskimport: temp cleanup failed", exc_info=True)


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
        os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
        cutoff = now.timestamp() - IMPORT_PENDING_TTL_SECONDS
        active_paths = {slot.get("file_path") for slot in _import_queue.values()}
        for name in os.listdir(IMPORT_TMP_DIR):
            path = os.path.join(IMPORT_TMP_DIR, name)
            if path in active_paths or not name.startswith("bskimport_"):
                continue
            try:
                if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except FileNotFoundError:
                pass
    except Exception:
        logger.debug("bskimport: stale temp cleanup failed", exc_info=True)


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


async def _download_url_to_import_file(url: str, max_bytes: int = MAX_IMPORT_FILE_SIZE) -> tuple[str, int]:
    import aiohttp as _aiohttp
    import os
    import tempfile
    from urllib.parse import urljoin

    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)
    suffix = ".osz" if url.lower().split("?", 1)[0].endswith(".osz") else ".zip"
    fd, tmp_path = tempfile.mkstemp(prefix="bskimport_", suffix=suffix, dir=IMPORT_TMP_DIR)
    size = 0
    current_url = url
    redirects_left = 5
    try:
        with os.fdopen(fd, "wb") as f:
            async with _aiohttp.ClientSession() as sess:
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
                        if resp.content_length and resp.content_length > max_bytes:
                            raise RuntimeError("Файл слишком большой (макс. 1 GB).")

                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError("Файл слишком большой (макс. 1 GB).")
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
                        logger.debug(f"bskimport: nested zip read failed for {name}", exc_info=True)
                elif name.lower().endswith(".osu"):
                    osu_count += 1
    except _zf.BadZipFile:
        try:
            with _zf.ZipFile(file_path) as inner:
                osz_count = 1
                osu_count = sum(1 for n in inner.namelist() if n.endswith(".osu"))
        except Exception:
            logger.debug("bskimport: zip recovery read failed", exc_info=True)
    return osz_count, osu_count


@router.callback_query(F.data.startswith("bskimport:"))
async def on_bsk_import_confirm(callback: types.CallbackQuery, osu_api_client):
    parts = callback.data.split(":")
    action = parts[1]
    slot_id = parts[2] if len(parts) > 2 else None

    # Legacy path (no slot_id) — old pending_imports dict
    if not slot_id:
        tg_id = callback.from_user.id
        if action == "cancel":
            path = _pending_imports.pop(tg_id, None)
            _cleanup_import_file(path)
            await callback.message.edit_text("Импорт отменён.")
            await callback.answer()
            return
        file_path = _pending_imports.pop(tg_id, None)
        if not file_path:
            await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
            return
        slot_id = _register_import(tg_id, file_path, "upload.zip")

    slot = _import_queue.get(slot_id)
    if not slot:
        await callback.answer("Сессия истекла. Загрузите файл заново.", show_alert=True)
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

    # Confirm — enqueue import in background; semaphore prevents parallel heavy imports.
    slot["status"] = "queued"
    await callback.message.edit_text(
        f"<b>Импорт поставлен в очередь</b>\n"
        f"Файл: <b>{escape_html(slot['filename'])}</b>\n"
        f"Размер: <b>{_fmt_bytes(slot.get('size'))}</b>\n"
        f"Параллельных импортов: <b>{MAX_RUNNING_IMPORTS}</b>",
        parse_mode="HTML",
    )
    await callback.answer()

    import asyncio
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
                        f"<b>Импортирую карты...</b>\n"
                        f"Файл: <b>{escape_html(slot['filename'])}</b>\n"
                        f"Остальные импорты ждут в очереди.",
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.debug("bskimport: running edit_text failed", exc_info=True)

                from services.bsk.bulk_import import import_from_file
                result = await import_from_file(slot["file_path"], osu_api_client)
        except Exception as e:
            logger.error(f"BSK bulk import error: {e}", exc_info=True)
            result = {"added": 0, "skipped": 0, "failed": 1, "errors": [str(e)]}
        finally:
            _import_queue.pop(slot_id, None)
            _cleanup_import_file(slot.get("file_path"))

        added, skipped, failed = result["added"], result["skipped"], result["failed"]
        lines = [
            "<b>BSK импорт завершён</b>",
            f"Файл: <b>{escape_html(slot['filename'])}</b>",
            f"✅ Добавлено: <b>{added}</b>",
            f"⏭ Пропущено: <b>{skipped}</b>",
            f"❌ Ошибок: <b>{failed}</b>",
        ]
        if result.get("errors"):
            lines.append("\nПервые ошибки:")
            for e in result["errors"]:
                lines.append(f"  • {escape_html(str(e)[:120])}")
        try:
            await msg.edit_text("\n".join(lines), parse_mode="HTML")
        except Exception:
            logger.debug("bskimport: result edit_text failed", exc_info=True)

    asyncio.create_task(_run())


@router.message(F.document & (F.caption.lower() == "bskimport"))
async def cmd_bsk_bulk_import(message: types.Message, osu_api_client):
    _cleanup_stale_imports()
    doc = message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".zip") or fname.endswith(".osz")):
        await message.answer("Поддерживаются только файлы <b>.zip</b> или <b>.osz</b>.", parse_mode="HTML")
        return

    if len(_import_queue) >= MAX_IMPORT_SLOTS:
        await message.answer(f"Очередь импорта заполнена (макс. {MAX_IMPORT_SLOTS}). Подождите завершения текущих.")
        return

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE:
        await message.answer("Файл слишком большой (макс. 1 GB).")
        return

    wait = await message.answer("Скачиваю файл в очередь импорта...")
    try:
        from config.settings import TELEGRAM_BOT_TOKEN
        file = await message.bot.get_file(doc.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file.file_path}"
        tmp_path, size = await _download_url_to_import_file(file_url, max_bytes=MAX_IMPORT_FILE_SIZE)
    except Exception as e:
        await wait.edit_text(f"Не удалось скачать файл: {escape_html(str(e))}", parse_mode="HTML")
        return

    slot_id = _register_import(message.from_user.id, tmp_path, doc.file_name or "upload.zip", size)
    osz_count, osu_count = _count_osu_files(tmp_path)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Импортировать", callback_data=f"bskimport:confirm:{slot_id}"),
        types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"bskimport:cancel:{slot_id}"),
    ]])
    await wait.edit_text(
        f"<b>Предпросмотр импорта</b>\n\n"
        f"Файл: <b>{escape_html(doc.file_name)}</b>\n"
        f"Размер: <b>{_fmt_bytes(size)}</b>\n"
        f"Архивов .osz: <b>{osz_count}</b>\n"
        f"Карт .osu: <b>{osu_count}</b>\n"
        f"Слот: <b>{_queue_position(slot_id)}/{MAX_IMPORT_SLOTS}</b>\n"
        f"Одновременно выполняется импортов: <b>{MAX_RUNNING_IMPORTS}</b>\n\n"
        f"Подтвердить импорт в BSK пул?",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.message(TextTriggerFilter("bskimportqueue", "bskiq"))
async def cmd_bsk_import_queue(message: types.Message):
    _cleanup_stale_imports()
    if not _import_queue:
        await message.answer("Очередь импорта пуста.")
        return
    lines = ["<b>Очередь импорта BSK</b>\n"]
    for i, (_sid, slot) in enumerate(_import_queue.items(), 1):
        status = slot["status"]
        fname = escape_html(slot["filename"])
        icon = "⏳" if status == "pending" else "🔄"
        lines.append(f"{icon} {i}. <b>{fname}</b> [{status}]")
    await message.answer("\n".join(lines), parse_mode="HTML")

