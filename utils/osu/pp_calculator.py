"""PP calculator using rosu-pp-py.

Downloads .osu beatmap files and calculates PP for different scenarios:
current play, if FC, if SS.
"""

import asyncio
from typing import Dict, Optional

import aiohttp

from utils.logger import get_logger
from utils.osu import assay
from utils.osu.mod_utils import MOD_BITS

try:
    import rosu_pp_py as rosu
except ImportError:
    rosu = None

logger = get_logger("utils.pp_calculator")

# In-memory cache for .osu file bytes: beatmap_id -> bytes
_osu_file_cache: Dict[int, bytes] = {}
_MAX_CACHE = 200


async def _download_osu_file(beatmap_id: int) -> Optional[bytes]:
    """Download .osu file from osu! servers.

    Also handed to `assay.for_score`, which needs the same bytes on disk — the
    engine is another process and takes a path.
    """
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
    total_objects: int = 0,
) -> Dict:
    """Synchronous PP calculation. Run in thread pool."""
    beatmap = rosu.Beatmap(bytes=osu_data)

    # How many objects the play was actually judged on. A play that failed or
    # was quit covers part of the map, and the difficulty it is scored against
    # has to be the part it reached — otherwise the whole map's difficulty is
    # credited to a play that never saw most of it, and because a player who
    # died has no misses to show for it, it reads as a near-perfect run of
    # something very hard. Measured on a map where a quarter-way play should be
    # worth 237: told nothing, rosu says 450.
    judged = count_300 + count_100 + count_50 + misses
    partial = bool(total_objects) and 0 < judged < total_objects

    # Current play PP
    perf = rosu.Performance(
        mods=mods_int,
        n300=count_300,
        n100=count_100,
        n50=count_50,
        misses=misses,
        combo=combo,
    )
    if partial:
        perf.set_passed_objects(judged)
    current = perf.calculate(beatmap)

    if partial:
        # "If FC" on a play that stopped means finishing it, so this one is
        # asked about the whole map rather than the part that was reached — at
        # the accuracy the play was managing, with the misses made good.
        weighted = 300 * (count_300 + misses) + 100 * count_100 + 50 * count_50
        fc_acc = 100.0 * weighted / (300 * judged) if judged else 100.0
        perf_fc = rosu.Performance(mods=mods_int, accuracy=fc_acc, misses=0)
    else:
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
        # Map's full max combo — the recent-score API often omits it, so the card
        # falls back to this for the COMBO bar + MAX COMBO stat.
        "max_combo": int(current.difficulty.max_combo or 0),
    }


def _strains_sync(osu_data: bytes, mods_int: int, points: int) -> Optional[list]:
    """Compute a normalized (0..1) performance/difficulty curve for the map.

    Uses rosu-pp per-section strains (osu!std: aim + speed). Downsampled to
    `points` averaged buckets for a compact line chart. Returns None if the map
    has no usable strain data."""
    beatmap = rosu.Beatmap(bytes=osu_data)
    diff = rosu.Difficulty(mods=mods_int)
    s = diff.strains(beatmap)

    aim = list(getattr(s, "aim", None) or [])
    speed = list(getattr(s, "speed", None) or [])
    if aim and speed and len(aim) == len(speed):
        series = [a + sp for a, sp in zip(aim, speed)]
    else:
        # Non-std modes expose other lists; take the first non-empty one.
        series = aim or speed or []
        if not series:
            for name in ("movement", "color", "rhythm", "stamina", "strains"):
                lst = getattr(s, name, None)
                if lst:
                    series = list(lst)
                    break
    series = [max(0.0, float(v)) for v in series]
    if not series:
        return None

    mx = max(series) or 1.0
    norm = [v / mx for v in series]
    n = len(norm)
    if n <= points:
        return [round(v, 4) for v in norm]
    out = []
    for i in range(points):
        lo = i * n // points
        hi = max(lo + 1, (i + 1) * n // points)
        chunk = norm[lo:hi]
        out.append(round(sum(chunk) / len(chunk), 4))
    return out


async def calculate_strains(beatmap_id: int, mods_str: str = "", points: int = 64) -> Optional[list]:
    """Normalized (0..1) difficulty curve for the recent-card performance graph,
    or None if unavailable. Reuses the cached .osu download."""
    if rosu is None:
        return None
    osu_data = await _download_osu_file(beatmap_id)
    if not osu_data:
        return None
    try:
        return await asyncio.to_thread(_strains_sync, osu_data, _parse_mods(mods_str), points)
    except Exception as e:
        logger.debug(f"strain calc failed for beatmap {beatmap_id}: {e}")
        return None


async def calculate_pp(
    beatmap_id: int,
    mods_str: str = "",
    accuracy: float = 100.0,
    combo: int = 0,
    misses: int = 0,
    count_300: int = 0,
    count_100: int = 0,
    count_50: int = 0,
    total_objects: int = 0,
    classic: bool = False,
    legacy_total_score: Optional[int] = None,
    slider_ends: Optional[int] = None,
    large_tick_misses: int = 0,
) -> Optional[Dict]:
    """Calculate PP for current play, if FC, and if SS.

    `total_objects` is how many objects the map has. Pass it: without it a play
    that failed halfway is scored against the whole map and comes out around
    twice what it is worth.

    `slider_ends` and `large_tick_misses` are what a *lazer* score knows about
    itself and a classic one cannot. They matter twice over: they say how much
    combo was really lost, and they count towards accuracy — under lazer's rules
    a slider tail is worth 150 and a large tick 30, so a play graded 825/85/2/16
    is 91.99% by the old arithmetic and 93.26% by the game's.

    `classic` and `legacy_total_score` say the play was scored the old way and
    what it scored. Pass them too, for a reason of the same size. A stable score
    records neither the slider ends it dropped nor the ticks it missed, so read
    as though it were a lazer score its combo losses vanish: a play that broke
    at 973 of 2354 with two misses is read as having broken exactly twice, and
    comes out at 263pp where the game says 243. With the total, the breaks are
    read out of the score itself and it comes out at 243.32.

    Returns dict with pp_current, pp_if_fc, pp_if_ss, star_rating
    or None if calculation fails or rosu-pp-py is not installed.
    """
    # Our own engine first, which is a port of ppy's own calculators graded
    # against their answers — see utils/osu/assay.py for what that replaced and
    # why. rosu is kept behind it because the engine is built separately from
    # the bot and a deployment can be half done.
    # The counts are passed as a set or not at all: a zero among them is a fact
    # about the play, not an absence, and turning one into "unknown" makes the
    # engine solve for a perfect play instead. That mistake put a 353pp score at
    # 422 and made "if unbroken" and "if perfect" the same number.
    counted = any(value is not None for value in (count_300, count_100, count_50))
    answer = await assay.for_score(
        beatmap_id, _download_osu_file, mods_str,
        # Always given, because the engine believes it rather than deriving it —
        # under lazer's rules accuracy is not derivable from the judgements.
        accuracy=accuracy,
        combo=combo or None,
        misses=misses,
        count_300=count_300 if counted else None,
        count_100=count_100 if counted else None,
        count_50=count_50 if counted else None,
        classic=classic,
        legacy_total=legacy_total_score,
        slider_ends=slider_ends,
        large_tick_misses=large_tick_misses,
    )
    if answer and answer.get("pp") is not None:
        return {
            "pp_current": round(answer["pp"], 2),
            # Computed rather than estimated. These two were shown as the port's
            # figures scaled to the official pp, because the port's own were not
            # good enough to show; they are the figures now.
            "pp_if_fc": round(answer["pp_if_unbroken"], 2),
            "pp_if_ss": round(answer["pp_if_perfect"], 2),
            "star_rating": round(answer["star_rating"], 2),
            "max_combo": int(answer["max_combo"]),
        }

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
            count_300, count_100, count_50, total_objects,
        )
    except Exception as e:
        logger.warning(f"PP calculation failed for beatmap {beatmap_id}: {e}")
        return None


