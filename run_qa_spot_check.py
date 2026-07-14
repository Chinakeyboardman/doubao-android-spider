#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆包 APP 抽检明细批量采集：读签单提示词 CSV，逐条 QA 采集并写入抽检 CSV。

用法:
  python run_qa_spot_check.py --pilot 10 --strict
  python run_qa_spot_check.py --resume --strict
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.profile_loader import load_profile
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_quality import DEFAULT_MIN_URL_RESOLVE_RATIO, validate_qa_session
from app.modules.qa_spot_check_export import (
  append_csv_row,
  load_batch_meta,
  load_completed_keyword_ids,
  load_completed_sessions,
  load_signed_prompts,
  dedupe_signed_prompts,
  purge_incomplete_spot_check_rows,
  qa_record_to_spot_check_row,
  save_batch_meta,
  select_pilot_rows,
)
from app.utils.device import DeviceManager
from app.utils.utils import log_error, log_info, log_warning


DEFAULT_PROMPTS_FILE = "var/vivo-x-fold6/签单提示词导出_20260710_183049.csv"
DEFAULT_OUT_CSV = "var/vivo-x-fold6/抽检明细_20260710_APP采集.csv"
DEFAULT_STATE = "var/vivo-x-fold6/spot_check_state.json"
DEFAULT_FAILURES = "var/vivo-x-fold6/spot_check_failures.jsonl"
DEFAULT_PROJECT = ""


def _append_failure(path: str, payload: dict) -> None:
  os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
  with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _pick_rows(
  all_rows: list,
  *,
  pilot: int,
  limit: int,
  resume: bool,
  completed: set[str],
) -> list:
  if pilot > 0:
    candidates = select_pilot_rows(all_rows, pilot)
  else:
    candidates = list(all_rows)

  if resume:
    candidates = [r for r in candidates if r.keyword_id not in completed]

  if limit > 0:
    candidates = candidates[:limit]
  return candidates


