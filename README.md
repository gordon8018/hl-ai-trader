# hl-ai-trader (Redis Streams)

## What it is
Minute-level portfolio trading skeleton for Hyperliquid:
market_data -> ai_decision -> risk_engine -> execution
with portfolio_state reconciliation. Redis Streams used for event bus.

## Quick start
1) Copy env:
   cp infra/env.example infra/.env

2) Fill:
   - HL_ACCOUNT_ADDRESS (main account)
   - HL_PRIVATE_KEY (API wallet or signer key; see Hyperliquid docs)
   - Keep DRY_RUN=true at first

3) Run:
   cd infra
   docker compose up --build

## Going live (small capital)
- Set DRY_RUN=false
- Keep leverage = 1x
- Start with very conservative limits

## Streams
- md.features.1m
- state.snapshot
- alpha.target
- risk.approved
- exec.plan
- exec.orders
- exec.reports
- audit.logs
