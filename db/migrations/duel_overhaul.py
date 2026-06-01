"""Migration: BSK → DUEL overhaul.

Converts a legacy BSK database to the new single-track-TrueSkill duel schema.
Idempotent and safe on both fresh installs (nothing to convert) and existing
BSK databases.  Runs *after* ``Base.metadata.create_all``, which has already
created the empty new tables (``duels``, ``duel_rounds``, ``duel_ratings``,
``duel_map_pool``) and — on fresh installs — the ``users`` /
``season_snapshots`` columns under their ``duel_*`` names.

Actions:
  * DROP the three duel tables' BSK predecessors and the removed duel-winner ML
    table — these are reset to zero by design (``bsk_duels``,
    ``bsk_duel_rounds``, ``bsk_ratings``, ``bsk_ml_runs``).  The empty
    ``duels`` / ``duel_rounds`` / ``duel_ratings`` made by ``create_all`` stay.
  * Move the map-type classifier pool (data-preserving): copy ``bsk_map_pool``
    rows into the ``create_all``-made ``duel_map_pool``, then drop the source.
  * Rename ``users.bsk_user_*`` / ``bsk_skill_calculated_at`` → ``duel_*``
    (data-preserving — the HPS Ψ(Δ) module reads these).
  * Zero ``users.duel_wins`` / ``duel_losses`` once, on the conversion run only.
  * Rename ``season_snapshots.bsk_conservative`` / ``bsk_division`` → ``duel_*``.

The legacy per-feature BSK migrations were removed once this overhaul landed —
they would have recreated the dropped tables / re-added the renamed columns on
every restart.  Their schema-evolution history lives in git.

Because this is the one destructive migration (it drops the three BSK duel
tables), it snapshots the SQLite file to a timestamped ``.bak-pre-duel-*`` copy
*before* touching anything — but only on the one-time conversion run (a legacy
BSK schema is present).  Already-migrated and fresh databases are left alone.
"""

import os
import shutil
from datetime import datetime

from sqlalchemy import text

from utils.logger import get_logger

logger = get_logger("db.migration.duel_overhaul")


async def _table_exists(conn, name: str) -> bool:
    row = (await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :n"),
        {"n": name},
    )).fetchone()
    return row is not None


async def _columns(conn, table: str) -> list[str]:
    return [r[1] for r in (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()]


async def _legacy_bsk_present(engine) -> bool:
    """True if any BSK artifact remains → this is the one-time conversion run."""
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name LIKE 'bsk\\_%' ESCAPE '\\'"
        ))).first()
        if row:
            return True
        return "bsk_user_aim" in await _columns(conn, "users")


def _backup_sqlite(engine) -> None:
    """Copy the SQLite file to a timestamped backup before destructive changes.

    No-op for non-file engines (e.g. in-memory test DBs) or a missing file.
    """
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return
    dst = f"{db_path}.bak-pre-duel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(db_path, dst)
        logger.warning(f"duel overhaul: legacy BSK schema detected — backed up DB to {dst}")
    except Exception as e:
        logger.error(f"duel overhaul: DB backup to {dst} failed: {e}", exc_info=True)


async def run_duel_overhaul_migration(engine) -> None:
    # Snapshot the DB once, before the destructive conversion touches anything.
    if await _legacy_bsk_present(engine):
        _backup_sqlite(engine)

    async with engine.begin() as conn:
        # 1. Drop reset tables + removed duel-winner ML table.
        for t in ("bsk_duel_rounds", "bsk_duels", "bsk_ratings", "bsk_ml_runs"):
            await conn.execute(text(f"DROP TABLE IF EXISTS {t}"))

        # 2. Classifier pool: bsk_map_pool -> duel_map_pool (data-preserving).
        if await _table_exists(conn, "bsk_map_pool"):
            if await _table_exists(conn, "duel_map_pool"):
                src = await _columns(conn, "bsk_map_pool")
                dst = set(await _columns(conn, "duel_map_pool"))
                shared = [c for c in src if c in dst]
                target_empty = (await conn.execute(
                    text("SELECT COUNT(*) FROM duel_map_pool")
                )).scalar() == 0
                if shared and target_empty:
                    collist = ", ".join(shared)
                    await conn.execute(text(
                        f"INSERT INTO duel_map_pool ({collist}) "
                        f"SELECT {collist} FROM bsk_map_pool"
                    ))
                await conn.execute(text("DROP TABLE bsk_map_pool"))
            else:
                await conn.execute(text("ALTER TABLE bsk_map_pool RENAME TO duel_map_pool"))

        # 3. users column renames (data-preserving).  bsk_user_aim presence is
        #    the marker for "this is the one-time conversion run".
        ucols = await _columns(conn, "users")
        is_conversion = "bsk_user_aim" in ucols
        for old, new in (
            ("bsk_user_aim", "duel_user_aim"),
            ("bsk_user_speed", "duel_user_speed"),
            ("bsk_user_acc", "duel_user_acc"),
            ("bsk_user_cons", "duel_user_cons"),
            ("bsk_skill_calculated_at", "duel_skill_calculated_at"),
        ):
            if old in ucols and new not in ucols:
                await conn.execute(text(f"ALTER TABLE users RENAME COLUMN {old} TO {new}"))

        # 4. Zero the (legacy, now duel-named) W/L counters once, on conversion.
        if is_conversion and "duel_wins" in ucols:
            await conn.execute(text("UPDATE users SET duel_wins = 0, duel_losses = 0"))

        # 5. season_snapshots column renames (data-preserving).
        if await _table_exists(conn, "season_snapshots"):
            scols = await _columns(conn, "season_snapshots")
            for old, new in (
                ("bsk_conservative", "duel_conservative"),
                ("bsk_division", "duel_division"),
            ):
                if old in scols and new not in scols:
                    await conn.execute(text(
                        f"ALTER TABLE season_snapshots RENAME COLUMN {old} TO {new}"
                    ))
