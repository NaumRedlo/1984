"""osu! mod-adjusted difficulty calculation.

Applies HR/EZ/DT/NC/HT modifiers to beatmap attributes
using the official osu! formulas.
"""

from typing import Dict


def _ar_to_ms(ar: float) -> float:
    if ar > 5:
        return 1200 - 150 * (ar - 5)
    return 1200 + 120 * (5 - ar)


def _ms_to_ar(ms: float) -> float:
    if ms < 1200:
        return 5 + (1200 - ms) / 150
    return 5 - (ms - 1200) / 120


def _od_to_ms(od: float) -> float:
    return 80 - 6 * od


def _ms_to_od(ms: float) -> float:
    return (80 - ms) / 6


def apply_mods(
    cs: float, ar: float, od: float, hp: float,
    bpm: float, length: int, mods_str: str,
) -> Dict:
    """Return mod-adjusted beatmap attributes.

    Args:
        cs, ar, od, hp: raw beatmap values
        bpm: beats per minute
        length: total length in seconds
        mods_str: concatenated mod acronyms, e.g. "HDDT", "HRFL"

    Returns:
        dict with keys: cs, ar, od, hp, bpm, total_length
    """
    mods = {mods_str[i:i + 2] for i in range(0, len(mods_str), 2)} if mods_str else set()

    # HR / EZ (mutually exclusive in practice)
    if "HR" in mods:
        cs = min(cs * 1.3, 10.0)
        ar = min(ar * 1.4, 10.0)
        od = min(od * 1.4, 10.0)
        hp = min(hp * 1.4, 10.0)
    elif "EZ" in mods:
        cs *= 0.5
        ar *= 0.5
        od *= 0.5
        hp *= 0.5

    # DT/NC / HT (speed mods — mutually exclusive)
    rate = 1.0
    if "DT" in mods or "NC" in mods:
        rate = 1.5
    elif "HT" in mods:
        rate = 0.75

    if rate != 1.0:
        bpm = bpm * rate
        length = int(length / rate)

        # AR recalculation through ms
        ar_ms = _ar_to_ms(ar) / rate
        ar = max(0.0, min(_ms_to_ar(ar_ms), 11.0))

        # OD recalculation through ms
        od_ms = _od_to_ms(od) / rate
        od = max(0.0, min(_ms_to_od(od_ms), 11.0))

    return {
        "cs": round(cs, 2),
        "ar": round(ar, 2),
        "od": round(od, 2),
        "hp": round(hp, 2),
        "bpm": round(bpm),
        "total_length": length,
    }
