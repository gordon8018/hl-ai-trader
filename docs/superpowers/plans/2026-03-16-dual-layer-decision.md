# Dual-Layer Decision Architecture Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-loop 15m decision system with a 1H LLM direction layer + 15m rule-based confirmation layer, add 1H market features, improve capital utilization, and build an offline backtester.

**Architecture:** `market_data` publishes hourly `md.features.1h` snapshots. `ai_decision` runs two polling loops: Layer 1 reads `md.features.1h`, calls LLM to produce a `DirectionBias` (LONG/SHORT/FLAT per symbol, valid 60 min), stored in Redis. Layer 2 reads `md.features.15m`, reads the cached `DirectionBias`, and applies a 3-signal confirmation rule before executing — no LLM call in Layer 2. Capital utilization is improved via dynamic EWMA alpha and mode-aware MAX_GROSS. A standalone `backtester/` module evaluates signal quality offline before live deployment.

**Tech Stack:** Python 3.11, Pydantic v2, Redis Streams via `shared.bus.redis_streams.RedisStreams`, pytest, existing `shared.ai.llm_client.call_llm_json`.

**Spec:** `docs/superpowers/specs/2026-03-16-dual-layer-decision-improvement-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `shared/schemas.py` | Modify | Add `FeatureSnapshot1h`, `SymbolBias`, `DirectionBias` |
| `infra/.env.example` | Create | Parameter template (committed); copy to `infra/.env` locally with real secrets |
| `services/market_data/app.py` | Modify | Extend price history to 5H; compute + publish `md.features.1h` at each hour boundary |
| `services/ai_decision/app.py` | Modify | Dual-loop main(); Layer 1 (1H LLM); Layer 2 (15m rules); dynamic EWMA; MIN_NOTIONAL; daily cap |
| `backtester/replay.py` | Create | Read Redis stream history; simulate Layer1+Layer2 logic; output predictions |
| `backtester/evaluator.py` | Create | Score predictions vs forward price outcomes; print win rate / PnL estimate |
| `backtester/run_backtest.sh` | Create | CLI entry point |
| `tests/test_schemas_1h.py` | Create | Round-trip tests for new schemas |
| `tests/test_market_data_1h.py` | Create | 1H feature computation unit tests |
| `tests/test_ai_decision_layer1.py` | Create | parse_direction_bias and is_direction_bias_valid tests |
| `tests/test_ai_decision_layer2.py` | Create | Direction confirmation rule logic unit tests |
| `tests/test_ai_decision_capital.py` | Create | Dynamic EWMA + MAX_GROSS selection tests |

---

## Chunk 1: Foundation — Schemas + infra/.env

### Task 1: Add FeatureSnapshot1h, SymbolBias, DirectionBias to shared/schemas.py

**Files:**
- Modify: `shared/schemas.py`
- Create: `tests/test_schemas_1h.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_schemas_1h.py
import pytest
from shared.schemas import FeatureSnapshot1h, DirectionBias, SymbolBias


def test_feature_snapshot_1h_roundtrip():
    fs = FeatureSnapshot1h(
        asof_minute="2026-03-16T15:00:00Z",
        window_start_minute="2026-03-16T14:00:00Z",
        universe=["BTC", "ETH"],
        mid_px={"BTC": 70000.0, "ETH": 2100.0},
        ret_1h={"BTC": 0.005, "ETH": 0.003},
        ret_4h={"BTC": 0.012, "ETH": 0.008},
        vol_1h={"BTC": 0.01, "ETH": 0.012},
        vol_4h={"BTC": 0.02, "ETH": 0.022},
        trend_1h={"BTC": 1.0, "ETH": 1.0},
        trend_4h={"BTC": 1.0, "ETH": 0.0},
        rsi_14_1h={"BTC": 58.0, "ETH": 52.0},
        aggr_delta_1h={"BTC": 1500.0, "ETH": 300.0},
    )
    dumped = fs.model_dump()
    fs2 = FeatureSnapshot1h(**dumped)
    assert fs2.asof_minute == fs.asof_minute
    assert fs2.ret_4h["BTC"] == 0.012


def test_direction_bias_valid():
    db = DirectionBias(
        asof_minute="2026-03-16T15:00:00Z",
        valid_until_minute="2026-03-16T16:00:00Z",
        market_state="TRENDING",
        biases=[
            SymbolBias(symbol="BTC", direction="LONG", confidence=0.72),
            SymbolBias(symbol="ETH", direction="FLAT", confidence=0.40),
        ],
    )
    assert db.biases[0].direction == "LONG"
    assert db.biases[1].confidence == 0.40


def test_direction_bias_confidence_bounds():
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="LONG", confidence=1.5)
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="LONG", confidence=-0.1)


def test_direction_bias_invalid_direction():
    with pytest.raises(Exception):
        SymbolBias(symbol="BTC", direction="MAYBE", confidence=0.5)
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
python -m pytest tests/test_schemas_1h.py -v
```
Expected: `ImportError` or `AttributeError` — `FeatureSnapshot1h` not found yet.

- [ ] **Step 3: Add new schemas to shared/schemas.py**

Insert after the `FeatureSnapshot15m` class (after line 168), before the `Position` class:

```python
class SymbolBias(BaseModel):
    symbol: str
    direction: Literal["LONG", "SHORT", "FLAT"]
    confidence: float = Field(ge=0.0, le=1.0)

class DirectionBias(BaseModel):
    asof_minute: str
    valid_until_minute: str
    market_state: Literal["TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"]
    biases: List[SymbolBias]
    rationale: str = ""

class FeatureSnapshot1h(BaseModel):
    asof_minute: str
    window_start_minute: str
    universe: List[str]
    mid_px: Dict[str, float]
    # 1H features (also in FeatureSnapshot15m but recomputed fresh at hour boundary)
    ret_1h: Dict[str, float] = Field(default_factory=dict)
    vol_1h: Dict[str, float] = Field(default_factory=dict)
    trend_1h: Dict[str, float] = Field(default_factory=dict)
    funding_rate: Dict[str, float] = Field(default_factory=dict)
    basis_bps: Dict[str, float] = Field(default_factory=dict)
    oi_change_1h: Dict[str, float] = Field(default_factory=dict)
    vol_regime: Dict[str, float] = Field(default_factory=dict)
    trend_agree: Dict[str, float] = Field(default_factory=dict)
    book_imbalance_l10: Dict[str, float] = Field(default_factory=dict)
    # New 4H features
    ret_4h: Dict[str, float] = Field(default_factory=dict)
    vol_4h: Dict[str, float] = Field(default_factory=dict)
    trend_4h: Dict[str, float] = Field(default_factory=dict)
    # New 1H aggregates
    rsi_14_1h: Dict[str, float] = Field(default_factory=dict)
    aggr_delta_1h: Dict[str, float] = Field(default_factory=dict)
```

Also add `FeatureSnapshot1h`, `SymbolBias`, `DirectionBias` to the file's imports where needed (they already import `Literal` in the existing code).

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_schemas_1h.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Confirm existing schema tests still pass**

```bash
python -m pytest tests/test_schemas_roundtrip.py tests/test_schemas_invalid.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas.py tests/test_schemas_1h.py
git commit -m "feat(schemas): add FeatureSnapshot1h, DirectionBias, SymbolBias"
```

---

### Task 2: Create infra/.env.example and infra/.env with updated parameters

**Files:**
- Create: `infra/.env.example` (committed to git — no secrets)
- Create: `infra/.env` (local only — gitignored, contains real secrets)

- [ ] **Step 1: Create infra/.env.example (template, no real secrets)**

```bash
# infra/.env.example  ← commit this to git
# Copy to infra/.env and fill in real values
# ── Redis ─────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Trading universe ──────────────────────────────────
UNIVERSE=BTC,ETH,SOL,ADA,DOGE

# ── LLM configuration ─────────────────────────────────
AI_USE_LLM=false
AI_LLM_ENDPOINT=
AI_LLM_API_KEY=
AI_LLM_MODEL=gpt-4o-mini
AI_LLM_TIMEOUT_MS=3000

