"""Migration: add n300/n100/n50/ur_est to submissions and bsk_duel_rounds.

Persists the raw hit counts from osu! `score.statistics` so we can:
  - compute UR_est (Manifest Part I) at submission time for HPS payout (Ω),
  - feed the same UR signal into the BSK ML pipeline,
  - backfill historical rounds/submissions by re-fetching osu! scores.

All columns are nullable: existing rows stay valid until the backfill script
runs, and rows where the source data is missing (very old API payloads, manual
admin entries) remain explicitly NULL rather than zeroed-out.
"""

from sqlalchemy import text


async def run_ur_hit_counts_migration(engine) -> None:
    async with engine.begin() as conn:
        # ── submissions: per-submission hit counts + UR ────────────────────
        sub_cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(submissions)"))).fetchall()]
        sub_new = [
            ("n_300", "INTEGER"),
            ("n_100", "INTEGER"),
            ("n_50",  "INTEGER"),
            ("ur_est", "FLOAT"),
        ]
        for col, typedef in sub_new:
            if col not in sub_cols:
                await conn.execute(text(f"ALTER TABLE submissions ADD COLUMN {col} {typedef}"))

        # ── bsk_duel_rounds: per-player hit counts + UR (both sides) ───────
        round_cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duel_rounds)"))).fetchall()]
        round_new = [
            ("player1_n300", "INTEGER"),
            ("player1_n100", "INTEGER"),
            ("player1_n50",  "INTEGER"),
            ("player1_ur_est", "FLOAT"),
            ("player2_n300", "INTEGER"),
            ("player2_n100", "INTEGER"),
            ("player2_n50",  "INTEGER"),
            ("player2_ur_est", "FLOAT"),
        ]
        for col, typedef in round_new:
            if col not in round_cols:
                await conn.execute(text(f"ALTER TABLE bsk_duel_rounds ADD COLUMN {col} {typedef}"))
