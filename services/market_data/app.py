# services/market_data/app.py
import os
import time
import math
import json
import httpx
from typing import List
from collections import deque
from datetime import datetime, timezone

from shared.bus.guard import require_env
from shared.bus.redis_streams import RedisStreams
from shared.schemas import Envelope, FeatureSnapshot1m, FeatureSnapshot15m
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
ERROR_STREAK_THRESHOLD = int(os.environ.get("ERROR_STREAK_THRESHOLD", "3"))

SERVICE = "market_data"
os.environ["SERVICE_NAME"] = SERVICE

STREAM_OUT = "md.features.1m"
STREAM_OUT_15M = "md.features.15m"
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
    depth_usd = sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in bid5 if isinstance(x, dict))
    depth_usd += sum(_to_float(x.get("px")) * _to_float(x.get("sz")) for x in ask5 if isinstance(x, dict))
    microprice = ((ask_px * bid_sz) + (bid_px * ask_sz)) / denom_l1 if denom_l1 > 0 else mid
    liquidity_score = min(1.0, max(0.0, (depth_usd / 1_000_000.0) / (1.0 + max(spread_bps, 0.0))))

    return {
        "spread_bps": spread_bps,
        "book_imbalance_l1": imbalance_l1,
        "book_imbalance_l5": imbalance_l5,
        "top_depth_usd": depth_usd,
        "microprice": microprice,
        "liquidity_score": liquidity_score,
    }

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
    # store last ~ 90 minutes at 2s sampling -> 2700 points; keep smaller
    mids_hist = {sym: deque(maxlen=4000) for sym in UNIVERSE}  # (ts, mid)
    oi_hist = {sym: deque(maxlen=120) for sym in UNIVERSE}  # (ts, open_interest)
    perp_ctx_cache = {}
    last_perp_ctx_ts = 0.0
    l2_metrics_cache = {}
    last_l2_ts = 0.0

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
                    book_imbalance_l1, book_imbalance_l5, top_depth_usd, microprice = {}, {}, {}, {}
                    funding_rate, next_funding_ts, basis_bps, open_interest, oi_change_15m = {}, {}, {}, {}, {}
                    reject_rate_15m, p95_latency_ms_15m, slippage_bps_15m = {}, {}, {}

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

                        # Microstructure from cached l2Book snapshots; fallback to zeros.
                        l2 = l2_metrics_cache.get(sym, {})
                        l2m = l2.get("metrics", {}) if isinstance(l2, dict) else {}
                        spread_bps[sym] = _to_float(l2m.get("spread_bps"), 0.0)
                        book_imbalance_l1[sym] = _to_float(l2m.get("book_imbalance_l1"), 0.0)
                        book_imbalance_l5[sym] = _to_float(l2m.get("book_imbalance_l5"), 0.0)
                        top_depth_usd[sym] = _to_float(l2m.get("top_depth_usd"), 0.0)
                        microprice[sym] = _to_float(l2m.get("microprice"), p_now)
                        liquidity_score[sym] = _to_float(l2m.get("liquidity_score"), 0.0)
                        # Keep existing 1m field populated for backward compatibility.
                        book_imbalance[sym] = book_imbalance_l1[sym]
                        liq_intensity[sym] = top_depth_usd[sym]

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
                        liq_intensity=liq_intensity,
                        liquidity_score=liquidity_score,
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
                        top_depth_usd=top_depth_usd,
                        microprice=microprice,
                        liquidity_score=liquidity_score,
                        reject_rate_15m=reject_rate_15m,
                        p95_latency_ms_15m=p95_latency_ms_15m,
                        slippage_bps_15m=slippage_bps_15m,
                    )
                    bus.xadd_json(STREAM_OUT_15M, require_env({"env": env.model_dump(), "data": fs15.model_dump()}))
                    MSG_OUT.labels(SERVICE, STREAM_OUT_15M).inc()

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
