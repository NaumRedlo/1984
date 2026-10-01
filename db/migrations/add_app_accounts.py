from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists
from db.models.oauth_token import OAuthToken

async def _relaxed_oauth(conn) -> None:
    table = OAuthToken.__tablename__
    if not await table_exists(conn, table):
        await conn.run_sync(lambda sync: OAuthToken.__table__.create(sync, checkfirst=True))
        return
    have = await existing_columns(conn, table)
    if "player_id" in have:
        return
    old = f"{table}_before_players"
    await conn.execute(text(f"ALTER TABLE {table} RENAME TO {old}"))
    for index in (await conn.execute(text(f"PRAGMA index_list('{old}')"))).all():
        name = index[1]
        if not name.startswith("sqlite_autoindex_"):
            quoted = '"' + name.replace('"', '""') + '"'
            await conn.execute(text(f"DROP INDEX {quoted}"))
    await conn.run_sync(lambda sync: OAuthToken.__table__.create(sync, checkfirst=True))
    kept = [name for name in (row[1] for row in (await conn.execute(text(f"PRAGMA table_info('{old}')"))).all()) if name in await existing_columns(conn, table)]
    names = ", ".join(f'"{name}"' for name in kept)
    await conn.execute(text(f"INSERT INTO {table} ({names}) SELECT {names} FROM {old}"))
    await conn.execute(text(f"DROP TABLE {old}"))

async def run_app_accounts_migration(engine) -> None:
    async with engine.begin() as conn:
        if await table_exists(conn, "render_worker_tokens") and "player_id" not in await existing_columns(conn, "render_worker_tokens"):
            await conn.execute(text("ALTER TABLE render_worker_tokens ADD COLUMN player_id INTEGER REFERENCES players(id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_render_worker_tokens_player_id ON render_worker_tokens (player_id)"))
        if await table_exists(conn, "players"):
            have = await existing_columns(conn, "players")
            for name in ("last_api_update", "last_full_update"):
                if name not in have:
                    await conn.execute(text(f"ALTER TABLE players ADD COLUMN {name} DATETIME"))
        if engine.dialect.name == "sqlite":
            await _relaxed_oauth(conn)
