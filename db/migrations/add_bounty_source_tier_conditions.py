"""Migration: add source/tier/week_id/conditions to bounties.

Splits the bounty space into two top-level categories used by the weekly
generator (Plan: unified-giggling-tiger):

  source     'auto' | 'manual'    — who created the bounty
  tier       'C' | 'B' | 'A' | 'Open' | NULL   — pool slot for auto-generated
  week_id    INTEGER NULL         — FK to weekly_bounty_pool.id
  conditions TEXT NULL            — JSON-serialised extra conditions
                                    (e.g. {"max_ur": 75}, {"min_combo_pct": 0.8})

Existing rows default to source='manual', tier=NULL, week_id=NULL — preserving
their behaviour.  The legacy columns (min_accuracy / required_mods / max_misses
/ min_rank / min_hp) are NOT touched here: the generator writes both forms so
that bounty_auto_checker keeps working without changes.
"""

from sqlalchemy import text


async def run_bounty_source_tier_conditions_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bounties)"))).fetchall()]
        new_cols = [
            ("source",     "TEXT NOT NULL DEFAULT 'manual'"),
            ("tier",       "TEXT"),
            ("week_id",    "INTEGER"),
            ("conditions", "TEXT"),
        ]
        for col, typedef in new_cols:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bounties ADD COLUMN {col} {typedef}"))
