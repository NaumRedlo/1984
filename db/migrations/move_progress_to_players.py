import asyncio
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
from typing import Optional

from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists
from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt
from db.models.player import PROGRESS, Player
from db.models.title_progress import UserTitleProgress

logger = logging.getLogger(__name__)

REPORT = os.path.join("logs", "players_progress_migration.log")
DONE = "players_progress_moved_at"

FRESHEST = ("level", "join_date", "grade_count_s", "grade_count_ss")
MOST = ("profile_opens_best", "active_streak_best", "week_plays_best")
SUMMED = ("compare_uses",)
ANY = ("comeback_done",)
EARLIEST = ("best_scores_baseline_at",)
LATEST = ("last_seen_at",)
FOLLOWING = {
    "active_day": ("active_streak",),
    "profile_opens_date": ("profile_opens_count",),
    "playcount_week_anchor_at": ("playcount_week_anchor",),
    "app_profile_at": ("app_profile",),
}

SCORED = (UserBestScore, UserMapAttempt)

def merged(rows: list[dict]) -> dict:
    out: dict = {}
    fresh = max(rows, key=lambda row: (str(row.get("last_api_update") or ""), row["id"]))
    for name in FRESHEST:
        out[name] = fresh.get(name)
    for name in MOST:
        known = [row[name] for row in rows if row.get(name) is not None]
        out[name] = max(known) if known else None
    for name in SUMMED:
        known = [row[name] for row in rows if row.get(name) is not None]
        out[name] = sum(known) if known else None
    for name in ANY:
        known = [row[name] for row in rows if row.get(name) is not None]
        out[name] = (1 if any(known) else 0) if known else None
    for name in EARLIEST:
        known = [row[name] for row in rows if row.get(name)]
        out[name] = min(known) if known else None
    for name in LATEST:
        known = [row[name] for row in rows if row.get(name)]
        out[name] = max(known) if known else None
    for lead, followers in FOLLOWING.items():
        holding = [row for row in rows if row.get(lead) or any(row.get(name) is not None for name in followers)]
        source = max(holding, key=lambda row: (str(row.get(lead) or ""), row["id"])) if holding else None
        for name in (lead, *followers):
            out[name] = source.get(name) if source else None
    return out

async def _add_columns(conn) -> list[str]:
    have = await existing_columns(conn, "players")
    added = []
    for name in PROGRESS:
        if name in have:
            continue
        kind = Player.__table__.c[name].type.compile(dialect=conn.dialect)
        await conn.execute(text(f"ALTER TABLE players ADD COLUMN {name} {kind}"))
        added.append(name)
    return added

async def _share_progress(conn) -> int:
    have = await existing_columns(conn, "users")
    names = [name for name in PROGRESS if name in have]
    if not names or "player_id" not in have:
        return 0
    fresh = "last_api_update" if "last_api_update" in have else "NULL"
    listed = ", ".join(["id", "player_id", fresh, *names])
    found = (await conn.execute(text(f"SELECT {listed} FROM users WHERE player_id IS NOT NULL"))).all()
    grouped: dict[int, list[dict]] = {}
    for row in found:
        grouped.setdefault(row[1], []).append({"id": row[0], "last_api_update": row[2], **dict(zip(names, row[3:]))})
    placed = ", ".join(f"{name} = :{name}" for name in names)
    for player_id, rows in grouped.items():
        values = {name: value for name, value in merged(rows).items() if name in names}
        await conn.execute(text(f"UPDATE players SET {placed} WHERE id = :player"), {**values, "player": player_id})
        if len(rows) > 1:
            await conn.execute(text(f"UPDATE users SET {placed} WHERE player_id = :player"), {**values, "player": player_id})
    return len(grouped)

async def _drop_indexes(conn, table: str) -> None:
    for index in (await conn.execute(text(f"PRAGMA index_list('{table}')"))).all():
        name = index[1]
        if not name.startswith("sqlite_autoindex_"):
            quoted = '"' + name.replace('"', '""') + '"'
            await conn.execute(text(f"DROP INDEX {quoted}"))

async def _rebuilt(conn, model) -> Optional[str]:
    table = model.__tablename__
    if not await table_exists(conn, table):
        await conn.run_sync(lambda sync: model.__table__.create(sync, checkfirst=True))
        return None
    have = await existing_columns(conn, table)
    if "player_id" in have or "user_id" not in have:
        return None
    old = f"{table}_before_players"
    await conn.execute(text(f"ALTER TABLE {table} RENAME TO {old}"))
    await _drop_indexes(conn, old)
    await conn.run_sync(lambda sync: model.__table__.create(sync, checkfirst=True))
    return old

async def _counted(conn, table: str) -> int:
    return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar() or 0

