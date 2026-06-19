# tests/exchange/test_hyperliquid_adapter.py
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState
from shared.exchange.base import OrderRejectedError


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("HL_WS_ALL_MIDS_ENABLED", "false")
    with patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter._build_sdk_client", return_value=MagicMock()):
        a = HyperliquidAdapter(
            private_key="0x" + "a" * 64,
            account_address="0x" + "b" * 40,
            base_url="https://api.hyperliquid.xyz",
        )
    return a


def test_get_all_mids_returns_float_dict(adapter):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"BTC": "50000.5", "ETH": "3000.1"}
    mock_resp.raise_for_status = MagicMock()
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert result["BTC"] == pytest.approx(50000.5)
    assert result["ETH"] == pytest.approx(3000.1)


def test_place_limit_ack(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "false")
    mock_sdk = MagicMock()
    mock_sdk.order.return_value = {
        "status": "ok",
        "response": {"data": {"statuses": [{"resting": {"oid": 12345}}]}}
    }
    adapter._exchange_sdk = mock_sdk
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, tif="GTC", client_order_id="c1")
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id == "12345"


def test_place_limit_dry_run(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "true")
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="c2")
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")


def test_cancel_order_success(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "false")
    mock_sdk = MagicMock()
    mock_sdk.cancel.return_value = {"status": "ok"}
    adapter._exchange_sdk = mock_sdk
    assert adapter.cancel_order("BTC", "12345") is True


def test_get_account_state_returns_normalized(adapter):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "marginSummary": {"accountValue": "1000.0", "totalRawUsd": "900.0"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "0.1", "entryPx": "49000.0",
                          "unrealizedPnl": "100.0"}, "type": "oneWay"}
        ],
    }
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    # Mock spot fallback and open orders
    spot_resp = MagicMock()
    spot_resp.raise_for_status = MagicMock()
    spot_resp.json.return_value = {"tokenToAvailableAfterMaintenance": []}
    mock_resp2 = MagicMock()
    mock_resp2.raise_for_status = MagicMock()
    mock_resp2.json.return_value = []  # empty open orders
    adapter._http.post.side_effect = [mock_resp, spot_resp, mock_resp2]
    state = adapter.get_account_state()
    assert isinstance(state, NormalizedAccountState)
    assert state.equity_usd == pytest.approx(1000.0)
    assert "BTC" in state.positions
    assert state.positions["BTC"].qty == pytest.approx(0.1)


def test_get_account_state_uses_unified_spot_usdc_when_perps_equity_is_zero(adapter):
    clearinghouse_resp = MagicMock()
    clearinghouse_resp.raise_for_status = MagicMock()
    clearinghouse_resp.json.return_value = {
        "marginSummary": {"accountValue": "0.0", "totalRawUsd": "0.0"},
        "assetPositions": [],
    }
    spot_resp = MagicMock()
    spot_resp.raise_for_status = MagicMock()
    spot_resp.json.return_value = {
        "balances": [{"coin": "USDC", "token": 0, "total": "1401.89191", "hold": "0.0"}],
        "tokenToAvailableAfterMaintenance": [[0, "1401.89191"]],
    }
    open_orders_resp = MagicMock()
    open_orders_resp.raise_for_status = MagicMock()
    open_orders_resp.json.return_value = []
    adapter._http = MagicMock()
    adapter._http.post.side_effect = [clearinghouse_resp, spot_resp, open_orders_resp]

    state = adapter.get_account_state()

    assert state.equity_usd == pytest.approx(1401.89191)
    assert state.cash_usd == pytest.approx(1401.89191)
    assert state.positions == {}


def test_get_account_state_uses_unified_spot_usdc_when_perps_equity_is_dust(adapter):
    clearinghouse_resp = MagicMock()
    clearinghouse_resp.raise_for_status = MagicMock()
    clearinghouse_resp.json.return_value = {
        "marginSummary": {"accountValue": "1.58", "totalRawUsd": "1.58"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "-0.00012", "entryPx": "64000.0", "markPx": "64000.0", "unrealizedPnl": "0.0"}}
        ],
    }
    spot_resp = MagicMock()
    spot_resp.raise_for_status = MagicMock()
    spot_resp.json.return_value = {
        "balances": [{"coin": "USDC", "token": 0, "total": "1401.89191", "hold": "0.0"}],
        "tokenToAvailableAfterMaintenance": [[0, "1401.89191"]],
    }
    open_orders_resp = MagicMock()
    open_orders_resp.raise_for_status = MagicMock()
    open_orders_resp.json.return_value = []
    adapter._http = MagicMock()
    adapter._http.post.side_effect = [clearinghouse_resp, spot_resp, open_orders_resp]

    state = adapter.get_account_state()

    assert state.equity_usd == pytest.approx(1401.89191)
    assert state.cash_usd == pytest.approx(1401.89191)
    assert state.positions["BTC"].qty == pytest.approx(-0.00012)