# ── Decision frequency (dual-layer) ───────────────────
AI_STREAM_IN=md.features.15m
AI_STREAM_IN_1H=md.features.1h
AI_MIN_MAJOR_INTERVAL_MIN=60
AI_DECISION_HORIZON=4h

# ── Capital utilization ───────────────────────────────
MAX_GROSS=0.35
MAX_GROSS_TRENDING_HIGH=0.65
MAX_GROSS_TRENDING_MID=0.45
MAX_GROSS_SIDEWAYS=0.00
MAX_GROSS_VOLATILE=0.20
MAX_NET=0.20
CAP_BTC_ETH=0.45
CAP_ALT=0.25
AI_SMOOTH_ALPHA=0.25
AI_SMOOTH_ALPHA_MID=0.40
AI_SMOOTH_ALPHA_HIGH=0.60
AI_MIN_CONFIDENCE=0.65
AI_CONFIDENCE_HIGH_THRESHOLD=0.80
AI_TURNOVER_CAP=0.20
AI_SIGNAL_DELTA_THRESHOLD=0.15
MIN_NOTIONAL_USD=50.0

# ── Frequency controls ────────────────────────────────
MAX_TRADES_PER_DAY=30

# ── Risk controls ─────────────────────────────────────
POSITION_MAX_AGE_MIN=120
DAILY_DRAWDOWN_HALT_USD=5.0
MAX_CONSECUTIVE_LOSS=2
PNL_DISABLE_DURATION_MIN=60
DIRECTION_REVERSAL_WINDOW_MIN=30
DIRECTION_REVERSAL_THRESHOLD=2
DIRECTION_REVERSAL_PENALTY=zero
COOLDOWN_MINUTES=15

# ── Market data ───────────────────────────────────────
MD_PRICE_HISTORY_MINUTES=310
```

- [ ] **Step 2: Verify ai_decision loads new env vars**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
source infra/.env 2>/dev/null || true
python -c "
import os
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
os.environ.setdefault('MIN_NOTIONAL_USD', '50.0')
os.environ.setdefault('MAX_TRADES_PER_DAY', '30')
print('MIN_NOTIONAL_USD:', os.environ.get('MIN_NOTIONAL_USD'))
print('MAX_TRADES_PER_DAY:', os.environ.get('MAX_TRADES_PER_DAY'))
"
```
Expected: prints `50.0` and `30`.

- [ ] **Step 3: Commit only the example file (never commit infra/.env)**

```bash
# Verify infra/.env is gitignored (it should already be)
grep -q "\.env" .gitignore || echo "WARNING: add infra/.env to .gitignore"

git add infra/.env.example
git commit -m "feat(infra): add .env.example with dual-layer and capital utilization parameters"
```

---

## Chunk 2: market_data — 1H Feature Stream

### Task 3: Extend price history window + compute 1H features

**Files:**
- Modify: `services/market_data/app.py`
- Create: `tests/test_market_data_1h.py`

- [ ] **Step 1: Write failing tests for 1H feature functions**

```python
# tests/test_market_data_1h.py
import importlib
import os
import sys
from collections import deque

def load_md():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.market_data.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def make_hist(prices_with_offsets_minutes: list, now_ts: float) -> deque:
    """Build a deque of (ts, px) from list of (minutes_ago, price)."""
    h = deque(maxlen=500)
    for mins_ago, px in sorted(prices_with_offsets_minutes, reverse=True):
        h.appendleft((now_ts - mins_ago * 60, px))
    return h


def test_rsi_from_hist_neutral():
    mod = load_md()
    import time
    now = time.time()
    # Flat price → RSI ~50
    hist = make_hist([(i, 100.0) for i in range(20, 0, -1)], now)
    hist.append((now, 100.0))
    rsi = mod.rsi_from_hist(hist, now, period=14)
    assert 45.0 <= rsi <= 55.0


def test_compute_1h_features_returns_expected_keys():
    mod = load_md()
    import time
    now = time.time()
    # Create 5h of price history at 100 + slow uptrend
    prices = [(i, 100.0 + (300 - i) * 0.01) for i in range(300, 0, -1)]
    hist = make_hist(prices, now)
    hist.append((now, 103.0))

    result = mod.compute_1h_features("BTC", hist, now)
    assert "ret_1h" in result
    assert "ret_4h" in result
    assert "vol_1h" in result
    assert "vol_4h" in result
    assert "trend_1h" in result
    assert "trend_4h" in result
    assert "rsi_14_1h" in result
    assert "aggr_delta_1h" in result


def test_compute_1h_features_uptrend():
    mod = load_md()
    import time
    now = time.time()
    # Strong uptrend: price rose 2% over 4 hours
    prices = [(i, 100.0 + (300 - i) * (2.0 / 300)) for i in range(300, 0, -1)]
    hist = make_hist(prices, now)
    hist.append((now, 102.0))
    result = mod.compute_1h_features("BTC", hist, now)
    assert result["ret_4h"] > 0.01   # ~2% over 4h
    assert result["trend_1h"] == 1   # uptrend
    assert result["trend_4h"] == 1
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_market_data_1h.py -v
```
Expected: `AttributeError: module has no attribute 'compute_1h_features'`.

- [ ] **Step 3: Add compute_1h_features() to market_data/app.py**

Add this function after the existing `realized_vol()` function (around line 82):

