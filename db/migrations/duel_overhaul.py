"""Migration: BSK → DUEL overhaul.

Converts a legacy BSK database to the new single-track-TrueSkill duel schema.
Idempotent and safe on fresh installs (nothing to convert), existing BSK
databases, and databases where an *earlier* revision of the new duel models was
already materialised by ``create_all`` (which never alters an existing table —
so a stale ``duels`` left over from mid-development is reconciled here).

Runs *after* ``Base.metadata.create_all``.

Actions:
  * DROP the three duel tables' BSK predecessors and the removed duel-winner ML
    table — reset to zero by design (``bsk_duels``, ``bsk_duel_rounds``,
    ``bsk_ratings``, ``bsk_ml_runs``).
  * Move the map-type classifier pool (data-preserving): copy ``bsk_map_pool``
    rows into ``duel_map_pool``, then drop the source.
  * Rename ``users.bsk_user_*`` / ``bsk_skill_calculated_at`` → ``duel_*``
    (data-preserving — the HPS Ψ(Δ) module reads these).
  * Zero ``users.duel_wins`` / ``duel_losses`` once, on the conversion run only.
  * Rename ``season_snapshots.bsk_conservative`` / ``bsk_division`` → ``duel_*``.
  * Reconcile the new duel tables to the current model: if ``duels`` /
    ``duel_rounds`` / ``duel_ratings`` carry a stale schema (missing a model
    column), drop and recreate them empty (they reset to zero anyway); add any
    missing — nullable — columns to ``duel_map_pool`` (data-preserving).

The legacy per-feature BSK migrations were removed once this overhaul landed —
they would have recreated the dropped tables / re-added the renamed columns on
every restart.  Their schema-evolution history lives in git.

Because this migration is destructive (drops the BSK tables / rebuilds stale
duel tables), it snapshots the SQLite file to a timestamped ``.bak-pre-duel-*``
copy *before* touching anything — but only when there is actually something to
convert (legacy BSK schema present, or a stale duel-table schema detected).
Already-migrated and fresh databases are left untouched.
"""

import os
import shutil
from datetime import datetime

import sqlalchemy as sa
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


async def _reset_tables_stale(engine) -> bool:
    """True if any of the reset tables exists but is missing a current-model
    column — i.e. it was created by an earlier model revision and create_all
    (which only creates *missing* tables) never updated it."""
    from db.models.duel import Duel
    from db.models.duel_round import DuelRound
    from db.models.duel_rating import DuelRating

    async with engine.connect() as conn:
        for model in (Duel, DuelRound, DuelRating):
            existing = set(await _columns(conn, model.__tablename__))
            if not existing:
                continue  # absent → create_all already made it correctly
            expected = {c.name for c in model.__table__.columns}
            if not expected.issubset(existing):
                return True
    return False


def _sqlite_type(col) -> str:
    t = col.type
    if isinstance(t, sa.Boolean):
        return "BOOLEAN"
    if isinstance(t, sa.Float):
        return "FLOAT"
    if isinstance(t, sa.Integer):
        return "INTEGER"
    if isinstance(t, sa.DateTime):
        return "DATETIME"
    if isinstance(t, sa.String):
        return "VARCHAR"
    return "TEXT"


def _backup_sqlite(engine, reason: str) -> None:
    """Copy the SQLite file to a timestamped backup before destructive changes.

    No-op for non-file engines (e.g. in-memory test DBs) or a missing file.
    """
    db_path = engine.url.database
    if not db_path or db_path == ":memory:" or not os.path.exists(db_path):
        return
    dst = f"{db_path}.bak-pre-duel-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.copy2(db_path, dst)
        logger.warning(f"duel overhaul: {reason} — backed up DB to {dst}")
    except Exception as e:
        logger.error(f"duel overhaul: DB backup to {dst} failed: {e}", exc_info=True)


async def run_duel_overhaul_migration(engine) -> None:
    legacy = await _legacy_bsk_present(engine)
    stale = await _reset_tables_stale(engine)

    # Snapshot the DB once, before any destructive change, when there's
    # actually something to convert or repair.
    if legacy or stale:
        reason = "legacy BSK schema detected" if legacy else "stale duel-table schema detected"
        _backup_sqlite(engine, reason)

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

    # 6. Reconcile the new duel tables to the current model.  Handles tables
    #    left from an earlier model revision (create_all never alters them).
    await _reconcile_duel_schema(engine, rebuild_reset=stale)


async def _reconcile_duel_schema(engine, rebuild_reset: bool) -> None:
    from db.database import Base
    from db.models.duel_map_pool import DuelMapPool

    # 6a. Reset tables (duels/duel_rounds/duel_ratings) reset to zero by design:
    #     on a stale schema, drop (child-first for the FK) and recreate empty.
    if rebuild_reset:
        logger.warning("duel overhaul: stale duel-table schema — rebuilding duels/duel_rounds/duel_ratings empty")
        async with engine.begin() as conn:
            for name in ("duel_rounds", "duels", "duel_ratings"):
                await conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # 6b. duel_map_pool is data-preserving: add any missing (nullable) columns
    #     so a stale classifier pool doesn't crash on the new feature columns.
    async with engine.begin() as conn:
        existing = set(await _columns(conn, "duel_map_pool"))
        if existing:
            for c in DuelMapPool.__table__.columns:
                if c.name not in existing:
                    await conn.execute(text(
                        f"ALTER TABLE duel_map_pool ADD COLUMN {c.name} {_sqlite_type(c)}"
                    ))
                    logger.warning(f"duel overhaul: added missing duel_map_pool column '{c.name}'")
