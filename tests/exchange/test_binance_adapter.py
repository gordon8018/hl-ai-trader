# tests/exchange/test_binance_adapter.py
"""Unit tests for BinanceAdapter."""
import pytest
from unittest.mock import MagicMock, patch
from shared.exchange.adapters.binance import BinanceAdapter, SYMBOL_MAP
from shared.exchange.models import NormalizedOrderResult, NormalizedAccountState


@pytest.fixture
def adapter():
    """Create BinanceAdapter with mocked SDK client."""
    with patch("shared.exchange.adapters.binance.BinanceAdapter._init_sdk_client"):
        a = BinanceAdapter(
            api_key="test_key",
            api_secret="test_secret",
            base_url="https://fapi.binance.com",
        )
    return a


def test_symbol_mapping():
    """Test symbol mapping is correct."""
    assert SYMBOL_MAP["BTC"] == "BTCUSDT"
    assert SYMBOL_MAP["ETH"] == "ETHUSDT"


def test_get_symbol(adapter):
    """Test internal symbol to Binance symbol conversion."""
    assert adapter._get_symbol("BTC") == "BTCUSDT"
    assert adapter._get_symbol("ETH") == "ETHUSDT"
    # Unknown symbol should pass through
    assert adapter._get_symbol("UNKNOWN") == "UNKNOWN"


def test_get_internal_symbol(adapter):
    """Test Binance symbol to internal symbol conversion."""
    assert adapter._get_internal_symbol("BTCUSDT") == "BTC"
    assert adapter._get_internal_symbol("ETHUSDT") == "ETH"
    # Unknown symbol should pass through
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

    mock_client = MagicMock()
    mock_client.ticker_price.return_value = [
        {"symbol": "BTCUSDT", "price": "50000.0"},
        {"symbol": "ETHUSDT", "price": "3000.0"},
    ]
    mock_client.book_ticker.return_value = {
        "bidPrice": "49999.0",
        "askPrice": "50001.0",
    }
    adapter._client = mock_client

    result = adapter.get_all_mids(["BTC"])
    assert result["BTC"] == pytest.approx(50000.0)


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

    mock_client = MagicMock()
    mock_client.depth.return_value = {
        "bids": [["50000.0", "1.5"], ["49999.0", "2.0"]],
        "asks": [["50010.0", "1.0"], ["50011.0", "0.5"]],
    }
    adapter._client = mock_client

    result = adapter.get_l2_book("BTC", depth=2)
    assert len(result["bids"]) == 2
    assert len(result["asks"]) == 2
    assert result["bids"][0] == ["50000.0", "1.5"]


def test_place_limit_dry_run(adapter):
    """Test place_limit in DRY_RUN mode."""
    result = adapter.place_limit("BTC", True, 0.001, 50000.0, client_order_id="test_cid")
    assert isinstance(result, NormalizedOrderResult)
    assert result.status == "ack"
    assert result.exchange_order_id.startswith("dry_")
    assert result.client_order_id == "test_cid"


def test_place_limit_buy_with_mock(adapter, monkeypatch):
    """Test place_limit buy order with mocked API."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.new_order.return_value = {
        "orderId": 123456,
        "status": "NEW",
        "executedQty": "0",
        "avgPrice": "0",
    }
    adapter._client = mock_client

    result = adapter.place_limit("BTC", True, 0.001, 50000.0, client_order_id="test_buy")
    assert result.status == "ack"
    assert result.exchange_order_id == "123456"
    assert result.client_order_id == "test_buy"


def test_place_limit_sell_with_mock(adapter, monkeypatch):
    """Test place_limit sell order with mocked API."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.new_order.return_value = {
        "orderId": 123457,
        "status": "NEW",
        "executedQty": "0",
        "avgPrice": "0",
    }
    adapter._client = mock_client

    result = adapter.place_limit("BTC", False, 0.001, 51000.0, client_order_id="test_sell")
    assert result.status == "ack"
    assert result.exchange_order_id == "123457"


def test_place_limit_rejected(adapter, monkeypatch):
    """Test place_limit with rejection."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.new_order.side_effect = Exception("Insufficient balance")
    adapter._client = mock_client

    from shared.exchange.base import OrderRejectedError
    with pytest.raises(OrderRejectedError):
        adapter.place_limit("BTC", True, 0.001, 50000.0)


def test_cancel_order_dry_run(adapter):
    """Test cancel_order in DRY_RUN mode."""
    assert adapter.cancel_order("BTC", "12345") is True


def test_cancel_order_success(adapter, monkeypatch):
    """Test cancel_order with success."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.cancel_order.return_value = {"status": "CANCELED"}
    adapter._client = mock_client

    assert adapter.cancel_order("BTC", "12345") is True


