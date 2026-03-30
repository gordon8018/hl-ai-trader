# Profit-Max With -5% Drawdown Guard Design

## 1. Goal
- Primary objective (30 days): maximize total return.
- Hard risk constraint: maximum drawdown must stay above `-5%`.
- Current observed bottleneck: low effective trading frequency (many HOLD/heartbeat cycles), resulting in low return velocity.

## 2. Scope
- In scope:
  - `services/ai_decision` signal gating and action policy
  - `services/risk_engine` dynamic risk budget and drawdown guard
  - `services/execution` edge-aware execution filter and adaptive slicing
  - `research/autoresearch_runner.py` candidate selection and gated promotion
  - reporting fields for KPI tracking
- Out of scope:
  - exchange connector rewrite
  - multi-exchange expansion

## 3. Design Options
1. Aggressive leverage/threshold relaxation only  
Trade-off: fastest to increase trades, but high probability of violating `-5%` drawdown.

2. Recommended: quality-first trade expansion with hard risk rails  
Trade-off: moderate implementation complexity, best balance of return increase and risk control.

3. Conservative execution-only optimization  
Trade-off: safer rollout, but weak upside because upstream HOLD frequency remains high.

Decision: **Option 2**.

## 4. System Design

### 4.1 AI Decision (increase valid entries, avoid low-edge entries)
- Replace single decision gate with tiered policy:
  - `strong_signal`: allow immediate rebalance.
  - `medium_signal`: require trend alignment + liquidity threshold + execution quality pass.
  - `weak_signal`: force HOLD.
- Add `expected_edge_bps` estimation per decision.
- Emit `decision_tier`, `expected_edge_bps`, and `entry_gate_reason` in evidence payload.

### 4.2 Risk Engine (profit-first but bounded)
- Add rolling peak-equity drawdown computation.
- Add staged risk mode by drawdown:
  - drawdown <= 4.0%: normal
  - 4.0% < drawdown < 5.0%: progressive de-risk (reduce max gross/net and turnover cap)
  - drawdown >= 5.0%: hard halt (no new risk)
- Add daily loss budget and daily open-trade budget with degrade-to-reduce-only path.

### 4.3 Execution (only execute positive-edge trades)
- Add minimum edge filter:
  - skip if `expected_edge_bps <= estimated_cost_bps` (fees + expected slippage).
- Adaptive slicing profile:
  - high volatility: more slices, smaller size, shorter timeout.
  - normal/low volatility: fewer slices to reduce fee drag.
- Keep heartbeat reports for observability even when skip/hold.

### 4.4 Autoresearch (safe auto-improvement loop)
- Evaluate candidates on rolling windows `3d/7d/14d`.
- Promotion gate must satisfy all:
  - higher return
  - max drawdown > `-5%`
  - no regression in reject rate / latency / slippage
- Promotion sequence remains `shadow -> canary -> stable`, with automatic rollback on guardrail breach.

## 5. Data Flow & Interfaces
- `ai_decision -> risk_engine`:
  - add fields: `decision_tier`, `expected_edge_bps`, `entry_gate_reason`.
- `risk_engine -> execution`:
  - propagate effective risk budget values used in current cycle.
- `execution -> reporting`:
  - distinguish `heartbeat`, `edge_skip`, `risk_skip`, `real_fill` for KPI decomposition.

## 6. KPIs and Success Criteria
- Effective trade count (non-heartbeat real execution events) >= 2x current baseline.
- 30-day cumulative return > current baseline.
- Max drawdown strictly > `-5%`.
- Reject/cancel-on-timeout rate reduced by >= 30%.
- 7-day rolling net return remains positive after fees.

## 7. Rollout Plan
1. Phase A (2 days): enable tiered signal + metrics only, no leverage increase.
2. Phase B (3-5 days): enable adaptive execution + moderate risk budget expansion.
3. Phase C (continuous): enable autoresearch candidate auto-gate and guarded promotion.

Each phase has explicit rollback trigger:
- drawdown breach trajectory
- reject-rate spike
- latency/slippage regression

## 8. Error Handling & Safety
- Any missing critical input (`expected_edge_bps`, drawdown state) fails closed to HOLD/skip.
- Drawdown guard has priority over return optimization.
- All mode changes must emit audit events.

## 9. Testing Strategy
- Unit tests:
  - tiered decision gate classification
  - drawdown band mode transition
  - edge filter behavior and skip reasons
- Integration tests:
  - simulated high-volatility and low-liquidity scenarios
  - rollback path for canary regression
- Acceptance:
  - live smoke + alert checks remain PASS
  - KPI report includes all new dimensions

## 10. Risks
- Overfitting short lookback windows in autoresearch.
- False positives in edge estimation causing overtrading.
- Interaction effects between turnover caps and adaptive slicing.

Mitigations:
- keep canary window and rollback automation
- monitor per-symbol edge realization vs expected edge
- phase-gated rollout with hard stop conditions
