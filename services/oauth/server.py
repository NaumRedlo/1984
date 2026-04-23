"""
Lightweight aiohttp server for osu! OAuth2 callback.
Runs on localhost — Caddy reverse-proxies HTTPS → here.
Tokens are stored encrypted in a separate oauth_tokens table.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from aiohttp import web
from aiogram import Bot
from sqlalchemy import select

from config.settings import (
    OSU_CLIENT_ID,
    OSU_CLIENT_SECRET,
    OSU_OAUTH_REDIRECT_URI,
    OSU_OAUTH_SCOPES,
    OAUTH_SERVER_PORT,
)
from db.database import get_db_session
from db.models.user import User
from db.models.oauth_token import OAuthToken
from utils.crypto import encrypt_token
from utils.logger import get_logger

logger = get_logger("oauth.server")

_pending_states: dict[str, int] = {}
_pending_messages: dict[int, tuple[int, int]] = {}  # telegram_id -> (chat_id, message_id)
_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def generate_oauth_url(telegram_id: int) -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = telegram_id
    return (
        f"https://osu.ppy.sh/oauth/authorize"
        f"?client_id={OSU_CLIENT_ID}"
        f"&redirect_uri={OSU_OAUTH_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={OSU_OAUTH_SCOPES.replace(' ', '+')}"
        f"&state={state}"
    )


def track_link_message(telegram_id: int, chat_id: int, message_id: int) -> None:
    _pending_messages[telegram_id] = (chat_id, message_id)


async def _exchange_code(code: str) -> Optional[dict]:
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": OSU_CLIENT_ID,
            "client_secret": OSU_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": OSU_OAUTH_REDIRECT_URI,
        }
        async with session.post("https://osu.ppy.sh/oauth/token", data=data) as resp:
            if resp.status != 200:
                error = await resp.text()
                logger.error(f"Token exchange failed: {resp.status} {error[:200]}")
                return None
            return await resp.json()


async def _get_oauth_user(access_token: str) -> Optional[dict]:
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with session.get("https://osu.ppy.sh/api/v2/me/osu", headers=headers) as resp:
            if resp.status != 200:
                return None
            return await resp.json()


async def _notify_telegram(telegram_id: int, osu_username: str) -> None:
    if not _bot:
        logger.error("_notify_telegram: bot not set")
        return
    try:
        link_msg = _pending_messages.pop(telegram_id, None)
        logger.info(f"_notify_telegram: tg={telegram_id}, link_msg={link_msg}")
        if link_msg:
            chat_id, msg_id = link_msg
            try:
                await _bot.delete_message(chat_id, msg_id)
            except Exception as e:
                logger.warning(f"Failed to delete link message: {e}")

            success_msg = await _bot.send_message(
                chat_id,
                f"OAuth <b>{osu_username}</b> привязан.",
                parse_mode="HTML",
            )
            await asyncio.sleep(10)
            try:
                await success_msg.delete()
            except Exception as e:
                logger.warning(f"Failed to delete success message: {e}")
        else:
            logger.warning(f"_notify_telegram: no pending message for tg={telegram_id}")
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}", exc_info=True)


async def handle_callback(request: web.Request) -> web.Response:
    code = request.query.get("code")
    state = request.query.get("state")
    error = request.query.get("error")

    if error:
        logger.warning(f"OAuth error: {error}")
        return web.Response(
            text="<h2>Ошибка авторизации</h2><p>Попробуйте снова через бота.</p>",
            content_type="text/html",
        )

    if not code or not state:
        return web.Response(
            text="<h2>Неверный запрос</h2>",
            content_type="text/html",
            status=400,
        )

    telegram_id = _pending_states.pop(state, None)
    if telegram_id is None:
        return web.Response(
            text="<h2>Ссылка устарела</h2><p>Используйте команду link заново.</p>",
            content_type="text/html",
            status=400,
        )

    token_data = await _exchange_code(code)
    if not token_data:
        return web.Response(
            text="<h2>Ошибка получения токена</h2><p>Попробуйте снова.</p>",
            content_type="text/html",
            status=500,
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 86400)

    osu_user = await _get_oauth_user(access_token)
    if not osu_user:
        return web.Response(
            text="<h2>Не удалось получить данные osu!</h2>",
            content_type="text/html",
            status=500,
        )

    osu_id = osu_user["id"]
    osu_username = osu_user["username"]
    now = datetime.now(timezone.utc)
    token_expiry = now + timedelta(seconds=expires_in)

    async with get_db_session() as session:
        stmt = select(User).where(User.telegram_id == telegram_id)
        user = (await session.execute(stmt)).scalar_one_or_none()

        if not user:
            return web.Response(
                text="<h2>Сначала зарегистрируйтесь</h2>"
                     "<p>Используйте команду <code>register</code> в боте, затем <code>link</code>.</p>",
                content_type="text/html",
                status=400,
            )

        if user.osu_user_id and user.osu_user_id != osu_id:
            return web.Response(
                text=f"<h2>Конфликт аккаунтов</h2>"
                     f"<p>Ваш Telegram привязан к osu! ID {user.osu_user_id}, "
                     f"но вы авторизовались как {osu_username} (ID {osu_id}).</p>"
                     f"<p>Используйте <code>unlink</code>, затем <code>register</code> заново.</p>",
                content_type="text/html",
                status=409,
            )

        if not user.osu_user_id:
            user.osu_user_id = osu_id
            user.osu_username = osu_username

        token_stmt = select(OAuthToken).where(OAuthToken.user_id == user.id)
        existing = (await session.execute(token_stmt)).scalar_one_or_none()

        access_enc = encrypt_token(access_token)
        refresh_enc = encrypt_token(refresh_token) if refresh_token else None

        if existing:
            existing.access_token_enc = access_enc
            existing.refresh_token_enc = refresh_enc
            existing.token_expiry = token_expiry
            existing.scopes = OSU_OAUTH_SCOPES
            existing.updated_at = now
        else:
            session.add(OAuthToken(
                user_id=user.id,
                access_token_enc=access_enc,
                refresh_token_enc=refresh_enc,
                token_expiry=token_expiry,
                scopes=OSU_OAUTH_SCOPES,
            ))

        await session.commit()

    logger.info(f"OAuth linked: tg={telegram_id} -> osu={osu_username} (ID {osu_id})")
    asyncio.create_task(_notify_telegram(telegram_id, osu_username))

    return web.Response(
        text=f"<h2>Привязка успешна!</h2>"
             f"<p>Аккаунт <b>{osu_username}</b> привязан.</p>"
             f"<p>Можете вернуться в Telegram.</p>",
        content_type="text/html",
    )


class OAuthServer:
    def __init__(self, port: int = OAUTH_SERVER_PORT):
        self.port = port
        self.app = web.Application()
        self.app.router.add_get("/oauth/callback", handle_callback)
        self.runner: Optional[web.AppRunner] = None

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await site.start()
        logger.info(f"OAuth server started on 127.0.0.1:{self.port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            logger.info("OAuth server stopped")
