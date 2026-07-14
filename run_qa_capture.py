#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆包问答完整采集入口（独立于电商详情爬虫 run_flow_crawl.py）。

采集：问题、思考、引用链接、回答正文 + 分屏截图/长图 + hierarchy 兜底，
落盘 logs/qa_capture/<日期>/<时刻>/。

用法:
  python run_qa_capture.py
  python run_qa_capture.py --prompt "你的问题"
  python run_qa_capture.py --mode think
  python run_qa_capture.py --skip-send
  python run_qa_capture.py -s <adb_serial>
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.profile_loader import load_profile
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_quality import GOLDEN_MODE, GOLDEN_PROMPT, validate_qa_session
from app.utils.device import DeviceManager
from app.utils.utils import log_error, log_info


def main() -> int:
  parser = argparse.ArgumentParser(description="豆包问答完整采集")
  parser.add_argument(
    "--prompt",
    type=str,
    default="请简要介绍2026年旗舰手机选购要点，并列出参考来源",
    help="发送的提示词",
  )
  parser.add_argument(
    "--skip-send",
    action="store_true",
    help="跳过发送（当前聊天已有目标回复时）",
  )
  parser.add_argument(
    "--mode",
    choices=("fast", "think"),
    default="fast",
    help="对话模式：fast=快速（默认），think=思考（含思考引用）",
  )
  parser.add_argument(
    "--no-deep-think",
    action="store_true",
    help="（已弃用）等同 --mode fast",
  )
  parser.add_argument(
    "--no-resolve-urls",
    action="store_true",
    help="跳过解析引用真实 HTTP 链接（默认会解析）",
  )
  parser.add_argument(
    "--resolve-method",
    choices=("auto", "logcat", "dumpsys", "net"),
    default="logcat",
    help="引用 URL 解析方式：logcat=点击后快速抓 Intent（默认），auto=logcat+dumpsys，net=mitm 零点击",
  )
  parser.add_argument(
    "--net-dump-dir",
    default="",
    help="--resolve-method net 时 mitm addon 落盘目录（默认 logs/qa_capture_net）",
  )
  parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
  parser.add_argument(
    "--device-profile",
    default=None,
    help="手动指定设备 profile（一般自动识别）",
  )
  parser.add_argument(
    "--out-dir",
    default="logs",
    help="产出根目录（默认 logs）",
  )
  parser.add_argument(
    "--project",
    default="",
    help="项目隔离目录名（logs/qa_capture/<project>/）",
  )
  parser.add_argument("--sms-token", default=None, help="SMS API Token")
  parser.add_argument("--sms-device-id", default=None, help="SMS 设备标识")
  parser.add_argument(
    "--strict",
    action="store_true",
    help="质量未达标时返回非 0（正文/引用/URL/截图不全）",
  )
  args = parser.parse_args()

  mode = "fast" if args.no_deep_think else args.mode

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device_name=args.device_profile, device=device)

  capturer = DoubaoQaCapture(
    device,
    output_dir=args.out_dir,
    profile=profile,
    project_slug=args.project,
  )
  record = capturer.run(
    prompt=args.prompt,
    skip_send=args.skip_send,
    mode=mode,
    resolve_urls=not args.no_resolve_urls,
    resolve_method=args.resolve_method,
    net_dump_dir=args.net_dump_dir,
    sms_token=args.sms_token or "",
    sms_device_id=args.sms_device_id or "",
  )

  if record.answer_body or record.raw_texts:
    log_info(
      f"问答采集完成：正文 {len(record.answer_body)} 字，"
      f"引用 {len(record.citations)} 条，"
      f"思考引用 {len(record.thinking_references)} 条，"
      f"截图 {len(record.screenshots)} 张"
    )
    report = validate_qa_session(
      session_dir=record.session_dir,
      answer_body=record.answer_body,
      thinking=record.thinking,
      thinking_references=record.thinking_references,
      screenshots=record.screenshots,
      stitched_screenshot=record.stitched_screenshot,
      mode=record.mode,
    )
    for line in report.lines():
      print(line)
    if not report.ok:
      print(
        f"[质量] 推荐黄金路径: --mode {GOLDEN_MODE} "
        f'--prompt "{GOLDEN_PROMPT}"'
      )
    if args.strict and not report.ok:
      return 2
    return 0

  log_error("未获取到有效问答数据")
  return 1


if __name__ == "__main__":
  sys.exit(main())
