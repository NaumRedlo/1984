"""
Migration: reset all BSK rating components to pp-seeded values and restore
placement_matches_left = 10 so every player re-calibrates from scratch.
"""

import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def run_bsk_reset_calibration_migration(engine: AsyncEngine) -> None:
    """
    For every existing bsk_ratings row:
      1. Look up the linked user's player_pp via the users table.
      2. Compute start_mu = starting_mu_from_pp(pp), per_comp = start_mu / 4.
      3. Reset all four mu_* components to per_comp.
      4. Reset sigma_* to 100.
      5. Reset placement_matches_left to 10.
      6. Reset wins/losses to 0.
      7. Set peak_mu = start_mu.

    This ensures the new K×6 placement K-factor and the updated SR table
    are applied to all players on the next restart.
    """
    from services.bsk.rating import starting_mu_from_pp

    async with engine.begin() as conn:
        rows = await conn.execute(
            text("""
                SELECT br.id, br.user_id, u.player_pp
                FROM bsk_ratings br
                LEFT JOIN users u ON u.id = br.user_id
            """)
        )
        records = rows.fetchall()

    if not records:
        logger.info("bsk_reset_calibration: no bsk_ratings rows found, nothing to reset.")
        return

    async with engine.begin() as conn:
        for row in records:
            rating_id = row[0]
            pp = float(row[2] or 0)
            start_mu = starting_mu_from_pp(pp)
            per_comp = start_mu / 4.0

            await conn.execute(
                text("""
                    UPDATE bsk_ratings SET
                        mu_aim   = :per_comp,
                        mu_speed = :per_comp,
                        mu_acc   = :per_comp,
                        mu_cons  = :per_comp,
                        sigma_aim   = 100.0,
                        sigma_speed = 100.0,
                        sigma_acc   = 100.0,
                        sigma_cons  = 100.0,
                        placement_matches_left = 10,
                        wins   = 0,
                        losses = 0,
                        peak_mu = :start_mu
                    WHERE id = :id
                """),
                {"per_comp": per_comp, "start_mu": start_mu, "id": rating_id},
            )

    logger.info(f"bsk_reset_calibration: reset {len(records)} rating(s) to pp-seeded values.")
