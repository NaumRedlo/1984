import os
import shutil
from datetime import datetime

from sqlalchemy import text

from config.settings import GROUP_CHAT_ID
from db.database import Base
from utils.logger import get_logger

logger = get_logger("db.migration.add_tenant_chat_id")

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
    dst = f"{db_path}.bak-pre-tenant-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(db_path, dst)
        logger.warning(f"tenant migration: {reason} — backed up DB to {dst}")
    except Exception as e:
        logger.error(f"tenant migration: DB backup to {dst} failed: {e}", exc_info=True)

async def run_tenant_chat_id_migration(engine) -> None:
    async with engine.connect() as conn:
        if not await _table_exists(conn, "users"):
            return
        ucols = await _columns(conn, "users")
        if "chat_id" in ucols:

            if await _table_exists(conn, "users_old"):
                async with engine.begin() as c2:
                    await c2.execute(text("DROP TABLE users_old"))
                logger.warning("tenant migration: dropped leftover users_old")
            return

    if not GROUP_CHAT_ID:
        async with engine.connect() as conn:
            n_users = (await conn.execute(text("SELECT COUNT(*) FROM users"))).scalar() or 0
        if n_users:
            raise RuntimeError(
                f"add_tenant_chat_id: GROUP_CHAT_ID is not set but `users` has "
                f"{n_users} existing row(s). Backfilling chat_id=0 would strand "
                f"them under a non-existent group. Set GROUP_CHAT_ID to the main "
                f"group's chat.id in the bot's .env, then restart. (No data has "
                f"been modified.)"
            )

    _backup_sqlite(engine, "rebuilding users with per-tenant chat_id")
    backfill = int(GROUP_CHAT_ID) if GROUP_CHAT_ID else 0

    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA legacy_alter_table=ON"))
        await conn.execute(text("DROP TABLE IF EXISTS users_old"))
        await conn.execute(text("ALTER TABLE users RENAME TO users_old"))
        await conn.execute(text("PRAGMA legacy_alter_table=OFF"))

    async with engine.begin() as conn:
        old_idx = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='users_old' AND name NOT LIKE 'sqlite_autoindex_%'"
        ))).fetchall()
        for (idx_name,) in old_idx:
            await conn.execute(text(f'DROP INDEX IF EXISTS "{idx_name}"'))
        await conn.run_sync(Base.metadata.create_all)

    async with engine.begin() as conn:
        old_cols = await _columns(conn, "users_old")
        new_cols = set(await _columns(conn, "users"))
        shared = [c for c in old_cols if c in new_cols and c != "chat_id"]
        collist = ", ".join(shared)
        await conn.execute(text(
            f"INSERT INTO users ({collist}, chat_id) "
            f"SELECT {collist}, :backfill FROM users_old"
        ), {"backfill": backfill})
        await conn.execute(text("DROP TABLE users_old"))

    logger.warning(
        f"tenant migration: rebuilt users with chat_id (backfill={backfill}); "
        f"copied {len(shared)} columns"
    )

__all__ = ["run_tenant_chat_id_migration"]
