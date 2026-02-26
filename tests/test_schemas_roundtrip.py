import json
import unittest

from shared.schemas import (
    FeatureSnapshot1m,
    FeatureSnapshot15m,
    StateSnapshot,
    Position,
    OpenOrder,
    TargetPortfolio,
    TargetWeight,
    ApprovedTargetPortfolio,
    Rejection,
    ExecutionPlan,
    SliceOrder,
    ExecutionLimits,
    OrderIntent,
    ExecutionReport,
)


class TestSchemaRoundTrip(unittest.TestCase):
    def _round_trip(self, model_cls, instance):
        payload = instance.model_dump()
        encoded = json.dumps(payload)
        decoded = json.loads(encoded)
        rebuilt = model_cls(**decoded)
        self.assertEqual(payload, rebuilt.model_dump())

    def test_feature_snapshot_round_trip(self):
        fs = FeatureSnapshot1m(
            asof_minute="2026-02-18T16:00:00Z",
            universe=["BTC", "ETH"],
            mid_px={"BTC": 50000.0, "ETH": 3000.0},
            ret_1m={"BTC": 0.001},
            ret_5m={"BTC": 0.002},
            ret_1h={"BTC": 0.01},
            vol_1h={"BTC": 0.02},
        )
        self._round_trip(FeatureSnapshot1m, fs)

    def test_feature_snapshot_15m_round_trip(self):
        fs = FeatureSnapshot15m(
            asof_minute="2026-02-18T16:00:00Z",
            window_start_minute="2026-02-18T15:46:00Z",
            universe=["BTC", "ETH"],
            mid_px={"BTC": 50000.0, "ETH": 3000.0},
            funding_rate={"BTC": 0.0001, "ETH": -0.0002},
            next_funding_ts={"BTC": "2026-02-18T16:00:00Z", "ETH": "2026-02-18T16:00:00Z"},
            basis_bps={"BTC": 2.5, "ETH": 1.2},
            open_interest={"BTC": 1500000000.0, "ETH": 800000000.0},
            oi_change_15m={"BTC": 0.03, "ETH": -0.01},
            ret_15m={"BTC": 0.005, "ETH": 0.004},
            ret_30m={"BTC": 0.008, "ETH": 0.007},
            ret_1h={"BTC": 0.015, "ETH": 0.012},
            vol_15m={"BTC": 0.01, "ETH": 0.012},
            vol_1h={"BTC": 0.02, "ETH": 0.025},
            spread_bps={"BTC": 0.8, "ETH": 1.1},
            book_imbalance_l1={"BTC": 0.12, "ETH": -0.04},
            book_imbalance_l5={"BTC": 0.09, "ETH": -0.02},
            top_depth_usd={"BTC": 350000.0, "ETH": 220000.0},
            microprice={"BTC": 50001.2, "ETH": 3000.7},
            liquidity_score={"BTC": 0.85, "ETH": 0.76},
            reject_rate_15m={"BTC": 0.02, "ETH": 0.01},
            p95_latency_ms_15m={"BTC": 210.0, "ETH": 185.0},
            slippage_bps_15m={"BTC": 1.4, "ETH": 1.9},
        )
        self._round_trip(FeatureSnapshot15m, fs)

    def test_state_snapshot_round_trip(self):
        st = StateSnapshot(
            equity_usd=1000.0,
            cash_usd=200.0,
            positions={
                "BTC": Position(
                    symbol="BTC",
                    qty=0.01,
                    entry_px=45000.0,
                    mark_px=50000.0,
                    unreal_pnl=50.0,
                    side="LONG",
                )
            },
            open_orders=[
                OpenOrder(
                    client_order_id="c1",
                    exchange_order_id="o1",
                    symbol="BTC",
                    side="BUY",
                    px=49000.0,
                    qty=0.01,
                    status="OPEN",
                )
            ],
            health={"reconcile_ok": True},
        )
        self._round_trip(StateSnapshot, st)

    def test_target_portfolio_round_trip(self):
        tp = TargetPortfolio(
            asof_minute="2026-02-18T16:00:00Z",
            universe=["BTC", "ETH"],
            targets=[TargetWeight(symbol="BTC", weight=0.2)],
            cash_weight=0.8,
            confidence=0.6,
            rationale="test",
            model={"name": "baseline", "version": "v1"},
            constraints_hint={"max_gross": 0.4},
        )
        self._round_trip(TargetPortfolio, tp)

    def test_approved_target_portfolio_round_trip(self):
        ap = ApprovedTargetPortfolio(
            asof_minute="2026-02-18T16:00:00Z",
            mode="NORMAL",
            approved_targets=[TargetWeight(symbol="BTC", weight=0.1)],
            rejections=[Rejection(symbol="ETH", reason="cap", original_weight=0.3, approved_weight=0.1)],
            risk_summary={"gross": 0.1, "net": 0.1},
        )
        self._round_trip(ApprovedTargetPortfolio, ap)

    def test_execution_plan_round_trip(self):
        plan = ExecutionPlan(
            cycle_id="20260218T1600Z",
            slices=[
                SliceOrder(
                    symbol="BTC",
                    side="BUY",
                    qty=0.01,
                    order_type="LIMIT",
                    px_guard_bps=10,
                    timeout_s=20,
                    slice_idx=0,
                )
            ],
            limits=ExecutionLimits(max_orders_per_min=30, max_cancels_per_min=30),
            idempotency_key="20260218T1600Z:TWAP:3",
        )
        self._round_trip(ExecutionPlan, plan)

    def test_order_intent_round_trip(self):
        intent = OrderIntent(
            action="PLACE",
            symbol="BTC",
            side="BUY",
            qty=0.01,
            order_type="LIMIT",
            limit_px=50000.0,
            tif="Gtc",
            client_order_id="20260218T1600Z:BTC:0:BUY",
            slice_ref="20260218T1600Z:BTC:0",
            reason="test",
        )
        self._round_trip(OrderIntent, intent)

    def test_execution_report_round_trip(self):
        rep = ExecutionReport(
            client_order_id="20260218T1600Z:BTC:0:BUY",
            exchange_order_id="123",
            symbol="BTC",
            status="ACK",
            filled_qty=0.0,
            avg_px=0.0,
            fee=0.0,
            latency_ms=10,
            raw={"ok": True},
        )
        self._round_trip(ExecutionReport, rep)


if __name__ == "__main__":
    unittest.main()
