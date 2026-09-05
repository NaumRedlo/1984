from sqlalchemy import text

async def table_exists(conn, table: str) -> bool:
    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name = :name"),
        {"name": table},
    )
    return result.first() is not None

async def existing_columns(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}
