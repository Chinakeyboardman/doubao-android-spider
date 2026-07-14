#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长按引用 → 分享/复制链接 实验：从豆包 App 取 URL 回传电脑侧抓取元数据。

用法:
  python run_qa_share_link_probe.py --skip-send
  python run_qa_share_link_probe.py -s <adb_serial>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.gesture_profile import GestureProfile
from app.config.profile_loader import load_profile
from app.modules.navigator import Navigator
from app.modules.qa_capture import DoubaoQaCapture
from app.modules.qa_hierarchy import Citation, REFERENCE_INDEX_RID
from app.modules.qa_quality import GOLDEN_MODE, GOLDEN_PROMPT
from app.modules.qa_reference_urls import (
  REFERENCE_CONTENT_RID,
  SOURCE_ITEM_RID,
  _click_citation,
  _device_serial,
  _ensure_citation_visible,
  _find_citation_click_target,
  _ref_list_root_xpath,
  extract_urls_from_dumpsys_text,
  poll_logcat_for_url,
  resolve_url_via_dumpsys,
)
from app.utils.device import DeviceManager
from app.utils.utils import build_session_dir, log_error, log_info

PROBE_SCRIPT = "qa_share_link_probe"
_MENU_KEYWORDS = (
  "分享", "复制链接", "复制", "链接", "拷贝",
  "share", "copy", "link", "url",
)
_SHARE_PANEL_KEYWORDS = (
  "复制链接", "复制", "链接", "拷贝链接", "copy link", "copy",
)
_PC_UA = (
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _write_text(path: str, text: str) -> None:
  with open(path, "w", encoding="utf-8") as f:
    f.write(text)


def _read_clipboard(device: Any) -> str:
  try:
    return (device.clipboard or "").strip()
  except Exception:
    return ""


def _dump_hierarchy(device: Any, path: str) -> str:
  xml = device.dump_hierarchy(compressed=False) or ""
  _write_text(path, xml)
  return xml


def _screenshot(device: Any, path: str) -> None:
  try:
    device.screenshot(path)
  except OSError as exc:
    print(f"[分享探针] 截图失败: {path}: {exc}")


def _iter_nodes(xml_text: str) -> list[dict[str, str]]:
  if not xml_text:
    return []
  out: list[dict[str, str]] = []
  try:
    root = ET.fromstring(xml_text)
  except ET.ParseError:
    return out
  for el in root.iter("node"):
    text = (el.attrib.get("text") or "").strip()
    desc = (el.attrib.get("content-desc") or "").strip()
    rid = el.attrib.get("resource-id", "")
    clickable = el.attrib.get("clickable", "false") == "true"
    if not any((text, desc)):
      continue
    out.append(
      {
        "text": text,
        "content_desc": desc,
        "resource_id": rid,
        "clickable": str(clickable),
        "class": el.attrib.get("class", ""),
        "bounds": el.attrib.get("bounds", ""),
      }
    )
  return out


def _find_menu_candidates(xml_text: str) -> list[dict[str, str]]:
  hits: list[dict[str, str]] = []
  for node in _iter_nodes(xml_text):
    blob = f"{node['text']} {node['content_desc']} {node['resource_id']}".lower()
    if any(k.lower() in blob for k in _MENU_KEYWORDS):
      hits.append(node)
  return hits


def _looks_like_url(text: str) -> bool:
  t = (text or "").strip()
  return t.startswith("http://") or t.startswith("https://")


def _extract_urls_from_text(text: str) -> list[str]:
  return extract_urls_from_dumpsys_text(text or "")


def _find_content_element(device: Any, citation: Citation, profile: GestureProfile):
  """长按标题 TextView，避免误触整行跳转。"""
  root = _ref_list_root_xpath(device, profile)
  idx = citation.ref_index or 0
  chunk = (citation.title or "")[:18].replace('"', "")
  xpaths: list[str] = []
  if idx > 0 and chunk:
    xpaths.append(
      f'{root}//*[@resource-id="{REFERENCE_CONTENT_RID}"'
      f' and contains(@text,"{chunk}")]'
    )
  if idx > 0:
    xpaths.append(
      f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]'
      f'[.//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{idx}."]]'
      f'//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
    )
  for xp in xpaths:
    try:
      el = device.xpath(xp).get(timeout=0.5)
      if el:
        return el
    except Exception:
      continue
  target = _find_citation_click_target(device, citation, profile=profile)
  return target.element if target else None


def _parse_bounds_y(bounds: str) -> int | None:
  m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
  if not m:
    return None
  return (int(m.group(2)) + int(m.group(4))) // 2


