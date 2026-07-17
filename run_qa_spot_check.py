#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆包 APP 抽检明细批量采集：读签单提示词 CSV，逐条 QA 采集并写入抽检 CSV。

用法:
  python run_qa_spot_check.py --pilot 10 --strict
  python run_qa_spot_check.py --resume --strict
  python run_qa_spot_check.py --resume --claims-dir var/.../claims --worker-id <serial>
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.profile_loader import load_profile
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_quality import DEFAULT_MIN_URL_RESOLVE_RATIO, validate_qa_session
from app.modules.qa_spot_check_export import (
  SignedPromptRow,
  SpotCheckBatchMeta,
  append_csv_row,
  build_detail_id_index,
  dedupe_signed_prompts,
  load_batch_meta,
  load_completed_keyword_ids,
  load_completed_sessions,
  load_signed_prompts,
  load_failure_counts,
  purge_incomplete_spot_check_rows,
  qa_record_to_spot_check_row,
  save_batch_meta,
  select_pilot_rows,
)
from app.modules.spot_check_claims import (
  append_csv_row_locked,
  claim_task,
  load_completed_keyword_ids_locked,
  prune_claims_for_completed,
  release_task,
)
from app.utils.device import DeviceManager
from app.utils.utils import log_error, log_info, log_warning


DEFAULT_PROMPTS_FILE = "var/vivo-x-fold6/签单提示词导出_20260710_183049.csv"
DEFAULT_OUT_CSV = "var/vivo-x-fold6/抽检明细_20260710_APP采集.csv"
DEFAULT_STATE = "var/vivo-x-fold6/spot_check_state.json"
DEFAULT_FAILURES = "var/vivo-x-fold6/spot_check_failures.jsonl"
DEFAULT_PROJECT = ""


_active_claim: tuple[str, str, str] | None = None


def _set_active_claim(claims_dir: str, keyword_id: str, worker_id: str) -> None:
  global _active_claim
  _active_claim = (claims_dir, keyword_id, worker_id)


def _clear_active_claim() -> None:
  global _active_claim
  _active_claim = None


def _release_active_claim() -> None:
  global _active_claim
  if _active_claim is None:
    return
  claims_dir, keyword_id, worker_id = _active_claim
  release_task(claims_dir, keyword_id, worker_id=worker_id)
  _active_claim = None


def _install_claim_cleanup() -> None:
  atexit.register(_release_active_claim)

  def _on_signal(signum: int, _frame: object) -> None:
    _release_active_claim()
    raise SystemExit(128 + signum)

  for sig in (signal.SIGTERM, signal.SIGINT):
    try:
      signal.signal(sig, _on_signal)
    except (ValueError, OSError):
      pass


def _append_failure(path: str, payload: dict) -> None:
  os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
  with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _pick_rows(
  all_rows: list[SignedPromptRow],
  *,
  pilot: int,
  limit: int,
  resume: bool,
  completed: set[str],
) -> list[SignedPromptRow]:
  if pilot > 0:
    candidates = select_pilot_rows(all_rows, pilot)
  else:
    candidates = list(all_rows)

  if resume:
    candidates = [r for r in candidates if r.keyword_id not in completed]

  if limit > 0:
    candidates = candidates[:limit]
  return candidates


def _merged_completed_ids(
  *,
  csv_path: str,
  state_completed: dict[str, str],
  resume: bool,
) -> set[str]:
  if not resume:
    return set()
  done = load_completed_keyword_ids_locked(csv_path)
  done.update(state_completed.keys())
  return done


def _ordered_pending(
  candidates: list[SignedPromptRow],
  *,
  all_rows: list[SignedPromptRow],
  completed_ids: set[str],
  failure_counts: dict[str, int],
) -> list[SignedPromptRow]:
  """未完成队列：失败少的优先；同失败数时优先「意图内完成数少」的新板块。"""
  order = {row.keyword_id: idx for idx, row in enumerate(all_rows)}
  intent_done: dict[str, int] = {}
  for row in all_rows:
    if row.keyword_id in completed_ids:
      intent_done[row.intent_name] = intent_done.get(row.intent_name, 0) + 1
  pending = [r for r in candidates if r.keyword_id not in completed_ids]
  pending.sort(
    key=lambda r: (
      failure_counts.get(r.keyword_id, 0),
      intent_done.get(r.intent_name, 0),
      order.get(r.keyword_id, 9999),
    ),
  )
  return pending


