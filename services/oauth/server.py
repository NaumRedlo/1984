"""
Lightweight aiohttp server for osu! OAuth2 callback.
Runs on localhost — Caddy reverse-proxies HTTPS → here.
"""

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from aiohttp import web
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
from utils.logger import get_logger

logger = get_logger("oauth.server")

_pending_states: dict[str, int] = {}  # state -> telegram_id


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
    token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

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

        user.oauth_access_token = access_token
        user.oauth_refresh_token = refresh_token
        user.oauth_token_expiry = token_expiry
        if not user.osu_user_id:
            user.osu_user_id = osu_id
            user.osu_username = osu_username
        await session.commit()

    logger.info(f"OAuth linked: tg={telegram_id} -> osu={osu_username} (ID {osu_id})")

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