async def _move_scored(conn, model) -> Optional[dict]:
    table = model.__tablename__
    old = await _rebuilt(conn, model)
    if old is None:
        return None
    before = await _counted(conn, old)
    old_columns = [row[1] for row in (await conn.execute(text(f"PRAGMA table_info('{old}')"))).all()]
    new_columns = await existing_columns(conn, table)
    kept = [name for name in old_columns if name in new_columns]
    found = (await conn.execute(text(
        f"SELECT o.id, u.player_id, o.score_id, COALESCE(u.last_api_update, '') FROM {old} o JOIN users u ON u.id = o.user_id WHERE u.player_id IS NOT NULL"
    ))).all()
    best: dict[tuple[int, int], tuple[str, int]] = {}
    for row_id, player_id, score_id, fresh in found:
        key = (player_id, score_id)
        mark = (str(fresh), row_id)
        if key not in best or mark > best[key]:
            best[key] = mark
    await conn.execute(text("CREATE TEMP TABLE IF NOT EXISTS players_kept_rows (id INTEGER PRIMARY KEY)"))
    await conn.execute(text("DELETE FROM players_kept_rows"))
    picked = [{"id": mark[1]} for mark in best.values()]
    for start in range(0, len(picked), 500):
        await conn.execute(text("INSERT INTO players_kept_rows (id) VALUES (:id)"), picked[start:start + 500])
    names = ", ".join(f'"{name}"' for name in kept)
    chosen = ", ".join(f'o."{name}"' for name in kept)
    await conn.execute(text(
        f"INSERT INTO {table} ({names}, player_id) SELECT {chosen}, u.player_id FROM {old} o "
        f"JOIN players_kept_rows k ON k.id = o.id JOIN users u ON u.id = o.user_id"
    ))
    await conn.execute(text("DROP TABLE players_kept_rows"))
    await conn.execute(text(f"DROP TABLE {old}"))
    after = await _counted(conn, table)
    return {"before": before, "after": after, "merged": len(found) - after, "left": before - len(found)}

async def _move_titles(conn) -> Optional[dict]:
    table = UserTitleProgress.__tablename__
    old = await _rebuilt(conn, UserTitleProgress)
    if old is None:
        return None
    before = await _counted(conn, old)
    joined = (await conn.execute(text(f"SELECT COUNT(*) FROM {old} o JOIN users u ON u.id = o.user_id WHERE u.player_id IS NOT NULL"))).scalar() or 0
    await conn.execute(text(
        f"INSERT INTO {table} (player_id, title_code, current_value, unlocked, unlocked_at) "
        f"SELECT u.player_id, o.title_code, MAX(o.current_value), MAX(o.unlocked), MIN(CASE WHEN o.unlocked THEN o.unlocked_at END) "
        f"FROM {old} o JOIN users u ON u.id = o.user_id WHERE u.player_id IS NOT NULL GROUP BY u.player_id, o.title_code"
    ))
    await conn.execute(text(f"DROP TABLE {old}"))
    after = await _counted(conn, table)
    return {"before": before, "after": after, "merged": joined - after, "left": before - joined}

async def _moved(conn) -> dict:
    said: dict = {}
    if not await table_exists(conn, "players") or not await table_exists(conn, "users"):
        for model in (*SCORED, UserTitleProgress):
            await conn.run_sync(lambda sync, model=model: model.__table__.create(sync, checkfirst=True))
        return said
    added = await _add_columns(conn)
    await conn.execute(text("CREATE TABLE IF NOT EXISTS bot_settings (key VARCHAR PRIMARY KEY, value VARCHAR)"))
    done = (await conn.execute(text("SELECT 1 FROM bot_settings WHERE key = :key"), {"key": DONE})).first()
    if not done:
        said["players"] = await _share_progress(conn)
        said["columns"] = added
    for model in SCORED:
        moved = await _move_scored(conn, model)
        if moved is not None:
            said[model.__tablename__] = moved
    titles = await _move_titles(conn)
    if titles is not None:
        said[UserTitleProgress.__tablename__] = titles
    if not done:
        await conn.execute(text("INSERT INTO bot_settings (key, value) VALUES (:key, CURRENT_TIMESTAMP)"), {"key": DONE})
    return said

async def run_player_progress_migration(engine) -> dict:
    async with engine.begin() as conn:
        said = await _moved(conn)
    if not said:
        return said
    for table, moved in said.items():
        if isinstance(moved, dict):
            logger.info("Migration: %s moved to players: %d rows became %d, %d merged, %d left without a player", table, moved["before"], moved["after"], moved["merged"], moved["left"])
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as out:
            json.dump(said, out, ensure_ascii=False, indent=1)
    except OSError as exc:
        logger.warning("Migration: the progress report was not written: %s", exc)
    return said

async def dry_run(path: str) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine

    from db import player_sync
    from db.migrations.add_players import run_players_migration

    folder = tempfile.mkdtemp(prefix="players-progress-")
    copy = os.path.join(folder, "copy.db")
    was_on = player_sync.is_on()
    try:
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        target = sqlite3.connect(copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
        try:
            await run_players_migration(engine)
            async with engine.begin() as conn:
                return await _moved(conn)
        finally:
            await engine.dispose()
    finally:
        player_sync.switch_on(was_on)
        shutil.rmtree(folder, ignore_errors=True)

if __name__ == "__main__":
    given = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    if "--dry-run" not in sys.argv or len(given) != 1:
        sys.exit("usage: python -m db.migrations.move_progress_to_players --dry-run <path to a database file>")
    print(json.dumps(asyncio.run(dry_run(given[0])), ensure_ascii=False, indent=1))