def test_cancel_order_not_found(adapter, monkeypatch):
    """Test cancel_order when order doesn't exist."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.cancel_order.side_effect = Exception("Unknown order sent")
    adapter._client = mock_client

    # Should return True for non-existent orders
    assert adapter.cancel_order("BTC", "12345") is True


def test_get_order_status_dry_run(adapter):
    """Test get_order_status in DRY_RUN mode."""
    result = adapter.get_order_status("BTC", "12345")
    assert result.status == "ack"
    assert result.exchange_order_id == "12345"


def test_get_order_status_filled(adapter, monkeypatch):
    """Test get_order_status for filled order."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.query_order.return_value = {
        "orderId": 12345,
        "clientOrderId": "test_cid",
        "status": "FILLED",
        "executedQty": "0.001",
        "avgPrice": "50000.0",
    }
    adapter._client = mock_client

    result = adapter.get_order_status("BTC", "12345")
    assert result.status == "filled"
    assert result.filled_qty == pytest.approx(0.001)
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

    mock_client = MagicMock()
    mock_client.account.return_value = {
        "assets": [
            {"asset": "USDT", "walletBalance": "10000.0", "availableBalance": "9000.0"}
        ],
        "positions": [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.1",
                "positionSide": "LONG",
                "entryPrice": "50000.0",
                "markPrice": "51000.0",
                "unRealizedProfit": "100.0",
            }
        ],
    }
    mock_client.get_open_orders.return_value = []
    adapter._client = mock_client

    result = adapter.get_account_state()
    assert result.equity_usd == pytest.approx(10000.0)
    assert result.cash_usd == pytest.approx(9000.0)
    assert "BTC" in result.positions
    assert result.positions["BTC"].qty == pytest.approx(0.1)


def test_get_instrument_spec_dry_run(adapter):
    """Test get_instrument_spec in DRY_RUN mode."""
    result = adapter.get_instrument_spec("BTC")
    assert result.symbol == "BTC"
    assert result.min_qty == 0.001
    assert result.contract_size == 1.0


def test_get_instrument_spec_with_mock(adapter, monkeypatch):
    """Test get_instrument_spec with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                ],
            }
        ]
    }
    adapter._client = mock_client

    result = adapter.get_instrument_spec("BTC")
    assert result.symbol == "BTC"
    assert result.min_qty == pytest.approx(0.001)
    assert result.qty_step == pytest.approx(0.001)
    assert result.price_tick == pytest.approx(0.1)
    assert result.contract_size == pytest.approx(1.0)

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

    mock_client = MagicMock()
    mock_client.premium_index.return_value = [
        {"lastFundingRate": "0.00015"}
    ]
    adapter._client = mock_client

    result = adapter.get_funding_rate("BTC")
    assert result == pytest.approx(0.00015)


def test_get_open_interest_dry_run(adapter):
    """Test get_open_interest in DRY_RUN mode."""
    result = adapter.get_open_interest("BTC")
    assert result == 1000000.0


def test_get_open_interest_with_mock(adapter, monkeypatch):
    """Test get_open_interest with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.open_interest.return_value = {"openInterest": "5000000"}
    adapter._client = mock_client

    result = adapter.get_open_interest("BTC")
    assert result == pytest.approx(5000000.0)


def test_get_recent_trades_with_mock(adapter, monkeypatch):
    """Test get_recent_trades with mocked API response."""
    monkeypatch.setenv("DRY_RUN", "false")

    mock_client = MagicMock()
    mock_client.recent_trades.return_value = [
        {"price": "50000.0", "qty": "0.1", "isBuyerMaker": False, "time": 1234567890},
        {"price": "49999.0", "qty": "0.2", "isBuyerMaker": True, "time": 1234567891},
    ]
    adapter._client = mock_client

    result = adapter.get_recent_trades("BTC", limit=2)
    assert len(result) == 2
    assert result[0]["price"] == pytest.approx(50000.0)
    assert result[0]["side"] == "buy"
    assert result[1]["side"] == "sell"


def test_format_qty(adapter):
    """Test quantity formatting."""
    from shared.exchange.models import InstrumentSpec

    spec = InstrumentSpec(
        symbol="BTC",
        min_qty=0.001,
        qty_step=0.001,
        price_tick=0.1,
        contract_size=1.0,
        maker_fee=0.0002,
        taker_fee=0.0004,
    )

    assert adapter._format_qty(0.001234, spec) == "0.001"
    assert adapter._format_qty(1.0, spec) == "1"
    assert adapter._format_qty(0.5, spec) == "0.5"