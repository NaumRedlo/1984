import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Optional

from services.duels.features import FEATURE_VERSION
from services.duels.rules import SLOTS

MODEL_VERSION = "slots-logreg-v1"
OTHER = "OTHER"
CLASSES = SLOTS + (OTHER,)
Z_LIMIT = 5.0
OUTSIDE_SHARE = 0.20
HD2_MOST_AR = 8.5
HR2_LEAST_CS = 6.0

def _get(source: dict[str, Any], path: str) -> float:
    value: Any = source
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return float("nan")
        value = value[key]
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")

PATHS = (
    "interval_ms.median", "interval_ms.p90", "spacing_radii.median", "spacing_radii.p90", "spacing_radii.p99",
    "angle_deg.median", "angle_deg.p90", "speed_px_ms.median", "speed_px_ms.p90", "speed_px_ms.p99",
    "rhythm_change.median", "rhythm_change.p90", "streams.share", "streams.chains",
    "streams.lengths.3-5", "streams.lengths.6-15", "streams.lengths.16-31", "streams.lengths.32-plus",
    "reading.visible.median", "reading.visible.p90", "reading.overlaps", "sliders.share", "sliders.speed.median",
    "sliders.speed.p90", "sliders.repeats", "effective.ar", "effective.cs", "effective.od", "length_seconds",
    "aim_windows.2s.median", "aim_windows.2s.p90", "aim_windows.2s.max", "aim_windows.10s.p90", "aim_windows.30s.loaded_share",
    "tap_windows.2s.median", "tap_windows.2s.p90", "tap_windows.2s.max", "tap_windows.10s.p90", "tap_windows.30s.loaded_share",
)

def vector(features: dict[str, Any]) -> list[float]:
    found = [_get(features, path) for path in PATHS]
    objects = max(1.0, _get(features, "objects") if math.isfinite(_get(features, "objects")) else 1.0)
    for name in ("streams.chains", "reading.overlaps", "sliders.repeats", "streams.lengths.3-5", "streams.lengths.6-15",
                 "streams.lengths.16-31", "streams.lengths.32-plus"):
        index = PATHS.index(name)
        found[index] = found[index] / objects
    return found

@dataclass
class Normaliser:
    centre: list[float]
    spread: list[float]
    low: list[float]
    high: list[float]

    @classmethod
    def fit(cls, rows: list[list[float]]) -> "Normaliser":
        centre, spread, low, high = [], [], [], []
        for column in zip(*rows):
            values = sorted(value for value in column if math.isfinite(value))
            if not values:
                centre.append(0.0), spread.append(1.0), low.append(0.0), high.append(0.0)
                continue
            q1, q3 = _quantile(values, 0.25), _quantile(values, 0.75)
            centre.append(median(values))
            spread.append((q3 - q1) or 1.0)
            low.append(_quantile(values, 0.01))
            high.append(_quantile(values, 0.99))
        return cls(centre, spread, low, high)

    def apply(self, row: list[float]) -> list[float]:
        return [0.0 if not math.isfinite(value) else max(-Z_LIMIT, min(Z_LIMIT, (value - c) / s))
                for value, c, s in zip(row, self.centre, self.spread)]

    def outside(self, row: list[float]) -> bool:
        missing = sum(1 for value in row if not math.isfinite(value))
        beyond = sum(1 for value, lo, hi in zip(row, self.low, self.high) if math.isfinite(value) and not lo <= value <= hi)
        return missing > 0 or beyond > OUTSIDE_SHARE * len(row)

def _quantile(values: list[float], share: float) -> float:
    spot = (len(values) - 1) * share
    low, high = math.floor(spot), math.ceil(spot)
    return values[low] + (values[high] - values[low]) * (spot - low)

def _softmax(scores: list[float]) -> list[float]:
    top = max(scores)
    raised = [math.exp(score - top) for score in scores]
    total = sum(raised)
    return [value / total for value in raised]

@dataclass
class Prediction:
    slot: Optional[str]
    probability: float
    margin: float
    probabilities: dict[str, float]
    outside: bool
    reasons: list[tuple[str, float]] = field(default_factory=list)

