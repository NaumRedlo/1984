import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
from aiohttp import web
from aiogram import Bot
from sqlalchemy import delete, select, update

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
from db.models.oauth_pending import OAuthPending
from services.render_farm import http as render_farm_http
from utils.aio import spawn
from utils.crypto import encrypt_token
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger("oauth.server")

_STATE_TTL = timedelta(minutes=15)
_bot: Optional[Bot] = None

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

async def _sweep_expired_states(session, now: datetime) -> None:
    await session.execute(delete(OAuthPending).where(OAuthPending.issued_at < now - _STATE_TTL))

async def _take_state(state: str) -> Optional[OAuthPending]:
    now = datetime.now(timezone.utc)
    async with get_db_session() as session:
        await _sweep_expired_states(session, now)
        entry = await session.get(OAuthPending, state)
        if entry is not None:
            await session.delete(entry)
        await session.commit()
    if entry is None or now - _aware(entry.issued_at) > _STATE_TTL:
        return None
    return entry

def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot

async def generate_oauth_url(telegram_id: int) -> str:
    now = datetime.now(timezone.utc)
    state = secrets.token_urlsafe(32)
    async with get_db_session() as session:
        await _sweep_expired_states(session, now)
        session.add(OAuthPending(state=state, telegram_id=telegram_id, issued_at=now))
        await session.commit()
    return (
        f"https://osu.ppy.sh/oauth/authorize"
        f"?client_id={OSU_CLIENT_ID}"
        f"&redirect_uri={OSU_OAUTH_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={OSU_OAUTH_SCOPES.replace(' ', '+')}"
        f"&state={state}"
    )

async def track_link_message(telegram_id: int, chat_id: int, message_id: int) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(OAuthPending)
            .where(OAuthPending.telegram_id == telegram_id, OAuthPending.message_id.is_(None))
            .values(chat_id=chat_id, message_id=message_id)
        )
        await session.commit()

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

async def _notify_telegram(telegram_id: int, osu_username: str,
                           link_msg: Optional[tuple[int, int]] = None) -> None:
    if not _bot:
        logger.error("_notify_telegram: bot not set")
        return
    try:
        logger.info(f"_notify_telegram: tg={telegram_id}, link_msg={link_msg}")
        if link_msg:
            chat_id, msg_id = link_msg
            try:
                await _bot.delete_message(chat_id, msg_id)
            except Exception as e:
                logger.warning(f"Failed to delete link message: {e}")

            lang = (await get_language(telegram_id)).lower()
            success_msg = await _bot.send_message(
                chat_id,
                t("oauth.notify_linked", lang, username=escape_html(osu_username)),
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
            text=t("oauth.error_page"),
            content_type="text/html",
        )

    if not code or not state:
        return web.Response(
            text=t("oauth.bad_request"),
            content_type="text/html",
            status=400,
        )

    entry = await _take_state(state)
    if entry is None:
        return web.Response(
            text=t("oauth.link_expired"),
            content_type="text/html",
            status=400,
        )
    telegram_id = entry.telegram_id
    link_msg = (entry.chat_id, entry.message_id) if entry.message_id else None
    lang = (await get_language(telegram_id)).lower()

    token_data = await _exchange_code(code)
    if not token_data:
        return web.Response(
            text=t("oauth.token_error", lang),
            content_type="text/html",
            status=500,
        )

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 86400)

    osu_user = await _get_oauth_user(access_token)
    if not osu_user:
        return web.Response(
            text=t("oauth.user_fetch_failed", lang),
            content_type="text/html",
            status=500,
        )

    osu_id = osu_user["id"]
    osu_username = osu_user["username"]
    now = datetime.now(timezone.utc)
    token_expiry = now + timedelta(seconds=expires_in)

    async with get_db_session() as session:

        stmt = select(User).where(User.telegram_id == telegram_id).order_by(User.id.desc())
        rows = (await session.execute(stmt)).scalars().all()

        if not rows:
            return web.Response(
                text=t("oauth.not_registered", lang),
                content_type="text/html",
                status=400,
            )

        bound_osu_ids = {u.osu_user_id for u in rows if u.osu_user_id}
        if bound_osu_ids and osu_id not in bound_osu_ids:
            other_id = next(iter(bound_osu_ids))
            return web.Response(
                text=t("oauth.account_conflict", lang, other_id=other_id, username=escape_html(osu_username), osu_id=osu_id),
                content_type="text/html",
                status=409,
            )

        for u in rows:
            if not u.osu_user_id:
                u.osu_user_id = osu_id
                u.osu_username = osu_username

        token_stmt = select(OAuthToken).where(OAuthToken.telegram_id == telegram_id)
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
                telegram_id=telegram_id,
                access_token_enc=access_enc,
                refresh_token_enc=refresh_enc,
                token_expiry=token_expiry,
                scopes=OSU_OAUTH_SCOPES,
            ))

        await session.commit()

    logger.info(f"OAuth linked: tg={telegram_id} -> osu={osu_username} (ID {osu_id})")
    spawn(_notify_telegram(telegram_id, osu_username, link_msg), name=f"oauth_notify_{telegram_id}")

    return web.Response(
        text=t("oauth.success_page", lang, username=escape_html(osu_username)),
        content_type="text/html",
    )

class OAuthServer:
    def __init__(self, port: int = OAUTH_SERVER_PORT):
        self.port = port
        self.app = web.Application()
        self.app.router.add_get("/oauth/callback", handle_callback)

        render_farm_http.install(self.app)

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
