from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from db.database import Base


class RenderWorkerToken(Base):
    """One machine's right to take renders from this bot.

    There used to be a single shared secret for everybody. Two things were
    wrong with that and both showed up in practice: it had to be copied by
    hand out of a chat, and a sixty-four character string copied slightly
    wrong looks exactly like one copied right — two people once ran for days
    against a token that differed from the server's. And because everyone held
    the same string, removing one person meant changing it for all of them.

    So each machine gets its own, handed out in exchange for a short code.

    **The token itself is not stored here.** Only `sha256` of it: this is a
    bearer secret, so a copy of this table would otherwise be a working key for
    every machine on the farm. The string is shown once, to the program that
    redeemed the code, and never again by anybody.
    """

    __tablename__ = "render_worker_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # sha256 hex. Indexed because every request a worker makes looks it up.
    digest = Column(String(64), unique=True, nullable=False, index=True)
    # Who asked for the code this came from, so `rdrw` can say whose machine
    # a worker is and so one person can be removed without touching the rest.
    issued_to = Column(BigInteger, nullable=True, index=True)
    issued_name = Column(String(128), nullable=True)
    # What the machine calls itself, learned the first time it uses this.
    worker = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen = Column(DateTime, nullable=True)
    # Set rather than deleted: a token that stops working is worth being able
    # to explain afterwards, and a row that is gone explains nothing.
    revoked_at = Column(DateTime, nullable=True)
