# backtester/replay.py
"""
Offline backtester: reads md.features.15m history from Redis XRANGE,
simulates Layer 2 direction-confirmation logic,
and evaluates signal quality vs forward price outcomes.
"""
from __future__ import annotations
import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis as redis_lib
from shared.schemas import FeatureSnapshot15m, DirectionBias, SymbolBias


REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
STREAM_15M = "md.features.15m"
STREAM_1H = "md.features.1h"
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")


def connect_redis() -> redis_lib.Redis:
    return redis_lib.from_url(REDIS_URL, decode_responses=True)


def read_stream_history(r: redis_lib.Redis, stream: str, count: int = 5000) -> List[dict]:
    """Read up to `count` recent messages from a Redis stream via XREVRANGE."""
    raw = r.xrevrange(stream, count=count)
    results = []
    for msg_id, fields in raw:
        payload_str = fields.get("p")
        if not payload_str:
            continue
        try:
            results.append(json.loads(payload_str))
        except Exception:
            continue
    results.reverse()  # chronological order
    return results


def build_simple_bias_from_features(fs: FeatureSnapshot15m, universe: List[str]) -> DirectionBias:
    """
    Build a simple rule-based DirectionBias (stand-in for LLM Layer 1).
    Uses 1H trend and 15m momentum to determine direction.
    This is used in dry-run mode (no LLM calls).
    """
    biases = []
    sideways_count = 0

    for sym in universe:
        ret_1h = fs.ret_1h.get(sym, 0.0)
        trend_1h = fs.trend_1h.get(sym, 0)
        trend_agree = fs.trend_agree.get(sym, 0)
        vol_regime = fs.vol_regime.get(sym, 0)

        # SIDEWAYS check (2+ conditions)
        sideways_signals = 0
        if abs(ret_1h) < 0.003:                sideways_signals += 1
        if vol_regime == 0 and trend_agree == 0: sideways_signals += 1

        if sideways_signals >= 2:
            biases.append(SymbolBias(symbol=sym, direction="FLAT", confidence=0.35))
            sideways_count += 1
            continue

        if trend_1h > 0 and ret_1h > 0.002:
            confidence = min(0.65 + abs(ret_1h) * 5, 0.80)
            biases.append(SymbolBias(symbol=sym, direction="LONG", confidence=confidence))
        elif trend_1h < 0 and ret_1h < -0.002:
            confidence = min(0.65 + abs(ret_1h) * 5, 0.80)
            biases.append(SymbolBias(symbol=sym, direction="SHORT", confidence=confidence))
        else:
            biases.append(SymbolBias(symbol=sym, direction="FLAT", confidence=0.40))

    market_state = "SIDEWAYS" if sideways_count > len(universe) // 2 else "TRENDING"
    now = datetime.now(timezone.utc)
    return DirectionBias(
        asof_minute=now.strftime("%Y-%m-%dT%H:%M:00Z"),
        valid_until_minute=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:00Z"),
        market_state=market_state,
        biases=biases,
    )


def simulate_layer2_decision(
    fs: FeatureSnapshot15m,
    bias: DirectionBias,
    universe: List[str],
    min_confirm: int = 3,
) -> Dict[str, str]:
    """
    Apply Layer 2 confirmation rules. Returns {symbol: "LONG"|"SHORT"|"HOLD"}.
    This duplicates apply_direction_confirmation() without importing ai_decision.app.
    """
    if bias.market_state == "SIDEWAYS":
        return {sym: "HOLD" for sym in universe}

    bias_map = {b.symbol: b for b in bias.biases}
    result = {}
    for sym in universe:
        sym_bias = bias_map.get(sym)
        if not sym_bias or sym_bias.direction == "FLAT":
            result[sym] = "HOLD"
            continue

        direction = sym_bias.direction
        confirm = 0
        if direction == "LONG":
            if fs.funding_rate.get(sym, 0) > 0:          confirm += 1
            if fs.basis_bps.get(sym, 0) > 0:             confirm += 1
            if fs.oi_change_15m.get(sym, 0) > 0:         confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) > 0.10:  confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) > 0:         confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:  confirm += 1
        else:
            if fs.funding_rate.get(sym, 0) < 0:          confirm += 1
            if fs.basis_bps.get(sym, 0) < 0:             confirm += 1
            if fs.oi_change_15m.get(sym, 0) < 0:         confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) < -0.10: confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) < 0:         confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:  confirm += 1  # strong trend exists (direction-agnostic)

        result[sym] = direction if confirm >= min_confirm else "HOLD"
    return result


