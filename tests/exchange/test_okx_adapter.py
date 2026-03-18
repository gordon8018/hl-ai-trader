# tests/exchange/test_okx_adapter.py
"""Unit tests for OKXAdapter."""
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.okx import OKXAdapter, SYMBOL_MAP
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
    """Create OKXAdapter with mocked SDK clients."""
    with patch("shared.exchange.adapters.okx.OKXAdapter._init_sdk_clients"):
        a = OKXAdapter(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
            base_url="https://www.okx.com",
        )
    return a


def test_symbol_mapping():
    """Test symbol mapping is correct."""
    assert SYMBOL_MAP["BTC"] == "BTC-USDT-SWAP"
    assert SYMBOL_MAP["ETH"] == "ETH-USDT-SWAP"


def test_get_instrument_id(adapter):
    """Test internal symbol to OKX instrument ID conversion."""
    assert adapter._get_instrument_id("BTC") == "BTC-USDT-SWAP"
    assert adapter._get_instrument_id("ETH") == "ETH-USDT-SWAP"
    # Unknown symbol should pass through
    assert adapter._get_instrument_id("UNKNOWN") == "UNKNOWN"


def test_get_internal_symbol(adapter):
    """Test OKX instrument ID to internal symbol conversion."""
    assert adapter._get_internal_symbol("BTC-USDT-SWAP") == "BTC"
    assert adapter._get_internal_symbol("ETH-USDT-SWAP") == "ETH"
    # Unknown instrument should pass through
    assert adapter._get_internal_symbol("UNKNOWN") == "UNKNOWN"


def test_get_all_mids_dry_run(adapter):
    """Test get_all_mids in DRY_RUN mode."""
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert "BTC" in result
    assert "ETH" in result
    assert result["BTC"] == 50000.0


def test_get_all_mids_with_mock(adapter, monkeypatch):
    """Test get_all_mids with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_market_api = MagicMock()
    mock_market_api.get_ticker.return_value = {
        "data": [{"bidPx": "50000.0", "askPx": "50010.0"}]
    }
    adapter._market_api = mock_market_api

    result = adapter.get_all_mids(["BTC"])
    assert result["BTC"] == pytest.approx(50005.0)


def test_get_l2_book_dry_run(adapter):
    """Test get_l2_book in DRY_RUN mode."""
    result = adapter.get_l2_book("BTC", depth=3)
    assert "bids" in result
    assert "asks" in result
    assert len(result["bids"]) == 3
    assert len(result["asks"]) == 3


def test_get_l2_book_with_mock(adapter, monkeypatch):
    """Test get_l2_book with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_market_api = MagicMock()
    mock_market_api.get_orderbook.return_value = {
        "data": [{
            "bids": [["50000.0", "1.5"], ["49999.0", "2.0"]],
            "asks": [["50010.0", "1.0"], ["50011.0", "0.5"]],
        }]
    }
    adapter._market_api = mock_market_api

    result = adapter.get_l2_book("BTC", depth=2)
    assert len(result["bids"]) == 2
    assert len(result["asks"]) == 2
    assert result["bids"][0] == ["50000.0", "1.5"]


def test_place_limit_dry_run(adapter):
    """Test place_limit in DRY_RUN mode."""
    result = adapter.place_limit("BTC", True, 0.01, 50000.0, client_order_id="test_cid")
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")
    assert result.client_order_id == "test_cid"


def test_place_limit_buy_with_mock(adapter, monkeypatch):
    """Test place_limit buy order with mocked API."""
    monkeypatch.setenv("DRY_RUN", "false")

    # Mock instrument spec
    adapter._instrument_cache["BTC"] = MagicMock(contract_size=10.0)

    mock_trade_api = MagicMock()
    mock_trade_api.place_order.return_value = {
        "data": [{"ordId": "123456", "sCode": "0"}]
    }
    adapter._trade_api = mock_trade_api

    result = adapter.place_limit("BTC", True, 10.0, 50000.0, client_order_id="test_buy")
    assert result.status == "ack"
    assert result.exchange_order_id == "123456"
    assert result.client_order_id == "test_buy"


