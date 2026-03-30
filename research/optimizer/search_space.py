"""Search space definition for crypto factor optimization."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from research.factors.registry import FACTOR_FAMILIES

# All factors from registry (may not all be present in data)
_ALL_FAMILY_FACTORS = {k: [name for name, _ in v] for k, v in FACTOR_FAMILIES.items()}

# Cache for available factors detected from actual data
_available_factors_cache: Optional[dict[str, list[str]]] = None


def get_available_families(data_dir: str = "research/data/parquet") -> dict[str, list[str]]:
    """Detect which factor families have data available in Parquet files.

    Returns {family_name: [available_factor_names]} only for families with ≥1 factor present.
    """
    global _available_factors_cache
    if _available_factors_cache is not None:
        return _available_factors_cache

    features_dir = Path(data_dir) / "features_15m"
    parquet_files = sorted(features_dir.glob("*.parquet"))
    if not parquet_files:
        _available_factors_cache = _ALL_FAMILY_FACTORS
        return _available_factors_cache

    import polars as pl
    sample = pl.read_parquet(parquet_files[0])
    available_cols = set(sample.columns)

    result = {}
    for family, factors in _ALL_FAMILY_FACTORS.items():
        present = [f for f in factors if f in available_cols]
        if present:
            result[family] = present

    _available_factors_cache = result
    return result


SEARCH_DIMENSIONS = {
    "active_factors": {
        "families": _ALL_FAMILY_FACTORS,
        "select_mode": "family",
    },
    "signal_params": {
        "confidence_threshold": {"min": 0.3, "max": 0.8, "step": 0.05},
        "signal_delta_threshold": {"min": 0.03, "max": 0.15, "step": 0.01},
        "smooth_alpha": {"min": 0.1, "max": 0.6, "step": 0.05},
    },
    "position_params": {
        "max_gross": {"min": 0.15, "max": 0.50, "step": 0.05},
        "max_net": {"min": 0.10, "max": 0.35, "step": 0.05},
        "turnover_cap": {"min": 0.02, "max": 0.10, "step": 0.01},
        "profit_target_bps": {"min": 10, "max": 50, "step": 5},
        "stop_loss_bps": {"min": 15, "max": 80, "step": 5},
    },
    "timing_params": {
        "min_trade_interval_min": {"min": 15, "max": 120, "step": 15},
        "position_max_age_min": {"min": 15, "max": 120, "step": 15},
        "max_trades_per_day": {"min": 5, "max": 30, "step": 5},
    },
    "layer1_params": {
        "bearish_ret_1h_threshold": {"min": -0.01, "max": -0.002, "step": 0.001},
        "trending_strength_threshold": {"min": 0.5, "max": 2.0, "step": 0.25},
        "layer1_debounce_min": {"min": 30, "max": 90, "step": 15},
        "bearish_regime_long_block": [True, False],
    },
}

GOOD_THRESHOLDS = {
    "sharpe": 1.0,
    "max_drawdown": 0.10,
    "win_rate": 0.45,
    "profit_factor": 1.3,
    "net_pnl_positive": True,
}

# All valid parameter names across all dimensions
ALL_PARAM_NAMES: set[str] = set()
for dim_spec in SEARCH_DIMENSIONS.values():
    if isinstance(dim_spec, dict):
        for key, val in dim_spec.items():
            if isinstance(val, (dict, list)) and key not in ("families", "select_mode"):
                ALL_PARAM_NAMES.add(key)


def compute_direction_fingerprint(params: dict) -> str:
    """Compute a fingerprint for a direction, ignoring threshold values.

    Two directions exploring the same factor families with different thresholds
    should have the same fingerprint.
    """
    families = sorted(params.get("primary_families", []))
    strategy = params.get("strategy_type", "weighted_factor")
    focus_dims = sorted(params.get("focus_dimensions", []))
    return f"{strategy}|{'_'.join(families)}|{'_'.join(focus_dims)}"


def hash_params(params: dict) -> str:
    """SHA1 hash of full params for experiment-level deduplication."""
    s = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def validate_params(params: dict) -> tuple[bool, str]:
    """Validate a parameter set. Returns (is_valid, error_message)."""
    if not isinstance(params, dict):
        return False, "params must be a dict"

    # Check all keys are recognized
    for key in params:
        if key not in ALL_PARAM_NAMES and key not in ("strategy_type", "factor_weights", "factor_name"):
            pass  # Allow extra keys for flexibility

    # Range checks
    for dim_spec in SEARCH_DIMENSIONS.values():
        if not isinstance(dim_spec, dict):
            continue
        for param_name, param_range in dim_spec.items():
            if param_name in params and isinstance(param_range, dict) and "min" in param_range:
                val = params[param_name]
                if isinstance(val, (int, float)):
                    if val < param_range["min"] or val > param_range["max"]:
                        return False, f"{param_name}={val} outside [{param_range['min']}, {param_range['max']}]"

    return True, ""


def describe_search_space() -> str:
    """Return a human-readable description of the search space for LLM prompts."""
    lines = ["## Crypto Perpetual Futures Trading Parameter Search Space\n"]
    lines.append("### Factor Families (from FeatureSnapshot15m):\n")

    families = SEARCH_DIMENSIONS["active_factors"]["families"]
    for family, factors in families.items():
        lines.append(f"- **{family}**: {', '.join(factors)}")

    lines.append("\n### Tunable Parameter Dimensions:\n")
    for dim_name, dim_spec in SEARCH_DIMENSIONS.items():
        if dim_name == "active_factors":
            continue
        lines.append(f"**{dim_name}:**")
        if isinstance(dim_spec, dict):
            for param, spec in dim_spec.items():
                if isinstance(spec, dict) and "min" in spec:
                    lines.append(f"  - {param}: [{spec['min']}, {spec['max']}] step={spec['step']}")
                elif isinstance(spec, list):
                    lines.append(f"  - {param}: {spec}")

    lines.append("\n### Quality Thresholds (good strategy = 4/5):")
    for k, v in GOOD_THRESHOLDS.items():
        lines.append(f"  - {k}: {v}")

    return "\n".join(lines)
