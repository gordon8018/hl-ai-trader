"""Safe V17 alpha target producer for canary/shadow integration."""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.schemas import Envelope, cycle_id_from_asof_minute, current_cycle_id

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_live_canary.json"
STREAM_OUT = "alpha.target.v17_shadow"
LIVE_STREAM_OUT = "alpha.target.v17_live"
DEFAULT_STRATEGY_VERSION = "V17_shadow"
STATE_KEY = "latest.state.snapshot"
DEFAULT_FEATURE_STREAM = "md.features.15m"
PRODUCER = "services.v17_alpha.app"


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
    return _target_portfolio(
        asof_minute=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        universe=_universe(resolved_config),
        weights={},
        reason=reason,
        confidence=0.0,
        config=resolved_config,
        evidence={"reason": reason, "signal_count": 0},
    )


def build_target_from_inputs(
    state: dict[str, Any] | None,
    features: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_config = _resolved_config(config)
    if not state:
        return build_flat_target("missing_state", config=resolved_config)
    if not features:
        return build_flat_target("missing_market_features", config=resolved_config)

    signals = _candidate_weights(features=features, config=resolved_config)
    if not signals:
        return _target_portfolio(
            asof_minute=_asof_minute(features),
            universe=_universe(resolved_config, features),
            weights={},
            reason="no_signal_after_quality_filters",
            confidence=0.0,
            config=resolved_config,
            evidence={"reason": "no_signal_after_quality_filters", "signal_count": 0},
        )

    capped = _apply_caps(signals, _caps(resolved_config))
    if not capped:
        return _target_portfolio(
            asof_minute=_asof_minute(features),
            universe=_universe(resolved_config, features),
            weights={},
            reason="signals_removed_by_caps",
            confidence=0.0,
            config=resolved_config,
            evidence={"reason": "signals_removed_by_caps", "raw_weights": signals, "signal_count": 0},
        )

    return _target_portfolio(
        asof_minute=_asof_minute(features),
        universe=_universe(resolved_config, features),
        weights=capped,
        reason="v17_live_canary_trend_momentum",
        confidence=min(1.0, 0.5 + sum(abs(weight) for weight in capped.values())),
        config=resolved_config,
        evidence={
            "reason": "v17_live_canary_trend_momentum",
            "raw_weights": signals,
            "signal_count": len(capped),
            "feature_asof_minute": features.get("asof_minute"),
        },
    )


def run_once(config: dict[str, Any] | None = None, bus: Any | None = None) -> dict[str, Any]:
    resolved_config = _resolved_config(config)
    if not resolved_config.get("enabled", True):
        return {"status": "blocked", "reason": "v17_alpha_disabled"}

    stream = _target_stream(resolved_config)
    if stream not in {STREAM_OUT, LIVE_STREAM_OUT}:
        raise ValueError("V17 alpha target stream must remain isolated")

    if bus is None:
        from shared.bus.redis_streams import RedisStreams

        redis_bus = RedisStreams(os.environ["REDIS_URL"])
    else:
        redis_bus = bus
    state = redis_bus.get_json(STATE_KEY)
    features = _latest_features(redis_bus, _feature_stream(resolved_config))
    target = build_target_from_inputs(state=state, features=features, config=resolved_config)
    envelope = {
        "env": _envelope_for_target(target).model_dump(),
        "data": target,
    }
    msg_id = redis_bus.xadd_json(stream, envelope)
    return {"status": "written", "stream": stream, "id": msg_id, "reason": target.get("rationale")}


def run_forever(config: dict[str, Any] | None = None, interval_seconds: float | None = None) -> None:
    resolved_config = _resolved_config(config)
    interval = float(interval_seconds or os.environ.get("V17_ALPHA_INTERVAL_SECONDS", "60"))
    while True:
        run_once(config=resolved_config)
        time.sleep(max(interval, 1.0))


def _target_stream(config: dict[str, Any]) -> str:
    requested = os.environ.get("V17_ALPHA_TARGET_STREAM") or str(config.get("streams", {}).get("target", STREAM_OUT))
    if requested == STREAM_OUT:
        return STREAM_OUT
    if requested == LIVE_STREAM_OUT:
        approved = os.environ.get("V17_ALPHA_LIVE_TARGET_APPROVED", "false").lower() == "true"
        if not approved:
            raise RuntimeError("V17 alpha live target stream requires explicit live target approval")
        return LIVE_STREAM_OUT
    return STREAM_OUT


def _envelope_for_target(target: dict[str, Any]) -> Envelope:
    asof_minute = target.get("asof_minute")
    try:
        cycle_id = cycle_id_from_asof_minute(asof_minute) if isinstance(asof_minute, str) else current_cycle_id()
    except ValueError:
        cycle_id = current_cycle_id()
    return Envelope(source=PRODUCER, cycle_id=cycle_id)


def _feature_stream(config: dict[str, Any]) -> str:
    return str(config.get("streams", {}).get("features", DEFAULT_FEATURE_STREAM))


def _latest_features(bus: Any, stream: str) -> dict[str, Any] | None:
    rows = bus.r.xrevrange(stream, count=1)
    if not rows:
        return None
    _, fields = rows[0]
    raw = fields.get("p") if isinstance(fields, dict) else None
    if not raw:
        return None
    payload = json.loads(raw)
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else None


def _real_money_execution_allowed(config: dict[str, Any]) -> bool:
    config_allows = bool(config.get("real_money_execution_allowed", False))
    env_allows = os.environ.get("V17_ALPHA_REAL_MONEY_EXECUTION_ALLOWED", "false").lower() == "true"
    confirmation = os.environ.get("V17_ALPHA_REAL_MONEY_CONFIRMATION") == "I_UNDERSTAND_REAL_MONEY_RISK"
    return config_allows and env_allows and confirmation


def _target_portfolio(
    *,
    asof_minute: str,
    universe: list[str],
    weights: dict[str, float],
    reason: str,
    confidence: float,
    config: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    gross = sum(abs(weight) for weight in weights.values())
    decision_action = "REBALANCE" if weights else "HOLD"
    merged_evidence = {
        "source": PRODUCER,
        "mode": str(config.get("mode", "live_canary")),
        "real_money_execution_allowed": _real_money_execution_allowed(config),
    }
    merged_evidence.update(evidence)
    return {
        "asof_minute": asof_minute,
        "universe": universe,
        "targets": [{"symbol": symbol, "weight": weight} for symbol, weight in sorted(weights.items())],
        "cash_weight": max(0.0, 1.0 - gross),
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "rationale": reason,
        "model": {"name": "v17_alpha", "version": _strategy_version(config)},
        "constraints_hint": _numeric_caps(_caps(config)),
        "decision_horizon": "15m",
        "major_cycle_id": f"v17-alpha-{asof_minute}",
        "decision_action": decision_action,
        "evidence": merged_evidence,
    }


def _candidate_weights(*, features: dict[str, Any], config: dict[str, Any]) -> dict[str, float]:
    universe = _universe(config, features)
    ret_15m = _mapping(features, "ret_15m")
    trend_15m = _mapping(features, "trend_15m")
    trend_1h = _mapping(features, "trend_1h")
    spread_bps = _mapping(features, "spread_bps")
    liquidity_score = _mapping(features, "liquidity_score")
    vol_1h = _mapping(features, "vol_1h")
    max_symbol = float(_caps(config).get("max_symbol_gross", 0.03))
    base_weight = min(max_symbol, 0.03)
    weights: dict[str, float] = {}
    for symbol in universe:
        ret = _float(ret_15m.get(symbol))
        t15 = _float(trend_15m.get(symbol))
        t1h = _float(trend_1h.get(symbol))
        spread = _float(spread_bps.get(symbol), default=999.0)
        liquidity = _float(liquidity_score.get(symbol))
        vol = _float(vol_1h.get(symbol))
        if spread > 2.0 or liquidity < 0.2 or vol <= 0.0 or vol > 0.03:
            continue
        if ret >= 0.0015 and t15 > 0 and t1h > 0:
            weights[symbol] = base_weight
        elif ret <= -0.0015 and t15 < 0 and t1h < 0:
            weights[symbol] = -base_weight
    return weights


def _apply_caps(weights: dict[str, float], caps: dict[str, Any]) -> dict[str, float]:
    max_symbol = float(caps.get("max_symbol_gross", 0.05))
    max_gross = float(caps.get("max_gross", 0.12))
    max_net = float(caps.get("max_net", max_gross))
    capped = {
        symbol: max(-max_symbol, min(max_symbol, float(weight)))
        for symbol, weight in weights.items()
        if abs(float(weight)) > 1e-12
    }
    gross = sum(abs(weight) for weight in capped.values())
    if gross > max_gross and gross > 0:
        scale = max_gross / gross
        capped = {symbol: weight * scale for symbol, weight in capped.items()}
    net = sum(capped.values())
    if abs(net) > max_net and capped:
        scale = max_net / abs(net)
        capped = {symbol: weight * scale for symbol, weight in capped.items()}
    return {symbol: round(weight, 12) for symbol, weight in capped.items() if abs(weight) > 1e-12}


def _universe(config: dict[str, Any], features: dict[str, Any] | None = None) -> list[str]:
    configured = config.get("universe")
    if isinstance(configured, list) and configured:
        return [str(symbol).upper() for symbol in configured]
    if features and isinstance(features.get("universe"), list):
        return [str(symbol).upper() for symbol in features["universe"]]
    return ["BTC", "ETH", "SOL", "ADA", "DOGE"]


def _asof_minute(features: dict[str, Any]) -> str:
    value = features.get("asof_minute")
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(features: dict[str, Any], key: str) -> dict[str, Any]:
    value = features.get(key)
    return value if isinstance(value, dict) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _numeric_caps(caps: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in caps.items():
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue
    return out


if __name__ == "__main__":
    run_forever()
