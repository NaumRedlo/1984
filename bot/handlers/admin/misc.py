from aiogram import Router, types, F
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger
from sqlalchemy import select, delete, asc

logger = get_logger(__name__)

router = Router(name="admin_misc")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


@router.message(TextTriggerFilter("whois"))
async def cmd_whois(message: types.Message, trigger_args: TriggerArgs):
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
        user = (await session.execute(
            select(User).where(User.id == target)
        )).scalar_one_or_none()
        if not user:
            user = (await session.execute(
                select(User).where(User.telegram_id == target).order_by(User.id.desc())
            )).scalars().first()

        if not user:
            await message.answer(f"Пользователь с id={target} не найден ни в User.id, ни в telegram_id.")
            return

        token = (await session.execute(
            select(OAuthToken).where(OAuthToken.telegram_id == user.telegram_id)
        )).scalar_one_or_none()

    last_seen = user.last_seen_at.strftime("%Y-%m-%d %H:%M") if getattr(user, "last_seen_at", None) else "—"
    if token:
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
                select(User).where(User.telegram_id == target).order_by(User.id.desc())
            )).scalars().first()
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


# ─── Users list ───────────────────────────────────────────────────────────────

@router.message(TextTriggerFilter("userslist"))
async def cmd_userslist(message: types.Message, trigger_args: TriggerArgs):
    """Show all registered users sorted by last_seen_at (oldest first)."""
    async with get_db_session() as session:
        users = (await session.execute(
            select(User).order_by(asc(User.last_seen_at))
        )).scalars().all()

    if not users:
        await message.answer("Нет зарегистрированных пользователей.")
        return

    lines = [f"<b>Все пользователи ({len(users)})</b>, сортировка по last_seen:\n"]
    for u in users:
        seen = u.last_seen_at.strftime("%Y-%m-%d") if u.last_seen_at else "never"
        name = escape_html(u.osu_username or u.telegram_username or "—")
        lines.append(f"<code>{u.id:>4}</code> │ {name} │ {seen}")

    text = "\n".join(lines)
    if len(text) > 4000:
        for i in range(0, len(text), 4000):
            chunk = text[i:i + 4000]
            await message.answer(chunk, parse_mode="HTML")
    else:
        await message.answer(text, parse_mode="HTML")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ─── Purge user (cascade delete) ─────────────────────────────────────────────

_PURGE_PENDING: dict[str, int] = {}


async def _chat_label(bot, chat_id) -> str:
    """Human-readable '<title> (<id>)' for a tenant chat, falling back to the
    raw id when the bot can't resolve the chat (e.g. it was removed from it)."""
    try:
        chat = await bot.get_chat(chat_id)
        title = getattr(chat, "title", None) or getattr(chat, "full_name", None)
        if title:
            return f"{escape_html(title)} (<code>{chat_id}</code>)"
    except Exception:
        pass
    return f"<code>{chat_id}</code>"


@router.message(TextTriggerFilter("purgeuser"))
async def cmd_purge_user(message: types.Message, trigger_args: TriggerArgs):
    raw = (trigger_args.args or "").strip()
    if not raw or not raw.lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>purgeuser &lt;user_id или telegram_id&gt;</code>",
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
                select(User).where(User.telegram_id == target).order_by(User.id.desc())
            )).scalars().first()
        if not user:
            await message.answer(f"Пользователь с id={target} не найден.")
            return

        target_id = user.id
        target_chat = user.chat_id
        target_tg = user.telegram_id
        osu_name = user.osu_username
        osu_id = user.osu_user_id
        last_seen = user.last_seen_at.strftime("%Y-%m-%d %H:%M") if user.last_seen_at else "—"
        # Every беседа this Telegram identity is registered in (multi-tenant).
        siblings = [
            (u.id, u.chat_id) for u in (await session.execute(
                select(User).where(User.telegram_id == target_tg).order_by(asc(User.id))
            )).scalars().all()
        ]

    confirm_id = uuid4().hex[:12]
    _PURGE_PENDING[confirm_id] = target_id

    others = [(uid, cid) for (uid, cid) in siblings if uid != target_id]
    lines = [
        "⚠️ <b>Удалить пользователя?</b>\n",
        f"<b>osu!:</b> {escape_html(osu_name or '—')} (osu_id <code>{osu_id or '—'}</code>)",
        f"<b>Telegram:</b> <code>{target_tg or '—'}</code>",
        f"<b>Last seen:</b> {last_seen}",
        "\n🗑 <b>Удаляется регистрация в беседе:</b>",
        f"• {await _chat_label(message.bot, target_chat)} · row <code>{target_id}</code>",
    ]
    if others:
        lines.append(f"\n✅ <b>Останутся ({len(others)}) — не трогаем:</b>")
        for uid, cid in others:
            lines.append(f"• {await _chat_label(message.bot, cid)} · row <code>{uid}</code>")
        lines.append("\n🔑 osu! OAuth-привязка <b>сохранится</b> (есть другие беседы).")
    else:
        lines.append("\n🔑 Это <b>последняя</b> регистрация — osu! OAuth-привязка тоже будет удалена.")
    lines.append("\nУдаляются: рейтинги, скоры, дуэли, сабмишены, прогресс, render-настройки.")

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Удалить", callback_data=f"purge_confirm:{confirm_id}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"purge_cancel:{confirm_id}"),
    ]])

    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("purge_confirm:"))
