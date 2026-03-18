# tests/exchange/test_models.py
"""Tests for normalized exchange data models."""
import pytest

from shared.exchange.models import (
    NormalizedTicker,
    NormalizedPosition,
    NormalizedOpenOrder,
    NormalizedAccountState,
    NormalizedOrderResult,
    InstrumentSpec,
)


def test_normalized_ticker_fields():
    t = NormalizedTicker(
        symbol="BTC", mid_px=50000.0, bid=49999.0, ask=50001.0, spread_bps=0.4
    )
    assert t.symbol == "BTC"
    assert t.mid_px == 50000.0
    assert t.bid == 49999.0
    assert t.ask == 50001.0
    assert t.spread_bps == pytest.approx(0.4)


def test_normalized_position_side_long():
    p = NormalizedPosition(
        symbol="ETH",
        qty=1.0,
        entry_px=3000.0,
        mark_px=3100.0,
        unreal_pnl=100.0,
        side="long",
    )
    assert p.side == "long"
    assert p.qty > 0


def test_normalized_position_side_short():
    p = NormalizedPosition(
        symbol="BTC",
        qty=-0.1,
        entry_px=50000.0,
        mark_px=49000.0,
        unreal_pnl=100.0,
        side="short",
    )
    assert p.side == "short"
    assert p.qty < 0


def test_normalized_order_result_statuses():
    for status in ("ack", "filled", "rejected", "canceled"):
        r = NormalizedOrderResult(
            client_order_id="c1",
            exchange_order_id="e1",
            status=status,
            filled_qty=0.0,
            avg_px=0.0,
            fee_usd=0.0,
            latency_ms=10,
        )
        assert r.status == status


def test_instrument_spec_fields():
    spec = InstrumentSpec(
        symbol="BTC",
        min_qty=0.001,
        qty_step=0.001,
        price_tick=0.1,
        contract_size=1.0,
        maker_fee=0.0002,
        taker_fee=0.0004,
    )
    assert spec.symbol == "BTC"
    assert spec.min_qty == 0.001
    assert spec.qty_step == 0.001
    assert spec.price_tick == 0.1
    assert spec.contract_size == 1.0
    assert spec.maker_fee == 0.0002
    assert spec.taker_fee == 0.0004


def test_normalized_account_state_defaults():
    state = NormalizedAccountState(equity_usd=10000.0, cash_usd=8000.0)
    assert state.equity_usd == 10000.0
    assert state.cash_usd == 8000.0
    assert state.positions == {}
    assert state.open_orders == []


def test_normalized_account_state_with_data():
    pos = NormalizedPosition(
        symbol="BTC",
        qty=0.5,
        entry_px=50000.0,
        mark_px=51000.0,
        unreal_pnl=500.0,
        side="long",
    )
    order = NormalizedOpenOrder(
        exchange_order_id="e1",
        client_order_id="c1",
        symbol="BTC",
        side="buy",
        qty=0.1,
        limit_px=50000.0,
        filled_qty=0.0,
    )
    state = NormalizedAccountState(
        equity_usd=10000.0,
        cash_usd=8000.0,
        positions={"BTC": pos},
        open_orders=[order],
    )
    assert state.positions["BTC"] == pos
    assert len(state.open_orders) == 1


def test_normalized_open_order_fields():
    order = NormalizedOpenOrder(
        exchange_order_id="ex123",
        client_order_id="cl456",
        symbol="ETH",
        side="sell",
        qty=2.0,
        limit_px=3000.0,
        filled_qty=1.0,
    )
    assert order.exchange_order_id == "ex123"
    assert order.client_order_id == "cl456"
    assert order.symbol == "ETH"
    assert order.side == "sell"
    assert order.qty == 2.0
    assert order.limit_px == 3000.0
    assert order.filled_qty == 1.0