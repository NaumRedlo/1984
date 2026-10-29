import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Optional, Union

from services.duels.features import FEATURE_VERSION
from services.duels.rules import SLOTS, reserved

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

VIEWS = (
    ("NFDT", ("interval_ms.median", "interval_ms.p90", "speed_px_ms.median", "speed_px_ms.p90", "speed_px_ms.p99", "aim_windows.2s.p90",
              "aim_windows.2s.max", "tap_windows.2s.median", "tap_windows.2s.p90", "tap_windows.2s.max", "effective.ar", "effective.od",
              "length_seconds")),
    ("NFHR", ("spacing_radii.median", "spacing_radii.p90", "speed_px_ms.p90", "aim_windows.2s.p90", "aim_windows.2s.max", "effective.cs",
              "effective.ar", "effective.od")),
)
SEEN = PATHS + tuple(f"{mods}:{path}" for mods, paths in VIEWS for path in paths)

def seen_vector(features: dict[str, Any], views: Optional[dict[str, dict[str, Any]]] = None, paths: tuple[str, ...] = SEEN) -> list[float]:
    found = vector(features)
    for name in paths[len(PATHS):]:
        mods, path = name.split(":", 1)
        found.append(_get((views or {}).get(mods) or {}, path))
    return found

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
    levels: dict[str, float] = field(default_factory=dict)

def _numbers(values: Any, width: int, what: str) -> list[float]:
    if not isinstance(values, list) or len(values) != width \
            or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values):
        raise ValueError(f"{what} does not fit the model")
    return [float(value) for value in values]

def _matrix(rows: Any, height: int, width: int, what: str) -> list[list[float]]:
    if not isinstance(rows, list) or len(rows) != height:
        raise ValueError(f"{what} does not fit the model")
    return [_numbers(row, width, what) for row in rows]

def checked_normaliser(body: dict[str, Any], width: int) -> Normaliser:
    found = Normaliser(*(_numbers(body.get(name), width, name) for name in ("centre", "spread", "low", "high")))
    if not all(value > 0 for value in found.spread):
        raise ValueError("spread does not fit the model")
    return found

def targets_of(labels: list[str], classes: tuple[str, ...], also: Optional[list[Optional[str]]], share: Union[float, list[float]]) -> list[list[float]]:
    found = []
    for at, label in enumerate(labels):
        second = also[at] if also is not None else None
        part = share[at] if isinstance(share, list) else share
        line = [0.0] * len(classes)
        if second in classes and second != label and part > 0:
            line[classes.index(label)], line[classes.index(second)] = 1.0 - part, part
        else:
            line[classes.index(label)] = 1.0
        found.append(line)
    return found

