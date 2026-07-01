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
from db.migrations.add_oauth_fields import run_oauth_migration
from db.migrations.add_seasons import run_add_seasons_migration
from db.migrations.add_last_seen import run_last_seen_migration
from db.migrations.add_submission_indexes import run_submission_indexes_migration
from db.migrations.add_bounty_beatmapset_id import run_bounty_beatmapset_id_migration
from db.migrations.add_bot_settings import run_bot_settings_migration
from db.migrations.add_ur_hit_counts import run_ur_hit_counts_migration
from db.migrations.add_bounty_source_tier_conditions import run_bounty_source_tier_conditions_migration
from db.migrations.add_user_bp_weekly_tier import run_user_bp_weekly_tier_migration
from db.migrations.add_weekly_pool_table import run_weekly_pool_table_migration
from db.migrations.add_hps_map_pool import run_hps_map_pool_migration
from db.migrations.add_user_first_approved_at import run_user_first_approved_at_migration
from db.migrations.drop_crawler_settings import run_drop_crawler_settings_migration
from db.migrations.duel_overhaul import run_duel_overhaul_migration
from db.migrations.add_tenant_chat_id import run_tenant_chat_id_migration
from db.migrations.add_oauth_telegram_key import run_oauth_telegram_key_migration
from db.migrations.add_submission_open_unique import run_submission_open_unique_migration
from db.migrations.add_weekly_pool_active_unique import run_weekly_pool_active_unique_migration
from db.migrations.scale_duel_rating_v2 import run_scale_duel_rating_v2_migration
from db.migrations.add_dm_active_tenant import run_dm_active_tenant_migration
from db.migrations.add_best_score_play_fields import run_best_score_play_fields_migration
from db.migrations.add_map_attempt_play_fields import run_map_attempt_play_fields_migration
from db.migrations.add_is_fc_fields import run_is_fc_fields_migration
from db.migrations.add_title_meta_fields import run_title_meta_fields_migration
from db.migrations.add_w4_logging_fields import run_w4_logging_fields_migration
from db.migrations.add_was_supporter_field import run_was_supporter_field_migration
from db.migrations.add_completion_fields import run_completion_fields_migration
from db.migrations.add_batch2_profile_stats import run_batch2_profile_stats_migration
from db.migrations.add_effective_fields import run_effective_fields_migration
from db.migrations.add_render_settings import run_render_settings_migration
from db.migrations.add_render_settings_extra import run_render_settings_extra_migration
from db.migrations.add_render_cache import run_render_cache_migration
from db.migrations.add_user_renders import run_user_renders_migration
from db.migrations.add_render_volumes import run_render_volumes_migration


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
    await run_add_seasons_migration(engine)
    await run_last_seen_migration(engine)
    await run_submission_indexes_migration(engine)
    await run_bounty_beatmapset_id_migration(engine)
    await run_bot_settings_migration(engine)
    await run_ur_hit_counts_migration(engine)
    await run_bounty_source_tier_conditions_migration(engine)
    await run_user_bp_weekly_tier_migration(engine)
    await run_weekly_pool_table_migration(engine)
    await run_hps_map_pool_migration(engine)
    await run_user_first_approved_at_migration(engine)
    await run_drop_crawler_settings_migration(engine)
    # Converts a legacy BSK schema to the duel_* schema after create_all has
    # made the new (empty) tables/columns.
    await run_duel_overhaul_migration(engine)
    # Multi-tenant: rebuild `users` with per-tenant chat_id. Runs after
    # duel_overhaul so the bsk_*→duel_* user-column renames land before the
    # column-intersection copy here.
    await run_tenant_chat_id_migration(engine)
    # OAuth is global per Telegram user: re-key oauth_tokens from per-tenant
    # users.id to telegram_id. Runs after the tenant migration so users.telegram_id
    # is stable for the backfill join.
    await run_oauth_telegram_key_migration(engine)
    # At most one OPEN (tracking/pending) submission per (bounty, user) — the
    # DB-level backstop against the double-accept → double-payout race.
    await run_submission_open_unique_migration(engine)
    # At most one active weekly bounty pool — DB-level backstop behind the
    # generator's lock+guard against two concurrent regen paths racing.
    await run_weekly_pool_active_unique_migration(engine)
    # One-shot ×1.5 rescale of stored duel beliefs (mu/sigma/peak_mu) to the v2
    # μ-system (mu0 1500→2250, exclusive-apex ladder). Behaviour-preserving;
    # gated on a bot_settings marker so it can never double-apply.
    await run_scale_duel_rating_v2_migration(engine)
    # DM access: per-Telegram-identity choice of which group's data to show in a
    # private chat. Additive; create_all also covers a fresh DB.
    await run_dm_active_tenant_migration(engine)
    # Phase B1: per-play columns on user_best_scores (bpm/length/combo/hitstats),
    # backfilled lazily as users re-sync. Additive; create_all covers a fresh DB.
    await run_best_score_play_fields_migration(engine)
    # Live titles: per-play columns on user_map_attempts (+ passed / played_at) so
    # observed recent plays join the title corpus. Additive.
    await run_map_attempt_play_fields_migration(engine)
    # FC titles: capture the API's perfect-combo flag (combo comparison was
    # fragile). Additive on both score tables.
    await run_is_fc_fields_migration(engine)
    # Wave-3 title metadata: users.is_supporter + status/ranked_date on both
    # score tables. Additive; backfilled lazily on re-sync.
    await run_title_meta_fields_migration(engine)
    # Wave-4 title logging subsystems: open/compare counters, daily-activity
    # streak, weekly play_count delta, 180d comeback flag (all on users). Additive.
    await run_w4_logging_fields_migration(engine)
    # Latched "ever a supporter" flag so "Volunteer" is permanent (is_supporter
    # itself must stay current for the profile badge). Additive.
    await run_was_supporter_field_migration(engine)
    # Completion %: count_300 + total_objects on map_attempts so failed plays can
    # be scored by how far they got ("Last Note"). Additive; backfilled on re-sync.
    await run_completion_fields_migration(engine)
    # Batch II profile stats: level / join_date / grade counts on users, for the
    # level/account-age/S-rank/SS-rank titles. Additive; backfilled on stats-sync.
    await run_batch2_profile_stats_migration(engine)
    # Effective difficulty: ar + eff_sr on both score tables for the mod-adjusted
    # Batch II titles. Additive; backfilled on re-sync (eff_sr falls back to nominal).
    await run_effective_fields_migration(engine)
    # Local replay renderer: per-user danser render settings. Last (FKs users.id);
    # idempotent. Resurrected 2026-06-29 with the danser cluster.
    await run_render_settings_migration(engine)
    # More per-user render toggles (strain graph / hit counter / seizure warning).
    await run_render_settings_extra_migration(engine)
    # Render cache: replay -> Telegram file_id, so repeat renders re-send instantly
    # without waking the GPU. Additive; idempotent.
    await run_render_cache_migration(engine)
    # Per-user render library (file_id + metadata snapshot) for the /settings
    # "Мои рендеры" picker. Additive; idempotent.
    await run_user_renders_migration(engine)
    # Music / hitsound volume (%) render settings. Additive; idempotent.
    await run_render_volumes_migration(engine)


__all__ = ["run_all_migrations"]
