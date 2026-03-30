"""Search space definition for crypto factor optimization."""
from __future__ import annotations

from research.factors.registry import FACTOR_FAMILIES

SEARCH_DIMENSIONS = {
    "active_factors": {
        "families": {k: [name for name, _ in v] for k, v in FACTOR_FAMILIES.items()},
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
