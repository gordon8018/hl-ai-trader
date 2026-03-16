# services/market_data/app.py
import os
import time
import math
import json
import logging
import httpx
from typing import Dict, List
from collections import deque
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(module)s:%(lineno)d %(message)s',
)
logger = logging.getLogger(__name__)

from shared.bus.guard import require_env
from shared.bus.redis_streams import RedisStreams
from shared.schemas import Envelope, FeatureSnapshot1m, FeatureSnapshot15m, FeatureSnapshot1h, current_cycle_id
from shared.time_utils import cycle_id_from_asof_minute
from shared.metrics.prom import start_metrics, MSG_OUT, ERR, LAT, set_alarm

REDIS_URL = os.environ["REDIS_URL"]
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
CYCLE_SECONDS = int(os.environ.get("CYCLE_SECONDS", "60"))
POLL_SECONDS = float(os.environ.get("MD_POLL_SECONDS", "2.0"))
PERP_CTX_POLL_SECONDS = float(os.environ.get("MD_PERP_CTX_POLL_SECONDS", "10.0"))
PERP_CTX_STALE_SECONDS = float(os.environ.get("MD_PERP_CTX_STALE_SECONDS", "120.0"))
L2_ENABLED = os.environ.get("MD_L2_ENABLED", "true").lower() == "true"
L2_POLL_SECONDS = float(os.environ.get("MD_L2_POLL_SECONDS", "10.0"))
L2_STALE_SECONDS = float(os.environ.get("MD_L2_STALE_SECONDS", "120.0"))
L2_N_SIGFIGS = os.environ.get("MD_L2_N_SIGFIGS", "").strip()
L2_MANTISSA = os.environ.get("MD_L2_MANTISSA", "").strip()
EXEC_REPORT_SCAN_LIMIT = int(os.environ.get("MD_EXEC_REPORT_SCAN_LIMIT", "800"))
MD_TRADES_ENABLED = os.environ.get("MD_TRADES_ENABLED", "true").lower() == "true"
MD_TRADES_WINDOW_SEC = float(os.environ.get("MD_TRADES_WINDOW_SEC", "60"))
MD_TRADES_ENDPOINT_TYPE = os.environ.get("MD_TRADES_ENDPOINT_TYPE", "recentTrades")
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))
MD_PRICE_HISTORY_MINUTES = int(os.environ.get("MD_PRICE_HISTORY_MINUTES", "310"))

SERVICE = "market_data"
os.environ["SERVICE_NAME"] = SERVICE

STREAM_OUT = "md.features.1m"
STREAM_OUT_15M = "md.features.15m"
STREAM_OUT_1H = "md.features.1h"
AUDIT = "audit.logs"

