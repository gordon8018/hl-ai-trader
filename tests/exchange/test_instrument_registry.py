# tests/exchange/test_instrument_registry.py
"""Tests for InstrumentRegistry with three-path lookup: Redis -> fetch -> fallback."""
import json
from unittest.mock import MagicMock, patch

import pytest

from shared.exchange.instrument_registry import InstrumentRegistry
from shared.exchange.models import InstrumentSpec


@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.get.return_value = None   # 默认缓存未命中
    return r


def test_fallback_when_redis_miss_and_fetch_fails(mock_redis):
    """Redis 未命中且动态拉取失败时，应返回静态兜底数据"""
    fetch_fn = MagicMock(side_effect=Exception("network error"))
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("BTC", fetch_fn=fetch_fn)
    assert isinstance(spec, InstrumentSpec)
    assert spec.symbol == "BTC"
    assert spec.taker_fee == pytest.approx(0.0004)


def test_cache_hit_returns_cached(mock_redis):
    """Redis 命中时直接返回缓存，不调用 fetch"""
    cached = InstrumentSpec("BTC", 0.001, 0.001, 0.1, 1.0, 0.0002, 0.0004)
    mock_redis.get.return_value = json.dumps(cached.__dict__)
    fetch_fn = MagicMock()
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("BTC", fetch_fn=fetch_fn)
    fetch_fn.assert_not_called()
    assert spec.symbol == "BTC"


def test_cache_miss_calls_fetch_and_writes_redis(mock_redis):
    """Redis 未命中时调用 fetch_fn，结果写入 Redis"""
    fetched = InstrumentSpec("ETH", 0.001, 0.001, 0.01, 1.0, 0.0002, 0.0004)
    fetch_fn = MagicMock(return_value=fetched)
    registry = InstrumentRegistry(redis=mock_redis, exchange="binance")
    spec = registry.get("ETH", fetch_fn=fetch_fn)
    fetch_fn.assert_called_once_with("ETH")
    mock_redis.setex.assert_called_once()
    assert spec.taker_fee == pytest.approx(0.0004)