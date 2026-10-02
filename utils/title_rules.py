from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from utils.title_history import A_OR_BETTER, S_OR_BETTER, SS_RANKS, History, Play, longest_run

FAILS_PER_SESSION = 10
FRESH_WINDOW = timedelta(days=30)
YEAR = timedelta(days=365)
LONG_SHIFT_SECONDS = 600
QUICK_SECONDS = 120

def _naive(moment: Optional[datetime]) -> Optional[datetime]:
    if moment is not None and moment.tzinfo is not None:
        return moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment

def daily_report(h: History, user) -> int:
    per_day = Counter(h.day(p.played_at) for p in h.attempts if p.ranked)
    return max(per_day.values(), default=0)

def approved_record(h: History, user) -> int:
    since = _naive(getattr(user, "created_at", None))
    if since is None:
        return 0
    return 1 if any(p.passed and p.fc and p.counts and p.played_at >= since for p in h.attempts) else 0

def fresh_ink(h: History, user) -> int:
    for p in h.attempts:
        if p.passed and p.counts and p.ranked and p.ranked_date is not None:
            if timedelta(0) <= p.played_at - p.ranked_date < FRESH_WINDOW:
                return 1
    return 0

def map_hopper(h: History, user) -> int:
    return max((len({p.beatmap_id for p in s}) for s in h.sessions()), default=0)

def routine_inspection(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts), lambda p: p.passed and p.rank in A_OR_BETTER)

def revision_department(h: History, user) -> int:
    best: Dict[tuple, int] = {}
    gains: Counter = Counter()
    for p in h.attempts:
        if not (p.passed and p.counts):
            continue
        key = (p.beatmap_id, p.mods)
        before = best.get(key)
        if before is None:
            best[key] = p.score
        elif p.score > before:
            best[key] = p.score
            gains[p.beatmap_id] += 1
    return max(gains.values(), default=0)

def no_warmup(h: History, user) -> int:
    seen: set = set()
    for p in h.attempts:
        day = h.day(p.played_at)
        if day in seen:
            continue
        seen.add(day)
        if p.passed and p.counts and p.rank in S_OR_BETTER:
            return 1
    return 0

def long_shift(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.length >= LONG_SHIFT_SECONDS for p in h.plays) else 0

def ministry_accuracy(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts), lambda p: p.passed and (p.accuracy or 0.0) >= 98.0)

def one_and_done(h: History, user) -> int:
    played_before = {p.beatmap_id for p in h.undated}
    first: Dict[int, Play] = {}
    for p in h.attempts:
        first.setdefault(p.beatmap_id, p)
    return 1 if any(
        p.beatmap_id not in played_before and p.passed and p.counts and p.ranked and p.fc and p.sr >= 5.0
        for p in first.values()
    ) else 0

def time_traveller(h: History, user) -> int:
    best = 0
    for session in h.sessions():
        years = {p.ranked_date.year for p in session if p.passed and p.counts and p.ranked and p.ranked_date is not None}
        best = max(best, len(years))
    return best

def mod_passport(h: History, user) -> int:
    stamps: set = set()
    for p in h.plays:
        if not (p.passed and p.counts and p.sr >= 5.0):
            continue
        style = p.style
        if not style:
            stamps.add("NM")
        stamps.update(m for m in ("HD", "HR", "FL") if m in style)
        if style & {"DT", "NC"}:
            stamps.add("DT")
    return len(stamps)

def _separate_styles(h: History, min_sr: float) -> int:
    done: set = set()
    for p in h.plays:
        if not (p.passed and p.counts and p.fc and p.sr >= min_sr):
            continue
        style = p.style
        if not style:
            done.add("NM")
        elif style == {"HD"}:
            done.add("HD")
        elif style == {"HR"}:
            done.add("HR")
        elif style in ({"DT"}, {"NC"}):
            done.add("DT")
    return len(done)

def four_ministries(h: History, user) -> int:
    return _separate_styles(h, 5.0)

def all_seeing_eye(h: History, user) -> int:
    return _separate_styles(h, 7.0)

def two_minutes_hate(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.fc and p.sr >= 7.0 and 0 < p.played_length < QUICK_SECONDS for p in h.plays) else 0

def clean_sweep(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts and p.sr >= 5.0), lambda p: p.passed and p.fc)

def untouchable(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts and p.sr >= 6.0), lambda p: p.passed and p.fc)

def model_citizen(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts and p.ranked), lambda p: p.passed and p.rank in S_OR_BETTER)

def idealist(h: History, user) -> int:
    return longest_run((p for p in h.attempts if p.counts and p.ranked and p.sr >= 5.0), lambda p: p.passed and p.rank in SS_RANKS)

def inner_party(h: History, user) -> int:
    best = 0
    for session in h.sessions():
        maps = {p.beatmap_id for p in session if p.passed and p.counts and p.rank in SS_RANKS and p.sr >= 6.0}
        best = max(best, len(maps))
    return best

def perfect_week(h: History, user) -> int:
    days = sorted({h.day(p.played_at) for p in h.attempts if p.passed and p.counts and p.rank in SS_RANKS})
    best = run = 0
    previous: Optional[date] = None
    for day in days:
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return best

def exact_combo(h: History, user) -> int:
    return 1 if any(p.max_combo == 1984 for p in h.plays) else 0