def utc_minute_key(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(second=0, microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")

def calc_return(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return (b / a) - 1.0

def collect_missing_symbols(allmids, universe, mids_hist) -> List[str]:
    missing = set()
    for sym in universe:
        if allmids.get(sym) is None:
            missing.add(sym)
        if not mids_hist.get(sym):
            missing.add(sym)
    return sorted(missing)

def price_ago(hist: deque, now_ts: float, seconds: float) -> float:
    target = now_ts - seconds
    for ts, px in reversed(hist):
        if ts <= target:
            return px
    return hist[0][1]

def realized_vol(hist: deque, now_ts: float, minutes: int) -> float:
    series = []
    for k in range(minutes, 0, -1):
        series.append(price_ago(hist, now_ts, k * 60))
    series.append(hist[-1][1])
    lrs = []
    for i in range(1, len(series)):
        a, b = series[i - 1], series[i]
        if a > 0 and b > 0:
            lrs.append(math.log(b / a))
    if not lrs:
        return 0.0
    mean = sum(lrs) / len(lrs)
    var = sum((x - mean) ** 2 for x in lrs) / max(len(lrs) - 1, 1)
    return math.sqrt(var) * math.sqrt(max(minutes, 1))


def compute_1h_features(sym: str, hist: deque, now_ts: float, aggr_delta_hist: deque = None) -> dict:
    """Compute 1H-level features for a single symbol."""
    ret_1h = calc_return(price_ago(hist, now_ts, 3600), hist[-1][1])
    ret_4h = calc_return(price_ago(hist, now_ts, 14400), hist[-1][1])
    vol_1h = realized_vol(hist, now_ts, 60)
    vol_4h = realized_vol(hist, now_ts, 240)

    # Trend: 1 if recent price > price N periods ago, else -1, else 0
    px_now = hist[-1][1]
    px_1h_ago = price_ago(hist, now_ts, 3600)
    px_4h_ago = price_ago(hist, now_ts, 14400)
    trend_1h = 1 if px_now > px_1h_ago * 1.001 else (-1 if px_now < px_1h_ago * 0.999 else 0)
    trend_4h = 1 if px_now > px_4h_ago * 1.002 else (-1 if px_now < px_4h_ago * 0.998 else 0)

    # RSI on 1m-sampled prices (14-period approximation)
    rsi_14_1h = rsi_from_hist(hist, now_ts, period=14)

    # Hourly aggr_delta: sum of 1m deltas over last 60 minutes
    aggr_delta_1h = 0.0
    if aggr_delta_hist is not None:
        cutoff = now_ts - 3600
        for ts, delta in aggr_delta_hist:
            if ts >= cutoff:
                aggr_delta_1h += delta

    return {
        "ret_1h": ret_1h,
        "ret_4h": ret_4h,
        "vol_1h": vol_1h,
        "vol_4h": vol_4h,
        "trend_1h": trend_1h,
        "trend_4h": trend_4h,
        "rsi_14_1h": rsi_14_1h,
        "aggr_delta_1h": aggr_delta_1h,
    }


def rsi_from_hist(hist: deque, now_ts: float, period: int = 14) -> float:
    if period <= 0 or len(hist) < 2:
        return 50.0
    prices = [price_ago(hist, now_ts, k * 60) for k in range(period, 0, -1)]
    prices.append(hist[-1][1])
    gains, losses = [], []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

def parse_meta_and_asset_ctxs(resp_json) -> dict:
    if not isinstance(resp_json, list) or len(resp_json) < 2:
        return {}
    meta = resp_json[0] if isinstance(resp_json[0], dict) else {}
    ctxs = resp_json[1] if isinstance(resp_json[1], list) else []
    universe = meta.get("universe", []) if isinstance(meta, dict) else []
    out = {}
    for i, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if not isinstance(name, str):
            continue
        ctx = ctxs[i] if i < len(ctxs) and isinstance(ctxs[i], dict) else {}
        out[name] = ctx
    return out

def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default

def parse_l2_metrics(resp_json: dict) -> dict:
    if not isinstance(resp_json, dict):
        return {}
    levels = resp_json.get("levels")
    if not isinstance(levels, list) or len(levels) < 2:
        return {}
    bids = levels[0] if isinstance(levels[0], list) else []
    asks = levels[1] if isinstance(levels[1], list) else []
    if not bids or not asks:
        return {}

    bid_px = _to_float(bids[0].get("px")) if isinstance(bids[0], dict) else 0.0
    ask_px = _to_float(asks[0].get("px")) if isinstance(asks[0], dict) else 0.0
    bid_sz = _to_float(bids[0].get("sz")) if isinstance(bids[0], dict) else 0.0
    ask_sz = _to_float(asks[0].get("sz")) if isinstance(asks[0], dict) else 0.0
    if bid_px <= 0 or ask_px <= 0:
        return {}
    mid = (bid_px + ask_px) / 2.0
    if mid <= 0:
        return {}

    spread_bps = ((ask_px - bid_px) / mid) * 10000.0
    denom_l1 = bid_sz + ask_sz
    imbalance_l1 = ((bid_sz - ask_sz) / denom_l1) if denom_l1 > 0 else 0.0

    bid5 = bids[:5]
    ask5 = asks[:5]
    bid5_sz = sum(_to_float(x.get("sz")) for x in bid5 if isinstance(x, dict))
    ask5_sz = sum(_to_float(x.get("sz")) for x in ask5 if isinstance(x, dict))
    denom_l5 = bid5_sz + ask5_sz
    imbalance_l5 = ((bid5_sz - ask5_sz) / denom_l5) if denom_l5 > 0 else 0.0
    depth_usd_l5 = sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in bid5 if isinstance(x, dict))
    depth_usd_l5 += sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in ask5 if isinstance(x, dict))

    bid10 = bids[:10]
    ask10 = asks[:10]
    bid10_sz = sum(_to_float(x.get("sz")) for x in bid10 if isinstance(x, dict))
    ask10_sz = sum(_to_float(x.get("sz")) for x in ask10 if isinstance(x, dict))
    denom_l10 = bid10_sz + ask10_sz
    imbalance_l10 = ((bid10_sz - ask10_sz) / denom_l10) if denom_l10 > 0 else 0.0
    depth_usd_l10 = sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in bid10 if isinstance(x, dict))
    depth_usd_l10 += sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in ask10 if isinstance(x, dict))

    microprice = ((ask_px * bid_sz) + (bid_px * ask_sz)) / denom_l1 if denom_l1 > 0 else mid
    liquidity_score = min(1.0, max(0.0, (depth_usd_l5 / 1_000_000.0) / (1.0 + max(spread_bps, 0.0))))

    def _slope(levels: List[dict], side: str) -> float:
        if len(levels) < 2:
            return 0.0
        px_first = _to_float(levels[0].get("px"))
        px_last = _to_float(levels[-1].get("px"))
        depth = sum(_to_float(x.get("sz")) for x in levels if isinstance(x, dict))
        if depth <= 1e-12:
            return 0.0
        if side == "bid":
            return max(0.0, (px_first - px_last) / depth)
        return max(0.0, (px_last - px_first) / depth)

    bid_slope = _slope(bid10, "bid")
    ask_slope = _slope(ask10, "ask")
    queue_imbalance_l1 = imbalance_l1

    return {
        "spread_bps": spread_bps,
        "book_imbalance_l1": imbalance_l1,
        "book_imbalance_l5": imbalance_l5,
        "book_imbalance_l10": imbalance_l10,
        "top_depth_usd": depth_usd_l5,
        "top_depth_usd_l10": depth_usd_l10,
        "microprice": microprice,
        "liquidity_score": liquidity_score,
        "bid_slope": bid_slope,
        "ask_slope": ask_slope,
        "queue_imbalance_l1": queue_imbalance_l1,
    }

def _trade_side(trade: dict) -> str:
    side = trade.get("side") or trade.get("takerSide") or trade.get("dir")
    if isinstance(side, str):
        s = side.lower()
        if s in {"b", "buy", "bid"}:
            return "buy"
        if s in {"a", "s", "sell", "ask"}:
            return "sell"
    is_buyer_maker = trade.get("isBuyerMaker")
    if isinstance(is_buyer_maker, bool):
        return "sell" if is_buyer_maker else "buy"
    return "unknown"