def _process_one(
  signed: SignedPromptRow,
  *,
  capturer: DoubaoQaCapture,
  args: argparse.Namespace,
  meta: SpotCheckBatchMeta,
  detail_id: int | None,
) -> tuple[bool, str, object | None, object | None]:
  """处理单条签单，返回 (成功, 错误信息, record, report)。"""
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

    # 误选专家/专业版导致额度提示：不算有效回答，强制重试并再切模式
    body = record.answer_body or ""
    if any(
      m in body
      for m in (
        "免费额度已用完",
        "专家模式额度",
        "专业版功能",
        "开通豆包专业版",
      )
    ):
      last_error = "专家/专业版额度提示（疑似误选专家模式）"
      log_warning(f"[抽检] {last_error}，将重试：{signed.keyword_id}")
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
    return False, last_error, record, report

  row = qa_record_to_spot_check_row(
    signed,
    answer_body=record.answer_body,
    thinking=record.thinking,
    thinking_references=record.thinking_references,
    quality_report=report,
    meta=meta,
    captured_at=datetime.now(),
    detail_id=detail_id,
  )

  if row.thinking_chars < 20 and row.citation_count > 0:
    log_warning(f"[抽检] 思考字数偏短 ({row.thinking_chars})：{signed.keyword_id}")

  return True, "", record, row


def _run_claim_loop(
  candidates: list[SignedPromptRow],
  *,
  all_rows: list[SignedPromptRow],
  capturer: DoubaoQaCapture,
  args: argparse.Namespace,
  meta: SpotCheckBatchMeta,
  completed_sessions: dict[str, str],
  detail_index: dict[str, int],
) -> tuple[int, int]:
  """多机认领模式主循环。"""
  claims_dir = args.claims_dir
  worker_id = args.worker_id or args.serial or "worker"
  success_count = 0
  failed_count = 0
  processed_total = 0

  if args.resume:
    done_now = _merged_completed_ids(
      csv_path=args.out_csv,
      state_completed=completed_sessions,
      resume=True,
    )
    pruned = prune_claims_for_completed(claims_dir, done_now)
    if pruned:
      log_info(
        f"[抽检] 已清理 {len(pruned)} 个已完成任务上的陈旧 claim"
      )

  while True:
    completed_ids = _merged_completed_ids(
      csv_path=args.out_csv,
      state_completed=completed_sessions,
      resume=args.resume,
    )
    failure_counts = load_failure_counts(args.failures_file)
    pending = _ordered_pending(
      candidates,
      all_rows=all_rows,
      completed_ids=completed_ids,
      failure_counts=failure_counts,
    )
    if not pending:
      break

    claimed_any = False
    for signed in pending:
      if signed.keyword_id in completed_ids:
        continue

      if not claim_task(
        claims_dir,
        signed.keyword_id,
        worker_id=worker_id,
        stale_sec=args.claim_stale_sec,
      ):
        continue

      # 认领成功后再读一次 CSV，避免他机刚落盘仍开跑
      completed_ids = _merged_completed_ids(
        csv_path=args.out_csv,
        state_completed=completed_sessions,
        resume=args.resume,
      )
      if signed.keyword_id in completed_ids:
        release_task(claims_dir, signed.keyword_id, worker_id=worker_id)
        log_warning(
          f"[抽检] 跳过已完成（认领竞态）: {signed.keyword_id}"
        )
        continue

      claimed_any = True
      processed_total += 1
      _set_active_claim(claims_dir, signed.keyword_id, worker_id)
      log_info(
        f"[抽检] 认领 {processed_total} "
        f"意图={signed.intent_name} 提示词={signed.prompt[:40]}..."
        f"（失败{failure_counts.get(signed.keyword_id, 0)}次）"
      )

      try:
        ok, last_error, record, payload = _process_one(
          signed,
          capturer=capturer,
          args=args,
          meta=meta,
          detail_id=detail_index.get(signed.keyword_id),
        )
      finally:
        _clear_active_claim()

      if ok:
        row = payload
        if append_csv_row_locked(args.out_csv, row):
          completed_sessions[signed.keyword_id] = record.session_dir
          save_batch_meta(args.state_file, meta, completed_sessions)
          success_count += 1
          log_info(
            f"[抽检] 完成 SN={worker_id} "
            f"质量={row.quality_grade} 引用={row.citation_count} "
            f"session={record.session_dir}"
          )
        else:
          log_warning(
            f"[抽检] 跳过重复落盘（CSV 已有）: {signed.keyword_id}"
          )
        release_task(claims_dir, signed.keyword_id, worker_id=worker_id)
      else:
        release_task(claims_dir, signed.keyword_id, worker_id=worker_id)
        failed_count += 1
        _append_failure(
          args.failures_file,
          {
            "keyword_id": signed.keyword_id,
            "prompt": signed.prompt,
            "intent_name": signed.intent_name,
            "error": last_error,
            "session_dir": getattr(record, "session_dir", ""),
            "at": datetime.now().isoformat(timespec="seconds"),
          },
        )
        log_error(f"[抽检] 失败 {signed.keyword_id}: {last_error}")

    if not claimed_any:
      break

  return success_count, failed_count


