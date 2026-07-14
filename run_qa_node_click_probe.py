#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点树精准点击探针：验证「滚到节点可见 + 元素 click + 禁止坐标」。

用法（当前屏已展开思考引用时）:
  python run_qa_node_click_probe.py --mode citations --dry-run
  python run_qa_node_click_probe.py --mode citations --max-refs 5
  python run_qa_node_click_probe.py --mode products --dry-run

产出: logs/qa_node_click/<日期>/<时刻>/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.config.profile_loader import load_profile
from app.modules.flow_crawler import FlowCrawler
from app.modules.navigator import Navigator, Page
from app.modules.qa_hierarchy import parse_thinking_panel
from app.modules.qa_reference_urls import (
  _detect_ref_list_root_xpath,
  _ensure_citation_visible,
  _find_citation_click_target,
  _get_ref_list_bounds,
  resolve_thinking_reference_urls,
)
from app.modules.ui_node_click import (
  click_bounds_via_node,
  click_element,
  scroll_citation_index_into_view,
)
from app.utils.device import DeviceManager
from app.utils.utils import log_error, log_info


def _session_dir(root: str) -> str:
  now = datetime.now()
  path = os.path.join(
    root, "qa_node_click", now.strftime("%Y-%m-%d"), now.strftime("%H%M%S"),
  )
  os.makedirs(path, exist_ok=True)
  return path


def _dump_refs(device: Any) -> list:
  xml = device.dump_hierarchy(compressed=False) or ""
  panel = parse_thinking_panel(xml)
  return list(panel.references or [])


def probe_citations(
  device: Any,
  profile: Any,
  *,
  dry_run: bool,
  max_refs: int,
  resolve_urls: bool,
) -> dict[str, Any]:
  nav = Navigator(device)
  page, cur = nav.current_page()
  report: dict[str, Any] = {
    "mode": "citations",
    "page": page.name,
    "activity": cur.get("activity", ""),
    "dry_run": dry_run,
    "items": [],
  }
  if page != Page.CHAT:
    report["error"] = f"当前非聊天页: {page.name}"
    return report

  refs = _dump_refs(device)
  report["ref_count"] = len(refs)
  if not refs:
    report["error"] = "当前屏未解析到引用（请先展开思考/搜索组）"
    return report

  root = _detect_ref_list_root_xpath(device, profile)
  list_bounds = _get_ref_list_bounds(device, profile)
  report["root_xpath"] = root
  report["list_bounds"] = list_bounds

  limit = max_refs if max_refs > 0 else len(refs)
  for cite in refs[:limit]:
    item: dict[str, Any] = {
      "ref_index": cite.ref_index,
      "title": (cite.title or "")[:80],
    }
    direction = "down"
    scrolled = None
    if cite.ref_index > 0:
      scrolled = scroll_citation_index_into_view(
        device,
        root_xpath=root,
        ref_index=cite.ref_index,
        container_bounds=list_bounds,
        direction_hint=direction,
        max_swipes=profile.qa_resolve_citation_max_swipes,
        get_container=lambda: _get_ref_list_bounds(device, profile),
      )
    item["scroll_hit"] = scrolled is not None

    visible = _ensure_citation_visible(device, cite, profile)
    item["visible"] = visible
    target = _find_citation_click_target(device, cite, log=True, profile=profile)
    if not target:
      item["ok"] = False
      item["error"] = "无精确节点（拒绝坐标点击）"
      report["items"].append(item)
      continue

    item["strategy"] = target.strategy
    item["click_rid"] = target.click_rid
    item["index_text"] = target.index_text
    item["title_text"] = target.title_text[:60]
    item["bounds"] = target.bounds

    if dry_run:
      item["ok"] = True
      item["action"] = "dry-run"
      report["items"].append(item)
      continue

    clicked = click_element(target.element, tag=f"#{cite.ref_index}")
    item["ok"] = clicked.ok
    item["action"] = "click"
    if clicked.ok:
      time.sleep(0.8)
      nav.lite_back_to_chat()
      time.sleep(0.4)
    report["items"].append(item)

  if resolve_urls and not dry_run:
    log_info("开始解析引用 URL（节点点击路径）...")
    resolve_thinking_reference_urls(
      device, refs, profile=profile, method="auto", max_refs=limit,
    )
    report["resolved"] = [
      {
        "ref_index": c.ref_index,
        "title": (c.title or "")[:60],
        "url": c.url or "",
      }
      for c in refs[:limit]
    ]
    with_url = sum(1 for c in refs[:limit] if c.url)
    report["url_count"] = with_url
    report["url_ratio"] = with_url / max(1, min(limit, len(refs)))

  ok_n = sum(1 for it in report["items"] if it.get("ok"))
  report["success_count"] = ok_n
  report["fail_count"] = len(report["items"]) - ok_n
  return report


