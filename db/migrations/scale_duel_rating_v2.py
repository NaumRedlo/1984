"""Migration: scale the duel rating μ-system ×1.5 (v2 ladder).

The duel TrueSkill environment was rescaled ×90 from stock (was ×60): mu0
1500→2250, sigma0 500→750, beta/tau and the pp→μ seed curve all ×1.5, and the
``rating_to_sr`` divisor 333→500 so map difficulty is preserved exactly. The
division ladder (``DUEL_DIVISION_THRESHOLDS``) moved with it, turning Rhythmus
into an *exclusive* apex (Rhythmus I = 5000). Multiplying every stored belief
(mu, sigma, peak_mu) by 1.5 keeps each existing player's division and SR target
identical under the new constants — the whole change is behaviour-preserving.

Idempotency: a ×1.5 UPDATE is NOT naturally idempotent (running it twice would
×2.25 every rating). We gate it on a one-shot marker row in ``bot_settings``
(``duel_rating_scale_v2`` = ``done``); a second run is a no-op. On a fresh DB
the table is empty so the UPDATE touches 0 rows and the marker simply records
that new rows already start on the v2 scale.
"""

from sqlalchemy import text

_MARKER_KEY = "duel_rating_scale_v2"
_SCALE = 1.5


async def run_scale_duel_rating_v2_migration(engine) -> None:
    async with engine.begin() as conn:
        # The ledger table this migration relies on for its one-shot guard.
        # (Also ensured by add_bot_settings, but be self-contained.)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)"
        ))

        already = (await conn.execute(
            text("SELECT value FROM bot_settings WHERE key = :k"),
            {"k": _MARKER_KEY},
        )).scalar_one_or_none()
        if already is not None:
            return  # ×1.5 already applied — never scale a second time.

        # Only scale if the table exists (create_all runs before migrations, so
        # it normally does; guard anyway for offline/partial schemas).
        has_table = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='duel_ratings'"
        ))).scalar_one_or_none()
        if has_table is not None:
            await conn.execute(text(
                "UPDATE duel_ratings SET mu = mu * :s, sigma = sigma * :s, "
                "peak_mu = peak_mu * :s"
            ), {"s": _SCALE})

        await conn.execute(
            text("INSERT INTO bot_settings (key, value) VALUES (:k, 'done')"),
            {"k": _MARKER_KEY},
        )
