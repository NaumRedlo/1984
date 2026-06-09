from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime

from db.database import Base


class DmActiveTenant(Base):
    """Which group's data a user sees when talking to the bot in a private chat.

    The bot is multi-tenant: every ``users`` row (and everything hanging off it)
    is scoped to the Telegram group it was registered in (``users.chat_id``). In
    a group the tenant is simply the chat. In a DM there is no group, so the user
    picks one of the groups they're registered in; that choice is stored here,
    keyed by Telegram identity (global, like OAuth), and applied to every
    data-scoped command issued in that private chat.
    """

    __tablename__ = "dm_active_tenant"

    telegram_id = Column(BigInteger, primary_key=True)
    chat_id = Column(BigInteger, nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<DmActiveTenant(tg={self.telegram_id}, chat_id={self.chat_id})>"
