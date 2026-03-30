"""Lightweight Hyperliquid REST client for historical data."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx


HL_INFO_URL = "https://api.hyperliquid.xyz/info"


class HLClient:
    """Minimal client for Hyperliquid /info endpoint."""

    def __init__(self, base_url: str = HL_INFO_URL, rate_limit_delay: float = 0.2):
        self._url = base_url
        self._delay = rate_limit_delay
        self._http = httpx.Client(timeout=30.0)

    def _post(self, payload: dict) -> Any:
        """POST to /info with rate limiting."""
        resp = self._http.post(self._url, json=payload)
        resp.raise_for_status()
        time.sleep(self._delay)
        return resp.json()

    def candles(
        self, coin: str, interval: str, start_time: int, end_time: int
    ) -> list[dict]:
        """Fetch candle data. Times in milliseconds.

        Returns list of {t, T, o, h, l, c, v, n, i, s}.
        Max 5000 candles per request.
        """
        return self._post({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_time,
                "endTime": end_time,
            }
        })

    def candles_range(
        self, coin: str, interval: str, start_time: int, end_time: int
    ) -> list[dict]:
        """Fetch candles with auto-pagination (max 5000 per request).

        Walks backward from end_time to start_time.
        """
        all_candles = []
        cursor_end = end_time

        while cursor_end > start_time:
            batch = self.candles(coin, interval, start_time, cursor_end)
            if not batch:
                break

            all_candles.extend(batch)

            # Move cursor to before the earliest candle in this batch
            earliest_t = min(int(c["t"]) for c in batch)
            if earliest_t <= start_time:
                break
            cursor_end = earliest_t - 1

            # If we got fewer than 5000, we have all available data
            if len(batch) < 5000:
                break

        # Deduplicate and sort by open time
        seen = set()
        unique = []
        for c in all_candles:
            t = int(c["t"])
            if t not in seen and t >= start_time:
                seen.add(t)
                unique.append(c)

        return sorted(unique, key=lambda c: int(c["t"]))

    def funding_history(
        self, coin: str, start_time: int, end_time: int
    ) -> list[dict]:
        """Fetch funding rate history with auto-pagination (max 500 per page).

        Returns list of {coin, fundingRate, premium, time}.
        """
        all_records = []
        cursor_start = start_time

        while cursor_start < end_time:
            batch = self._post({
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cursor_start,
                "endTime": end_time,
            })
            if not batch:
                break

            all_records.extend(batch)

            # Advance cursor past the last record
            latest_t = max(int(r["time"]) for r in batch)
            if latest_t <= cursor_start:
                break
            cursor_start = latest_t + 1

            if len(batch) < 500:
                break

        # Deduplicate
        seen = set()
        unique = []
        for r in all_records:
            t = int(r["time"])
            if t not in seen:
                seen.add(t)
                unique.append(r)

        return sorted(unique, key=lambda r: int(r["time"]))

    def meta_and_asset_ctxs(self) -> tuple[list, list]:
        """Fetch current metadata and asset contexts (funding, OI, mark/oracle px)."""
        result = self._post({"type": "metaAndAssetCtxs"})
        return result[0], result[1]

    def close(self):
        self._http.close()
