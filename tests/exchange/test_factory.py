# tests/exchange/test_factory.py
"""Tests for exchange factory function."""
import os
import pytest
from unittest.mock import patch, MagicMock


def test_factory_selects_hyperliquid(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("HL_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setenv("HL_ACCOUNT_ADDRESS", "0x" + "b" * 40)
    monkeypatch.setenv("HL_HTTP_URL", "https://api.hyperliquid.xyz")
    with patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter.__init__", return_value=None):
        from shared.exchange.factory import create_exchange_adapter
        adapter = create_exchange_adapter()
        from shared.exchange.adapters.hyperliquid import HyperliquidAdapter
        assert isinstance(adapter, HyperliquidAdapter)


def test_factory_selects_okx(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "okx")
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "pass")
    with patch("shared.exchange.adapters.okx.OKXAdapter.__init__", return_value=None):
        from importlib import reload
        import shared.exchange.factory as fac
        reload(fac)
        adapter = fac.create_exchange_adapter()
        from shared.exchange.adapters.okx import OKXAdapter
        assert isinstance(adapter, OKXAdapter)


def test_factory_selects_binance(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "binance")
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    with patch("shared.exchange.adapters.binance.BinanceAdapter.__init__", return_value=None):
        from importlib import reload
        import shared.exchange.factory as fac
        reload(fac)
        adapter = fac.create_exchange_adapter()
        from shared.exchange.adapters.binance import BinanceAdapter
        assert isinstance(adapter, BinanceAdapter)


def test_factory_raises_for_unknown(monkeypatch):
    monkeypatch.setenv("EXCHANGE", "deribit")
    from importlib import reload
    import shared.exchange.factory as fac
    reload(fac)
    with pytest.raises(ValueError, match="Unsupported exchange"):
        fac.create_exchange_adapter()