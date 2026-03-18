# tests/exchange/test_hyperliquid_adapter.py
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
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
    # Mock the second call for open orders
    mock_resp2 = MagicMock()
    mock_resp2.raise_for_status = MagicMock()
    mock_resp2.json.return_value = []  # empty open orders
    adapter._http.post.side_effect = [mock_resp, mock_resp2]
    state = adapter.get_account_state()
    assert isinstance(state, NormalizedAccountState)
    assert state.equity_usd == pytest.approx(1000.0)
    assert "BTC" in state.positions
    assert state.positions["BTC"].qty == pytest.approx(0.1)


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