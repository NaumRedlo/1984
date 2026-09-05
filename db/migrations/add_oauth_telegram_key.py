import os
import shutil
from datetime import datetime

from sqlalchemy import text

from db.database import Base
from utils.logger import get_logger

logger = get_logger("db.migration.add_oauth_telegram_key")

async def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()]

async def _table_exists(conn, name: str) -> bool:
    row = (await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :n"),
        {"n": name},
    )).fetchone()
    return row is not None

def _backup_sqlite(engine, reason: str) -> None:
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return
    dst = f"{db_path}.bak-pre-oauthkey-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(db_path, dst)
        logger.warning(f"oauth re-key migration: {reason} — backed up DB to {dst}")
    except Exception as e:
        logger.error(f"oauth re-key migration: DB backup to {dst} failed: {e}", exc_info=True)

async def run_oauth_telegram_key_migration(engine) -> None:
    async with engine.connect() as conn:
        if not await _table_exists(conn, "oauth_tokens"):
            return
        cols = await _columns(conn, "oauth_tokens")
        if "telegram_id" in cols:
            if await _table_exists(conn, "oauth_tokens_old"):
                async with engine.begin() as c2:
                    await c2.execute(text("DROP TABLE oauth_tokens_old"))
                logger.warning("oauth re-key migration: dropped leftover oauth_tokens_old")
            return
        if "user_id" not in cols:
            return

    _backup_sqlite(engine, "re-keying oauth_tokens to telegram_id")

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA legacy_alter_table=ON"))
        await conn.execute(text("DROP TABLE IF EXISTS oauth_tokens_old"))
        await conn.execute(text("ALTER TABLE oauth_tokens RENAME TO oauth_tokens_old"))
        await conn.execute(text("PRAGMA legacy_alter_table=OFF"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.begin() as conn:
        old_cols = await _columns(conn, "oauth_tokens_old")
        new_cols = set(await _columns(conn, "oauth_tokens"))
        shared = [
            c for c in old_cols
            if c in new_cols and c not in ("telegram_id", "user_id", "id")
        ]
        select_cols = ", ".join(f"o.{c}" for c in shared)
        insert_cols = ", ".join(shared)
        await conn.execute(text(
            f"INSERT INTO oauth_tokens (telegram_id, {insert_cols}) "
            f"SELECT u.telegram_id, {select_cols} "
            f"FROM oauth_tokens_old o JOIN users u ON u.id = o.user_id"
        ))
        await conn.execute(text("DROP TABLE oauth_tokens_old"))

    logger.warning(
        f"oauth re-key migration: rebuilt oauth_tokens keyed by telegram_id; "
        f"copied {len(shared)} extra columns"
    )

__all__ = ["run_oauth_telegram_key_migration"]
