import asyncio
from typing import Dict, Optional

import aiohttp

from utils.logger import get_logger
from utils.osu import assay, assay_service
from utils.osu.mod_utils import MOD_BITS

try:
    import rosu_pp_py as rosu
except ImportError:
    rosu = None

logger = get_logger("utils.pp_calculator")

_osu_file_cache: Dict[int, bytes] = {}
_MAX_CACHE = 200

async def _download_osu_file(beatmap_id: int) -> Optional[bytes]:
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

                if len(_osu_file_cache) >= _MAX_CACHE:
                    oldest = next(iter(_osu_file_cache))
                    del _osu_file_cache[oldest]
                _osu_file_cache[beatmap_id] = data
                return data
    except Exception as e:
        logger.debug(f"Error downloading .osu for {beatmap_id}: {e}")
        return None

def _parse_mods(mods_str: str) -> int:
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
    beatmap = rosu.Beatmap(bytes=osu_data)
    judged = count_300 + count_100 + count_50 + misses
    partial = bool(total_objects) and 0 < judged < total_objects

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
        weighted = 300 * (count_300 + misses) + 100 * count_100 + 50 * count_50
        fc_acc = 100.0 * weighted / (300 * judged) if judged else 100.0
        perf_fc = rosu.Performance(mods=mods_int, accuracy=fc_acc, misses=0)
    else:
        perf_fc = rosu.Performance(
            mods=mods_int,
            n300=count_300 + misses,
            n100=count_100,
            n50=count_50,
            misses=0,
        )
    fc_result = perf_fc.calculate(beatmap)

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
        "max_combo": int(current.difficulty.max_combo or 0),
    }

def _strains_sync(osu_data: bytes, mods_int: int, points: int) -> Optional[list]:
    beatmap = rosu.Beatmap(bytes=osu_data)
    diff = rosu.Difficulty(mods=mods_int)
    s = diff.strains(beatmap)

    aim = list(getattr(s, "aim", None) or [])
    speed = list(getattr(s, "speed", None) or [])
    if aim and speed and len(aim) == len(speed):
        series = [a + sp for a, sp in zip(aim, speed)]
    else:

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
    statistics: Optional[Dict] = None,
    mods: Optional[list] = None,
    checksum: Optional[str] = None,
    is_legacy: Optional[bool] = None,
) -> Optional[Dict]:
    served = await assay_service.score(
        beatmap_id,
        mods=mods if mods is not None else mods_str,
        statistics=statistics or {
            "great": count_300 or 0, "ok": count_100 or 0,
            "meh": count_50 or 0, "miss": misses or 0,
        },
        checksum=checksum,
        accuracy=accuracy / 100 if accuracy is not None else None,
        max_combo=combo or None,
        legacy_total_score=legacy_total_score,
        is_legacy=is_legacy,
    )
    if served and served.get("pp") is not None:
        return {
            "pp_current": round(served["pp"], 2),
            "pp_if_fc": round(served.get("pp_if_fc") or served["pp"], 2),
            "pp_if_ss": round(served.get("pp_if_ss") or served["pp"], 2),
            "star_rating": round(served["star_rating"], 2),
            "max_combo": int(served["map"]["max_combo"]),
            "source": "assay",
        }

    counted = any(value is not None for value in (count_300, count_100, count_50))
    answer = await assay.for_score(
        beatmap_id, _download_osu_file, mods_str,
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
            "pp_if_fc": round(answer["pp_if_unbroken"], 2),
            "pp_if_ss": round(answer["pp_if_perfect"], 2),
            "star_rating": round(answer["star_rating"], 2),
            "max_combo": int(answer["max_combo"]),
            "source": "engine",
        }

    logger.warning(
        "pp: the engine did not answer for beatmap %s — falling back to "
        "rosu-pp-py, whose figures are behind the game's",
        beatmap_id,
    )

    if rosu is None:
        logger.warning("pp: and rosu-pp-py is not installed either — no figure at all")
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

DRIFT_WARNING = 0.01

def note_drift(score_id, beatmap_id: int, official_pp: float, computed: Dict) -> Optional[float]:
    ours = computed.get("pp_current")
    if not official_pp or not ours:
        return None
    drift = (ours - official_pp) / official_pp
    if abs(drift) > DRIFT_WARNING:
        logger.warning(
            "pp drift: score %s on beatmap %s — osu! says %.2f, %s says %.2f (%+.1f%%)",
            score_id, beatmap_id, official_pp, computed.get("source", "rosu-pp-py"), ours, drift * 100,
        )
    return drift

WHATIF_BRACKETS = (95.0, 98.0, 99.0, 100.0)

def _calc_whatif_sync(osu_data: bytes, mods_int: int, accuracy: float) -> Dict:
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

        "max_combo": int(result.difficulty.max_combo or 0),
        "combo": int(state.max_combo) if state else int(result.difficulty.max_combo or 0),
        "count_300": int(state.n300) if state else 0,
        "count_100": int(state.n100) if state else 0,
        "count_50": int(state.n50) if state else 0,
        "count_miss": int(state.misses) if state else 0,
        "brackets": brackets,
    }

async def _whatif_from_service(beatmap_id: int, accuracy: float, mods_str: str,
                               checksum: Optional[str]) -> Optional[Dict]:
    wanted = [*WHATIF_BRACKETS, accuracy]
    served = await assay_service.whatif(beatmap_id, wanted, mods=mods_str, checksum=checksum)
    points = (served or {}).get("points") or []
    if len(points) != len(wanted):
        return None
    asked = points[-1]
    hits = asked.get("statistics") or {}
    whole = int(served["map"]["max_combo"])
    return {
        "pp": round(asked["pp"], 2),
        "star_rating": round(served["map"]["star_rating"], 2),
        "max_combo": whole,
        "combo": whole,
        "count_300": int(hits.get("great", 0)),
        "count_100": int(hits.get("ok", 0)),
        "count_50": int(hits.get("meh", 0)),
        "count_miss": int(hits.get("miss", 0)),
        "brackets": {pct: round(point["pp"], 2) for pct, point in zip(WHATIF_BRACKETS, points)},
    }

async def calculate_whatif_pp(beatmap_id: int, accuracy: float, mods_str: str = "",
                              checksum: Optional[str] = None) -> Optional[Dict]:
    served = await _whatif_from_service(beatmap_id, accuracy, mods_str, checksum)
    if served:
        return served

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