def test_get_account_state_uses_unified_spot_usdc_when_perps_equity_low_but_cash_above_floor(adapter):
    clearinghouse_resp = MagicMock()
    clearinghouse_resp.raise_for_status = MagicMock()
    clearinghouse_resp.json.return_value = {
        "marginSummary": {"accountValue": "3.80", "totalRawUsd": "22.65"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "-0.00030", "entryPx": "63873.8", "markPx": "62819.5", "unrealizedPnl": "0.316"}}
        ],
    }
    spot_resp = MagicMock()
    spot_resp.raise_for_status = MagicMock()
    spot_resp.json.return_value = {
        "balances": [{"coin": "USDC", "token": 0, "total": "1396.79191", "hold": "0.0"}],
        "tokenToAvailableAfterMaintenance": [[0, "1396.79191"]],
    }
    open_orders_resp = MagicMock()
    open_orders_resp.raise_for_status = MagicMock()
    open_orders_resp.json.return_value = []
    adapter._http = MagicMock()
    adapter._http.post.side_effect = [clearinghouse_resp, spot_resp, open_orders_resp]

    state = adapter.get_account_state()

    assert state.equity_usd == pytest.approx(1396.79191)
    assert state.cash_usd == pytest.approx(1396.79191)
    assert state.positions["BTC"].qty == pytest.approx(-0.00030)


def test_get_account_state_uses_unified_spot_usdc_when_perps_equity_above_floor_after_fill(adapter):
    clearinghouse_resp = MagicMock()
    clearinghouse_resp.raise_for_status = MagicMock()
    clearinghouse_resp.json.return_value = {
        "marginSummary": {"accountValue": "12.36", "totalRawUsd": "74.14"},
        "assetPositions": [
            {"position": {"coin": "BTC", "szi": "-0.00099", "entryPx": "62414.0", "markPx": "62400.5", "unrealizedPnl": "0.01188"}}
        ],
    }
    spot_resp = MagicMock()
    spot_resp.raise_for_status = MagicMock()
    spot_resp.json.return_value = {
        "balances": [{"coin": "USDC", "token": 0, "total": "1393.509193", "hold": "0.0"}],
        "tokenToAvailableAfterMaintenance": [[0, "1393.509193"]],
    }
    open_orders_resp = MagicMock()
    open_orders_resp.raise_for_status = MagicMock()
    open_orders_resp.json.return_value = []
    adapter._http = MagicMock()
    adapter._http.post.side_effect = [clearinghouse_resp, spot_resp, open_orders_resp]

    state = adapter.get_account_state()

    assert state.equity_usd == pytest.approx(1393.509193)
    assert state.cash_usd == pytest.approx(1393.509193)
    assert state.positions["BTC"].qty == pytest.approx(-0.00099)


def test_get_l2_book_raw_returns_raw_response(adapter):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "levels": [[["50000", "1.0"]], [["50100", "0.5"]]],
        "coin": "BTC",
    }
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    result = adapter.get_l2_book_raw("BTC", n_sig_figs=5)
    assert result["coin"] == "BTC"
    assert len(result["levels"]) == 2


def test_get_meta_and_asset_ctxs_raw_returns_list(adapter):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
        [{"funding": "0.0001", "openInterest": "1000000"}, {"funding": "0.0002", "openInterest": "500000"}],
    ]
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    result = adapter.get_meta_and_asset_ctxs_raw()
    assert isinstance(result, list)
    assert len(result) == 2
    assert "universe" in result[0]


def test_http_url_converts_to_ws_url(adapter):
    assert adapter._derive_ws_url("https://api.hyperliquid.xyz") == "wss://api.hyperliquid.xyz/ws"
    assert adapter._derive_ws_url("https://api.hyperliquid-testnet.xyz") == "wss://api.hyperliquid-testnet.xyz/ws"


