from sqlalchemy import text

from db.models.best_score import UserBestScore


async def run_scope_best_score_ids_migration(engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as conn:
        indexes = (await conn.execute(text("PRAGMA index_list('user_best_scores')"))).all()
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

        await conn.execute(text("ALTER TABLE user_best_scores RENAME TO user_best_scores_old"))
        old_indexes = (await conn.execute(text("PRAGMA index_list('user_best_scores_old')"))).all()
        for index in old_indexes:
            name = index[1]
            if not name.startswith("sqlite_autoindex_"):
                quoted = '"' + name.replace('"', '""') + '"'
                await conn.execute(text(f"DROP INDEX {quoted}"))

        await conn.run_sync(lambda sync_conn: UserBestScore.__table__.create(sync_conn, checkfirst=True))
        old_columns = [row[1] for row in (await conn.execute(text("PRAGMA table_info('user_best_scores_old')"))).all()]
        new_columns = {row[1] for row in (await conn.execute(text("PRAGMA table_info('user_best_scores')"))).all()}
        shared = [name for name in old_columns if name in new_columns]
        names = ", ".join(f'"{name}"' for name in shared)
        await conn.execute(text(f"INSERT INTO user_best_scores ({names}) SELECT {names} FROM user_best_scores_old"))
        await conn.execute(text("DROP TABLE user_best_scores_old"))