@dataclass
class SlotModel:
    normaliser: Normaliser
    weights: list[list[float]]
    bias: list[float]
    version: str = MODEL_VERSION
    classes: tuple[str, ...] = CLASSES
    paths: tuple[str, ...] = PATHS

    @classmethod
    def fit(cls, rows: list[list[float]], labels: list[str], strength: float = 0.01, steps: int = 400, rate: float = 0.5,
            paths: tuple[str, ...] = PATHS, also: Optional[list[Optional[str]]] = None, share: Union[float, list[float]] = 0.0,
            progress=None) -> "SlotModel":
        seconds = also or [None] * len(labels)
        parts = share if isinstance(share, list) else [share] * len(labels)
        classes = SLOTS + tuple(sorted({label for label in list(labels) + list(seconds) if label and reserved(label)}))
        kept = [(row, label, second, part) for row, label, second, part in zip(rows, labels, seconds, parts) if label in classes]
        rows, labels, seconds = [row for row, _, _, _ in kept], [label for _, label, _, _ in kept], [second for _, _, second, _ in kept]
        share = [part for _, _, _, part in kept]
        normaliser = Normaliser.fit(rows)
        data = [normaliser.apply(row) for row in rows]
        targets = targets_of(labels, classes, seconds, share)
        width = len(paths)
        weights = [[0.0] * width for _ in classes]
        bias = [0.0] * len(classes)
        count = max(1, len(data))
        for step in range(steps):
            grad_w = [[0.0] * width for _ in classes]
            grad_b = [0.0] * len(classes)
            for row, target in zip(data, targets):
                probabilities = _softmax([b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(weights, bias)])
                for k, probability in enumerate(probabilities):
                    error = probability - target[k]
                    grad_b[k] += error
                    line = grad_w[k]
                    for j, x in enumerate(row):
                        line[j] += error * x
            for k in range(len(classes)):
                bias[k] -= rate * grad_b[k] / count
                for j in range(width):
                    weights[k][j] -= rate * (grad_w[k][j] / count + strength * weights[k][j])
            if progress is not None and ((step + 1) % max(1, steps // 100) == 0 or step + 1 == steps):
                progress(step + 1)
        return cls(normaliser, weights, bias, MODEL_VERSION, classes, paths)

    def predict(self, features: dict[str, Any], base_ar: float, hr_cs: float, views: Optional[dict[str, dict[str, Any]]] = None) -> Prediction:
        raw = seen_vector(features, views, self.paths)
        row = self.normaliser.apply(raw)
        scores = [b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(self.weights, self.bias)]
        classes = self.classes
        allowed = [name != "HD2" or base_ar <= HD2_MOST_AR for name in classes]
        allowed = [ok and (name != "HR2" or hr_cs >= HR2_LEAST_CS) for ok, name in zip(allowed, classes)]
        kept = [score if ok else -math.inf for score, ok in zip(scores, allowed)]
        probabilities = _softmax(kept)
        ranked = sorted(range(len(classes)), key=lambda k: -probabilities[k])
        best = ranked[0]
        second = next((k for k in ranked[1:] if classes[k] != OTHER), None)
        margin = probabilities[best] - (probabilities[second] if second is not None else 0.0)
        reasons = sorted(((self.paths[j], self.weights[best][j] * row[j]) for j in range(len(self.paths))), key=lambda pair: -abs(pair[1]))[:5]
        outside = self.normaliser.outside(raw)
        slot = classes[best] if classes[best] in SLOTS and not outside else None
        return Prediction(slot, probabilities[best], margin, dict(zip(classes, probabilities)), outside, reasons, levels_of(self.paths, row, reasons))

    def dump(self) -> dict[str, Any]:
        return {"version": self.version, "features": FEATURE_VERSION, "paths": list(self.paths), "classes": list(self.classes), "weights": self.weights, "bias": self.bias,
                "centre": self.normaliser.centre, "spread": self.normaliser.spread, "low": self.normaliser.low,
                "high": self.normaliser.high}

    @classmethod
    def load(cls, body: dict[str, Any], allowed: tuple[tuple[str, ...], ...] = (PATHS, SEEN)) -> "SlotModel":
        classes = tuple(body.get("classes") or ())
        paths = tuple(body.get("paths") or ())
        if paths not in allowed or body.get("features", FEATURE_VERSION) != FEATURE_VERSION:
            raise ValueError("model was built for other features")
        extra = classes[len(SLOTS):-1] if classes[-1:] == (OTHER,) else classes[len(SLOTS):]
        if classes[:len(SLOTS)] != SLOTS or not all(reserved(name) for name in extra) \
                or len(body.get("weights") or ()) != len(classes) or len(body.get("bias") or ()) != len(classes):
            raise ValueError("model was built for other classes")
        return cls(checked_normaliser(body, len(paths)), _matrix(body["weights"], len(classes), len(paths), "weights"),
                   _numbers(body["bias"], len(classes), "bias"), body.get("version", MODEL_VERSION), classes, paths)

def levels_of(paths: tuple[str, ...], row: list[float], reasons: list[tuple[str, float]]) -> dict[str, float]:
    at = {path: j for j, path in enumerate(paths)}
    return {name: row[at[name]] for name, _ in reasons if name in at}

def report(truth: list[str], guessed: list[Optional[str]]) -> dict[str, Any]:
    seen = set(truth) | set(guessed)
    names = list(SLOTS) + sorted({name for name in seen if name and reserved(name)}) + ([OTHER] if OTHER in seen else [])
    per_slot = {}
    for name in names:
        hit = sum(1 for t, g in zip(truth, guessed) if g == name and t == name)
        said = sum(1 for g in guessed if g == name)
        real = sum(1 for t in truth if t == name)
        precision = hit / said if said else 0.0
        recall = hit / real if real else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_slot[name] = {"precision": precision, "recall": recall, "f1": f1, "support": real}
    present = [name for name in SLOTS if per_slot[name]["support"]]
    macro = sum(per_slot[name]["f1"] for name in present) / len(present) if present else 0.0
    every = [name for name in names if per_slot[name]["support"]]
    confusion = {t: {g or "NONE": sum(1 for a, b in zip(truth, guessed) if a == t and (b or "NONE") == (g or "NONE"))
                     for g in names + ["NONE"]} for t in names}
    return {"per_slot": per_slot, "macro_f1": macro, "macro_f1_all": sum(per_slot[name]["f1"] for name in every) / len(every) if every else 0.0,
            "confusion": confusion, "count": len(truth)}

def grouped_split(keys: list[tuple[int, int]], test_years: set[int]) -> tuple[list[int], list[int]]:
    held_sets = {set_id for set_id, year in keys if year in test_years}
    train = [i for i, (set_id, year) in enumerate(keys) if year not in test_years and set_id not in held_sets]
    test = [i for i, (set_id, year) in enumerate(keys) if year in test_years]
    return train, test
