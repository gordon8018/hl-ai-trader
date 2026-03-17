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


def load_config(path: str = None) -> Dict[str, Any]:
    """
    Load trading parameters from a JSON config file.

    Args:
        path: Path to the JSON config file. Defaults to config/trading_params.json.

    Returns:
        Dict of parameter name -> value for the active version.

    Raises:
        FileNotFoundError: If the config file does not exist.
        KeyError: If active_version is not found in versions.
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

    return data["versions"][active]