#  Reference accuracy milestones shown alongside the player's own queried
# accuracy on the `map` card, so "227pp at 94%" has context ("...and 100%
# would be 320pp"). Computed on the SAME loaded beatmap as the main query,
# so this costs a few extra Performance.calculate() calls, not another
# download.
WHATIF_BRACKETS = (95.0, 98.0, 99.0, 100.0)


def _calc_whatif_sync(osu_data: bytes, mods_int: int, accuracy: float) -> Dict:
    """Hypothetical full-clear (0 misses) at `accuracy`% — the `map` command's
    "what if I played this at X% with these mods" calculator. Not a real play:
    rosu-pp itself picks a plausible 300/100/50 split to reach that accuracy
    (exposed via .state), so the breakdown returned here is rosu's own,
    not something this function invents."""
    beatmap = rosu.Beatmap(bytes=osu_data)
    perf = rosu.Performance(mods=mods_int, accuracy=accuracy, misses=0)
    result = perf.calculate(beatmap)
    state = result.state
    brackets = {}
    for pct in WHATIF_BRACKETS:
        bp = rosu.Performance(mods=mods_int, accuracy=pct, misses=0)
        brackets[pct] = round(bp.calculate(beatmap).pp, 2)
    return {
        "pp": round(result.pp, 2),
        "star_rating": round(result.difficulty.stars, 2),
        # Map's full max combo, same fallback rationale as _calc_sync's.
        "max_combo": int(result.difficulty.max_combo or 0),
        "combo": int(state.max_combo) if state else int(result.difficulty.max_combo or 0),
        "count_300": int(state.n300) if state else 0,
        "count_100": int(state.n100) if state else 0,
        "count_50": int(state.n50) if state else 0,
        "count_miss": int(state.misses) if state else 0,
        "brackets": brackets,
    }


async def calculate_whatif_pp(beatmap_id: int, accuracy: float, mods_str: str = "") -> Optional[Dict]:
    """Hypothetical FC PP at `accuracy`% + `mods_str` — no real play involved.
    Returns a dict (see _calc_whatif_sync) or None if rosu-pp-py is
    unavailable or the .osu file can't be downloaded. Reuses the same cached
    .osu download as calculate_pp/calculate_strains."""
    if rosu is None:
        logger.debug("rosu-pp-py not installed, skipping whatif PP calculation")
        return None

    osu_data = await _download_osu_file(beatmap_id)
    if not osu_data:
        return None

    try:
        return await asyncio.to_thread(_calc_whatif_sync, osu_data, _parse_mods(mods_str), accuracy)
    except Exception as e:
        logger.warning(f"whatif PP calculation failed for beatmap {beatmap_id}: {e}")
        return None