class Prediction:
    __slots__ = ["asof_minute", "symbol", "direction", "entry_px", "outcome_px",
                 "market_state", "bias_confidence"]

    def __init__(self, asof_minute, symbol, direction, entry_px,
                 outcome_px, market_state, bias_confidence):
        self.asof_minute = asof_minute
        self.symbol = symbol
        self.direction = direction
        self.entry_px = entry_px
        self.outcome_px = outcome_px
        self.market_state = market_state
        self.bias_confidence = bias_confidence

    @property
    def ret(self) -> float:
        if self.entry_px <= 0:
            return 0.0
        return (self.outcome_px / self.entry_px) - 1.0

    @property
    def won(self) -> bool:
        if self.direction == "LONG":
            return self.ret > 0
        if self.direction == "SHORT":
            return self.ret < 0
        return False


def run_replay(
    days: int = 7,
    use_llm: bool = False,
    outcome_horizon_min: int = 60,
) -> List[Prediction]:
    """
    Main replay function. Returns list of Prediction objects.

    Args:
        days: How many days of history to replay.
        use_llm: If True, call the actual LLM for Layer 1 decisions (expensive).
        outcome_horizon_min: Minutes ahead to measure outcome price.
    """
    r = connect_redis()

    logger.info(f"Reading md.features.15m history from Redis...")
    msgs = read_stream_history(r, STREAM_15M, count=10000)
    logger.info(f"Read {len(msgs)} messages from md.features.15m")

    # Filter to requested days
    cutoff_ts = time.time() - days * 86400
    msgs = [m for m in msgs
            if _parse_ts(m.get("data", {}).get("asof_minute", "")) > cutoff_ts]
    logger.info(f"After {days}-day filter: {len(msgs)} messages remain")

    # Build a price-lookup index: {symbol -> [(ts, mid_px), ...]}
    price_index: Dict[str, List[Tuple[float, float]]] = {sym: [] for sym in UNIVERSE}
    for m in msgs:
        data = m.get("data", {})
        ts = _parse_ts(data.get("asof_minute", ""))
        if ts <= 0:
            continue
        mid_px = data.get("mid_px", {})
        for sym in UNIVERSE:
            if sym in mid_px:
                price_index[sym].append((ts, float(mid_px[sym])))

    # Sort price index
    for sym in price_index:
        price_index[sym].sort()

    predictions: List[Prediction] = []
    last_bias_ts = 0.0
    current_bias: Optional[DirectionBias] = None
    prev_hourly_ts = 0.0

    for m in msgs:
        data = m.get("data", {})
        try:
            fs = FeatureSnapshot15m(**data)
        except Exception:
            continue

        asof_ts = _parse_ts(fs.asof_minute)

        # Refresh direction bias every hour (simulate Layer 1)
        hour_ts = (asof_ts // 3600) * 3600
        if hour_ts > prev_hourly_ts:
            prev_hourly_ts = hour_ts
            if use_llm:
                logger.warning("LLM replay not yet implemented; using rule-based fallback")
            current_bias = build_simple_bias_from_features(fs, UNIVERSE)

        if current_bias is None:
            continue

        decisions = simulate_layer2_decision(fs, current_bias, UNIVERSE)

        for sym, decision in decisions.items():
            if decision == "HOLD":
                continue

            entry_px = fs.mid_px.get(sym, 0.0)
            if entry_px <= 0:
                continue

            # Look up outcome price N minutes later
            outcome_px = _lookup_price(price_index, sym, asof_ts, outcome_horizon_min * 60)
            if outcome_px <= 0:
                continue

            bias_conf = next(
                (b.confidence for b in current_bias.biases if b.symbol == sym),
                0.0
            )

            predictions.append(Prediction(
                asof_minute=fs.asof_minute,
                symbol=sym,
                direction=decision,
                entry_px=entry_px,
                outcome_px=outcome_px,
                market_state=current_bias.market_state,
                bias_confidence=bias_conf,
            ))

    logger.info(f"Replay complete: {len(predictions)} predictions generated")
    return predictions


def _parse_ts(asof_minute: str) -> float:
    try:
        s = asof_minute.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _lookup_price(
    price_index: Dict[str, List[Tuple[float, float]]],
    sym: str,
    from_ts: float,
    offset_sec: float,
) -> float:
    """Find the price closest to from_ts + offset_sec."""
    target_ts = from_ts + offset_sec
    entries = price_index.get(sym, [])
    if not entries:
        return 0.0
    # Binary search for closest entry
    lo, hi = 0, len(entries) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid][0] < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return entries[lo][1]
