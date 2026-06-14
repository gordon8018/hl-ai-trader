#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/gordon8018/workspace/.worktrees/hl-ai-trader/v17-shadow-integration"
PY="/Users/gordon8018/workspace/hl-ai-trader/.venv/bin/python"
RUN_DIR="$ROOT/.run/v17-shadow"
REDIS_DIR="$RUN_DIR/redis"
LOG_DIR="$RUN_DIR/logs"
PORT="${V17_REDIS_PORT:-6381}"
REDIS_URL="redis://127.0.0.1:${PORT}/0"

mkdir -p "$REDIS_DIR" "$LOG_DIR" "$ROOT/data/v17_shadow"

cd "$ROOT"

# Safety gate: V17 must remain paper-shadow/dry-run only.
"$PY" - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path('config/v17_shadow.json').read_text())
assert cfg.get('mode') == 'paper_shadow', cfg
assert cfg.get('dry_run') is True, cfg
assert cfg.get('real_money_execution_allowed') is False, cfg
assert cfg.get('streams', {}).get('target') == 'alpha.target.v17_shadow', cfg
assert cfg.get('streams', {}).get('target') != 'alpha.target', cfg
print('safety_gate=passed')
PY

# Start isolated Redis if not already available.
if ! redis-cli -h 127.0.0.1 -p "$PORT" ping >/dev/null 2>&1; then
  redis-server --port "$PORT" --dir "$REDIS_DIR" --dbfilename dump.rdb --appendonly no --save "" >"$LOG_DIR/redis.log" 2>&1 &
  echo $! > "$RUN_DIR/redis.pid"
  for _ in {1..30}; do
    if redis-cli -h 127.0.0.1 -p "$PORT" ping >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done
fi
redis-cli -h 127.0.0.1 -p "$PORT" ping >/dev/null

export REDIS_URL
export DRY_RUN=true
export V17_DRY_RUN_INTEGRATION=true
export UNIVERSE="BTC,ETH,SOL"
export PYTHONUNBUFFERED=1
export RISK_ENGINE_METRICS_PORT=19104
export EXECUTION_METRICS_PORT=19105
export METRICS_PORT=0

# Seed safe control/state so risk engine does not require production state services.
"$PY" - <<'PY'
import json, os
from datetime import datetime, timezone
import redis
r = redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
now = datetime.now(timezone.utc).isoformat()
r.set('ctl.mode', json.dumps({'mode': 'NORMAL', 'source': 'v17_shadow_wrapper', 'ts': now}))
r.set('latest.state.snapshot', json.dumps({
    'schema_version': '1.0',
    'ts': now,
    'positions': [],
    'open_orders': [],
    'equity': 100000.0,
    'health': {'reconcile_ok': True, 'last_reconcile_ts': now, 'mode': 'paper_shadow'},
}))
print('redis_seed=ok')
PY

start_service() {
  local name="$1"
  shift
  local pid_file="$RUN_DIR/${name}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "${name}=already_running pid=$(cat "$pid_file")"
    return
  fi
  "$@" >"$LOG_DIR/${name}.log" 2>&1 &
  echo $! > "$pid_file"
  echo "${name}=started pid=$(cat "$pid_file")"
}

start_service risk_engine "$PY" -m services.risk_engine.app
start_service execution "$PY" -m services.execution.app

sleep 2
for name in redis risk_engine execution; do
  pid_file="$RUN_DIR/${name}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "${name}=running pid=$(cat "$pid_file")"
  else
    echo "${name}=not_running"
    [[ -f "$LOG_DIR/${name}.log" ]] && tail -40 "$LOG_DIR/${name}.log" || true
    exit 2
  fi
done

redis-cli -h 127.0.0.1 -p "$PORT" XINFO STREAM alpha.target.v17_shadow >/dev/null 2>&1 || true

echo "redis_url=${REDIS_URL}"
echo "logs=${LOG_DIR}"
echo "mode=paper_shadow dry_run=true v17_dry_run_integration=true"
