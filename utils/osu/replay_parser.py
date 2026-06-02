"""Parse Unstable Rate (UR) from an osu! `.osr` replay file.

Used by the Metronome bounty flow. The auto-checker
(`tasks/bounty_auto_checker.py`) spots a score that satisfies every
condition except UR, downloads the replay through
`OsuApiClient.download_replay(score_id)`, feeds the bytes here, then
re-runs the UR condition with the parsed value.

Algorithm follows the stable-style "first eligible tap in window" model
that osu! itself uses for hit-judging, calibrated against circleguard's
reference implementation (`circleguard.investigations.judgments`):

  1. Walk the replay frames, recording (time, x, y) for every rising-edge
     key-press (M1/M2/K1/K2). Releases and held frames don't count.
  2. Hit window = ±hw_50 = ±(200 - 10·OD_eff) ms. OD_eff applies the
     HR/EZ multiplier from the replay mods (1.4 / 0.5, capped at 10).
  3. Hit radius = stable's 64·(1 - 0.7·(CS_eff - 5)/5)/2 · 1.00041.
     CS_eff applies the HR/EZ multiplier (1.3 / 0.5, capped at 10).
  4. For each hit object (circles + slider heads, spinners excluded),
     scan keydowns chronologically and accept the FIRST one inside both
     the time window AND the hit radius. Earlier taps that miss either
     check are discarded (wasted clicks / mashing). This matters: picking
     the time-nearest tap regardless of position underestimates UR by
     ~5 ms in real plays.
  5. UR = stddev(errors) · 10. Population stddev — circleguard, danser
     and Stable's score panel all agree on this.

Returns None on:
  - corrupt .osr
  - non-osu!std game modes (taiko/ctb/mania)
  - empty hit-objects list (parser couldn't read the beatmap)
  - too few matched taps (<10) — UR statistic isn't meaningful
"""

from __future__ import annotations

import math
from typing import Optional

from osrparse import Replay
from osrparse.utils import GameMode, Key, Mod

from utils.osu.parser_core import _parse_hitobjects
from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum number of matched hit events before we trust the UR figure.
# 10 picked as a safe floor; a player who only landed 10 hits on a Metronome
# bounty almost certainly failed the underlying acc/miss check anyway.
MIN_MATCHED_HITS = 10

# Keys that count as a "tap" — left/right mouse, K1, K2.
TAP_KEYS = Key.M1 | Key.M2 | Key.K1 | Key.K2


def _hit_window_50(od: float) -> float:
    """Stable's hw_50: anything outside this is a miss. ±(200 - 10·OD) ms."""
    return max(20.0, 200.0 - 10.0 * od)


def _hit_radius(cs: float) -> float:
    """Stable's hit-object radius in osu-pixels (CS-derived).

    Verified against `circleguard.utils.hitradius`. The 1.00041 nudge is
    a leftover from stable's float32 conversion path; carrying it keeps us
    bit-compatible with both Stable and circleguard.
    """
    return 64.0 * (1.0 - 0.7 * (cs - 5.0) / 5.0) / 2.0 * 1.00041


def _apply_mod_scaling(od: float, cs: float, mods: Mod) -> tuple[float, float]:
    """Return (od_eff, cs_eff) after applying HR / EZ multipliers."""
    if mods & Mod.HardRock:
        od = min(10.0, od * 1.4)
        cs = min(10.0, cs * 1.3)
    elif mods & Mod.Easy:
        od *= 0.5
        cs *= 0.5
    return od, cs


# Sentinel `time_delta` Stable writes on the trailing seed-marker frame.
# Real "selection-screen → map-start" frames also carry a negative delta
# (often ~3 s) and MUST be applied — otherwise every keypress is shifted
# forward in time and the cursor-position check matches the wrong object.
_REPLAY_SEED_MARKER = -12345


