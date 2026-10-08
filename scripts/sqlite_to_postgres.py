from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import pkgutil
import sqlite3
import sys
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, LargeBinary, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base, attach_postgres

BATCH = 1000

def tables():
    for found in pkgutil.iter_modules(db.models.__path__):
        importlib.import_module(f"db.models.{found.name}")
    return Base.metadata.sorted_tables

def read(column, value):
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, DateTime):
        return datetime.fromisoformat(str(value)).replace(tzinfo=None)
    if isinstance(kind, Date):
        return date.fromisoformat(str(value))
    if isinstance(kind, Boolean):
        return bool(value)
    if isinstance(kind, JSON):
        return json.loads(value)
    if isinstance(kind, LargeBinary):
        return bytes(value)
    return value

def missing(source, wanted) -> list[str]:
    out = []
    for table in wanted:
        have = {row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')}
        if not have:
            out.append(table.name)
            continue
        out.extend(f"{table.name}.{column.name}" for column in table.columns if column.name not in have)
    return out

def left_behind(source, wanted) -> list[tuple[str, int]]:
    known = {table.name for table in wanted}
    names = [row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return [(name, source.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in names if name not in known]

def rows_of(source, table, kept) -> tuple[list[dict], int]:
    names = [column.name for column in table.columns]
    listed = ", ".join(f'"{name}"' for name in names)
    keys = ", ".join(f'"{column.name}"' for column in table.primary_key.columns)
    links = [(key.parent.name, (key.column.table.name, key.column.name)) for key in table.foreign_keys]
    out, orphans = [], 0
    for raw in source.execute(f'SELECT {listed} FROM "{table.name}" ORDER BY {keys}'):
        row = {column.name: read(column, value) for column, value in zip(table.columns, raw)}
        if any(row[name] is not None and row[name] not in kept[parent] for name, parent in links):
            orphans += 1
            continue
        out.append(row)
    return out, orphans

async def copy(source_path: str, target_url: str, apply: bool) -> int:
    wanted = tables()
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    absent = missing(source, wanted)
    if absent:
        print("The SQLite file is behind the code; start the current bot on it once, then try again. Missing:")
        for name in absent:
            print(f"  {name}")
        return 2
    referenced = {(key.column.table.name, key.column.name) for table in wanted for key in table.foreign_keys}
    kept: dict[tuple[str, str], set] = {pair: set() for pair in referenced}
    engine = create_async_engine(target_url, poolclass=NullPool)
    if engine.dialect.name != "postgresql":
        print("The target has to be a PostgreSQL address")
        return 2
    attach_postgres(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    report = []
    async with engine.connect() as conn:
        work = await conn.begin()
        for table in wanted:
            if (await conn.execute(select(func.count()).select_from(table))).scalar():
                print(f"The target already holds rows in {table.name}; it has to be empty")
                await work.rollback()
                await engine.dispose()
                return 2
        for table in wanted:
            rows, orphans = rows_of(source, table, kept)
            for start in range(0, len(rows), BATCH):
                await conn.execute(table.insert(), rows[start:start + BATCH])
            for parent, column in referenced:
                if parent == table.name:
                    kept[(parent, column)].update(row[column] for row in rows)
            landed = (await conn.execute(select(table).order_by(*table.primary_key.columns))).mappings().all()
            same = len(landed) == len(rows) and all(dict(there) == here for there, here in zip(landed, rows))
            report.append((table.name, len(rows), orphans, same))
            if not same:
                print(f"{table.name} did not arrive as it was read; nothing is kept")
                await work.rollback()
                await engine.dispose()
                return 1
        for table in wanted:
            keys = list(table.primary_key.columns)
            if len(keys) != 1 or not isinstance(keys[0].type, Integer):
                continue
            sequence = (await conn.execute(text("SELECT pg_get_serial_sequence(:name, :key)"), {"name": f'"{table.name}"', "key": keys[0].name})).scalar()
            top = (await conn.execute(select(func.max(keys[0])))).scalar()
            if sequence and top is not None:
                await conn.execute(text("SELECT setval(CAST(:sequence AS regclass), :top, true)"), {"sequence": sequence, "top": top})
        if apply:
            await work.commit()
        else:
            await work.rollback()
    await engine.dispose()
    print(f"{'table':28}{'rows':>8}{'orphans':>9}")
    for name, count, orphans, _same in report:
        print(f"{name:28}{count:>8}{orphans:>9}")
    print(f"{'total':28}{sum(row[1] for row in report):>8}{sum(row[2] for row in report):>9}")
    behind = left_behind(source, wanted)
    if behind:
        print("Tables the code no longer knows stay in the SQLite file: " + ", ".join(f"{name} ({count})" for name, count in behind))
    print("Copied and kept." if apply else "Rehearsal only: every row was written, compared and taken back. Add --apply to keep it.")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copy the bot's SQLite file into an empty PostgreSQL database, row for row, and only keep it when asked")
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(copy(args.source, args.target, args.apply)))
