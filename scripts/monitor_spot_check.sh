#!/usr/bin/env bash
# 抽检进度监控：每 INTERVAL 秒打印；进程意外退出且未完成时自动续跑
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

INTERVAL="${1:-120}"
TOTAL="${2:-123}"
VAR_DIR="${SPOT_CHECK_VAR_DIR:-var/vivo-x-fold6}"
LOG="${SPOT_CHECK_LOG:-${VAR_DIR}/spot_check_run.log}"
CSV="${SPOT_CHECK_OUT_CSV:-${VAR_DIR}/抽检明细_20260710_APP采集.csv}"
PID_FILE="${SPOT_CHECK_PID_FILE:-${VAR_DIR}/spot_check.pid}"
MONITOR_LOG="${SPOT_CHECK_MONITOR_LOG:-${VAR_DIR}/spot_check_monitor.log}"

count_done() {
  ROOT="$ROOT" CSV="$CSV" "$ROOT/.venv/bin/python" - <<'PY' 2>/dev/null || echo 0
import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ["ROOT"])
from app.modules.qa_spot_check_export import count_unique_completed_keywords

p = os.environ["CSV"]
print(count_unique_completed_keywords(p))
PY
}

is_running() {
  if [[ -n "${SPOT_CHECK_SERIALS:-}" ]]; then
    local s
    for s in $SPOT_CHECK_SERIALS; do
      if pgrep -f "run_qa_spot_check.py.*-s[ =]${s}" >/dev/null 2>&1; then
        return 0
      fi
    done
    return 1
  fi
  if pgrep -f "run_qa_spot_check.py" >/dev/null 2>&1; then
    return 0
  fi
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

worker_status_line() {
  if [[ -n "${SPOT_CHECK_SERIALS:-}" ]]; then
    local s n=0 total=0 line=""
    for s in $SPOT_CHECK_SERIALS; do
      total=$((total + 1))
      if pgrep -f "run_qa_spot_check.py.*-s[ =]${s}" >/dev/null 2>&1; then
        n=$((n + 1))
        line+=" ${s}:ok"
      else
        line+=" ${s}:down"
      fi
    done
    echo "workers ${n}/${total}${line}"
    return
  fi
  if is_running; then
    if [[ -f "$PID_FILE" ]]; then
      echo "running pid=$(cat "$PID_FILE")"
    else
      echo "running"
    fi
  else
    echo "not running"
  fi
}

maybe_restart() {
  local done_n=$1
  if (( done_n >= TOTAL )); then
    return
  fi
  if [[ -n "${SPOT_CHECK_SERIALS:-}" ]]; then
    local s ts restarted=0
    for s in $SPOT_CHECK_SERIALS; do
      if pgrep -f "run_qa_spot_check.py.*-s[ =]${s}" >/dev/null 2>&1; then
        continue
      fi
      ts=$(date '+%Y-%m-%d %H:%M:%S')
      echo "[$ts] worker 已退出 SN=${s}（完成 ${done_n}/${TOTAL}），单独续跑" | tee -a "$MONITOR_LOG"
      if [[ -n "${SPOT_CHECK_RESTART_SCRIPT:-}" ]]; then
        SPOT_CHECK_SERIALS="$s" bash "$SPOT_CHECK_RESTART_SCRIPT" restart >>"$MONITOR_LOG" 2>&1 || true
        restarted=1
      fi
    done
    if (( restarted )); then
      sleep 5
    fi
    return
  fi
  if is_running; then
    return
  fi
  local ts
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  echo "[$ts] 批量进程已退出且完成 ${done_n}/${TOTAL}，自动续跑" | tee -a "$MONITOR_LOG"
  if [[ -n "${SPOT_CHECK_RESTART_SCRIPT:-}" ]]; then
    bash "$SPOT_CHECK_RESTART_SCRIPT" restart >>"$MONITOR_LOG" 2>&1 || true
  else
    echo "[$ts] 未配置 SPOT_CHECK_RESTART_SCRIPT，请在 var/<项目>/run_unattended.sh 中设置后手动重启" | tee -a "$MONITOR_LOG"
  fi
  sleep 5
}

while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  done_n=$(count_done)
  status=$(worker_status_line)

  line="[$ts] $status | 完成=${done_n}/${TOTAL}"
  echo "$line" | tee -a "$MONITOR_LOG"
  tail -3 "$LOG" 2>/dev/null | sed 's/^/  /' | tee -a "$MONITOR_LOG"

  if (( done_n >= TOTAL )); then
    echo "[$ts] 全部完成 ${done_n}/${TOTAL}" | tee -a "$MONITOR_LOG"
    break
  fi

  maybe_restart "$done_n"
  if ! is_running; then
    if pgrep -f "run_spot_check_batch.sh" >/dev/null 2>&1; then
      echo "[$ts] 批量启动脚本已在运行，等待" | tee -a "$MONITOR_LOG"
    elif [[ -z "${SPOT_CHECK_SERIALS:-}" ]]; then
      echo "[$ts] 续跑启动失败，$INTERVAL 秒后重试" | tee -a "$MONITOR_LOG"
    fi
  fi

  sleep "$INTERVAL"
done
