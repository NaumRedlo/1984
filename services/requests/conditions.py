"""Map-request pass conditions: schema, (de)serialization, human summary, and
the single `score_meets` check reused for both live evaluation and progress.

A condition dict has these keys (all optional; missing = not required):
    pass          bool   must the play be a pass (default True)
    min_accuracy  float  minimum accuracy in PERCENT (0–100), or None
    require_fc    bool   must be a full combo
    min_combo     int    minimum max-combo reached, or None
    mods          str    required mods, e.g. "HDDT" (subset must be present), or None
    min_rank      str    minimum grade: "S" or "SS", or None
"""

from __future__ import annotations

import json
from dataclasses import dataclass


DEFAULT_CONDITIONS = {
    "pass": True,
    "min_accuracy": None,
    "require_fc": False,
    "min_combo": None,
    "mods": None,
    "min_rank": None,
}

# Grade ordering for min_rank. SH == S (silver), X == SS, XH == SSH.
_RANK_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "S": 4, "SH": 4, "X": 5, "XH": 5}

# Conventional display order for mod acronyms (unknown mods sort last, stable).
_MOD_DISPLAY_ORDER = ["EZ", "NF", "HT", "HD", "HR", "SD", "PF", "DT", "NC", "FL", "SO"]
# min_rank is expressed in the wizard as S / SS; map to the internal order key.
_MIN_RANK_KEY = {"S": "S", "SS": "X"}


def default_conditions() -> dict:
    return dict(DEFAULT_CONDITIONS)


def serialize(cond: dict) -> str:
    return json.dumps({**DEFAULT_CONDITIONS, **(cond or {})}, separators=(",", ":"))


def parse(raw: str | None) -> dict:
    """Parse a stored conditions JSON string, filling in defaults for any
    missing keys (tolerant of a malformed/empty value)."""
    if not raw:
        return default_conditions()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return default_conditions()
    if not isinstance(data, dict):
        return default_conditions()
    return {**DEFAULT_CONDITIONS, **data}


def parse_mods(text: str | None) -> frozenset[str]:
    """Normalize a mods string into a set of 2-letter acronyms.

    Accepts "HDDT", "HD DT", "hd,dt". NC (nightcore) is treated as DT for
    matching, SD/PF/SO etc. pass through. Empty / "-" / "nomod" -> empty set."""
    if not text:
        return frozenset()
    t = text.strip().upper()
    if t in ("-", "NM", "NOMOD", "NONE"):
        return frozenset()
    # Split on separators first; otherwise chunk into 2-char acronyms.
    parts: list[str] = []
    for chunk in t.replace(",", " ").split():
        if len(chunk) > 2 and len(chunk) % 2 == 0:
            parts.extend(chunk[i:i + 2] for i in range(0, len(chunk), 2))
        else:
            parts.append(chunk)
    mods = {p for p in parts if len(p) == 2}
    if "NC" in mods:
        mods.discard("NC")
        mods.add("DT")
    return frozenset(mods)


def format_mods(mods: frozenset[str]) -> str:
    """Join mod acronyms in the conventional osu! display order."""
    order = {m: i for i, m in enumerate(_MOD_DISPLAY_ORDER)}
    return "".join(sorted(mods, key=lambda m: (order.get(m, len(order)), m)))


@dataclass(frozen=True)
class Play:
    """Normalized view of one play, from either a UserMapAttempt or a raw score,
    for `score_meets`. accuracy is PERCENT (0–100)."""
    passed: bool
    accuracy: float
    max_combo: int | None
    mods: frozenset[str]
    rank: str | None
    is_fc: bool | None
    count_miss: int | None


def _acc_percent(value) -> float:
    """Accuracy is stored 0–1 (API v2) but conditions are in percent — normalize."""
    v = float(value or 0.0)
    return v * 100.0 if v <= 1.0 else v


def play_from_attempt(attempt) -> Play:
    return Play(
        passed=bool(attempt.passed),
        accuracy=_acc_percent(attempt.accuracy),
        max_combo=attempt.max_combo,
        mods=parse_mods(attempt.mods),
        rank=(attempt.rank or None),
        is_fc=attempt.is_fc,
        count_miss=attempt.count_miss,
    )


def _is_full_combo(play: Play) -> bool:
    if play.is_fc is not None:
        return bool(play.is_fc)
    # Fallback when the API flag wasn't captured: no misses.
    return play.count_miss == 0


def score_meets(cond: dict, play: Play) -> bool:
    """True if `play` satisfies every set condition."""
    cond = {**DEFAULT_CONDITIONS, **(cond or {})}

    if cond.get("pass", True) and not play.passed:
        return False

    min_acc = cond.get("min_accuracy")
    if min_acc is not None and play.accuracy + 1e-9 < float(min_acc):
        return False

    if cond.get("require_fc") and not _is_full_combo(play):
        return False

    min_combo = cond.get("min_combo")
    if min_combo is not None and (play.max_combo or 0) < int(min_combo):
        return False

    required = parse_mods(cond.get("mods"))
    if required and not required <= play.mods:
        return False

    min_rank = cond.get("min_rank")
    if min_rank:
        need = _RANK_ORDER.get(_MIN_RANK_KEY.get(min_rank, min_rank), None)
        have = _RANK_ORDER.get((play.rank or "").upper(), -1)
        if need is not None and have < need:
            return False

    return True


def condition_pills(cond: dict, t, lang: str = "en") -> list[str]:
    """Localized per-condition pill labels (mods excluded — those render as
    badges; use ``parse_mods(cond['mods'])`` for them)."""
    cond = {**DEFAULT_CONDITIONS, **(cond or {})}
    pills: list[str] = [t("req.cond.pass" if cond.get("pass", True) else "req.cond.play", lang)]
    if cond.get("min_accuracy") is not None:
        pills.append(t("req.cond.acc", lang, value=f"{float(cond['min_accuracy']):g}"))
    if cond.get("require_fc"):
        pills.append(t("req.cond.fc", lang))
    elif cond.get("min_combo") is not None:
        pills.append(t("req.cond.combo", lang, value=int(cond["min_combo"])))
    if cond.get("min_rank"):
        pills.append(t("req.cond.rank", lang, value=cond["min_rank"]))
    return pills


def describe(cond: dict, t, lang: str = "en") -> str:
    """One-line human summary of the conditions, localized via `t` (utils.i18n.t)."""
    cond = {**DEFAULT_CONDITIONS, **(cond or {})}
    parts: list[str] = []
    parts.append(t("req.cond.pass" if cond.get("pass", True) else "req.cond.play", lang))
    if cond.get("min_accuracy") is not None:
        parts.append(t("req.cond.acc", lang, value=f"{float(cond['min_accuracy']):g}"))
    if cond.get("require_fc"):
        parts.append(t("req.cond.fc", lang))
    elif cond.get("min_combo") is not None:
        parts.append(t("req.cond.combo", lang, value=int(cond["min_combo"])))
    if cond.get("mods"):
        parts.append(t("req.cond.mods", lang, value=format_mods(parse_mods(cond["mods"])) or cond["mods"]))
    if cond.get("min_rank"):
        parts.append(t("req.cond.rank", lang, value=cond["min_rank"]))
    return " · ".join(parts)
