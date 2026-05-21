"""Migration: add per-axis BSK skill cache columns to users.

Stores the four skill values (aim/speed/acc/cons, range [0..10]) used by the
HPS Ψ(Δ) module.  Values are computed by services/hps/bsk_user_skill.py from
a weighted average of the user's top-10 approved (win/condition) submissions
in the last 90 days, with a PP-derived bootstrap for new accounts.

Default 4.0 is a neutral mid-of-scale value — the bootstrap calculator will
overwrite it on the user's first refresh.
"""

from sqlalchemy import text


async def run_user_bsk_skill_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()]
        new_cols = [
            ("bsk_user_aim",            "FLOAT DEFAULT 4.0 NOT NULL"),
            ("bsk_user_speed",          "FLOAT DEFAULT 4.0 NOT NULL"),
            ("bsk_user_acc",            "FLOAT DEFAULT 4.0 NOT NULL"),
            ("bsk_user_cons",           "FLOAT DEFAULT 4.0 NOT NULL"),
            ("bsk_skill_calculated_at", "DATETIME"),
        ]
        for col, typedef in new_cols:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typedef}"))
