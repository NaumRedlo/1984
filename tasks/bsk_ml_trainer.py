"""
Nightly ML training job for BSK map skill weights.

Schedule: runs at 02:00, stops by 05:00 (hard deadline). Reads match history
from bsk_duel_rounds + bsk_ratings and updates w_aim / w_speed / w_acc /
w_cons in bsk_map_pool through a three-tier prior:

  Phase 1 — Per-map weighted-Pearson correlation between (mu_diff_c) and the
            continuous win margin. Confidence ~ #rounds; only fires for maps
            with ≥MIN_ROUNDS_PER_MAP completed rounds.

  Phase 2 — Round-level Random Forest. One forest, trained on every round
            (X = map_features ⊕ mu_diffs, y = margin in [0,1], w = time-decay).
            For any map, weights are extracted via partial-dependence probes:
            we vary diff_c by ±IQR/2 with all other diffs zeroed and read off
            the forest's sensitivity on each axis. Pools information across
            maps via shared feature space, so unplayed maps still get an
            informed prior the moment a forest exists.

  Phase 3 — Heuristic prior from osu_parser.weights_from_features. Last
            resort when the dataset is too small to train a forest at all.

Implementation notes: pure-Python CART regression trees with weighted MSE
splits, bootstrap aggregation, OOB R² scoring, normalized SSE-reduction
feature importances. No numpy / sklearn. Run history (status, breakdown,
OOB R², top features, prediction accuracy) is persisted to bsk_ml_runs.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("tasks.bsk_ml_trainer")

HARD_DEADLINE_SECONDS = 3 * 3600  # 3 hours max
MIN_ROUNDS_FOR_TRAINING = 50
# Lowered from 3 → 2: per-map correlation needs ≥2 points to be defined, and
# even a tiny per-map signal is fine because Phase 1 is now blended against the
# round-level RF prior with confidence ~ #rounds, so n=2 contributes ~30 %.
MIN_ROUNDS_PER_MAP = 2

# Time-decay half-life: a round 30 days old contributes half as much as today's.
# Floor keeps very old rounds from going to zero (they still hold some signal
# and we need them to bootstrap fresh datasets).
TIME_DECAY_HALF_LIFE_DAYS = 30.0
TIME_DECAY_FLOOR = 0.10


def _round_decay_weight(completed_at, now) -> float:
    """exp-decay sample weight; clamped to [TIME_DECAY_FLOOR, 1.0]."""
    if completed_at is None:
        return TIME_DECAY_FLOOR
    if completed_at.tzinfo is None:
        from datetime import timezone as _tz
        completed_at = completed_at.replace(tzinfo=_tz.utc)
    age_days = max(0.0, (now - completed_at).total_seconds() / 86400.0)
    w = 0.5 ** (age_days / TIME_DECAY_HALF_LIFE_DAYS)
    return max(TIME_DECAY_FLOOR, min(1.0, w))


def _round_target(rnd) -> tuple[float, str]:
    """Build the regression target for a round in [0, 1].

    Preferred: continuous *win margin* from composite scores —
        target = 0.5 + 0.5·(p1_composite − p2_composite),  clamped to [0, 1].
    A 95%–5% blowout becomes ~0.95; a 51%–49% nail-biter becomes ~0.51.
    This carries far more signal per round than a 0/1 label and lets the
    weighted correlation in Phase 1 distinguish tight wins from one-sided
    rounds. Falls back to a binary label on forfeits / missing scores.

    Returns (target, kind) where kind ∈ {'margin', 'binary', 'tie'}.
    """
    p1c = getattr(rnd, "player1_composite", None)
    p2c = getattr(rnd, "player2_composite", None)
    if p1c is not None and p2c is not None:
        margin = float(p1c) - float(p2c)
        target = 0.5 + 0.5 * margin
        return max(0.0, min(1.0, target)), "margin"

    # Forfeits / partial submissions: full-strength binary fallback
    if rnd.winner_player == 1:
        return 1.0, "binary"
    if rnd.winner_player == 2:
        return 0.0, "binary"
    return 0.5, "tie"

# Global state for monitoring
_current_task: Optional[asyncio.Task] = None
_paused = False
_progress: dict = {}


def get_progress() -> dict:
    """Return current training progress snapshot."""
    return dict(_progress)


def _progress_snapshot() -> dict:
    """Pluck the persistable counters from _progress (for cancelled/timeout paths)."""
    keys = (
        "rounds_used", "maps_updated", "maps_skipped",
        "maps_data_driven", "maps_rf_prior", "maps_heuristic",
        "global_model_trained", "global_model_samples",
    )
    return {k: _progress.get(k, 0) for k in keys}


def is_running() -> bool:
    return _current_task is not None and not _current_task.done()


def is_paused() -> bool:
    return _paused


def pause_training():
    global _paused
    _paused = True


def resume_training():
    global _paused
    _paused = False


def cancel_training():
    global _current_task, _paused
    _paused = False
    if _current_task and not _current_task.done():
        _current_task.cancel()


async def run_nightly_training(triggered_by: str = "scheduler") -> dict:
    global _current_task, _progress
    start_ts = time.monotonic()
    logger.info(f"BSK ML training started (triggered_by={triggered_by})")
    _progress = {
        "status": "running", "triggered_by": triggered_by,
        "maps_updated": 0, "maps_skipped": 0, "rounds_used": 0,
        "maps_data_driven": 0, "maps_rf_prior": 0, "maps_heuristic": 0,
        "global_model_trained": 0, "global_model_samples": 0,
    }

    try:
        _current_task = asyncio.current_task()
        result = await asyncio.wait_for(
            _train(),
            timeout=HARD_DEADLINE_SECONDS,
        )
        elapsed = time.monotonic() - start_ts
        logger.info(f"BSK ML training finished in {elapsed:.0f}s: {result}")
    except asyncio.CancelledError:
        result = {"status": "cancelled", **_progress_snapshot()}
        logger.info("BSK ML training cancelled")
    except asyncio.TimeoutError:
        result = {"status": "timeout", **_progress_snapshot()}
        logger.warning("BSK ML training hit hard deadline, stopping")
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        logger.error(f"BSK ML training error: {e}", exc_info=True)
    finally:
        _current_task = None
        _progress = {}

    await _save_run(result, triggered_by)
    return result


async def _save_run(result: dict, triggered_by: str) -> None:
    try:
        from db.database import AsyncSessionFactory
        from db.models.bsk_ml_run import BskMlRun
        async with AsyncSessionFactory() as session:
            run = BskMlRun(
                ran_at=datetime.now(timezone.utc),
                rounds_used=result.get("rounds_used", 0),
                maps_updated=result.get("maps_updated", 0),
                maps_skipped=result.get("maps_skipped", 0),
                status=result.get("status", "ok"),
                triggered_by=triggered_by,
                notes=result.get("error"),
                predictions_total=result.get("predictions_total"),
                predictions_correct=result.get("predictions_correct"),
                prediction_accuracy=result.get("prediction_accuracy"),
                maps_data_driven=result.get("maps_data_driven"),
                maps_rf_prior=result.get("maps_rf_prior"),
                maps_heuristic=result.get("maps_heuristic"),
                global_model_trained=result.get("global_model_trained"),
                global_model_samples=result.get("global_model_samples"),
                oob_r2=result.get("oob_r2"),
                feature_importances=result.get("feature_importances"),
            )
            session.add(run)
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to save ML run history: {e}")


async def _train() -> dict:
    from db.database import AsyncSessionFactory
    from db.models.bsk_map_pool import BskMapPool
    from db.models.bsk_duel_round import BskDuelRound
    from db.models.bsk_rating import BskRating
    from sqlalchemy import select

    async with AsyncSessionFactory() as session:
        rounds = (await session.execute(
            select(BskDuelRound).where(
                BskDuelRound.status == "completed",
                BskDuelRound.winner_player.isnot(None),
            )
        )).scalars().all()

        if len(rounds) < MIN_ROUNDS_FOR_TRAINING:
            logger.info(f"Not enough data: {len(rounds)} rounds (need {MIN_ROUNDS_FOR_TRAINING})")
            return {
                "status": "skipped", "rounds_used": len(rounds),
                "maps_updated": 0, "maps_skipped": 0,
                "maps_data_driven": 0, "maps_rf_prior": 0, "maps_heuristic": 0,
                "global_model_trained": 0, "global_model_samples": 0,
            }

        ratings_raw = (await session.execute(
            select(BskRating).where(BskRating.mode == "ranked")
        )).scalars().all()
        ratings = {r.user_id: r for r in ratings_raw}
        maps_raw = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)
        )).scalars().all()
        maps = {m.beatmap_id: m for m in maps_raw}

    now_ts = datetime.now(timezone.utc)
    map_data: dict[int, list[dict]] = {}
    rounds_for_global: list[dict] = []   # round-level dataset for the new RF
    label_kind_counts = {"margin": 0, "binary": 0, "tie": 0}
    for rnd in rounds:
        if rnd.beatmap_id not in maps:
            continue

        # Use per-round before-snapshots if available (v2), else current ratings
        if rnd.p1_mu_aim_before is not None and rnd.p2_mu_aim_before is not None:
            mu1_aim   = rnd.p1_mu_aim_before
            mu1_speed = rnd.p1_mu_speed_before
            mu1_acc   = rnd.p1_mu_acc_before
            mu1_cons  = rnd.p1_mu_cons_before
            mu2_aim   = rnd.p2_mu_aim_before
            mu2_speed = rnd.p2_mu_speed_before
            mu2_acc   = rnd.p2_mu_acc_before
            mu2_cons  = rnd.p2_mu_cons_before
        else:
            r1 = ratings.get(rnd.player1_user_id)
            r2 = ratings.get(rnd.player2_user_id)
            if not r1 or not r2:
                continue
            mu1_aim, mu1_speed, mu1_acc, mu1_cons = r1.mu_aim, r1.mu_speed, r1.mu_acc, r1.mu_cons
            mu2_aim, mu2_speed, mu2_acc, mu2_cons = r2.mu_aim, r2.mu_speed, r2.mu_acc, r2.mu_cons

        target, kind = _round_target(rnd)
        label_kind_counts[kind] = label_kind_counts.get(kind, 0) + 1
        decay_w = _round_decay_weight(rnd.completed_at, now_ts)
        diffs = {
            "aim":   mu1_aim   - mu2_aim,
            "speed": mu1_speed - mu2_speed,
            "acc":   mu1_acc   - mu2_acc,
            "cons":  mu1_cons  - mu2_cons,
        }
        map_data.setdefault(rnd.beatmap_id, []).append({
            "actual":     target,                  # continuous margin in [0,1] or 0/1 fallback
            "label_kind": kind,
            "diff_aim":   diffs["aim"],
            "diff_speed": diffs["speed"],
            "diff_acc":   diffs["acc"],
            "diff_cons":  diffs["cons"],
            "weight":     decay_w,
        })
        # Round-level sample for the global outcome RF.
        rounds_for_global.append({
            "map_feats": _map_to_feature_vector(maps[rnd.beatmap_id]),
            "diffs":     diffs,
            "y":         target,
            "w":         decay_w,
        })

    if sum(label_kind_counts.values()) > 0:
        logger.info(
            f"BSK ML: round labels — margin={label_kind_counts.get('margin', 0)}, "
            f"binary={label_kind_counts.get('binary', 0)}, "
            f"tie={label_kind_counts.get('tie', 0)}"
        )

    updated = 0
    skipped = 0
    total_maps = len(map_data)

    from services.bsk.osu_parser import map_type_from_weights, weights_from_features

    # ── Phase 1: per-map correlation training ────────────────────────────────
    trained_map_weights: dict[int, dict] = {}   # beatmap_id → data-derived weights

    async with AsyncSessionFactory() as session:
        for i, (beatmap_id, entries) in enumerate(map_data.items()):
            while _paused:
                _progress["status"] = "paused"
                await asyncio.sleep(1)
            _progress.update({"status": "running", "maps_updated": updated,
                               "maps_skipped": skipped, "maps_total": total_maps, "maps_done": i})

            if len(entries) < MIN_ROUNDS_PER_MAP:
                skipped += 1
                continue

            data_weights = _estimate_weights_from_residuals(entries)
            if data_weights is None:
                skipped += 1
                continue

            trained_map_weights[beatmap_id] = {
                **data_weights,
                "_n": len(entries),
                "_w": sum(e.get("weight", 1.0) for e in entries),  # time-decayed total
            }

        await session.commit()

    # ── Phase 2: build global outcome-based RF on rounds ──────────────────
    # New (round-level) model: trained directly on per-round outcomes, not on
    # per-map aggregates. Generalizes from each round, gives weights even for
    # maps with zero rounds played, and re-uses the time-decayed sample weights.
    round_rf, rl_n, rl_diag, diff_scales = _build_round_level_model(rounds_for_global)
    _progress["global_model_trained"] = 1 if round_rf is not None else 0
    _progress["global_model_samples"] = rl_n

    # ── Phase 3: apply to all maps ───────────────────────────────────────────
    # Tiered prior:
    #   1. Per-map correlation from rounds played on THIS map (Phase 1). Most
    #      direct evidence; weighted by confidence ~ #rounds.
    #   2. Round-level RF + partial-dependence (Phase 2). Pools information
    #      across all rounds via shared map features — covers unplayed maps.
    #   3. Heuristic from features (weights_from_features). Last-resort prior
    #      when neither (1) nor (2) is available for this map.
    maps_data_driven = 0
    maps_rf_prior    = 0
    maps_heuristic   = 0
    async with AsyncSessionFactory() as session:
        for beatmap_id, map_entry in maps.items():
            if beatmap_id in trained_map_weights:
                data_w = trained_map_weights[beatmap_id]
                n_rounds = data_w["_n"]
                confidence = 1.0 - 1.0 / (1.0 + n_rounds / 5.0)
            else:
                data_w = None
                confidence = 0.0

            # Tier 2/3 prior. Try the round-level RF first via PD; if it can't
            # extract a positive sensitivity, fall back to the feature heuristic.
            pd_w = None
            if round_rf is not None:
                pd_w = _pd_weights_for_map(
                    round_rf, _map_to_feature_vector(map_entry), diff_scales,
                )
            if pd_w is not None:
                feat_prior = pd_w
                feat_source = "rf"
            else:
                feat_prior = _feature_prior(map_entry, None, weights_from_features)
                feat_source = "heuristic"

            if data_w and confidence > 0:
                blended = {
                    c: confidence * data_w[c] + (1 - confidence) * feat_prior[c]
                    for c in ("aim", "speed", "acc", "cons")
                }
            else:
                blended = feat_prior

            db_entry = (await session.execute(
                select(BskMapPool).where(BskMapPool.beatmap_id == beatmap_id)
            )).scalar_one_or_none()
            if not db_entry:
                continue

            db_entry.w_aim   = round(blended["aim"],   3)
            db_entry.w_speed = round(blended["speed"], 3)
            db_entry.w_acc   = round(blended["acc"],   3)
            db_entry.w_cons  = round(blended["cons"],  3)
            db_entry.map_type = map_type_from_weights(blended)

            if beatmap_id in trained_map_weights:
                maps_data_driven += 1
                updated += 1
            elif feat_source == "rf":
                maps_rf_prior += 1
            else:
                maps_heuristic += 1

        await session.commit()

    # Diagnostics now come straight from the round-level RF (single forest).
    oob_r2_mean = rl_diag.get("oob_r2") if round_rf is not None else None

    feature_imp_payload = None
    fi = rl_diag.get("feature_importances") or []
    if fi:
        # Round-level X has 33 features: 29 map features + 4 diff_* axes.
        names = list(_FEATURE_NAMES) + [f"diff_{c}" for c in _ROUND_DIFF_COMPS]
        import json as _json
        named = sorted(zip(names, fi), key=lambda kv: kv[1], reverse=True)[:8]
        feature_imp_payload = _json.dumps(
            {"top": [{"name": n, "imp": round(v, 4)} for n, v in named]}
        )

    return {
        "status": "ok",
        "rounds_used":      len(rounds),
        "maps_updated":     updated,           # legacy = data-driven count
        "maps_skipped":     skipped,
        "maps_data_driven": maps_data_driven,
        "maps_rf_prior":    maps_rf_prior,
        "maps_heuristic":   maps_heuristic,
        "global_model_trained": 1 if round_rf is not None else 0,
        "global_model_samples": rl_n,
        "oob_r2":              oob_r2_mean,
        "feature_importances": feature_imp_payload,
        **_compute_prediction_accuracy(rounds),
    }


def _compute_prediction_accuracy(rounds) -> dict:
    """Count how many ml_predicted_winner matched actual winner_player."""
    total = correct = 0
    for rnd in rounds:
        if rnd.ml_predicted_winner is None or rnd.winner_player is None:
            continue
        total += 1
        if rnd.ml_predicted_winner == rnd.winner_player:
            correct += 1
    if total == 0:
        return {"predictions_total": 0, "predictions_correct": 0, "prediction_accuracy": None}
    return {
        "predictions_total": total,
        "predictions_correct": correct,
        "prediction_accuracy": round(correct / total, 4),
    }


def _estimate_weights_from_residuals(entries: list[dict]) -> dict | None:
    """Weighted Pearson correlation per skill component → simplex weights.

    Each entry carries a `weight` (time-decay sample weight); `actual` is
    a continuous *win margin* in [0, 1] (0.5 + 0.5·composite_diff) on
    rounds with full submissions, falling back to 0/1 on forfeits. The
    continuous label gives ~10× more usable signal per round than a pure
    binary outcome — a 90/10 round and a 51/49 round used to look the same.
    """
    import math
    components = ["aim", "speed", "acc", "cons"]
    weights = [e.get("weight", 1.0) for e in entries]
    wsum = sum(weights)
    if wsum <= 0:
        return None

    actual_vals = [e["actual"] for e in entries]
    mean_actual = sum(w * a for w, a in zip(weights, actual_vals)) / wsum

    correlations = {}
    for comp in components:
        diffs = [e[f"diff_{comp}"] for e in entries]
        mean_diff = sum(w * d for w, d in zip(weights, diffs)) / wsum
        num   = sum(w * (d - mean_diff) * (a - mean_actual)
                    for w, d, a in zip(weights, diffs, actual_vals))
        den_d = math.sqrt(sum(w * (d - mean_diff) ** 2 for w, d in zip(weights, diffs)) + 1e-9)
        den_a = math.sqrt(sum(w * (a - mean_actual) ** 2 for w, a in zip(weights, actual_vals)) + 1e-9)
        correlations[comp] = abs(num / (den_d * den_a))

    total = sum(correlations.values())
    if total < 1e-9:
        return None
    return {k: round(v / total, 3) for k, v in correlations.items()}


# ─── Global feature→weight model (pure Python Ridge regression) ───────────────

def _map_to_feature_vector(map_entry) -> list[float]:
    """
    Build a normalised feature vector from BskMapPool fields.
    Order MUST be consistent between training and prediction.

    Phase-2 extension: includes new acc-targeted features
    (subdiv_entropy, polyrhythm, jack, off-beat, slider tail, od_demand,
    flow_break, bpm_rel_speed, intensity_floor, pattern_repeat).
    """
    def _f(v, default=0.5): return float(v) if v is not None else default

    # osu! API attributes (absolute aim/speed difficulties, normalised to 0..1)
    api_aim   = min(_f(map_entry.api_aim_diff,   0.0) / 8.0, 1.0)
    api_speed = min(_f(map_entry.api_speed_diff, 0.0) / 8.0, 1.0)
    api_sf    = _f(map_entry.api_slider_factor,  1.0)

    # Aim signals
    f_jd      = _f(map_entry.f_jump_density,    0.0)
    f_jv      = _f(map_entry.f_jump_vel,        0.0)
    f_bf      = _f(map_entry.f_back_forth,      0.0)
    f_angle   = _f(map_entry.f_angle_var,       0.0)
    f_flow    = _f(map_entry.f_flow_break,      0.0)

    # Speed signals
    f_burst   = _f(map_entry.f_burst,           0.0)
    f_stream  = _f(map_entry.f_stream,          0.0)
    f_death   = _f(map_entry.f_death_stream,    0.0)
    f_bpm_rel = _f(map_entry.f_bpm_rel_speed,   0.0)

    # Acc signals
    f_subdiv  = _f(map_entry.f_subdiv_entropy,     0.0)
    f_poly    = _f(map_entry.f_polyrhythm_density, 0.0)
    f_offbeat = _f(map_entry.f_off_beat_ratio,     0.0)
    f_jack    = _f(map_entry.f_jack_density,       0.0)
    f_stail   = _f(map_entry.f_slider_tail_demand, 0.0)
    f_oddem   = _f(map_entry.f_od_demand,          0.0)
    f_sv      = _f(map_entry.f_sv_var,             0.0)
    f_sld     = _f(map_entry.f_slider_density,     0.0)

    # Cons signals
    f_dv      = _f(map_entry.f_density_var,    0.0)
    f_floor   = _f(map_entry.f_intensity_floor, 0.0)
    f_repeat  = _f(map_entry.f_pattern_repeat,  0.0)

    # General
    f_rc      = _f(map_entry.f_rhythm_complexity, 0.0)

    # Metadata
    bpm_n = min(_f(map_entry.bpm,    120.0) / 240.0, 1.0)
    ar_n  = min(_f(map_entry.ar,     8.0)   / 11.0,  1.0)
    od_n  = min(_f(map_entry.od,     8.0)   / 11.0,  1.0)
    len_n = min(_f(map_entry.length, 120.0) / 300.0, 1.0)

    nc  = float(map_entry.f_note_count or 0)
    dur = float(map_entry.f_duration or map_entry.length or 1)
    nps = min((nc / max(dur, 1)) / 8.0, 1.0) if nc > 0 else 0.0

    return [
        api_aim, api_speed, api_sf,
        # aim
        f_jd, f_jv, f_bf, f_angle, f_flow,
        # speed
        f_burst, f_stream, f_death, f_bpm_rel,
        # acc
        f_subdiv, f_poly, f_offbeat, f_jack, f_stail, f_oddem, f_sv, f_sld,
        # cons
        f_dv, f_floor, f_repeat,
        # general
        f_rc,
        # metadata
        bpm_n, ar_n, od_n, len_n, nps,
        1.0,  # bias term
    ]


class _DecisionTree:
    """Minimal CART regression tree (pure Python, no deps).

    Supports per-sample weights w_i: leaves return weighted means, splits
    minimize weighted SSE = Σ w_i (y_i − ȳ_w)² = Σ w_i y_i² − (Σ w_i y_i)² / Σ w_i.
    Tracks per-feature importance as the total weighted SSE reduction
    contributed by splits on each feature, summed across the tree.
    """

    def __init__(self, max_depth: int = 6, min_samples_leaf: int = 3,
                 max_features: int | None = None, rng_seed: int = 0):
        self.max_depth = max_depth
        self.min_leaf = min_samples_leaf
        self.max_features = max_features
        self._rng_state = rng_seed
        self._tree: dict | None = None
        self.feature_importances_: list[float] = []   # filled after fit

    def _rng_next(self) -> int:
        self._rng_state = (self._rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        return self._rng_state

    def _sample_features(self, p: int) -> list[int]:
        k = self.max_features or p
        indices = list(range(p))
        for i in range(min(k, p)):
            j = i + (self._rng_next() % (p - i))
            indices[i], indices[j] = indices[j], indices[i]
        return indices[:k]

    def fit(self, X: list[list[float]], y: list[float],
            sample_weight: list[float] | None = None) -> None:
        n = len(X)
        if sample_weight is None:
            sample_weight = [1.0] * n
        p = len(X[0]) if X else 0
        self.feature_importances_ = [0.0] * p
        self._tree = self._build(X, y, sample_weight, list(range(n)), 0)

    @staticmethod
    def _weighted_stats(idx: list[int], y: list[float], w: list[float]) -> tuple[float, float]:
        """Return (Σw, weighted_mean) for the given index set."""
        wsum = sum(w[i] for i in idx)
        if wsum <= 0:
            return 0.0, 0.0
        wm = sum(w[i] * y[i] for i in idx) / wsum
        return wsum, wm

    def _build(self, X: list[list[float]], y: list[float],
               w: list[float], idx: list[int], depth: int) -> dict:
        wsum, mean_val = self._weighted_stats(idx, y, w)
        if wsum <= 0:
            return {"leaf": True, "val": 0.0}

        # Weighted SSE = Σ w_i (y_i − ȳ)² = Σ w_i y_i² − (Σ w_i y_i)²/Σw
        wy_sum = sum(w[i] * y[i] for i in idx)
        wy2_sum = sum(w[i] * y[i] * y[i] for i in idx)
        parent_sse = wy2_sum - (wy_sum * wy_sum) / wsum

        if depth >= self.max_depth or len(idx) <= self.min_leaf or parent_sse < 1e-12:
            return {"leaf": True, "val": mean_val}

        p = len(X[0])
        feat_subset = self._sample_features(p)

        best_score = float("inf")
        best_feat = -1
        best_thr = 0.0
        best_left: list[int] = []
        best_right: list[int] = []
        best_reduction = 0.0

        for fi in feat_subset:
            sorted_idx = sorted(idx, key=lambda i: X[i][fi])
            left_w = 0.0
            left_wy = 0.0
            left_wy2 = 0.0
            right_w = wsum
            right_wy = wy_sum
            right_wy2 = wy2_sum
            n_total = len(sorted_idx)

            for k in range(self.min_leaf - 1, n_total - self.min_leaf):
                i = sorted_idx[k]
                wi = w[i]
                yi = y[i]
                left_w   += wi
                left_wy  += wi * yi
                left_wy2 += wi * yi * yi
                right_w  -= wi
                right_wy -= wi * yi
                right_wy2 -= wi * yi * yi
                nl = k + 1
                nr = n_total - nl

                if nl < self.min_leaf or nr < self.min_leaf:
                    continue
                if left_w <= 0 or right_w <= 0:
                    continue
                if X[sorted_idx[k]][fi] == X[sorted_idx[k + 1]][fi]:
                    continue

                sse_l = left_wy2  - (left_wy  * left_wy ) / left_w
                sse_r = right_wy2 - (right_wy * right_wy) / right_w
                score = sse_l + sse_r

                if score < best_score:
                    best_score = score
                    best_feat = fi
                    best_thr = (X[sorted_idx[k]][fi] + X[sorted_idx[k + 1]][fi]) / 2.0
                    best_left = sorted_idx[:k + 1]
                    best_right = sorted_idx[k + 1:]
                    best_reduction = parent_sse - score

        if best_feat < 0 or not best_left or not best_right:
            return {"leaf": True, "val": mean_val}

        # Track importance: weighted SSE reduction summed per feature.
        if best_reduction > 0:
            self.feature_importances_[best_feat] += best_reduction

        return {
            "leaf": False,
            "feat": best_feat,
            "thr": best_thr,
            "left":  self._build(X, y, w, best_left,  depth + 1),
            "right": self._build(X, y, w, best_right, depth + 1),
        }

    def predict(self, x: list[float]) -> float:
        node = self._tree
        while node and not node["leaf"]:
            if x[node["feat"]] <= node["thr"]:
                node = node["left"]
            else:
                node = node["right"]
        return node["val"] if node else 0.0


class _RandomForest:
    """Bagged ensemble of _DecisionTree regressors with OOB scoring.

    After fit() exposes:
      - feature_importances_  (list[float], normalized to sum to 1)
      - oob_r2_               (float | None — out-of-bag R²; None if no OOB samples)
    """

    def __init__(self, n_trees: int = 30, max_depth: int = 6,
                 min_samples_leaf: int = 3, max_features: int | None = None):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.min_leaf = min_samples_leaf
        self.max_features = max_features
        self.trees: list[_DecisionTree] = []
        self._oob_masks: list[list[int]] = []   # per-tree OOB indices into X
        self.feature_importances_: list[float] = []
        self.oob_r2_: float | None = None

    def fit(self, X: list[list[float]], y: list[float],
            sample_weight: list[float] | None = None) -> None:
        import math
        n = len(X)
        if n == 0:
            return
        if sample_weight is None:
            sample_weight = [1.0] * n
        p = len(X[0])
        mf = self.max_features or max(1, int(math.sqrt(p)))

        self.trees = []
        self._oob_masks = []
        agg_importance = [0.0] * p

        for t in range(self.n_trees):
            tree = _DecisionTree(
                max_depth=self.max_depth,
                min_samples_leaf=self.min_leaf,
                max_features=mf,
                rng_seed=t * 7919 + 42,
            )
            # Bootstrap with replacement; track which originals were left out.
            boot_idx = [tree._rng_next() % n for _ in range(n)]
            in_bag = set(boot_idx)
            oob_idx = [i for i in range(n) if i not in in_bag]

            bX = [X[i] for i in boot_idx]
            by = [y[i] for i in boot_idx]
            bw = [sample_weight[i] for i in boot_idx]
            tree.fit(bX, by, sample_weight=bw)

            self.trees.append(tree)
            self._oob_masks.append(oob_idx)

            for fi, contrib in enumerate(tree.feature_importances_):
                agg_importance[fi] += contrib

        total = sum(agg_importance)
        self.feature_importances_ = (
            [v / total for v in agg_importance] if total > 0 else [0.0] * p
        )
        self.oob_r2_ = self._compute_oob_r2(X, y, sample_weight)

    def _compute_oob_r2(self, X: list[list[float]], y: list[float],
                        sample_weight: list[float]) -> float | None:
        """OOB R² = 1 - Σwᵢ(yᵢ − ŷ_oob,i)² / Σwᵢ(yᵢ − ȳ_w)²; only over samples
        that were OOB for ≥1 tree (predicted by averaging only those trees)."""
        n = len(X)
        oob_pred_sum = [0.0] * n
        oob_count = [0] * n
        for tree, oob_idx in zip(self.trees, self._oob_masks):
            for i in oob_idx:
                oob_pred_sum[i] += tree.predict(X[i])
                oob_count[i] += 1
        usable = [i for i in range(n) if oob_count[i] > 0]
        if len(usable) < 2:
            return None
        wsum = sum(sample_weight[i] for i in usable)
        if wsum <= 0:
            return None
        ymean = sum(sample_weight[i] * y[i] for i in usable) / wsum
        ss_res = 0.0
        ss_tot = 0.0
        for i in usable:
            yhat = oob_pred_sum[i] / oob_count[i]
            ss_res += sample_weight[i] * (y[i] - yhat) ** 2
            ss_tot += sample_weight[i] * (y[i] - ymean) ** 2
        if ss_tot < 1e-12:
            return None
        return 1.0 - ss_res / ss_tot

    def predict(self, x: list[float]) -> float:
        if not self.trees:
            return 0.0
        return sum(t.predict(x) for t in self.trees) / len(self.trees)


# Names parallel to _map_to_feature_vector output (30 entries incl. bias).
_FEATURE_NAMES: list[str] = [
    "api_aim", "api_speed", "api_slider_factor",
    "f_jump_density", "f_jump_vel", "f_back_forth", "f_angle_var", "f_flow_break",
    "f_burst", "f_stream", "f_death_stream", "f_bpm_rel_speed",
    "f_subdiv_entropy", "f_polyrhythm", "f_off_beat", "f_jack",
    "f_slider_tail", "f_od_demand", "f_sv_var", "f_slider_density",
    "f_density_var", "f_intensity_floor", "f_pattern_repeat",
    "f_rhythm_complexity",
    "bpm", "ar", "od", "length", "nps",
    "bias",
]
# Indexes into the round-level feature vector: 29 map features + 4 diff features.
_ROUND_DIFF_OFFSET = len(_FEATURE_NAMES)        # diffs start at index 29
_ROUND_DIFF_COMPS  = ("aim", "speed", "acc", "cons")


# ─────────── Round-level RF: outcome-based forest with PD weight extraction ────

def _build_round_level_model(
    rounds_data: list[dict],   # each = {map_feats: list[float], diffs: dict, y: float, w: float}
) -> tuple["_RandomForest | None", int, dict, tuple[float, float, float, float]]:
    """
    Train a single Random Forest on per-round outcomes:
        X = [map_features..., diff_aim, diff_speed, diff_acc, diff_cons]
        y = win-margin in [0, 1]      (continuous, falls back to 0/1 on forfeit)
        w = time-decay sample weight

    Returns (forest_or_None, n_samples, diagnostics, diff_scales) where
    diff_scales = (s_aim, s_speed, s_acc, s_cons) is a (p75 − p25) interquartile
    range per component, used for partial-dependence weight extraction. The forest
    learns to predict round outcomes from BOTH map features and skill diffs, so
    every round contributes a usable sample (no MIN_ROUNDS_PER_MAP filter).
    """
    MIN_ROUND_SAMPLES = 30   # fewer rounds → don't pretend to have a model

    if len(rounds_data) < MIN_ROUND_SAMPLES:
        return None, len(rounds_data), {"oob_r2": None, "feature_importances": []}, (0, 0, 0, 0)

    X: list[list[float]] = []
    Y: list[float] = []
    W: list[float] = []
    diffs_by_comp: dict[str, list[float]] = {c: [] for c in _ROUND_DIFF_COMPS}

    for r in rounds_data:
        feats = list(r["map_feats"])
        for c in _ROUND_DIFF_COMPS:
            d = float(r["diffs"][c])
            feats.append(d)
            diffs_by_comp[c].append(d)
        X.append(feats)
        Y.append(float(r["y"]))
        W.append(float(r["w"]))

    # Per-component IQR (p75 − p25) — natural scale for the PD probe; fall back
    # to a sensible default if the dataset is too narrow on a given axis.
    def _quantile(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        k = (len(s) - 1) * q
        lo = int(k)
        hi = min(lo + 1, len(s) - 1)
        frac = k - lo
        return s[lo] * (1 - frac) + s[hi] * frac

    diff_scales: list[float] = []
    for c in _ROUND_DIFF_COMPS:
        xs = diffs_by_comp[c]
        iqr = _quantile(xs, 0.75) - _quantile(xs, 0.25)
        diff_scales.append(max(iqr, 50.0))   # 50 mu units = sensible floor

    rf = _RandomForest(n_trees=50, max_depth=7, min_samples_leaf=3)
    rf.fit(X, Y, sample_weight=W)

    diag = {
        "oob_r2": rf.oob_r2_,
        "feature_importances": list(rf.feature_importances_),
    }
    logger.info(
        f"BSK ML round-level RF: n={len(X)}, "
        f"OOB R²={'%.3f' % rf.oob_r2_ if rf.oob_r2_ is not None else '—'}, "
        f"diff_scales={[round(s, 1) for s in diff_scales]}"
    )
    return rf, len(X), diag, tuple(diff_scales)


def _pd_weights_for_map(
    forest: "_RandomForest",
    map_feats: list[float],
    diff_scales: tuple[float, float, float, float],
) -> dict[str, float] | None:
    """Partial-dependence weight extraction.

    For each component c, predict outcomes at (map, diff_c=+s/2, others=0) and
    (map, diff_c=-s/2, others=0); the difference is the forest's *sensitivity*
    to that skill axis on this specific map. Negative or zero sensitivities are
    clamped to 0; the rest is normalized to a simplex.

    Returns None if all sensitivities clamp to zero (forest learned no signal
    for this map type — caller falls back to the heuristic).
    """
    n_map = _ROUND_DIFF_OFFSET
    sensitivities: list[float] = []
    for ci, c in enumerate(_ROUND_DIFF_COMPS):
        s = diff_scales[ci]
        x_pos = list(map_feats) + [0.0] * 4
        x_neg = list(map_feats) + [0.0] * 4
        x_pos[n_map + ci] = +s / 2.0
        x_neg[n_map + ci] = -s / 2.0
        sens = forest.predict(x_pos) - forest.predict(x_neg)
        sensitivities.append(max(0.0, sens))

    total = sum(sensitivities)
    if total <= 1e-9:
        return None
    return {
        c: round(sensitivities[i] / total, 3)
        for i, c in enumerate(_ROUND_DIFF_COMPS)
    }


def _feature_prior(
    map_entry,
    global_model,           # kept positional for back-compat; ignored
    weights_from_features_fn,
) -> dict:
    """
    Heuristic feature-based weight prior. Used as a last-resort fallback when
    the round-level RF can't extract sensitivities for this map (cold-start
    runs with no model). Output sums to 1.0.
    """
    # Reconstruct features dict from stored columns
    features = {
        # aim
        "jump_density":          map_entry.f_jump_density or 0.0,
        "avg_jump_velocity":     map_entry.f_jump_vel or 0.0,
        "back_forth_ratio":      map_entry.f_back_forth or 0.0,
        "angle_variance":        map_entry.f_angle_var or 0.0,
        "flow_break_density":    map_entry.f_flow_break or 0.0,
        # speed
        "burst_density":         map_entry.f_burst or 0.0,
        "full_stream_density":   map_entry.f_stream or 0.0,
        "death_stream_density":  map_entry.f_death_stream or 0.0,
        "bpm_rel_speed":         map_entry.f_bpm_rel_speed or 0.0,
        # acc
        "subdiv_entropy":        map_entry.f_subdiv_entropy or 0.0,
        "polyrhythm_density":    map_entry.f_polyrhythm_density or 0.0,
        "off_beat_ratio":        map_entry.f_off_beat_ratio or 0.0,
        "jack_density":          map_entry.f_jack_density or 0.0,
        "slider_tail_demand":    map_entry.f_slider_tail_demand or 0.0,
        "sv_variance":           map_entry.f_sv_var or 0.0,
        "slider_density":        map_entry.f_slider_density or 0.0,
        # cons
        "density_variance":      map_entry.f_density_var or 0.0,
        "intensity_floor":       map_entry.f_intensity_floor or 0.0,
        "pattern_repetition":    map_entry.f_pattern_repeat or 0.0,
        # general
        "rhythm_complexity":     map_entry.f_rhythm_complexity or 0.0,
        "note_count":            map_entry.f_note_count or 0,
        "duration_seconds":      map_entry.f_duration or map_entry.length or 0,
    }

    return weights_from_features_fn(
        features,
        bpm=map_entry.bpm or 0,
        ar=map_entry.ar   or 0,
        od=map_entry.od   or 0,
        api_aim=map_entry.api_aim_diff       or 0.0,
        api_speed=map_entry.api_speed_diff   or 0.0,
        api_slider_factor=map_entry.api_slider_factor if map_entry.api_slider_factor is not None else 1.0,
    )