def thoughtcrime(h: History, user) -> int:
    return 1 if any(p.passed and p.rank not in SS_RANKS and p.accuracy is not None and round(p.accuracy, 2) == 99.99 for p in h.plays) else 0

def _scores(h: History) -> Dict[int, Dict[int, Optional[datetime]]]:
    by_score: Dict[int, Dict[int, Optional[datetime]]] = defaultdict(dict)
    for p in h.plays:
        if p.passed and p.score > 0:
            held = by_score[p.score]
            if held.get(p.beatmap_id) is None:
                held[p.beatmap_id] = p.ranked_date
    return by_score

def dejavu(h: History, user) -> int:
    return 1 if any(len(maps) >= 2 for maps in _scores(h).values()) else 0

def memory_hole(h: History, user) -> int:
    for maps in _scores(h).values():
        dates = [d for d in maps.values() if d is not None]
        if len(maps) >= 2 and len(dates) >= 2 and max(dates) - min(dates) >= YEAR:
            return 1
    return 0

def _improved(h: History, before: Iterable[str], after: Iterable[str]) -> int:
    before, after = tuple(before), tuple(after)
    by_map: Dict[int, List[str]] = defaultdict(list)
    for p in h.undated:
        if p.passed:
            by_map[p.beatmap_id].append(p.rank)
    for p in h.attempts:
        if p.passed:
            by_map[p.beatmap_id].append(p.rank)
    for ranks in by_map.values():
        started = False
        for rank in ranks:
            if started and rank in after:
                return 1
            if rank in before:
                started = True
    return 0

def reeducated(h: History, user) -> int:
    return _improved(h, ("D",), A_OR_BETTER)

def perfectionist(h: History, user) -> int:
    return _improved(h, ("S", "SH"), SS_RANKS)

def total_failure(h: History, user) -> int:
    counted: Counter = Counter()
    for session in h.sessions():
        for beatmap, fails in Counter(p.beatmap_id for p in session if p.failed).items():
            counted[beatmap] += min(fails, FAILS_PER_SESSION)
    for beatmap, fails in Counter(p.beatmap_id for p in h.undated if p.failed).items():
        counted[beatmap] += min(fails, FAILS_PER_SESSION)
    return max(counted.values(), default=0)

def persistent(h: History, user) -> int:
    by_map: Dict[int, List[Play]] = defaultdict(list)
    for p in h.attempts:
        by_map[p.beatmap_id].append(p)
    best = 0
    for plays in by_map.values():
        fails = 0
        for p in plays:
            if p.failed:
                fails += 1
            elif p.passed:
                best = max(best, fails)
    return best

def clockwork(h: History, user) -> int:
    return max(h.playtime_by_day().values(), default=0)

def assembly_line(h: History, user) -> int:
    return max((len({p.beatmap_id for p in s if p.ranked}) for s in h.sessions()), default=0)

def rapid_fire(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.fc and p.sr >= 6.0 and p.eff_bpm >= 240.0 for p in h.plays) else 0

def close_to_absolute(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.rank in SS_RANKS and p.sr >= 6.5 and p.eff_bpm >= 240.0 for p in h.plays) else 0

def overdrive(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.fc and p.sr >= 7.0 and p.eff_bpm >= 300.0 for p in h.plays) else 0

def double_sentence(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.fc and p.sr >= 7.0 and {"HD", "HR"} <= p.mods for p in h.plays) else 0

def heavy_hand(h: History, user) -> int:
    from utils.osu.mod_utils import apply_mods

    for p in h.plays:
        if p.passed and p.counts and p.fc and p.base_sr >= 5.0 and p.ar is not None:
            joined = "".join(sorted(p.mods - {"CL"}))
            if apply_mods(0.0, float(p.ar), 0.0, 0.0, 0.0, 0, joined)["ar"] >= 10.3:
                return 1
    return 0

def double_digit(h: History, user) -> int:
    return 1 if any(p.passed and p.counts and p.sr >= 10.0 for p in h.plays) else 0

RULES = {
    "daily_report": daily_report,
    "approved_record": approved_record,
    "fresh_ink": fresh_ink,
    "map_hopper": map_hopper,
    "routine_inspection": routine_inspection,
    "revision_department": revision_department,
    "no_warmup": no_warmup,
    "long_shift": long_shift,
    "ministry_accuracy": ministry_accuracy,
    "one_and_done": one_and_done,
    "time_traveller": time_traveller,
    "mod_passport": mod_passport,
    "clean_sweep": clean_sweep,
    "four_ministries": four_ministries,
    "two_minutes_hate": two_minutes_hate,
    "model_citizen": model_citizen,
    "untouchable": untouchable,
    "inner_party": inner_party,
    "perfect_week": perfect_week,
    "all_seeing_eye": all_seeing_eye,
    "combo_1984": exact_combo,
    "thoughtcrime": thoughtcrime,
    "memory_hole": memory_hole,
    "dejavu": dejavu,
    "reeducated": reeducated,
    "perfectionist": perfectionist,
    "off_day": total_failure,
    "lowacc_streak_10": persistent,
    "session_3h": clockwork,
    "session_30maps": assembly_line,
    "ss_streak_10": idealist,
    "fc_bpm_210": rapid_fire,
    "ss_bpm240": close_to_absolute,
    "fc_bpm_250": overdrive,
    "hdhr_fc7": double_sentence,
    "heavy_hand": heavy_hand,
    "sr_10": double_digit,
}