def test_place_limit_sell_with_mock(adapter, monkeypatch):
    """Test place_limit sell order with mocked API."""
    monkeypatch.setenv("DRY_RUN", "false")

    # Mock instrument spec
    adapter._instrument_cache["BTC"] = MagicMock(contract_size=10.0)

    mock_trade_api = MagicMock()
    mock_trade_api.place_order.return_value = {
        "data": [{"ordId": "123457", "sCode": "0"}]
    }
    adapter._trade_api = mock_trade_api

    result = adapter.place_limit("BTC", False, 10.0, 51000.0, client_order_id="test_sell")
    assert result.status == "ack"
    assert result.exchange_order_id == "123457"


def test_place_limit_rejected(adapter, monkeypatch):
    """Test place_limit with rejection."""
    monkeypatch.setenv("DRY_RUN", "false")

    # Mock instrument spec
    adapter._instrument_cache["BTC"] = MagicMock(contract_size=10.0)

    mock_trade_api = MagicMock()
    mock_trade_api.place_order.return_value = {
        "data": [{"ordId": "", "sCode": "51001", "sMsg": "Insufficient balance"}]
    }
    adapter._trade_api = mock_trade_api

    from shared.exchange.base import OrderRejectedError
    with pytest.raises(OrderRejectedError):
        adapter.place_limit("BTC", True, 10.0, 50000.0)


def test_cancel_order_dry_run(adapter):
    """Test cancel_order in DRY_RUN mode."""
    assert adapter.cancel_order("BTC", "12345") is True


def test_cancel_order_success(adapter, monkeypatch):
    """Test cancel_order with success."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_trade_api = MagicMock()
    mock_trade_api.cancel_order.return_value = {
        "data": [{"sCode": "0"}]
    }
    adapter._trade_api = mock_trade_api

    assert adapter.cancel_order("BTC", "12345") is True


def test_get_order_status_dry_run(adapter):
    """Test get_order_status in DRY_RUN mode."""
    result = adapter.get_order_status("BTC", "12345")
    assert result.status == "ack"
    assert result.exchange_order_id == "12345"


def test_get_order_status_filled(adapter, monkeypatch):
    """Test get_order_status for filled order."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_trade_api = MagicMock()
    mock_trade_api.get_order.return_value = {
        "data": [{
            "ordId": "12345",
            "clOrdId": "test_cid",
            "state": "filled",
            "fillSz": "1.0",
            "avgPx": "50000.0",
            "fee": "5.0",
        }]
    }
    adapter._trade_api = mock_trade_api

    result = adapter.get_order_status("BTC", "12345")
    assert result.status == "filled"
    assert result.filled_qty == pytest.approx(1.0)
    assert result.avg_px == pytest.approx(50000.0)


def test_get_account_state_dry_run(adapter):
    """Test get_account_state in DRY_RUN mode."""
    result = adapter.get_account_state()
    assert isinstance(result, NormalizedAccountState)
    assert result.equity_usd == 10000.0
    assert result.cash_usd == 9000.0
    assert len(result.positions) == 0


def test_get_account_state_with_mock(adapter, monkeypatch):
    """Test get_account_state with mocked API responses."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_account_api = MagicMock()
    mock_account_api.get_account_balance.return_value = {
        "data": [{
            "details": [{"ccy": "USDT", "eq": "10000.0", "availBal": "9000.0"}]
        }]
    }
    mock_account_api.get_positions.return_value = {
        "data": [{
            "instId": "BTC-USDT-SWAP",
            "pos": "10",
            "posSide": "long",
            "avgPx": "50000.0",
            "markPx": "51000.0",
            "upl": "1000.0",
        }]
    }
    adapter._account_api = mock_account_api

    mock_trade_api = MagicMock()
    mock_trade_api.get_order_list.return_value = {"data": []}
    adapter._trade_api = mock_trade_api

    result = adapter.get_account_state()
    assert result.equity_usd == pytest.approx(10000.0)
    assert result.cash_usd == pytest.approx(9000.0)
    assert "BTC" in result.positions
    assert result.positions["BTC"].qty == pytest.approx(10.0)


def test_get_instrument_spec_dry_run(adapter):
    """Test get_instrument_spec in DRY_RUN mode."""
    result = adapter.get_instrument_spec("BTC")
    assert result.symbol == "BTC"
    assert result.min_qty == 1.0
    assert result.contract_size == 10.0


def test_get_instrument_spec_with_mock(adapter, monkeypatch):
    """Test get_instrument_spec with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_public_api = MagicMock()
    mock_public_api.get_instruments.return_value = {
        "data": [{
            "instId": "BTC-USDT-SWAP",
            "minSz": "1",
            "lotSz": "1",
            "tickSz": "0.1",
            "ctVal": "10",
        }]
    }
    adapter._public_api = mock_public_api

    result = adapter.get_instrument_spec("BTC")
    assert result.symbol == "BTC"
    assert result.min_qty == pytest.approx(1.0)
    assert result.qty_step == pytest.approx(1.0)
    assert result.price_tick == pytest.approx(0.1)
    assert result.contract_size == pytest.approx(10.0)

    # Test caching
    cached = adapter.get_instrument_spec("BTC")
    assert cached is result


