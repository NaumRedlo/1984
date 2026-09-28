from sqlalchemy import text

from db.models.best_score import UserBestScore
from db.models.map_attempt import UserMapAttempt


async def _scope_score_ids(conn, model) -> None:
    table = model.__tablename__
    indexes = (await conn.execute(text(f"PRAGMA index_list('{table}')"))).all()
    global_unique = False
    for index in indexes:
        if not index[2]:
            continue
        name = index[1].replace('"', '""')
        columns = (await conn.execute(text(f'PRAGMA index_info("{name}")'))).all()
        if [column[2] for column in columns] == ["score_id"]:
            global_unique = True
            break
    if not global_unique:
        return

    old = f"{table}_old"
    await conn.execute(text(f"ALTER TABLE {table} RENAME TO {old}"))
    old_indexes = (await conn.execute(text(f"PRAGMA index_list('{old}')"))).all()
    for index in old_indexes:
        name = index[1]
        if not name.startswith("sqlite_autoindex_"):
            quoted = '"' + name.replace('"', '""') + '"'
            await conn.execute(text(f"DROP INDEX {quoted}"))

    await conn.run_sync(lambda sync_conn: model.__table__.create(sync_conn, checkfirst=True))
    old_columns = [row[1] for row in (await conn.execute(text(f"PRAGMA table_info('{old}')"))).all()]
    new_columns = {row[1] for row in (await conn.execute(text(f"PRAGMA table_info('{table}')"))).all()}
    shared = [name for name in old_columns if name in new_columns]
    names = ", ".join(f'"{name}"' for name in shared)
    await conn.execute(text(f"INSERT INTO {table} ({names}) SELECT {names} FROM {old}"))
    await conn.execute(text(f"DROP TABLE {old}"))


async def run_scope_best_score_ids_migration(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        for model in (UserBestScore, UserMapAttempt):
            await _scope_score_ids(conn, model)
