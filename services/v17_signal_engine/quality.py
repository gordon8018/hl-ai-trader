from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

from shared.v17.schemas import V17FeatureSnapshot, V17QualityLabel, V17Status

DEFAULT_QUALITY_THRESHOLDS: dict[str, float] = {
    "Q1": 0.75,
    "Q2": 0.55,
    "Q3": 0.35,
}
DEFAULT_LABEL_CAP_MULTIPLIERS: dict[str, float] = {
    "Q1": 1.0,
    "Q2": 0.75,
    "Q3": 0.5,
    "Q4": 0.25,
    "UNAVAILABLE": 0.0,
}
DEFAULT_FEATURE_WEIGHTS: dict[str, float] = {
    "range_rank": 0.30,
    "volume_rank": 0.30,
    "side_momentum_rank": 0.30,
    "atr_pct": 0.10,
}
DEFAULT_ATR_PCT_CAP = 0.08
REQUIRED_FEATURES = tuple(DEFAULT_FEATURE_WEIGHTS.keys())
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"


@dataclass(frozen=True)
class QualityScoreResult:
    quality_score: float | None
    quality_label: V17QualityLabel
    event_gross_cap_multiplier: float
    status: V17Status
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "quality_score": self.quality_score,
            "quality_label": self.quality_label,
            "event_gross_cap_multiplier": self.event_gross_cap_multiplier,
            "status": self.status,
            "reason": self.reason,
        }


def score_quality(
    snapshot: V17FeatureSnapshot | Mapping[str, Any] | object,
    *,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
) -> QualityScoreResult:
    payload = _coerce_snapshot(snapshot)
    if payload.get("status", "valid") != "valid":
        return _unavailable(payload.get("reason") or "feature_snapshot_not_valid")

    features = payload.get("features")
    if not isinstance(features, Mapping):
        return _unavailable("missing_features")

    missing = [name for name in REQUIRED_FEATURES if name not in features]
    if missing:
        return _unavailable(f"missing_quality_fields:{','.join(missing)}")

    values = _coerce_feature_values(features)
    if values is None:
        return _unavailable("non_numeric_quality_fields")

    resolved_config = _load_quality_config(config=config, config_path=config_path)
    weights = _normalize_weights(resolved_config.get("feature_weights", DEFAULT_FEATURE_WEIGHTS))
    atr_pct_cap = float(resolved_config.get("atr_pct_cap", DEFAULT_ATR_PCT_CAP))
    thresholds = _quality_thresholds(resolved_config.get("thresholds", DEFAULT_QUALITY_THRESHOLDS))
    cap_mapping = _cap_mapping(resolved_config.get("label_cap_multipliers", DEFAULT_LABEL_CAP_MULTIPLIERS))

    components = {
        "range_rank": _clamp01(values["range_rank"]),
        "volume_rank": _clamp01(values["volume_rank"]),
        "side_momentum_rank": _clamp01(values["side_momentum_rank"]),
        "atr_pct": _clamp01(values["atr_pct"] / atr_pct_cap) if atr_pct_cap > 0 else 0.0,
    }
    score = round(sum(components[name] * weights[name] for name in REQUIRED_FEATURES), 6)
    label = _label_for_score(score, thresholds)
    return QualityScoreResult(
        quality_score=score,
        quality_label=label,
        event_gross_cap_multiplier=cap_mapping[label],
        status="valid",
        reason="quality_score_ready",
    )


def _coerce_snapshot(snapshot: V17FeatureSnapshot | Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(snapshot, V17FeatureSnapshot):
        return snapshot.to_dict()
    if isinstance(snapshot, Mapping):
        return snapshot
    to_dict = getattr(snapshot, "to_dict", None)
    if callable(to_dict):
        value = cast(Callable[[], Any], to_dict)()
        if isinstance(value, Mapping):
            return value
    model_dump = getattr(snapshot, "model_dump", None)
    if callable(model_dump):
        value = cast(Callable[..., Any], model_dump)(mode="json")
        if isinstance(value, Mapping):
            return value
    if hasattr(snapshot, "__dict__"):
        return vars(snapshot)
    raise TypeError("snapshot must be V17FeatureSnapshot, mapping, or object with fields")


def _coerce_feature_values(features: Mapping[str, Any]) -> dict[str, float] | None:
    values: dict[str, float] = {}
    try:
        for name in REQUIRED_FEATURES:
            values[name] = float(features[name])
    except (TypeError, ValueError):
        return None
    return values


def _load_quality_config(
    *,
    config: Mapping[str, Any] | None,
    config_path: str | Path | None,
) -> Mapping[str, Any]:
    if config is not None:
        return _quality_section(config)
    path = Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH
    if not path.exists():
        return {}
    with path.open() as f:
        return _quality_section(json.load(f))


def _quality_section(config: Mapping[str, Any]) -> Mapping[str, Any]:
    quality = config.get("quality_score", {})
    return quality if isinstance(quality, Mapping) else {}


def _quality_thresholds(raw: Any) -> dict[str, float]:
    thresholds = dict(DEFAULT_QUALITY_THRESHOLDS)
    if isinstance(raw, Mapping):
        for label in ("Q1", "Q2", "Q3"):
            if label in raw:
                thresholds[label] = float(raw[label])
    if not (thresholds["Q1"] >= thresholds["Q2"] >= thresholds["Q3"]):
        raise ValueError("quality thresholds must satisfy Q1 >= Q2 >= Q3")
    return thresholds


def _cap_mapping(raw: Any) -> dict[V17QualityLabel, float]:
    caps = dict(DEFAULT_LABEL_CAP_MULTIPLIERS)
    if isinstance(raw, Mapping):
        for label in caps:
            if label in raw:
                caps[label] = float(raw[label])
    return caps  # type: ignore[return-value]


def _normalize_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        raw = DEFAULT_FEATURE_WEIGHTS
    weights = {name: float(raw.get(name, DEFAULT_FEATURE_WEIGHTS[name])) for name in REQUIRED_FEATURES}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("quality feature weights must sum positive")
    return {name: weights[name] / total for name in REQUIRED_FEATURES}


def _label_for_score(score: float, thresholds: Mapping[str, float]) -> V17QualityLabel:
    if score >= thresholds["Q1"]:
        return "Q1"
    if score >= thresholds["Q2"]:
        return "Q2"
    if score >= thresholds["Q3"]:
        return "Q3"
    return "Q4"


def _unavailable(reason: str) -> QualityScoreResult:
    return QualityScoreResult(
        quality_score=None,
        quality_label="UNAVAILABLE",
        event_gross_cap_multiplier=DEFAULT_LABEL_CAP_MULTIPLIERS["UNAVAILABLE"],
        status="invalid",
        reason=reason,
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))
