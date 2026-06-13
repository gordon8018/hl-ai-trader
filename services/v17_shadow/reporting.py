from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.v17_shadow.ledger import read_rows
from services.v17_shadow.runner import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config

REPORT_STATUSES = ("valid", "invalid", "blocked", "duplicate")
ANOMALY_REASON_TOKENS = (
    "stale",
    "freshness",
    "missing_history",
    "insufficient_history",
    "missing-symbol",
    "missing_symbol",
    "no_history",
)


@dataclass(frozen=True)
class V17ShadowReport:
    report_date: date
    signal_count: int
    valid_count: int
    invalid_count: int
    blocked_count: int
    duplicate_count: int
    per_symbol_exposure: dict[str, float] = field(default_factory=dict)
    paper_pnl: float | None = None
    data_freshness_anomalies: tuple[str, ...] = field(default_factory=tuple)
    missing_history_symbols: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date.isoformat(),
            "signal_count": self.signal_count,
            "valid_count": self.valid_count,
            "invalid_count": self.invalid_count,
            "blocked_count": self.blocked_count,
            "duplicate_count": self.duplicate_count,
            "per_symbol_exposure": dict(self.per_symbol_exposure),
            "paper_pnl": self.paper_pnl,
            "data_freshness_anomalies": list(self.data_freshness_anomalies),
            "missing_history_symbols": list(self.missing_history_symbols),
            "warnings": list(self.warnings),
        }

    def to_text(self) -> str:
        lines = [
            f"V17 shadow {self.report_date.isoformat()}: signals={self.signal_count} "
            f"valid={self.valid_count} invalid={self.invalid_count} "
            f"blocked={self.blocked_count} duplicates={self.duplicate_count}"
        ]
        lines.append(f"Exposure: {_format_exposure(self.per_symbol_exposure)}")
        lines.append(f"Paper PnL: {_format_pnl(self.paper_pnl)}")
        lines.append(f"Anomalies: {_format_items(self.data_freshness_anomalies)}")
        lines.append(f"Missing history: {_format_items(self.missing_history_symbols)}")
        if self.warnings:
            lines.append(f"Warnings: {_format_items(self.warnings)}")
        return "\n".join(lines)


class V17ShadowReporter:
    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        ledger_path: str | Path | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.config = dict(config) if config is not None else load_config(self.config_path)
        self.ledger_path = Path(ledger_path) if ledger_path is not None else _ledger_path(self.config)

    def build_daily_report(self, report_date: date | str | None = None) -> V17ShadowReport:
        target_date = _coerce_report_date(report_date)
        rows = _rows_for_date(read_rows(self.ledger_path), target_date)
        return build_daily_report(rows, report_date=target_date, config=self.config)

    def render_daily_report(self, report_date: date | str | None = None) -> str:
        return self.build_daily_report(report_date).to_text()


def build_daily_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    report_date: date | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> V17ShadowReport:
    target_date = _coerce_report_date(report_date)
    scoped_rows = _rows_for_date(rows, target_date)
    status_counts = {status: 0 for status in REPORT_STATUSES}
    exposure: dict[str, float] = {}
    pnl_values: list[float] = []

    for row in scoped_rows:
        status = str(row.get("status") or "").lower()
        if status in status_counts:
            status_counts[status] += 1
        if status == "valid":
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                exposure[symbol] = exposure.get(symbol, 0.0) + _float_or_zero(row.get("final_leg_weight"))
        pnl = _extract_pnl(row)
        if pnl is not None:
            pnl_values.append(pnl)

    anomalies = _data_freshness_anomalies(scoped_rows)
    missing_history = _missing_history_symbols(scoped_rows)
    warnings = _warnings(scoped_rows, config)
    return V17ShadowReport(
        report_date=target_date,
        signal_count=len(scoped_rows),
        valid_count=status_counts["valid"],
        invalid_count=status_counts["invalid"],
        blocked_count=status_counts["blocked"],
        duplicate_count=status_counts["duplicate"],
        per_symbol_exposure={symbol: round(weight, 12) for symbol, weight in sorted(exposure.items())},
        paper_pnl=round(sum(pnl_values), 12) if pnl_values else None,
        data_freshness_anomalies=tuple(sorted(anomalies)),
        missing_history_symbols=tuple(sorted(missing_history)),
        warnings=tuple(warnings),
    )


def render_daily_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    report_date: date | str | None = None,
    config: Mapping[str, Any] | None = None,
) -> str:
    return build_daily_report(rows, report_date=report_date, config=config).to_text()


def generate_daily_report(
    *,
    report_date: date | str | None = None,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    ledger_path: str | Path | None = None,
) -> V17ShadowReport:
    return V17ShadowReporter(
        config=config,
        config_path=config_path,
        ledger_path=ledger_path,
    ).build_daily_report(report_date)


def _rows_for_date(rows: Sequence[Mapping[str, Any]], target_date: date) -> list[dict[str, Any]]:
    return [dict(row) for row in rows if _row_date(row) == target_date]


def _row_date(row: Mapping[str, Any]) -> date | None:
    for field_name in ("entry_time_utc", "bar_close_time_utc", "recorded_at_utc"):
        value = row.get(field_name)
        if value:
            return _parse_datetime(str(value)).date()
    return None


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_report_date(value: date | str | None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    return _parse_datetime(value).date()


def _ledger_path(config: Mapping[str, Any]) -> Path:
    ledger_config = config.get("ledger", {})
    if not isinstance(ledger_config, Mapping):
        raise ValueError("ledger config must be a mapping")
    path = Path(str(ledger_config.get("path", "data/v17_shadow/ledger.csv")))
    return path if path.is_absolute() else PROJECT_ROOT / path


def _extract_pnl(row: Mapping[str, Any]) -> float | None:
    for key in ("paper_pnl", "paper_pnl_usd", "pnl", "pnl_usd"):
        if key in row and row.get(key) not in (None, ""):
            return float(row[key])
    return None


def _data_freshness_anomalies(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    anomalies: set[str] = set()
    for row in rows:
        reason = str(row.get("reason") or "").lower()
        if "missing_history" in reason or "insufficient_history" in reason:
            continue
        if any(token in reason for token in ANOMALY_REASON_TOKENS):
            symbol = str(row.get("symbol") or "UNKNOWN").upper()
            anomalies.add(f"{symbol}:{row.get('reason')}")
    return anomalies


def _missing_history_symbols(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    symbols: set[str] = set()
    for row in rows:
        reason = str(row.get("reason") or "").lower()
        if "missing_history" in reason or "insufficient_history" in reason:
            symbol = str(row.get("symbol") or "UNKNOWN").upper()
            symbols.add(symbol)
    return symbols


def _warnings(rows: Sequence[Mapping[str, Any]], config: Mapping[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    if config is not None and not _bridge_enabled(config):
        warnings.append("dry_run_bridge_disabled")
    if not rows:
        warnings.append("no_events_processed")
    return warnings


def _bridge_enabled(config: Mapping[str, Any]) -> bool:
    streams = config.get("streams", {})
    if not isinstance(streams, Mapping):
        return False
    return config.get("dry_run") is True and str(streams.get("target", "")) == "alpha.target.v17_shadow"


def _float_or_zero(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def _format_exposure(exposure: Mapping[str, float]) -> str:
    if not exposure:
        return "none"
    return ", ".join(f"{symbol}={weight:+.4f}" for symbol, weight in exposure.items())


def _format_pnl(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def _format_items(items: Sequence[str]) -> str:
    return "none" if not items else "; ".join(items)
