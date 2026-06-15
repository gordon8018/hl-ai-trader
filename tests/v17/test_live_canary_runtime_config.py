from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_live_canary.json"


def _run_import(module: str, extra_env: dict[str, str], expression: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "REDIS_URL": "redis://127.0.0.1:6381/0",
            "DRY_RUN": "false",
            "V17_LIVE_EXECUTION": "true",
            "V17_LIVE_REAL_MONEY_APPROVED": "true",
            "V17_LIVE_CANARY_CONFIG": "true",
        }
    )
    env.update(extra_env)
    result = subprocess.run(
        [sys.executable, "-c", f"import {module} as mod; print({expression})"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_live_canary_config_maps_execution_overrides() -> None:
    from shared.v17_live_canary_config import execution_overrides, load_config

    overrides = execution_overrides(load_config(CONFIG_PATH))

    assert overrides["UNIVERSE"] == "BTC,ETH,SOL,ADA,DOGE"
    assert overrides["POSITION_LEVERAGE"] == "5"
    assert overrides["V17_LIVE_STATE_MAX_AGE_SECONDS"] == "120"
    assert overrides["MAX_OPEN_ORDERS"] == "1"


def test_live_canary_config_maps_risk_overrides() -> None:
    from shared.v17_live_canary_config import load_config, risk_overrides

    overrides = risk_overrides(load_config(CONFIG_PATH))

    assert overrides["UNIVERSE"] == "BTC,ETH,SOL,ADA,DOGE"
    assert overrides["MAX_GROSS"] == "0.12"
    assert overrides["MAX_NET"] == "0.08"
    assert overrides["TURNOVER_CAP"] == "0.18"
    assert overrides["CAP_BTC_ETH"] == "0.05"
    assert overrides["CAP_ALT"] == "0.05"
    assert overrides["DAILY_LOSS_HALT_PCT"] == "0.05"
    assert overrides["DAILY_LOSS_REDUCE_ONLY_PCT"] == "0.025"


def test_execution_import_uses_canary_runtime_overrides() -> None:
    assert _run_import("services.execution.app", {}, "mod.UNIVERSE") == "['BTC', 'ETH', 'SOL', 'ADA', 'DOGE']"
    assert _run_import("services.execution.app", {}, "mod.POSITION_LEVERAGE") == "5.0"
    assert _run_import("services.execution.app", {}, "mod.V17_LIVE_STATE_MAX_AGE_SECONDS") == "120"


def test_risk_engine_import_uses_canary_runtime_overrides() -> None:
    assert _run_import("services.risk_engine.app", {}, "mod.UNIVERSE") == "['BTC', 'ETH', 'SOL', 'ADA', 'DOGE']"
    assert _run_import("services.risk_engine.app", {}, "mod.cap_for('BTC')") == "0.05"
    assert _run_import("services.risk_engine.app", {}, "mod.cap_for('SOL')") == "0.05"


def test_live_state_payload_accepts_envelope_ts_when_health_ts_missing(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6381/0")
    monkeypatch.setenv("DRY_RUN", "true")
    from services.execution.app import validate_live_state_payload

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = {
        "env": {"source": "portfolio_state", "cycle_id": "20260615T0000Z", "ts": now},
        "data": {
            "equity_usd": 100.0,
            "cash_usd": 100.0,
            "positions": {},
            "open_orders": [],
            "health": {},
        },
    }

    assert validate_live_state_payload(state, max_age_seconds=120) == (True, "ok")


def test_live_state_payload_rejects_explicit_unhealthy_reconcile(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6381/0")
    monkeypatch.setenv("DRY_RUN", "true")
    from services.execution.app import validate_live_state_payload

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = {
        "env": {"source": "portfolio_state", "cycle_id": "20260615T0000Z", "ts": now},
        "data": {
            "equity_usd": 100.0,
            "cash_usd": 100.0,
            "positions": {},
            "open_orders": [],
            "health": {"reconcile_ok": False},
        },
    }

    assert validate_live_state_payload(state, max_age_seconds=120) == (False, "reconcile_unhealthy")
