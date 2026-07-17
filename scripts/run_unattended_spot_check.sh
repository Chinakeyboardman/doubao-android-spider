#!/usr/bin/env bash
# 无人值守抽检：screen 会话跑 worker + monitor（脱离 Cursor/IDE 终端）
#
# 通用入口：通过环境变量配置项目路径与产出文件。
# 多机：设置 SPOT_CHECK_SERIALS="serial1 serial2" + SPOT_CHECK_USE_CLAIMS=1
# 项目专用包装脚本请放在 var/<项目>/run_unattended.sh（不入 git），示例：
#   bash var/vivo-x-fold6/run_unattended.sh start
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VAR_DIR="${SPOT_CHECK_VAR_DIR:-var/vivo-x-fold6}"
BATCH_DIR="${SPOT_CHECK_BATCH_DIR:-}"
WORK_DIR="${BATCH_DIR:-$VAR_DIR}"

PROMPTS_FILE="${SPOT_CHECK_PROMPTS_FILE:-${VAR_DIR}/签单提示词导出_20260710_183049.csv}"
if [[ -n "$BATCH_DIR" ]]; then
  OUT_CSV="${SPOT_CHECK_OUT_CSV:-${BATCH_DIR}/抽检明细_APP采集.csv}"
  STATE_FILE="${SPOT_CHECK_STATE_FILE:-${BATCH_DIR}/spot_check_state.json}"
  FAILURES_FILE="${SPOT_CHECK_FAILURES_FILE:-${BATCH_DIR}/spot_check_failures.jsonl}"
  LOG="${SPOT_CHECK_LOG:-${BATCH_DIR}/spot_check_run.log}"
  MONITOR_LOG="${SPOT_CHECK_MONITOR_LOG:-${BATCH_DIR}/spot_check_monitor.log}"
  PID_FILE="${SPOT_CHECK_PID_FILE:-${BATCH_DIR}/spot_check.pid}"
  LOCK_DIR="${SPOT_CHECK_LOCK_DIR:-${BATCH_DIR}/spot_check.lock.d}"
else
  OUT_CSV="${SPOT_CHECK_OUT_CSV:-${VAR_DIR}/抽检明细_20260710_APP采集.csv}"
  STATE_FILE="${SPOT_CHECK_STATE_FILE:-${VAR_DIR}/spot_check_state.json}"
  FAILURES_FILE="${SPOT_CHECK_FAILURES_FILE:-${VAR_DIR}/spot_check_failures.jsonl}"
  LOG="${SPOT_CHECK_LOG:-${VAR_DIR}/spot_check_run.log}"
  MONITOR_LOG="${SPOT_CHECK_MONITOR_LOG:-${VAR_DIR}/spot_check_monitor.log}"
  PID_FILE="${SPOT_CHECK_PID_FILE:-${VAR_DIR}/spot_check.pid}"
  LOCK_DIR="${SPOT_CHECK_LOCK_DIR:-${VAR_DIR}/spot_check.lock.d}"
fi
PROJECT_SLUG="${SPOT_CHECK_PROJECT:-}"
SCREEN_WORKER="${SPOT_CHECK_SCREEN_WORKER:-spotcheck_worker}"
SCREEN_MONITOR="${SPOT_CHECK_SCREEN_MONITOR:-spotcheck_monitor}"
SCREEN_WATCH="${SPOT_CHECK_SCREEN_WATCH:-spotcheck_watch}"
SERIAL="${ADB_SERIAL:-46H0219118001437}"
TOTAL="${SPOT_CHECK_TOTAL:-123}"
ALLOW_PARTIAL_DOUYIN="${SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS:-1}"
USE_CLAIMS="${SPOT_CHECK_USE_CLAIMS:-0}"
CLAIMS_DIR="${SPOT_CHECK_CLAIMS_DIR:-${BATCH_DIR:+${BATCH_DIR}/claims}}"
CLAIM_STALE_SEC="${SPOT_CHECK_CLAIM_STALE_SEC:-1500}"
MAX_RETRIES="${SPOT_CHECK_MAX_RETRIES:-4}"
PARTIAL_FLAG=""
if [[ "$ALLOW_PARTIAL_DOUYIN" != "0" && "$ALLOW_PARTIAL_DOUYIN" != "false" && "$ALLOW_PARTIAL_DOUYIN" != "no" ]]; then
  PARTIAL_FLAG="--allow-partial-douyin-urls"