def _frame_keydowns(replay: Replay) -> list[tuple[int, float, float]]:
    """Return (time_ms, x, y) for every rising-edge tap in the replay.

    A "rising-edge tap" is a frame where one or more of M1/M2/K1/K2 went
    from released to pressed. Releases and held frames don't count.
    """
    out: list[tuple[int, float, float]] = []
    t = 0
    prev = Key(0)
    for ev in replay.replay_data:
        if ev.time_delta == _REPLAY_SEED_MARKER:
            continue
        t += ev.time_delta
        keys = getattr(ev, "keys", None)
        if keys is None:
            continue
        now = keys & TAP_KEYS
        new_bits = now & ~(prev & TAP_KEYS)
        if new_bits != Key(0) and t >= 0:
            # Pre-zero frames (cursor moves on the selection screen, before
            # the map clock starts) can carry stray keys but never produce
            # gameplay hits — exclude them from the tap list.
            out.append((t, float(ev.x), float(ev.y)))
        prev = keys
    return out


def _match_keydowns_to_objects(
    keydowns: list[tuple[int, float, float]],
    objects: list[dict],
    hit_window_ms: float,
    hit_radius: float,
) -> list[float]:
    """Stable-style hit assignment.

    Walks objects and keydowns together. For each object: skip any taps
    that fell before the object's window opens (they were wasted), then
    advance to the next object if the current tap is past the close of
    the window. While the tap is in the time window, check the cursor
    position — if within hit radius, register the hit; otherwise it's a
    wasted tap and we try the next one for this same object.
    """
    r2 = hit_radius * hit_radius
    errors: list[float] = []
    obj_i = 0
    kd_i = 0
    while obj_i < len(objects) and kd_i < len(keydowns):
        obj = objects[obj_i]
        if obj.get("spinner"):
            obj_i += 1
            continue
        obj_t = float(obj["t"])
        obj_x = float(obj["x"])
        obj_y = float(obj["y"])

        kt, kx, ky = keydowns[kd_i]
        if kt < obj_t - hit_window_ms:
            # Tap fired before this object's window even opened — wasted.
            kd_i += 1
            continue
        if kt > obj_t + hit_window_ms:
            # Window closed without a hit — object will be a miss for UR,
            # the same tap may still match the NEXT object.
            obj_i += 1
            continue
        # Tap is inside the time window. Check cursor position.
        dx = kx - obj_x
        dy = ky - obj_y
        if dx * dx + dy * dy <= r2:
            errors.append(float(kt - obj_t))
            obj_i += 1
            kd_i += 1
        else:
            # Cursor wasn't on the object — wasted tap, try the next one
            # against this same object.
            kd_i += 1
    return errors


def _stddev(values: list[float]) -> float:
    """Population stddev. Caller guarantees n≥2."""
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var)


def _read_difficulty_field(osu_text: str, key: str, default: float) -> float:
    """Pull a `[Difficulty]` numeric field out of the .osu text."""
    needle = f"{key}:"
    for line in osu_text.splitlines():
        if line.startswith(needle):
            try:
                return float(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
            break
    return default


async def parse_ur_from_osr(
    osr_bytes: bytes,
    *,
    osu_text: str,
    od: Optional[float] = None,
    cs: Optional[float] = None,
) -> Optional[float]:
    """Compute Unstable Rate from a replay + the matching .osu file text.

    `osu_text` is the raw contents of the beatmap's `.osu` file — fetch
    it via `OsuApiClient.download_osu_file(beatmap_id)` and decode to
    UTF-8. `od` / `cs` are optional; when absent they're read from the
    `[Difficulty]` section (final fallback 5.0 / 4.0). HR/EZ mods in the
    replay are applied automatically.

    Returns the UR in ms (osu! convention: stddev · 10), or None on
    any failure (logged with the cause).
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

    if od is None:
        od = _read_difficulty_field(osu_text, "OverallDifficulty", 5.0)
    if cs is None:
        cs = _read_difficulty_field(osu_text, "CircleSize", 4.0)

    od_eff, cs_eff = _apply_mod_scaling(od, cs, replay.mods)
    hit_window = _hit_window_50(od_eff)
    hit_radius = _hit_radius(cs_eff)

    keydowns = _frame_keydowns(replay)
    if not keydowns:
        logger.warning("parse_ur_from_osr: replay had 0 tap events")
        return None

    errors = _match_keydowns_to_objects(keydowns, objects, hit_window, hit_radius)
    if len(errors) < MIN_MATCHED_HITS:
        logger.info(
            f"parse_ur_from_osr: only matched {len(errors)} hits (need ≥{MIN_MATCHED_HITS})"
        )
        return None

    return _stddev(errors) * 10.0


__all__ = ["parse_ur_from_osr", "MIN_MATCHED_HITS"]
