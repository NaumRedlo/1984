"""Migration: create hps_map_pool table.

Plan: unified-giggling-tiger (step 4/9).

HPS-side counterpart to duel_map_pool.  Holds rule-tagged map metadata
for the weekly bounty generator: genre / length / bpm buckets, per-
bounty-type suitability hints (JSON), and anti-repeat tracking via
`last_used_at` + `use_count`.

Idempotent — uses IF NOT EXISTS so the migration is safe to re-run on
existing databases.
"""

from sqlalchemy import text


async def run_hps_map_pool_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hps_map_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                beatmap_id    INTEGER NOT NULL UNIQUE,
                beatmapset_id INTEGER NOT NULL,

                title   VARCHAR(255) NOT NULL,
                artist  VARCHAR(255) NOT NULL,
                version VARCHAR(255) NOT NULL,
                creator VARCHAR(255),

                star_rating FLOAT   NOT NULL,
                bpm         FLOAT,
                length      INTEGER,
                ar          FLOAT,
                od          FLOAT,
                cs          FLOAT,
                max_combo   INTEGER,

                genre_tag     VARCHAR(20),
                length_bucket VARCHAR(10),
                bpm_bucket    VARCHAR(10),
                ranked_status VARCHAR(20),
                typing_hints  TEXT,

                last_used_at DATETIME,
                use_count    INTEGER NOT NULL DEFAULT 0,

                enabled  BOOLEAN  NOT NULL DEFAULT 1,
                added_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hps_map_pool_beatmap_id "
            "ON hps_map_pool(beatmap_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hps_map_pool_last_used_at "
            "ON hps_map_pool(last_used_at)"
        ))