def test_get_funding_rate_dry_run(adapter):
    """Test get_funding_rate in DRY_RUN mode."""
    result = adapter.get_funding_rate("BTC")
    assert result == 0.0001


def test_get_funding_rate_with_mock(adapter, monkeypatch):
    """Test get_funding_rate with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_public_api = MagicMock()
    mock_public_api.get_funding_rate.return_value = {
        "data": [{"fundingRate": "0.00015"}]
    }
    adapter._public_api = mock_public_api

    result = adapter.get_funding_rate("BTC")
    assert result == pytest.approx(0.00015)


def test_get_open_interest_dry_run(adapter):
    """Test get_open_interest in DRY_RUN mode."""
    result = adapter.get_open_interest("BTC")
    assert result == 1000000.0


def test_place_limit_uses_oneway_pos_side(monkeypatch):
    """place_limit should use posSide=net (one-way mode)."""
    from unittest.mock import MagicMock
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = OKXAdapter.__new__(OKXAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._passphrase = "p"
    adapter._base_url = "https://www.okx.com"
    adapter._instrument_cache = {}
    adapter._trade_api = MagicMock()
    adapter._trade_api.place_order.return_value = {
        "code": "0",
        "data": [{"ordId": "123456", "sCode": "0", "sMsg": ""}],
    }
    from shared.exchange.models import InstrumentSpec
    adapter._instrument_cache["BTC"] = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=0.01, maker_fee=0.0002, taker_fee=0.0004
    )
    adapter.place_limit("BTC", is_buy=True, qty=0.01, limit_px=50000.0)
    call_kwargs = adapter._trade_api.place_order.call_args[1]
    assert call_kwargs.get("posSide") == "net"


def test_get_open_interest_no_nested_get_all_mids(monkeypatch):
    """get_open_interest should NOT call get_all_mids (avoid nested HTTP calls)."""
    from unittest.mock import MagicMock
    monkeypatch.setenv("DRY_RUN", "false")
    adapter = OKXAdapter.__new__(OKXAdapter)
    adapter._api_key = "k"
    adapter._api_secret = "s"
    adapter._passphrase = "p"
    adapter._base_url = "https://www.okx.com"
    adapter._instrument_cache = {}
    adapter._public_api = MagicMock()
    adapter._market_api = MagicMock()

    from shared.exchange.models import InstrumentSpec
    adapter._instrument_cache["BTC"] = InstrumentSpec(
        symbol="BTC", min_qty=0.001, qty_step=0.001,
        price_tick=0.1, contract_size=0.01, maker_fee=0.0002, taker_fee=0.0004
    )
    adapter._public_api.get_open_interest.return_value = {
        "code": "0", "data": [{"oi": "1000"}]
    }
    adapter._market_api.get_ticker.return_value = {
        "code": "0", "data": [{"bidPx": "49990", "askPx": "50010"}]
    }

    result = adapter.get_open_interest("BTC")
    # market_api.get_ticker called directly, NOT via get_all_mids
    adapter._market_api.get_ticker.assert_called_once()
    assert result > 0