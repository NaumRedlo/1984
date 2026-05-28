"""Centralised entry point for running all schema migrations in order.

Both `bot/main.py` (startup) and offline scripts (dry-run, backfill) call
`run_all_migrations(engine)` so the schema stays consistent regardless of
which entry point opened the database.  Adding a new migration: import its
runner here and append the await to the body.
"""

from db.migrations.add_leaderboard_fields import run_migration
from db.migrations.add_avatar_cover_fields import run_avatar_migration
from db.migrations.add_beatmapset_id import run_beatmapset_id_migration
from db.migrations.add_total_score import run_total_score_migration
from db.migrations.add_avatar_cover_cache import run_avatar_cache_migration
from db.migrations.add_best_score_score import run_best_score_score_migration
from db.migrations.add_map_attempts import run_map_attempts_migration
from db.migrations.add_user_unlink_at import run_user_unlink_at_migration
from db.migrations.add_render_settings import run_render_settings_migration
from db.migrations.add_oauth_fields import run_oauth_migration
from db.migrations.add_seasons import run_add_seasons_migration
from db.migrations.add_bsk_tables import run_bsk_migration
from db.migrations.add_bsk_duels import run_bsk_duels_migration
from db.migrations.add_last_seen import run_last_seen_migration
from db.migrations.add_bsk_ml_runs import run_bsk_ml_runs_migration
from db.migrations.add_bsk_duel_test import run_bsk_duel_test_migration
from db.migrations.add_bsk_duel_overhaul import run_bsk_duel_overhaul_migration
from db.migrations.add_bsk_pause_ml_accuracy import run_bsk_pause_ml_accuracy_migration
from db.migrations.add_bsk_pick_phase import run_bsk_pick_phase_migration
from db.migrations.add_bsk_map_features import run_bsk_map_features_migration
from db.migrations.add_bsk_hp_drain import run_bsk_hp_drain_migration
from db.migrations.add_bsk_map_features_v2 import run_bsk_map_features_v2_migration
from db.migrations.add_bsk_pool_turn import run_bsk_pool_turn_migration
from db.migrations.add_bsk_skill_stars import run_bsk_skill_stars_migration
from db.migrations.add_bsk_per_player_pool import run_bsk_per_player_pool_migration
from db.migrations.add_bsk_ml_run_breakdown import run_bsk_ml_run_breakdown_migration
from db.migrations.add_bsk_duel_thread_id import run_bsk_duel_thread_id_migration
from db.migrations.add_bsk_duel_match_id import run_bsk_duel_match_id_migration
from db.migrations.add_submission_indexes import run_submission_indexes_migration
from db.migrations.add_bounty_beatmapset_id import run_bounty_beatmapset_id_migration
from db.migrations.add_bot_settings import run_bot_settings_migration
from db.migrations.add_user_bsk_skill import run_user_bsk_skill_migration
from db.migrations.add_ur_hit_counts import run_ur_hit_counts_migration
from db.migrations.add_bounty_source_tier_conditions import run_bounty_source_tier_conditions_migration
from db.migrations.add_user_bp_weekly_tier import run_user_bp_weekly_tier_migration
from db.migrations.add_weekly_pool_table import run_weekly_pool_table_migration
from db.migrations.add_hps_map_pool import run_hps_map_pool_migration
from db.migrations.add_user_first_approved_at import run_user_first_approved_at_migration


async def run_all_migrations(engine) -> None:
    await run_migration(engine)
    await run_avatar_migration(engine)
    await run_beatmapset_id_migration(engine)
    await run_total_score_migration(engine)
    await run_avatar_cache_migration(engine)
    await run_best_score_score_migration(engine)
    await run_map_attempts_migration(engine)
    await run_user_unlink_at_migration(engine)
    await run_render_settings_migration(engine)
    await run_oauth_migration(engine)
    await run_add_seasons_migration(engine)
    await run_bsk_migration(engine)
    await run_bsk_duels_migration(engine)
    await run_last_seen_migration(engine)
    await run_bsk_ml_runs_migration(engine)
    await run_bsk_duel_test_migration(engine)
    await run_bsk_duel_overhaul_migration(engine)
    await run_bsk_pause_ml_accuracy_migration(engine)
    await run_bsk_pick_phase_migration(engine)
    await run_bsk_map_features_migration(engine)
    await run_bsk_hp_drain_migration(engine)
    await run_bsk_map_features_v2_migration(engine)
    await run_bsk_pool_turn_migration(engine)
    await run_bsk_skill_stars_migration(engine)
    await run_bsk_per_player_pool_migration(engine)
    await run_bsk_ml_run_breakdown_migration(engine)
    await run_bsk_duel_thread_id_migration(engine)
    await run_bsk_duel_match_id_migration(engine)
    await run_submission_indexes_migration(engine)
    await run_bounty_beatmapset_id_migration(engine)
    await run_bot_settings_migration(engine)
    await run_user_bsk_skill_migration(engine)
    await run_ur_hit_counts_migration(engine)
    await run_bounty_source_tier_conditions_migration(engine)
    await run_user_bp_weekly_tier_migration(engine)
    await run_weekly_pool_table_migration(engine)
    await run_hps_map_pool_migration(engine)
    await run_user_first_approved_at_migration(engine)


__all__ = ["run_all_migrations"]
