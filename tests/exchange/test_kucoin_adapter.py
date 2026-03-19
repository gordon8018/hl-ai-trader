# tests/exchange/test_kucoin_adapter.py
"""Unit tests for KucoinAdapter."""
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.kucoin import KucoinAdapter, SYMBOL_MAP
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
    """Create KucoinAdapter with mocked SDK client."""
    with patch("shared.exchange.adapters.kucoin.KucoinAdapter._init_sdk_client"):
        a = KucoinAdapter(
            api_key="test_key",
            api_secret="test_secret",
            passphrase="test_pass",
            base_url="https://api-futures.kucoin.com",
        )
    return a


def test_symbol_mapping():
    """Test symbol mapping is correct."""
    assert SYMBOL_MAP["BTC"] == "XBTUSDTM"  # Note: XBT not BTC
    assert SYMBOL_MAP["ETH"] == "ETHUSDTM"


def test_get_instrument_id(adapter):
    """Test internal symbol to KuCoin instrument ID conversion."""
    assert adapter._get_instrument_id("BTC") == "XBTUSDTM"
    assert adapter._get_instrument_id("ETH") == "ETHUSDTM"


def test_get_internal_symbol(adapter):
    """Test KuCoin instrument ID to internal symbol conversion."""
    assert adapter._get_internal_symbol("XBTUSDTM") == "BTC"
    assert adapter._get_internal_symbol("ETHUSDTM") == "ETH"


def test_get_all_mids_dry_run(adapter):
    """Test get_all_mids in DRY_RUN mode."""
    result = adapter.get_all_mids(["BTC", "ETH"])
    assert "BTC" in result
    assert "ETH" in result
    assert result["BTC"] == 50000.0


def test_get_l2_book_dry_run(adapter):
    """Test get_l2_book in DRY_RUN mode."""
    result = adapter.get_l2_book("BTC", depth=3)
    assert "bids" in result
    assert "asks" in result
    assert len(result["bids"]) == 3


def test_get_recent_trades_dry_run(adapter):
    """Test get_recent_trades in DRY_RUN mode."""
    result = adapter.get_recent_trades("BTC", limit=5)
    assert len(result) == 5
    assert "price" in result[0]


def test_get_funding_rate_dry_run(adapter):
    """Test get_funding_rate in DRY_RUN mode."""
    result = adapter.get_funding_rate("BTC")
    assert result == 0.0001


def test_get_open_interest_dry_run(adapter):
    """Test get_open_interest in DRY_RUN mode."""
    result = adapter.get_open_interest("BTC")
    assert result == 1000000.0


def test_place_limit_dry_run(adapter):
    """Test place_limit in DRY_RUN mode."""
    result = adapter.place_limit("BTC", True, 0.001, 50000.0)
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_kc_")


def test_place_limit_with_client_order_id(adapter):
    """Test place_limit preserves client_order_id."""
    result = adapter.place_limit("BTC", True, 0.001, 50000.0, client_order_id="my_order")
    assert result.client_order_id == "my_order"


def test_cancel_order_dry_run(adapter):
    """Test cancel_order in DRY_RUN mode."""
    result = adapter.cancel_order("BTC", "order123")
    assert result is True


def test_get_order_status_dry_run(adapter):
    """Test get_order_status in DRY_RUN mode."""
    result = adapter.get_order_status("BTC", "order123")
    assert isinstance(result, NormalizedOrderResult)
    assert result.exchange_order_id == "order123"


def test_get_account_state_dry_run(adapter):
    """Test get_account_state in DRY_RUN mode."""
    result = adapter.get_account_state()
    assert isinstance(result, NormalizedAccountState)
    assert result.equity_usd == 10000.0


def test_get_instrument_spec_dry_run(adapter):
    """Test get_instrument_spec in DRY_RUN mode."""
    from shared.exchange.models import InstrumentSpec
    result = adapter.get_instrument_spec("BTC")
    assert isinstance(result, InstrumentSpec)
    assert result.symbol == "BTC"


# Non-DRY_RUN tests with mocked client

def test_get_all_mids_with_mock(adapter, monkeypatch):
    """Test get_all_mids with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_ticker.return_value = {
        "data": {"bestBidPrice": "50000.0", "bestAskPrice": "50010.0"}
    }
    adapter._client = mock_client

    result = adapter.get_all_mids(["BTC"])
    assert result["BTC"] == pytest.approx(50005.0)


def test_place_limit_with_mock(adapter, monkeypatch):
    """Test place_limit with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_create_order.return_value = {
        "data": {"orderId": "kucoin_order_123"}
    }
    mock_client.futures_get_symbols.return_value = {
        "data": [{
            "symbol": "XBTUSDTM",
            "lotSize": "0.001",
            "tickSize": "0.1",
            "multiplier": "0.001",
        }]
    }
    adapter._client = mock_client

    result = adapter.place_limit("BTC", True, 0.001, 50000.0)
    assert result.exchange_order_id == "kucoin_order_123"


