#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
真机 UI 侦察：轮询 Activity + 界面层级，仅在屏幕变化时写入 JSONL + 截图。

用法（仓库根、已连接设备）:
  python recon/ui_spy.py
  python recon/ui_spy.py --interval 1.5 --no-screenshot

依赖：uiautomator2（与主项目 requirements.txt 一致）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _connect_device(serial: str | None):
    sys.path.insert(0, str(_repo_root()))
    import uiautomator2 as u2

    if serial:
        return u2.connect(serial)
    return u2.connect()


def _parse_hierarchy_elements(xml_text: str, max_elems: int) -> list[dict[str, str | bool]]:
    """从 uiautomator dump 提取简化节点列表（供 AI 阅读）。"""
    out: list[dict[str, str | bool]] = []
    if not xml_text or not xml_text.strip():
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    def walk(node: ET.Element, depth: int) -> None:
        if len(out) >= max_elems:
            return
        tag = node.tag
        if tag == "node":
            bounds = node.get("bounds") or ""
            rid = node.get("resource-id") or ""
            text = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            clickable = (node.get("clickable") or "").lower() == "true"
            cls = node.get("class") or ""
            if rid or text or desc or clickable:
                out.append(
                    {
                        "class": cls[:120],
                        "resource_id": rid[:200],
                        "text": text[:300],
                        "content_desc": desc[:300],
                        "clickable": clickable,
                        "bounds": bounds[:80],
                    }
                )
        for child in list(node):
            walk(child, depth + 1)

    walk(root, 0)
    return out


def _fingerprint(activity: str, package: str, elements: list[dict]) -> str:
    payload = json.dumps(
        {"a": activity, "p": package, "e": elements},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="真机 UI 变化记录（JSONL）")
    parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="轮询间隔秒",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSONL 输出路径（默认 recon/output/ui_spy_log.jsonl）",
    )
    parser.add_argument(
        "--shot-dir",
        type=Path,
        default=None,
        help="截图目录（默认 recon/output/ui_spy_shots）",
    )
    parser.add_argument(
        "--max-elems",
        type=int,
        default=400,
        help="每条记录最多保留的节点数",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        help="不写截图，仅 JSONL",
    )
    args = parser.parse_args()

    root = _repo_root()
    out_jsonl = (args.out or (root / "recon" / "output" / "ui_spy_log.jsonl")).resolve()
    shot_dir = (args.shot_dir or (root / "recon" / "output" / "ui_spy_shots")).resolve()
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    if not args.no_screenshot:
        shot_dir.mkdir(parents=True, exist_ok=True)

    device = _connect_device(args.serial)
    last_fp: str | None = None
    seq = 0

    print("UI Spy 已启动，Ctrl+C 结束。输出:", out_jsonl, flush=True)

    try:
        while True:
            try:
                cur = device.app_current() or {}
                pkg = str(cur.get("package") or "")
                act = str(cur.get("activity") or "")
                try:
                    xml_text = device.dump_hierarchy(compressed=False)
                except TypeError:
                    xml_text = device.dump_hierarchy()
            except Exception as e:
                print(f"采样失败: {e}", flush=True)
                time.sleep(args.interval)
                continue

            elements = _parse_hierarchy_elements(xml_text, max_elems=args.max_elems)
            fp = _fingerprint(act, pkg, elements)
            if fp == last_fp:
                time.sleep(args.interval)
                continue
            last_fp = fp
            seq += 1

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
            shot_path_str = ""
            if not args.no_screenshot:
                shot_name = f"{seq:05d}_{int(time.time())}.png"
                shot_path = shot_dir / shot_name
                try:
                    device.screenshot(str(shot_path))
                    shot_path_str = str(shot_path.relative_to(root))
                except Exception as e:
                    shot_path_str = f"(screenshot_failed:{e})"

            record = {
                "timestamp": ts,
                "seq": seq,
                "package": pkg,
                "activity": act,
                "fingerprint": fp[:16],
                "screen_elements": elements,
                "screenshot_path": shot_path_str,
            }
            with out_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"[{seq}] {pkg} / {act} elems={len(elements)}", flush=True)

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
