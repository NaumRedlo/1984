"""PP calculator using rosu-pp-py.

Downloads .osu beatmap files and calculates PP for different scenarios:
current play, if FC, if SS.
"""

import asyncio
from typing import Dict, Optional

import aiohttp

from utils.logger import get_logger

try:
    import rosu_pp_py as rosu
except ImportError:
    rosu = None

logger = get_logger("utils.pp_calculator")

# In-memory cache for .osu file bytes: beatmap_id -> bytes
_osu_file_cache: Dict[int, bytes] = {}
_MAX_CACHE = 200


async def _download_osu_file(beatmap_id: int) -> Optional[bytes]:
    """Download .osu file from osu! servers."""
    if beatmap_id in _osu_file_cache:
        return _osu_file_cache[beatmap_id]

    url = f"https://osu.ppy.sh/osu/{beatmap_id}"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    logger.debug(f"Failed to download .osu file for {beatmap_id}: HTTP {resp.status}")
                    return None
                data = await resp.read()
                if len(data) < 50:
                    return None
                # Evict oldest if cache is full
                if len(_osu_file_cache) >= _MAX_CACHE:
                    oldest = next(iter(_osu_file_cache))
                    del _osu_file_cache[oldest]
                _osu_file_cache[beatmap_id] = data
                return data
    except Exception as e:
        logger.debug(f"Error downloading .osu for {beatmap_id}: {e}")
        return None


def _parse_mods(mods_str: str) -> int:
    """Convert mod string like 'HDDT' to rosu-pp mod bitfield."""
    MOD_BITS = {
        "NF": 1 << 0,
        "EZ": 1 << 1,
        "TD": 1 << 2,
        "HD": 1 << 3,
        "HR": 1 << 4,
        "SD": 1 << 5,
        "DT": 1 << 6,
        "RX": 1 << 7,
        "HT": 1 << 8,
        "NC": (1 << 6) | (1 << 9),
        "FL": 1 << 10,
        "SO": 1 << 12,
        "PF": (1 << 5) | (1 << 14),
        "CL": 0,
    }
    bits = 0
    for i in range(0, len(mods_str), 2):
        mod = mods_str[i:i + 2]
        bits |= MOD_BITS.get(mod, 0)
    return bits


def _calc_sync(
    osu_data: bytes,
    mods_int: int,
    accuracy: float,
    combo: int,
    misses: int,
    count_300: int,
    count_100: int,
    count_50: int,
) -> Dict:
    """Synchronous PP calculation. Run in thread pool."""
    beatmap = rosu.Beatmap(bytes=osu_data)

    # Current play PP
    perf = rosu.Performance(
        mods=mods_int,
        n300=count_300,
        n100=count_100,
        n50=count_50,
        misses=misses,
        combo=combo,
    )
    current = perf.calculate(beatmap)

    # If FC: same accuracy distribution but 0 misses, max combo
    # Redistribute misses into 300s for if-FC scenario
    perf_fc = rosu.Performance(
        mods=mods_int,
        n300=count_300 + misses,
        n100=count_100,
        n50=count_50,
        misses=0,
    )
    fc_result = perf_fc.calculate(beatmap)

    # If SS: 100% accuracy, max combo, 0 misses
    perf_ss = rosu.Performance(
        mods=mods_int,
        accuracy=100.0,
        misses=0,
    )
    ss_result = perf_ss.calculate(beatmap)

    return {
        "pp_current": round(current.pp, 2),
        "pp_if_fc": round(fc_result.pp, 2),
        "pp_if_ss": round(ss_result.pp, 2),
        "star_rating": round(current.difficulty.stars, 2),
    }


async def calculate_pp(
    beatmap_id: int,
    mods_str: str = "",
    accuracy: float = 100.0,
    combo: int = 0,
    misses: int = 0,
    count_300: int = 0,
    count_100: int = 0,
    count_50: int = 0,
) -> Optional[Dict]:
    """Calculate PP for current play, if FC, and if SS.

    Returns dict with pp_current, pp_if_fc, pp_if_ss, star_rating
    or None if calculation fails or rosu-pp-py is not installed.
    """
    if rosu is None:
        logger.debug("rosu-pp-py not installed, skipping PP calculation")
        return None

    osu_data = await _download_osu_file(beatmap_id)
    if not osu_data:
        return None

    mods_int = _parse_mods(mods_str)

    try:
        return await asyncio.to_thread(
            _calc_sync, osu_data, mods_int,
            accuracy, combo, misses,
            count_300, count_100, count_50,
        )
    except Exception as e:
        logger.warning(f"PP calculation failed for beatmap {beatmap_id}: {e}")
        return None
