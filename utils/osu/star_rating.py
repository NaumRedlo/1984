"""The star rating a play actually had, taken from ppy rather than guessed.

The API hands back `beatmap.difficulty_rating` on every score, and it is the
*nominal* figure — the map with no mods on it. Showing that against a play is
wrong for most of them, because most of them are HD, DT or HR, and each of
those moves the number. Measured against ppy's own attributes endpoint on three
maps, one mod at a time:

    NF SD RX SO PF CL   +0.000 — the figure cannot move, so no call is made
    HD                  +0.441  +0.433  +0.328
    TD                  -0.782  -0.219  -1.027
    HR                  +0.385  +0.520  +0.425
    EZ                  +0.727  -0.149  +0.185
    FL                  +1.656  +1.963  +1.427
    HT                  -1.504  -1.870  -1.255
    DT NC               +3.690  +4.928  +2.627

A DT play on a 6.67 map is 10.36, and the card used to call it 6.67.

Asked of ppy rather than worked out here. The project carries rosu-pp and it
was the obvious tool, but it is a port and it is behind: at 4.0.2, the newest
there is, it disagrees with ppy by 0.20 to 0.82 stars on the same map and mods,
and its performance figures overshoot the API's by ten to sixty per cent. A
number that is nearly right is worse than one taken from the source, when the
source answers in one call and caches for ever.

So rosu keeps the questions ppy has no endpoint for — what a play would have
been worth without the misses — and everything the game itself knows is asked
of the game.
"""

import asyncio
from typing import Any, Iterable, Optional, Sequence

from utils.logger import get_logger

logger = get_logger("utils.osu.star_rating")


async def resolve(client, beatmap_id, mods, nominal) -> Optional[float]:
    """One play's star rating, or the nominal figure when it cannot be had.

    Never raises and never returns nothing: a card with a slightly stale figure
    on it is worth more than a card that failed to draw.
    """
    if client is None or not beatmap_id:
        return nominal
    try:
        return await client.effective_sr(beatmap_id, mods, nominal)
    except Exception:  # noqa: BLE001 — a flaky endpoint must not lose the card
        logger.debug("star rating lookup failed for %s (%s)", beatmap_id, mods, exc_info=True)
        return nominal


async def fill(
    client,
    rows: Sequence[dict],
    *,
    beatmap_key: str = "beatmap_id",
    mods_key: str = "mods",
    sr_key: str = "eff_sr",
    nominal_key: str = "star_rating",
) -> None:
    """Replace the nominal rating on each row with the one the play had.

    In place, because every caller here is handing the same list on to a card
    builder and a second list to keep in step with the first is a bug waiting
    for somebody to add a column.

    Given all the rows at once rather than called per row, so a page of five
    is five lookups issued together. They still leave one at a time — the
    client holds a lock and a delay between requests — but the wait for one is
    the wait for all of them, and the client caches by map and mods, so a page
    revisited costs nothing.

    Rows whose mods cannot move the figure never reach the network; that check
    is `_sr_mods_bitset` in the client and it is why a nomod page is free.

    Done again even for rows that already carry a resolved figure, because a
    great many of them carry the wrong one: they were resolved against a mod
    table that left HD out, so every HD play was written down at its nominal
    rating and there is no way to tell those apart from a play whose mods truly
    did not matter. Asking again repairs them where they are read, which is
    cheaper than a migration over every row anyone might one day look at.
    """
    if client is None or not rows:
        return
    wanted = [row for row in rows if isinstance(row, dict) and row.get(beatmap_key)]
    if not wanted:
        return
    found = await asyncio.gather(*(
        resolve(
            client,
            row.get(beatmap_key),
            row.get(mods_key),
            row.get(nominal_key) or row.get(sr_key),
        )
        for row in wanted
    ))
    for row, rating in zip(wanted, found):
        if rating is not None:
            row[sr_key] = rating


__all__ = ["fill", "resolve"]
