import os
from unittest.mock import patch

from shared.exchange.factory import create_exchange_adapter


@patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter._build_sdk_client", return_value=None)
def test_factory_allows_empty_hyperliquid_private_key_in_dry_run(_mock_sdk, monkeypatch):
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("HL_ACCOUNT_ADDRESS", "0x" + "b" * 40)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    adapter = create_exchange_adapter()
    assert adapter._account.endswith("b" * 40)


@patch("shared.exchange.adapters.hyperliquid.HyperliquidAdapter._build_sdk_client", return_value=None)
def test_factory_requires_hyperliquid_private_key_when_live(_mock_sdk, monkeypatch):
    monkeypatch.setenv("EXCHANGE", "hyperliquid")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("HL_ACCOUNT_ADDRESS", "0x" + "b" * 40)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    try:
        create_exchange_adapter()
    except KeyError as exc:
        assert str(exc) == "'HL_PRIVATE_KEY'"
    else:
        raise AssertionError("expected missing live private key to raise")
