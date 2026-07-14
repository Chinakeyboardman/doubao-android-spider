#!/usr/bin/env bash
# 雅诗兰黛项目无人值守抽检
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export SPOT_CHECK_VAR_DIR="var/雅诗兰黛"
export SPOT_CHECK_PROMPTS_FILE="var/雅诗兰黛/签单提示词导出_20260714_000454.xlsx"
export SPOT_CHECK_OUT_CSV="var/雅诗兰黛/抽检明细_20260714_APP采集.csv"
export SPOT_CHECK_STATE_FILE="var/雅诗兰黛/spot_check_state.json"
export SPOT_CHECK_FAILURES_FILE="var/雅诗兰黛/spot_check_failures.jsonl"
export SPOT_CHECK_PROJECT="雅诗兰黛"
export SPOT_CHECK_TOTAL="32"
export SPOT_CHECK_SCREEN_WORKER="spotcheck_estee_worker"
export SPOT_CHECK_SCREEN_MONITOR="spotcheck_estee_monitor"
export SPOT_CHECK_RESTART_SCRIPT="$ROOT/scripts/run_unattended_spot_check_estee.sh"

exec bash "$ROOT/scripts/run_unattended_spot_check.sh" "$@"
