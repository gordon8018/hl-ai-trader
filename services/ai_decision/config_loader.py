# services/ai_decision/config_loader.py
"""
JSON-based configuration loader for ai_decision service.
Replaces os.environ.get() parameter loading.

Usage:
    from services.ai_decision.config_loader import load_config
    params = load_config()   # reads config/trading_params.json by default
    MAX_GROSS = float(params["MAX_GROSS"])
"""
import json
import os
from typing import Any, Dict

# Default config path: config/trading_params.json relative to project root
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "trading_params.json"
)

# Keys that must be present in the active version block.
# Missing keys cause silent default fallback in _get(), which is dangerous for
# version-specific parameters (e.g. V9 raised POSITION_PROFIT_TARGET_BPS from 15→25).
REQUIRED_KEYS = [
    "REDIS_URL",
    "UNIVERSE",
    "MAX_GROSS",
    "CAP_ALT",
    "AI_TURNOVER_CAP",
    "AI_TURNOVER_CAP_HIGH",
    "AI_MIN_CONFIDENCE",
    "AI_MIN_MAJOR_INTERVAL_MIN",
    "AI_SIGNAL_DELTA_THRESHOLD",
    "POSITION_PROFIT_TARGET_BPS",
    "DAILY_DRAWDOWN_HALT_USD",
    "COOLDOWN_MINUTES",
]


def load_config(path: str = None) -> Dict[str, Any]:
    """
    Load trading parameters from a JSON config file.

    Args:
        path: Path to the JSON config file. Defaults to config/trading_params.json.

    Returns:
        Dict of parameter name -> value for the active version.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError: If active_version is not found in versions, or required keys are missing.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    resolved = path or _DEFAULT_CONFIG_PATH
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Config file not found: {resolved}")

    with open(resolved, "r") as f:
        data = json.load(f)

    active = data["active_version"]
    if active not in data["versions"]:
        raise KeyError(f"active_version '{active}' not found in versions: {list(data['versions'].keys())}")

    params = data["versions"][active]
    missing = [k for k in REQUIRED_KEYS if k not in params]
    if missing:
        raise KeyError(f"Config version '{active}' is missing required keys: {missing}")

    # 合并 exchange_overrides（如果有）
    exchange = os.environ.get("EXCHANGE", "hyperliquid").lower()
    overrides = data.get("exchange_overrides", {}).get(exchange, {})
    for k, v in overrides.items():
        if not k.startswith("_"):   # 跳过注释字段
            params[k] = v

    return params
