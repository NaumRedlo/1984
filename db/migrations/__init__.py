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


__all__ = ["run_all_migrations"]