fi

# 多机 serial 列表（空格分隔）；未设则单机 ADB_SERIAL
if [[ -n "${SPOT_CHECK_SERIALS:-}" ]]; then
  read -r -a SERIAL_LIST <<<"${SPOT_CHECK_SERIALS}"
else
  SERIAL_LIST=("$SERIAL")
fi

_serial_suffix() {
  local s="$1"
  echo "${s: -6}"
}

_lock_dir_for() {
  local s="$1"
  if [[ -n "$BATCH_DIR" ]]; then
    echo "${BATCH_DIR}/spot_check.lock.$(_serial_suffix "$s").d"
  else
    echo "${VAR_DIR}/spot_check.lock.$(_serial_suffix "$s").d"
  fi
}

_pid_file_for() {
  local s="$1"
  if [[ -n "$BATCH_DIR" ]]; then
    echo "${BATCH_DIR}/spot_check.$(_serial_suffix "$s").pid"
  else
    echo "${VAR_DIR}/spot_check.$(_serial_suffix "$s").pid"
  fi
}

_screen_worker_for() {
  local s="$1"
  echo "${SCREEN_WORKER}_$(_serial_suffix "$s")"
}

_sms_device_for() {
  local s="$1"
  case "$s" in
    10ADBY1Z7C0042Z) echo "${SPOT_CHECK_SMS_10ADBY1Z7C0042Z:-doubao-crawler-vivo-v2301}" ;;
    10AE3B0DSU0063K) echo "${SPOT_CHECK_SMS_10AE3B0DSU0063K:-doubao-crawler-vivo-063k}" ;;
    10AE3F0PNK00657) echo "${SPOT_CHECK_SMS_10AE3F0PNK00657:-doubao-crawler-vivo-0657}" ;;
    46H0219118001437) echo "${SPOT_CHECK_SMS_46H0219118001437:-${SMS_DEVICE_ID:-doubao-crawler-01}}" ;;
    *) echo "${SMS_DEVICE_ID:-doubao-crawler-vivo-v2301}" ;;
  esac
}

worker_cmd() {
  local serial="$1"
  local lock_dir pid_file sms_id claims_dir_arg worker_id_arg
  lock_dir="$(_lock_dir_for "$serial")"
  pid_file="$(_pid_file_for "$serial")"
  sms_id="$(_sms_device_for "$serial")"
  claims_dir_arg=""
  worker_id_arg=""
  if [[ "$USE_CLAIMS" == "1" || "$USE_CLAIMS" == "true" || "$USE_CLAIMS" == "yes" ]]; then
    claims_dir_arg="--claims-dir \"${CLAIMS_DIR}\""
    worker_id_arg="--worker-id \"${serial}\""
  fi
  cat <<EOF
set -euo pipefail
cd "$ROOT"
mkdir -p "$WORK_DIR"
WORKER_LOG="${WORK_DIR}/spot_check_run.$(_serial_suffix "$serial").log"
if ! mkdir "$lock_dir" 2>/dev/null; then
  if ! pgrep -f "run_qa_spot_check.py.*-s[ =]${serial}" >/dev/null 2>&1; then
    rmdir "$lock_dir" 2>/dev/null || true
    mkdir "$lock_dir"
  fi
fi
cleanup() { rmdir "$lock_dir" 2>/dev/null || true; rm -f "$pid_file"; }
trap cleanup EXIT
echo \$\$ >"$pid_file"
export PYTHONUNBUFFERED=1
export SMS_API_TOKEN="${SMS_API_TOKEN:-}"
export SMS_DEVICE_ID="${sms_id}"
export SPOT_CHECK_USE_CLAIMS="${USE_CLAIMS}"
exec caffeinate -i .venv/bin/python run_qa_spot_check.py \\
  -s "${serial}" \\
  --prompts-file "$PROMPTS_FILE" \\
  --out-csv "$OUT_CSV" \\
  --state-file "$STATE_FILE" \\
  --failures-file "$FAILURES_FILE" \\
  --out-dir "$WORK_DIR" \\
  --project "$PROJECT_SLUG" \\
  --purge-incomplete \\
  --resume \\
  $PARTIAL_FLAG \\
  --mode fast \\
  --resolve-method auto \\
  --claim-stale-sec ${CLAIM_STALE_SEC} \\
  --max-retries ${MAX_RETRIES} \\
  ${claims_dir_arg} \\
  ${worker_id_arg}
EOF
}

