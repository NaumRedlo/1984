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

# Minimum OOB R² before we let the round-level RF rewrite weights for the whole
# pool. Below this the forest hasn't beaten the mean by enough to trust its PD
# probes — μ_aim/μ_speed/μ_acc/μ_cons are highly collinear (a strong player is
# strong on all four), so on small datasets CART splits land on whichever axis
# wins the bootstrap lottery and the others get importance ≈ 0. That's how
# `w_aim = 0` ended up on all 5603 maps after the 2026-05-01 run (n=108,
# OOB R²=0.216). Gate keeps the heuristic prior in charge until the forest is
# actually informative.
MIN_RF_OOB_R2 = 0.30

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
        # Round-level sample for the global outcome RF. We pass map_entry along
        # so _build_round_level_model can compute map×share interaction terms.
        rounds_for_global.append({
            "map_feats": _map_to_feature_vector(maps[rnd.beatmap_id]),
            "map_entry": maps[rnd.beatmap_id],
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
    round_rf, rl_n, rl_diag, share_scales = _build_round_level_model(rounds_for_global)

    # Quality gate: a weak forest (low OOB R² or no OOB at all) is worse than
    # the heuristic, because PD probes on collinear μ-diffs zero out whichever
    # axis lost the bootstrap lottery and rewrite the whole pool with that.
    rf_oob = rl_diag.get("oob_r2") if round_rf is not None else None
    if round_rf is not None and (rf_oob is None or rf_oob < MIN_RF_OOB_R2):
        logger.info(
            f"BSK ML: discarding round-level RF (OOB R²={rf_oob}, "
            f"need ≥{MIN_RF_OOB_R2}); falling back to heuristic prior for all maps"
        )
        round_rf = None

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
                    round_rf, _map_to_feature_vector(map_entry), map_entry, share_scales,
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
        # Round-level X layout: map_feats (N_MAP) + diff_total + share_aim/speed/acc + 4 interactions.
        names = list(_FEATURE_NAMES) + list(_ROUND_EXTRA_NAMES)
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
_ROUND_DIFF_COMPS  = ("aim", "speed", "acc", "cons")
# Map-feature signals paired with each share component for the explicit
# interaction terms — the forest can't discover these on n≈100 by itself
# (too few samples for a 2-deep split to separate signal from noise).
_INTERACTION_MAP_FEATURES = {
    "aim":   "f_jump_density",
    "speed": "f_stream",
    "acc":   "f_subdiv_entropy",
    "cons":  "f_density_var",
}
# Round-level X layout (computed by _build_round_features):
#   [0 : N_MAP)                    = map_feats      (29 incl. bias)
#   [N_MAP]                        = diff_total     (mean of 4 μ-diffs — overall edge)
#   [N_MAP+1 : N_MAP+4)            = share_aim/speed/acc  (cons is implied: shares sum to 0)
#   [N_MAP+4 : N_MAP+8)            = interactions: map_signal · share_c, one per component
# Interaction terms keep all four shares (incl. cons) so each axis has its own
# direct map-conditional sensitivity probe in PD.
_ROUND_DIFF_OFFSET = len(_FEATURE_NAMES)        # diff_total index = N_MAP
_ROUND_SHARE_OFFSET = _ROUND_DIFF_OFFSET + 1    # share_aim index  = N_MAP + 1
_ROUND_INTER_OFFSET = _ROUND_SHARE_OFFSET + 3   # interactions     = N_MAP + 4
_ROUND_FEATURE_COUNT = _ROUND_INTER_OFFSET + 4
_ROUND_EXTRA_NAMES = (
    ["diff_total", "share_aim", "share_speed", "share_acc"]
    + [f"inter_{c}" for c in _ROUND_DIFF_COMPS]
)


def _diffs_to_share(diffs: dict) -> tuple[float, dict]:
    """Decorrelate four μ-diffs → (overall edge, per-axis share above mean).

    diff_total = mean(diff_c)              ← raw skill gap (correlated axis)
    share_c    = diff_c − diff_total       ← *relative* edge in axis c

    Shares sum to zero by construction, so cons is implied by aim+speed+acc
    and we drop it from the feature vector to break the linear dependency
    that confused CART splits on raw diffs.
    """
    total = sum(diffs[c] for c in _ROUND_DIFF_COMPS) / 4.0
    return total, {c: diffs[c] - total for c in _ROUND_DIFF_COMPS}


def _build_round_features(
    map_feats: list[float],
    map_entry,
    total: float,
    share: dict,
) -> list[float]:
    """Assemble the round-level X row: [map_feats, total, share_aim/speed/acc, interactions]."""
    inter = []
    for c in _ROUND_DIFF_COMPS:
        attr = _INTERACTION_MAP_FEATURES[c]
        sig = float(getattr(map_entry, attr) or 0.0) if map_entry is not None else 0.0
        inter.append(sig * share[c])
    return list(map_feats) + [
        total,
        share["aim"], share["speed"], share["acc"],
        *inter,
    ]


# ─────────── Round-level RF: outcome-based forest with PD weight extraction ────

def _build_round_level_model(
    rounds_data: list[dict],   # each = {map_feats, map_entry, diffs, y, w}
) -> tuple["_RandomForest | None", int, dict, tuple[float, float, float, float]]:
    """
    Train a single Random Forest on per-round outcomes.

    Three modernizations vs. the raw-diff baseline (all important on n≈100):

    A. Mirror augmentation. Every round R is duplicated as R' with
         diffs ↦ −diffs, y ↦ 1 − y.
       Removes the side-bias data leak (player1 wins 88/108 raw) — the forest
       can no longer key on "p1 usually stronger" because every sample has its
       opposite. Doubles N for free.

    B. Decorrelation. Raw μ-diffs are highly collinear (a strong player is
       strong on all four axes; |corr| ≈ 0.9 in production). We replace them
       with `(diff_total, share_aim, share_speed, share_acc)` where
         diff_total = mean(diffs)              ← overall edge
         share_c    = diff_c − diff_total      ← *relative* edge in axis c
       Shares sum to zero, so cons is implied and dropped (breaks the linear
       dependency that randomized CART splits across collinear axes).

    C. Explicit map×share interactions. RF on n≈100 can't reliably discover a
       depth-2 split structure, so we hand-feed four interaction features:
         f_jump_density · share_aim,   f_stream · share_speed,
         f_subdiv_entropy · share_acc, f_density_var · share_cons.
       The forest now sees "extra aim helps on jump-heavy maps" as a single
       linear feature instead of needing two-level splits to find it.

    Returns (forest_or_None, n_samples, diagnostics, share_scales) where
    share_scales = IQR per share component, used by PD probing.
    """
    # 33-feature forest needs ≳10 samples per feature before splits stop
    # tracking noise. Was 30, but with that few rounds OOB R² lands at ~0.2
    # and PD probes on collinear μ-diffs collapse one axis to zero (see
    # MIN_RF_OOB_R2 docstring). Bumped to 300.
    # Mirror augmentation doubles the count, so the gate is on raw rounds.
    MIN_ROUND_SAMPLES = 300

    if len(rounds_data) < MIN_ROUND_SAMPLES:
        return None, len(rounds_data), {"oob_r2": None, "feature_importances": []}, (0, 0, 0, 0)

    X: list[list[float]] = []
    Y: list[float] = []
    W: list[float] = []
    shares_by_comp: dict[str, list[float]] = {c: [] for c in _ROUND_DIFF_COMPS}

    for r in rounds_data:
        diffs = r["diffs"]
        map_entry = r.get("map_entry")
        # (A) original
        total, share = _diffs_to_share(diffs)
        X.append(_build_round_features(r["map_feats"], map_entry, total, share))
        Y.append(float(r["y"]))
        W.append(float(r["w"]))
        for c in _ROUND_DIFF_COMPS:
            shares_by_comp[c].append(share[c])
        # (A) mirrored
        mdiffs = {c: -diffs[c] for c in _ROUND_DIFF_COMPS}
        mtotal, mshare = _diffs_to_share(mdiffs)
        X.append(_build_round_features(r["map_feats"], map_entry, mtotal, mshare))
        Y.append(1.0 - float(r["y"]))
        W.append(float(r["w"]))
        for c in _ROUND_DIFF_COMPS:
            shares_by_comp[c].append(mshare[c])

    # Per-component IQR over the *share* distribution — natural scale for the
    # PD probe. Falls back to a sensible floor if data is degenerate.
    def _quantile(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        k = (len(s) - 1) * q
        lo = int(k)
        hi = min(lo + 1, len(s) - 1)
        frac = k - lo
        return s[lo] * (1 - frac) + s[hi] * frac

    share_scales: list[float] = []
    for c in _ROUND_DIFF_COMPS:
        xs = shares_by_comp[c]
        iqr = _quantile(xs, 0.75) - _quantile(xs, 0.25)
        share_scales.append(max(iqr, 25.0))   # 25 mu = floor on relative edge

    rf = _RandomForest(n_trees=50, max_depth=7, min_samples_leaf=3)
    rf.fit(X, Y, sample_weight=W)

    diag = {
        "oob_r2": rf.oob_r2_,
        "feature_importances": list(rf.feature_importances_),
    }
    logger.info(
        f"BSK ML round-level RF: n={len(X)} (incl. mirror), "
        f"OOB R²={'%.3f' % rf.oob_r2_ if rf.oob_r2_ is not None else '—'}, "
        f"share_scales={[round(s, 1) for s in share_scales]}"
    )
    return rf, len(X), diag, tuple(share_scales)


def _pd_weights_for_map(
    forest: "_RandomForest",
    map_feats: list[float],
    map_entry,
    share_scales: tuple[float, float, float, float],
) -> dict[str, float] | None:
    """Partial-dependence weight extraction in *share* space.

    For each axis c we shift `share_c` by ±s/2 (other shares zero, diff_total
    zero), rebuild the row including interaction terms, and read the forest's
    prediction delta. That delta is "how much extra a +IQR/2 *relative* edge
    in c contributes to this specific map's outcome" — the very definition of
    the per-axis weight we want.

    Probing in share space (not raw diffs) is the whole point of decorrelation:
    every probe point is now near the data manifold instead of being an OOD
    "huge aim, zero everything else" combo the forest never trained on.

    Returns None if all sensitivities clamp to zero. Each axis is floored at
    1% of the peak to prevent a single noise-collapsed axis from being nuked.
    """
    raw_sens: list[float] = []
    for ci, c in enumerate(_ROUND_DIFF_COMPS):
        s = share_scales[ci]
        share_pos = {k: 0.0 for k in _ROUND_DIFF_COMPS}
        share_neg = dict(share_pos)
        share_pos[c] = +s / 2.0
        share_neg[c] = -s / 2.0
        x_pos = _build_round_features(map_feats, map_entry, 0.0, share_pos)
        x_neg = _build_round_features(map_feats, map_entry, 0.0, share_neg)
        raw_sens.append(forest.predict(x_pos) - forest.predict(x_neg))

    clamped = [max(0.0, s) for s in raw_sens]
    peak = max(clamped)
    if peak <= 1e-9:
        return None

    # Floor each axis at 1% of the peak so a single losing-the-bootstrap-lottery
    # axis can't get nuked to zero and pull the simplex onto the other three —
    # that's what produced w_aim ≡ 0 across the entire pool on 2026-05-01.
    floor = peak * 0.01
    sensitivities = [max(s, floor) for s in clamped]

    total = sum(sensitivities)
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