```python
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

    # RSI on hourly prices (sample 1 price per hour for 14 periods = 14h of data needed)
    rsi_14_1h = rsi_from_hist(hist, now_ts, period=14)  # reuses 1m-sampled RSI; acceptable approximation

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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_market_data_1h.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Extend mids_hist rolling window and add aggr_delta_hist**

In `services/market_data/app.py`, find the section where `mids_hist` deques are initialized (search for `deque(maxlen=`). Update the maxlen from `90` to `MD_PRICE_HISTORY_MINUTES` (read from env, default 310 to cover 4H + buffer):

At top of file add:
```python
MD_PRICE_HISTORY_MINUTES = int(os.environ.get("MD_PRICE_HISTORY_MINUTES", "310"))
```

Find `deque(maxlen=90)` (or similar) and replace with `deque(maxlen=MD_PRICE_HISTORY_MINUTES)`.

Also add `aggr_delta_hist` per symbol (rolling 70-minute buffer of 1m aggr_delta values):
```python
# Near where mids_hist is initialized:
aggr_delta_hist: Dict[str, deque] = {sym: deque(maxlen=70) for sym in UNIVERSE}
```

And in the 1m feature computation loop, after computing `aggr_delta_1m`, append to the per-symbol buffer:
```python
aggr_delta_hist[sym].append((now, aggr_delta_1m.get(sym, 0.0)))
```

- [ ] **Step 6: Run existing market_data tests**

```bash
python -m pytest tests/test_market_data_utils.py -v
```
Expected: all PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add services/market_data/app.py tests/test_market_data_1h.py
git commit -m "feat(market_data): add compute_1h_features() and extend price history to 310min"
```

---

### Task 4: Publish md.features.1h stream at each hour boundary

**Files:**
- Modify: `services/market_data/app.py`

- [ ] **Step 1: Add STREAM_OUT_1H constant and import FeatureSnapshot1h**

At top of `services/market_data/app.py`, find the existing stream constants:
```python
STREAM_OUT = "md.features.1m"
STREAM_OUT_15M = "md.features.15m"
```
Add:
```python
STREAM_OUT_1H = "md.features.1h"
```

Update the schema import line to include `FeatureSnapshot1h`:
```python
from shared.schemas import Envelope, FeatureSnapshot1m, FeatureSnapshot15m, FeatureSnapshot1h, current_cycle_id
```

- [ ] **Step 2: Add 1H publishing block in the main loop**

In the section where the 15m feature block is (search for `dt.minute % 15 == 0`), add an adjacent 1H block:

```python
# Existing 15m block:
if dt.second == 0 and dt.minute % 15 == 0:
    # ... existing 15m code ...

# NEW: 1H block — publish at the top of every hour
if dt.second == 0 and dt.minute == 0:
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
            # Collect cross-symbol fields
            window_start = utc_minute_key(now - 3600)
            fs1h = FeatureSnapshot1h(
                asof_minute=utc_minute_key(now),
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
                # Pass through fields that come from perp context (already computed in 15m)
                funding_rate={sym: perp_ctx.get(sym, {}).get("funding_rate", 0.0) for sym in UNIVERSE},
                basis_bps={sym: perp_ctx.get(sym, {}).get("basis_bps", 0.0) for sym in UNIVERSE},
                vol_regime={sym: float(fs15.vol_regime.get(sym, 0)) for sym in UNIVERSE} if 'fs15' in dir() else {},
                trend_agree={sym: float(fs15.trend_agree.get(sym, 0)) for sym in UNIVERSE} if 'fs15' in dir() else {},
                book_imbalance_l10={sym: l2_cache.get(sym, {}).get("imbalance_l10", 0.0) for sym in UNIVERSE},
            )
            env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
            bus.xadd_json(STREAM_OUT_1H, {"env": env.model_dump(), "data": fs1h.model_dump()})
            MSG_OUT.labels(SERVICE, STREAM_OUT_1H).inc()
            logger.info(f"Published md.features.1h | asof={fs1h.asof_minute}")
    except Exception as e:
        logger.warning(f"Failed to publish 1H features: {e}")
        ERR.labels(SERVICE, "1h_features").inc()
```

**Note:** The variable name for perp context and l2 cache may differ in the actual code. Check the existing 15m block to find the exact variable names for `funding_rate`, `basis_bps`, and l2 metrics, then use those same variables in the 1H block.

- [ ] **Step 3: Manual integration check (dry-run)**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
python -c "
import os
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
import services.market_data.app as md
print('STREAM_OUT_1H:', md.STREAM_OUT_1H)
print('MD_PRICE_HISTORY_MINUTES:', md.MD_PRICE_HISTORY_MINUTES)
print('compute_1h_features exists:', hasattr(md, 'compute_1h_features'))
"
```
Expected: prints `md.features.1h`, `310`, `True`.

- [ ] **Step 4: Run all market_data tests**

```bash
python -m pytest tests/test_market_data_utils.py tests/test_market_data_1h.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/market_data/app.py
git commit -m "feat(market_data): publish md.features.1h stream at hourly boundaries"
```

---

## Chunk 3: ai_decision — Dual-Layer Architecture

### Task 5: Layer 1 — 1H LLM direction decision

**Files:**
- Modify: `services/ai_decision/app.py`
- Create: `tests/test_ai_decision_layer1.py`

- [ ] **Step 1: Write failing tests for Layer 1 helper functions**

```python
# tests/test_ai_decision_layer1.py
import os
import sys
import importlib
import json

def load_ai():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE")
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_parse_direction_bias_valid():
    mod = load_ai()
    raw = json.dumps({
        "biases": [
            {"symbol": "BTC", "direction": "LONG", "confidence": 0.72},
            {"symbol": "ETH", "direction": "FLAT", "confidence": 0.45},
        ],
        "market_state": "TRENDING",
        "rationale": "strong trend"
    })
    db = mod.parse_direction_bias(raw, ["BTC", "ETH", "SOL", "ADA", "DOGE"])
    assert db is not None
    assert db.market_state == "TRENDING"
    assert len(db.biases) == 2
    btc = next(b for b in db.biases if b.symbol == "BTC")
    assert btc.direction == "LONG"


def test_parse_direction_bias_missing_symbols_filled_flat():
    mod = load_ai()
    # LLM only returned BTC — missing symbols should be filled as FLAT
    raw = json.dumps({
        "biases": [{"symbol": "BTC", "direction": "LONG", "confidence": 0.70}],
        "market_state": "TRENDING",
    })
    db = mod.parse_direction_bias(raw, ["BTC", "ETH"])
    symbols = {b.symbol for b in db.biases}
    assert "ETH" in symbols
    eth = next(b for b in db.biases if b.symbol == "ETH")
    assert eth.direction == "FLAT"


def test_parse_direction_bias_invalid_json_returns_none():
    mod = load_ai()
    db = mod.parse_direction_bias("not json", ["BTC", "ETH"])
    assert db is None


def test_is_direction_bias_valid_true():
    mod = load_ai()
    import json
    from shared.schemas import DirectionBias, SymbolBias
    now_iso = "2026-03-16T15:00:00Z"
    valid_until = "2026-03-16T16:00:00Z"
    db = DirectionBias(
        asof_minute=now_iso,
        valid_until_minute=valid_until,
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.70)],
    )
    assert mod.is_direction_bias_valid(db, now_iso) is True


def test_is_direction_bias_valid_expired():
    mod = load_ai()
    from shared.schemas import DirectionBias, SymbolBias
    db = DirectionBias(
        asof_minute="2026-03-16T13:00:00Z",
        valid_until_minute="2026-03-16T14:00:00Z",   # expired
        market_state="TRENDING",
        biases=[SymbolBias(symbol="BTC", direction="LONG", confidence=0.70)],
    )
    assert mod.is_direction_bias_valid(db, "2026-03-16T15:00:00Z") is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_ai_decision_layer1.py -v
```
Expected: `AttributeError` — `parse_direction_bias` not found.

- [ ] **Step 3: Add Layer 1 constants, prompt, and helper functions to ai_decision/app.py**

**3a. Update existing `AI_SMOOTH_ALPHA_HIGH` default** (line 88 in current app.py) from `"0.50"` to `"0.60"`:

```python
# Find this line:
AI_SMOOTH_ALPHA_HIGH = float(os.environ.get("AI_SMOOTH_ALPHA_HIGH", "0.50"))
# Change to:
AI_SMOOTH_ALPHA_HIGH = float(os.environ.get("AI_SMOOTH_ALPHA_HIGH", "0.60"))
```

**3b. Add new imports and constants** (after existing imports, before `SYSTEM_PROMPT`):

```python
from shared.schemas import DirectionBias, SymbolBias, FeatureSnapshot1h

STREAM_IN_1H = os.environ.get("AI_STREAM_IN_1H", "md.features.1h")
DIRECTION_BIAS_KEY = "latest.direction_bias"
GROUP_1H = "ai_grp_1h"
CONSUMER_1H = os.environ.get("CONSUMER_1H", "ai_layer1_1")

MIN_NOTIONAL_USD = float(os.environ.get("MIN_NOTIONAL_USD", "50.0"))
MAX_TRADES_PER_DAY = int(os.environ.get("MAX_TRADES_PER_DAY", "30"))

# Capital utilization: mode-aware MAX_GROSS
MAX_GROSS_TRENDING_HIGH = float(os.environ.get("MAX_GROSS_TRENDING_HIGH", "0.65"))
MAX_GROSS_TRENDING_MID  = float(os.environ.get("MAX_GROSS_TRENDING_MID",  "0.45"))
MAX_GROSS_SIDEWAYS      = float(os.environ.get("MAX_GROSS_SIDEWAYS",      "0.00"))
MAX_GROSS_VOLATILE      = float(os.environ.get("MAX_GROSS_VOLATILE",      "0.20"))

# Threshold at which TRENDING switches from MID → HIGH gross exposure (spec: 0.70)
MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD = float(os.environ.get("MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD", "0.70"))

