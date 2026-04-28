"""Migration: add absolute skill stars + new pattern features to bsk_map_pool.

Phase 2 of the BSK skill metric overhaul.  Replaces the old share-weight model
(w_*) with an independent per-skill stars model (*_stars in [0..10]).  Old
columns are kept for backward compatibility; new code derives w_* from stars
via softmax for UI use.
"""

from sqlalchemy import text


async def run_bsk_skill_stars_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_map_pool)"))).fetchall()]
        new_cols = [
            # ── Independent skill stars (0..10 per axis) ──
            ("aim_stars",   "FLOAT"),
            ("speed_stars", "FLOAT"),
            ("acc_stars",   "FLOAT"),
            ("cons_stars",  "FLOAT"),

            # ── New parser features (acc-targeted) ──
            ("f_subdiv_entropy",     "FLOAT"),  # entropy of subdivision usage
            ("f_polyrhythm_density", "FLOAT"),  # 4s windows with mixed subdivisions
            ("f_off_beat_ratio",     "FLOAT"),  # mean snap distance from 1/4 grid
            ("f_jack_density",       "FLOAT"),  # stacked notes (close in space + close in time)
            ("f_slider_tail_demand", "FLOAT"),  # long sliders × repeats × density
            ("f_od_demand",          "FLOAT"),  # (od-5)/5 × NPS_norm

            # ── Refined features ──
            ("f_flow_break",      "FLOAT"),  # sharp angles + spaced
            ("f_bpm_rel_speed",   "FLOAT"),  # 1/4 ratio relative to BPM
            ("f_intensity_floor", "FLOAT"),  # min density over 8s windows
            ("f_pattern_repeat",  "FLOAT"),  # self-repetition score
        ]
        for col, typedef in new_cols:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_map_pool ADD COLUMN {col} {typedef}"))
