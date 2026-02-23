#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="${ROOT_DIR}/.venv/bin/python"
LOG_DIR="${ROOT_DIR}/logs/local"
PID_DIR="${ROOT_DIR}/run/local"

SERVICES=(
  "market_data:services.market_data.app"
  "portfolio_state:services.portfolio_state.app"
  "ai_decision:services.ai_decision.app"
  "risk_engine:services.risk_engine.app"
  "execution:services.execution.app"
)

REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
HL_HTTP_URL="${HL_HTTP_URL:-https://api.hyperliquid.xyz}"
DRY_RUN="${DRY_RUN:-true}"
UNIVERSE="${UNIVERSE:-BTC,ETH,SOL,ADA,DOGE}"
HL_ACCOUNT_ADDRESS="${HL_ACCOUNT_ADDRESS:-}"
HL_PRIVATE_KEY="${HL_PRIVATE_KEY:-}"
METRICS_ENABLED="${METRICS_ENABLED:-false}"
RESET_CTL_MODE="${RESET_CTL_MODE:-true}"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <start|stop|restart|status|logs>

Environment (optional):
  REDIS_URL           default: ${REDIS_URL}
  HL_HTTP_URL         default: ${HL_HTTP_URL}
  DRY_RUN             default: ${DRY_RUN}
  UNIVERSE            default: ${UNIVERSE}
  HL_ACCOUNT_ADDRESS  required for portfolio_state snapshot/reconcile
  HL_PRIVATE_KEY      required only when DRY_RUN=false
  METRICS_ENABLED     default: ${METRICS_ENABLED} (set false to avoid local bind issues)
  RESET_CTL_MODE      default: ${RESET_CTL_MODE} (clear ctl.mode on start for local debug)
EOF
}

pid_file() {
  local name="$1"
  echo "${PID_DIR}/${name}.pid"
}

log_file() {
  local name="$1"
  echo "${LOG_DIR}/${name}.log"
}

is_running() {
  local name="$1"
  local pf
  pf="$(pid_file "${name}")"
  if [[ -f "${pf}" ]]; then
    local pid
    pid="$(cat "${pf}")"
    if kill -0 "${pid}" >/dev/null 2>&1; then
      return 0
    fi
  fi
  return 1
}

start_one() {
  local name="$1"
  local module="$2"

  if is_running "${name}"; then
    echo "[skip] ${name} already running (pid=$(cat "$(pid_file "${name}")"))"
    return
  fi

  local logf
  logf="$(log_file "${name}")"
  local pf
  pf="$(pid_file "${name}")"

  : >"${logf}"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] starting ${name} module=${module}" >>"${logf}"

  (
    cd "${ROOT_DIR}"
    REDIS_URL="${REDIS_URL}" \
    HL_HTTP_URL="${HL_HTTP_URL}" \
    DRY_RUN="${DRY_RUN}" \
    UNIVERSE="${UNIVERSE}" \
    HL_ACCOUNT_ADDRESS="${HL_ACCOUNT_ADDRESS}" \
    HL_PRIVATE_KEY="${HL_PRIVATE_KEY}" \
    METRICS_ENABLED="${METRICS_ENABLED}" \
    nohup "${VENV_PY}" -m "${module}" >>"${logf}" 2>&1 &
    echo $! >"${pf}"
  )

  sleep 0.3
  if is_running "${name}"; then
    echo "[ok] started ${name} (pid=$(cat "${pf}")) log=${logf}"
  else
    echo "[error] ${name} exited immediately. tail log:"
    tail -n 25 "${logf}" || true
  fi
}

stop_one() {
  local name="$1"
  local pf
  pf="$(pid_file "${name}")"
  if [[ ! -f "${pf}" ]]; then
    echo "[skip] ${name} not running"
    return
  fi

  local pid
  pid="$(cat "${pf}")"
  if kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 0.2
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -9 "${pid}" >/dev/null 2>&1 || true
    fi
    echo "[ok] stopped ${name} (pid=${pid})"
  else
    echo "[skip] ${name} pid file exists but process not running"
  fi
  rm -f "${pf}"
}

status_all() {
  for entry in "${SERVICES[@]}"; do
    IFS=":" read -r name _ <<<"${entry}"
    if is_running "${name}"; then
      echo "[up]   ${name} pid=$(cat "$(pid_file "${name}")") log=$(log_file "${name}")"
    else
      echo "[down] ${name}"
    fi
  done
}

logs_all() {
  echo "=== tail logs (last 30 lines) ==="
  for entry in "${SERVICES[@]}"; do
    IFS=":" read -r name _ <<<"${entry}"
    local_log="$(log_file "${name}")"
    echo ""
    echo "--- ${name}: ${local_log} ---"
    if [[ -f "${local_log}" ]]; then
      tail -n 30 "${local_log}"
    else
      echo "(no log yet)"
    fi
  done
}

preflight() {
  if [[ ! -x "${VENV_PY}" ]]; then
    echo "[error] python not found: ${VENV_PY}"
    exit 1
  fi
  if [[ -z "${HL_ACCOUNT_ADDRESS}" ]]; then
    echo "[warn] HL_ACCOUNT_ADDRESS is empty. portfolio_state will not produce state.snapshot."
  fi
  if [[ "${DRY_RUN}" == "false" && -z "${HL_PRIVATE_KEY}" ]]; then
    echo "[error] DRY_RUN=false but HL_PRIVATE_KEY is empty"
    exit 1
  fi
  # Quick Redis connectivity check (fail fast with clear message).
  if ! "${VENV_PY}" -c "import redis,sys; r=redis.Redis.from_url('${REDIS_URL}', decode_responses=True); r.ping(); print('ok')" >/dev/null 2>&1; then
    echo "[error] cannot connect to Redis: ${REDIS_URL}"
    echo "        suggestion: use REDIS_URL=redis://127.0.0.1:6379/0 and ensure redis is running"
    exit 1
  fi
}

show_ctl_mode() {
  "${VENV_PY}" -c "import redis,json; r=redis.Redis.from_url('${REDIS_URL}', decode_responses=True); v=r.get('ctl.mode'); print(v if v else 'null')" 2>/dev/null || true
}

reset_ctl_mode_if_needed() {
  if [[ "${RESET_CTL_MODE}" != "true" ]]; then
    return
  fi
  "${VENV_PY}" -c "import redis; r=redis.Redis.from_url('${REDIS_URL}', decode_responses=True); r.delete('ctl.mode')" >/dev/null 2>&1 || true
  echo "[info] ctl.mode cleared (RESET_CTL_MODE=true)"
}

cmd="${1:-}"
case "${cmd}" in
  start)
    preflight
    echo "[info] ctl.mode before start: $(show_ctl_mode)"
    reset_ctl_mode_if_needed
    echo "[info] ctl.mode after reset: $(show_ctl_mode)"
    for entry in "${SERVICES[@]}"; do
      IFS=":" read -r name module <<<"${entry}"
      start_one "${name}" "${module}"
    done
    status_all
    ;;
  stop)
    for entry in "${SERVICES[@]}"; do
      IFS=":" read -r name _ <<<"${entry}"
      stop_one "${name}"
    done
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    status_all
    ;;
  logs)
    logs_all
    ;;
  *)
    usage
    exit 1
    ;;
esac
