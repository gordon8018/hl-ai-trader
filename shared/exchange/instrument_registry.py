# shared/exchange/instrument_registry.py
"""Instrument specification cache with three-path lookup.

Lookup order: Redis cache -> Dynamic fetch -> Static fallback.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from shared.exchange.models import InstrumentSpec

logger = logging.getLogger(__name__)

_FALLBACK_PATH = Path(__file__).parent / "static_fallback.json"
_TTL = 3600  # seconds


def _load_fallback() -> dict:
    """Load static fallback data from JSON file."""
    with open(_FALLBACK_PATH) as f:
        return json.load(f)


class InstrumentRegistry:
    """
    Instrument specification cache.

    Lookup order: Redis cache -> Dynamic fetch -> Static fallback.
    fetch_fn is provided by each adapter with signature (symbol: str) -> InstrumentSpec.
    """

    def __init__(self, redis, exchange: str):
        self._redis = redis
        self._exchange = exchange
        self._fallback = _load_fallback()

    def _cache_key(self, symbol: str) -> str:
        return f"instrument:{self._exchange}:{symbol}"

    def get(self, symbol: str, fetch_fn: Callable[[str], InstrumentSpec]) -> InstrumentSpec:
        key = self._cache_key(symbol)

        # 1. Try Redis cache
        cached = self._redis.get(key)
        if cached:
            try:
                data = json.loads(cached)
                return InstrumentSpec(**data)
            except Exception:
                pass  # Corrupted cache, continue to next path

        # 2. Dynamic fetch
        try:
            spec = fetch_fn(symbol)
            self._redis.setex(key, _TTL, json.dumps(spec.__dict__))
            return spec
        except Exception as e:
            logger.warning(f"InstrumentRegistry: fetch failed for {self._exchange}/{symbol}: {e}, using fallback")

        # 3. Static fallback
        exchange_data = self._fallback.get(self._exchange, {})
        sym_data = exchange_data.get(symbol)
        if sym_data:
            return InstrumentSpec(symbol=symbol, **sym_data)

        from shared.exchange.base import InstrumentNotFoundError
        raise InstrumentNotFoundError(f"No instrument spec found for {self._exchange}/{symbol}")