"""Migration: per-tenant `chat_id` on users (multi-tenant isolation).

Rebuilds the `users` table so each row is scoped to a Telegram group (chat_id).
The old single-column UNIQUE on `telegram_id` / `osu_user_id` becomes composite
UNIQUE(chat_id, telegram_id) and UNIQUE(chat_id, osu_user_id) — SQLite cannot
drop/alter a column-level UNIQUE via ALTER, so the table is rebuilt.

Existing rows are backfilled with `chat_id = GROUP_CHAT_ID` (the single legacy
group) or 0 if that setting is unset.

Idempotent: if `users` already has a `chat_id` column this is a no-op (and it
cleans up a leftover `users_old` from a half-finished run).

Must run *after* duel_overhaul so the legacy `bsk_*` → `duel_*` user-column
renames have already happened — otherwise the column-intersection copy here
would silently drop the still-`bsk_*`-named columns.

Rebuild technique (SQLite-safe): rename `users` aside with
`PRAGMA legacy_alter_table=ON` so child tables' foreign keys keep pointing at
`users` (not the renamed-aside copy), let `create_all` recreate `users` with the
new schema + indexes, copy rows back, then drop the old table. Foreign-key
enforcement is off (project never sets `PRAGMA foreign_keys=ON`) and `id` values
are preserved, so child FKs stay valid throughout.
"""

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
    """Snapshot the SQLite file before the destructive rebuild. No-op for
    in-memory / missing files."""
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
            return  # fresh install — create_all already made users with chat_id
        ucols = await _columns(conn, "users")
        if "chat_id" in ucols:
            # Already migrated; tidy up a leftover from a half-finished run.
            if await _table_exists(conn, "users_old"):
                async with engine.begin() as c2:
                    await c2.execute(text("DROP TABLE users_old"))
                logger.warning("tenant migration: dropped leftover users_old")
            return

    # Safeguard: refuse to silently strand existing registrations under chat_id=0.
    # The backfill sets every legacy row's chat_id to GROUP_CHAT_ID; if that's
    # unset while real users exist, they'd all land under a non-existent group
    # (chat_id=0) and look unregistered in their actual chat. Fail loudly so the
    # operator sets GROUP_CHAT_ID first (data is untouched — nothing is renamed
    # or dropped before this point).
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

    # 1. Rename old table aside WITHOUT rewriting child FK references.
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA legacy_alter_table=ON"))
        await conn.execute(text("DROP TABLE IF EXISTS users_old"))
        await conn.execute(text("ALTER TABLE users RENAME TO users_old"))
        await conn.execute(text("PRAGMA legacy_alter_table=OFF"))

    # 2. Recreate `users` from the model (chat_id + composite UNIQUE + indexes).
    #    SQLite index names are global: the old `ix_users_*` indexes followed
    #    `users` into `users_old` but kept their names, so create_all would
    #    collide recreating them ("index ix_users_osu_user_id already exists").
    #    Drop the renamed-aside named indexes first (they vanish with users_old
    #    anyway). Auto-indexes (sqlite_autoindex_*) belong to UNIQUE constraints
    #    and can't/needn't be dropped explicitly.
    async with engine.begin() as conn:
        old_idx = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='users_old' AND name NOT LIKE 'sqlite_autoindex_%'"
        ))).fetchall()
        for (idx_name,) in old_idx:
            await conn.execute(text(f'DROP INDEX IF EXISTS "{idx_name}"'))
        await conn.run_sync(Base.metadata.create_all)

    # 3. Copy rows back, backfilling chat_id. Copy the column intersection so a
    #    stale-schema users_old (missing/extra columns) can't break the insert.
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
