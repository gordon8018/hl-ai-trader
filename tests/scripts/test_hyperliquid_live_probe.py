import importlib
import os
import sys
from types import SimpleNamespace


def load_module():
    mod_name = "scripts.hyperliquid_live_probe"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def get_all_mids(self, symbols):
        self.calls.append(("get_all_mids", tuple(symbols)))
        return {sym: 100.0 for sym in symbols}

    def get_account_state(self):
        self.calls.append(("get_account_state", None))
        return SimpleNamespace(equity_usd=1000.0, cash_usd=900.0, positions={}, open_orders=[])

    def get_instrument_spec(self, symbol):
        self.calls.append(("get_instrument_spec", symbol))
        return SimpleNamespace(symbol=symbol, min_qty=0.001, qty_step=0.001, price_tick=0.1)

    def place_limit(self, symbol, is_buy, qty, limit_px, tif="GTC", reduce_only=False, client_order_id=None):
        self.calls.append(("place_limit", symbol, is_buy, qty, limit_px, tif, reduce_only, client_order_id))
        return SimpleNamespace(exchange_order_id="12345", status="ack", filled_qty=0.0, avg_px=0.0, fee_usd=0.0)

    def cancel_order(self, symbol, exchange_order_id):
        self.calls.append(("cancel_order", symbol, exchange_order_id))
        return True


def test_run_read_only_probe_collects_market_and_account():
    mod = load_module()
    adapter = FakeAdapter()
    out = mod.run_probe(adapter=adapter, symbols=["BTC", "ETH"], place_order=False)
    assert out["market"]["BTC"] == 100.0
    assert out["account"]["equity_usd"] == 1000.0
    assert ("get_account_state", None) in adapter.calls


def test_run_probe_places_and_cancels_limit_order():
    mod = load_module()
    adapter = FakeAdapter()
    out = mod.run_probe(
        adapter=adapter,
        symbols=["BTC"],
        place_order=True,
        symbol="BTC",
        side="buy",
        qty=0.01,
        limit_px=50000.0,
        cancel_after=True,
    )
    assert out["order"]["exchange_order_id"] == "12345"
    assert out["cancelled"] is True
    assert any(call[0] == "place_limit" for call in adapter.calls)
    assert any(call[0] == "cancel_order" for call in adapter.calls)