def probe_products(
  device: Any,
  profile: Any,
  *,
  dry_run: bool,
  max_cards: int,
) -> dict[str, Any]:
  """嵌入商品卡：用节点反查点击，禁止 d.click(cx,cy)。"""
  crawler = FlowCrawler(device, profile=profile)
  nav = Navigator(device)
  page, cur = nav.current_page()
  report: dict[str, Any] = {
    "mode": "products",
    "page": page.name,
    "activity": cur.get("activity", ""),
    "dry_run": dry_run,
    "items": [],
  }

  if page == Page.APPLET_LIST:
    items = crawler._collect_applet_items()
    for i, it in enumerate(items[: max(1, max_cards)]):
      entry: dict[str, Any] = {
        "title": it.get("title", "")[:80],
        "bounds": list(it["bounds"]),
      }
      if dry_run:
        from app.modules.ui_node_click import find_clickable_covering_bounds

        el = find_clickable_covering_bounds(device, it["bounds"])
        entry["ok"] = el is not None
        entry["action"] = "dry-run-find-node"
        if el:
          info = getattr(el, "info", None) or {}
          entry["rid"] = str(info.get("resourceName") or "")
          entry["clickable"] = bool(info.get("clickable"))
      else:
        result = click_bounds_via_node(device, it["bounds"], tag=f"product#{i+1}")
        entry["ok"] = result.ok
        entry["action"] = result.strategy
        entry["message"] = result.message
        if result.ok:
          time.sleep(1.5)
          nav.safe_back_to_chat(max_backs=4)
          # 回到列表可能需要再进 applet；探针只验证点击方式
      report["items"].append(entry)
  else:
    cards = crawler.find_embedded_product_cards()
    report["card_count"] = len(cards)
    for i, c in enumerate(cards[: max(1, max_cards)]):
      b = c["bounds"]
      entry = {"bounds": list(b), "area": c.get("area")}
      if dry_run:
        from app.modules.ui_node_click import find_clickable_covering_bounds

        el = find_clickable_covering_bounds(device, b)
        entry["ok"] = el is not None
        entry["action"] = "dry-run-find-node"
      else:
        result = click_bounds_via_node(device, b, tag=f"card#{i+1}")
        entry["ok"] = result.ok
        entry["action"] = result.strategy
        entry["message"] = result.message
        if result.ok:
          time.sleep(1.5)
          nav.safe_back_to_chat(max_backs=4)
      report["items"].append(entry)

  ok_n = sum(1 for it in report["items"] if it.get("ok"))
  report["success_count"] = ok_n
  report["fail_count"] = len(report["items"]) - ok_n
  return report


def main() -> int:
  parser = argparse.ArgumentParser(description="UI 节点树精准点击探针")
  parser.add_argument(
    "--mode",
    choices=("citations", "products"),
    default="citations",
    help="citations=思考引用；products=商品卡/列表（节点反查，禁坐标）",
  )
  parser.add_argument(
    "--dry-run",
    action="store_true",
    help="只定位/校验，不真正点击",
  )
  parser.add_argument("--max-refs", type=int, default=0, help="最多处理引用条数（0=全部）")
  parser.add_argument("--max-cards", type=int, default=3, help="最多处理商品卡数")
  parser.add_argument(
    "--resolve-urls",
    action="store_true",
    help="citations 模式下点击后解析 URL",
  )
  parser.add_argument("-s", "--serial", default=None)
  parser.add_argument("--device-profile", default=None)
  parser.add_argument("--out-dir", default="logs")
  args = parser.parse_args()

  dm = DeviceManager(args.serial)
  device = dm.get_device()
  profile = load_profile(device_name=args.device_profile, device=device)
  session = _session_dir(args.out_dir)
  log_info(f"产出目录: {session}")

  if args.mode == "citations":
    report = probe_citations(
      device,
      profile,
      dry_run=args.dry_run,
      max_refs=args.max_refs,
      resolve_urls=args.resolve_urls,
    )
  else:
    report = probe_products(
      device,
      profile,
      dry_run=args.dry_run,
      max_cards=args.max_cards,
    )

  report["session_dir"] = session
  report_path = os.path.join(session, "report.json")
  with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
  log_info(f"已写入 {report_path}")

  if report.get("error"):
    log_error(report["error"])
    return 1

  fail = int(report.get("fail_count") or 0)
  ok = int(report.get("success_count") or 0)
  log_info(f"完成：成功 {ok}，失败 {fail}")
  return 0 if fail == 0 and ok > 0 else 1


if __name__ == "__main__":
  sys.exit(main())