def main() -> int:
  parser = argparse.ArgumentParser(description="豆包 APP 抽检明细批量采集")
  parser.add_argument(
    "--prompts-file",
    "--prompts-csv",
    dest="prompts_file",
    default=DEFAULT_PROMPTS_FILE,
    help="签单提示词 CSV 或 xlsx 路径",
  )
  parser.add_argument(
    "--out-csv",
    default=DEFAULT_OUT_CSV,
    help="抽检明细产出 CSV",
  )
  parser.add_argument(
    "--state-file",
    default=DEFAULT_STATE,
    help="断点状态 JSON",
  )
  parser.add_argument(
    "--failures-file",
    default=DEFAULT_FAILURES,
    help="失败记录 JSONL",
  )
  parser.add_argument("--pilot", type=int, default=0, help="试点条数（0=全量）")
  parser.add_argument("--limit", type=int, default=0, help="最多处理条数（0=不限制）")
  parser.add_argument("--resume", action="store_true", help="跳过 CSV 中已有行")
  parser.add_argument(
    "--mode",
    choices=("fast", "think"),
    default="fast",
    help="对话模式（推荐 fast）",
  )
  parser.add_argument("--max-retries", type=int, default=2, help="单条质量不达标最大重试次数")
  parser.add_argument(
    "--no-resolve-urls",
    action="store_true",
    help="跳过解析引用 URL",
  )
  parser.add_argument(
    "--resolve-method",
    choices=("auto", "logcat", "dumpsys", "net"),
    default="auto",
    help="引用 URL 解析：auto=logcat+dumpsys 同屏保底（抽检推荐），logcat=仅 logcat+dumpsys",
  )
  parser.add_argument("--net-dump-dir", default="")
  parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
  parser.add_argument("--device-profile", default=None)
  parser.add_argument("--out-dir", default="", help="qa_capture 产出根目录（默认与 state-file 同目录）")
  parser.add_argument(
    "--project",
    default=DEFAULT_PROJECT,
    help="项目隔离目录名（logs/qa_capture/<project>/）",
  )
  parser.add_argument("--sms-token", default=None)
  parser.add_argument("--sms-device-id", default=None)
  parser.add_argument(
    "--strict",
    action="store_true",
    help="任一条最终失败则 exit 2",
  )
  parser.add_argument(
    "--min-url-resolve-ratio",
    type=float,
    default=DEFAULT_MIN_URL_RESOLVE_RATIO,
    help="引用 URL 最低解析率（默认 0.5：超一半无链接则失败）",
  )
  parser.add_argument(
    "--require-all-urls",
    action="store_true",
    help="要求全部引用均有 URL（覆盖 --min-url-resolve-ratio）",
  )
  parser.add_argument(
    "--allow-partial-douyin-urls",
    action="store_true",
    help="抖音引用允许无 URL（网页类引用仍须有链接）",
  )
  parser.add_argument(
    "--purge-incomplete",
    action="store_true",
    help="启动前删除 CSV 中引用/URL 不达标的行（保留 logs 会话）",
  )
  args = parser.parse_args()

  if not args.out_dir.strip():
    args.out_dir = os.path.dirname(os.path.abspath(args.state_file)) or "logs"

  check_date = datetime.now().strftime("%Y-%m-%d")
  if args.purge_incomplete:
    purge = purge_incomplete_spot_check_rows(
      args.out_csv,
      state_path=args.state_file,
      failures_path=args.failures_file,
      min_url_resolve_ratio=args.min_url_resolve_ratio,
      require_refs=True,
      allow_no_references=True,
    )
    if purge.removed_keyword_ids:
      log_info(
        f"已清理 {len(purge.removed_keyword_ids)} 条不完整抽检行: "
        + ", ".join(purge.removed_keyword_ids[:5])
        + ("..." if len(purge.removed_keyword_ids) > 5 else "")
      )
      for kid, reason in purge.removed_reasons.items():
        log_warning(f"  删除 {kid}: {reason}")
    else:
      log_info("无需清理：CSV 中无引用/URL 不达标行")

  all_rows_raw = load_signed_prompts(args.prompts_file)
  all_rows = dedupe_signed_prompts(all_rows_raw)
  if len(all_rows) < len(all_rows_raw):
    log_info(
      f"签单去重: {len(all_rows_raw)} -> {len(all_rows)} 条（相同提示词保留首条）"
    )
  completed_ids = load_completed_keyword_ids(args.out_csv) if args.resume else set()
  completed_sessions = load_completed_sessions(args.state_file) if args.resume else {}
  meta = load_batch_meta(args.state_file, check_date)
  if not meta.check_date:
    meta.check_date = check_date

  todo = _pick_rows(
    all_rows,
    pilot=args.pilot,
    limit=args.limit,
    resume=args.resume,
    completed=completed_ids,
  )
  if not todo:
    log_info("无待处理提示词（已全部完成或未选中）")
    return 0

  log_info(f"待抽检 {len(todo)} 条（签单共 {len(all_rows)} 条）")

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device_name=args.device_profile, device=device)
  capturer = DoubaoQaCapture(
    device,
    output_dir=args.out_dir,
    profile=profile,
    project_slug=args.project,
  )

  failed_count = 0
  success_count = 0

  for idx, signed in enumerate(todo, start=1):
    log_info(
      f"[抽检] 开始 {idx}/{len(todo)} "
      f"意图={signed.intent_name} 提示词={signed.prompt[:40]}..."
    )

    record = None
    report = None
    last_error = ""

    for attempt in range(args.max_retries + 1):
      if attempt > 0:
        log_warning(f"[抽检] 重试 {attempt}/{args.max_retries}：{signed.keyword_id}")

      record = capturer.run(
        prompt=signed.prompt,
        skip_send=False,
        mode=args.mode,
        resolve_urls=not args.no_resolve_urls,
        resolve_method=args.resolve_method,
        net_dump_dir=args.net_dump_dir,
        sms_token=args.sms_token or "",
        sms_device_id=args.sms_device_id or "",
      )

      if not record.answer_body and not record.raw_texts:
        last_error = "未获取到有效问答数据"
        continue

      url_ratio = None if args.require_all_urls else args.min_url_resolve_ratio
      report = validate_qa_session(
        session_dir=record.session_dir,
        answer_body=record.answer_body,
        thinking=record.thinking,
        thinking_references=record.thinking_references,
        screenshots=record.screenshots,
        stitched_screenshot=record.stitched_screenshot,
        mode=record.mode,
        require_all_urls=args.require_all_urls and not args.no_resolve_urls,
        allow_missing_douyin_urls=args.allow_partial_douyin_urls,
        min_url_resolve_ratio=url_ratio if not args.no_resolve_urls else None,
        allow_no_references=True,
      )

      if report.ok:
        break
      missing_urls = report.ref_count - report.url_count
      last_error = (
        f"质量未通过 score={report.score} "
        f"引用URL={report.url_count}/{report.ref_count}"
      )
      if (
        url_ratio is not None
        and report.ref_count
        and missing_urls > int(report.ref_count * url_ratio)
      ):
        last_error += "（超一半引用无真链接，数据不可用）"
      for line in report.lines():
        print(line)

    if record is None or report is None or not report.ok:
      failed_count += 1
      payload = {
        "keyword_id": signed.keyword_id,
        "prompt": signed.prompt,
        "intent_name": signed.intent_name,
        "error": last_error,
        "session_dir": getattr(record, "session_dir", ""),
        "at": datetime.now().isoformat(timespec="seconds"),
      }
      _append_failure(args.failures_file, payload)
      log_error(f"[抽检] 失败 {signed.keyword_id}: {last_error}")
      continue

    row = qa_record_to_spot_check_row(
      signed,
      answer_body=record.answer_body,
      thinking=record.thinking,
      thinking_references=record.thinking_references,
      quality_report=report,
      meta=meta,
      captured_at=datetime.now(),
    )

    if row.thinking_chars < 20 and row.citation_count > 0:
      log_warning(f"[抽检] 思考字数偏短 ({row.thinking_chars})：{signed.keyword_id}")

    append_csv_row(args.out_csv, row)
    completed_sessions[signed.keyword_id] = record.session_dir
    save_batch_meta(args.state_file, meta, completed_sessions)

    success_count += 1
    log_info(
      f"[抽检] 完成 {idx}/{len(todo)} "
      f"质量={row.quality_grade} 引用={row.citation_count} "
      f"session={record.session_dir}"
    )

  log_info(f"抽检结束：成功 {success_count}，失败 {failed_count}")
  if failed_count and args.strict:
    return 2
  return 0 if failed_count == 0 else 1


if __name__ == "__main__":
  sys.exit(main())
