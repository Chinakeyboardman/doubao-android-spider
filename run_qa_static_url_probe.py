#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Static URL Probe 实验：验证豆包思考引用静态 dump 无法获取 URL。

复用 QA 采集流程走到「思考引用已展开可见」，在点击前做纯静态 dump，
再点一条引用做正向对照，产出可复现的对照数据。

用法:
  python run_qa_static_url_probe.py
  python run_qa_static_url_probe.py -s <adb_serial>
  python run_qa_static_url_probe.py --skip-send   # 当前屏已有目标回复时
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.profile_loader import load_profile
from app.modules.navigator import Navigator
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_hierarchy import REFERENCE_CONTENT_RID
from app.modules.qa_quality import GOLDEN_MODE, GOLDEN_PROMPT
from app.modules.qa_reference_urls import (
  _adb_dumpsys,
  _click_citation,
  _device_serial,
  _ensure_citation_visible,
  extract_urls_from_dumpsys_text,
  extract_urls_from_logcat_text,
  poll_logcat_for_url,
)
from app.utils.device import DeviceManager
from app.utils.utils import build_session_dir, log_error, log_info
from capture.utils.capture_logcat import clear_logcat, dump_logcat_tail

PROBE_SCRIPT = "qa_static_url_probe"
_REF_CONTENT_MARKER = "tv_reference_content"


def _write_text(path: str, text: str) -> None:
  with open(path, "w", encoding="utf-8") as f:
    f.write(text)


def _count_ref_nodes(xml_text: str) -> int:
  """统计 hierarchy 中带标题的 tv_reference_content 节点数。"""
  if not xml_text:
    return 0
  try:
    root = ET.fromstring(xml_text)
    count = 0
    for el in root.iter("node"):
      rid = el.attrib.get("resource-id", "")
      if REFERENCE_CONTENT_RID in rid or _REF_CONTENT_MARKER in rid:
        if (el.attrib.get("text") or "").strip():
          count += 1
    return count
  except ET.ParseError:
    return len(re.findall(r"tv_reference_content", xml_text))


def _scan_urls(text: str) -> list[str]:
  return extract_urls_from_dumpsys_text(text)


def _reach_thinking_refs(capturer: DoubaoQaCapture, session_dir: str, prompt: str):
  """复用 QA 采集流程走到引用可见。"""
  if not capturer._crawler.start_app():
    raise RuntimeError("启动豆包失败")
  if not capturer._crawler.handle_login_if_needed(sms_token="", device_id=""):
    raise RuntimeError("登录失败")

  if not capturer._open_new_conversation():
    print("[探针] 创建新对话失败，继续在当前会话")
  time.sleep(1.2)

  for attempt in range(3):
    if capturer._select_mode(GOLDEN_MODE):
      break
    if attempt < 2:
      print(f"[探针] 模式切换重试 {attempt + 2}/3...")
      time.sleep(0.8)
  else:
    print(f"[探针] 警告: 未能切换到 {GOLDEN_MODE} 模式")

  if not capturer._crawler.send_message(prompt):
    raise RuntimeError("发送提示词失败")
  if not capturer._crawler.wait_reply_done(timeout=180):
    print("[探针] 等待回复超时，继续尝试采集")

  time.sleep(1.0)
  capturer._ensure_chat()
  capturer._dismiss_overlays()

  thinking_panel, _shot_paths, _stitched = capturer._sweep_expand_and_capture(session_dir)
  return thinking_panel


def phase1_static_probe(device, serial: str | None, session_dir: str) -> dict:
  """Phase1: 静态探针，不点击。"""
  print("\n[Phase1] 静态探针（不点击）...")

  xml_text = device.dump_hierarchy(compressed=False) or ""
  _write_text(os.path.join(session_dir, "hierarchy.xml"), xml_text)
  ref_count = _count_ref_nodes(xml_text)
  hierarchy_urls = _scan_urls(xml_text)
  print(f"  hierarchy: 引用节点 {ref_count}，URL 命中 {len(hierarchy_urls)}")

  window_text = _adb_dumpsys(serial, "window", "windows")
  _write_text(os.path.join(session_dir, "dumpsys_window.txt"), window_text)
  window_urls = _scan_urls(window_text)
  print(f"  dumpsys window: URL 命中 {len(window_urls)}")

  activity_pre_text = _adb_dumpsys(serial, "activity", "top")
  _write_text(os.path.join(session_dir, "dumpsys_activity_pre.txt"), activity_pre_text)
  activity_pre_urls = _scan_urls(activity_pre_text)
  print(f"  dumpsys activity (pre): URL 命中 {len(activity_pre_urls)}")

  return {
    "ref_count": ref_count,
    "static_hierarchy_url_hits": len(hierarchy_urls),
    "static_hierarchy_urls": hierarchy_urls,
    "dumpsys_window_url_hits": len(window_urls),
    "dumpsys_window_urls": window_urls,
    "dumpsys_activity_pre_url_hits": len(activity_pre_urls),
    "dumpsys_activity_pre_urls": activity_pre_urls,
  }


