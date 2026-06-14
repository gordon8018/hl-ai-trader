#!/usr/bin/env bash
# L1: Install scheduled autoresearch cron job.
# Runs daily at 03:15 UTC; the script itself enforces the 14-day biweekly schedule
# and 7-day minimum cooldown, so running the check daily is harmless.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/local"
LOG_FILE="${LOG_DIR}/scheduled_autoresearch.log"
mkdir -p "${LOG_DIR}"

CRON_TAG="# hl-ai-trader-scheduled-autoresearch"
CRON_CMD="15 3 * * * cd ${ROOT_DIR} && /usr/bin/env bash -lc './.venv/bin/python scripts/scheduled_autoresearch.py >> ${LOG_FILE} 2>&1' ${CRON_TAG}"

TMP="$(mktemp)"
crontab -l 2>/dev/null | sed "/${CRON_TAG//\//\\/}/d" > "${TMP}" || true
echo "${CRON_CMD}" >> "${TMP}"
crontab "${TMP}"
rm -f "${TMP}"

echo "Installed scheduled autoresearch cron job (daily 03:15 UTC):"
echo "${CRON_CMD}"
echo ""
echo "Override env vars (set in infra/.env or shell before cron runs):"
echo "  AR_BIWEEKLY_DAYS=14        # days between scheduled runs"
echo "  AR_MIN_COOLDOWN_DAYS=7     # minimum days between any two runs"
echo "  AR_IC_DECAY_TRIGGER=-0.05  # IC_1h threshold triggering early research"
echo "  AR_IC_STREAK_THRESHOLD=3   # consecutive bad IC observations to trigger"
echo ""
echo "Manual test (dry-run):"
echo "  .venv/bin/python scripts/scheduled_autoresearch.py --dry-run"
echo "  .venv/bin/python scripts/scheduled_autoresearch.py --force --dry-run"