def test_cancel_order_with_mock(adapter, monkeypatch):
    """Test cancel_order with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_cancel_order.return_value = {"code": "200000"}
    adapter._client = mock_client

    assert adapter.cancel_order("BTC", "order123") is True


def test_get_order_status_with_mock(adapter, monkeypatch):
    """Test get_order_status with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_order.return_value = {
        "data": {
            "clientOid": "test_cid",
            "status": "filled",
            "dealSize": "1.0",
            "dealValue": "50000.0",
            "fee": "5.0",
        }
    }
    adapter._client = mock_client

    result = adapter.get_order_status("BTC", "order123")
    assert result.status == "filled"
    assert result.filled_qty == pytest.approx(1.0)
    assert result.avg_px == pytest.approx(50000.0)
    assert result.fee_usd == pytest.approx(5.0)


def test_get_order_status_unfilled(adapter, monkeypatch):
    """Test get_order_status when order is not filled (avg_px should be 0)."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_order.return_value = {
        "data": {
            "clientOid": "test_cid",
            "status": "active",
            "dealSize": "0",
            "dealValue": "0",
            "fee": "0",
        }
    }
    adapter._client = mock_client

    result = adapter.get_order_status("BTC", "order123")
    assert result.status == "ack"  # active -> ack
    assert result.filled_qty == 0.0
    assert result.avg_px == 0.0  # C2 fix: should be 0, not division error


def test_get_account_state_with_mock(adapter, monkeypatch):
    """Test get_account_state with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_account_detail.return_value = {
        "data": {
            "accountEquity": "10000.0",
            "availableBalance": "8000.0",
        }
    }
    mock_client.futures_get_positions.return_value = {"data": []}
    mock_client.futures_get_orders.return_value = {"data": []}
    adapter._client = mock_client

    result = adapter.get_account_state()
    assert result.equity_usd == pytest.approx(10000.0)
    assert result.cash_usd == pytest.approx(8000.0)


def test_get_account_state_fetches_active_orders(adapter, monkeypatch):
    """Test get_account_state passes status='active' to futures_get_orders."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_account_detail.return_value = {"data": {"accountEquity": "1000", "availableBalance": "800"}}
    mock_client.futures_get_positions.return_value = {"data": []}
    mock_client.futures_get_orders.return_value = {"data": []}
    adapter._client = mock_client

    adapter.get_account_state()

    # Verify status='active' was passed (not 'open')
    mock_client.futures_get_orders.assert_called_once_with(status="active")


def test_get_instrument_spec_with_mock(adapter, monkeypatch):
    """Test get_instrument_spec with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_symbols.return_value = {
        "data": [{
            "symbol": "XBTUSDTM",
            "lotSize": "0.001",
            "tickSize": "0.1",
            "multiplier": "0.001",
            "makerFeeRate": "0.0002",
            "takerFeeRate": "0.0006",
        }]
    }
    adapter._client = mock_client

    result = adapter.get_instrument_spec("BTC")
    assert result.symbol == "BTC"
    assert result.contract_size == pytest.approx(0.001)


def test_get_instrument_spec_not_found(adapter, monkeypatch):
    """Test get_instrument_spec raises InstrumentNotFoundError for unknown symbol."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_get_symbols.return_value = {"data": []}
    adapter._client = mock_client

    from shared.exchange.base import InstrumentNotFoundError
    with pytest.raises(InstrumentNotFoundError):
        adapter.get_instrument_spec("UNKNOWN_SYMBOL")


def test_place_limit_contracts_calculation(adapter, monkeypatch):
    """Test place_limit calculates contracts correctly from qty/contract_size."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_create_order.return_value = {"data": {"orderId": "test_order"}}
    mock_client.futures_get_symbols.return_value = {
        "data": [{
            "symbol": "XBTUSDTM",
            "lotSize": "0.001",
            "tickSize": "0.1",
            "multiplier": "0.01",  # contract_size = 0.01 BTC
        }]
    }
    adapter._client = mock_client

    # qty=0.05 BTC with contract_size=0.01 should give 5 contracts
    adapter.place_limit("BTC", True, 0.05, 50000.0)

    call_kwargs = mock_client.futures_create_order.call_args[1]
    assert call_kwargs["size"] == "5"


def test_cancel_order_with_cancelled_order_id(adapter, monkeypatch):
    """Test cancel_order success via cancelledOrderId in response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.futures_cancel_order.return_value = {
        "code": "200001",  # Not 200000
        "data": {"cancelledOrderId": "order123"}  # But has cancelledOrderId
    }
    adapter._client = mock_client

    assert adapter.cancel_order("BTC", "order123") is True


def test_cancel_order_no_exception_returns_true(adapter, monkeypatch):
    """Test cancel_order returns True if no exception (robust handling)."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    # Response without clear success indicators but no exception
    mock_client.futures_cancel_order.return_value = {"code": "unknown", "data": {}}
    adapter._client = mock_client

    # Should return True (no exception = assume success)
    assert adapter.cancel_order("BTC", "order123") is True