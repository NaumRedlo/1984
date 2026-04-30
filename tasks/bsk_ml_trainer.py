"""
Nightly ML training job for BSK map skill weights.

Schedule: runs at 02:00, stops by 05:00 (hard deadline).
Reads match history from bsk_duel_rounds + bsk_ratings, trains a per-skill
random forest (pure-Python `_RandomForest` with bagged decision trees),
and updates w_aim/w_speed/w_acc/w_cons in bsk_map_pool. There is also a
legacy ridge-regression path used as a fallback for the global model when
forest training is skipped.  Run history is persisted to bsk_ml_runs.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger

logger = get_logger("tasks.bsk_ml_trainer")

HARD_DEADLINE_SECONDS = 3 * 3600  # 3 hours max
MIN_ROUNDS_FOR_TRAINING = 50
MIN_ROUNDS_PER_MAP = 3

# Global state for monitoring
_current_task: Optional[asyncio.Task] = None
_paused = False
_progress: dict = {}


def get_progress() -> dict:
    """Return current training progress snapshot."""
    return dict(_progress)


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
    _progress = {"status": "running", "triggered_by": triggered_by, "maps_updated": 0, "maps_skipped": 0, "rounds_used": 0}

    try:
        _current_task = asyncio.current_task()
        result = await asyncio.wait_for(
            _train(),
            timeout=HARD_DEADLINE_SECONDS,
        )
        elapsed = time.monotonic() - start_ts
        logger.info(f"BSK ML training finished in {elapsed:.0f}s: {result}")
    except asyncio.CancelledError:
        result = {"status": "cancelled", "rounds_used": _progress.get("rounds_used", 0),
                  "maps_updated": _progress.get("maps_updated", 0), "maps_skipped": _progress.get("maps_skipped", 0)}
        logger.info("BSK ML training cancelled")
    except asyncio.TimeoutError:
        result = {"status": "timeout", "rounds_used": _progress.get("rounds_used", 0),
                  "maps_updated": _progress.get("maps_updated", 0), "maps_skipped": _progress.get("maps_skipped", 0)}
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
            return {"status": "skipped", "rounds_used": len(rounds), "maps_updated": 0, "maps_skipped": 0}

        ratings_raw = (await session.execute(
            select(BskRating).where(BskRating.mode == "ranked")
        )).scalars().all()
        ratings = {r.user_id: r for r in ratings_raw}
        maps_raw = (await session.execute(
            select(BskMapPool).where(BskMapPool.enabled == True)
        )).scalars().all()
        maps = {m.beatmap_id: m for m in maps_raw}

    map_data: dict[int, list[dict]] = {}
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

        actual = 1.0 if rnd.winner_player == 1 else 0.0
        map_data.setdefault(rnd.beatmap_id, []).append({
            "actual": actual,
            "diff_aim":   mu1_aim   - mu2_aim,
            "diff_speed": mu1_speed - mu2_speed,
            "diff_acc":   mu1_acc   - mu2_acc,
            "diff_cons":  mu1_cons  - mu2_cons,
        })

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
            }

        await session.commit()

    # ── Phase 2: build global feature→weight model ───────────────────────────
    # Collect (feature_vector, data_weights) for all maps with enough data
    global_model = _build_global_model(trained_map_weights, maps)

    # ── Phase 3: apply to all maps ───────────────────────────────────────────
    async with AsyncSessionFactory() as session:
        for beatmap_id, map_entry in maps.items():
            if beatmap_id in trained_map_weights:
                data_w = trained_map_weights[beatmap_id]
                n_rounds = data_w["_n"]
                # Confidence grows with data: 3 rounds → 0.6, 10 rounds → 0.85, 30+ → 0.97
                confidence = 1.0 - 1.0 / (1.0 + n_rounds / 5.0)
            else:
                # No duel data: rely on global model or feature prior
                data_w = None
                confidence = 0.0

            # Feature-based prior for this map (from stored features or metadata)
            feat_prior = _feature_prior(map_entry, global_model, weights_from_features)

            # Blend: data (confidence) + prior (1 - confidence)
            if data_w and confidence > 0:
                blended = {
                    "aim":   confidence * data_w["aim"]   + (1 - confidence) * feat_prior["aim"],
                    "speed": confidence * data_w["speed"] + (1 - confidence) * feat_prior["speed"],
                    "acc":   confidence * data_w["acc"]   + (1 - confidence) * feat_prior["acc"],
                    "cons":  confidence * data_w["cons"]  + (1 - confidence) * feat_prior["cons"],
                }
            else:
                blended = feat_prior

            # Load DB entry and update
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
                updated += 1

        await session.commit()

    return {"status": "ok", "rounds_used": len(rounds), "maps_updated": updated, "maps_skipped": skipped,
            **_compute_prediction_accuracy(rounds)}


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
    import math
    components = ["aim", "speed", "acc", "cons"]
    correlations = {}
    actual_vals = [e["actual"] for e in entries]
    mean_actual = sum(actual_vals) / len(actual_vals)

    for comp in components:
        diffs = [e[f"diff_{comp}"] for e in entries]
        mean_diff = sum(diffs) / len(diffs)
        num = sum((d - mean_diff) * (a - mean_actual) for d, a in zip(diffs, actual_vals))
        den_d = math.sqrt(sum((d - mean_diff) ** 2 for d in diffs) + 1e-9)
        den_a = math.sqrt(sum((a - mean_actual) ** 2 for a in actual_vals) + 1e-9)
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
    """Minimal CART regression tree (pure Python, no deps)."""

    def __init__(self, max_depth: int = 6, min_samples_leaf: int = 3,
                 max_features: int | None = None, rng_seed: int = 0):
        self.max_depth = max_depth
        self.min_leaf = min_samples_leaf
        self.max_features = max_features
        self._rng_state = rng_seed
        self._tree: dict | None = None

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

    def fit(self, X: list[list[float]], y: list[float]) -> None:
        self._tree = self._build(X, y, list(range(len(X))), 0)

    def _build(self, X: list[list[float]], y: list[float],
               idx: list[int], depth: int) -> dict:
        vals = [y[i] for i in idx]
        mean_val = sum(vals) / len(vals)

        if depth >= self.max_depth or len(idx) <= self.min_leaf:
            return {"leaf": True, "val": mean_val}

        p = len(X[0])
        feat_subset = self._sample_features(p)

        best_score = float("inf")
        best_feat = -1
        best_thr = 0.0
        best_left: list[int] = []
        best_right: list[int] = []

        total_var = sum((v - mean_val) ** 2 for v in vals)
        if total_var < 1e-12:
            return {"leaf": True, "val": mean_val}

        for fi in feat_subset:
            sorted_idx = sorted(idx, key=lambda i: X[i][fi])
            left_sum = 0.0
            left_sq = 0.0
            right_sum = sum(y[i] for i in sorted_idx)
            right_sq = sum(y[i] ** 2 for i in sorted_idx)
            n_total = len(sorted_idx)

            for k in range(self.min_leaf - 1, n_total - self.min_leaf):
                v = y[sorted_idx[k]]
                left_sum += v
                left_sq += v * v
                right_sum -= v
                right_sq -= v * v
                nl = k + 1
                nr = n_total - nl

                if nl < self.min_leaf or nr < self.min_leaf:
                    continue
                if X[sorted_idx[k]][fi] == X[sorted_idx[k + 1]][fi]:
                    continue

                mse_l = left_sq - left_sum * left_sum / nl
                mse_r = right_sq - right_sum * right_sum / nr
                score = mse_l + mse_r

                if score < best_score:
                    best_score = score
                    best_feat = fi
                    best_thr = (X[sorted_idx[k]][fi] + X[sorted_idx[k + 1]][fi]) / 2.0
                    best_left = sorted_idx[:k + 1]
                    best_right = sorted_idx[k + 1:]

        if best_feat < 0 or not best_left or not best_right:
            return {"leaf": True, "val": mean_val}

        return {
            "leaf": False,
            "feat": best_feat,
            "thr": best_thr,
            "left": self._build(X, y, best_left, depth + 1),
            "right": self._build(X, y, best_right, depth + 1),
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
    """Bagged ensemble of _DecisionTree regressors."""

    def __init__(self, n_trees: int = 30, max_depth: int = 6,
                 min_samples_leaf: int = 3, max_features: int | None = None):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.min_leaf = min_samples_leaf
        self.max_features = max_features
        self.trees: list[_DecisionTree] = []

    def fit(self, X: list[list[float]], y: list[float]) -> None:
        import math
        n = len(X)
        mf = self.max_features or max(1, int(math.sqrt(len(X[0]))))
        self.trees = []
        for t in range(self.n_trees):
            tree = _DecisionTree(
                max_depth=self.max_depth,
                min_samples_leaf=self.min_leaf,
                max_features=mf,
                rng_seed=t * 7919 + 42,
            )
            boot_idx = [tree._rng_next() % n for _ in range(n)]
            bX = [X[i] for i in boot_idx]
            by = [y[i] for i in boot_idx]
            tree.fit(bX, by)
            self.trees.append(tree)

    def predict(self, x: list[float]) -> float:
        if not self.trees:
            return 0.0
        return sum(t.predict(x) for t in self.trees) / len(self.trees)


def _build_global_model(
    trained: dict,       # beatmap_id → {aim, speed, acc, cons, _n}
    maps: dict,          # beatmap_id → BskMapPool
) -> dict | None:
    """
    Train a Random Forest per skill axis: feature_vector → weight.
    Returns {comp: _RandomForest} or None if insufficient data.
    """
    MIN_SAMPLES = 10

    X_rows: list[list[float]] = []
    Y_rows: list[list[float]] = []

    for bid, tw in trained.items():
        if bid not in maps:
            continue
        x = _map_to_feature_vector(maps[bid])
        y = [tw["aim"], tw["speed"], tw["acc"], tw["cons"]]
        X_rows.append(x)
        Y_rows.append(y)

    if len(X_rows) < MIN_SAMPLES:
        return None

    components = ["aim", "speed", "acc", "cons"]
    forests: dict[str, _RandomForest] = {}
    for ci, comp in enumerate(components):
        y_col = [Y_rows[k][ci] for k in range(len(Y_rows))]
        rf = _RandomForest(n_trees=30, max_depth=6, min_samples_leaf=3)
        rf.fit(X_rows, y_col)
        forests[comp] = rf

    logger.info(f"BSK ML: Random Forest model trained on {len(X_rows)} maps")
    return forests


def _feature_prior(
    map_entry,
    global_model: dict | None,
    weights_from_features_fn,
) -> dict:
    """
    Return feature-based weight prior for a map.
    If the global model is available, use it; else fall back to the new
    intrinsic+softmax pipeline.  Output sums to 1.0.
    """
    if global_model is not None:
        fv = _map_to_feature_vector(map_entry)
        raw = {}
        for comp, forest in global_model.items():
            raw[comp] = forest.predict(fv)
        raw = {k: max(v, 0.0) for k, v in raw.items()}
        total = sum(raw.values()) or 1.0
        return {k: round(v / total, 3) for k, v in raw.items()}

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
