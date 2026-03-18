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



def test_parse_l2_metrics():
    mod = load_module()
    payload = {
        "bids": [
            ["50000", "10"],
            ["49990", "6"],
            ["49980", "5"],
            ["49970", "4"],
            ["49960", "3"],
            ["49950", "2"],
            ["49940", "2"],
            ["49930", "2"],
            ["49920", "2"],
            ["49910", "2"],
        ],
        "asks": [
            ["50010", "8"],
            ["50020", "4"],
            ["50030", "4"],
            ["50040", "3"],
            ["50050", "3"],
            ["50060", "2"],
            ["50070", "2"],
            ["50080", "2"],
            ["50090", "2"],
            ["50100", "2"],
        ],
    }
    out = mod.parse_l2_metrics(payload)
    assert out["spread_bps"] > 0
    assert "book_imbalance_l1" in out
    assert "book_imbalance_l10" in out
    assert out["top_depth_usd"] > 0
    assert out["top_depth_usd_l10"] >= out["top_depth_usd"]
    assert out["microprice"] > 0
    assert out["bid_slope"] >= 0
    assert out["ask_slope"] >= 0
    assert "queue_imbalance_l1" in out


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


def test_rsi_from_hist_basic():
    mod = load_module()
    hist = [(1, 100.0), (2, 101.0), (3, 102.0), (4, 101.5), (5, 103.0), (6, 104.0)]
    val = mod.rsi_from_hist(hist, now_ts=6, period=3)
    assert 0.0 <= val <= 100.0


def test_parse_trade_metrics():
    mod = load_module()
    now = 1_700_000_000.0
    trades = [
        {"time": now * 1000, "sz": "1.0", "side": "B"},
        {"time": now * 1000, "sz": "2.0", "side": "A"},
    ]
    out = mod.parse_trade_metrics(trades, now, 60.0)
    assert out["trade_volume_1m"] == 3.0
    assert out["trade_count_1m"] == 2.0
    assert out["aggr_delta_1m"] == -1.0


def test_parse_l2_metrics_standard_format():
    """parse_l2_metrics should handle standard [[px, sz], ...] format."""
    mod = load_module()
    book = {
        "bids": [["50000.0", "1.5"], ["49990.0", "2.0"], ["49980.0", "3.0"],
                 ["49970.0", "1.0"], ["49960.0", "0.5"]],
        "asks": [["50010.0", "1.0"], ["50020.0", "2.5"], ["50030.0", "1.5"],
                 ["50040.0", "0.8"], ["50050.0", "1.2"]],
    }
    metrics = mod.parse_l2_metrics(book)
    assert "spread_bps" in metrics
    assert metrics["spread_bps"] > 0
    assert "book_imbalance_l1" in metrics
    assert "microprice" in metrics
    assert abs(metrics["microprice"] - 50000.0) < 100


def test_parse_l2_metrics_empty_book():
    mod = load_module()
    assert mod.parse_l2_metrics({}) == {}
    assert mod.parse_l2_metrics({"bids": [], "asks": []}) == {}
