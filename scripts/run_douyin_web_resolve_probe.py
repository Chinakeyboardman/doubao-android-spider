#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Web 抖音链接验证探针（无需真机）。

测试 aweme_id → 多 HTTP 格式级联验证、格式矩阵、v.douyin.com 短链反向展开。

产出
----
doc/reports/douyin_web_resolve/<timestamp>/
  - probe_report.json
  - REPORT.md

运行
----
  .venv/bin/python scripts/run_douyin_web_resolve_probe.py
  .venv/bin/python scripts/run_douyin_web_resolve_probe.py --aweme-id 7428415093521648905
  .venv/bin/python scripts/run_douyin_web_resolve_probe.py --short-url https://v.douyin.com/JPa1xhq/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.douyin_web_resolve import (
  AWEME_URL_FORMATS,
  build_share_url,
  build_url_candidates,
  expand_short_link,
  validate_aweme_multi_format,
)

DEFAULT_AWEME_IDS = [
  "7428415093521648905",  # manual_verify2 WebActivity / jingxuan modal_id
  "7548775039182294330",  # vivo 探针 A 成功样本
  "7650085520299273595",  # logcat/dumpsys 单测样本
  "7356065400192355620",  # adaptive_wait 报告样本
  "6883418578486349070",  # v.douyin 反向展开样本
]

DEFAULT_SHORT_URL = "https://v.douyin.com/JPa1xhq/"

REPORT_ROOT = Path("doc/reports/douyin_web_resolve")


def _run_format_matrix(aweme_id: str, device_id: str) -> list[dict]:
  """对每个 HTTP 模板单独验证（矩阵行）。"""
  from app.modules.douyin_web_resolve import _validate_single_url

  rows: list[dict] = []
  for fid, url in build_url_candidates(aweme_id, device_id=device_id):
    t0 = time.time()
    result = _validate_single_url(
      url,
      aweme_id,
      format_id=fid,
      device_id=device_id,
      min_interval_s=0,
    )
    row = asdict(result)
    row["elapsed_s"] = round(time.time() - t0, 2)
    row["probe_url"] = url
    rows.append(row)
  return rows


def _run_aweme_probe(aweme_id: str, device_id: str) -> dict:
  t0 = time.time()
  result = validate_aweme_multi_format(aweme_id, device_id=device_id, min_interval_s=0)
  elapsed = round(time.time() - t0, 2)
  row = asdict(result)
  row["elapsed_s"] = elapsed
  row["share_url_built"] = build_share_url(aweme_id, device_id=device_id)
  row["format_matrix"] = _run_format_matrix(aweme_id, device_id)
  return row


def _run_short_probe(short_url: str) -> dict:
  t0 = time.time()
  result = expand_short_link(short_url)
  elapsed = round(time.time() - t0, 2)
  row = asdict(result)
  row["elapsed_s"] = elapsed
  row["input_short_url"] = short_url
  return row


def _render_md(report: dict, out_dir: Path) -> str:
  lines = [
    "# PC Web 抖音多格式链接验证探针报告",
    "",
    f"- 时间: {report['timestamp']}",
    f"- 输出目录: `{out_dir}`",
    f"- overall_ok: **{report['overall_ok']}**",
    "",
    "## 结论摘要",
    "",
  ]
  for note in report.get("summary_notes", []):
    lines.append(f"- {note}")
  lines.extend(["", "## aweme_id 多格式级联（best_verified）", ""])
  lines.append("| aweme_id | verified | format_id | share_url | status | note |")
  lines.append("|----------|----------|-----------|-----------|--------|------|")
  for row in report.get("aweme_probes", []):
    url = row.get("share_url", "")[:56]
    lines.append(
      f"| `{row['aweme_id']}` | {row['verified']} | {row.get('format_id', '')} | "
      f"{url} | {row['status']} | {row['note']} |"
    )
  lines.extend(["", "## 格式矩阵（单格式探测）", ""])
  for row in report.get("aweme_probes", []):
    lines.append(f"### `{row['aweme_id']}`")
    lines.append("")
    lines.append("| format_id | verified | http | note |")
    lines.append("|-----------|----------|------|------|")
    for fr in row.get("format_matrix", []):
      lines.append(
        f"| {fr.get('format_id', '')} | {fr.get('verified')} | "
        f"{fr.get('http_status', '')} | {fr.get('note', '')} |"
      )
    lines.append("")
  lines.extend(["", "## 深链 vs HTTP 对照", ""])
  lines.append("| 侧 | 模板 | 说明 |")
  lines.append("|----|------|------|")
  for fid, fmt in AWEME_URL_FORMATS.items():
    lines.append(f"| HTTP | `{fid}` | {fmt.label} |")
  lines.append("| 深链 | `snssdk1128://aweme/detail/{id}` | logcat / am start |")
  lines.append("| 深链 | `snssdk1180://aweme/detail/{id}?device_id=` | 备用 scheme |")
  lines.extend(["", "## v.douyin 短链反向展开", ""])
  for row in report.get("short_probes", []):
    lines.append(f"- 输入: `{row.get('input_short_url', '')}`")
    lines.append(f"  - verified: {row['verified']}, aweme_id: `{row['aweme_id']}`")
    lines.append(f"  - share_url: {row.get('share_url', '')}")
    lines.append(f"  - chain: {' → '.join(row.get('redirect_chain', [])[:4])}")
  lines.extend(
    [
      "",
      "## 方案说明",
      "",
      "1. **关键输入**：19 位 `aweme_id`（logcat / dumpsys / WebActivity）。",
      "2. **HTTP 模板**：jingxuan modal_id → douyin.com/video → iesdouyin share。",
      "3. **best_verified**：级联验证，首个通过的**原始 URL** 写入 Citation.url。",
      "4. **短链**：`v.douyin.com` 仅反向展开，不能从 aweme_id 正向 HTTP 生成。",
      "5. **profile**：`qa_douyin_web_url_formats` 控制启用格式顺序。",
      "",
    ]
  )
  return "\n".join(lines)


