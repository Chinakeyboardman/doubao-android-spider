#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全流程操作录制器 — 常驻进程，你手动操作豆包，它自动记录一切。

产出（每次运行生成带时间戳的 session 目录）:
  recon/flow_recorder/sessions/<ts>/
    ├── flow.jsonl          # 每一帧：时间、Activity、操作推断、元素摘要
    ├── hierarchy/          # 每帧完整 XML（可离线 re-parse）
    ├── screenshots/        # 每帧截图 PNG
    └── summary.md          # 结束时自动生成的可读摘要

操作推断原理：
  - 比较前后两帧的 focused 节点、bounds 变化、Activity 跳转来推断
    点击/滑动/输入/返回/切换页面等操作。

用法:
  python recon/flow_recorder/recorder.py                  # 默认 0.8s 轮询
  python recon/flow_recorder/recorder.py --interval 0.5   # 更快
  python recon/flow_recorder/recorder.py --no-screenshot   # 只记 XML+JSONL

启动后你去操作手机，完成后回终端按 Ctrl+C 结束，自动生成 summary.md。
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
from typing import Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _connect(serial: Optional[str]):
    sys.path.insert(0, str(_repo_root()))
    import uiautomator2 as u2
    return u2.connect(serial) if serial else u2.connect()


# --------------- XML 解析 ---------------

def parse_elements(xml_text: str, max_elems: int = 600) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not xml_text or not xml_text.strip():
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    def walk(node: ET.Element, depth: int):
        if len(out) >= max_elems:
            return
        if node.tag == "node":
            bounds = node.get("bounds", "")
            rid = node.get("resource-id", "")
            text = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            cls = node.get("class", "")
            clickable = node.get("clickable", "false") == "true"
            focused = node.get("focused", "false") == "true"
            selected = node.get("selected", "false") == "true"
            checked = node.get("checked", "false") == "true"
            enabled = node.get("enabled", "true") == "true"
            pkg = node.get("package", "")
            out.append({
                "class": cls[:120],
                "rid": rid[:200],
                "text": text[:300],
                "desc": desc[:300],
                "clickable": clickable,
                "focused": focused,
                "selected": selected,
                "checked": checked,
                "enabled": enabled,
                "bounds": bounds[:80],
                "depth": depth,
                "pkg": pkg[:60],
            })
        for child in node:
            walk(child, depth + 1)

    walk(root, 0)
    return out


