from __future__ import annotations

from pathlib import Path

from services.v17_shadow.ledger import append_row
from services.v17_shadow.reporting import (
    V17ShadowReporter,
    build_daily_report,
    render_daily_report,
)


def config(*, dry_run: bool = True, target: str = "alpha.target.v17_shadow") -> dict:
    return {"dry_run": dry_run, "streams": {"target": target}}


def row(
    symbol: str,
    status: str,
    *,
    reason: str = "accepted",
    weight: float | str = 0.0,
    paper_pnl_usd: float | str | None = None,
    entry_time_utc: str = "2026-06-13T06:00:00Z",
) -> dict:
    data = {
        "event_id": f"event-{symbol}-{status}",
        "leg_id": f"event-{symbol}-{status}:leg:0",
        "entry_time_utc": entry_time_utc,
        "bar_close_time_utc": "2026-06-13T05:00:00Z",
        "symbol": symbol,
        "side": "LONG",
        "quality_score": 0.8,
        "quality_label": "Q1",
        "raw_leg_weight": weight,
        "final_leg_weight": weight,
        "status": status,
        "reason": reason,
        "event_gross_cap": 0.06,
        "strategy_version": "V17_shadow",
    }
    if paper_pnl_usd is not None:
        data["paper_pnl_usd"] = paper_pnl_usd
    return data


def test_shadow_report_normal_day_counts_exposure_pnl_and_text() -> None:
    rows = [
        row("BTC", "valid", weight="0.03", paper_pnl_usd="1.25"),
        row("ETH", "valid", weight="-0.02", paper_pnl_usd="-0.50"),
        row("SOL", "invalid", reason="quality_score_below_threshold"),
        row("DOGE", "blocked", reason="event_cap_exceeded"),
        row("ADA", "duplicate", reason="duplicate_event_leg"),
        row("XRP", "valid", entry_time_utc="2026-06-12T23:00:00Z", weight="0.05"),
    ]

    report = build_daily_report(rows, report_date="2026-06-13", config=config())
    text = report.to_text()

    assert report.signal_count == 5
    assert report.valid_count == 2
    assert report.invalid_count == 1
    assert report.blocked_count == 1
    assert report.duplicate_count == 1
    assert report.per_symbol_exposure == {"BTC": 0.03, "ETH": -0.02}
    assert report.paper_pnl == 0.75
    assert report.data_freshness_anomalies == ()
    assert report.missing_history_symbols == ()
    assert report.warnings == ()
    assert "V17 shadow 2026-06-13: signals=5 valid=2 invalid=1 blocked=1 duplicates=1" in text
    assert "Exposure: BTC=+0.0300, ETH=-0.0200" in text
    assert "Paper PnL: +0.75" in text
    assert "Warnings:" not in text


def test_shadow_report_no_signal_day_warns_for_no_events() -> None:
    report = build_daily_report([], report_date="2026-06-13", config=config())

    assert report.signal_count == 0
    assert report.per_symbol_exposure == {}
    assert report.paper_pnl is None
    assert report.warnings == ("no_events_processed",)
    assert "Exposure: none" in report.to_text()
    assert "Paper PnL: n/a" in report.to_text()
    assert "Warnings: no_events_processed" in report.to_text()


def test_shadow_report_anomalies_missing_history_and_duplicates() -> None:
    rows = [
        row("SOL", "invalid", reason="stale_completed_bar"),
        row("AVAX", "invalid", reason="insufficient_history"),
        row("ARB", "blocked", reason="missing_history"),
        row("BTC", "duplicate", reason="duplicate_event_leg"),
    ]

    report = build_daily_report(rows, report_date="2026-06-13", config=config())

    assert report.signal_count == 4
    assert report.invalid_count == 2
    assert report.blocked_count == 1
    assert report.duplicate_count == 1
    assert report.data_freshness_anomalies == ("SOL:stale_completed_bar",)
    assert report.missing_history_symbols == ("ARB", "AVAX")
    text = report.to_text()
    assert "Anomalies: SOL:stale_completed_bar" in text
    assert "Missing history: ARB; AVAX" in text


def test_shadow_report_warns_when_dryrun_bridge_disabled_or_misconfigured() -> None:
    disabled = build_daily_report([row("BTC", "valid", weight="0.01")], report_date="2026-06-13", config=config(dry_run=False))
    wrong_stream = build_daily_report(
        [row("BTC", "valid", weight="0.01")],
        report_date="2026-06-13",
        config=config(target="alpha.target"),
    )

    assert disabled.warnings == ("dry_run_bridge_disabled",)
    assert wrong_stream.warnings == ("dry_run_bridge_disabled",)


def test_render_daily_report_is_concise_ops_text() -> None:
    text = render_daily_report([row("BTC", "valid", weight="0.01")], report_date="2026-06-13", config=config())

    assert len(text.splitlines()) == 5
    assert text.startswith("V17 shadow 2026-06-13:")
    assert "Exposure: BTC=+0.0100" in text


def test_shadow_reporter_reads_configured_ledger_path(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.csv"
    btc_row = row("BTC", "valid", weight="0.02")
    append_row(ledger_path, btc_row)
    append_row(ledger_path, btc_row)

    reporter = V17ShadowReporter(config={**config(), "ledger": {"path": str(ledger_path)}}, ledger_path=ledger_path)
    report = reporter.build_daily_report("2026-06-13")

    assert report.signal_count == 2
    assert report.valid_count == 1
    assert report.duplicate_count == 1
    assert reporter.render_daily_report("2026-06-13").startswith("V17 shadow 2026-06-13:")
