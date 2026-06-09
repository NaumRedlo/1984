from typing import Optional, Dict, Any, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User


def get_real_reply(message):
    """Return ``message.reply_to_message`` only when it is a genuine reply to
    another message — never the forum-topic root that Telegram auto-attaches to
    top-level posts inside a topic.

    Inside a forum topic every top-level message carries a ``reply_to_message``
    pointing at the ``forum_topic_created`` service message, whose ``from_user``
    is whoever opened the topic. Treating that as a real reply made bare
    commands (``pf`` / ``rs`` / ``duels``) resolve to the topic creator instead
    of the sender — players in the duel topic got the topic owner's card. We
    filter it out two ways: the service message id equals the thread id, and the
    ``forum_topic_created`` marker is present.
    """
    reply = getattr(message, "reply_to_message", None)
    if reply is None:
        return None
    # Service message that opened the topic (auto-filled as the reply target
    # for top-level topic posts).
    if getattr(reply, "forum_topic_created", None) is not None:
        return None
    thread_id = getattr(message, "message_thread_id", None)
    if thread_id is not None and getattr(reply, "message_id", None) == thread_id:
        return None
    return reply


class OsuUserLookupError(Exception):
    pass


class OsuUserNotFoundError(OsuUserLookupError):
    pass


async def resolve_osu_user(api_client, query: str) -> Optional[Dict[str, Any]]:
    """Resolve an osu! user query via the API client."""
    query = query.strip().lstrip("@").strip()
    if not query:
        return None

    lowered = query.lower()
    if lowered.startswith("id:"):
        try:
            osu_id = int(query[3:].strip())
        except ValueError:
            return None
        return await api_client.get_user_data(osu_id)

    return await api_client.get_user_data(query)


async def get_registered_user(
    session: AsyncSession, telegram_id: int, chat_id: int,
) -> Optional[User]:
    """Fetch the linked user for ``telegram_id`` **within tenant ``chat_id``**."""
    stmt = select(User).where(
        User.chat_id == chat_id,
        User.telegram_id == telegram_id,
        User.osu_user_id.isnot(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_any_user_by_telegram_id(
    session: AsyncSession, telegram_id: int, chat_id: int,
) -> Optional[User]:
    """Fetch any user row for ``telegram_id`` **within tenant ``chat_id``**."""
    stmt = select(User).where(
        User.chat_id == chat_id, User.telegram_id == telegram_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_identity_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Any user row for this Telegram identity, **across all groups** — for
    global identity ops (OAuth link/relink/unlink) that aren't tied to one
    tenant. Returns the most recent row, or None."""
    stmt = (
        select(User)
        .where(User.telegram_id == telegram_id)
        .order_by(User.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_registered_identity_user(session: AsyncSession, telegram_id: int) -> Optional[User]:
    """Like :func:`get_identity_user` but only an osu!-linked row — for global
    'is this identity registered anywhere?' checks (e.g. ``link`` in DM)."""
    stmt = (
        select(User)
        .where(User.telegram_id == telegram_id, User.osu_user_id.isnot(None))
        .order_by(User.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_reply_target_user(
    session: AsyncSession, message, *, registered_only: bool = True,
    chat_id: Optional[int] = None,
) -> Optional[User]:
    """If ``message`` is a reply to someone else's message, return that user's
    DB row (or None) **scoped to the given tenant** (``chat_id``; defaults to the
    chat the message lives in). Skips bots, the sender themselves, and (by
    default) users who haven't linked an osu! account.

    ``chat_id`` lets callers pass the *effective tenant* (the DM-selected group)
    instead of the literal chat, so reply-lookups work in a private chat too.

    Used by /pf, /rs and /duels to turn ``[reply] pf`` into "show that person's
    card", which is what people expect from a Telegram-native UX.
    """
    reply = get_real_reply(message)
    if not reply:
        return None
    rfrom = getattr(reply, "from_user", None)
    if not rfrom or getattr(rfrom, "is_bot", False):
        return None
    if rfrom.id == message.from_user.id:
        return None  # replying to yourself behaves like no reply
    if chat_id is None:
        chat_id = message.chat.id
    if registered_only:
        return await get_registered_user(session, rfrom.id, chat_id)
    return await get_any_user_by_telegram_id(session, rfrom.id, chat_id)


async def get_registered_user_by_osu(
    session: AsyncSession,
    chat_id: int,
    osu_user_id: Optional[int] = None,
    osu_username: Optional[str] = None,
) -> Optional[User]:
    """Fetch a registered user by osu! account identity **within tenant
    ``chat_id``**."""
    if osu_user_id is not None:
        stmt = select(User).where(
            User.chat_id == chat_id, User.osu_user_id == osu_user_id,
        )
        user = (await session.execute(stmt)).scalar_one_or_none()
        if user:
            return user

    if osu_username:
        stmt = select(User).where(
            User.chat_id == chat_id,
            func.lower(User.osu_username) == osu_username.lower(),
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    return None


async def resolve_registered_user(
    session: AsyncSession,
    api_client,
    query: str,
    chat_id: int,
) -> Tuple[Optional[User], Optional[Dict[str, Any]]]:
    """Resolve an osu! query and match it to a registered user in ``chat_id``."""
    user_data = await resolve_osu_user(api_client, query)
    if not user_data:
        return None, None

    user = await get_registered_user_by_osu(
        session,
        chat_id,
        osu_user_id=user_data.get("id"),
        osu_username=user_data.get("username"),
    )
    return user, user_data


async def resolve_osu_query_status(
    session: AsyncSession,
    api_client,
    query: str,
    chat_id: int,
) -> Tuple[Optional[User], Optional[Dict[str, Any]], str]:
    """Resolve an osu! query and report whether it was found and registered
    **in tenant ``chat_id``**.

    Returns (registered_user, user_data, status) where status is one of:
    - "registered": found in osu! and locally registered
    - "unregistered": found in osu! but not registered locally
    - "not_found": not found in osu!
    """
    user_data = await resolve_osu_user(api_client, query)
    if not user_data:
        return None, None, "not_found"

    user = await get_registered_user_by_osu(
        session,
        chat_id,
        osu_user_id=user_data.get("id"),
        osu_username=user_data.get("username"),
    )
    if user:
        return user, user_data, "registered"
    return None, user_data, "unregistered"
