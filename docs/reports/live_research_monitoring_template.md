# Live-Research Monitoring Template

Use this template for every daily live-research review, candidate switch, and acceptance checkpoint.

## 1. Session metadata
- Date:
- Operator:
- Environment: DRY_RUN / shadow / live-research
- Active candidate: primary 20% / secondary 25% / baseline / SOL watchlist
- Universe:
- Config / version id:
- Redis URL / deployment target:

## 2. Expected candidate profile
- Candidate structure:
- Core leg market:
- Overlay leg market:
- Target overlay weight:
- Expected role: default / comparison / fallback / expansion watchlist

## 3. Platform health snapshot
- `state.snapshot` fresh: yes / no
- `reconcile_ok`: yes / no
- `risk.approved` mode: NORMAL / REDUCE_ONLY / HALT
- Stream lag issues: none / list
- Recent exec terminal reports present: yes / no
- DLQ events: count
- Reject rate:
- P95 latency ms:

## 4. Candidate-specific monitoring
### 4.1 Performance
- Rolling OOS-like PnL proxy:
- Daily return:
- Rolling drawdown:
- Best recent window behavior:
- Worst recent window behavior:

### 4.2 Overlay behavior
- Actual overlay contribution:
- Actual core contribution:
- Target/approved gap:
- Overlay drift concern: yes / no

### 4.3 Funding / carry
- ETH funding regime:
- BTC funding regime:
- Estimated cumulative funding drag:
- Funding warning triggered: yes / no

### 4.4 Market quality
- Basis avg:
- Liquidity score avg:
- Slippage avg:
- OI change anomalies:

## 5. Decision
- Keep current candidate unchanged
- Downgrade to baseline pure SMA
- Move to REDUCE_ONLY
- HALT
- Promote secondary candidate for comparison window only
- Keep SOL watchlist only

## 6. Reasoning
- Why this decision:
- Key risks observed:
- Required follow-up:

## 7. Commands run
```bash
./.venv/bin/python scripts/live_smoke_check.py --max-lag-sec 180 --state-max-age-sec 60
./.venv/bin/python scripts/live_alert_check.py --window-minutes 10 --max-lag-sec 180 --max-reject-rate 0.70 --max-p95-latency-ms 5000 --max-dlq-events 0 --max-basis-bps-abs-avg 60 --min-liquidity-score-avg 0.10 --max-slippage-bps-15m-avg 8
./.venv/bin/python scripts/live_acceptance_report.py --window-minutes 30 --max-lag-sec 180 --state-max-age-sec 60 --min-ack 1 --max-rejected 0 --max-error-events 0 --require-terminal
```

## 8. Escalation actions if needed
### REDUCE_ONLY
```bash
./.venv/bin/python scripts/send_ctl_command.py --cmd REDUCE_ONLY --reason "candidate package defensive mode"
```

### HALT
```bash
./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "candidate package halt"
```

## 9. Sign-off
- Reviewer:
- Time:
- Next review due:
