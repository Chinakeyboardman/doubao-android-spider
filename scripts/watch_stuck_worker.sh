#!/usr/bin/env bash
# 按 serial 检测「认领超时且关键词未进 CSV」的空转 worker，仅重启该台。
#
# 环境变量（与 run_unattended_spot_check.sh 对齐）：
#   SPOT_CHECK_BATCH_DIR / SPOT_CHECK_VAR_DIR
#   SPOT_CHECK_SERIALS / SPOT_CHECK_OUT_CSV / SPOT_CHECK_CLAIMS_DIR
#   SPOT_CHECK_RESTART_SCRIPT（默认 var/<项目>/run_multi.sh）
#   WATCH_STUCK_INTERVAL_SEC（默认 120）
#   WATCH_STUCK_CLAIM_SEC（默认 1500，认领超过此时长且未完成则视为卡死）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INTERVAL="${WATCH_STUCK_INTERVAL_SEC:-120}"
CLAIM_SEC="${WATCH_STUCK_CLAIM_SEC:-1500}"
VAR_DIR="${SPOT_CHECK_VAR_DIR:-var/vivo-x-fold6}"
BATCH_DIR="${SPOT_CHECK_BATCH_DIR:-${VAR_DIR}/spot_check/20260714}"
OUT_CSV="${SPOT_CHECK_OUT_CSV:-${BATCH_DIR}/抽检明细_APP采集.csv}"
CLAIMS_DIR="${SPOT_CHECK_CLAIMS_DIR:-${BATCH_DIR}/claims}"
RESTART_SCRIPT="${SPOT_CHECK_RESTART_SCRIPT:-${ROOT}/var/vivo-x-fold6/run_multi.sh}"
MONITOR_LOG="${SPOT_CHECK_WATCH_LOG:-${BATCH_DIR}/spot_check_watch.log}"

if [[ -n "${SPOT_CHECK_SERIALS:-}" ]]; then
  read -r -a SERIAL_LIST <<<"${SPOT_CHECK_SERIALS}"
else
  SERIAL_LIST=("${ADB_SERIAL:-}")
fi

_serial_suffix() {
  echo "${1: -6}"
}

_log() {
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$ts] [watch] $*" | tee -a "$MONITOR_LOG"
}

_count_unique_done() {
  ROOT="$ROOT" OUT_CSV="$OUT_CSV" "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
import os
import sys

sys.path.insert(0, os.environ["ROOT"])
from app.modules.qa_spot_check_export import count_unique_completed_keywords

print(count_unique_completed_keywords(os.environ["OUT_CSV"]))
PY
}

_scan_stuck_serials() {
  CLAIMS_DIR="$CLAIMS_DIR" OUT_CSV="$OUT_CSV" CLAIM_SEC="$CLAIM_SEC" \
    "$ROOT/.venv/bin/python" - <<'PY'
import json
import os
import time
from datetime import datetime
from pathlib import Path

claims_dir = Path(os.environ["CLAIMS_DIR"])
csv_path = Path(os.environ["OUT_CSV"])
claim_sec = float(os.environ.get("CLAIM_SEC", "1500"))

done_ids: set[str] = set()
if csv_path.is_file():
    import csv
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            kid = (row.get("关键词编号") or "").strip()
            if kid:
                done_ids.add(kid)

if not claims_dir.is_dir():
    raise SystemExit

now = time.time()
stuck: dict[str, str] = {}
for path in sorted(claims_dir.glob("*.json")):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    worker = str(data.get("worker_id") or "")
    kid = str(data.get("keyword_id") or "")
    claimed_at = str(data.get("claimed_at") or "")
    if not worker or not kid or kid in done_ids:
        continue
    try:
        age = now - datetime.fromisoformat(claimed_at).timestamp()
    except ValueError:
        continue
    if age >= claim_sec:
        stuck[worker] = kid

for serial, kid in stuck.items():
    print(f"{serial}\t{kid}")
PY
}

_restart_serial() {
  local serial="$1"
  local kid="$2"
  _log "卡死重启 SN=${serial} claim=${kid}（认领>${CLAIM_SEC}s 且未进 CSV）"
  SPOT_CHECK_SERIALS="$serial" bash "$RESTART_SCRIPT" restart >>"$MONITOR_LOG" 2>&1 || true
  CLAIMS_DIR="$CLAIMS_DIR" WORKER_ID="$serial" KID="$kid" \
    "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || true
import json
import os
from pathlib import Path

claims_dir = Path(os.environ["CLAIMS_DIR"])
worker = os.environ["WORKER_ID"]
kid = os.environ["KID"]
for path in claims_dir.glob("*.json"):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if data.get("worker_id") == worker and data.get("keyword_id") == kid:
        path.unlink(missing_ok=True)
PY
}

while true; done_n=$(_count_unique_done); do
  stuck_lines=$(_scan_stuck_serials || true)
  if [[ -n "$stuck_lines" ]]; then
    while IFS=$'\t' read -r serial kid; do
      [[ -z "$serial" ]] && continue
      if pgrep -f "run_qa_spot_check.py.*-s[ =]${serial}" >/dev/null 2>&1; then
        _restart_serial "$serial" "$kid"
      else
        _log "SN=${serial} 进程已退出，清理僵尸 claim ${kid}"
        CLAIMS_DIR="$CLAIMS_DIR" WORKER_ID="$serial" KID="$kid" \
          "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || true
import json, os
from pathlib import Path
claims_dir = Path(os.environ["CLAIMS_DIR"])
worker = os.environ["WORKER_ID"]
kid = os.environ["KID"]
for path in claims_dir.glob("*.json"):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if data.get("worker_id") == worker and data.get("keyword_id") == kid:
        path.unlink(missing_ok=True)
PY
      fi
    done <<<"$stuck_lines"
  fi
  sleep "$INTERVAL"
done
