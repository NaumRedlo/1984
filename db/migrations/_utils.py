from sqlalchemy import inspect, text

async def table_exists(conn, table: str) -> bool:
    return await conn.run_sync(lambda sync: inspect(sync).has_table(table))

async def existing_columns(conn, table: str) -> set[str]:
    def read(sync) -> set[str]:
        seen = inspect(sync)
        if not seen.has_table(table):
            return set()
        return {column["name"] for column in seen.get_columns(table)}

    return await conn.run_sync(read)

async def add_column(conn, column) -> bool:
    table = column.table.name
    if column.name in await existing_columns(conn, table):
        return False
    kind = column.type.compile(dialect=conn.dialect)
    await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column.name}" {kind}'))
    return True
