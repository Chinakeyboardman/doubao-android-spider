#!/usr/bin/env bash
# 无人值守抽检：screen 会话跑 worker + monitor（脱离 Cursor/IDE 终端）
# 通过环境变量切换项目，默认 vivo-x-fold6。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VAR_DIR="${SPOT_CHECK_VAR_DIR:-var/vivo-x-fold6}"
PROMPTS_FILE="${SPOT_CHECK_PROMPTS_FILE:-${VAR_DIR}/签单提示词导出_20260710_183049.csv}"
OUT_CSV="${SPOT_CHECK_OUT_CSV:-${VAR_DIR}/抽检明细_20260710_APP采集.csv}"
STATE_FILE="${SPOT_CHECK_STATE_FILE:-${VAR_DIR}/spot_check_state.json}"
FAILURES_FILE="${SPOT_CHECK_FAILURES_FILE:-${VAR_DIR}/spot_check_failures.jsonl}"
PROJECT_SLUG="${SPOT_CHECK_PROJECT:-}"
LOG="${SPOT_CHECK_LOG:-${VAR_DIR}/spot_check_run.log}"
MONITOR_LOG="${SPOT_CHECK_MONITOR_LOG:-${VAR_DIR}/spot_check_monitor.log}"
PID_FILE="${SPOT_CHECK_PID_FILE:-${VAR_DIR}/spot_check.pid}"
LOCK_DIR="${SPOT_CHECK_LOCK_DIR:-${VAR_DIR}/spot_check.lock.d}"
SCREEN_WORKER="${SPOT_CHECK_SCREEN_WORKER:-spotcheck_worker}"
SCREEN_MONITOR="${SPOT_CHECK_SCREEN_MONITOR:-spotcheck_monitor}"
SERIAL="${ADB_SERIAL:-46H0219118001437}"
TOTAL="${SPOT_CHECK_TOTAL:-123}"

worker_cmd() {
  cat <<EOF
set -euo pipefail
cd "$ROOT"
mkdir -p "$VAR_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if ! pgrep -f "run_qa_spot_check.py" >/dev/null 2>&1; then
    rmdir "$LOCK_DIR" 2>/dev/null || true
    mkdir "$LOCK_DIR"
  fi
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; rm -f "$PID_FILE"; }
trap cleanup EXIT
echo \$\$ >"$PID_FILE"
export PYTHONUNBUFFERED=1
exec caffeinate -i .venv/bin/python run_qa_spot_check.py \\
  -s "$SERIAL" \\
  --prompts-file "$PROMPTS_FILE" \\
  --out-csv "$OUT_CSV" \\
  --state-file "$STATE_FILE" \\
  --failures-file "$FAILURES_FILE" \\
  --out-dir "$VAR_DIR" \\
  --project "$PROJECT_SLUG" \\
  --purge-incomplete \\
  --resume \\
  --allow-partial-douyin-urls \\
  --mode fast \\
  --resolve-method auto
EOF
}

stop_all() {
  pkill -f "run_qa_spot_check.py" 2>/dev/null || true
  screen -S "$SCREEN_WORKER" -X quit 2>/dev/null || true
  screen -S "$SCREEN_MONITOR" -X quit 2>/dev/null || true
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
  sleep 1
}

start_worker() {
  if pgrep -f "run_qa_spot_check.py" >/dev/null 2>&1; then
    echo "worker 已在运行"
    return 0
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
  echo "===== UNATTENDED WORKER $(date '+%Y-%m-%d %H:%M:%S') serial=${SERIAL} var=${VAR_DIR} =====" >>"$LOG"
  screen -dmS "$SCREEN_WORKER" bash -lc "$(worker_cmd) >>\"$LOG\" 2>&1"
  sleep 2
  if pgrep -f "run_qa_spot_check.py" >/dev/null 2>&1; then
    echo "worker 已启动 (screen=${SCREEN_WORKER})"
    return 0
  fi
  echo "worker 启动失败" >&2
  return 1
}

start_monitor() {
  if screen -ls | grep -q "[.]${SCREEN_MONITOR}"; then
    echo "monitor 已在运行"
    return 0
  fi
  screen -dmS "$SCREEN_MONITOR" bash -lc \
    "cd \"$ROOT\" && export SPOT_CHECK_VAR_DIR=\"$VAR_DIR\" SPOT_CHECK_OUT_CSV=\"$OUT_CSV\" SPOT_CHECK_TOTAL=\"$TOTAL\" SPOT_CHECK_MONITOR_LOG=\"$MONITOR_LOG\" SPOT_CHECK_PID_FILE=\"$PID_FILE\" && exec bash scripts/monitor_spot_check.sh 90 ${TOTAL} >>\"$MONITOR_LOG\" 2>&1"
  sleep 1
  echo "monitor 已启动 (screen=${SCREEN_MONITOR}, interval=90s)"
}

count_csv_rows() {
  OUT_CSV="$OUT_CSV" .venv/bin/python - <<'PY' 2>/dev/null || echo 0
import csv
import os
from pathlib import Path
p = Path(os.environ["OUT_CSV"])
if not p.exists():
    print(0)
    raise SystemExit
with p.open(encoding="utf-8-sig", newline="") as f:
    n = sum(1 for _ in csv.reader(f))
print(max(0, n - 1))
PY
}

status() {
  local done_n
  done_n=$(count_csv_rows)
  echo "项目: ${VAR_DIR}"
  echo "CSV完成: ${done_n}/${TOTAL}"
  if pgrep -f "\.venv/bin/python run_qa_spot_check\.py" >/dev/null 2>&1; then
    echo "worker: running pid=$(pgrep -f '\.venv/bin/python run_qa_spot_check\.py' | head -1)"
  else
    echo "worker: stopped"
  fi
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
    stop_all
    start_worker
    start_monitor
    status
    ;;
  stop)
    stop_all
    echo "已停止"
    ;;
  restart)
    stop_all
    start_worker
    ;;
  status)
    status
    ;;
  *)
    echo "用法: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
