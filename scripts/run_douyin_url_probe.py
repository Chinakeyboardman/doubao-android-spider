#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音链接专项探针（真机用例，非 pytest）。

在豆包 App 内对同一组思考引用，依次跑三种取链策略并对比结果。
用于新机适配（如 vivo V2301A）验证「必须拿到 iesdouyin 链接」。

用例一览
--------
| 用例 | 函数 | 说明 | 通过标准 |
|------|------|------|----------|
| A | _strategy_wait_accept_collect | 单条点击 → 等 AppJump → 进抖音 feed → logcat 抓 aweme id | resolved_url 含 iesdouyin |
| B | _strategy_batch | 生产同款 try_batch_resolve_douyin（首条进 feed 批量回填） | filled_count ≥ 抖音引用数 |
| C | _strategy_per_click_dumpsys | 单条点击 + logcat/dumpsys，不滑 feed | resolved_url 含 iesdouyin |
| D | _strategy_deeplink_device_id | 深链 snssdk1128/1180 + android_id，不滑 feed | resolved_url 含 iesdouyin |

产出目录
--------
var/新设备适配/vivo_v2301a/douyin_url_probe/<timestamp>/
  - probe_report.json       汇总
  - strategy_A/             用例 A 截图、hierarchy、logcat
  - strategy_B/             用例 B
  - strategy_C/             用例 C
  - strategy_D/             用例 D（深链 device_id）
  - qa_session/             可选：完整问答采集目录（非 --skip-send 时）

运行
----
  .venv/bin/python scripts/run_douyin_url_probe.py -s 10ADBY1Z7C0042Z
  .venv/bin/python scripts/run_douyin_url_probe.py -s 10ADBY1Z7C0042Z --skip-send
  .venv/bin/python scripts/run_douyin_url_probe.py --prompt "雅诗兰黛智妍面霜值得买吗？"

注意
----
- 策略执行前会 _prepare_panel 重新展开思考引用列表（采集后面板常收起）。
- 若 logcat 无 aweme id：抖音 App 需用与豆包相同手机号登录（SMS 同池）。
- 退出码 0=至少一条策略成功；2=全部失败。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.profile_loader import load_profile
from app.modules.navigator import Navigator
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_hierarchy import Citation
from app.modules.douyin_handoff import (
  get_android_device_id,
  resolve_via_aweme_deeplink,
)
from app.modules.qa_reference_urls import (
  LogcatStream,
  _click_citation,
  _device_serial,
  _ensure_citation_visible,
  collect_aweme_ids_after_open,
  extract_aweme_ids_ordered,
  extract_urls_from_logcat_text,
  is_likely_douyin_citation,
  poll_logcat_for_url,
  prepare_citations_for_url_resolve,
  resolve_url_via_dumpsys,
  try_batch_resolve_douyin,
)
from app.utils.device import DeviceManager
from capture.utils.capture_logcat import dump_logcat_tail

DEFAULT_PROMPT = "雅诗兰黛智妍面霜值得买吗？"
PROBE_ROOT = Path("var/新设备适配/vivo_v2301a/douyin_url_probe")


