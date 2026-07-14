#!/usr/bin/env bash
# 抽检批量续跑（自守护 + mkdir 锁 + caffeinate）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SERIAL="${ADB_SERIAL:-46H0219118001437}"
LOG="var/vivo-x-fold6/spot_check_run.log"
PID_FILE="var/vivo-x-fold6/spot_check.pid"
LOCK_DIR="var/vivo-x-fold6/spot_check.lock.d"

mkdir -p var/vivo-x-fold6

# 第一层：脱离调用方终端，避免 IDE/nohup 父进程退出时带走子进程
if [[ "${SPOT_CHECK_DAEMON:-}" != "1" ]]; then
  export SPOT_CHECK_DAEMON=1
  nohup env SPOT_CHECK_DAEMON=1 ADB_SERIAL="$SERIAL" bash "$0" </dev/null >>"$LOG" 2>&1 &
  echo $! >"$PID_FILE"
  echo "已后台启动抽检 pid=$! 日志=$LOG"
  exit 0
fi

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  if pgrep -f "run_qa_spot_check.py" >/dev/null 2>&1; then
    echo "另一路抽检已在运行（${LOCK_DIR}），跳过" >>"$LOG"
    exit 0
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "无法获取抽检锁（${LOCK_DIR}）" >>"$LOG"
    exit 1
  fi
  echo "===== 清理陈旧锁后重启 $(date '+%Y-%m-%d %H:%M:%S') =====" >>"$LOG"
fi

cleanup() {
  rm -f "$PID_FILE"
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

echo $$ >"$PID_FILE"
echo "===== BATCH $(date '+%Y-%m-%d %H:%M:%S') pid=$$ serial=$SERIAL =====" >>"$LOG"

export PYTHONUNBUFFERED=1
PY_ARGS=(
  run_qa_spot_check.py
  -s "$SERIAL"
  --purge-incomplete
  --resume
  --strict
  --allow-partial-douyin-urls
  --mode fast
  --resolve-method auto
)

if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -i .venv/bin/python "${PY_ARGS[@]}"
else
  exec .venv/bin/python "${PY_ARGS[@]}"
fi