def test_handle_ws_all_mids_updates_cache(adapter):
    adapter._handle_ws_message({"channel": "allMids", "data": {"mids": {"BTC": "50001.0", "ETH": "3001.5"}}})
    assert adapter._ws_mid_cache["BTC"] == pytest.approx(50001.0)
    assert adapter._ws_mid_cache["ETH"] == pytest.approx(3001.5)


def test_get_all_mids_prefers_fresh_ws_cache(monkeypatch, adapter):
    monkeypatch.setenv("HL_WS_ALL_MIDS_ENABLED", "true")
    adapter._ws_thread = MagicMock()
    adapter._ws_thread.is_alive.return_value = True
    adapter._ws_mid_cache = {"BTC": 50100.0, "ETH": 3010.0}
    adapter._ws_mid_cache_ts = 100.0
    monkeypatch.setattr("shared.exchange.adapters.hyperliquid.time.time", lambda: 102.0)
    adapter._http = MagicMock()
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert result == {"BTC": 50100.0, "ETH": 3010.0}
    adapter._http.post.assert_not_called()


def test_get_all_mids_falls_back_to_rest_when_ws_cache_missing_requested_symbols(monkeypatch, adapter):
    monkeypatch.setenv("HL_WS_ALL_MIDS_ENABLED", "true")
    adapter._ws_thread = MagicMock()
    adapter._ws_thread.is_alive.return_value = True
    adapter._ws_mid_cache = {"BTC": 50100.0, "ETH": 3010.0, "SOL": 151.0}
    adapter._ws_mid_cache_ts = 100.0
    monkeypatch.setattr("shared.exchange.adapters.hyperliquid.time.time", lambda: 102.0)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "BTC": "50000.5",
        "ETH": "3000.1",
        "SOL": "150.2",
        "ADA": "0.61",
        "DOGE": "0.19",
    }
    mock_resp.raise_for_status = MagicMock()
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp

    result = adapter.get_all_mids(["BTC", "ETH", "SOL", "ADA", "DOGE"])

    assert result == {
        "BTC": pytest.approx(50000.5),
        "ETH": pytest.approx(3000.1),
        "SOL": pytest.approx(150.2),
        "ADA": pytest.approx(0.61),
        "DOGE": pytest.approx(0.19),
    }
    adapter._http.post.assert_called_once()


def test_get_all_mids_falls_back_to_rest_when_ws_cache_stale(monkeypatch, adapter):
    monkeypatch.setenv("HL_WS_ALL_MIDS_ENABLED", "true")
    adapter._ws_thread = MagicMock()
    adapter._ws_thread.is_alive.return_value = True
    adapter._ws_mid_cache = {"BTC": 50100.0}
    adapter._ws_mid_cache_ts = 100.0
    monkeypatch.setattr("shared.exchange.adapters.hyperliquid.time.time", lambda: 120.0)
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"BTC": "50000.5"}
    mock_resp.raise_for_status = MagicMock()
    adapter._http = MagicMock()
    adapter._http.post.return_value = mock_resp
    result = adapter.get_all_mids(["BTC"])
    assert result == {"BTC": pytest.approx(50000.5)}
    adapter._http.post.assert_called_once()



def test_place_limit_filled_extracts_exchange_order_id(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "false")
    mock_sdk = MagicMock()
    mock_sdk.order.return_value = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"filled": {"oid": 67890, "totalSz": "0.02", "avgPx": "50010.0"}}
                ]
            }
        },
    }
    adapter._exchange_sdk = mock_sdk

    result = adapter.place_limit("BTC", True, 0.02, 50010.0, tif="GTC", client_order_id="c-filled")

    assert result.status == "filled"
    assert result.exchange_order_id == "67890"
    assert result.filled_qty == pytest.approx(0.02)
    assert result.avg_px == pytest.approx(50010.0)


def test_place_limit_error_status_raises_rejected_instead_of_empty_ack(monkeypatch, adapter):
    monkeypatch.setenv("DRY_RUN", "false")
    mock_sdk = MagicMock()
    mock_sdk.order.return_value = {
        "status": "ok",
        "response": {
            "data": {
                "statuses": [
                    {"error": "Order must have minimum value of $10."}
                ]
            }
        },
    }
    adapter._exchange_sdk = mock_sdk

    with pytest.raises(OrderRejectedError, match="minimum value"):
        adapter.place_limit("BTC", True, 0.00001, 50000.0, tif="GTC", client_order_id="c-error")