# Dynamic EWMA alpha (by confidence)
AI_SMOOTH_ALPHA_MID  = float(os.environ.get("AI_SMOOTH_ALPHA_MID",  "0.40"))
# AI_SMOOTH_ALPHA (0.25) already exists; update AI_SMOOTH_ALPHA_HIGH default from 0.50 → 0.60:
# Find the existing line: AI_SMOOTH_ALPHA_HIGH = float(os.environ.get("AI_SMOOTH_ALPHA_HIGH", "0.50"))
# Change default to "0.60". The infra/.env value overrides at runtime anyway.
```

**3b. Add `SYSTEM_PROMPT_1H`** for the 1H direction LLM call (add after `SYSTEM_PROMPT`):

```python
SYSTEM_PROMPT_1H = (
    "You are a directional bias engine for crypto perpetual futures. "
    "Determine the likely price direction for the next 1-4 hours per symbol.\n"
    "OUTPUT: STRICT JSON only. Zero prose outside the JSON object.\n\n"

    "=== OUTPUT SCHEMA ===\n"
    "{\n"
    "  \"biases\": [{\"symbol\": str, \"direction\": \"LONG\"|\"SHORT\"|\"FLAT\", \"confidence\": float}],\n"
    "  \"market_state\": \"TRENDING\"|\"SIDEWAYS\"|\"VOLATILE\"|\"EMERGENCY\",\n"
    "  \"rationale\": str (max 300 chars)\n"
    "}\n\n"

    "=== DECISION RULES ===\n"
    "STEP -1 — SIDEWAYS filter (output FLAT if 2+ conditions met for a symbol):\n"
    "  - |ret_1h| < 0.003 AND trend_agree == 0\n"
    "  - vol_regime == 0\n"
    "  - rsi_14_1h between 45-55\n"
    "  - aggr_delta_1h sign differs from trend_1h sign\n"
    "  If market_state is SIDEWAYS for majority of symbols → set market_state=SIDEWAYS\n\n"

    "STEP 1 — TRENDING/VOLATILE regime:\n"
    "  VOLATILE: vol_spike signals or |ret_4h| > 0.03 → reduce all confidence by 0.15, prefer FLAT\n"
    "  TRENDING: trend_1h and trend_4h agree AND |ret_4h| > 0.005\n\n"

    "STEP 2 — Direction for TRENDING symbols (need 3+ signals for LONG, 3+ for SHORT):\n"
    "  LONG signals: ret_1h > 0.003, trend_1h > 0, trend_4h > 0, funding_rate > 0, rsi_14_1h < 65, aggr_delta_1h > 0\n"
    "  SHORT signals: ret_1h < -0.003, trend_1h < 0, trend_4h < 0, funding_rate < 0, rsi_14_1h > 35, aggr_delta_1h < 0\n"
    "  < 3 signals → FLAT\n\n"

    "STEP 3 — Confidence calibration:\n"
    "  3 signals: confidence 0.65\n"
    "  4 signals: confidence 0.72\n"
    "  5+ signals: confidence 0.80\n"
    "  < 3 signals or FLAT: confidence ≤ 0.50\n\n"

    "CONSTRAINTS:\n"
    "  - Minimum confidence to output LONG/SHORT: 0.65\n"
    "  - If confidence < 0.65 → direction = FLAT\n"
    "  - Never output LONG during EMERGENCY market_state\n"
)
```

**3c. Add helper functions** (add before the `main()` function):

```python
def parse_direction_bias(raw_text: str, universe: List[str]) -> Optional[DirectionBias]:
    """Parse LLM JSON response into DirectionBias, filling missing symbols as FLAT."""
    try:
        text = raw_text.strip()
        # Extract JSON from possible markdown code blocks
        if "```" in text:
            import re
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
        data = json.loads(text)
        biases_raw = data.get("biases", [])
        market_state = data.get("market_state", "NEUTRAL")
        if market_state not in ("TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"):
            market_state = "TRENDING"
        rationale = str(data.get("rationale", ""))[:300]

        # Build bias dict, fill missing as FLAT
        bias_map = {}
        for b in biases_raw:
            sym = b.get("symbol")
            if sym in universe:
                direction = b.get("direction", "FLAT")
                if direction not in ("LONG", "SHORT", "FLAT"):
                    direction = "FLAT"
                conf = float(b.get("confidence", 0.5))
                conf = max(0.0, min(1.0, conf))
                bias_map[sym] = SymbolBias(symbol=sym, direction=direction, confidence=conf)

        for sym in universe:
            if sym not in bias_map:
                bias_map[sym] = SymbolBias(symbol=sym, direction="FLAT", confidence=0.0)

        now_dt = datetime.now(timezone.utc)
        valid_until = (now_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z")

        return DirectionBias(
            asof_minute=now_dt.strftime("%Y-%m-%dT%H:%M:00Z"),
            valid_until_minute=valid_until,
            market_state=market_state,
            biases=list(bias_map.values()),
            rationale=rationale,
        )
    except Exception as e:
        logger.warning(f"parse_direction_bias failed: {e} | raw={raw_text[:200]}")
        return None


def is_direction_bias_valid(bias: DirectionBias, asof_minute: str) -> bool:
    """Return True if the direction bias has not yet expired."""
    try:
        now_dt = datetime.fromisoformat(asof_minute.replace("Z", "+00:00"))
        valid_until = datetime.fromisoformat(bias.valid_until_minute.replace("Z", "+00:00"))
        return now_dt < valid_until
    except Exception:
        return False


def get_cached_direction_bias(bus: "RedisStreams") -> Optional[DirectionBias]:
    """Read DirectionBias from Redis cache key."""
    raw = bus.r.get(DIRECTION_BIAS_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return DirectionBias(**data)
    except Exception as e:
        logger.warning(f"get_cached_direction_bias parse error: {e}")
        return None


def store_direction_bias(bus: "RedisStreams", bias: DirectionBias) -> None:
    """Store DirectionBias in Redis with 70-minute TTL."""
    bus.r.setex(DIRECTION_BIAS_KEY, 4200, json.dumps(bias.model_dump()))


def build_1h_user_payload(fs1h: FeatureSnapshot1h, universe: List[str]) -> dict:
    """Build the user message payload for the 1H LLM direction call."""
    features = {}
    for sym in universe:
        features[sym] = {
            "ret_1h": round(fs1h.ret_1h.get(sym, 0.0), 6),
            "ret_4h": round(fs1h.ret_4h.get(sym, 0.0), 6),
            "vol_1h": round(fs1h.vol_1h.get(sym, 0.0), 6),
            "vol_4h": round(fs1h.vol_4h.get(sym, 0.0), 6),
            "trend_1h": fs1h.trend_1h.get(sym, 0),
            "trend_4h": fs1h.trend_4h.get(sym, 0),
            "trend_agree": fs1h.trend_agree.get(sym, 0),
            "rsi_14_1h": round(fs1h.rsi_14_1h.get(sym, 50.0), 2),
            "aggr_delta_1h": round(fs1h.aggr_delta_1h.get(sym, 0.0), 4),
            "funding_rate": round(fs1h.funding_rate.get(sym, 0.0), 6),
            "basis_bps": round(fs1h.basis_bps.get(sym, 0.0), 2),
            "vol_regime": fs1h.vol_regime.get(sym, 0),
            "book_imbalance_l10": round(fs1h.book_imbalance_l10.get(sym, 0.0), 4),
        }
    return {
        "universe": universe,
        "asof_minute": fs1h.asof_minute,
        "decision_horizon": "4h",
        "features": features,
        "instruction": "Output direction bias JSON per the schema. FLAT when uncertain.",
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_ai_decision_layer1.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Add Layer 1 processing function**

Add `process_1h_message()` function before `main()`:

```python
def process_1h_message(
    fs1h: FeatureSnapshot1h,
    bus: "RedisStreams",
    llm_cfg: "LLMConfig",
) -> Optional[DirectionBias]:
    """
    Layer 1: receive a 1H feature snapshot, call LLM, store DirectionBias.
    Returns the DirectionBias if successful, None otherwise.
    """
    user_payload = build_1h_user_payload(fs1h, UNIVERSE)
    user_msg = json.dumps(user_payload, ensure_ascii=False)

    raw_response = None
    try:
        if AI_USE_LLM:
            raw_response = call_llm_json(llm_cfg, SYSTEM_PROMPT_1H, user_msg)
        elif AI_LLM_MOCK_RESPONSE:
            raw_response = AI_LLM_MOCK_RESPONSE
    except Exception as e:
        logger.warning(f"Layer 1 LLM call failed: {e}")
        AI_FALLBACK.labels(SERVICE, "layer1_llm_error").inc()
        return None

    if not raw_response:
        return None

    bias = parse_direction_bias(raw_response, UNIVERSE)
    if bias is None:
        AI_FALLBACK.labels(SERVICE, "layer1_parse_error").inc()
        return None

    store_direction_bias(bus, bias)
    logger.info(f"Layer 1 decision | market_state={bias.market_state} | "
                f"biases={[(b.symbol, b.direction, round(b.confidence, 2)) for b in bias.biases]}")
    return bias
```

- [ ] **Step 6: Commit**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_layer1.py
git commit -m "feat(ai_decision): add Layer 1 - 1H LLM direction decision with parse/cache helpers"
```

---

### Task 6: Layer 2 — 15m Rule Confirmation

**Files:**
- Modify: `services/ai_decision/app.py`
- Create: `tests/test_ai_decision_layer2.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ai_decision_layer2.py
import os
import sys
import importlib
from shared.schemas import DirectionBias, SymbolBias, FeatureSnapshot15m

def load_ai():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE")
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def make_bias(symbol_dirs: dict, market_state="TRENDING") -> DirectionBias:
    return DirectionBias(
        asof_minute="2026-03-16T15:00:00Z",
        valid_until_minute="2026-03-16T16:00:00Z",
        market_state=market_state,
        biases=[
            SymbolBias(symbol=sym, direction=d, confidence=0.72)
            for sym, d in symbol_dirs.items()
        ],
    )


def make_fs15(sym_overrides: dict = None) -> FeatureSnapshot15m:
    """Create a FeatureSnapshot15m with all signals favoring LONG for BTC."""
    base = {
        "asof_minute": "2026-03-16T15:00:00Z",
        "window_start_minute": "2026-03-16T14:45:00Z",
        "universe": ["BTC", "ETH", "SOL", "ADA", "DOGE"],
        "mid_px": {"BTC": 70000.0, "ETH": 2100.0, "SOL": 90.0, "ADA": 0.28, "DOGE": 0.095},
        "funding_rate": {"BTC": 0.0001, "ETH": 0.0001, "SOL": 0.0001, "ADA": 0.0001, "DOGE": 0.0001},
        "basis_bps": {"BTC": 5.0, "ETH": 4.0, "SOL": 3.0, "ADA": 2.0, "DOGE": 2.0},
        "oi_change_15m": {"BTC": 100.0, "ETH": 50.0, "SOL": 30.0, "ADA": 20.0, "DOGE": 10.0},
        "book_imbalance_l5": {"BTC": 0.15, "ETH": 0.12, "SOL": 0.11, "ADA": 0.10, "DOGE": 0.10},
        "aggr_delta_5m": {"BTC": 500.0, "ETH": 200.0, "SOL": 100.0, "ADA": 50.0, "DOGE": 30.0},
        "trend_strength_15m": {"BTC": 0.7, "ETH": 0.65, "SOL": 0.62, "ADA": 0.61, "DOGE": 0.60},
        "ret_15m": {"BTC": 0.005, "ETH": 0.004, "SOL": 0.003, "ADA": 0.003, "DOGE": 0.003},
        "vol_15m": {"BTC": 0.01, "ETH": 0.012, "SOL": 0.015, "ADA": 0.018, "DOGE": 0.020},
    }
    if sym_overrides:
        for k, v in sym_overrides.items():
            base[k] = v
    return FeatureSnapshot15m(**base)


def test_flat_direction_returns_zero_weight():
    mod = load_ai()
    bias = make_bias({"BTC": "FLAT"})
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] == 0.0


def test_long_with_3_signals_returns_positive_weight():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] > 0.0


def test_long_with_insufficient_signals_returns_zero():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG"})
    # Override all signals to be neutral/against
    fs = make_fs15({
        "funding_rate": {"BTC": -0.001},         # against LONG
        "basis_bps": {"BTC": -2.0},               # against LONG
        "oi_change_15m": {"BTC": -50.0},          # against LONG
        "book_imbalance_l5": {"BTC": -0.05},      # neutral
        "aggr_delta_5m": {"BTC": -100.0},         # against LONG
        "trend_strength_15m": {"BTC": 0.3},       # weak
    })
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC"])
    assert weights["BTC"] == 0.0


def test_sideways_market_all_flat():
    mod = load_ai()
    bias = make_bias({"BTC": "LONG", "ETH": "LONG"}, market_state="SIDEWAYS")
    fs = make_fs15()
    weights = mod.apply_direction_confirmation(fs, bias, ["BTC", "ETH"])
    assert weights["BTC"] == 0.0
    assert weights["ETH"] == 0.0
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_ai_decision_layer2.py -v
```
Expected: `AttributeError` — `apply_direction_confirmation` not found.

- [ ] **Step 3: Add apply_direction_confirmation() to ai_decision/app.py**

```python
def apply_direction_confirmation(
    fs: FeatureSnapshot15m,
    bias: DirectionBias,
    universe: List[str],
    min_confirm: int = 3,
) -> Dict[str, float]:
    """
    Layer 2: apply 3-signal confirmation rule to a 1H DirectionBias.
    Returns target weights (positive = LONG, negative = SHORT, 0 = HOLD).
    """
    # Entire market SIDEWAYS → all zero
    if bias.market_state == "SIDEWAYS":
        return {sym: 0.0 for sym in universe}

    bias_map = {b.symbol: b for b in bias.biases}
    result = {}

    for sym in universe:
        sym_bias = bias_map.get(sym)
        if not sym_bias or sym_bias.direction == "FLAT":
            result[sym] = 0.0
            continue

        direction = sym_bias.direction
        confirm = 0

        if direction == "LONG":
            if fs.funding_rate.get(sym, 0) > 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) > 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) > 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) > 0.10:     confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) > 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1
        else:  # SHORT
            if fs.funding_rate.get(sym, 0) < 0:             confirm += 1
            if fs.basis_bps.get(sym, 0) < 0:                confirm += 1
            if fs.oi_change_15m.get(sym, 0) < 0:            confirm += 1
            if fs.book_imbalance_l5.get(sym, 0) < -0.10:    confirm += 1
            if fs.aggr_delta_5m.get(sym, 0) < 0:            confirm += 1
            if fs.trend_strength_15m.get(sym, 0) > 0.6:     confirm += 1

        if confirm >= min_confirm:
            cap = CAP_BTC_ETH if sym in ("BTC", "ETH") else CAP_ALT
            weight = cap * sym_bias.confidence
            result[sym] = weight if direction == "LONG" else -weight
        else:
            result[sym] = 0.0

    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_ai_decision_layer2.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_layer2.py
