import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from shared.schemas import Envelope, TargetPortfolio


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
APP_PATH = PROJECT_ROOT / "services" / "v17_alpha" / "app.py"


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def test_v17_alpha_app_exists() -> None:
    assert APP_PATH.exists()


def test_v17_alpha_default_stream_out_is_shadow_target() -> None:
    app = importlib.import_module("services.v17_alpha.app")

    assert app.STREAM_OUT == "alpha.target.v17_shadow"
    assert app.LIVE_STREAM_OUT == "alpha.target.v17_live"
    assert app.STREAM_OUT != "alpha.target"
    assert app.LIVE_STREAM_OUT != "alpha.target"


def test_v17_alpha_missing_inputs_return_noop_target() -> None:
    app = importlib.import_module("services.v17_alpha.app")

    missing_state = app.build_target_from_inputs(state=None, features={"BTC": {}})
    missing_features = app.build_target_from_inputs(state={"equity": 1000}, features=None)

    for target in (missing_state, missing_features):
        parsed = TargetPortfolio(**target)
        assert parsed.decision_action == "HOLD"
        assert parsed.targets == []
        assert parsed.rationale in {"missing_state", "missing_market_features"}
        assert parsed.model["version"] == "V17_live_canary"


def test_v17_alpha_flat_target_carries_configured_caps() -> None:
    app = importlib.import_module("services.v17_alpha.app")
    config = load_config()

    target = app.build_flat_target("safety_default", config=config)

    parsed = TargetPortfolio(**target)
    assert parsed.decision_action == "HOLD"
    assert parsed.targets == []
    assert parsed.rationale == "safety_default"
    assert parsed.constraints_hint["max_gross"] == config["caps"]["max_gross"]


def test_v17_alpha_valid_inputs_generate_nonzero_signal_with_caps() -> None:
    app = importlib.import_module("services.v17_alpha.app")
    config = {
        "enabled": True,
        "mode": "live_canary",
        "strategy_version": "V17_live_canary",
        "streams": {"target": "alpha.target.v17_shadow", "features": "md.features.15m"},
        "caps": {"max_gross": 0.12, "max_net": 0.08, "max_symbol_gross": 0.05},
        "universe": ["BTC", "ETH", "SOL"],
    }

    target = app.build_target_from_inputs(
        state={"equity": 1000, "positions": {}},
        features={
            "asof_minute": "2026-06-16T22:26:00Z",
            "universe": ["BTC", "ETH", "SOL"],
            "ret_15m": {"BTC": 0.004, "ETH": -0.003, "SOL": 0.0001},
            "trend_15m": {"BTC": 1.0, "ETH": -1.0, "SOL": 1.0},
            "trend_1h": {"BTC": 1.0, "ETH": -1.0, "SOL": -1.0},
            "spread_bps": {"BTC": 0.2, "ETH": 0.3, "SOL": 0.2},
            "liquidity_score": {"BTC": 1.0, "ETH": 1.0, "SOL": 1.0},
            "vol_1h": {"BTC": 0.003, "ETH": 0.004, "SOL": 0.004},
        },
        config=config,
    )

    parsed = TargetPortfolio(**target)
    weights = {item.symbol: item.weight for item in parsed.targets}
    assert parsed.decision_action == "REBALANCE"
    assert weights["BTC"] > 0
    assert weights["ETH"] < 0
    assert "SOL" not in weights
    assert sum(abs(weight) for weight in weights.values()) <= config["caps"]["max_gross"]
    assert max(abs(weight) for weight in weights.values()) <= config["caps"]["max_symbol_gross"]
    assert abs(sum(weights.values())) <= config["caps"]["max_net"]
    assert parsed.evidence["real_money_execution_allowed"] is False


class FakeBus:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.r = FakeRedis()

    def get_json(self, key: str) -> dict[str, Any] | None:
        assert key == "latest.state.snapshot"
        return {
            "env": {"ts": "2026-06-16T00:00:00Z"},
            "data": {
                "positions": {
                    "BTC": {"mark_px": 100.0},
                    "ETH": {"mark_px": 10.0},
                },
                "health": {"reconcile_ok": True},
            },
        }

    def xadd_json(self, stream: str, obj: dict[str, Any], maxlen: int = 20000) -> str:
        self.writes.append((stream, obj))
        return "1-0"


class FakeRedis:
    def xrevrange(self, stream: str, count: int = 1):
        assert stream == "md.features.15m"
        return [
            (
                "1-0",
                {
                    "p": json.dumps(
                        {
                            "data": {
                                "mid_px": {"BTC": 100.0, "ETH": 10.0},
                                "ret_1m": {"BTC": 0.0, "ETH": 0.0},
                            }
                        }
                    )
                },
            )
        ]


def test_live_canary_config_keeps_real_money_disabled() -> None:
    config = json.loads((PROJECT_ROOT / "config" / "v17_live_canary.json").read_text())
    assert config["enabled"] is True
    assert config["real_money_execution_allowed"] is False
    assert config["requires_manual_confirmation"] is True


