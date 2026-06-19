# Live-Research Candidate Pack Runbook (2026-04-17)

## Goal
Operate the validated candidate package in dry-run / paper / live-research mode using the existing hl-ai-trader pipeline, while keeping BTC/ETH as the mainline and SOL as a second-wave expansion market.

Primary package:
- main: SMA core + BTC MACD 20%
- secondary: SMA core + BTC MACD 25%
- baseline: pure SMA core
- expansion watchlist: SOL 20% / 25%

Manifest:
- `config/candidate_profiles/live_research_candidate_pack_2026-04-17.json`

## 1. Promotion policy
1. Default candidate for next-stage live-research: BTC/ETH main 20%
2. Secondary aggressive candidate: BTC/ETH 25%
3. Baseline fallback: pure SMA
4. SOL remains watchlist / expansion only and must not replace BTC/ETH mainline

## 2. Operating modes
### A. DRY_RUN
Use first for every config change.
Required:
- `DRY_RUN=true`
- keep small or zero live exposure assumptions
- verify stream freshness, target generation, risk approvals, and terminal execution reports

### B. Shadow / paper-live research
Use after DRY_RUN passes.
Recommended:
- keep real market data + state snapshot running
- candidate output recorded and compared against baseline
- no promotion to default execution path without acceptance report

### C. Small-capital live-research
Only after DRY_RUN and shadow pass.
Recommended:
- start with primary candidate only
- keep secondary candidate as comparison track, not default
- keep SOL disabled in main execution universe unless explicitly testing expansion

## 3. Candidate activation policy
### Primary candidate
Use when:
- stream lag healthy
- reconcile healthy
- funding regime not abnormally adverse
- rolling drawdown within expected band

### Secondary candidate
Use only when:
- you intentionally want more upside exposure
- current operations are stable
- you accept heavier tail drawdown than the primary

### Baseline fallback
Use when:
- overlay contribution becomes unstable
- funding regime deteriorates significantly
- you need to simplify the system quickly without shutting down research

## 4. Recommended universe policy
### Mainline
- `UNIVERSE=BTC,ETH`
- Use BTC signal leg + ETH core leg behavior as validated package

### Expansion track
- `UNIVERSE=BTC,ETH,SOL` only for isolated SOL expansion tests
- SOL should remain watchlist-only unless BTC/ETH mainline is already stable

## 5. Monitoring commands
### A. Smoke check
```bash
./.venv/bin/python scripts/live_smoke_check.py --max-lag-sec 180 --state-max-age-sec 60
```
Pass conditions:
- `RESULT: PASS`
- no lag issues
- no state issue

### B. Acceptance report
```bash
./.venv/bin/python scripts/live_acceptance_report.py \
  --window-minutes 30 \
  --max-lag-sec 180 \
  --state-max-age-sec 60 \
  --min-ack 1 \
  --max-rejected 0 \
  --max-error-events 0 \
  --require-terminal
```

### C. Alert check
```bash
./.venv/bin/python scripts/live_alert_check.py \
  --window-minutes 10 \
  --max-lag-sec 180 \
  --max-reject-rate 0.70 \
  --max-p95-latency-ms 5000 \
  --max-dlq-events 0 \
  --max-basis-bps-abs-avg 60 \
  --min-liquidity-score-avg 0.10 \
  --max-slippage-bps-15m-avg 8
```

## 6. Strategy-specific monitoring items
These are the candidate-specific items to record in addition to generic platform health.

### For primary 20% candidate
- funding regime and cumulative funding drag
- overlay contribution vs core contribution
- rolling drawdown
- mode flips in `risk.approved`
- target drift between intended overlay size and actual approved target

### For secondary 25% candidate
Track everything above, plus:
- bad-window tails
- higher sensitivity to drawdown expansion
- whether extra CAGR is actually compensating for larger tail risk

### For baseline SMA
Track as control only:
- relative underperformance vs primary
- whether baseline is materially more stable during adverse funding regimes

### For SOL expansion
Track separately from mainline:
- positive-window ratio deterioration
- worst-window tail
- whether cross-window stability improves enough to justify promotion

## 7. Daily operator checklist
1. Check `state.snapshot` freshness and reconcile health
2. Run smoke check
3. Run alert check
4. Inspect latest acceptance / research report if candidate changed
5. Review funding / basis / liquidity conditions
6. Confirm candidate mode:
   - primary default
   - secondary comparison only
   - baseline fallback ready
7. If risk quality degrades, move to `REDUCE_ONLY` or `HALT`

## 8. Escalation policy
### Move to REDUCE_ONLY when
- stream quality degrades but not fully broken
- funding drag becomes materially adverse
- drawdown is rising but not beyond hard halt
- execution quality weakens without total failure

Recommended command:
```bash
./.venv/bin/python scripts/send_ctl_command.py --cmd REDUCE_ONLY --reason "candidate package defensive mode"
```

### Move to HALT when
- reconcile unhealthy
- state stale
- persistent execution failures
- drawdown threshold breached
- candidate behavior no longer matches expected validated profile

Recommended command:
```bash
./.venv/bin/python scripts/send_ctl_command.py --cmd HALT --reason "candidate package halt"
```

## 9. Rollback order
1. Primary 20% -> baseline pure SMA
2. If still unstable -> REDUCE_ONLY
3. If infrastructure or execution unhealthy -> HALT
4. SOL should never be used as rollback destination for BTC/ETH mainline

## 10. Suggested config discipline
- Do not overwrite production-active logic blindly
- Keep primary / secondary / baseline profiles explicit in config or deployment metadata
- Record which candidate is active in every acceptance report and research log
- Do not promote SOL into mainline without a separate approval step

## 11. Deliverables to maintain
- candidate manifest JSON
- live monitoring template
- acceptance reports under `docs/reports/`
- llm-wiki research conclusion page

## 12. Current recommended next action
Use the primary BTC/ETH 20% candidate as the default live-research package, keep 25% as a comparison track, and keep SOL marked as secondary expansion only.