def phase2_positive_control(
  device,
  serial: str | None,
  profile,
  session_dir: str,
  thinking_panel,
) -> dict:
  """Phase2: 点一条引用做正向对照。"""
  print("\n[Phase2] 正向对照（点击 1 条引用）...")

  refs = list(thinking_panel.references) if thinking_panel else []
  if not refs:
    print("[探针] 无思考引用，跳过点击对照")
    return {
      "clicked_ref_title": "",
      "clicked_ref_index": 0,
      "post_click_url": "",
      "post_click_logcat_url_hits": 0,
      "post_click_dumpsys_url_hits": 0,
      "skipped": True,
      "skip_reason": "no_refs",
    }

  # 优先点序号最小的引用（通常在列表顶部、最易可见）
  citation = min(refs, key=lambda r: r.ref_index if r.ref_index > 0 else 9999)
  clicked_title = citation.title or ""
  clicked_index = citation.ref_index
  nav = Navigator(device)

  if not _ensure_citation_visible(device, citation, profile):
    print("[探针] 首条引用不可见，跳过点击")
    return {
      "clicked_ref_title": clicked_title,
      "clicked_ref_index": clicked_index,
      "post_click_url": "",
      "post_click_logcat_url_hits": 0,
      "post_click_dumpsys_url_hits": 0,
      "skipped": True,
      "skip_reason": "citation_not_visible",
    }

  clear_logcat(serial=serial)
  time.sleep(0.15)

  if not _click_citation(device, citation, profile=profile):
    print("[探针] 点击引用失败")
    return {
      "clicked_ref_title": clicked_title,
      "clicked_ref_index": clicked_index,
      "post_click_url": "",
      "post_click_logcat_url_hits": 0,
      "post_click_dumpsys_url_hits": 0,
      "skipped": True,
      "skip_reason": "click_failed",
    }

  post_url = poll_logcat_for_url(serial=serial, timeout_s=1.5, poll_interval_s=0.2)

  logcat_text = dump_logcat_tail(serial=serial, count=120)
  _write_text(os.path.join(session_dir, "logcat_post_click.txt"), logcat_text)
  logcat_urls = extract_urls_from_logcat_text(logcat_text)

  activity_post_text = _adb_dumpsys(serial, "activity", "top")
  _write_text(os.path.join(session_dir, "dumpsys_activity_post.txt"), activity_post_text)
  dumpsys_post_urls = _scan_urls(activity_post_text)

  if not post_url and logcat_urls:
    post_url = logcat_urls[-1]
  if not post_url and dumpsys_post_urls:
    post_url = dumpsys_post_urls[-1]

  print(f"  点击后 logcat URL 命中 {len(logcat_urls)}")
  print(f"  点击后 dumpsys URL 命中 {len(dumpsys_post_urls)}")
  if post_url:
    print(f"  解析 URL: {post_url[:96]}")

  nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)

  return {
    "clicked_ref_title": clicked_title,
    "clicked_ref_index": clicked_index,
    "post_click_url": post_url,
    "post_click_logcat_url_hits": len(logcat_urls),
    "post_click_logcat_urls": logcat_urls,
    "post_click_dumpsys_url_hits": len(dumpsys_post_urls),
    "post_click_dumpsys_urls": dumpsys_post_urls,
    "skipped": False,
  }


