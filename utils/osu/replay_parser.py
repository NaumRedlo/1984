"""Parse Unstable Rate (UR) from an osu! `.osr` replay file.

Used by the Metronome bounty flow. The auto-checker
(`tasks/bounty_auto_checker.py`) spots a score that satisfies every
condition except UR, downloads the replay through
`OsuApiClient.download_replay(score_id)`, feeds the bytes here, then
re-runs the UR condition with the parsed value.

Scope is deliberately small — we only need a UR number, not full anti-cheat
analysis. The parser:

  1. Reads frames out of the .osr via `osrparse`.
  2. Walks key-press transitions to find each "tap" event in ms.
  3. Loads the beatmap's `[HitObjects]` (timestamps) via the existing
     `services.bsk.osu_parser._parse_hitobjects` helper. Spinners are
     filtered out (they're spun, not tapped).
  4. Greedily pairs each tap to the closest unmatched hit-object within
     the OD-derived hit window. Unpaired taps are ignored (extra clicks);
     unpaired objects are misses and don't contribute.
  5. Returns UR = stddev(hit_errors) × 10.

Returns None on:
  - corrupt .osr
  - non-osu!std game modes (taiko/ctb/mania)
  - empty hit-objects list (parser couldn't read the beatmap)
  - too few matched taps (<10) — UR statistic isn't meaningful
"""

from __future__ import annotations

import io
import math
from typing import Optional

from osrparse import Replay
from osrparse.utils import GameMode, Key

from services.bsk.osu_parser import _parse_hitobjects
from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum number of matched hit events before we trust the UR figure.
# 10 picked as a safe floor; a player who only landed 10 hits on a Metronome
# bounty almost certainly failed the underlying acc/miss check anyway.
MIN_MATCHED_HITS = 10

# Keys that count as a "tap" — left/right mouse, K1, K2.
TAP_KEYS = Key.M1 | Key.M2 | Key.K1 | Key.K2


def _od_hit_window_300(od: Optional[float]) -> float:
    """Standard osu! Great hit window in ms for the given OD.

    Reference: 80 - 6·OD (Stable scoring, applied by both Stable and Lazer).
    OD=0 → 80 ms; OD=10 → 20 ms. None falls back to OD=5 (50 ms).
    Used as the matching tolerance for tap↔object pairing — slightly
    generous (×1.5 in the caller) since we want to capture taps a little
    late/early too, not just 300-judgement ones.
    """
    od_val = 5.0 if od is None else float(od)
    return max(20.0, 80.0 - 6.0 * od_val)


def _frame_taps(replay: Replay) -> list[int]:
    """Return absolute (ms) timestamps of each tap-down transition.

    osrparse frames carry `time_delta` (cumulative offset to add) and a
    `keys` bitfield. A tap is a frame where any tap-key bit went from 0
    to 1 compared to the previous frame.
    """
    taps: list[int] = []
    t = 0
    prev = Key(0)
    for ev in replay.replay_data:
        # `time_delta` may be -12345 on the seed-marker frame at the very
        # end of stable replays; ignore those.
        if ev.time_delta < 0:
            continue
        t += ev.time_delta
        keys = getattr(ev, "keys", None)
        if keys is None:
            continue
        pressed_now = keys & TAP_KEYS
        pressed_prev = prev & TAP_KEYS
        # New bits = pressed_now AND NOT pressed_prev. Any new bit → tap.
        if (pressed_now & ~pressed_prev) != Key(0):
            taps.append(t)
        prev = keys
    return taps


def _match_taps_to_objects(
    taps: list[int], objects: list[dict], hit_window_ms: float,
) -> list[float]:
    """Pair each hit-object to its closest unmatched tap within ±hit_window.

    Greedy two-pointer: both lists are time-ordered. For each object, walk
    the tap list forward to find the closest tap inside the window. Unused
    taps are dropped (e.g. spinner spins, slider re-presses, extra clicks).
    """
    errors: list[float] = []
    i = 0  # tap cursor
    for obj in objects:
        # Spinners are spun, not tapped — they'd inflate the error count
        # and skew UR. Slider heads still count (they're a tap event).
        if obj.get("spinner"):
            continue
        obj_t = float(obj["t"])
        # Advance past any tap clearly before the window opens.
        while i < len(taps) and taps[i] < obj_t - hit_window_ms:
            i += 1
        if i >= len(taps):
            break
        # Pick the closer of taps[i] and taps[i+1] if both lie inside the
        # window — covers the case where the player double-tapped.
        best_j = i
        best_err = abs(taps[i] - obj_t)
        if best_err > hit_window_ms:
            continue
        if i + 1 < len(taps):
            err2 = abs(taps[i + 1] - obj_t)
            if err2 < best_err and err2 <= hit_window_ms:
                best_j = i + 1
                best_err = err2
        errors.append(float(taps[best_j] - obj_t))
        i = best_j + 1  # consume this tap so the next object doesn't reuse it
    return errors


def _stddev(values: list[float]) -> float:
    """Population stddev (the convention UR uses). Caller guarantees n≥2."""
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var)


async def parse_ur_from_osr(
    osr_bytes: bytes,
    *,
    osu_text: str,
    od: Optional[float] = None,
) -> Optional[float]:
    """Compute Unstable Rate from a replay + the matching .osu file text.

    `osu_text` is the raw contents of the beatmap's `.osu` file — fetch it
    via `OsuApiClient.download_osu_file(beatmap_id)` and decode to UTF-8.
    `od` is optional; when absent it's parsed out of the .osu metadata
    (OverallDifficulty line) with a final fallback to 5.0.

    Returns the UR in ms (osu! standard convention: stddev × 10), or None
    on any failure (logged with the cause).
    """
    try:
        replay = Replay.from_string(osr_bytes)
    except Exception as e:
        logger.warning(f"parse_ur_from_osr: osrparse failed: {e}")
        return None

    if replay.mode != GameMode.STD:
        logger.info(f"parse_ur_from_osr: replay mode is {replay.mode}, only STD supported")
        return None

    objects = _parse_hitobjects(osu_text)
    if not objects:
        logger.warning("parse_ur_from_osr: beatmap had 0 hit-objects")
        return None

    # OD fallback: read "OverallDifficulty:" out of the .osu Difficulty section.
    if od is None:
        for line in osu_text.splitlines():
            if line.startswith("OverallDifficulty:"):
                try:
                    od = float(line.split(":", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
                break
        if od is None:
            od = 5.0

    hit_window = _od_hit_window_300(od) * 1.5  # 1.5× — see _od_hit_window_300 doc
    taps = _frame_taps(replay)
    if not taps:
        logger.warning("parse_ur_from_osr: replay had 0 tap events")
        return None

    errors = _match_taps_to_objects(taps, objects, hit_window)
    if len(errors) < MIN_MATCHED_HITS:
        logger.info(
            f"parse_ur_from_osr: only matched {len(errors)} hits (need ≥{MIN_MATCHED_HITS})"
        )
        return None

    return _stddev(errors) * 10.0


__all__ = ["parse_ur_from_osr", "MIN_MATCHED_HITS"]
