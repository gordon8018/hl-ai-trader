import importlib
import os
import sys
from datetime import datetime, timezone


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    mod_name = "services.market_data.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_utc_minute_key():
    mod = load_module()
    dt = datetime(2026, 2, 18, 16, 0, 59, tzinfo=timezone.utc)
    ts = dt.timestamp()
    assert mod.utc_minute_key(ts) == "2026-02-18T16:00:00Z"


def test_calc_return_positive():
    mod = load_module()
    assert abs(mod.calc_return(100.0, 110.0) - 0.1) < 1e-9


def test_calc_return_non_positive():
    mod = load_module()
    assert mod.calc_return(0.0, 110.0) == 0.0
    assert mod.calc_return(-1.0, 110.0) == 0.0
    assert mod.calc_return(100.0, -1.0) == 0.0


def test_collect_missing_symbols():
    mod = load_module()
    allmids = {"BTC": "50000"}
    universe = ["BTC", "ETH"]
    mids_hist = {"BTC": [(1, 50000.0)], "ETH": []}
    missing = mod.collect_missing_symbols(allmids, universe, mids_hist)
    assert missing == ["ETH"]


def test_parse_meta_and_asset_ctxs():
    mod = load_module()
    payload = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [
            {"funding": "0.0001", "openInterest": "1200000", "premium": "0.0002"},
            {"funding": "-0.0002", "openInterest": "800000", "premium": "-0.0001"},
        ],
    ]
    out = mod.parse_meta_and_asset_ctxs(payload)
    assert set(out.keys()) == {"BTC", "ETH"}
    assert out["BTC"]["funding"] == "0.0001"


def test_parse_meta_and_asset_ctxs_invalid():
    mod = load_module()
    assert mod.parse_meta_and_asset_ctxs({}) == {}


def test_parse_l2_metrics():
    mod = load_module()
    payload = {
        "coin": "BTC",
        "levels": [
            [
                {"px": "50000", "sz": "10"},
                {"px": "49990", "sz": "6"},
            ],
            [
                {"px": "50010", "sz": "8"},
                {"px": "50020", "sz": "4"},
            ],
        ],
    }
    out = mod.parse_l2_metrics(payload)
    assert out["spread_bps"] > 0
    assert "book_imbalance_l1" in out
    assert out["top_depth_usd"] > 0
    assert out["microprice"] > 0


def test_parse_l2_metrics_invalid():
    mod = load_module()
    assert mod.parse_l2_metrics({}) == {}


def test_aggregate_exec_feedback():
    mod = load_module()
    now = datetime(2026, 2, 18, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    entries = [
        {
            "env": {"ts": "2026-02-18T15:58:00Z"},
            "data": {"symbol": "BTC", "status": "ACK", "latency_ms": 100, "raw": {"slippage_bps": 1.2}},
        },
        {
            "env": {"ts": "2026-02-18T15:57:00Z"},
            "data": {"symbol": "BTC", "status": "REJECTED", "latency_ms": 300, "raw": {"slippage_bps": 2.0}},
        },
        {
            "env": {"ts": "2026-02-18T15:56:00Z"},
            "data": {"symbol": "ETH", "status": "ACK", "latency_ms": 200, "raw": {"slippage_bps": 1.0}},
        },
    ]
    rr, p95, slp = mod.aggregate_exec_feedback(entries, ["BTC", "ETH"], now, 900.0)
    assert abs(rr["BTC"] - 0.5) < 1e-9
    assert rr["ETH"] == 0.0
    assert p95["BTC"] >= 300.0
    assert slp["BTC"] >= 1.2


def test_aggregate_exec_feedback_window_excludes_old():
    mod = load_module()
    now = datetime(2026, 2, 18, 16, 0, 0, tzinfo=timezone.utc).timestamp()
    entries = [
        {
            "env": {"ts": "2026-02-18T15:30:00Z"},
            "data": {"symbol": "BTC", "status": "REJECTED", "latency_ms": 500, "raw": {"slippage_bps": 5.0}},
        }
    ]
    rr, p95, slp = mod.aggregate_exec_feedback(entries, ["BTC"], now, 900.0)
    assert rr["BTC"] == 0.0
    assert p95["BTC"] == 0.0
    assert slp["BTC"] == 0.0
