from typing import Dict, Optional

from utils.logger import get_logger
from utils.osu import assay_service

logger = get_logger("utils.pp_calculator")

# Every figure here comes from the assay service, which counts with osu!'s own packages.
# Without it (ASSAY_URL unset or the service down) there is no figure rather than a wrong one.

async def calculate_strains(beatmap_id: int, mods=None, points: int = 64,
                            checksum: Optional[str] = None, ruleset: int = 0) -> Optional[list]:
    served = await assay_service.strains(beatmap_id, mods=mods or "", points=points, checksum=checksum,
                                         ruleset=ruleset)
    strains = (served or {}).get("strains")
    return list(strains) if strains else None

async def calculate_pp(
    beatmap_id: int,
    *,
    mods=None,
    statistics: Optional[Dict] = None,
    accuracy: Optional[float] = None,
    combo: int = 0,
    checksum: Optional[str] = None,
    legacy_total_score: Optional[int] = None,
    is_legacy: Optional[bool] = None,
    ruleset: int = 0,
) -> Optional[Dict]:
    served = await assay_service.score(
        beatmap_id,
        mods=mods or "",
        statistics=statistics or {},
        checksum=checksum,
        accuracy=accuracy / 100 if accuracy is not None else None,
        max_combo=combo or None,
        legacy_total_score=legacy_total_score,
        is_legacy=is_legacy,
        ruleset=ruleset,
    )
    if not served or served.get("pp") is None:
        return None
    # if FC / if SS are only simulated for osu!standard; elsewhere they are left out
    if_fc, if_ss = served.get("pp_if_fc"), served.get("pp_if_ss")
    return {
        "pp_current": round(served["pp"], 2),
        "pp_if_fc": round(if_fc, 2) if if_fc is not None else None,
        "pp_if_ss": round(if_ss, 2) if if_ss is not None else None,
        "star_rating": round(served["star_rating"], 2),
        "max_combo": int(served["map"]["max_combo"]),
        "attributes": dict(served["map"].get("attributes") or {}),
        "source": "assay",
    }

DRIFT_WARNING = 0.01

def note_drift(score_id, beatmap_id: int, official_pp: float, computed: Dict) -> Optional[float]:
    ours = computed.get("pp_current")
    if not official_pp or not ours:
        return None
    drift = (ours - official_pp) / official_pp
    if abs(drift) > DRIFT_WARNING:
        logger.warning(
            "pp drift: score %s on beatmap %s — osu! says %.2f, %s says %.2f (%+.1f%%)",
            score_id, beatmap_id, official_pp, computed.get("source", "assay"), ours, drift * 100,
        )
    return drift

WHATIF_BRACKETS = (95.0, 98.0, 99.0, 100.0)

async def calculate_whatif_pp(beatmap_id: int, accuracy: float, mods_str: str = "",
                              checksum: Optional[str] = None) -> Optional[Dict]:
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