def test_v17_alpha_run_once_writes_flat_target_to_canary_stream() -> None:
    app = importlib.import_module("services.v17_alpha.app")
    bus = FakeBus()
    config = {
        "enabled": True,
        "mode": "live_canary",
        "strategy_version": "V17_live_canary",
        "streams": {"target": "alpha.target.v17_shadow", "features": "md.features.15m"},
        "caps": {"max_gross": 0.12},
        "universe": ["BTC", "ETH"],
    }

    result = app.run_once(config=config, bus=bus)

    assert result["status"] == "written"
    assert result["stream"] == "alpha.target.v17_shadow"
    assert len(bus.writes) == 1
    stream, payload = bus.writes[0]
    assert stream == "alpha.target.v17_shadow"
    env = Envelope(**payload["env"])
    assert env.source == "services.v17_alpha.app"
    assert env.cycle_id
    parsed = TargetPortfolio(**payload["data"])
    assert parsed.decision_action in {"HOLD", "REBALANCE"}
    assert parsed.model["version"] == "V17_live_canary"
    assert parsed.evidence["real_money_execution_allowed"] is False


def test_v17_alpha_rejects_live_target_stream_without_explicit_approval(monkeypatch) -> None:
    app = importlib.import_module("services.v17_alpha.app")
    bus = FakeBus()
    config = {
        "enabled": True,
        "mode": "live_canary",
        "strategy_version": "V17_live_canary",
        "streams": {"target": "alpha.target.v17_live", "features": "md.features.15m"},
        "caps": {"max_gross": 0.12},
        "universe": ["BTC", "ETH"],
    }
    monkeypatch.delenv("V17_ALPHA_LIVE_TARGET_APPROVED", raising=False)
    monkeypatch.delenv("V17_ALPHA_TARGET_STREAM", raising=False)

    with pytest.raises(RuntimeError, match="explicit live target approval"):
        app.run_once(config=config, bus=bus)

    assert bus.writes == []


def test_v17_alpha_can_write_live_target_with_explicit_runtime_gate(monkeypatch) -> None:
    app = importlib.import_module("services.v17_alpha.app")
    bus = FakeBus()
    config = {
        "enabled": True,
        "mode": "live_canary",
        "strategy_version": "V17_live_canary",
        "streams": {"target": "alpha.target.v17_live", "features": "md.features.15m"},
        "caps": {"max_gross": 0.12},
        "universe": ["BTC", "ETH"],
    }
    monkeypatch.setenv("V17_ALPHA_TARGET_STREAM", "alpha.target.v17_live")
    monkeypatch.setenv("V17_ALPHA_LIVE_TARGET_APPROVED", "true")
    monkeypatch.delenv("V17_ALPHA_REAL_MONEY_EXECUTION_ALLOWED", raising=False)
    monkeypatch.delenv("V17_ALPHA_REAL_MONEY_CONFIRMATION", raising=False)

    result = app.run_once(config=config, bus=bus)

    assert result["status"] == "written"
    assert result["stream"] == "alpha.target.v17_live"
    assert len(bus.writes) == 1
    stream, payload = bus.writes[0]
    assert stream == "alpha.target.v17_live"
    parsed = TargetPortfolio(**payload["data"])
    assert parsed.model["version"] == "V17_live_canary"
    assert parsed.evidence["real_money_execution_allowed"] is False


def test_v17_alpha_real_money_evidence_requires_second_runtime_gate(monkeypatch) -> None:
    app = importlib.import_module("services.v17_alpha.app")
    config = {
        "enabled": True,
        "mode": "live_canary",
        "strategy_version": "V17_live_canary",
        "streams": {"target": "alpha.target.v17_live", "features": "md.features.15m"},
        "caps": {"max_gross": 0.12, "max_net": 0.08, "max_symbol_gross": 0.05},
        "universe": ["BTC", "ETH", "SOL"],
        "real_money_execution_allowed": True,
    }
    features = {
        "asof_minute": "2026-06-16T22:26:00Z",
        "universe": ["BTC", "ETH", "SOL"],
        "ret_15m": {"BTC": 0.004, "ETH": -0.003, "SOL": 0.0001},
        "trend_15m": {"BTC": 1.0, "ETH": -1.0, "SOL": 1.0},
        "trend_1h": {"BTC": 1.0, "ETH": -1.0, "SOL": -1.0},
        "spread_bps": {"BTC": 0.2, "ETH": 0.3, "SOL": 0.2},
        "liquidity_score": {"BTC": 1.0, "ETH": 1.0, "SOL": 1.0},
        "vol_1h": {"BTC": 0.003, "ETH": 0.004, "SOL": 0.004},
    }
    monkeypatch.setenv("V17_ALPHA_REAL_MONEY_EXECUTION_ALLOWED", "true")
    monkeypatch.setenv("V17_ALPHA_REAL_MONEY_CONFIRMATION", "I_UNDERSTAND_REAL_MONEY_RISK")

    target = app.build_target_from_inputs(
        state={"equity": 1000, "positions": {}},
        features=features,
        config=config,
    )

    parsed = TargetPortfolio(**target)
    assert parsed.decision_action == "REBALANCE"
    assert parsed.targets
    assert parsed.evidence["real_money_execution_allowed"] is True
