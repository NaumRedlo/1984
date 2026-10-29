from db.migrations.drop_old_chat_data import run_drop_old_chat_data_migration
from db.migrations.add_leaderboard_fields import run_migration
from db.migrations.add_avatar_cover_fields import run_avatar_migration
from db.migrations.add_beatmapset_id import run_beatmapset_id_migration
from db.migrations.add_total_score import run_total_score_migration
from db.migrations.add_avatar_cover_cache import run_avatar_cache_migration
from db.migrations.add_best_score_score import run_best_score_score_migration
from db.migrations.add_map_attempts import run_map_attempts_migration
from db.migrations.add_user_unlink_at import run_user_unlink_at_migration
from db.migrations.add_oauth_fields import run_oauth_migration
from db.migrations.add_last_seen import run_last_seen_migration
from db.migrations.add_bot_settings import run_bot_settings_migration
from db.migrations.add_render_worker_tokens import run_render_worker_tokens_migration
from db.migrations.add_share_replays import run_share_replays_migration
from db.migrations.add_render_settings import run_render_settings_migration
from db.migrations.add_ur_hit_counts import run_ur_hit_counts_migration
from db.migrations.add_user_first_approved_at import run_user_first_approved_at_migration
from db.migrations.drop_crawler_settings import run_drop_crawler_settings_migration
from db.migrations.add_tenant_chat_id import run_tenant_chat_id_migration
from db.migrations.add_oauth_telegram_key import run_oauth_telegram_key_migration
from db.migrations.add_dm_active_tenant import run_dm_active_tenant_migration
from db.migrations.add_best_score_play_fields import run_best_score_play_fields_migration
from db.migrations.add_map_attempt_play_fields import run_map_attempt_play_fields_migration
from db.migrations.add_is_fc_fields import run_is_fc_fields_migration
from db.migrations.add_title_meta_fields import run_title_meta_fields_migration
from db.migrations.add_w4_logging_fields import run_w4_logging_fields_migration
from db.migrations.add_was_supporter_field import run_was_supporter_field_migration
from db.migrations.add_app_profile_fields import run_app_profile_fields_migration
from db.migrations.add_pp_estimated import run_pp_estimated_migration
from db.migrations.add_completion_fields import run_completion_fields_migration
from db.migrations.add_batch2_profile_stats import run_batch2_profile_stats_migration
from db.migrations.add_effective_fields import run_effective_fields_migration
from db.migrations.add_best_score_pp_delta_fields import run_best_score_pp_delta_fields_migration
from db.migrations.add_leaderboard_snapshots import run_leaderboard_snapshots_migration
from db.migrations.add_last_full_update import run_last_full_update_migration
from db.migrations.add_players import run_players_migration
from db.migrations.add_video_sharing import run_video_sharing_migration
from db.migrations.add_app_video_storage import run_app_video_storage_migration
from db.migrations.add_shared_replays import run_shared_replays_migration
from db.migrations.add_witnessed_plays import run_witnessed_plays_migration
from db.migrations.add_witness_sessions import run_witness_sessions_migration
from db.migrations.add_local_scores import run_local_scores_migration
from db.migrations.add_left_members import run_left_members_migration
from db.migrations.move_progress_to_players import run_player_progress_migration
from db.migrations.add_app_accounts import run_app_accounts_migration
from db.migrations.add_billing_change import run_billing_change_migration
from db.migrations.add_support_level import run_support_level_migration
from db.migrations.add_command_ruleset import run_command_ruleset_migration
from db.migrations.add_attempt_ruleset import run_attempt_ruleset_migration
from db.migrations.add_local_score_checks import run_local_score_checks_migration
from db.migrations.add_duel_phases import run_duel_phases_migration
from db.migrations.add_duel_batches import run_duel_batches_migration
from db.migrations.add_duel_review_tiers import run_duel_review_tiers_migration

async def run_all_migrations(engine) -> None:
    if engine.dialect.name == "sqlite":
        await _sqlite_history(engine)
    await run_billing_change_migration(engine)
    await run_support_level_migration(engine)
    await run_command_ruleset_migration(engine)
    await run_attempt_ruleset_migration(engine)
    await run_local_score_checks_migration(engine)
    await run_duel_phases_migration(engine)
    await run_duel_batches_migration(engine)
    await run_duel_review_tiers_migration(engine)
    await run_drop_old_chat_data_migration(engine)

async def _sqlite_history(engine) -> None:
    await run_migration(engine)
    await run_avatar_migration(engine)
    await run_beatmapset_id_migration(engine)
    await run_total_score_migration(engine)
    await run_avatar_cache_migration(engine)
    await run_best_score_score_migration(engine)
    await run_map_attempts_migration(engine)
    await run_user_unlink_at_migration(engine)
    await run_oauth_migration(engine)
    await run_last_seen_migration(engine)
    await run_bot_settings_migration(engine)
    await run_share_replays_migration(engine)
    await run_render_settings_migration(engine)
    await run_ur_hit_counts_migration(engine)
    await run_user_first_approved_at_migration(engine)
    await run_drop_crawler_settings_migration(engine)

    await run_tenant_chat_id_migration(engine)

    await run_oauth_telegram_key_migration(engine)

    await run_dm_active_tenant_migration(engine)

    await run_best_score_play_fields_migration(engine)

    await run_map_attempt_play_fields_migration(engine)

    await run_is_fc_fields_migration(engine)

    await run_title_meta_fields_migration(engine)

    await run_w4_logging_fields_migration(engine)

    await run_was_supporter_field_migration(engine)

    await run_completion_fields_migration(engine)

    await run_batch2_profile_stats_migration(engine)

    await run_effective_fields_migration(engine)

    await run_best_score_pp_delta_fields_migration(engine)

    await run_leaderboard_snapshots_migration(engine)

    await run_last_full_update_migration(engine)
    await run_render_worker_tokens_migration(engine)
    await run_pp_estimated_migration(engine)
    await run_app_profile_fields_migration(engine)
    await run_players_migration(engine)
    await run_video_sharing_migration(engine)
    await run_app_video_storage_migration(engine)
    await run_shared_replays_migration(engine)
    await run_player_progress_migration(engine)
    await run_app_accounts_migration(engine)
    await run_witnessed_plays_migration(engine)
    await run_witness_sessions_migration(engine)
    await run_local_scores_migration(engine)
    await run_left_members_migration(engine)

__all__ = ["run_all_migrations"]
