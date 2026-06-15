from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "v17_live_canary.json"


def enabled() -> bool:
    return os.environ.get("V17_LIVE_CANARY_CONFIG", "false").lower() == "true"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with config_path.open() as config_file:
        return json.load(config_file)


def universe_csv(config: Mapping[str, Any]) -> str:
    universe = config.get("universe", [])
    if not isinstance(universe, list) or not universe:
        raise ValueError("v17 live canary config requires a non-empty universe list")
    return ",".join(str(symbol).strip().upper() for symbol in universe if str(symbol).strip())


def execution_overrides(config: Mapping[str, Any]) -> dict[str, str]:
    execution = config.get("execution", {})
    if not isinstance(execution, Mapping):
        raise ValueError("v17 live canary execution config must be an object")
    overrides = {
        "UNIVERSE": universe_csv(config),
        "POSITION_LEVERAGE": str(execution.get("position_leverage", 1.0)),
        "V17_LIVE_STATE_MAX_AGE_SECONDS": str(execution.get("state_max_age_seconds", 120)),
    }
    max_open_orders = execution.get("max_open_orders")
    if max_open_orders is not None:
        overrides["MAX_OPEN_ORDERS"] = str(max_open_orders)
    return overrides


def risk_overrides(config: Mapping[str, Any]) -> dict[str, str]:
    caps = config.get("caps", {})
    capital = config.get("capital", {})
    if not isinstance(caps, Mapping):
        raise ValueError("v17 live canary caps config must be an object")
    if not isinstance(capital, Mapping):
        raise ValueError("v17 live canary capital config must be an object")
    overrides = {
        "UNIVERSE": universe_csv(config),
        "MAX_GROSS": str(caps.get("max_gross", 0.40)),
        "MAX_NET": str(caps.get("max_net", 0.20)),
        "TURNOVER_CAP": str(caps.get("max_daily_turnover", 0.10)),
        "CAP_BTC_ETH": str(caps.get("max_symbol_gross", 0.15)),
        "CAP_ALT": str(caps.get("max_symbol_gross", 0.10)),
    }
    loss_stop_usd = capital.get("loss_stop_usd")
    capital_cap_usd = capital.get("capital_cap_usd")
    if loss_stop_usd is not None and capital_cap_usd:
        loss_halt_pct = float(loss_stop_usd) / float(capital_cap_usd)
        overrides["DAILY_LOSS_HALT_PCT"] = str(loss_halt_pct)
        overrides["DAILY_LOSS_REDUCE_ONLY_PCT"] = str(loss_halt_pct / 2.0)
    return overrides


def apply_if_enabled(kind: str) -> dict[str, str]:
    if not enabled():
        return {}
    config_path = os.environ.get("V17_LIVE_CANARY_CONFIG_PATH")
    config = load_config(config_path)
    overrides = execution_overrides(config) if kind == "execution" else risk_overrides(config)
    for key, value in overrides.items():
        os.environ.setdefault(key, value)
    return overrides
