"""Migration: create BSK duel tables."""

from sqlalchemy import text


async def run_bsk_duels_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bsk_duels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player1_user_id INTEGER NOT NULL REFERENCES users(id),
                player2_user_id INTEGER NOT NULL REFERENCES users(id),
                mode TEXT NOT NULL DEFAULT 'casual',
                status TEXT NOT NULL DEFAULT 'pending',
                chat_id INTEGER,
                message_id INTEGER,
                player1_total_score REAL NOT NULL DEFAULT 0.0,
                player2_total_score REAL NOT NULL DEFAULT 0.0,
                winner_user_id INTEGER REFERENCES users(id),
                current_round INTEGER NOT NULL DEFAULT 0,
                total_rounds INTEGER NOT NULL DEFAULT 5,
                current_star_rating REAL NOT NULL DEFAULT 0.0,
                pressure_offset REAL NOT NULL DEFAULT 0.0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                accepted_at DATETIME,
                completed_at DATETIME,
                expires_at DATETIME
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bsk_duels_status ON bsk_duels(status)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bsk_duels_players ON bsk_duels(player1_user_id, player2_user_id)"))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bsk_duel_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                duel_id INTEGER NOT NULL REFERENCES bsk_duels(id),
                round_number INTEGER NOT NULL,
                beatmap_id INTEGER,
                beatmapset_id INTEGER,
                beatmap_title TEXT,
                star_rating REAL NOT NULL DEFAULT 0.0,
                w_aim REAL NOT NULL DEFAULT 0.25,
                w_speed REAL NOT NULL DEFAULT 0.25,
                w_acc REAL NOT NULL DEFAULT 0.25,
                w_cons REAL NOT NULL DEFAULT 0.25,
                player1_score INTEGER,
                player1_accuracy REAL,
                player1_combo INTEGER,
                player1_misses INTEGER,
                player1_pp REAL,
                player1_composite REAL,
                player1_submitted_at DATETIME,
                player2_score INTEGER,
                player2_accuracy REAL,
                player2_combo INTEGER,
                player2_misses INTEGER,
                player2_pp REAL,
                player2_composite REAL,
                player2_submitted_at DATETIME,
                winner_player INTEGER,
                status TEXT NOT NULL DEFAULT 'waiting',
                started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                forfeit_at DATETIME
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bsk_duel_rounds_duel_id ON bsk_duel_rounds(duel_id)"))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bsk_map_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                beatmap_id INTEGER NOT NULL UNIQUE,
                beatmapset_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                version TEXT NOT NULL,
                creator TEXT,
                star_rating REAL NOT NULL,
                bpm REAL,
                length INTEGER,
                ar REAL,
                od REAL,
                cs REAL,
                w_aim REAL NOT NULL DEFAULT 0.25,
                w_speed REAL NOT NULL DEFAULT 0.25,
                w_acc REAL NOT NULL DEFAULT 0.25,
                w_cons REAL NOT NULL DEFAULT 0.25,
                map_type TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            )
        """))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_bsk_map_pool_beatmap_id ON bsk_map_pool(beatmap_id)"))
