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
from db.migrations.add_heavy_render_settings import run_heavy_render_migration
from db.migrations.add_render_effects import run_render_effects_migration
from db.migrations.add_map_hitsounds import run_map_hitsounds_migration
from db.migrations.add_render_dim import run_render_dim_migration
from db.migrations.add_render_meter import run_render_meter_migration
from db.migrations.add_render_volume import run_render_volume_migration
from db.migrations.add_render_leaderboard import run_render_leaderboard_migration
from db.migrations.add_render_cursor import run_render_cursor_migration
from db.migrations.add_render_blur import run_render_blur_migration
from db.migrations.add_render_levels import run_render_levels_migration
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
from db.migrations.add_pp_estimated import run_pp_estimated_migration
from db.migrations.add_completion_fields import run_completion_fields_migration
from db.migrations.add_batch2_profile_stats import run_batch2_profile_stats_migration
from db.migrations.add_effective_fields import run_effective_fields_migration
from db.migrations.add_best_score_pp_delta_fields import run_best_score_pp_delta_fields_migration
from db.migrations.add_leaderboard_snapshots import run_leaderboard_snapshots_migration
from db.migrations.add_last_full_update import run_last_full_update_migration

async def run_all_migrations(engine) -> None:
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
    await run_heavy_render_migration(engine)
    await run_render_effects_migration(engine)
    await run_render_levels_migration(engine)
    await run_map_hitsounds_migration(engine)
    await run_render_dim_migration(engine)
    await run_render_meter_migration(engine)
    await run_render_volume_migration(engine)
    await run_render_leaderboard_migration(engine)
    await run_render_cursor_migration(engine)
    await run_render_blur_migration(engine)
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

__all__ = ["run_all_migrations"]
