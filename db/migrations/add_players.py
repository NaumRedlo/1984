import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text

from db import player_sync
from db.models.chat_member import ChatMember
from db.models.player import SHARED, Player

logger = logging.getLogger(__name__)

REPORT = os.path.join("logs", "players_migration.log")
DONE = "players_migrated_at"

@dataclass(frozen=True)
class Row:
    id: int
    chat_id: int
    telegram_id: int
    osu_user_id: Optional[int]
    fresh: str = ""
    values: dict = field(default_factory=dict)

@dataclass
class Plan:
    players: dict = field(default_factory=dict)
    members: list = field(default_factory=list)
    detached: list = field(default_factory=list)

def plan(rows: list[Row], oauth: set[int]) -> Plan:
    registered = sorted((row for row in rows if row.osu_user_id), key=lambda row: row.id)
    main: dict[int, int] = {}
    latest: dict[int, int] = {}
    for row in registered:
        main[row.telegram_id] = row.osu_user_id
        latest[row.telegram_id] = row.id
    claims: dict[int, list[int]] = {}
    for telegram_id, osu_id in main.items():
        claims.setdefault(osu_id, []).append(telegram_id)
    owner = {
        osu_id: max(telegrams, key=lambda telegram_id: (telegram_id in oauth, latest[telegram_id]))
        for osu_id, telegrams in claims.items()
    }

    made = Plan()
    freshest: dict[int, Row] = {}
    for row in sorted(rows, key=lambda row: row.id):
        if not row.osu_user_id:
            made.detached.append((row.id, "no osu! account"))
            continue
        if main[row.telegram_id] != row.osu_user_id:
            made.detached.append((row.id, f"telegram {row.telegram_id} plays as osu! {main[row.telegram_id]}"))
            continue
        if owner[row.osu_user_id] != row.telegram_id:
            made.detached.append((row.id, f"osu! {row.osu_user_id} belongs to telegram {owner[row.osu_user_id]}"))
            continue
        made.members.append((row.id, row.chat_id, row.osu_user_id))
        known = freshest.get(row.osu_user_id)
        if known is None or (row.fresh, row.id) > (known.fresh, known.id):
            freshest[row.osu_user_id] = row
    made.players = {osu_id: (owner[osu_id], freshest[osu_id]) for osu_id in freshest}
    return made

async def _columns(conn, table: str) -> set[str]:
    return {row[1] for row in (await conn.execute(text(f"PRAGMA table_info({table})"))).all()}

async def _read(conn) -> tuple[list[Row], set[int]]:
    tables = {row[0] for row in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
    if "users" not in tables:
        return [], set()
    have = await _columns(conn, "users")
    wanted = [name for name in SHARED if name in have]
    fresh = "last_api_update" if "last_api_update" in have else "NULL"
    listed = ", ".join(["id", "chat_id", "telegram_id", "osu_user_id", fresh, *wanted])
    result = await conn.execute(text(f"SELECT {listed} FROM users"))
    rows = [
        Row(
            id=found[0],
            chat_id=found[1],
            telegram_id=found[2],
            osu_user_id=found[3],
            fresh=str(found[4] or ""),
            values=dict(zip(wanted, found[5:])),
        )
        for found in result.all()
    ]
    oauth: set[int] = set()
    if "oauth_tokens" in tables:
        oauth = {row[0] for row in (await conn.execute(text("SELECT telegram_id FROM oauth_tokens"))).all()}
    return rows, oauth

def _report(made: Plan, rows: list[Row]) -> dict:
    return {
        "users": len(rows),
        "players": len(made.players),
        "members": len(made.members),
        "detached": [{"user_id": user_id, "why": why} for user_id, why in made.detached],
    }

async def run_players_migration(engine) -> Optional[dict]:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Player.__table__.create(sync, checkfirst=True))
        if "player_id" not in await _columns(conn, "users"):
            await conn.execute(text("ALTER TABLE users ADD COLUMN player_id INTEGER REFERENCES players(id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_player_id ON users (player_id)"))
            logger.info("Migration: added column users.player_id")
        await conn.run_sync(lambda sync: ChatMember.__table__.create(sync, checkfirst=True))

        await conn.execute(text("CREATE TABLE IF NOT EXISTS bot_settings (key VARCHAR PRIMARY KEY, value VARCHAR)"))
        if (await conn.execute(text("SELECT 1 FROM bot_settings WHERE key = :key"), {"key": DONE})).first():
            player_sync.switch_on()
            return None
        rows, oauth = await _read(conn)
        linked = {row[0] for row in (await conn.execute(text("SELECT id FROM users WHERE player_id IS NOT NULL"))).all()}
        made = plan(rows, oauth)

        existing = {osu_id: (player_id, telegram_id) for player_id, osu_id, telegram_id in (await conn.execute(text("SELECT id, osu_user_id, telegram_id FROM players"))).all()}
        ids: dict[int, int] = {}
        for osu_id, (telegram_id, source) in made.players.items():
            if osu_id in existing:
                player_id, owner = existing[osu_id]
                if owner not in (None, telegram_id):
                    continue
                ids[osu_id] = player_id
                continue
            values = {name: source.values.get(name) for name in SHARED if name in source.values}
            values["osu_username"] = values.get("osu_username") or ""
            names = ["osu_user_id", "telegram_id", *values]
            placed = ", ".join(f":{name}" for name in names)
            result = await conn.execute(
                text(f"INSERT INTO players ({', '.join(names)}, created_at, updated_at) VALUES ({placed}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"osu_user_id": osu_id, "telegram_id": telegram_id, **values},
            )
            ids[osu_id] = result.lastrowid

        joined = 0
        for user_id, chat_id, osu_id in made.members:
            player_id = ids.get(osu_id)
            if player_id is None or user_id in linked:
                continue
            await conn.execute(text("UPDATE users SET player_id = :player WHERE id = :user"), {"player": player_id, "user": user_id})
            await conn.execute(
                text("INSERT OR IGNORE INTO chat_members (chat_id, player_id, user_id, joined_at) VALUES (:chat, :player, :user, CURRENT_TIMESTAMP)"),
                {"chat": chat_id, "player": player_id, "user": user_id},
            )
            joined += 1
        await conn.execute(text("INSERT INTO bot_settings (key, value) VALUES (:key, CURRENT_TIMESTAMP)"), {"key": DONE})
    player_sync.switch_on()

    said = _report(made, rows)
    logger.info("Migration: %d players for %d chat rows, %d rows left as they were", len(ids), joined, len(made.detached))
    for user_id, why in made.detached[:20]:
        logger.warning("Migration: users.id %s is not joined to a player: %s", user_id, why)
    try:
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as out:
            json.dump(said, out, ensure_ascii=False, indent=1)
    except OSError as exc:
        logger.warning("Migration: the players report was not written: %s", exc)
    return said

async def dry_run(url: str) -> dict:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            rows, oauth = await _read(conn)
    finally:
        await engine.dispose()
    return _report(plan(rows, oauth), rows)

if __name__ == "__main__":
    if "--dry-run" not in sys.argv:
        sys.exit("usage: python -m db.migrations.add_players --dry-run [database url]")
    from config.settings import DATABASE_URL

    given = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    print(json.dumps(asyncio.run(dry_run(given[0] if given else DATABASE_URL)), ensure_ascii=False, indent=1))