git commit -m "feat(ai_decision): add Layer 2 apply_direction_confirmation() rule engine"
```

---

### Task 7: Dynamic EWMA + mode-aware MAX_GROSS + MIN_NOTIONAL

**Files:**
- Modify: `services/ai_decision/app.py`
- Create: `tests/test_ai_decision_capital.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ai_decision_capital.py
import os
import sys
import importlib

def load_ai():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH,SOL,ADA,DOGE")
    mod_name = "services.ai_decision.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_select_max_gross_trending_high():
    mod = load_ai()
    # ≥ 0.70 (MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD) → HIGH
    gross = mod.select_max_gross("TRENDING", confidence=0.72)
    assert gross == mod.MAX_GROSS_TRENDING_HIGH


def test_select_max_gross_trending_mid():
    mod = load_ai()
    # < 0.70 → MID
    gross = mod.select_max_gross("TRENDING", confidence=0.65)
    assert gross == mod.MAX_GROSS_TRENDING_MID


def test_select_max_gross_sideways():
    mod = load_ai()
    gross = mod.select_max_gross("SIDEWAYS", confidence=0.80)
    assert gross == 0.0


def test_select_max_gross_volatile():
    mod = load_ai()
    gross = mod.select_max_gross("VOLATILE", confidence=0.72)
    assert gross == mod.MAX_GROSS_VOLATILE


def test_select_smooth_alpha_high_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.72)
    assert alpha == mod.AI_SMOOTH_ALPHA_HIGH   # 0.60


def test_select_smooth_alpha_mid_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.60)
    assert alpha == mod.AI_SMOOTH_ALPHA_MID    # 0.40


def test_select_smooth_alpha_low_confidence():
    mod = load_ai()
    alpha = mod.select_smooth_alpha(confidence=0.40)
    assert alpha == mod.AI_SMOOTH_ALPHA        # 0.25


def test_apply_min_notional_filters_small_positions():
    mod = load_ai()
    weights = {"BTC": 0.001, "ETH": 0.10, "SOL": 0.0}
    equity = 1000.0
    result = mod.apply_min_notional(weights, equity, min_notional_usd=50.0)
    assert result["BTC"] == 0.0   # 0.001 * 1000 = $1 < $50 → filtered
    assert result["ETH"] == 0.10  # 0.10 * 1000 = $100 > $50 → kept
    assert result["SOL"] == 0.0   # already 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
