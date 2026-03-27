# tests/test_config_loader.py
import json
import os
import pytest
import tempfile

from services.ai_decision.config_loader import REQUIRED_KEYS

# 最小合法配置：包含全部必填键
_MINIMAL_VALID_VERSION = {k: "placeholder" for k in REQUIRED_KEYS}
_MINIMAL_VALID_VERSION.update({"MAX_GROSS": 0.30, "CAP_ALT": 0.08})


def write_config(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def test_load_config_returns_active_version_params():
    from services.ai_decision.config_loader import load_config
    cfg = {
        "active_version": "V9",
        "versions": {"V9": _MINIMAL_VALID_VERSION}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        params = load_config(path)
        assert params["MAX_GROSS"] == 0.30
        assert params["CAP_ALT"] == 0.08
    finally:
        os.unlink(path)


def test_load_config_can_load_v9_recovery_profile():
    from services.ai_decision.config_loader import load_config

    recovery = dict(_MINIMAL_VALID_VERSION)
    recovery.update({
        "AI_SIGNAL_DELTA_THRESHOLD": 0.05,
        "MIN_NOTIONAL_USD": 50.0,
    })
    cfg = {
        "active_version": "V9_recovery",
        "versions": {"V9_recovery": recovery},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        params = load_config(path)
        assert params["AI_SIGNAL_DELTA_THRESHOLD"] == 0.05
        assert params["MIN_NOTIONAL_USD"] == 50.0
    finally:
        os.unlink(path)


def test_load_config_missing_required_key_raises():
    from services.ai_decision.config_loader import load_config
    # 缺少 AI_TURNOVER_CAP（必填键之一）
    incomplete = {k: "x" for k in REQUIRED_KEYS if k != "AI_TURNOVER_CAP"}
    cfg = {
        "active_version": "V9",
        "versions": {"V9": incomplete}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        with pytest.raises(KeyError, match="AI_TURNOVER_CAP"):
            load_config(path)
    finally:
        os.unlink(path)


def test_load_config_missing_version_raises():
    from services.ai_decision.config_loader import load_config
    cfg = {
        "active_version": "V99",
        "versions": {"V9": {"MAX_GROSS": 0.30}}
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    try:
        with pytest.raises(KeyError, match="V99"):
            load_config(path)
    finally:
        os.unlink(path)


def test_load_config_missing_file_raises():
    from services.ai_decision.config_loader import load_config
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/path/config.json")


def test_exchange_overrides_applied(tmp_path, monkeypatch):
    """Test that exchange_overrides are applied correctly based on EXCHANGE env var."""
    from services.ai_decision.config_loader import load_config
    config = {
        "active_version": "V9",
        "exchange_overrides": {
            "okx": {"MIN_NOTIONAL_USD": 5.0}
        },
        "versions": {
            "V9": {**_MINIMAL_VALID_VERSION, "MIN_NOTIONAL_USD": 50.0}
        }
    }
    p = tmp_path / "params.json"
    p.write_text(json.dumps(config))
    monkeypatch.setenv("EXCHANGE", "okx")
    result = load_config(str(p))
    assert result["MIN_NOTIONAL_USD"] == 5.0  # override 生效


def test_exchange_overrides_skips_notes(tmp_path, monkeypatch):
    """Test that exchange_overrides skips fields starting with underscore."""
    from services.ai_decision.config_loader import load_config
    config = {
        "active_version": "V9",
        "exchange_overrides": {
            "binance": {"_note": "should be skipped", "MIN_NOTIONAL_USD": 5.0}
        },
        "versions": {"V9": _MINIMAL_VALID_VERSION}
    }
    p = tmp_path / "params.json"
    p.write_text(json.dumps(config))
    monkeypatch.setenv("EXCHANGE", "binance")
    result = load_config(str(p))
    assert "_note" not in result
