"""Migration: add bp / weekly_tier / weekly_tier_set_at to users.

Plan: unified-giggling-tiger.

  bp                  INTEGER NOT NULL DEFAULT 0
    Bounty points — placeholder currency for snipe/event spending.  Not
    consumed in MVP, only the column is reserved.

  weekly_tier         TEXT NULL  ∈ {'C','B','A'}
    Snapshot of the player's tier (via get_tier_for_hp on their hps_points),
    frozen Monday 00:00 MSK by the weekly generator.  NULL = not yet assigned.
    Open is not stored here because Open is visible to everyone regardless.

  weekly_tier_set_at  DATETIME NULL
    When the snapshot above was taken.

Idempotent: re-running the migration on a schema that already has the columns
is a no-op.
"""

from sqlalchemy import text


async def run_user_bp_weekly_tier_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()]
        new_cols = [
            ("bp",                 "INTEGER NOT NULL DEFAULT 0"),
            ("weekly_tier",        "TEXT"),
            ("weekly_tier_set_at", "DATETIME"),
        ]
        for col, typedef in new_cols:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
