"""Safe V17 alpha target skeleton for shadow-only integration."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
STREAM_OUT = "alpha.target.v17_shadow"
DEFAULT_STRATEGY_VERSION = "V17_shadow"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open() as config_file:
        return json.load(config_file)


def _resolved_config(config: dict[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(config) if config is not None else load_config()


def _caps(config: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(config.get("caps", {}))


def _strategy_version(config: dict[str, Any]) -> str:
    return str(config.get("strategy_version", DEFAULT_STRATEGY_VERSION))


def build_flat_target(reason: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_config = _resolved_config(config)
    configured_stream = resolved_config.get("streams", {}).get("target")
    stream = configured_stream if configured_stream == STREAM_OUT else STREAM_OUT

    return {
        "stream": stream,
        "action": "flat",
        "reason": reason,
        "weights": {},
        "caps": _caps(resolved_config),
        "metadata": {
            "strategy_version": _strategy_version(resolved_config),
            "producer": "services.v17_alpha.app",
            "real_money_execution_allowed": False,
        },
    }


def build_target_from_inputs(
    state: dict[str, Any] | None,
    features: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not state:
        target = build_flat_target("missing_state", config=config)
        target["action"] = "skip"
        return target

    if not features:
        target = build_flat_target("missing_market_features", config=config)
        target["action"] = "skip"
        return target

    return build_flat_target("skeleton_noop", config=config)
