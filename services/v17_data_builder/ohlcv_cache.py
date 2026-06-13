from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Sequence

BAR_INTERVAL = timedelta(hours=1)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "v17_shadow.json"

BarStatus = Literal["valid", "invalid"]


class OHLCVCacheError(ValueError):
    """Raised when OHLCV cache input cannot be normalized safely."""


class MissingSymbolError(OHLCVCacheError):
    """Raised when the requested symbol has no bars in the cache."""


class InsufficientHistoryError(OHLCVCacheError):
    """Raised when too few valid bars are available for the requested symbol."""


@dataclass(frozen=True)
class OHLCVBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    symbol: str
    status: BarStatus = "valid"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "symbol": self.symbol,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OHLCVReadResult:
    symbol: str
    bars: tuple[OHLCVBar, ...]
    status: BarStatus
    reason: str = ""

    @property
    def latest_bar(self) -> OHLCVBar | None:
        if not self.bars:
            return None
        return self.bars[-1]


def load_freshness_tolerance_seconds(config_path: str | Path = DEFAULT_CONFIG_PATH) -> int:
    with Path(config_path).open() as config_file:
        config = json.load(config_file)
    return int(config["freshness"]["max_completed_bar_lag_seconds"])


class OHLCVCacheReader:
    def __init__(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        freshness_tolerance_seconds: int | None = None,
    ) -> None:
        self.freshness_tolerance_seconds = (
            load_freshness_tolerance_seconds()
            if freshness_tolerance_seconds is None
            else int(freshness_tolerance_seconds)
        )
        self._bars_by_symbol: dict[str, list[OHLCVBar]] = {}
        for row in rows:
            bar = self._bar_from_row(row)
            self._bars_by_symbol.setdefault(bar.symbol, []).append(bar)
        for bars in self._bars_by_symbol.values():
            bars.sort(key=lambda bar: bar.timestamp)

    @classmethod
    def from_csv(
        cls,
        path: str | Path,
        *,
        freshness_tolerance_seconds: int | None = None,
    ) -> "OHLCVCacheReader":
        with Path(path).open(newline="") as csv_file:
            return cls(
                csv.DictReader(csv_file),
                freshness_tolerance_seconds=freshness_tolerance_seconds,
            )

    def read_symbol(
        self,
        symbol: str,
        *,
        as_of: datetime,
        min_history: int = 1,
        raise_on_invalid: bool = False,
    ) -> OHLCVReadResult:
        requested_symbol = symbol.strip().upper()
        if requested_symbol not in self._bars_by_symbol:
            return self._invalid_result(
                requested_symbol,
                "missing_symbol",
                raise_on_invalid,
                MissingSymbolError,
            )

        as_of_utc = self._parse_utc_datetime(as_of)
        eligible_bars = [
            self._classify_bar(bar, as_of_utc)
            for bar in self._bars_by_symbol[requested_symbol]
            if bar.timestamp <= as_of_utc
        ]
        valid_bars = tuple(bar for bar in eligible_bars if bar.status == "valid")
        if len(valid_bars) < min_history:
            return self._invalid_result(
                requested_symbol,
                "insufficient_history",
                raise_on_invalid,
                InsufficientHistoryError,
            )

        latest_bar = valid_bars[-1]
        if as_of_utc - latest_bar.timestamp > timedelta(
            seconds=self.freshness_tolerance_seconds
        ):
            return OHLCVReadResult(
                symbol=requested_symbol,
                bars=valid_bars,
                status="invalid",
                reason="stale_bar",
            )

        return OHLCVReadResult(symbol=requested_symbol, bars=valid_bars, status="valid")

    def read_many(
        self,
        symbols: Sequence[str],
        *,
        as_of: datetime,
        min_history: int = 1,
        raise_on_invalid: bool = False,
    ) -> dict[str, OHLCVReadResult]:
        return {
            symbol.strip().upper(): self.read_symbol(
                symbol,
                as_of=as_of,
                min_history=min_history,
                raise_on_invalid=raise_on_invalid,
            )
            for symbol in symbols
        }

    @classmethod
    def _bar_from_row(cls, row: Mapping[str, Any]) -> OHLCVBar:
        timestamp = cls._parse_utc_datetime(row.get("timestamp"))
        symbol = str(row.get("symbol", "")).strip().upper()
        if not symbol:
            raise OHLCVCacheError("symbol is required")
        return OHLCVBar(
            timestamp=timestamp,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            symbol=symbol,
        )

    @staticmethod
    def _parse_utc_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise OHLCVCacheError("timestamp must be an ISO datetime")

        if parsed.tzinfo is None:
            raise OHLCVCacheError("timestamp must be timezone-aware UTC")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _classify_bar(bar: OHLCVBar, as_of: datetime) -> OHLCVBar:
        if bar.timestamp > as_of:
            return OHLCVBar(**{**bar.__dict__, "status": "invalid", "reason": "future_bar"})
        if bar.timestamp + BAR_INTERVAL > as_of:
            return OHLCVBar(
                **{**bar.__dict__, "status": "invalid", "reason": "incomplete_bar"}
            )
        return bar

    @staticmethod
    def _invalid_result(
        symbol: str,
        reason: str,
        raise_on_invalid: bool,
        exc_type: type[OHLCVCacheError],
    ) -> OHLCVReadResult:
        if raise_on_invalid:
            raise exc_type(reason)
        return OHLCVReadResult(symbol=symbol, bars=(), status="invalid", reason=reason)