async def purge_confirm(callback: types.CallbackQuery):
    confirm_id = callback.data.split(":", 1)[1]
    user_id = _PURGE_PENDING.pop(confirm_id, None)
    if user_id is None:
        await callback.answer("Запрос устарел.", show_alert=True)
        return

    from db.models.duel_rating import DuelRating
    from db.models.duel import Duel
    from db.models.duel_round import DuelRound
    from db.models.oauth_token import OAuthToken
    from db.models.title_progress import UserTitleProgress
    from db.models.render_settings import UserRenderSettings
    from db.models.best_score import UserBestScore
    from db.models.season_snapshot import SeasonSnapshot
    from db.models.map_attempt import UserMapAttempt
    from db.models.bounty import Submission

    async with get_db_session() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if not user:
            await callback.message.edit_text("Пользователь уже удалён.")
            await callback.answer()
            return

        username = user.osu_username or str(user_id)

        await session.execute(delete(DuelRating).where(DuelRating.user_id == user_id))
        await session.execute(delete(UserTitleProgress).where(UserTitleProgress.user_id == user_id))
        await session.execute(delete(UserRenderSettings).where(UserRenderSettings.user_id == user_id))
        await session.execute(delete(UserBestScore).where(UserBestScore.user_id == user_id))
        await session.execute(delete(SeasonSnapshot).where(SeasonSnapshot.user_id == user_id))
        await session.execute(delete(UserMapAttempt).where(UserMapAttempt.user_id == user_id))
        await session.execute(delete(Submission).where(Submission.user_id == user_id))

        # Delete duel rounds for duels involving this user, then the duels themselves
        duel_ids_stmt = select(Duel.id).where(
            (Duel.player1_user_id == user_id) | (Duel.player2_user_id == user_id)
        )
        duel_ids = (await session.execute(duel_ids_stmt)).scalars().all()
        if duel_ids:
            await session.execute(delete(DuelRound).where(DuelRound.duel_id.in_(duel_ids)))
            await session.execute(delete(Duel).where(Duel.id.in_(duel_ids)))

        await session.execute(delete(User).where(User.id == user_id))

        # OAuth is global per telegram_id — remove it only if this was the user's
        # LAST registration; otherwise their osu! link stays valid in other chats.
        other_exists = (await session.execute(
            select(User.id).where(User.telegram_id == user.telegram_id).limit(1)
        )).scalar_one_or_none()
        oauth_removed = other_exists is None
        if oauth_removed:
            await session.execute(
                delete(OAuthToken).where(OAuthToken.telegram_id == user.telegram_id)
            )

        await session.commit()

    logger.info(
        f"User {username} (row {user_id}) purged by admin {callback.from_user.id} "
        f"(oauth_removed={oauth_removed})"
    )
    oauth_note = (
        "\n🔑 osu! OAuth-привязка удалена (была последняя регистрация)."
        if oauth_removed else
        "\n🔑 osu! OAuth-привязка сохранена (есть другие беседы)."
    )
    await callback.message.edit_text(
        f"✅ Пользователь <b>{escape_html(username)}</b> (row {user_id}) "
        f"удалён из беседы.{oauth_note}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("purge_cancel:"))
async def purge_cancel(callback: types.CallbackQuery):
    confirm_id = callback.data.split(":", 1)[1]
    _PURGE_PENDING.pop(confirm_id, None)
    await callback.message.edit_text("Отменено.")
    await callback.answer()