python -m pytest tests/test_ai_decision_capital.py -v
```
Expected: `AttributeError` — functions not found.

- [ ] **Step 3: Add the three helper functions to ai_decision/app.py**

```python
def select_max_gross(market_state: str, confidence: float) -> float:
    """Return the appropriate MAX_GROSS based on market state and confidence.
    TRENDING HIGH threshold is 0.70 (spec table), independent of AI_CONFIDENCE_HIGH_THRESHOLD.
    """
    if market_state in ("SIDEWAYS", "EMERGENCY"):
        return MAX_GROSS_SIDEWAYS
    if market_state == "VOLATILE":
        return MAX_GROSS_VOLATILE
    # TRENDING: use spec-defined threshold of 0.70 for HIGH vs MID
    if confidence >= MAX_GROSS_HIGH_CONFIDENCE_THRESHOLD:   # 0.70
        return MAX_GROSS_TRENDING_HIGH
    return MAX_GROSS_TRENDING_MID


def select_smooth_alpha(confidence: float) -> float:
    """Return EWMA alpha based on confidence: higher confidence → faster response."""
    if confidence >= AI_CONFIDENCE_HIGH_THRESHOLD:
        return AI_SMOOTH_ALPHA_HIGH   # 0.60
    if confidence >= 0.55:
        return AI_SMOOTH_ALPHA_MID    # 0.40
    return AI_SMOOTH_ALPHA            # 0.25


def apply_min_notional(
    weights: Dict[str, float],
    equity_usd: float,
    min_notional_usd: float = 50.0,
) -> Dict[str, float]:
    """Zero out positions whose notional value is below the minimum threshold."""
    result = {}
    for sym, w in weights.items():
        notional = abs(w) * equity_usd
        result[sym] = w if notional >= min_notional_usd else 0.0
    return result
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
python -m pytest tests/test_ai_decision_capital.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Integrate into the Layer 2 decision pipeline**

In the existing `main()` processing section, find the block where `eff_max_gross` and `eff_smooth_alpha` are determined (around line 1146–1157). Replace the confidence-tier logic with calls to the new functions, and add `apply_min_notional` after turnover cap:

```python
# Replace the if/else confidence-mode block with:
market_state = getattr(cached_bias, 'market_state', 'TRENDING') if cached_bias else 'TRENDING'
eff_max_gross = select_max_gross(market_state, adjusted_confidence)
eff_smooth_alpha = select_smooth_alpha(adjusted_confidence)
eff_max_net = MAX_NET_HIGH if adjusted_confidence >= AI_CONFIDENCE_HIGH_THRESHOLD else MAX_NET
eff_turnover_cap = AI_TURNOVER_CAP

# ... (existing constraint application) ...

# After apply_turnover_cap, add:
equity_usd = get_equity_usd(bus)  # reads from state snapshot
capped_w = apply_min_notional(capped_w, equity_usd, MIN_NOTIONAL_USD)
```

Add `get_equity_usd()` helper:
```python
def get_equity_usd(bus: "RedisStreams") -> float:
    """Read portfolio equity from cached state snapshot. Returns 1000.0 as safe fallback."""
    try:
        raw = bus.r.get(STATE_KEY)
        if raw:
            data = json.loads(raw)
            return float(data.get("equity_usd", 1000.0))
    except Exception:
        pass
    return 1000.0
```

- [ ] **Step 6: Run all ai_decision tests**

```bash
python -m pytest tests/test_ai_decision_utils.py tests/test_ai_decision_layer1.py \
    tests/test_ai_decision_layer2.py tests/test_ai_decision_capital.py -v
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add services/ai_decision/app.py tests/test_ai_decision_capital.py
git commit -m "feat(ai_decision): add dynamic EWMA, mode-aware MAX_GROSS, MIN_NOTIONAL filter"
```

---

### Task 8: Dual-loop main(), daily trade cap, minutes_since_last_trade

**Files:**
- Modify: `services/ai_decision/app.py`

- [ ] **Step 1: Update to_major_cycle_id to align to 1H boundaries**

Find `to_major_cycle_id()` (line 580). Change the 15-min floor to 1H floor:

```python
def to_major_cycle_id(asof_minute: str) -> str:
    """Return the current 1-hour boundary cycle id, e.g. '20260316T1500Z'."""
    s = asof_minute.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    dt = dt.replace(minute=0, second=0, microsecond=0)   # floor to hour
    return dt.strftime("%Y%m%dT%H%MZ")
```

- [ ] **Step 2: Add daily trade cap and minutes_since_last_trade helpers**

```python
DAILY_TRADE_CAP_KEY_PREFIX = "daily_trade_count:"
LAST_TRADE_TS_KEY = "last_trade_timestamp"

def get_today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def check_daily_trade_cap(bus: "RedisStreams") -> bool:
    """Return True if daily trade cap has been reached (no more trades today)."""
    key = f"{DAILY_TRADE_CAP_KEY_PREFIX}{get_today_utc()}"
    count_raw = bus.r.get(key)
    count = int(count_raw) if count_raw else 0
    return count >= MAX_TRADES_PER_DAY

def increment_daily_trade_count(bus: "RedisStreams") -> int:
    """Increment today's trade counter, set TTL to expire at end of day."""
    key = f"{DAILY_TRADE_CAP_KEY_PREFIX}{get_today_utc()}"
    count = bus.r.incr(key)
    bus.r.expire(key, 86400)  # expire after 24h
    return count

def get_minutes_since_last_trade(bus: "RedisStreams") -> float:
    """Return minutes elapsed since last trade was recorded."""
    raw = bus.r.get(LAST_TRADE_TS_KEY)
    if not raw:
        return 9999.0
    try:
        last_ts = float(raw)
        return (time.time() - last_ts) / 60.0
    except Exception:
        return 9999.0

def record_trade_executed(bus: "RedisStreams") -> None:
    """Record the timestamp of a trade execution for frequency tracking."""
    bus.r.setex(LAST_TRADE_TS_KEY, 86400, str(time.time()))
```

- [ ] **Step 3: Restructure main() to run two polling loops**

Replace the current single `while True` loop in `main()` with a dual-poll loop:

```python
def main():
    logger.info(f"Starting ai_decision service | STREAM_IN_1H={STREAM_IN_1H} | STREAM_IN={STREAM_IN}")
    start_metrics("METRICS_PORT", 9103)
    bus = RedisStreams(REDIS_URL)

    # Ensure consumer groups exist for both streams
    for stream, group in [(STREAM_IN_1H, GROUP_1H), (STREAM_IN, GROUP)]:
        try:
            bus.r.xgroup_create(stream, group, id="$", mkstream=True)
        except Exception:
            pass  # group already exists

    llm_cfg = LLMConfig(
        endpoint=AI_LLM_ENDPOINT,
        api_key=AI_LLM_API_KEY,
        model=AI_LLM_MODEL,
        timeout_ms=AI_LLM_TIMEOUT_MS,
    )

    error_streak = 0
    alarm_on = False
    last_mid_px: Dict[str, float] = {}
    cached_bias: Optional[DirectionBias] = None

    def note_error(env, where, err):
        nonlocal error_streak, alarm_on
        error_streak += 1
        ERR.labels(SERVICE, where).inc()
        if error_streak >= ERROR_STREAK_THRESHOLD and not alarm_on:
            alarm_on = True
            set_alarm("error_streak", True)

    def note_ok(env):
        nonlocal error_streak, alarm_on
        if error_streak > 0:
            error_streak = 0
        if alarm_on:
            alarm_on = False
            set_alarm("error_streak", False)

    logger.info("Entering dual-loop processing")
    while True:
        # ── Layer 1: poll md.features.1h (non-blocking) ──────────────────
        msgs_1h = bus.xreadgroup_json(STREAM_IN_1H, GROUP_1H, CONSUMER_1H,
                                       count=5, block_ms=0)
        for stream, msg_id, payload in msgs_1h:
            try:
                payload = require_env(payload)
                fs1h = FeatureSnapshot1h(**payload["data"])
                new_bias = process_1h_message(fs1h, bus, llm_cfg)
                if new_bias:
                    cached_bias = new_bias
            except Exception as e:
                logger.warning(f"Layer 1 error: {e}")
                ERR.labels(SERVICE, "layer1").inc()
            finally:
                bus.xack(STREAM_IN_1H, GROUP_1H, msg_id)

        # ── Layer 2: poll md.features.15m (blocking 5s) ──────────────────
        msgs_15m = bus.xreadgroup_json(STREAM_IN, GROUP, CONSUMER,
                                        count=10, block_ms=5000)
        for stream, msg_id, payload in msgs_15m:
            MSG_IN.labels(SERVICE, stream).inc()
            t0 = time.time()
            try:
                payload = require_env(payload)
                incoming_env = Envelope(**payload["env"])
                fs = parse_feature(payload["data"])

                # Refresh cached bias from Redis if local copy is None or expired
                if cached_bias is None or not is_direction_bias_valid(cached_bias, fs.asof_minute):
                    cached_bias = get_cached_direction_bias(bus)

                # Check daily trade cap
                if check_daily_trade_cap(bus):
                    logger.info("Daily trade cap reached, HOLD")
                    bus.xack(STREAM_IN, GROUP, msg_id)
                    continue

                # Inject minutes_since_last_trade into payload for use
                minutes_since = get_minutes_since_last_trade(bus)

                # Build target weights via Layer 2 confirmation
                if cached_bias and is_direction_bias_valid(cached_bias, fs.asof_minute):
                    target_w = apply_direction_confirmation(fs, cached_bias, UNIVERSE)
                    market_state = cached_bias.market_state
                    # Use highest bias confidence across active symbols
                    active_confs = [b.confidence for b in cached_bias.biases
                                    if b.direction != "FLAT"]
                    confidence = max(active_confs) if active_confs else 0.0
                else:
                    # No valid 1H bias — fall back to baseline, minimal exposure
                    target_w = build_baseline_weights(fs, UNIVERSE, MAX_GROSS)
                    market_state = "TRENDING"
                    confidence = 0.3

                # Minutes-since-last-trade gate (matches prompt Step 2.5)
                if minutes_since < 30 and confidence < 0.70:
                    logger.info(f"Frequency gate: minutes_since={minutes_since:.1f} conf={confidence:.2f} → HOLD")
                    target_w = {sym: 0.0 for sym in UNIVERSE}

                # Capital utilization
                adjusted_confidence = adjust_confidence_by_risk(confidence, fs)
                eff_max_gross = select_max_gross(market_state, adjusted_confidence)
                eff_smooth_alpha = select_smooth_alpha(adjusted_confidence)
                eff_max_net = MAX_NET_HIGH if adjusted_confidence >= AI_CONFIDENCE_HIGH_THRESHOLD else MAX_NET

                # Emergency + reversal + pnl penalties (existing functions)
                emergency = check_emergency_shutdown(fs, last_mid_px)
                if emergency and FORCE_CASH_WHEN_EXTREME:
                    target_w = apply_emergency_shutdown(target_w)
                last_mid_px = fs.mid_px.copy()

                prev_w, _ = get_prev_target(bus, UNIVERSE)
                current_w = get_current_weights(bus, UNIVERSE)
                asof_dt = datetime.fromisoformat(fs.asof_minute.replace("Z", "+00:00"))
                target_w = apply_reversal_penalty(target_w, prev_w, bus, asof_dt)
                target_w = apply_pnl_penalty(target_w, bus)

                # Apply constraints
                capped_sym = apply_symbol_caps(target_w, UNIVERSE, CAP_BTC_ETH, CAP_ALT)
                cash_adj, _, _ = apply_cash_weight(capped_sym, None, eff_max_gross)
                gross_capped = scale_gross(cash_adj, eff_max_gross)
                gated = apply_confidence_gating(gross_capped, adjusted_confidence, AI_MIN_CONFIDENCE)
                smoothed = ewma_smooth(gated, prev_w, eff_smooth_alpha, UNIVERSE)
                capped, to_before, to_after = apply_turnover_cap(smoothed, current_w, AI_TURNOVER_CAP, UNIVERSE)
                capped = apply_net_direction_cap(capped, fs)
                candidate_w, _, _ = apply_net_cap(capped, eff_max_net)
                equity_usd = get_equity_usd(bus)
                candidate_w = apply_min_notional(candidate_w, equity_usd, MIN_NOTIONAL_USD)

                rebalance, signal_delta, action_reason = should_rebalance(bus, prev_w, candidate_w, fs.asof_minute)
                final_w = candidate_w if rebalance else prev_w
                decision_action = "REBALANCE" if rebalance else "HOLD"

                # Track trade execution
                if decision_action == "REBALANCE":
                    increment_daily_trade_count(bus)
                    record_trade_executed(bus)

                # Publish TargetPortfolio (existing code, kept as-is)
                env = Envelope(source=SERVICE, cycle_id=current_cycle_id())
                tp = TargetPortfolio(
                    asof_minute=fs.asof_minute,
                    universe=UNIVERSE,
                    targets=[TargetWeight(symbol=s, weight=w) for s, w in final_w.items()],
                    cash_weight=1.0 - sum(abs(v) for v in final_w.values()),
                    confidence=adjusted_confidence,
                    rationale=f"layer2_confirmed | market={market_state}",
                    model={"name": "dual_layer_v1", "version": "v1"},
                    decision_action=decision_action,
                )
                bus.xadd_json(STREAM_OUT, require_env({"env": env.model_dump(), "data": tp.model_dump()}))
                bus.r.setex(LATEST_ALPHA_KEY, 7200, json.dumps(tp.model_dump()))
                MSG_OUT.labels(SERVICE, STREAM_OUT).inc()
                note_ok(env)

            except Exception as e:
                logger.error(f"Layer 2 processing error: {e}", exc_info=True)
                note_error(incoming_env if 'incoming_env' in dir() else
                           Envelope(source=SERVICE, cycle_id=current_cycle_id()), "layer2", e)
            finally:
                bus.xack(STREAM_IN, GROUP, msg_id)
                LAT.labels(SERVICE, "layer2").observe(time.time() - t0)
```

- [ ] **Step 4: Run the full ai_decision test suite**

```bash
python -m pytest tests/test_ai_decision_utils.py tests/test_ai_decision_layer1.py \
    tests/test_ai_decision_layer2.py tests/test_ai_decision_capital.py -v
```
Expected: all PASS.

- [ ] **Step 5: Smoke-test the import**

```bash
python -c "
import os
os.environ['REDIS_URL'] = 'redis://localhost:6379/0'
import services.ai_decision.app as app
print('to_major_cycle_id(2026-03-16T15:30:00Z):', app.to_major_cycle_id('2026-03-16T15:30:00Z'))
print('Expected: 20260316T1500Z')
print('select_max_gross(TRENDING, 0.72):', app.select_max_gross('TRENDING', 0.72))
"
```
Expected: `20260316T1500Z` and `0.65`.

- [ ] **Step 6: Commit**

```bash
git add services/ai_decision/app.py
git commit -m "feat(ai_decision): dual-loop main(), daily trade cap, 1H-aligned cycle id"
```

---

## Chunk 4: Backtester

### Task 9: backtester/replay.py — simulate Layer 1 + 2 on historical data

**Files:**
- Create: `backtester/__init__.py`
- Create: `backtester/replay.py`

- [ ] **Step 1: Create backtester package**

```bash
mkdir -p /home/gordonyang/workspace/myproject/hl-ai-trader/backtester
touch /home/gordonyang/workspace/myproject/hl-ai-trader/backtester/__init__.py
```

- [ ] **Step 2: Create backtester/replay.py**

```python
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
        valid_until_minute=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
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
            if fs.trend_strength_15m.get(sym, 0) > 0.6:  confirm += 1

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
                # Real LLM call — import and use process_1h_message
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
```

- [ ] **Step 3: Verify import works**