stop_worker_serial() {
  local serial="$1"
  local lock_dir pid_file screen_name
  lock_dir="$(_lock_dir_for "$serial")"
  pid_file="$(_pid_file_for "$serial")"
  screen_name="$(_screen_worker_for "$serial")"
  pkill -f "run_qa_spot_check.py.*-s[ =]${serial}" 2>/dev/null || true
  screen -S "$screen_name" -X quit 2>/dev/null || true
  rm -f "$pid_file"
  rmdir "$lock_dir" 2>/dev/null || true
}

stop_all() {
  local s
  for s in "${SERIAL_LIST[@]}"; do
    stop_worker_serial "$s"
  done
  # 清理历史遗留的 monitor/watch screen（避免重复 tee 写日志）
  while read -r sess; do
    [[ -z "$sess" ]] && continue
    screen -S "$sess" -X quit 2>/dev/null || true
  done < <(screen -ls 2>/dev/null | awk '/spotcheck/ {print $1}')
  screen -S "$SCREEN_MONITOR" -X quit 2>/dev/null || true
  screen -S "$SCREEN_WATCH" -X quit 2>/dev/null || true
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
  sleep 1
}

start_worker() {
  local serial="$1"
  local screen_name
  screen_name="$(_screen_worker_for "$serial")"
  if pgrep -f "run_qa_spot_check.py.*-s[ =]${serial}" >/dev/null 2>&1; then
    echo "worker 已在运行 (SN=${serial})"
    return 0
  fi
  rmdir "$(_lock_dir_for "$serial")" 2>/dev/null || true
  echo "===== UNATTENDED WORKER $(date '+%Y-%m-%d %H:%M:%S') SN=${serial} var=${VAR_DIR} batch=${BATCH_DIR:-—} work=${WORK_DIR} claims=${USE_CLAIMS} =====" | tee -a "$LOG" >>"${WORK_DIR}/spot_check_run.$(_serial_suffix "$serial").log"
  screen -dmS "$screen_name" bash -lc "$(worker_cmd "$serial") 2>&1 | tee -a \"$LOG\" >>\"${WORK_DIR}/spot_check_run.$(_serial_suffix "$serial").log\""
  sleep 2
  if pgrep -f "run_qa_spot_check.py.*-s[ =]${serial}" >/dev/null 2>&1; then
    echo "worker 已启动 SN=${serial} (screen=${screen_name})"
    return 0
  fi
  echo "worker 启动失败 SN=${serial}" >&2
  return 1
}

start_all_workers() {
  local s failed=0
  for s in "${SERIAL_LIST[@]}"; do
    stop_worker_serial "$s"
    if ! start_worker "$s"; then
      failed=1
    fi
  done
  return "$failed"
}

start_monitor() {
  if screen -ls 2>/dev/null | grep -q "[.]${SCREEN_MONITOR}"; then
    echo "monitor 已在运行"
    return 0
  fi
  screen -dmS "$SCREEN_MONITOR" bash -lc \
    "cd \"$ROOT\" && export SPOT_CHECK_VAR_DIR=\"$VAR_DIR\" SPOT_CHECK_BATCH_DIR=\"$BATCH_DIR\" SPOT_CHECK_OUT_CSV=\"$OUT_CSV\" SPOT_CHECK_TOTAL=\"$TOTAL\" SPOT_CHECK_MONITOR_LOG=\"$MONITOR_LOG\" SPOT_CHECK_PID_FILE=\"$PID_FILE\" SPOT_CHECK_SERIALS=\"${SPOT_CHECK_SERIALS:-}\" SPOT_CHECK_RESTART_SCRIPT=\"${SPOT_CHECK_RESTART_SCRIPT:-}\" && exec bash scripts/monitor_spot_check.sh 90 ${TOTAL} >>\"$MONITOR_LOG\" 2>&1"
  sleep 1
  echo "monitor 已启动 (screen=${SCREEN_MONITOR}, interval=90s)"
}