def parse_trade_metrics(trades: List[dict], now_ts: float, window_sec: float) -> dict:
    total = 0.0
    count = 0
    buy_vol = 0.0
    sell_vol = 0.0
    for tr in trades:
        if not isinstance(tr, dict):
            continue
        ts = tr.get("time") or tr.get("timestamp") or tr.get("ts")
        if isinstance(ts, (int, float)):
            t = float(ts) / (1000.0 if ts > 1e12 else 1.0)
        elif isinstance(ts, str) and ts.isdigit():
            t = float(ts)
            if t > 1e12:
                t = t / 1000.0
        else:
            t = None
        if t is None or (now_ts - t) > window_sec:
            continue
        sz = _to_float(tr.get("sz") or tr.get("size") or tr.get("qty"), 0.0)
        if sz <= 0:
            continue
        total += sz
        count += 1
        side = _trade_side(tr)
        if side == "buy":
            buy_vol += sz
        elif side == "sell":
            sell_vol += sz
    buy_ratio = (buy_vol / total) if total > 0 else 0.0
    aggr_delta = buy_vol - sell_vol
    vol_imb = (aggr_delta / total) if total > 0 else 0.0
    buy_pressure = buy_ratio
    sell_pressure = (sell_vol / total) if total > 0 else 0.0
    return {
        "trade_volume_1m": total,
        "trade_count_1m": float(count),
        "trade_volume_buy_ratio": buy_ratio,
        "aggr_buy_volume_1m": buy_vol,
        "aggr_sell_volume_1m": sell_vol,
        "aggr_delta_1m": aggr_delta,
        "volume_imbalance_1m": vol_imb,
        "buy_pressure_1m": buy_pressure,
        "sell_pressure_1m": sell_pressure,
    }


def returns_series(hist: deque, now_ts: float, minutes: int) -> List[float]:
    series = []
    for k in range(minutes, 0, -1):
        series.append(price_ago(hist, now_ts, k * 60))
    series.append(hist[-1][1])
    out = []
    for i in range(1, len(series)):
        a, b = series[i - 1], series[i]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def corr_coeff(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a = a[-n:]
    b = b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / max(n - 1, 1)
    va = sum((x - ma) ** 2 for x in a) / max(n - 1, 1)
    vb = sum((x - mb) ** 2 for x in b) / max(n - 1, 1)
    if va <= 1e-12 or vb <= 1e-12:
        return 0.0
    return cov / math.sqrt(va * vb)


def percentile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    qq = max(0.0, min(1.0, float(q)))
    arr = sorted(float(v) for v in values)
    idx = max(0, math.ceil(qq * len(arr)) - 1)
    return arr[idx]

def _parse_env_ts(ts: str) -> float:
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()

def _decode_stream_payload(fields: dict) -> dict:
    raw = fields.get("p")
    if not isinstance(raw, str):
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}

def _extract_slippage_bps(data: dict) -> float:
    raw = data.get("raw")
    if isinstance(raw, dict):
        for k in ("slippage_bps", "fill_slippage_bps"):
            v = raw.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    return 0.0

def aggregate_exec_feedback(entries: List[dict], universe: List[str], now_ts: float, window_sec: float) -> tuple[dict, dict, dict]:
    by_sym = {
        sym: {
            "total": 0,
            "rejected": 0,
            "latencies": [],
            "slippage": [],
        }
        for sym in universe
    }
    for payload in entries:
        if not isinstance(payload, dict):
            continue
        env = payload.get("env")
        data = payload.get("data")
        if not isinstance(env, dict) or not isinstance(data, dict):
            continue
        ts = env.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            t = _parse_env_ts(ts)
        except Exception:
            continue
        if (now_ts - t) > window_sec:
            continue
        sym = data.get("symbol")
        if not isinstance(sym, str) or sym not in by_sym:
            continue
        s = by_sym[sym]
        s["total"] += 1
        status = data.get("status")
        if status == "REJECTED":
            s["rejected"] += 1
        lat = data.get("latency_ms")
        if isinstance(lat, (int, float)):
            s["latencies"].append(float(lat))
        slp = _extract_slippage_bps(data)
        s["slippage"].append(float(slp))

    reject_rate, p95_latency, slippage_bps = {}, {}, {}
    for sym in universe:
        s = by_sym[sym]
        total = float(s["total"])
        reject_rate[sym] = (float(s["rejected"]) / total) if total > 0 else 0.0
        p95_latency[sym] = percentile(s["latencies"], 0.95)
        slippage_bps[sym] = percentile(s["slippage"], 0.5)
    return reject_rate, p95_latency, slippage_bps

