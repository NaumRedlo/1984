"""osu! mod-adjusted difficulty calculation.

Applies HR/EZ/DT/NC/HT modifiers to beatmap attributes
using the official osu! formulas.
"""

from typing import Dict, Tuple

# Single source of truth for "which mod acronyms exist and what rosu-pp bit
# each maps to" — pp_calculator._parse_mods imports MOD_BITS rather than
# keeping its own copy, since the two dicts drifting apart silently would be
# a hard-to-notice bug (a mod parsing fine here but not there, or vice versa).
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
KNOWN_PP_MODS = frozenset(MOD_BITS)

# The 5 mods exposed as toggle controls on the `map` what-if card — both the
# card's own mod-pill row (map_card.py) and the interactive keyboard
# (maplink/whatif.py) key off this exact set/order, so a button always
# corresponds to a real pill on the card.
WHATIF_MOD_SET: Tuple[str, ...] = ("EZ", "HD", "HR", "DT", "NF")


def parse_mods_tokens(mods_str: str) -> Tuple[str, ...]:
    """Split a concatenated mod string ('HDDT') into 2-char acronym tokens,
    e.g. for validating user-typed mods against KNOWN_PP_MODS before use."""
    return tuple(mods_str[i:i + 2] for i in range(0, len(mods_str), 2))


# How much harder each mod makes a play, as stable's own score multiplier says.
#
# Not a scale anyone here invented: these are the numbers the engine already
# uses for stable's ScoreV1, in `dossier/crates/dossier-sim/src/multiplier.rs`,
# and they are the game's own statement of how much a mod changes the
# challenge. DT and HT come out of its `rate_adjust_v1` at the standard rates
# (1.5x -> 1.1, 0.75x -> 0.3) rather than being written down twice.
#
# Anything the game rewards *less* for lands below no-mod, which is what makes
# the ordering read correctly at both ends: FL and DT above, the assists at the
# bottom, and NoFail — a mod you take to avoid losing — under a bare play.
MOD_DIFFICULTY = {
    "FL": 1.12,
    "DT": 1.10,
    "NC": 1.10,
    "HD": 1.06,
    "HR": 1.06,
    "SD": 1.00,
    "PF": 1.00,
    "TD": 1.00,
    "CL": 0.96,
    "SO": 0.90,
    "EZ": 0.50,
    "NF": 0.50,
    "HT": 0.30,
    "RX": 0.10,
    "AP": 0.10,
}


def mod_difficulty(mods_str: str) -> float:
    """How hard a mod combination is, as a single number. No mod is 1.0.

    The multipliers multiply, so a combination always outranks the mods it is
    made of — HDHR is harder than either alone — which is the property that
    makes "the hardest mods on this map" mean something on a board where most
    plays carry two or three.

    An acronym nothing here knows is worth 1.0: an unfamiliar mod should not
    quietly drag a play to the top or the bottom of the order.
    """
    weight = 1.0
    for token in parse_mods_tokens((mods_str or "").upper()):
        weight *= MOD_DIFFICULTY.get(token, 1.0)
    return weight


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
