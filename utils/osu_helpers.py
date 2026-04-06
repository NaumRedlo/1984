import re
from typing import Optional, Dict

from sqlalchemy import select

from db.models.user import User
from utils.logger import get_logger

logger = get_logger(__name__)


def extract_beatmap_id(text: str) -> Optional[str]:
    patterns = [
        r'osu\.ppy\.sh/beatmaps/(\d+)',
        r'osu\.ppy\.sh/beatmapsets/\d+.*?/(\d+)',
        r'^(\d+)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, text.strip())
        if match:
            return match.group(1)
    return None


async def get_community_stats(session) -> Dict[str, int]:
    stmt = select(User.player_pp).where(User.player_pp.is_not(None))
    result = await session.execute(stmt)
    pp_values = [row[0] for row in result.fetchall() if row[0] and row[0] > 0]

    if len(pp_values) < 2:
        logger.warning("Not enough players for community stats, RF will be neutral.")
        return {"p25": 0, "p40": 0, "p60": 0, "p75": 0}

    pp_values.sort()
    count = len(pp_values)

    def percentile(p: int) -> int:
        idx = int(count * p / 100)
        return pp_values[min(idx, count - 1)]

    return {
        "p25": percentile(25),
        "p40": percentile(40),
        "p60": percentile(60),
        "p75": percentile(75),
    }