def main():
    start_metrics("METRICS_PORT", 9101)
    bus = RedisStreams(REDIS_URL)

    # rolling store for mid prices
    # MD_PRICE_HISTORY_MINUTES (default 310) at ~2s sampling → ~9300 points; covers 4H lookback + buffer
    mids_hist = {sym: deque(maxlen=MD_PRICE_HISTORY_MINUTES * 30) for sym in UNIVERSE}  # (ts, mid) ~2s sampling
    oi_hist = {sym: deque(maxlen=120) for sym in UNIVERSE}  # (ts, open_interest)
    vol15_hist = {sym: deque(maxlen=96) for sym in UNIVERSE}  # ~1 day of 15m vols
    depth_hist = {sym: deque(maxlen=96) for sym in UNIVERSE}  # ~1 day of top depth
    trade_hist = {sym: deque(maxlen=5) for sym in UNIVERSE}  # last 5x1m trade metrics
    aggr_delta_hist: Dict[str, deque] = {sym: deque(maxlen=70) for sym in UNIVERSE}
    last_micro = {sym: {} for sym in UNIVERSE}
    perp_ctx_cache = {}
    last_perp_ctx_ts = 0.0
    l2_metrics_cache = {}
    last_l2_ts = 0.0
    last_exec_stats = {}

    last_emitted_minute = None
    missing_this_minute = set()
    error_streak = 0
    alarm_on = False

    def note_error(env: Envelope, where: str, err: Exception) -> None:
        nonlocal error_streak, alarm_on
        error_streak += 1
        ERR.labels(SERVICE, where).inc()
        if error_streak >= ERROR_STREAK_THRESHOLD and not alarm_on:
            alarm_on = True
            set_alarm("error_streak", True)
            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "alarm", "where": where, "reason": "error_streak"}))

    def note_ok(env: Envelope) -> None:
        nonlocal error_streak, alarm_on
        if error_streak > 0:
            error_streak = 0
        if alarm_on:
            alarm_on = False
            set_alarm("error_streak", False)
            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "alarm_cleared", "reason": "recovered"}))

    def fetch_recent_trades(coin: str) -> List[dict]:
        if not MD_TRADES_ENABLED:
            return []
        try:
            resp = client.post(f"{HL_HTTP_URL}/info", json={"type": MD_TRADES_ENDPOINT_TYPE, "coin": coin})
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return data.get("data")
            return []
        except Exception as e:
            env = Envelope(source="market_data", cycle_id=current_cycle_id())
            bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "md.trades_error", "coin": coin, "err": str(e)}))
            return []

    with httpx.Client(timeout=5.0) as client:
        while True:
            t0 = time.time()
            try:
                # /info allMids: returns dict symbol -> string mid. :contentReference[oaicite:4]{index=4}
                resp = client.post(f"{HL_HTTP_URL}/info", json={"type": "allMids"})
                resp.raise_for_status()
                allmids = resp.json()  # { "BTC":"12345.6", ... }
                now = time.time()

                if (now - last_perp_ctx_ts) >= PERP_CTX_POLL_SECONDS:
                    try:
                        ctx_resp = client.post(f"{HL_HTTP_URL}/info", json={"type": "metaAndAssetCtxs"})
                        ctx_resp.raise_for_status()
                        perp_ctx_cache = parse_meta_and_asset_ctxs(ctx_resp.json())
                        last_perp_ctx_ts = now
                    except Exception as e:
                        env = Envelope(source="market_data", cycle_id=cycle_id_from_asof_minute(utc_minute_key(now)))
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "md.perp_ctx_error",
                                    "err": str(e),
                                    "cache_age_sec": max(now - last_perp_ctx_ts, 0.0),
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()

                if L2_ENABLED and (now - last_l2_ts) >= L2_POLL_SECONDS:
                    l2_body_base = {"type": "l2Book"}
                    if L2_N_SIGFIGS:
                        l2_body_base["nSigFigs"] = int(L2_N_SIGFIGS)
                    if L2_MANTISSA:
                        l2_body_base["mantissa"] = int(L2_MANTISSA)
                    for sym in UNIVERSE:
                        try:
                            body = dict(l2_body_base)
                            body["coin"] = sym
                            l2_resp = client.post(f"{HL_HTTP_URL}/info", json=body)
                            l2_resp.raise_for_status()
                            metrics = parse_l2_metrics(l2_resp.json())
                            if metrics:
                                l2_metrics_cache[sym] = {"ts": now, "metrics": metrics}
                        except Exception as e:
                            env = Envelope(source="market_data", cycle_id=cycle_id_from_asof_minute(utc_minute_key(now)))
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "md.l2_error",
                                        "data": {"symbol": sym, "err": str(e)},
                                    }
                                ),
                            )
                            MSG_OUT.labels(SERVICE, AUDIT).inc()
                    last_l2_ts = now

                for sym in UNIVERSE:
                    v = allmids.get(sym)
                    if v is None:
                        missing_this_minute.add(sym)
                        continue
                    mid = float(v)
                    mids_hist[sym].append((now, mid))

                # emit features once per minute boundary
                minute_key = utc_minute_key(now)
                if last_emitted_minute is None:
                    last_emitted_minute = minute_key

                if minute_key != last_emitted_minute:
                    # build features using last known samples
                    mid_px = {}
                    ret_1m, ret_5m, ret_1h, vol_1h = {}, {}, {}, {}
                    ret_15m, ret_30m, vol_15m = {}, {}, {}
                    spread_bps, book_imbalance, liq_intensity, liquidity_score = {}, {}, {}, {}
                    book_imbalance_l1, book_imbalance_l5, book_imbalance_l10 = {}, {}, {}
                    top_depth_usd, top_depth_usd_l10, microprice = {}, {}, {}
                    funding_rate, next_funding_ts, basis_bps, open_interest, oi_change_15m = {}, {}, {}, {}, {}
                    reject_rate_15m, p95_latency_ms_15m, slippage_bps_15m = {}, {}, {}
                    reject_rate_15m_delta, p95_latency_ms_15m_delta, slippage_bps_15m_delta = {}, {}, {}
                    trend_15m, trend_1h, trend_agree = {}, {}, {}
                    rsi_14_1m = {}
                    vol_15m_p90, vol_spike = {}, {}
                    top_depth_usd_p10, liquidity_drop = {}, {}
                    trade_volume_1m, trade_volume_buy_ratio, trade_count_1m = {}, {}, {}
                    aggr_buy_volume_1m, aggr_sell_volume_1m, aggr_delta_1m = {}, {}, {}
                    aggr_delta_5m = {}
                    volume_imbalance_1m, volume_imbalance_5m = {}, {}
                    buy_pressure_1m, sell_pressure_1m = {}, {}
                    absorption_ratio_bid, absorption_ratio_ask = {}, {}
                    microprice_change_1m, book_imbalance_change_1m = {}, {}
                    spread_change_1m, depth_change_1m = {}, {}
                    bid_slope, ask_slope, queue_imbalance_l1 = {}, {}, {}
                    btc_ret_1m, btc_ret_15m, btc_vol_15m = {}, {}, {}
                    eth_ret_1m, eth_ret_15m = {}, {}
                    market_ret_mean_1m, market_ret_std_1m = {}, {}
                    corr_btc_1h, corr_eth_1h = {}, {}
                    trend_strength_15m, vol_regime, liq_regime = {}, {}, {}

                    exec_entries = []
                    try:
                        rows = bus.r.xrevrange("exec.reports", count=max(1, EXEC_REPORT_SCAN_LIMIT))  # type: ignore[arg-type]
                        for _, fields in rows:
                            payload = _decode_stream_payload(fields)
                            if payload:
                                exec_entries.append(payload)
                    except Exception:
                        exec_entries = []
                    rr, p95, slp = aggregate_exec_feedback(exec_entries, UNIVERSE, now, 900.0)
                    reject_rate_15m.update(rr)
                    p95_latency_ms_15m.update(p95)
                    slippage_bps_15m.update(slp)

                    for sym in UNIVERSE:
                        prev = last_exec_stats.get(sym, {})
                        reject_rate_15m_delta[sym] = reject_rate_15m.get(sym, 0.0) - float(prev.get("reject_rate_15m", 0.0))
                        p95_latency_ms_15m_delta[sym] = p95_latency_ms_15m.get(sym, 0.0) - float(prev.get("p95_latency_ms_15m", 0.0))
                        slippage_bps_15m_delta[sym] = slippage_bps_15m.get(sym, 0.0) - float(prev.get("slippage_bps_15m", 0.0))
                        last_exec_stats[sym] = {
                            "reject_rate_15m": reject_rate_15m.get(sym, 0.0),
                            "p95_latency_ms_15m": p95_latency_ms_15m.get(sym, 0.0),
                            "slippage_bps_15m": slippage_bps_15m.get(sym, 0.0),
                        }

                    for sym in UNIVERSE:
                        if not mids_hist[sym]:
                            missing_this_minute.add(sym)
                            continue
                        mid_px[sym] = mids_hist[sym][-1][1]

                        p_now = mid_px[sym]
                        p_1m = price_ago(mids_hist[sym], now, 60)
                        p_5m = price_ago(mids_hist[sym], now, 300)
                        p_15m = price_ago(mids_hist[sym], now, 900)
                        p_30m = price_ago(mids_hist[sym], now, 1800)
                        p_1h = price_ago(mids_hist[sym], now, 3600)

                        ret_1m[sym] = calc_return(p_1m, p_now)
                        ret_5m[sym] = calc_return(p_5m, p_now)
                        ret_15m[sym] = calc_return(p_15m, p_now)
                        ret_30m[sym] = calc_return(p_30m, p_now)
                        ret_1h[sym] = calc_return(p_1h, p_now)

                        vol_15m[sym] = realized_vol(mids_hist[sym], now, 15)
                        vol_1h[sym] = realized_vol(mids_hist[sym], now, 60)
                        rsi_14_1m[sym] = rsi_from_hist(mids_hist[sym], now, 14)

                        trend_15m[sym] = 1.0 if ret_15m[sym] > 0 else (-1.0 if ret_15m[sym] < 0 else 0.0)
                        trend_1h[sym] = 1.0 if ret_1h[sym] > 0 else (-1.0 if ret_1h[sym] < 0 else 0.0)
                        trend_agree[sym] = 1.0 if trend_15m[sym] == trend_1h[sym] else 0.0

                        # Microstructure from cached l2Book snapshots; fallback to zeros.
                        l2 = l2_metrics_cache.get(sym, {})
                        l2m = l2.get("metrics", {}) if isinstance(l2, dict) else {}
                        spread_bps[sym] = _to_float(l2m.get("spread_bps"), 0.0)
                        book_imbalance_l1[sym] = _to_float(l2m.get("book_imbalance_l1"), 0.0)
                        book_imbalance_l5[sym] = _to_float(l2m.get("book_imbalance_l5"), 0.0)
                        book_imbalance_l10[sym] = _to_float(l2m.get("book_imbalance_l10"), 0.0)
                        top_depth_usd[sym] = _to_float(l2m.get("top_depth_usd"), 0.0)
                        top_depth_usd_l10[sym] = _to_float(l2m.get("top_depth_usd_l10"), 0.0)
                        microprice[sym] = _to_float(l2m.get("microprice"), p_now)
                        liquidity_score[sym] = _to_float(l2m.get("liquidity_score"), 0.0)
                        bid_slope[sym] = _to_float(l2m.get("bid_slope"), 0.0)
                        ask_slope[sym] = _to_float(l2m.get("ask_slope"), 0.0)
                        queue_imbalance_l1[sym] = _to_float(l2m.get("queue_imbalance_l1"), 0.0)
                        # Keep existing 1m field populated for backward compatibility.
                        book_imbalance[sym] = book_imbalance_l1[sym]
                        liq_intensity[sym] = top_depth_usd[sym]

                        prev_micro = last_micro.get(sym, {})
                        microprice_change_1m[sym] = microprice[sym] - float(prev_micro.get("microprice", microprice[sym]))
                        book_imbalance_change_1m[sym] = book_imbalance_l1[sym] - float(prev_micro.get("book_imbalance_l1", book_imbalance_l1[sym]))
                        spread_change_1m[sym] = spread_bps[sym] - float(prev_micro.get("spread_bps", spread_bps[sym]))
                        depth_change_1m[sym] = top_depth_usd[sym] - float(prev_micro.get("top_depth_usd", top_depth_usd[sym]))
                        last_micro[sym] = {
                            "microprice": microprice[sym],
                            "book_imbalance_l1": book_imbalance_l1[sym],
                            "spread_bps": spread_bps[sym],
                            "top_depth_usd": top_depth_usd[sym],
                        }

                        trades = fetch_recent_trades(sym)
                        tmetrics = parse_trade_metrics(trades, now, MD_TRADES_WINDOW_SEC)
                        trade_volume_1m[sym] = float(tmetrics.get("trade_volume_1m", 0.0))
                        trade_count_1m[sym] = float(tmetrics.get("trade_count_1m", 0.0))
                        trade_volume_buy_ratio[sym] = float(tmetrics.get("trade_volume_buy_ratio", 0.0))
                        aggr_buy_volume_1m[sym] = float(tmetrics.get("aggr_buy_volume_1m", 0.0))
                        aggr_sell_volume_1m[sym] = float(tmetrics.get("aggr_sell_volume_1m", 0.0))
                        aggr_delta_1m[sym] = float(tmetrics.get("aggr_delta_1m", 0.0))
                        aggr_delta_hist[sym].append((now, aggr_delta_1m.get(sym, 0.0)))
                        volume_imbalance_1m[sym] = float(tmetrics.get("volume_imbalance_1m", 0.0))
                        buy_pressure_1m[sym] = float(tmetrics.get("buy_pressure_1m", 0.0))
                        sell_pressure_1m[sym] = float(tmetrics.get("sell_pressure_1m", 0.0))

                        trade_hist[sym].append(
                            {
                                "total": trade_volume_1m[sym],
                                "buy": aggr_buy_volume_1m[sym],
                                "sell": aggr_sell_volume_1m[sym],
                                "delta": aggr_delta_1m[sym],
                            }
                        )
                        tot5 = sum(x.get("total", 0.0) for x in trade_hist[sym])
                        buy5 = sum(x.get("buy", 0.0) for x in trade_hist[sym])
                        sell5 = sum(x.get("sell", 0.0) for x in trade_hist[sym])
                        delta5 = sum(x.get("delta", 0.0) for x in trade_hist[sym])
                        aggr_delta_5m[sym] = delta5
                        volume_imbalance_5m[sym] = (delta5 / tot5) if tot5 > 0 else 0.0

                        if abs(ret_1m[sym]) < 0.0005 and trade_volume_1m[sym] > 0:
                            absorption_ratio_bid[sym] = buy_pressure_1m[sym]
                            absorption_ratio_ask[sym] = sell_pressure_1m[sym]
                        else:
                            absorption_ratio_bid[sym] = 0.0
                            absorption_ratio_ask[sym] = 0.0

                        vol15_hist[sym].append(vol_15m[sym])
                        depth_hist[sym].append(top_depth_usd[sym])
                        vol_p90 = percentile(list(vol15_hist[sym]), 0.90)
                        depth_p10 = percentile(list(depth_hist[sym]), 0.10)
                        vol_15m_p90[sym] = vol_p90
                        top_depth_usd_p10[sym] = depth_p10
                        vol_spike[sym] = 1.0 if vol_15m[sym] > max(vol_p90, 1e-12) else 0.0
                        liquidity_drop[sym] = 1.0 if top_depth_usd[sym] < max(depth_p10, 1e-12) else 0.0

                        ctx = perp_ctx_cache.get(sym, {}) if isinstance(perp_ctx_cache, dict) else {}
                        if not isinstance(ctx, dict):
                            ctx = {}
                        funding = float(ctx.get("funding", 0.0) or 0.0)
                        oi = float(ctx.get("openInterest", 0.0) or 0.0)
                        premium = ctx.get("premium")
                        mark = ctx.get("markPx")
                        oracle = ctx.get("oraclePx")
                        basis = 0.0
                        try:
                            if premium is not None:
                                basis = float(premium) * 10000.0
                            elif mark is not None and oracle is not None and float(oracle) > 0:
                                basis = ((float(mark) / float(oracle)) - 1.0) * 10000.0
                        except Exception:
                            basis = 0.0

                        funding_rate[sym] = funding
                        open_interest[sym] = oi
                        basis_bps[sym] = basis
                        next_funding_ts[sym] = ""

                        oi_hist[sym].append((now, oi))
                        oi_ref = oi_hist[sym][0][1] if oi_hist[sym] else oi
                        for ts, val in reversed(oi_hist[sym]):
                            if ts <= now - 900:
                                oi_ref = val
                                break
                        if abs(oi_ref) <= 1e-9:
                            oi_change_15m[sym] = 0.0
                        else:
                            oi_change_15m[sym] = (oi / oi_ref) - 1.0

                    btc_ret_1m_val = ret_1m.get("BTC", 0.0)
                    btc_ret_15m_val = ret_15m.get("BTC", 0.0)
                    btc_vol_15m_val = vol_15m.get("BTC", 0.0)
                    eth_ret_1m_val = ret_1m.get("ETH", 0.0)
                    eth_ret_15m_val = ret_15m.get("ETH", 0.0)

                    ret_vals = [float(v) for v in ret_1m.values()] if ret_1m else []
                    ret_mean = sum(ret_vals) / max(len(ret_vals), 1)
                    ret_std = math.sqrt(sum((x - ret_mean) ** 2 for x in ret_vals) / max(len(ret_vals) - 1, 1)) if len(ret_vals) > 1 else 0.0

                    btc_series = returns_series(mids_hist.get("BTC", deque()), now, 60) if mids_hist.get("BTC") else []
                    eth_series = returns_series(mids_hist.get("ETH", deque()), now, 60) if mids_hist.get("ETH") else []

                    for sym in UNIVERSE:
                        btc_ret_1m[sym] = btc_ret_1m_val
                        btc_ret_15m[sym] = btc_ret_15m_val
                        btc_vol_15m[sym] = btc_vol_15m_val
                        eth_ret_1m[sym] = eth_ret_1m_val
                        eth_ret_15m[sym] = eth_ret_15m_val
                        market_ret_mean_1m[sym] = ret_mean
                        market_ret_std_1m[sym] = ret_std

                        if sym == "BTC":
                            corr_btc_1h[sym] = 1.0
                        else:
                            series = returns_series(mids_hist.get(sym, deque()), now, 60) if mids_hist.get(sym) else []
                            corr_btc_1h[sym] = corr_coeff(series, btc_series)

                        if sym == "ETH":
                            corr_eth_1h[sym] = 1.0
                        else:
                            series = returns_series(mids_hist.get(sym, deque()), now, 60) if mids_hist.get(sym) else []
                            corr_eth_1h[sym] = corr_coeff(series, eth_series)

                        trend_strength_15m[sym] = abs(ret_15m.get(sym, 0.0)) / max(vol_15m.get(sym, 1e-6), 1e-6)
                        v_p90 = max(vol_15m_p90.get(sym, 0.0), 1e-9)
                        v_ratio = vol_15m.get(sym, 0.0) / v_p90 if v_p90 > 0 else 0.0
                        if v_ratio > 2.0:
                            vol_regime[sym] = 3.0
                        elif v_ratio > 1.0:
                            vol_regime[sym] = 2.0
                        elif v_ratio > 0.6:
                            vol_regime[sym] = 1.0
                        else:
                            vol_regime[sym] = 0.0

                        d_p10 = max(top_depth_usd_p10.get(sym, 0.0), 1e-9)
                        d_ratio = top_depth_usd.get(sym, 0.0) / d_p10 if d_p10 > 0 else 0.0
                        if d_ratio < 1.0:
                            liq_regime[sym] = 0.0
                        elif d_ratio < 2.0:
                            liq_regime[sym] = 1.0
                        else:
                            liq_regime[sym] = 2.0

                    fs = FeatureSnapshot1m(
                        asof_minute=last_emitted_minute,   # the minute that just finished
                        universe=UNIVERSE,
                        mid_px=mid_px,
                        ret_1m=ret_1m,
                        ret_5m=ret_5m,
                        ret_1h=ret_1h,
                        vol_1h=vol_1h,
                        spread_bps=spread_bps,
                        book_imbalance=book_imbalance,
                        book_imbalance_l1=book_imbalance_l1,
                        book_imbalance_l5=book_imbalance_l5,
                        book_imbalance_l10=book_imbalance_l10,
                        top_depth_usd=top_depth_usd,
                        top_depth_usd_l10=top_depth_usd_l10,
                        microprice=microprice,
                        liq_intensity=liq_intensity,
                        liquidity_score=liquidity_score,
                        trade_volume_1m=trade_volume_1m,
                        trade_volume_buy_ratio=trade_volume_buy_ratio,
                        trade_count_1m=trade_count_1m,
                        aggr_buy_volume_1m=aggr_buy_volume_1m,
                        aggr_sell_volume_1m=aggr_sell_volume_1m,
                        aggr_delta_1m=aggr_delta_1m,
                        aggr_delta_5m=aggr_delta_5m,
                        volume_imbalance_1m=volume_imbalance_1m,
                        volume_imbalance_5m=volume_imbalance_5m,
                        buy_pressure_1m=buy_pressure_1m,
                        sell_pressure_1m=sell_pressure_1m,
                        absorption_ratio_bid=absorption_ratio_bid,
                        absorption_ratio_ask=absorption_ratio_ask,
                        microprice_change_1m=microprice_change_1m,
                        book_imbalance_change_1m=book_imbalance_change_1m,
                        spread_change_1m=spread_change_1m,
                        depth_change_1m=depth_change_1m,
                        bid_slope=bid_slope,
                        ask_slope=ask_slope,
                        queue_imbalance_l1=queue_imbalance_l1,
                    )
                    # cycle_id = last_emitted_minute.replace(":", "").replace("-", "").replace("T", "T")
                   
                    env = Envelope(source="market_data", cycle_id=cycle_id_from_asof_minute(fs.asof_minute))

                    bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": fs.model_dump()}))
                    MSG_OUT.labels(SERVICE, STREAM_OUT).inc()

                    fs15 = FeatureSnapshot15m(
                        asof_minute=fs.asof_minute,
                        window_start_minute=utc_minute_key(now - 14 * 60),
                        universe=UNIVERSE,
                        mid_px=mid_px,
                        funding_rate=funding_rate,
                        next_funding_ts=next_funding_ts,
                        basis_bps=basis_bps,
                        open_interest=open_interest,
                        oi_change_15m=oi_change_15m,
                        ret_15m=ret_15m,
                        ret_30m=ret_30m,
                        ret_1h=ret_1h,
                        vol_15m=vol_15m,
                        vol_1h=vol_1h,
                        spread_bps=spread_bps,
                        book_imbalance_l1=book_imbalance_l1,
                        book_imbalance_l5=book_imbalance_l5,
                        book_imbalance_l10=book_imbalance_l10,
                        top_depth_usd=top_depth_usd,
                        top_depth_usd_l10=top_depth_usd_l10,
                        microprice=microprice,
                        liquidity_score=liquidity_score,
                        reject_rate_15m=reject_rate_15m,
                        p95_latency_ms_15m=p95_latency_ms_15m,
                        slippage_bps_15m=slippage_bps_15m,
                        reject_rate_15m_delta=reject_rate_15m_delta,
                        p95_latency_ms_15m_delta=p95_latency_ms_15m_delta,
                        slippage_bps_15m_delta=slippage_bps_15m_delta,
                        trend_15m=trend_15m,
                        trend_1h=trend_1h,
                        trend_agree=trend_agree,
                        rsi_14_1m=rsi_14_1m,
                        vol_15m_p90=vol_15m_p90,
                        vol_spike=vol_spike,
                        top_depth_usd_p10=top_depth_usd_p10,
                        liquidity_drop=liquidity_drop,
                        trade_volume_1m=trade_volume_1m,
                        trade_volume_buy_ratio=trade_volume_buy_ratio,
                        trade_count_1m=trade_count_1m,
                        aggr_buy_volume_1m=aggr_buy_volume_1m,
                        aggr_sell_volume_1m=aggr_sell_volume_1m,
                        aggr_delta_1m=aggr_delta_1m,
                        aggr_delta_5m=aggr_delta_5m,
                        volume_imbalance_1m=volume_imbalance_1m,
                        volume_imbalance_5m=volume_imbalance_5m,
                        buy_pressure_1m=buy_pressure_1m,
                        sell_pressure_1m=sell_pressure_1m,
                        absorption_ratio_bid=absorption_ratio_bid,
                        absorption_ratio_ask=absorption_ratio_ask,
                        microprice_change_1m=microprice_change_1m,
                        book_imbalance_change_1m=book_imbalance_change_1m,
                        spread_change_1m=spread_change_1m,
                        depth_change_1m=depth_change_1m,
                        bid_slope=bid_slope,
                        ask_slope=ask_slope,
                        queue_imbalance_l1=queue_imbalance_l1,
                        btc_ret_1m=btc_ret_1m,
                        btc_ret_15m=btc_ret_15m,
                        btc_vol_15m=btc_vol_15m,
                        eth_ret_1m=eth_ret_1m,
                        eth_ret_15m=eth_ret_15m,
                        market_ret_mean_1m=market_ret_mean_1m,
                        market_ret_std_1m=market_ret_std_1m,
                        corr_btc_1h=corr_btc_1h,
                        corr_eth_1h=corr_eth_1h,
                        trend_strength_15m=trend_strength_15m,
                        vol_regime=vol_regime,
                        liq_regime=liq_regime,
                    )
                    bus.xadd_json(STREAM_OUT_15M, require_env({"env": env.model_dump(), "data": fs15.model_dump()}))
                    MSG_OUT.labels(SERVICE, STREAM_OUT_15M).inc()

                    # NEW: 1H block — publish at the top of every hour
                    _now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
                    if _now_dt.minute == 0:
                        try:
                            fs1h_per_sym = {}
                            for sym in UNIVERSE:
                                if sym not in mids_hist or not mids_hist[sym]:
                                    continue
                                feats = compute_1h_features(
                                    sym,
                                    mids_hist[sym],
                                    now,
                                    aggr_delta_hist=aggr_delta_hist.get(sym),
                                )
                                fs1h_per_sym[sym] = feats

                            if fs1h_per_sym:
                                window_start = utc_minute_key(now - 3600)
                                fs1h = FeatureSnapshot1h(
                                    asof_minute=last_emitted_minute,
                                    window_start_minute=window_start,
                                    universe=UNIVERSE,
                                    mid_px={sym: mids_hist[sym][-1][1] for sym in UNIVERSE if sym in mids_hist and mids_hist[sym]},
                                    ret_1h={sym: fs1h_per_sym[sym]["ret_1h"] for sym in fs1h_per_sym},
                                    ret_4h={sym: fs1h_per_sym[sym]["ret_4h"] for sym in fs1h_per_sym},
                                    vol_1h={sym: fs1h_per_sym[sym]["vol_1h"] for sym in fs1h_per_sym},
                                    vol_4h={sym: fs1h_per_sym[sym]["vol_4h"] for sym in fs1h_per_sym},
                                    trend_1h={sym: float(fs1h_per_sym[sym]["trend_1h"]) for sym in fs1h_per_sym},
                                    trend_4h={sym: float(fs1h_per_sym[sym]["trend_4h"]) for sym in fs1h_per_sym},
                                    rsi_14_1h={sym: fs1h_per_sym[sym]["rsi_14_1h"] for sym in fs1h_per_sym},
                                    aggr_delta_1h={sym: fs1h_per_sym[sym]["aggr_delta_1h"] for sym in fs1h_per_sym},
                                    funding_rate=funding_rate,
                                    basis_bps=basis_bps,
                                    vol_regime=vol_regime,
                                    trend_agree=trend_agree,
                                    book_imbalance_l10=book_imbalance_l10,
                                )
                                env_1h = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                                bus.xadd_json(STREAM_OUT_1H, require_env({"env": env_1h.model_dump(), "data": fs1h.model_dump()}))
                                MSG_OUT.labels(SERVICE, STREAM_OUT_1H).inc()
                                logger.info(f"Published md.features.1h | asof={fs1h.asof_minute}")
                        except Exception as e:
                            logger.warning(f"Failed to publish 1H features: {e}")
                            ERR.labels(SERVICE, "1h_features").inc()

                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "md.features.1m", "data": {"asof_minute": fs.asof_minute}}))
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "md.features.15m", "data": {"asof_minute": fs15.asof_minute, "window_start_minute": fs15.window_start_minute}}))
                    cache_age = max(now - last_perp_ctx_ts, 0.0)
                    if cache_age > PERP_CTX_STALE_SECONDS:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "md.perp_ctx_stale",
                                    "data": {"cache_age_sec": cache_age, "stale_threshold_sec": PERP_CTX_STALE_SECONDS},
                                }
                            ),
                        )
                    if L2_ENABLED:
                        l2_cache_age = max(now - last_l2_ts, 0.0)
                        if l2_cache_age > L2_STALE_SECONDS:
                            bus.xadd_json(
                                AUDIT,
                                require_env(
                                    {
                                        "env": env.model_dump(),
                                        "event": "md.l2_stale",
                                        "data": {"cache_age_sec": l2_cache_age, "stale_threshold_sec": L2_STALE_SECONDS},
                                    }
                                ),
                            )
                    MSG_OUT.labels(SERVICE, AUDIT).inc()

                    missing = set(missing_this_minute) | set(collect_missing_symbols(allmids, UNIVERSE, mids_hist))
                    if missing:
                        bus.xadd_json(
                            AUDIT,
                            require_env(
                                {
                                    "env": env.model_dump(),
                                    "event": "md.missing_symbols",
                                    "data": {"asof_minute": fs.asof_minute, "missing": sorted(missing)},
                                }
                            ),
                        )
                        MSG_OUT.labels(SERVICE, AUDIT).inc()
                        missing_this_minute.clear()

                    last_emitted_minute = minute_key
                    note_ok(env)

            except Exception as e:
                env = Envelope(source="market_data", cycle_id=cycle_id_from_asof_minute(utc_minute_key(time.time())))
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "err": str(e)}))
                MSG_OUT.labels(SERVICE, AUDIT).inc()
                note_error(env, "loop", e)

            dt = time.time() - t0
            sleep_s = max(POLL_SECONDS - dt, 0.05)
            time.sleep(sleep_s)
            LAT.labels(SERVICE, "loop").observe(time.time() - t0)

if __name__ == "__main__":
    main()
