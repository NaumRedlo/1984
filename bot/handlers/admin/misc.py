from aiogram import Router, types

from bot.filters import TextTriggerFilter, TriggerArgs
from db.database import get_db_session
from db.models.user import User
from utils.admin_check import AdminFilter
from utils.formatting.text import escape_html
from utils.logger import get_logger
from sqlalchemy import select

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