def _filter_citation_menu(
  menu_items: list[dict[str, str]],
  citation: Citation,
) -> list[dict[str, str]]:
  """排除顶部全局分享按钮，只保留引用附近的菜单项。"""
  cy = None
  if citation.bounds and len(citation.bounds) == 4:
    cy = (citation.bounds[1] + citation.bounds[3]) // 2
  out: list[dict[str, str]] = []
  for item in menu_items:
    rid = item.get("resource_id", "")
    if "btn_share" in rid:
      continue
    if cy is not None:
      iy = _parse_bounds_y(item.get("bounds", ""))
      if iy is not None and abs(iy - cy) > 420:
        continue
    out.append(item)
  return out


def _clipboard_url(device: Any) -> str:
  clip = _read_clipboard(device)
  if _looks_like_url(clip):
    return clip
  urls = _extract_urls_from_text(clip)
  return urls[0] if urls else ""


def _click_share_copy_in_panel(device: Any, session_dir: str, tag: str) -> tuple[str, list[dict[str, Any]]]:
  """在分享面板尝试复制链接（含横向滑动）。"""
  steps: list[dict[str, Any]] = []
  labels = ("复制链接", "拷贝链接", "复制", "链接")
  for round_i in range(3):
    xml = _dump_hierarchy(device, os.path.join(session_dir, f"{tag}_panel_r{round_i}.xml"))
    _screenshot(device, os.path.join(session_dir, f"{tag}_panel_r{round_i}.png"))
    candidates = _find_menu_candidates(xml)
    steps.append({"round": round_i, "candidates": candidates[:40]})
    for label in labels:
      hit = _click_by_text(device, (label,))
      if not hit:
        continue
      time.sleep(0.7)
      url = _clipboard_url(device)
      steps.append({"action": f"click_{label}", "clipboard_url": url})
      if url:
        return url, steps
    # 分享渠道常为横向列表，向右滑再找一轮
    try:
      w, h = device.window_size()
      device.swipe(int(w * 0.82), int(h * 0.88), int(w * 0.18), int(h * 0.88), 0.25)
      time.sleep(0.35)
    except Exception:
      break
  return "", steps


def _try_click_then_share_copy(
  device: Any,
  nav: Navigator,
  citation: Citation,
  profile: GestureProfile,
  session_dir: str,
) -> dict[str, Any]:
  """点击引用进详情/抖音 → 点分享 → 复制链接。"""
  steps: list[dict[str, Any]] = []
  if not _click_citation(device, citation, profile=profile):
    return {"ok": False, "reason": "click_failed", "steps": steps}

  time.sleep(1.2)
  _screenshot(device, os.path.join(session_dir, "10_after_click.png"))
  cur = device.app_current() or {}
  steps.append({"action": "click_citation", "activity": cur.get("activity", "")})

  # 抖音/详情页顶栏分享
  share_hit = _click_by_text(device, ("分享",))
  if not share_hit:
    try:
      el = device.xpath('//*[@resource-id="com.larus.nova:id/btn_share"]').get(timeout=0.8)
      if el:
        el.click()
        share_hit = "btn_share"
    except Exception:
      pass
  if not share_hit:
    try:
      el = device.xpath('//*[contains(@content-desc,"分享")]').get(timeout=0.8)
      if el:
        el.click()
        share_hit = "content_desc_share"
    except Exception:
      pass

  if not share_hit:
    # 兜底：dumpsys 拿 URL（与现有 resolver 一致）
    url = resolve_url_via_dumpsys(device, serial=_device_serial(device), wait_s=1.0)
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
    return {
      "ok": bool(url),
      "resolved_url": url,
      "path": "click_dumpsys_fallback",
      "steps": steps,
    }

  time.sleep(1.0)
  url, panel_steps = _click_share_copy_in_panel(device, session_dir, "11_share")
  steps.extend(panel_steps)

  if not url:
    url = poll_logcat_for_url(serial=_device_serial(device), timeout_s=1.0)
  if not url:
    url = resolve_url_via_dumpsys(device, serial=_device_serial(device), wait_s=0.8)

  nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  return {
    "ok": bool(url),
    "resolved_url": url,
    "path": "click_share_copy",
    "steps": steps,
  }


def _long_press_element(device: Any, element: Any, profile: GestureProfile) -> bool:
  duration = profile.long_click_duration
  try:
    element.long_click(duration=duration)
    return True
  except Exception:
    pass
  try:
    info = element.info or {}
    b = info.get("bounds") or {}
    if b:
      x = (int(b["left"]) + int(b["right"])) // 2
      y = (int(b["top"]) + int(b["bottom"])) // 2
      device.long_click(x, y, duration)
      return True
  except Exception as exc:
    print(f"[分享探针] 长按失败: {exc}")
  return False


def _click_by_text(device: Any, labels: tuple[str, ...], *, timeout: float = 0.8) -> str:
  """按 text/content-desc 点击，返回命中的文案。"""
  for label in labels:
    for xp in (
      f'//*[@text="{label}"]',
      f'//*[contains(@text,"{label}")]',
      f'//*[@content-desc="{label}"]',
      f'//*[contains(@content-desc,"{label}")]',
    ):
      try:
        el = device.xpath(xp).get(timeout=timeout)
        if el:
          el.click()
          return label
      except Exception:
        continue
  return ""


def _scroll_refs_visible(capturer: DoubaoQaCapture) -> None:
  capturer._scroll_message_to_top()
  time.sleep(0.5)
  if not capturer._thinking_panel_on_screen():
    capturer._scroll_to_thinking_panel()
    time.sleep(0.4)
  capturer._ensure_thinking_header_expanded()
  time.sleep(0.35)
  capturer._expand_visible_search_groups(set())
  time.sleep(0.3)


def _reach_panel(
  capturer: DoubaoQaCapture,
  session_dir: str,
  prompt: str,
  *,
  skip_send: bool,
):
  if skip_send:
    if not capturer._crawler.start_app():
      raise RuntimeError("启动豆包失败")
    capturer._ensure_chat()
    capturer._dismiss_overlays()
    panel, _, _ = capturer._sweep_expand_and_capture(session_dir)
    return panel

  if not capturer._crawler.start_app():
    raise RuntimeError("启动豆包失败")
  if not capturer._crawler.handle_login_if_needed(sms_token="", device_id=""):
    raise RuntimeError("登录失败")
  if not capturer._open_new_conversation():
    print("[分享探针] 创建新对话失败，继续在当前会话")
  time.sleep(1.2)
  for attempt in range(3):
    if capturer._select_mode(GOLDEN_MODE):
      break
    if attempt < 2:
      time.sleep(0.8)
  if not capturer._crawler.send_message(prompt):
    raise RuntimeError("发送提示词失败")
  if not capturer._crawler.wait_reply_done(timeout=180):
    print("[分享探针] 等待回复超时，继续")
  time.sleep(1.0)
  capturer._ensure_chat()
  capturer._dismiss_overlays()
  panel, _, _ = capturer._sweep_expand_and_capture(session_dir)
  return panel


def _pick_citation(panel) -> Citation | None:
  refs = list(panel.references) if panel and panel.references else []
  if not refs:
    return None
  return min(refs, key=lambda r: r.ref_index if r.ref_index > 0 else 9999)


def _fetch_url_on_pc(url: str) -> dict[str, Any]:
  """电脑侧拉取 URL 元数据（标题/状态码/最终跳转）。"""
  result: dict[str, Any] = {"input_url": url}
  try:
    resp = requests.get(
      url,
      timeout=20,
      headers={"User-Agent": _PC_UA},
      allow_redirects=True,
    )
    result["status_code"] = resp.status_code
    result["final_url"] = resp.url
    result["content_type"] = resp.headers.get("Content-Type", "")
    result["content_length"] = len(resp.content or b"")
    title_m = re.search(r"<title[^>]*>([^<]+)</title>", resp.text or "", re.I)
    result["title"] = (title_m.group(1).strip() if title_m else "")[:300]
    host = urlparse(resp.url).netloc
    result["host"] = host
  except requests.RequestException as exc:
    result["error"] = str(exc)
  return result


