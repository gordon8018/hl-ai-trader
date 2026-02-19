# services/market_data/app.py
import os
import time
import math
import httpx
from collections import deque
from datetime import datetime, timezone

from shared.bus.guard import require_env
from shared.bus.redis_streams import RedisStreams
from shared.schemas import Envelope, FeatureSnapshot1m
from shared.time_utils import cycle_id_from_asof_minute

REDIS_URL = os.environ["REDIS_URL"]
HL_HTTP_URL = os.environ.get("HL_HTTP_URL", "https://api.hyperliquid.xyz")
UNIVERSE = os.environ.get("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE").split(",")
CYCLE_SECONDS = int(os.environ.get("CYCLE_SECONDS", "60"))
POLL_SECONDS = float(os.environ.get("MD_POLL_SECONDS", "2.0"))

STREAM_OUT = "md.features.1m"
AUDIT = "audit.logs"

def utc_minute_key(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(second=0, microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")

def calc_return(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return (b / a) - 1.0

def main():
    bus = RedisStreams(REDIS_URL)

    # rolling store for mid prices
    # store last ~ 90 minutes at 2s sampling -> 2700 points; keep smaller
    mids_hist = {sym: deque(maxlen=4000) for sym in UNIVERSE}  # (ts, mid)
    spreads_hist = {sym: deque(maxlen=4000) for sym in UNIVERSE}

    last_emitted_minute = None

    with httpx.Client(timeout=5.0) as client:
        while True:
            t0 = time.time()
            try:
                # /info allMids: returns dict symbol -> string mid. :contentReference[oaicite:4]{index=4}
                resp = client.post(f"{HL_HTTP_URL}/info", json={"type": "allMids"})
                resp.raise_for_status()
                allmids = resp.json()  # { "BTC":"12345.6", ... }
                now = time.time()

                for sym in UNIVERSE:
                    v = allmids.get(sym)
                    if v is None:
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
                    spread_bps, book_imbalance, liq_intensity, liquidity_score = {}, {}, {}, {}

                    for sym in UNIVERSE:
                        if not mids_hist[sym]:
                            continue
                        mid_px[sym] = mids_hist[sym][-1][1]

                        # helper: find price at/near lookback
                        def price_ago(seconds: float):
                            target = now - seconds
                            # walk from end backwards (deques are small enough)
                            for ts, px in reversed(mids_hist[sym]):
                                if ts <= target:
                                    return px
                            return mids_hist[sym][0][1]

                        p_now = mid_px[sym]
                        p_1m = price_ago(60)
                        p_5m = price_ago(300)
                        p_1h = price_ago(3600)

                        ret_1m[sym] = calc_return(p_1m, p_now)
                        ret_5m[sym] = calc_return(p_5m, p_now)
                        ret_1h[sym] = calc_return(p_1h, p_now)

                        # realized vol (approx) over last hour using log returns on 1m grid
                        # build 1m sampled series from stored points
                        # (simple + robust; good enough for MVP)
                        # take last 60 minutes by selecting nearest samples every minute
                        series = []
                        for k in range(60, 0, -1):
                            series.append(price_ago(k * 60))
                        series.append(p_now)
                        lrs = []
                        for i in range(1, len(series)):
                            a, b = series[i-1], series[i]
                            if a > 0 and b > 0:
                                lrs.append(math.log(b/a))
                        if lrs:
                            mean = sum(lrs) / len(lrs)
                            var = sum((x-mean)**2 for x in lrs) / max(len(lrs)-1, 1)
                            vol_1h[sym] = math.sqrt(var) * math.sqrt(60)  # per-hour-ish scale
                        else:
                            vol_1h[sym] = 0.0

                        # Placeholder microstructure (MVP):
                        # If later you subscribe l2Book, fill spread_bps / imbalance / liquidity_score.
                        spread_bps[sym] = 0.0
                        book_imbalance[sym] = 0.0
                        liq_intensity[sym] = 0.0
                        liquidity_score[sym] = 0.0

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
                    bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "md.features.1m", "data": {"asof_minute": fs.asof_minute}}))

                    last_emitted_minute = minute_key

            except Exception as e:
                env = Envelope(source="market_data", cycle_id=cycle_id_from_asof_minute(utc_minute_key(time.time())))
                bus.xadd_json(AUDIT, require_env({"env": env.model_dump(), "event": "error", "err": str(e)}))

            dt = time.time() - t0
            sleep_s = max(POLL_SECONDS - dt, 0.05)
            time.sleep(sleep_s)

if __name__ == "__main__":
    main()
