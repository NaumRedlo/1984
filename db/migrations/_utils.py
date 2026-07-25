"""Shared guards for schema migrations.

Kept in its own module (not ``db/migrations/__init__.py``) because that package
imports every migration — importing back from it would be circular.
"""

from sqlalchemy import text


async def table_exists(conn, table: str) -> bool:
    """True if `table` is present in this SQLite database.

    ``PRAGMA table_info(<missing table>)`` returns an EMPTY result set rather
    than raising, so a "column not in table_info" check silently passes for a
    table that doesn't exist at all — and the ALTER that follows then dies with
    "no such table". Migrations touching tables from removed features must gate
    on this first.
    """
    result = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name = :name"),
        {"name": table},
    )
    return result.first() is not None


async def existing_columns(conn, table: str) -> set[str]:
    """Column names of `table`, or an empty set if the table is absent."""
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}
