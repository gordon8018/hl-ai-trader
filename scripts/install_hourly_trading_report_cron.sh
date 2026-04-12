#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/local"
LOG_FILE="${LOG_DIR}/hourly_trading_report.log"
mkdir -p "${LOG_DIR}"

CRON_TAG="# hl-ai-trader-hourly-report"
CRON_CMD="5 * * * * cd ${ROOT_DIR} && /usr/bin/env bash -lc './.venv/bin/python scripts/hourly_trading_health_report.py >> ${LOG_FILE} 2>&1' ${CRON_TAG}"

TMP="$(mktemp)"
crontab -l 2>/dev/null | sed "/${CRON_TAG//\//\\/}/d" > "${TMP}" || true
echo "${CRON_CMD}" >> "${TMP}"
crontab "${TMP}"
rm -f "${TMP}"

echo "Installed hourly cron job:"
echo "${CRON_CMD}"