def _snap(device, nav, out: Path, tag: str) -> dict:
  out.mkdir(parents=True, exist_ok=True)
  png = out / f"{tag}.png"
  xml = out / f"{tag}.xml"
  try:
    device.screenshot(str(png))
  except Exception:
    pass
  try:
    xml.write_text(device.dump_hierarchy(compressed=False) or "", encoding="utf-8")
  except Exception:
    pass
  pg, cur = nav.current_page()
  info = {
    "tag": tag,
    "page": pg.name,
    "package": cur.get("package", ""),
    "activity": cur.get("activity", ""),
    "app_jump_prompt": nav.is_app_jump_prompt(),
    "aweme_foreground": nav.is_aweme_foreground(),
  }
  (out / f"{tag}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
  return info


def _reach_refs(capturer: DoubaoQaCapture, prompt: str, skip_send: bool) -> list[Citation]:
  """准备阶段：启动豆包、登录，采集思考引用（不解析 URL）。"""
  record = capturer.run(
    prompt=prompt,
    skip_send=skip_send,
    resolve_urls=False,
    sms_token=os.environ.get("SMS_API_TOKEN", ""),
    sms_device_id=os.environ.get("SMS_DEVICE_ID", "doubao-crawler-vivo-v2301"),
  )
  refs = list(record.thinking_references or [])
  if not refs:
    raise RuntimeError("无思考引用，请先 --skip-send 或换提示词")
  return refs


def _prepare_panel(capturer: DoubaoQaCapture, refs: list[Citation], profile) -> None:
  """采集结束后思考面板常收起，策略执行前重新展开并刷新 bounds。"""
  capturer._scroll_message_to_top()
  time.sleep(0.35)
  capturer._scroll_to_thinking_panel()
  capturer._ensure_thinking_header_expanded()
  capturer._expand_visible_search_groups(set())
  time.sleep(0.35)
  prepare_citations_for_url_resolve(capturer.d, refs, profile=profile)


def _first_douyin(citations: list[Citation]) -> Citation | None:
  for c in citations:
    if is_likely_douyin_citation(c):
      return c
  return None


def _strategy_wait_accept_collect(device, nav, cite: Citation, profile, out: Path, serial: str) -> dict:
  """用例 A：wait_accept_collect — 单条抖音引用完整取链路径。

  步骤：
    1. 点击首条抖音类引用
    2. wait_and_accept_app_jump（vivo「是否打开抖音」）
    3. wait_for_aweme_foreground + 权限允许
    4. 轻滑 feed，collect_aweme_ids_after_open
    5. recover 回聊天页

  通过：resolved_url 非空（优先 aweme id 拼 iesdouyin）。
  产出：strategy_A/A01~A04 截图与 activity.json、A_logcat_tail.txt
  """
  stream = LogcatStream(serial=serial)
  stream.start(settle_s=0.2)
  stream.mark()
  _ensure_citation_visible(device, cite, profile)
  ok_click = _click_citation(device, cite, profile=profile)
  snap1 = _snap(device, nav, out, "A01_after_click")
  accepted = nav.wait_and_accept_app_jump(timeout=8.0)
  snap2 = _snap(device, nav, out, "A02_after_accept")
  in_aweme = nav.wait_for_aweme_foreground(timeout=12.0)
  snap3 = _snap(device, nav, out, "A03_aweme_or_stuck")
  if in_aweme:
    try:
      w, h = device.window_size()
      device.swipe(int(w * 0.5), int(h * 0.72), int(w * 0.5), int(h * 0.38), 0.35)
      time.sleep(1.0)
    except Exception:
      pass
  ids = collect_aweme_ids_after_open(
    stream=stream, serial=serial, timeout_s=18.0, expected_count=1,
  )
  tail = dump_logcat_tail(serial=serial, count=400)
  (out / "A_logcat_tail.txt").write_text(tail[-12000:], encoding="utf-8")
  urls = extract_urls_from_logcat_text(tail)
  stream.stop()
  nav.recover_from_external_douyin()
  nav.safe_back_to_chat(max_backs=5)
  _snap(device, nav, out, "A04_back_chat")
  url = ""
  if ids:
    url = f"https://www.iesdouyin.com/share/video/{ids[0]}"
  elif urls:
    url = next((u for u in urls if "iesdouyin" in u), urls[0] if urls else "")
  return {
    "strategy": "wait_accept_collect",
    "click_ok": ok_click,
    "accepted_prompt": accepted,
    "aweme_foreground": in_aweme,
    "aweme_ids": ids,
    "logcat_urls_sample": urls[:5],
    "resolved_url": url,
    "ok": bool(url),
    "snaps": [snap1, snap2, snap3],
  }


def _strategy_batch(device, nav, citations: list[Citation], profile, out: Path, serial: str) -> dict:
  """用例 B：batch_resolve_douyin — 与 qa_reference_urls 生产批量逻辑一致。

  步骤：点开首条抖音引用进 feed，logcat 批量抓齐 aweme id 按序回填全部抖音引用。

  通过：try_batch_resolve_douyin 返回 True，或 filled_count > 0。
  产出：strategy_B/B_after_batch.*
  """
  copies = [
    Citation(
      title=c.title, url=c.url, source=c.source, desc=c.desc,
      resource_id=c.resource_id, bounds=list(c.bounds) if c.bounds else None,
      ref_index=c.ref_index, group=c.group,
    )
    for c in citations
  ]
  stream = LogcatStream(serial=serial)
  stream.start(settle_s=0.2)
  ok = try_batch_resolve_douyin(device, copies, nav=nav, profile=profile, stream=stream)
  stream.stop()
  filled = [
    {"ref_index": c.ref_index, "title": c.title[:60], "url": c.url}
    for c in copies if c.url
  ]
  _snap(device, nav, out, "B_after_batch")
  return {
    "strategy": "batch_resolve_douyin",
    "ok": ok,
    "filled_count": len(filled),
    "filled": filled[:8],
  }


def _strategy_per_click_dumpsys(device, nav, cite: Citation, profile, out: Path, serial: str) -> dict:
  """用例 C：per_click_dumpsys — 单条点击，不滑 feed，logcat 优先再 dumpsys。

  用于对比：跳转后是否无需进 feed 即可从 activity intent 拿到 URL。

  通过：resolved_url 含 iesdouyin。
  产出：strategy_C/C_after_single.*
  """
  stream = LogcatStream(serial=serial)
  stream.start(settle_s=0.2)
  stream.mark()
  _ensure_citation_visible(device, cite, profile)
  _click_citation(device, cite, profile=profile)
  nav.wait_and_accept_app_jump(timeout=6.0)
  time.sleep(2.0)
  url = poll_logcat_for_url(serial=serial, timeout_s=3.0)
  if not url:
    url = resolve_url_via_dumpsys(device, serial=serial, wait_s=2.0)
  stream.stop()
  nav.recover_from_external_douyin()
  nav.safe_back_to_chat(max_backs=5)
  _snap(device, nav, out, "C_after_single")
  return {
    "strategy": "per_click_dumpsys",
    "resolved_url": url,
    "ok": bool(url) and "iesdouyin" in (url or ""),
  }


def _strategy_deeplink_device_id(device, nav, cite: Citation, profile, out: Path, serial: str) -> dict:
  """用例 D：deeplink_device_id — snssdk1128/1180 + android_id 深链打开详情。

  步骤：
    1. 点击首条抖音类引用
    2. logcat 短轮询 aweme_id
    3. resolve_via_aweme_deeplink（不滑 feed）
    4. 温和 recover 回聊天页

  通过：resolved_url 含 iesdouyin。
  产出：strategy_D/D01~D03 截图、device_id.txt、scheme 命中
  """
  stream = LogcatStream(serial=serial)
  stream.start(settle_s=0.2)
  stream.mark()
  _ensure_citation_visible(device, cite, profile)
  ok_click = _click_citation(device, cite, profile=profile)
  snap1 = _snap(device, nav, out, "D01_after_click")
  device_id = get_android_device_id(serial)
  (out / "device_id.txt").write_text(device_id or "(empty)", encoding="utf-8")
  url = resolve_via_aweme_deeplink(
    device,
    nav,
    profile,
    serial=serial,
    stream=stream,
  )
  snap2 = _snap(device, nav, out, "D02_after_deeplink")
  tail = dump_logcat_tail(serial=serial, count=400)
  (out / "D_logcat_tail.txt").write_text(tail[-12000:], encoding="utf-8")
  ids = extract_aweme_ids_ordered(tail)
  schemes_hit: list[str] = []
  for scheme in profile.qa_douyin_deeplink_schemes or ("snssdk1128", "snssdk1180"):
    if f"{scheme}://aweme/detail/" in tail:
      schemes_hit.append(scheme)
  stream.stop()
  nav.recover_from_external_douyin(gentle=True)
  nav.safe_back_to_chat(max_backs=5)
  _snap(device, nav, out, "D03_back_chat")
  return {
    "strategy": "deeplink_device_id",
    "click_ok": ok_click,
    "device_id": device_id,
    "aweme_ids": ids[:3],
    "deeplink_schemes_hit": schemes_hit,
    "resolved_url": url,
    "ok": bool(url) and "iesdouyin" in (url or ""),
    "snaps": [snap1, snap2],
  }


def main() -> int:
  parser = argparse.ArgumentParser(description="抖音链接专项探针")
  parser.add_argument("-s", "--serial", default=os.environ.get("ADB_SERIAL", "10ADBY1Z7C0042Z"))
  parser.add_argument("--prompt", default=DEFAULT_PROMPT)
  parser.add_argument("--skip-send", action="store_true")
  args = parser.parse_args()

  ts = datetime.now().strftime("%Y%m%d_%H%M%S")
  out = PROBE_ROOT / ts
  out.mkdir(parents=True, exist_ok=True)

  dm = DeviceManager(device_id=args.serial)
  if not dm.connect():
    print("[探针] 设备连接失败")
    return 1
  device = dm.get_device()
  profile = load_profile(device=device)
  nav = Navigator(device)
  serial = _device_serial(device) or args.serial

  report: dict = {
    "serial": args.serial,
    "prompt": args.prompt,
    "profile_key": getattr(profile, "_profile_key", "vivo_v2301a"),
    "qa_resolve_accept_app_jump": profile.qa_resolve_accept_app_jump,
    "qa_resolve_batch_douyin": profile.qa_resolve_batch_douyin,
    "qa_douyin_deeplink_first": profile.qa_douyin_deeplink_first,
    "qa_douyin_deeplink_schemes": list(profile.qa_douyin_deeplink_schemes or ()),
    "douyin_installed": bool(
      (device.shell("pm path com.ss.android.ugc.aweme").output or "").strip()
    ),
    "note_same_phone": (
      "若抖音 feed 无 aweme id：请在抖音 App 用与豆包相同的手机号登录一次（SMS 取号同池）"
    ),
    "strategies": [],
  }

  print(f"[探针] 输出目录: {out}")
  capturer = DoubaoQaCapture(device, str(out / "qa_session"), profile=profile)
  try:
    refs = _reach_refs(capturer, args.prompt, args.skip_send)
  except Exception as exc:
    report["error"] = str(exc)
    (out / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[探针] 失败: {exc}")
    return 1

  report["ref_count"] = len(refs)
  report["douyin_ref_count"] = sum(1 for r in refs if is_likely_douyin_citation(r))
  douyin = _first_douyin(refs)
  if not douyin:
    report["error"] = "无抖音类引用"
    (out / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[探针] 无抖音类引用")
    return 1

  print(f"[探针] 抖音引用首条: #{douyin.ref_index} {douyin.title[:40]!r}")
  _prepare_panel(capturer, refs, profile)

  sdir_a = out / "strategy_A"
  report["strategies"].append(_strategy_wait_accept_collect(device, nav, douyin, profile, sdir_a, serial))

  _prepare_panel(capturer, refs, profile)
  sdir_b = out / "strategy_B"
  report["strategies"].append(_strategy_batch(device, nav, refs, profile, sdir_b, serial))

  _prepare_panel(capturer, refs, profile)
  sdir_c = out / "strategy_C"
  report["strategies"].append(_strategy_per_click_dumpsys(device, nav, douyin, profile, sdir_c, serial))

  _prepare_panel(capturer, refs, profile)
  sdir_d = out / "strategy_D"
  report["strategies"].append(_strategy_deeplink_device_id(device, nav, douyin, profile, sdir_d, serial))

  any_ok = any(s.get("ok") for s in report["strategies"])
  report["overall_ok"] = any_ok
  (out / "probe_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

  print("\n========== 探针结果 ==========")
  for s in report["strategies"]:
    print(f"  {s['strategy']}: ok={s.get('ok')} url={s.get('resolved_url') or s.get('filled_count', '')}")
  print(f"  overall_ok={any_ok}")
  print(f"  报告: {out / 'probe_report.json'}")
  return 0 if any_ok else 2


if __name__ == "__main__":
  raise SystemExit(main())