```bash
cd /home/gordonyang/workspace/myproject/hl-ai-trader
REDIS_URL=redis://localhost:6379/0 python -c "from backtester.replay import run_replay; print('OK')"
```
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backtester/__init__.py backtester/replay.py
git commit -m "feat(backtester): add replay.py with Layer1+2 simulation and price outcome lookup"
```

---

### Task 10: backtester/evaluator.py — score predictions

**Files:**
- Create: `backtester/evaluator.py`

- [ ] **Step 1: Create evaluator.py**

```python
# backtester/evaluator.py
"""
Evaluate a list of Prediction objects and print a scoring report.
"""
from __future__ import annotations
from collections import defaultdict
from typing import List

from backtester.replay import Prediction


def evaluate(predictions: List[Prediction], fee_per_trade_usd: float = 0.006) -> dict:
    """
    Compute win rate, avg gain/loss, PnL estimate per market_state.
    Returns structured metrics dict.
    """
    if not predictions:
        return {"error": "no predictions"}

    groups = defaultdict(list)
    groups["ALL"] = predictions
    for p in predictions:
        groups[p.market_state].append(p)
        groups[p.symbol].append(p)

    results = {}
    for label, preds in sorted(groups.items()):
        active = [p for p in preds if p.direction != "HOLD"]
        if not active:
            continue
        wins = [p for p in active if p.won]
        losses = [p for p in active if not p.won]
        win_rate = len(wins) / len(active)
        avg_win = sum(p.ret for p in wins) / len(wins) if wins else 0.0
        avg_loss = sum(p.ret for p in losses) / len(losses) if losses else 0.0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
        breakeven_wr = 1 / (1 + rr) if rr != float("inf") else 0.5
        # Estimate PnL assuming $100 position per trade, minus fees
        est_pnl_per_trade = win_rate * avg_win * 100 + (1 - win_rate) * avg_loss * 100
        net_pnl_per_trade = est_pnl_per_trade - fee_per_trade_usd
        results[label] = {
            "n": len(active),
            "win_rate": win_rate,
            "avg_win_pct": avg_win * 100,
            "avg_loss_pct": avg_loss * 100,
            "reward_risk": rr,
            "breakeven_win_rate": breakeven_wr,
            "est_net_pnl_per_trade_usd": net_pnl_per_trade,
            "wins": len(wins),
            "losses": len(losses),
        }
    return results


def print_report(results: dict) -> None:
    """Pretty-print evaluation results."""
    print("\n" + "=" * 80)
    print("BACKTEST EVALUATION REPORT")
    print("=" * 80)

    all_res = results.get("ALL")
    if all_res:
        wr = all_res["win_rate"] * 100
        be = all_res["breakeven_win_rate"] * 100
        net = all_res["est_net_pnl_per_trade_usd"]
        verdict = "✓ PROFITABLE" if wr > be else "✗ LOSING"
        print(f"\nOVERALL: {verdict}")
        print(f"  Win rate: {wr:.1f}%  (need >{be:.1f}% to break even)")
        print(f"  Reward/Risk: {all_res['reward_risk']:.2f}")
        print(f"  Est net PnL per $100 trade: ${net:.3f}")
        print(f"  Total predictions: {all_res['n']}")

    print("\nBY MARKET STATE:")
    for label in ("TRENDING", "SIDEWAYS", "VOLATILE", "EMERGENCY"):
        if label in results:
            r = results[label]
            print(f"  {label:<12}: n={r['n']:>4}  WR={r['win_rate']*100:>5.1f}%  "
                  f"RR={r['reward_risk']:>4.2f}  net=${r['est_net_pnl_per_trade_usd']:>+.3f}/trade")

    print("\nBY SYMBOL:")
    for sym in ("BTC", "ETH", "SOL", "ADA", "DOGE"):
        if sym in results:
            r = results[sym]
            print(f"  {sym:<6}: n={r['n']:>4}  WR={r['win_rate']*100:>5.1f}%  "
                  f"net=${r['est_net_pnl_per_trade_usd']:>+.3f}/trade")

    print("=" * 80)
    if all_res:
        wr = all_res["win_rate"] * 100
        be = all_res["breakeven_win_rate"] * 100
        if wr >= be:
            print("RECOMMENDATION: Strategy appears profitable. Consider deploying to live.")
        else:
            gap = be - wr
            print(f"RECOMMENDATION: Win rate gap is {gap:.1f}%. Tune prompt before deploying.")
    print()
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from backtester.evaluator import evaluate, print_report; print('OK')"
```
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add backtester/evaluator.py
git commit -m "feat(backtester): add evaluator.py with win-rate/PnL scoring report"
```

---

### Task 11: backtester/run_backtest.sh — CLI entry point

**Files:**
- Create: `backtester/run_backtest.sh`

- [ ] **Step 1: Create run_backtest.sh**

```bash
#!/usr/bin/env bash
# backtester/run_backtest.sh
# Usage:
#   ./backtester/run_backtest.sh                        # 7-day dry-run
#   ./backtester/run_backtest.sh --days 14              # 14-day dry-run
#   ./backtester/run_backtest.sh --use-llm              # use real LLM (costs tokens)
#   ./backtester/run_backtest.sh --outcome-horizon 30   # 30-min outcome window
#   ./backtester/run_backtest.sh --compare 3 14         # compare last 3 days vs last 14 days

set -euo pipefail
cd "$(dirname "$0")/.."

DAYS=7
USE_LLM=false
OUTCOME_HORIZON=60
COMPARE_DAYS=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --days)            DAYS="$2";           shift 2 ;;
        --use-llm)         USE_LLM=true;        shift   ;;
        --outcome-horizon) OUTCOME_HORIZON="$2"; shift 2 ;;
        --compare)         COMPARE_DAYS="$2 $3"; shift 3 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# Load env if available
if [[ -f infra/.env ]]; then
    set -a
    source infra/.env
    set +a
fi

export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

echo "Running backtest: days=${DAYS} use_llm=${USE_LLM} outcome_horizon=${OUTCOME_HORIZON}min"

python - <<EOF
import sys
sys.path.insert(0, '.')
from backtester.replay import run_replay
from backtester.evaluator import evaluate, print_report

compare_days = "${COMPARE_DAYS}".strip()
if compare_days:
    parts = compare_days.split()
    days_a, days_b = int(parts[0]), int(parts[1])
    print(f"=== Comparing last {days_a} days vs last {days_b} days ===")
    for label, d in [(f"Last {days_a}d", days_a), (f"Last {days_b}d", days_b)]:
        preds = run_replay(days=d, use_llm=${USE_LLM,,}, outcome_horizon_min=${OUTCOME_HORIZON})
        results = evaluate(preds)
        print(f"\n--- {label} ---")
        print_report(results)
else:
    predictions = run_replay(
        days=${DAYS},
        use_llm=${USE_LLM,,},
        outcome_horizon_min=${OUTCOME_HORIZON},
    )
    results = evaluate(predictions)
    print_report(results)
EOF
```

- [ ] **Step 2: Make executable and test**

```bash
chmod +x backtester/run_backtest.sh
# Syntax check (doesn't require live Redis):
bash -n backtester/run_backtest.sh && echo "Syntax OK"
```

- [ ] **Step 3: Run a dry-run against local Redis (requires running Redis with some stream data)**

```bash
./backtester/run_backtest.sh --days 7
```
Expected: prints the evaluation report. If Redis has no `md.features.15m` data, it prints `0 messages` — that's acceptable; it will work once live data accumulates.

- [ ] **Step 4: Commit**

```bash
git add backtester/run_backtest.sh
git commit -m "feat(backtester): add run_backtest.sh CLI entry point"
```

---

## Post-Implementation Checklist

- [ ] Run full test suite: `python -m pytest tests/ -v --tb=short`
- [ ] Deploy market_data and let it run for 1+ hours to accumulate `md.features.1h` data
- [ ] Run `./backtester/run_backtest.sh --days 3` to verify replay works on live data
- [ ] Set `AI_USE_LLM=true` in infra/.env and run `./backtester/run_backtest.sh --use-llm --days 1` to evaluate LLM quality
- [ ] If backtest win rate ≥ 47%, deploy ai_decision with new dual-layer architecture
- [ ] Monitor daily trade count in Redis: `redis-cli get "daily_trade_count:$(date +%Y-%m-%d)"`
- [ ] Monitor direction bias: `redis-cli get latest.direction_bias | python -m json.tool`