def fingerprint(elems: list[dict]) -> str:
    key_fields = []
    for e in elems:
        key_fields.append(f"{e['rid']}|{e['text'][:60]}|{e['bounds']}|{e['focused']}")
    payload = "\n".join(key_fields)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def parse_bounds(bounds_str: str) -> Optional[tuple[int, int, int, int]]:
    try:
        s = bounds_str.replace("][", ",").strip("[]")
        parts = s.split(",")
        return (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    except Exception:
        return None


# --------------- 操作推断 ---------------

def infer_action(
    prev_act: str, curr_act: str,
    prev_pkg: str, curr_pkg: str,
    prev_elems: list[dict], curr_elems: list[dict],
    prev_focused: Optional[dict], curr_focused: Optional[dict],
) -> dict[str, Any]:
    """推断两帧之间用户做了什么操作。"""
    action: dict[str, Any] = {"type": "unknown", "detail": ""}

    # Activity / package 变化
    if curr_pkg != prev_pkg:
        action = {"type": "app_switch", "detail": f"{prev_pkg} -> {curr_pkg}"}
        return action
    if curr_act != prev_act:
        action = {"type": "navigate", "detail": f"{prev_act} -> {curr_act}"}
        return action

    # focused 节点变化 -> 可能点击了某个输入框
    if curr_focused and (not prev_focused or curr_focused["rid"] != prev_focused.get("rid", "")):
        action = {
            "type": "focus_change",
            "detail": f"focused -> rid={curr_focused['rid'][:60]} text={curr_focused['text'][:40]!r}",
            "target": _elem_summary(curr_focused),
        }
        return action

    # 文本变化（同一个 rid 的 text 变了 -> 输入）
    prev_by_rid = {e["rid"]: e for e in prev_elems if e["rid"]}
    for e in curr_elems:
        if not e["rid"]:
            continue
        pe = prev_by_rid.get(e["rid"])
        if pe and pe["text"] != e["text"] and e["text"]:
            if "EditText" in e["class"] or "input" in e["rid"].lower():
                action = {
                    "type": "text_input",
                    "detail": f"rid={e['rid'][:60]} '{pe['text'][:30]}' -> '{e['text'][:30]}'",
                    "target": _elem_summary(e),
                }
                return action

    # 元素大量增减 -> 可能滑动或页面刷新
    prev_texts = {e["text"][:80] for e in prev_elems if e["text"]}
    curr_texts = {e["text"][:80] for e in curr_elems if e["text"]}
    new_texts = curr_texts - prev_texts
    gone_texts = prev_texts - curr_texts
    if len(new_texts) > 3 or len(gone_texts) > 3:
        # bounds 整体偏移 -> 滑动
        direction = _detect_scroll_direction(prev_elems, curr_elems)
        if direction:
            action = {
                "type": "scroll",
                "detail": f"方向={direction}, 新增文本={len(new_texts)}, 消失文本={len(gone_texts)}",
                "new_texts": sorted(list(new_texts))[:8],
            }
            return action
        action = {
            "type": "content_change",
            "detail": f"新增文本={len(new_texts)}, 消失文本={len(gone_texts)}",
            "new_texts": sorted(list(new_texts))[:8],
        }
        return action

    # 少量元素出现/消失 -> 可能点击触发了弹窗/按钮
    if new_texts:
        action = {
            "type": "ui_update",
            "detail": f"出现: {sorted(list(new_texts))[:5]}",
        }
        return action

    return action


def _detect_scroll_direction(prev_elems: list[dict], curr_elems: list[dict]) -> Optional[str]:
    """通过同 rid 元素的 y 坐标偏移判断滑动方向。"""
    prev_by_rid = {}
    for e in prev_elems:
        if e["rid"]:
            b = parse_bounds(e["bounds"])
            if b:
                prev_by_rid[e["rid"]] = b

    offsets = []
    for e in curr_elems:
        if e["rid"] and e["rid"] in prev_by_rid:
            b = parse_bounds(e["bounds"])
            if b:
                offsets.append(b[1] - prev_by_rid[e["rid"]][1])

    if not offsets:
        return None
    avg = sum(offsets) / len(offsets)
    if avg < -30:
        return "上滑(内容上移)"
    if avg > 30:
        return "下滑(内容下移)"
    return None


def _elem_summary(e: dict) -> dict[str, str]:
    return {
        "class": e["class"][:60],
        "rid": e["rid"][:60],
        "text": e["text"][:60],
        "desc": e["desc"][:60],
        "bounds": e["bounds"],
    }


def _find_focused(elems: list[dict]) -> Optional[dict]:
    for e in elems:
        if e.get("focused"):
            return e
    return None


# --------------- 摘要生成 ---------------

def generate_summary(session_dir: Path, frames: list[dict]) -> str:
    lines = [
        f"# 操作录制摘要",
        f"",
        f"- 录制时间: {frames[0]['timestamp'] if frames else '?'} ~ {frames[-1]['timestamp'] if frames else '?'}",
        f"- 总帧数: {len(frames)}",
        f"- 设备: 见首帧 package",
        f"",
        f"## 操作时间线",
        f"",
        f"| # | 时间 | Activity | 操作 | 详情 |",
        f"|---|------|----------|------|------|",
    ]
    for f in frames:
        act_short = f["activity"].split(".")[-1] if f["activity"] else "?"
        action = f.get("action", {})
        atype = action.get("type", "")
        detail = action.get("detail", "")[:80]
        lines.append(f"| {f['seq']} | {f['timestamp'][-12:]} | {act_short} | {atype} | {detail} |")

    lines.append("")
    lines.append("## 关键页面元素（去重）")
    lines.append("")

    seen_rids: set[str] = set()
    for f in frames:
        for e in f.get("key_elements", []):
            rid = e.get("rid", "")
            if rid and rid not in seen_rids:
                seen_rids.add(rid)
                lines.append(f"- `{rid}` class=`{e.get('class','')}` text={e.get('text','')[:40]!r} desc={e.get('desc','')[:40]!r} clickable={e.get('clickable')}")

    lines.append("")
    lines.append(f"完整数据: `{session_dir}`")
    return "\n".join(lines)


# --------------- 主循环 ---------------

def main() -> int:
    parser = argparse.ArgumentParser(description="豆包操作全流程录制器")
    parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")
    parser.add_argument("--interval", type=float, default=0.8, help="轮询间隔秒（默认 0.8）")
    parser.add_argument("--no-screenshot", action="store_true", help="不截图，节省空间")
    parser.add_argument("--max-elems", type=int, default=600, help="每帧最多保留节点数")
    args = parser.parse_args()

    root = _repo_root()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = root / "recon" / "flow_recorder" / "sessions" / ts
    hierarchy_dir = session_dir / "hierarchy"
    shot_dir = session_dir / "screenshots"
    session_dir.mkdir(parents=True, exist_ok=True)
    hierarchy_dir.mkdir(exist_ok=True)
    if not args.no_screenshot:
        shot_dir.mkdir(exist_ok=True)

    flow_jsonl = session_dir / "flow.jsonl"

    device = _connect(args.serial)
    print(f"{'='*60}", flush=True)
    print(f"  操作录制器已启动", flush=True)
    print(f"  session: {session_dir}", flush=True)
    print(f"  轮询间隔: {args.interval}s  截图: {'关' if args.no_screenshot else '开'}", flush=True)
    print(f"  现在去手机上操作，完成后回这里按 Ctrl+C", flush=True)
    print(f"{'='*60}", flush=True)

    frames: list[dict] = []
    last_fp: Optional[str] = None
    prev_act = ""
    prev_pkg = ""
    prev_elems: list[dict] = []
    prev_focused: Optional[dict] = None
    seq = 0

    try:
        while True:
            try:
                cur = device.app_current() or {}
                curr_pkg = str(cur.get("package") or "")
                curr_act = str(cur.get("activity") or "")
                try:
                    xml_text = device.dump_hierarchy(compressed=False)
                except TypeError:
                    xml_text = device.dump_hierarchy()
            except Exception as e:
                print(f"  [!] 采样失败: {e}", flush=True)
                time.sleep(args.interval)
                continue

            curr_elems = parse_elements(xml_text, max_elems=args.max_elems)
            fp = fingerprint(curr_elems)

            if fp == last_fp:
                time.sleep(args.interval)
                continue

            last_fp = fp
            seq += 1
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

            # 保存完整 XML
            xml_path = hierarchy_dir / f"{seq:04d}.xml"
            xml_path.write_text(xml_text, encoding="utf-8")

            # 截图
            shot_rel = ""
            if not args.no_screenshot:
                shot_path = shot_dir / f"{seq:04d}.png"
                try:
                    device.screenshot(str(shot_path))
                    shot_rel = str(shot_path.relative_to(session_dir))
                except Exception:
                    shot_rel = "(failed)"

            # 推断操作
            curr_focused = _find_focused(curr_elems)
            action = infer_action(
                prev_act, curr_act, prev_pkg, curr_pkg,
                prev_elems, curr_elems,
                prev_focused, curr_focused,
            ) if seq > 1 else {"type": "session_start", "detail": f"{curr_pkg}/{curr_act}"}

            # 提取关键元素（有 rid 或有文本且可点击的）
            key_elems = [
                _elem_summary(e)
                for e in curr_elems
                if (e["rid"] and "systemui" not in e["rid"])
                or (e["text"] and e["clickable"])
                or (e["desc"] and e["clickable"])
            ][:80]

            frame = {
                "seq": seq,
                "timestamp": now,
                "package": curr_pkg,
                "activity": curr_act,
                "fingerprint": fp,
                "action": action,
                "key_elements": key_elems,
                "total_elements": len(curr_elems),
                "screenshot": shot_rel,
                "hierarchy_xml": str(xml_path.relative_to(session_dir)),
            }
            frames.append(frame)

            with flow_jsonl.open("a", encoding="utf-8") as f:
                f.write(json.dumps(frame, ensure_ascii=False) + "\n")

            atype = action.get("type", "")
            detail = action.get("detail", "")[:60]
            act_short = curr_act.split(".")[-1] if curr_act else "?"
            print(
                f"  [{seq:3d}] {act_short:<30s} | {atype:<16s} | {detail} | elems={len(curr_elems)}",
                flush=True,
            )

            prev_act = curr_act
            prev_pkg = curr_pkg
            prev_elems = curr_elems
            prev_focused = curr_focused

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print(f"\n{'='*60}", flush=True)
        print(f"  录制结束，共 {seq} 帧", flush=True)

    # 生成摘要
    summary_text = generate_summary(session_dir, frames)
    summary_path = session_dir / "summary.md"
    summary_path.write_text(summary_text, encoding="utf-8")
    print(f"  摘要已生成: {summary_path}", flush=True)
    print(f"  完整数据: {session_dir}", flush=True)
    print(f"{'='*60}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
