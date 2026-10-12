import asyncio
import sys

from sqlalchemy import func, inspect, select, text

from db.database import Base, engine
import db.models  # noqa: F401


def surveyed(sync) -> tuple[list[str], dict[str, set[str]]]:
    seen = inspect(sync)
    names = [name for name in seen.get_table_names() if not name.startswith("sqlite_")]
    return names, {name: {column["name"] for column in seen.get_columns(name)} for name in names}


async def audit() -> int:
    declared = {table.name: table for table in Base.metadata.sorted_tables}
    async with engine.connect() as conn:
        names, columns = await conn.run_sync(surveyed)
        postgres = conn.dialect.name == "postgresql"
        print(f"database: {conn.dialect.name}, {len(names)} tables, {len(declared)} declared by the code")
        print()
        print("tables the code no longer declares:")
        strangers = sorted(name for name in names if name not in declared)
        for name in strangers:
            rows = (await conn.execute(text(f'SELECT COUNT(*) FROM "{name}"'))).scalar()
            print(f"  {name}: {rows} rows")
        if not strangers:
            print("  none")
        print()
        print("columns the code no longer declares:")
        loose = [(name, sorted(columns[name] - {column.name for column in declared[name].columns})) for name in sorted(names) if name in declared]
        loose = [(name, extra) for name, extra in loose if extra]
        for name, extra in loose:
            print(f"  {name}: {', '.join(extra)}")
        if not loose:
            print("  none")
        print()
        print("rows and size of every declared table:")
        for name in sorted(declared):
            if name not in columns:
                print(f"  {name}: missing in the database")
                continue
            rows = (await conn.execute(select(func.count()).select_from(declared[name]))).scalar()
            size = ""
            if postgres:
                size = (await conn.execute(text("SELECT pg_size_pretty(pg_total_relation_size(CAST(:name AS regclass)))"), {"name": name})).scalar()
            print(f"  {name}: {rows} rows {size}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(audit()))