start_watchdog() {
  if [[ "$USE_CLAIMS" != "1" && "$USE_CLAIMS" != "true" && "$USE_CLAIMS" != "yes" ]]; then
    return 0
  fi
  if screen -ls 2>/dev/null | grep -q "[.]${SCREEN_WATCH}"; then
    echo "watchdog 已在运行"
    return 0
  fi
  local watch_log="${BATCH_DIR:+${BATCH_DIR}/spot_check_watch.log}"
  watch_log="${watch_log:-${VAR_DIR}/spot_check_watch.log}"
  screen -dmS "$SCREEN_WATCH" bash -lc \
    "cd \"$ROOT\" && export SPOT_CHECK_VAR_DIR=\"$VAR_DIR\" SPOT_CHECK_BATCH_DIR=\"$BATCH_DIR\" SPOT_CHECK_OUT_CSV=\"$OUT_CSV\" SPOT_CHECK_CLAIMS_DIR=\"${CLAIMS_DIR}\" SPOT_CHECK_SERIALS=\"${SPOT_CHECK_SERIALS:-}\" SPOT_CHECK_RESTART_SCRIPT=\"${SPOT_CHECK_RESTART_SCRIPT:-}\" SPOT_CHECK_WATCH_LOG=\"$watch_log\" WATCH_STUCK_CLAIM_SEC=\"${CLAIM_STALE_SEC}\" && exec bash scripts/watch_stuck_worker.sh >>\"$watch_log\" 2>&1"
  sleep 1
  echo "watchdog 已启动 (screen=${SCREEN_WATCH}, claim_sec=${CLAIM_STALE_SEC})"
}

count_unique_done() {
  ROOT="$ROOT" OUT_CSV="$OUT_CSV" .venv/bin/python - <<'PY' 2>/dev/null || echo 0
import os
import sys

sys.path.insert(0, os.environ["ROOT"])
from app.modules.qa_spot_check_export import spot_check_csv_stats

unique_n, row_n = spot_check_csv_stats(os.environ["OUT_CSV"])
print(f"{unique_n}\t{row_n}")
PY
}

count_claims() {
  CLAIMS_DIR="$CLAIMS_DIR" .venv/bin/python - <<'PY' 2>/dev/null || echo 0
import os
from pathlib import Path
d = Path(os.environ.get("CLAIMS_DIR", ""))
if not d.is_dir():
    print(0)
    raise SystemExit
print(len([p for p in d.glob("*.json") if p.is_file()]))
PY
}

status() {
  local done_n csv_rows claims_n s running=0 progress
  progress=$(count_unique_done)
  done_n="${progress%%$'\t'*}"
  csv_rows="${progress#*$'\t'}"
  claims_n=$(count_claims)
  echo "项目: ${VAR_DIR}"
  if [[ -n "$BATCH_DIR" ]]; then
    echo "批次: ${BATCH_DIR}"
  fi
  echo "产出: ${WORK_DIR}"
  echo "完成: ${done_n}/${TOTAL}（唯一关键词；CSV 行 ${csv_rows}）"
  if [[ "$USE_CLAIMS" == "1" || "$USE_CLAIMS" == "true" || "$USE_CLAIMS" == "yes" ]]; then
    echo "活跃认领: ${claims_n}"
    echo "认领目录: ${CLAIMS_DIR}"
  fi
  for s in "${SERIAL_LIST[@]}"; do
    if pgrep -f "run_qa_spot_check.py.*-s[ =]${s}" >/dev/null 2>&1; then
      echo "worker SN=${s}: running pid=$(pgrep -f "run_qa_spot_check.py.*-s[ =]${s}" | head -1)"
      running=$((running + 1))
    else
      echo "worker SN=${s}: stopped"
    fi
  done
  echo "workers 运行中: ${running}/${#SERIAL_LIST[@]}"
  if screen -ls 2>/dev/null | grep -q "spotcheck_"; then
    screen -ls 2>/dev/null | grep "spotcheck_" | sed 's/^/  /'
  else
    echo "screen: none"
  fi
  echo "--- log tail ---"
  tail -5 "$LOG" 2>/dev/null | sed 's/^/  /'
}

case "${1:-start}" in
  start)
    start_all_workers
    start_monitor
    start_watchdog
    status
    ;;
  stop)
    stop_all
    echo "已停止全部抽检 worker"
    ;;
  restart)
    start_all_workers
    ;;
  watchdog)
    start_watchdog
    ;;
  status)
    status
    ;;
  logs|follow)
    echo "跟踪日志: $LOG (Ctrl+C 退出)"
    echo "提示: 另开终端可运行 bash var/<项目>/run_unattended.sh status"
    exec tail -f "$LOG"
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status|logs|watchdog}" >&2
    exit 1
    ;;
esac
