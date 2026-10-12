import json
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists

logger = logging.getLogger(__name__)

OLD = (
    "hps_points", "rank", "season_bonus_hps", "bounties_participated", "duel_wins", "duel_losses", "last_active_bounty_id",
    "duel_user_aim", "duel_user_speed", "duel_user_acc", "duel_user_cons", "duel_skill_calculated_at", "bp", "weekly_tier",
    "weekly_tier_set_at",
    "render_size", "render_fps", "render_mute", "render_background", "render_bare", "render_effects", "render_music",
    "render_hitsounds", "render_map_hitsounds", "render_dim", "render_meter", "render_volume", "render_leaderboard",
    "render_cursor", "render_blur", "heavy_renders", "heavy_renders_on",
)
KEPT_IN = os.path.join("data", "removed")


def kept_path(now: datetime) -> str:
    return os.path.join(KEPT_IN, f"users-old-chat-data-{now.strftime('%Y%m%d-%H%M%S')}.json")


async def run_drop_old_chat_data_migration(engine, *, kept_in: str | None = None, now: datetime | None = None) -> list[str]:
    async with engine.begin() as conn:
        if not await table_exists(conn, "users"):
            return []
        held = [name for name in OLD if name in await existing_columns(conn, "users")]
        if not held:
            return []
        listed = ", ".join(f'"{name}"' for name in held)
        rows = (await conn.execute(text(f'SELECT id, chat_id, telegram_id, osu_username, {listed} FROM users ORDER BY id'))).mappings().all()
        path = kept_in or kept_path(now or datetime.now(timezone.utc))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as out:
            json.dump([{key: (value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()} for row in rows], out, ensure_ascii=False)
        for name in held:
            await conn.execute(text(f'ALTER TABLE users DROP COLUMN "{name}"'))
    logger.info("removed %d old columns from users; their values are kept in %s", len(held), path)
    return held
