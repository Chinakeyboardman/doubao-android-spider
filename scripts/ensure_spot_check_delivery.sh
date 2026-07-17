#!/usr/bin/env bash
# 交付前一键：清理重复 screen、全量重启 worker + monitor + watchdog，并打印进度。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VAR_DIR="${SPOT_CHECK_VAR_DIR:-var/vivo-x-fold6}"
BATCH_DIR="${SPOT_CHECK_BATCH_DIR:-${VAR_DIR}/spot_check/20260714}"
RESTART="${SPOT_CHECK_RESTART_SCRIPT:-${ROOT}/var/vivo-x-fold6/run_multi.sh}"

export SPOT_CHECK_VAR_DIR="$VAR_DIR"
export SPOT_CHECK_BATCH_DIR="$BATCH_DIR"
export SPOT_CHECK_OUT_CSV="${SPOT_CHECK_OUT_CSV:-${BATCH_DIR}/抽检明细_APP采集.csv}"
export SPOT_CHECK_TOTAL="${SPOT_CHECK_TOTAL:-123}"

echo "=== 抽检交付保障 $(date '+%F %T') ==="
echo "批次: $BATCH_DIR"

# 杀掉游离的 spotcheck 相关 screen（保留即将启动的新会话）
while read -r sess; do
  [[ -z "$sess" ]] && continue
  screen -S "$sess" -X quit 2>/dev/null || true
done < <(screen -ls 2>/dev/null | awk '/spotcheck/ {print $1}')
pkill -f "monitor_spot_check.sh" 2>/dev/null || true
pkill -f "watch_stuck_worker.sh" 2>/dev/null || true
sleep 2

bash "$RESTART" stop
sleep 2
bash "$RESTART" start

echo ""
bash "$RESTART" status

done_n=$(
  ROOT="$ROOT" OUT_CSV="$SPOT_CHECK_OUT_CSV" .venv/bin/python - <<'PY'
import os, sys
sys.path.insert(0, os.environ["ROOT"])
from app.modules.qa_spot_check_export import spot_check_csv_stats
u, r = spot_check_csv_stats(os.environ["OUT_CSV"])
print(u)
PY
)
echo ""
echo "当前完成: ${done_n}/${SPOT_CHECK_TOTAL}"
echo "监控日志: ${BATCH_DIR}/spot_check_monitor.log"
echo "看门狗:   ${BATCH_DIR}/spot_check_watch.log"
echo "主日志:   ${BATCH_DIR}/spot_check_run.log"
echo ""
echo "无人值守已就绪：monitor 每 90s 巡检，watchdog 每 ${WATCH_STUCK_INTERVAL_SEC:-180}s 查卡死（认领>${SPOT_CHECK_CLAIM_STALE_SEC:-3600}s）。"
