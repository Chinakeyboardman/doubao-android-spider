#!/usr/bin/env bash
# 抽检批次健康巡检：定时扫日志，发现问题打印到 stderr
set -euo pipefail

LOG="${1:-var/vivo-x-fold6/spot_check/20260714/spot_check_run.log}"
CSV="${2:-var/vivo-x-fold6/spot_check/20260714/抽检明细_APP采集.csv}"
INTERVAL="${3:-90}"
RESTART_MARK="${4:-2026-07-16 17:22}"

last_progress=""
last_line_count=0

alert() { echo "[WATCH $(date '+%H:%M:%S')] $*" >&2; }

while true; do
  if [[ ! -f "$LOG" ]]; then
    alert "日志不存在: $LOG"
    sleep "$INTERVAL"
    continue
  fi

  # 仅看重启后的日志段
  tmp=$(mktemp)
  awk -v mark="$RESTART_MARK" '$0 >= "[" mark {print}' "$LOG" >"$tmp" 2>/dev/null || cp "$LOG" "$tmp"

  # 旧策略回流
  if grep -qE '快速逐条|笨办法补齐|笨办法 [0-9]|URL 阶段超时|会话错位' "$tmp" 2>/dev/null; then
    alert "⚠️ 检测到旧多策略/异常: $(grep -E '快速逐条|笨办法补齐|笨办法 [0-9]|URL 阶段超时|会话错位' "$tmp" | tail -3)"
  fi

  # worker 存活
  running=$(bash var/vivo-x-fold6/run_multi.sh status 2>/dev/null | grep -c 'running pid=' || true)
  if [[ "$running" -lt 4 ]]; then
    alert "⚠️ worker 不足 4 台: ${running}/4"
  fi

  # 进度
  if [[ -f "$CSV" ]]; then
    prog=$(.venv/bin/python -c "from app.modules.qa_spot_check_export import spot_check_csv_stats; print(spot_check_csv_stats('$CSV')[0])" 2>/dev/null || echo "?")
    if [[ "$prog" != "$last_progress" && -n "$prog" && "$prog" != "?" ]]; then
      alert "✅ 进度更新: ${prog}/123"
      last_progress="$prog"
    fi
  fi

  # URL 单遍速率（Honor 抽样）
  honor_urls=$(grep '46H0219118001437.*解析引用 [0-9]' "$tmp" | tail -3)
  if [[ -n "$honor_urls" ]]; then
    alert "Honor URL: $(echo "$honor_urls" | tail -1 | sed 's/.*\[SN=[^]]*\] //')"
  fi

  # 日志停滞
  lc=$(wc -l <"$LOG" | tr -d ' ')
  if [[ "$lc" == "$last_line_count" && "$last_line_count" -gt 0 ]]; then
    alert "⚠️ 日志 ${INTERVAL}s 无新行，可能卡住"
  fi
  last_line_count="$lc"

  # 最近错误
  errs=$(grep -E 'ERROR|Traceback|device not found|AdbError|创建新对话失败|未获取到有效问答' "$tmp" | tail -2)
  if [[ -n "$errs" ]]; then
    alert "最近异常: $errs"
  fi

  rm -f "$tmp"
  sleep "$INTERVAL"
done
