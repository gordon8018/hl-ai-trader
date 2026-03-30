"""Factor registry with family grouping and timeframe annotations."""
from __future__ import annotations

# Each entry is (factor_name, set_of_timeframes)
FACTOR_FAMILIES: dict[str, list[tuple[str, set[str]]]] = {
    "microstructure": [
        ("book_imbalance_l1", {"1m", "15m"}),
        ("book_imbalance_l5", {"1m", "15m"}),
        ("book_imbalance_l10", {"1m", "15m"}),
        ("book_imbalance", {"1m"}),
        ("spread_bps", {"1m", "15m"}),
        ("liquidity_score", {"1m", "15m"}),
        ("top_depth_usd", {"1m", "15m"}),
        ("top_depth_usd_l10", {"15m"}),
    ],
    "microstructure_dynamics": [
        ("microprice_change_1m", {"1m", "15m"}),
        ("book_imbalance_change_1m", {"1m", "15m"}),
        ("spread_change_1m", {"1m", "15m"}),
        ("depth_change_1m", {"1m", "15m"}),
        ("bid_slope", {"1m", "15m"}),
        ("ask_slope", {"1m", "15m"}),
        ("queue_imbalance_l1", {"1m", "15m"}),
    ],
    "order_flow": [
        ("trade_volume_buy_ratio", {"1m", "15m"}),
        ("aggr_delta_1m", {"1m", "15m"}),
        ("aggr_delta_5m", {"1m", "15m"}),
        ("volume_imbalance_1m", {"1m", "15m"}),
        ("volume_imbalance_5m", {"1m", "15m"}),
        ("buy_pressure_1m", {"1m", "15m"}),
        ("sell_pressure_1m", {"1m", "15m"}),
        ("absorption_ratio_bid", {"1m", "15m"}),
        ("absorption_ratio_ask", {"1m", "15m"}),
    ],
    "momentum": [
        ("ret_15m", {"15m"}),
        ("ret_30m", {"15m"}),
        ("ret_1h", {"15m", "1h"}),
        ("ret_4h", {"15m", "1h"}),
        ("ret_24h", {"15m"}),
        ("trend_15m", {"15m"}),
        ("trend_1h", {"15m", "1h"}),
        ("trend_4h", {"15m", "1h"}),
        ("trend_agree", {"15m", "1h"}),
        ("trend_strength_15m", {"15m"}),
        ("rsi_14_1m", {"15m"}),
    ],
    "volatility": [
        ("vol_15m", {"15m"}),
        ("vol_1h", {"15m", "1h"}),
        ("vol_4h", {"15m", "1h"}),
        ("vol_spike", {"15m"}),
        ("vol_regime", {"15m", "1h"}),
        ("vol_15m_p90", {"15m"}),
    ],
    "regime": [
        ("liq_regime", {"15m"}),
        ("liquidity_drop", {"15m"}),
        ("top_depth_usd_p10", {"15m"}),
    ],
    "funding_basis": [
        ("funding_rate", {"15m", "1h"}),
        ("basis_bps", {"15m", "1h"}),
        ("oi_change_15m", {"15m"}),
    ],
    "cross_market": [
        ("btc_ret_1m", {"1m", "15m"}),
        ("btc_ret_15m", {"15m"}),
        ("eth_ret_1m", {"1m", "15m"}),
        ("eth_ret_15m", {"15m"}),
        ("corr_btc_1h", {"15m"}),
        ("corr_eth_1h", {"15m"}),
        ("market_ret_mean_1m", {"1m", "15m"}),
        ("market_ret_std_1m", {"1m", "15m"}),
    ],
    "execution_feedback": [
        ("reject_rate_15m", {"15m"}),
        ("slippage_bps_15m", {"15m"}),
        ("p95_latency_ms_15m", {"15m"}),
    ],
}


def get_all_factors() -> list[str]:
    return [name for factors in FACTOR_FAMILIES.values() for name, _ in factors]


def get_factors_for_timeframe(timeframe: str) -> list[str]:
    return [name for factors in FACTOR_FAMILIES.values() for name, tfs in factors if timeframe in tfs]


def get_family(factor_name: str) -> str | None:
    for family, factors in FACTOR_FAMILIES.items():
        for name, _ in factors:
            if name == factor_name:
                return family
    return None
