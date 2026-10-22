from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select

from db.models.local_score import LocalScore
from utils.logger import get_logger
from utils.timeutils import utcnow

logger = get_logger("render_farm.map_checks")

OK, OTHER, GONE = "ok", "other", "gone"
BATCH = 40
GONE_AGAIN = timedelta(days=7)

def _date(said: Any) -> Optional[datetime]:
    if not isinstance(said, str) or not said:
        return None
    try:
        moment = datetime.fromisoformat(said.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment

def verdict(found: Any) -> tuple[str, Optional[int], Optional[str], Optional[datetime]]:
    if not isinstance(found, dict):
        return GONE, None, None, None
    beatmap = found.get("id")
    beatmap = beatmap if isinstance(beatmap, int) and not isinstance(beatmap, bool) and beatmap > 0 else None
    status = found.get("status")
    status = status.lower() if isinstance(status, str) and status else None
    beatmapset = found.get("beatmapset") if isinstance(found.get("beatmapset"), dict) else {}
    return OK, beatmap, status, _date(beatmapset.get("ranked_date"))

async def due(session, *, now: datetime, batch: int = BATCH) -> list[str]:
    rows = await session.execute(
        select(LocalScore.beatmap_md5)
        .where(or_(LocalScore.checked.is_(None), (LocalScore.checked == GONE) & (LocalScore.checked_at < now - GONE_AGAIN)))
        .group_by(LocalScore.beatmap_md5)
        .limit(batch)
    )
    return [md5 for (md5,) in rows.all()]

async def check(session, osu, *, now: Optional[datetime] = None, batch: int = BATCH) -> dict[str, int]:
    now = now or utcnow()
    told = {OK: 0, OTHER: 0, GONE: 0}
    touched: set[int] = set()
    for md5 in await due(session, now=now, batch=batch):
        try:
            found = await osu.lookup_beatmap_by_checksum(md5)
        except Exception as exc:
            logger.info("the map %s could not be looked up now: %s", md5, exc)
            continue
        said, beatmap, status, ranked = verdict(found)
        rows = (await session.execute(select(LocalScore).where(LocalScore.beatmap_md5 == md5))).scalars().all()
        for row in rows:
            if said == OK and beatmap is not None and row.beatmap_id != beatmap:
                row.checked = OTHER
            else:
                row.checked = said
            if row.checked == OK:
                row.status = status or row.status
                row.ranked_date = ranked
            row.checked_at = now
            told[row.checked] += 1
            touched.add(row.player_id)
    await session.commit()
    if any(told.values()):
        logger.info("local scores checked against osu!: %s for %d players", told, len(touched))
    return told