def main() -> int:
  parser = argparse.ArgumentParser(description="PC Web 抖音多格式链接验证探针")
  parser.add_argument(
    "--aweme-id",
    action="append",
    dest="aweme_ids",
    default=[],
    help="指定 aweme_id（可重复）",
  )
  parser.add_argument("--short-url", default=DEFAULT_SHORT_URL)
  parser.add_argument("--device-id", default=os.environ.get("DOUYIN_WEB_DID", ""))
  parser.add_argument("--min-interval", type=float, default=0.8)
  args = parser.parse_args()

  aweme_ids = args.aweme_ids or list(DEFAULT_AWEME_IDS)
  ts = datetime.now().strftime("%Y%m%d_%H%M%S")
  out_dir = REPORT_ROOT / ts
  out_dir.mkdir(parents=True, exist_ok=True)

  print(f"[PC探针] 输出: {out_dir}", flush=True)
  aweme_probes: list[dict] = []
  for i, aid in enumerate(aweme_ids):
    print(f"[PC探针] aweme {i + 1}/{len(aweme_ids)}: {aid}", flush=True)
    aweme_probes.append(_run_aweme_probe(aid, args.device_id))
    if i + 1 < len(aweme_ids):
      time.sleep(args.min_interval)

  print(f"[PC探针] 短链反向: {args.short_url}", flush=True)
  short_probes = [_run_short_probe(args.short_url)]

  verified_n = sum(1 for r in aweme_probes if r.get("verified"))
  short_ok = any(r.get("verified") for r in short_probes)
  overall_ok = verified_n >= max(1, len(aweme_ids) // 2) and short_ok

  summary_notes = [
    f"aweme_id 多格式级联 {verified_n}/{len(aweme_ids)} 通过",
    "best_verified 优先 jingxuan modal_id → douyin.com/video → iesdouyin",
    "格式矩阵见各 aweme_id 小节",
    "v.douyin.com 无法从 aweme_id 正向生成（仅反向展开可行）",
  ]

  report = {
    "timestamp": ts,
    "device_id_query": args.device_id,
    "min_interval_s": args.min_interval,
    "aweme_probes": aweme_probes,
    "short_probes": short_probes,
    "verified_count": verified_n,
    "overall_ok": overall_ok,
    "summary_notes": summary_notes,
  }

  (out_dir / "probe_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )
  md = _render_md(report, out_dir)
  (out_dir / "REPORT.md").write_text(md, encoding="utf-8")

  print("\n========== PC Web 探针结果 ==========", flush=True)
  for row in aweme_probes:
    print(
      f"  {row['aweme_id']}: verified={row['verified']} "
      f"format={row.get('format_id', '')} url={row.get('share_url', '')[:60]}",
      flush=True,
    )
  for row in short_probes:
    print(
      f"  short→id: verified={row['verified']} id={row['aweme_id']}",
      flush=True,
    )
  print(f"  overall_ok={overall_ok}", flush=True)
  print(f"  报告: {out_dir / 'REPORT.md'}", flush=True)
  return 0 if overall_ok else 2


if __name__ == "__main__":
  raise SystemExit(main())