def _run_serial_loop(
  todo: list[SignedPromptRow],
  *,
  capturer: DoubaoQaCapture,
  args: argparse.Namespace,
  meta: SpotCheckBatchMeta,
  completed_sessions: dict[str, str],
) -> tuple[int, int]:
  """单机顺序模式（向后兼容）。"""
  success_count = 0
  failed_count = 0

  for idx, signed in enumerate(todo, start=1):
    log_info(
      f"[抽检] 开始 {idx}/{len(todo)} "
      f"意图={signed.intent_name} 提示词={signed.prompt[:40]}..."
    )

    ok, last_error, record, payload = _process_one(
      signed,
      capturer=capturer,
      args=args,
      meta=meta,
      detail_id=None,
    )

    if not ok:
      failed_count += 1
      _append_failure(
        args.failures_file,
        {
          "keyword_id": signed.keyword_id,
          "prompt": signed.prompt,
          "intent_name": signed.intent_name,
          "error": last_error,
          "session_dir": getattr(record, "session_dir", ""),
          "at": datetime.now().isoformat(timespec="seconds"),
        },
      )
      log_error(f"[抽检] 失败 {signed.keyword_id}: {last_error}")
      continue

    row = payload
    append_csv_row(args.out_csv, row)
    completed_sessions[signed.keyword_id] = record.session_dir
    save_batch_meta(args.state_file, meta, completed_sessions)
    success_count += 1
    log_info(
      f"[抽检] 完成 {idx}/{len(todo)} "
      f"质量={row.quality_grade} 引用={row.citation_count} "
      f"session={record.session_dir}"
    )

  return success_count, failed_count


def main() -> int:
  from app.utils.utils import install_op_logging, set_op_log_device

  install_op_logging()

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
    help="抖音/网页链接统一计入比例；全空视为可接受，有部分则按 --min-url-resolve-ratio 判失败",
  )
  parser.add_argument(
    "--purge-incomplete",
    action="store_true",
    help="启动前删除 CSV 中引用/URL 不达标的行（保留 logs 会话）",
  )
  parser.add_argument(
    "--claims-dir",
    default="",
    help="多机协作认领目录（设则启用原子认领；默认关闭保持单机行为）",
  )
  parser.add_argument(
    "--worker-id",
    default="",
    help="认领 worker 标识（默认 adb serial）",
  )
  parser.add_argument(
    "--claim-stale-sec",
    type=float,
    default=3600.0,
    help="认领超时秒数，超时且属主进程已死则可接管",
  )
  args = parser.parse_args()

  if not args.worker_id:
    args.worker_id = args.serial or "worker"

  set_op_log_device(args.serial or args.worker_id)

  if not args.out_dir.strip():
    args.out_dir = os.path.dirname(os.path.abspath(args.state_file)) or "logs"

  use_claims = bool(args.claims_dir.strip()) or (
    os.environ.get("SPOT_CHECK_USE_CLAIMS", "").lower() in ("1", "true", "yes")
  )
  if use_claims and not args.claims_dir.strip():
    state_dir = os.path.dirname(os.path.abspath(args.state_file)) or "."
    args.claims_dir = os.path.join(state_dir, "claims")

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

  candidates = _pick_rows(
    all_rows,
    pilot=args.pilot,
    limit=args.limit,
    resume=args.resume,
    completed=completed_ids,
  )
  if not candidates:
    log_info("无待处理提示词（已全部完成或未选中）")
    return 0

  log_info(
    f"待抽检 {len(candidates)} 条（签单共 {len(all_rows)} 条）"
    + (f"，认领模式 SN={args.worker_id}" if use_claims else "")
  )

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device_name=args.device_profile, device=device)
  capturer = DoubaoQaCapture(
    device,
    output_dir=args.out_dir,
    profile=profile,
    project_slug=args.project,
  )

  if use_claims:
    _install_claim_cleanup()
    detail_index = build_detail_id_index(all_rows)
    success_count, failed_count = _run_claim_loop(
      candidates,
      all_rows=all_rows,
      capturer=capturer,
      args=args,
      meta=meta,
      completed_sessions=completed_sessions,
      detail_index=detail_index,
    )
  else:
    success_count, failed_count = _run_serial_loop(
      candidates,
      capturer=capturer,
      args=args,
      meta=meta,
      completed_sessions=completed_sessions,
    )

  log_info(f"抽检结束：成功 {success_count}，失败 {failed_count}")
  if failed_count and args.strict:
    return 2
  return 0 if failed_count == 0 else 1


if __name__ == "__main__":
  sys.exit(main())
