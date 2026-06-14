import importlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
APP_PATH = PROJECT_ROOT / "services" / "v17_alpha" / "app.py"


def load_config():
    return json.loads(CONFIG_PATH.read_text())


def test_v17_alpha_app_exists() -> None:
    assert APP_PATH.exists()


def test_v17_alpha_stream_out_is_shadow_target_only() -> None:
    app = importlib.import_module("services.v17_alpha.app")

    assert app.STREAM_OUT == "alpha.target.v17_shadow"
    assert app.STREAM_OUT != "alpha.target"


def test_v17_alpha_missing_inputs_return_noop_target() -> None:
    app = importlib.import_module("services.v17_alpha.app")

    missing_state = app.build_target_from_inputs(state=None, features={"BTC": {}})
    missing_features = app.build_target_from_inputs(state={"equity": 1000}, features=None)

    for target in (missing_state, missing_features):
        assert target["action"] == "skip"
        assert target["weights"] == {}
        assert target["reason"] in {"missing_state", "missing_market_features"}
        assert target["stream"] == "alpha.target.v17_shadow"
        assert target["metadata"]["strategy_version"] == "V17_shadow"


def test_v17_alpha_flat_target_carries_configured_caps() -> None:
    app = importlib.import_module("services.v17_alpha.app")
    config = load_config()

    target = app.build_flat_target("safety_default", config=config)

    assert target["action"] == "flat"
    assert target["weights"] == {}
    assert target["reason"] == "safety_default"
    assert target["stream"] == config["streams"]["target"]
    assert target["caps"] == config["caps"]
    assert target["caps"]["profile"] == "V17_shadow_conservative"


def test_v17_alpha_valid_inputs_still_default_to_flat_noop_with_caps() -> None:
    app = importlib.import_module("services.v17_alpha.app")
    config = load_config()

    target = app.build_target_from_inputs(
        state={"equity": 1000, "positions": {}},
        features={"BTC": {"completed_bar": True}},
        config=config,
    )

    assert target["action"] == "flat"
    assert target["weights"] == {}
    assert target["reason"] == "skeleton_noop"
    assert target["caps"] == config["caps"]
