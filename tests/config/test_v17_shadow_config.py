import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
TRADING_PARAMS_PATH = PROJECT_ROOT / "config" / "trading_params.json"


REQUIRED_STREAMS = {
    "features_1h": "v17.features.1h",
    "signals": "v17.signals",
    "shadow_ledger": "v17.shadow.ledger",
    "target": "alpha.target.v17_shadow",
    "live_approved": "risk.approved.v17_live",
    "live_reports": "exec.reports.v17_live",
}


def load_v17_shadow_config():
    with CONFIG_PATH.open() as f:
        return json.load(f)


def test_v17_shadow_config_safe_defaults():
    config = load_v17_shadow_config()

    assert config["enabled"] is False
    assert config["mode"] == "paper_shadow"
    assert config["dry_run"] is True
    assert config["real_money_execution_allowed"] is False


def test_v17_shadow_streams_are_isolated_from_production_alpha_target():
    config = load_v17_shadow_config()

    assert config["streams"] == REQUIRED_STREAMS
    assert "alpha.target" not in config["streams"].values()
    assert config["streams"]["target"] == "alpha.target.v17_shadow"


def test_v17_shadow_caps_are_conservative_and_separate_from_v9_prod():
    config = load_v17_shadow_config()
    caps = config["caps"]

    with TRADING_PARAMS_PATH.open() as f:
        trading_params = json.load(f)
    v9_prod = trading_params["versions"]["V9_prod"]

    assert caps["profile"] == "V17_shadow_conservative"
    assert caps["separate_from"] == "V9_prod"
    assert caps["max_gross"] < v9_prod["MAX_GROSS"]
    assert caps["max_net"] < v9_prod["MAX_NET"]
    assert caps["max_trades_per_day"] < v9_prod["MAX_TRADES_PER_DAY"]
    assert caps["max_event_gross"] <= caps["max_gross"]
    assert caps["max_symbol_gross"] <= caps["max_event_gross"]


def test_v17_shadow_requires_completed_fresh_1h_bars():
    config = load_v17_shadow_config()
    freshness = config["freshness"]

    assert freshness["bar_interval"] == "1h"
    assert freshness["completed_bars_only"] is True
    assert 3600 <= freshness["max_completed_bar_lag_seconds"] <= 7200


def test_v17_shadow_ledger_is_local_project_data_not_obsidian():
    config = load_v17_shadow_config()
    ledger_path = Path(config["ledger"]["path"])

    assert config["ledger"]["format"] == "csv"
    assert not ledger_path.is_absolute()
    assert ledger_path.parts[:2] == ("data", "v17_shadow")
    assert "Obsidian" not in ledger_path.parts
    assert "obsidian" not in str(ledger_path).lower()


def test_v17_shadow_config_contains_no_secret_fields_or_values():
    config_text = CONFIG_PATH.read_text().lower()

    forbidden_tokens = ("secret", "private_key", "api_key", "seed", "withdrawal")
    assert all(token not in config_text for token in forbidden_tokens)
