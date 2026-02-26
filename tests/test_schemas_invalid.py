import unittest
from pydantic import ValidationError

from shared.schemas import (
    FeatureSnapshot1m,
    FeatureSnapshot15m,
    TargetPortfolio,
    TargetWeight,
    ExecutionReport,
)


class TestSchemaInvalidInputs(unittest.TestCase):
    def test_feature_snapshot_missing_mid_px_raises(self):
        with self.assertRaises(ValidationError):
            FeatureSnapshot1m(
                asof_minute="2026-02-18T16:00:00Z",
                universe=["BTC"],
                mid_px=None,  # type: ignore[arg-type]
            )

    def test_target_portfolio_invalid_symbol_raises(self):
        with self.assertRaises(ValidationError):
            TargetPortfolio(
                asof_minute="2026-02-18T16:00:00Z",
                universe=["BTC"],
                targets=[TargetWeight(symbol="ETH", weight=0.2)],
                cash_weight=0.8,
            )

    def test_execution_report_missing_status_raises(self):
        with self.assertRaises(ValidationError):
            ExecutionReport(
                client_order_id="c1",
                exchange_order_id="o1",
                symbol="BTC",
                status=None,  # type: ignore[arg-type]
            )

    def test_feature_snapshot_15m_invalid_funding_rate_raises(self):
        with self.assertRaises(ValidationError):
            FeatureSnapshot15m(
                asof_minute="2026-02-18T16:00:00Z",
                window_start_minute="2026-02-18T15:46:00Z",
                universe=["BTC"],
                mid_px={"BTC": 50000.0},
                funding_rate=None,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
