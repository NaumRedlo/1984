"""Migration: honest per-run breakdown of how map weights were produced."""

from sqlalchemy import text


async def run_bsk_ml_run_breakdown_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_ml_runs)"))).fetchall()]
        for col, typ in [
            ("maps_data_driven",     "INTEGER"),
            ("maps_rf_prior",        "INTEGER"),
            ("maps_heuristic",       "INTEGER"),
            ("global_model_trained", "INTEGER"),
            ("global_model_samples", "INTEGER"),
            ("oob_r2",               "REAL"),
            ("feature_importances",  "TEXT"),
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_ml_runs ADD COLUMN {col} {typ}"))