@dataclass
class SlotModel:
    normaliser: Normaliser
    weights: list[list[float]]
    bias: list[float]
    version: str = MODEL_VERSION

    @classmethod
    def fit(cls, rows: list[list[float]], labels: list[str], strength: float = 0.01, steps: int = 400, rate: float = 0.5) -> "SlotModel":
        normaliser = Normaliser.fit(rows)
        data = [normaliser.apply(row) for row in rows]
        targets = [CLASSES.index(label) for label in labels]
        width = len(PATHS)
        weights = [[0.0] * width for _ in CLASSES]
        bias = [0.0] * len(CLASSES)
        count = max(1, len(data))
        for _ in range(steps):
            grad_w = [[0.0] * width for _ in CLASSES]
            grad_b = [0.0] * len(CLASSES)
            for row, target in zip(data, targets):
                probabilities = _softmax([b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(weights, bias)])
                for k, probability in enumerate(probabilities):
                    error = probability - (1.0 if k == target else 0.0)
                    grad_b[k] += error
                    line = grad_w[k]
                    for j, x in enumerate(row):
                        line[j] += error * x
            for k in range(len(CLASSES)):
                bias[k] -= rate * grad_b[k] / count
                for j in range(width):
                    weights[k][j] -= rate * (grad_w[k][j] / count + strength * weights[k][j])
        return cls(normaliser, weights, bias)

    def predict(self, features: dict[str, Any], base_ar: float, hr_cs: float) -> Prediction:
        raw = vector(features)
        row = self.normaliser.apply(raw)
        scores = [b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(self.weights, self.bias)]
        allowed = [name != "HD2" or base_ar <= HD2_MOST_AR for name in CLASSES]
        allowed = [ok and (name != "HR2" or hr_cs >= HR2_LEAST_CS) for ok, name in zip(allowed, CLASSES)]
        kept = [score if ok else -math.inf for score, ok in zip(scores, allowed)]
        probabilities = _softmax(kept)
        ranked = sorted(range(len(CLASSES)), key=lambda k: -probabilities[k])
        best = ranked[0]
        second = next((k for k in ranked[1:] if CLASSES[k] != OTHER), None)
        margin = probabilities[best] - (probabilities[second] if second is not None else 0.0)
        reasons = sorted(((PATHS[j], self.weights[best][j] * row[j]) for j in range(len(PATHS))), key=lambda pair: -abs(pair[1]))[:5]
        outside = self.normaliser.outside(raw)
        slot = None if CLASSES[best] == OTHER or outside else CLASSES[best]
        return Prediction(slot, probabilities[best], margin, dict(zip(CLASSES, probabilities)), outside, reasons)

    def dump(self) -> dict[str, Any]:
        return {"version": self.version, "features": FEATURE_VERSION, "paths": list(PATHS), "classes": list(CLASSES), "weights": self.weights, "bias": self.bias,
                "centre": self.normaliser.centre, "spread": self.normaliser.spread, "low": self.normaliser.low,
                "high": self.normaliser.high}

    @classmethod
    def load(cls, body: dict[str, Any]) -> "SlotModel":
        if body.get("paths") != list(PATHS) or body.get("classes") != list(CLASSES) or body.get("features", FEATURE_VERSION) != FEATURE_VERSION:
            raise ValueError("model was built for other features")
        return cls(Normaliser(body["centre"], body["spread"], body["low"], body["high"]), body["weights"], body["bias"],
                   body.get("version", MODEL_VERSION))

def report(truth: list[str], guessed: list[Optional[str]]) -> dict[str, Any]:
    per_slot = {}
    for name in CLASSES:
        hit = sum(1 for t, g in zip(truth, guessed) if g == name and t == name)
        said = sum(1 for g in guessed if g == name)
        real = sum(1 for t in truth if t == name)
        precision = hit / said if said else 0.0
        recall = hit / real if real else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_slot[name] = {"precision": precision, "recall": recall, "f1": f1, "support": real}
    present = [name for name in SLOTS if per_slot[name]["support"]]
    macro = sum(per_slot[name]["f1"] for name in present) / len(present) if present else 0.0
    confusion = {t: {g or "NONE": sum(1 for a, b in zip(truth, guessed) if a == t and (b or "NONE") == (g or "NONE"))
                     for g in list(CLASSES) + ["NONE"]} for t in CLASSES}
    return {"per_slot": per_slot, "macro_f1": macro, "confusion": confusion, "count": len(truth)}

def grouped_split(keys: list[tuple[int, int]], test_years: set[int]) -> tuple[list[int], list[int]]:
    held_sets = {set_id for set_id, year in keys if year in test_years}
    train = [i for i, (set_id, year) in enumerate(keys) if year not in test_years and set_id not in held_sets]
    test = [i for i, (set_id, year) in enumerate(keys) if year in test_years]
    return train, test