def _try_share_flow(
  device: Any,
  nav: Navigator,
  citation: Citation,
  profile: GestureProfile,
  session_dir: str,
) -> dict[str, Any]:
  """长按引用 → 分享/复制；失败则回落点击进详情再分享复制。"""
  steps: list[dict[str, Any]] = []
  clipboard_before = _read_clipboard(device)

  if not _ensure_citation_visible(device, citation, profile):
    return {
      "ok": False,
      "reason": "citation_not_visible",
      "clipboard_before": clipboard_before,
      "steps": steps,
    }

  content_el = _find_content_element(device, citation, profile)
  if not content_el:
    return {
      "ok": False,
      "reason": "no_long_press_target",
      "clipboard_before": clipboard_before,
      "steps": steps,
    }

  _screenshot(device, os.path.join(session_dir, "00_before_long_press.png"))
  if not _long_press_element(device, content_el, profile):
    return {
      "ok": False,
      "reason": "long_press_failed",
      "clipboard_before": clipboard_before,
      "steps": steps,
    }

  time.sleep(0.9)
  xml_after_long = _dump_hierarchy(device, os.path.join(session_dir, "01_after_long_press.xml"))
  _screenshot(device, os.path.join(session_dir, "01_after_long_press.png"))
  menu_items = _filter_citation_menu(_find_menu_candidates(xml_after_long), citation)
  clip_after_long = _read_clipboard(device)
  resolved_url = _clipboard_url(device)
  steps.append(
    {
      "action": "long_press_content",
      "menu_candidates": menu_items[:30],
      "clipboard": clip_after_long,
      "clipboard_changed": clip_after_long != clipboard_before,
    }
  )

  if not resolved_url:
    for label in ("复制链接", "复制", "拷贝链接", "拷贝"):
      hit = _click_by_text(device, (label,))
      if not hit:
        continue
      time.sleep(0.6)
      clip = _read_clipboard(device)
      steps.append({"action": f"long_press_menu_{label}", "clipboard": clip})
      resolved_url = _clipboard_url(device)
      if resolved_url:
        break

  if not resolved_url and menu_items:
    share_hit = _click_by_text(device, ("分享",))
    if share_hit:
      time.sleep(1.0)
      url, panel_steps = _click_share_copy_in_panel(device, session_dir, "03_long_press_share")
      steps.extend(panel_steps)
      resolved_url = url

  path = "long_press_share"
  if resolved_url:
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  else:
    print("[分享探针] 长按路径未拿到 URL，回落：点击 → 分享 → 复制链接")
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
    time.sleep(0.5)
    _ensure_citation_visible(device, citation, profile)
    fallback = _try_click_then_share_copy(device, nav, citation, profile, session_dir)
    steps.append({"action": "fallback_click_share", **fallback})
    resolved_url = fallback.get("resolved_url", "")
    path = fallback.get("path", "click_share_copy")

  pc_fetch = _fetch_url_on_pc(resolved_url) if resolved_url else {}
  return {
    "ok": bool(resolved_url),
    "resolved_url": resolved_url,
    "path": path,
    "clipboard_before": clipboard_before,
    "clipboard_after": _read_clipboard(device),
    "steps": steps,
    "pc_fetch": pc_fetch,
  }


def _print_summary(report: dict[str, Any]) -> None:
  print("\n" + "=" * 60)
  print("Share Link Probe 结果")
  print("=" * 60)
  print(f"  引用: #{report.get('ref_index', '?')} {(report.get('ref_title') or '')[:56]}")
  print(f"  长按分享成功: {'是' if report.get('ok') else '否'} ({report.get('reason', '')})")
  print(f"  路径: {report.get('path', '')}")
  print(f"  解析 URL: {report.get('resolved_url') or '(无)'}")
  pc = report.get("pc_fetch") or {}
  if pc:
    print(f"  电脑侧抓取: status={pc.get('status_code')} host={pc.get('host')}")
    if pc.get("title"):
      print(f"  页面标题: {pc['title'][:80]}")
    if pc.get("error"):
      print(f"  抓取错误: {pc['error']}")
  print("=" * 60)


def main() -> int:
  parser = argparse.ArgumentParser(description="长按引用分享/复制链接实验")
  parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
  parser.add_argument("--out-dir", default="logs", help="产出根目录")
  parser.add_argument("--skip-send", action="store_true", help="跳过发送，用当前屏回复")
  args = parser.parse_args()

  session_dir = build_session_dir(args.out_dir, PROBE_SCRIPT)
  log_info(f"实验目录: {session_dir}")

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device=device)
  capturer = DoubaoQaCapture(device, output_dir=args.out_dir, profile=profile)
  nav = Navigator(device)

  try:
    panel = _reach_panel(
      capturer, session_dir, GOLDEN_PROMPT, skip_send=args.skip_send,
    )
    _scroll_refs_visible(capturer)

    citation = _pick_citation(panel)
    if not citation:
      log_error("无思考引用可测试")
      return 1

    result = _try_share_flow(device, nav, citation, profile, session_dir)
    report = {
      "prompt": GOLDEN_PROMPT,
      "mode": GOLDEN_MODE,
      "session_dir": session_dir,
      "ref_index": citation.ref_index,
      "ref_title": citation.title,
      "panel_ref_count": len(panel.references) if panel else 0,
      **result,
    }
    if not result.get("ok"):
      report["reason"] = result.get("reason", "no_url")

    report_path = os.path.join(session_dir, "report.json")
    with open(report_path, "w", encoding="utf-8") as f:
      json.dump(report, f, ensure_ascii=False, indent=2)

    _print_summary(report)
    log_info(f"报告: {report_path}")
    return 0 if report.get("ok") else 2
  except Exception as exc:
    log_error(f"实验失败: {exc}")
    return 1


if __name__ == "__main__":
  sys.exit(main())