def _print_report_table(static: dict, click: dict) -> None:
  print("\n" + "=" * 60)
  print("Static URL Probe 对照表")
  print("=" * 60)
  print(f"  提示词: {GOLDEN_PROMPT}")
  print(f"  模式: {GOLDEN_MODE}")
  print(f"  引用条数 (hierarchy): {static.get('ref_count', 0)}")
  print("-" * 60)
  print(f"  {'探针':<28} {'URL命中':>8}  说明")
  print(
    f"  {'hierarchy (uiautomator)':<28} "
    f"{static.get('static_hierarchy_url_hits', 0):>8}  静态，不点击"
  )
  print(
    f"  {'dumpsys window':<28} "
    f"{static.get('dumpsys_window_url_hits', 0):>8}  静态，不点击"
  )
  print(
    f"  {'dumpsys activity (pre)':<28} "
    f"{static.get('dumpsys_activity_pre_url_hits', 0):>8}  点击前基线"
  )
  print("-" * 60)
  if click.get("skipped"):
    print(f"  点击对照: 已跳过 ({click.get('skip_reason', 'no_refs')})")
  else:
    print(
      f"  {'点击后 logcat':<28} "
      f"{click.get('post_click_logcat_url_hits', 0):>8}  正向对照"
    )
    print(
      f"  {'点击后 dumpsys activity':<28} "
      f"{click.get('post_click_dumpsys_url_hits', 0):>8}  正向对照"
    )
    title_preview = (click.get("clicked_ref_title") or "")[:60]
    print(f"  点击引用: #{click.get('clicked_ref_index', '?')} {title_preview}")
    print(f"  解析 URL: {click.get('post_click_url') or '(无)'}")
  print("=" * 60)

  static_zero = (
    static.get("static_hierarchy_url_hits", 0) == 0
    and static.get("dumpsys_window_url_hits", 0) == 0
    and static.get("dumpsys_activity_pre_url_hits", 0) == 0
  )
  click_hit = bool(click.get("post_click_url"))
  if static_zero and click_hit:
    print("结论: 静态 dump 拿不到引用 URL；点击后 logcat/dumpsys 可拿到。")
  elif static_zero and not click_hit and not click.get("skipped"):
    print("结论: 静态 dump 无 URL（符合预期）；点击对照未命中，需检查设备/引用类型。")
  elif not static_zero:
    print("结论: 静态 dump 出现了 URL，与预期不符，请检查 hierarchy/dumpsys 输出。")
  else:
    print("结论: 静态无 URL；点击对照已跳过。")


def main() -> int:
  parser = argparse.ArgumentParser(
    description="Static URL Probe：验证静态 dump 无法获取引用 URL",
  )
  parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
  parser.add_argument("--out-dir", default="logs", help="产出根目录")
  parser.add_argument(
    "--skip-send",
    action="store_true",
    help="跳过发送（当前聊天已有目标回复时）",
  )
  args = parser.parse_args()

  prompt = GOLDEN_PROMPT
  session_dir = build_session_dir(args.out_dir, PROBE_SCRIPT)
  log_info(f"实验目录: {session_dir}")
  log_info(f"提示词: {prompt} | 模式: {GOLDEN_MODE}")

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device=device)
  capturer = DoubaoQaCapture(device, output_dir=args.out_dir, profile=profile)
  serial = _device_serial(device)

  try:
    if args.skip_send:
      if not capturer._crawler.start_app():
        raise RuntimeError("启动豆包失败")
      capturer._ensure_chat()
      capturer._dismiss_overlays()
      thinking_panel, _, _ = capturer._sweep_expand_and_capture(session_dir)
    else:
      thinking_panel = _reach_thinking_refs(capturer, session_dir, prompt)

    ref_panel_count = len(thinking_panel.references) if thinking_panel else 0
    print(f"[探针] 思考引用条数 (panel): {ref_panel_count}")

    # sweep 结束在底部，引用列表可能不在屏上；回顶后再做静态探针与点击对照
    capturer._scroll_message_to_top()
    time.sleep(0.5)
    if not capturer._thinking_panel_on_screen():
      capturer._scroll_to_thinking_panel()
      time.sleep(0.4)
    capturer._ensure_thinking_header_expanded()
    time.sleep(0.35)
    capturer._expand_visible_search_groups(set())
    time.sleep(0.3)

    static_result = phase1_static_probe(device, serial, session_dir)
    click_result = phase2_positive_control(
      device, serial, profile, session_dir, thinking_panel,
    )

    report = {
      "prompt": prompt,
      "mode": GOLDEN_MODE,
      "session_dir": session_dir,
      "panel_ref_count": ref_panel_count,
      **static_result,
      **click_result,
    }
    report_path = os.path.join(session_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
      json.dump(report, f, ensure_ascii=False, indent=2)

    _print_report_table(static_result, click_result)
    log_info(f"报告: {report_path}")
    return 0
  except Exception as exc:
    log_error(f"实验失败: {exc}")
    return 1


if __name__ == "__main__":
  sys.exit(main())
