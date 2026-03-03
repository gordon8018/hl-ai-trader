# Live Acceptance Report

- Verdict: **FAIL**
- Generated At: `2026-03-03T00:45:24Z`
- Window: `2026-03-03T00:15:24Z` -> `2026-03-03T00:45:24Z`

## Gates
- FAIL: `pipeline_lag_issues={'exec.reports': 'missing'}`

## Pipeline
- Stream Lags: `{'exec.reports': 'missing'}`
- State Issue: `none`

## Execution
- ACK: `0`
- PARTIAL: `0`
- FILLED: `0`
- CANCELED: `0`
- REJECTED: `0`
- TERMINAL: `0`

## Audit
- error events: `0`
- retry events: `0`
- dlq events: `0`

## Market Features (15m)
- funding_rate_avg: `-0.000006`
- basis_bps_abs_avg: `5.0355`
- oi_change_15m_abs_max: `0.0162`
- spread_bps_avg: `0.9937`
- liquidity_score_avg: `0.4177`
- reject_rate_15m_avg: `0.0000`
- p95_latency_ms_15m_avg: `0.00`
- slippage_bps_15m_avg: `0.0000`

## Cycle Coverage
- `alpha.target`: `30` cycles
- `exec.reports`: `0` cycles
- `md.features.15m`: `30` cycles
- `md.features.1m`: `30` cycles
- `risk.approved`: `30` cycles
- `state.snapshot`: `31` cycles

## Raw Status Counts
```json
{}
```
