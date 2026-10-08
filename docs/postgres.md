# PostgreSQL

The bot keeps its data in one database chosen by `DATABASE_URL`. It ran on a
SQLite file (`botdata.db`) and can now run on PostgreSQL; the reason for the
move is that several writers — the bot, the app's HTTP side, the render farm —
queue on one SQLite file (`database is locked` in `logs/bot.log`), and a server
database also gives backups and access from another machine.

```
DATABASE_URL=postgresql+asyncpg://bot:PASSWORD@127.0.0.1:5432/botdata
```

Leaving `DATABASE_URL` unset keeps SQLite, which is what the tests and a
development machine use by default.

## What differs, and where it is handled

SQLite accepts almost anything; PostgreSQL checks. Each difference is settled
in one place so the rest of the code does not know which database it talks to.

- **Time zones.** Every date column is a timestamp without a zone. SQLite
  dropped the zone of an aware datetime and kept the wall clock; `asyncpg`
  refuses an aware datetime outright. `attach_postgres` in `db/database.py`
  drops the zone the same way before a statement is sent, so what is stored
  and read back is exactly what it was on SQLite.
- **Foreign keys are enforced.** SQLite ignored them. A row may not go while
  another row still points at it: the admin purge now takes the membership and
  the leaderboard snapshots of a row with it, as `membership.leave` already
  did.
- **Empty values sort the other way.** Descending puts NULL last on SQLite and
  first on PostgreSQL. A query that sorts by a column that can be empty says
  so (`nulls_first()` / `nulls_last()`); the others filter NULL out or sort by
  a column that is never empty.
- **The schema comes from the models.** The chain in `db/migrations` is the
  history of the SQLite file and is run on SQLite only. PostgreSQL starts from
  `Base.metadata.create_all`, and the data arrives already in today's shape.

## Changing the schema from now on

`create_all` makes a table that is missing; it does not add a column to a
table that exists. A new column needs a step that works on both databases:
ask `existing_columns` and add it with `add_column` from
`db/migrations/_utils.py`, which writes the type the database understands, and
call the step from `run_all_migrations` outside `_sqlite_history`. `PRAGMA`,
`sqlite_master` and type names such as `DATETIME` belong to the history only.

## Tests

```
python -m pytest -q
TEST_DATABASE_URL=postgresql+asyncpg://bot@127.0.0.1:5432/bottest python -m pytest -q
```

With `TEST_DATABASE_URL` set, every database a test would open in memory is a
schema of its own in that PostgreSQL database, removed when the run ends. The
tests about the SQLite file itself are marked `sqlite_only` and skipped there.
Both runs are part of CI.

## Moving the data

`scripts/sqlite_to_postgres.py SOURCE TARGET`, run from the repository root with
`PYTHONPATH=.`, copies every table the models know, parents before children, in one transaction. It refuses a SQLite file
that is behind the code and a target that already holds rows. Rows that point
at something no longer there are left out and counted; the tables the code no
longer knows (duels, bounties, seasons and the rest) stay in the SQLite file.
After writing a table it reads it back and compares every row with what it
read from SQLite, then moves the id counters past the highest id. Without
`--apply` all of that is taken back: a rehearsal.

On the snapshot of 6 October 2026 the rehearsal wrote and compared 41 893 rows
in about two seconds and left out 61 leaderboard snapshots of rows that an
admin purge had removed.

## The switch

1. Install PostgreSQL on the server, create a role and an empty database, and
   keep the password out of the repository.
2. Deploy this code and let the bot start once on SQLite as usual, so the file
   is in today's shape. Stop the bot.
3. Copy `botdata.db` aside. It is the way back and is not touched again.
4. Rehearse:
   `PYTHONPATH=. venv/bin/python scripts/sqlite_to_postgres.py botdata.db "$TARGET"`.
5. Keep it: the same line with `--apply`.
6. Put `DATABASE_URL` in `.env` and start the bot.

To go back, stop the bot, remove `DATABASE_URL` from `.env` and start it: the
SQLite file is as it was at step 3. What was written to PostgreSQL in between
does not come back with it.
