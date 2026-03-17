# tests/test_config_loader.py
import json
import os
import pytest
import tempfile


def write_config(path, data):
    with open(path, "w") as f:
        json.dump(data, f)


def test_load_config_returns_active_version_params():
    from services.ai_decision.config_loader import load_config
    cfg = {
        "active_version": "V9",
        "versions": {
            "V9": {"MAX_GROSS": 0.30, "CAP_ALT": 0.08}
        }
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
