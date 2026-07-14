#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理项目冗余 qa_capture 会话，并迁移到 var/<项目>/qa_capture/。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.qa_spot_check_export import (  # noqa: E402
  SPOT_CHECK_COLUMNS,
  dedupe_signed_prompts,
  load_signed_prompts,
)


def _session_roots(var_dir: Path) -> list[Path]:
  return [
    var_dir / "qa_capture",
    ROOT / "logs" / "qa_capture" / var_dir.name,
  ]


def _find_sessions(root: Path) -> dict[str, Path]:
  out: dict[str, Path] = {}
  if not root.is_dir():
    return out
  for rec in root.rglob("record.json"):
    out[str(rec.parent.resolve())] = rec.parent.resolve()
  return out


def _relocate_session(src: Path, var_dir: Path) -> Path:
  """logs/.../雅诗兰黛/date/time -> var/雅诗兰黛/qa_capture/date/time"""
  parts = src.parts
  try:
    idx = parts.index("qa_capture")
    tail = parts[idx + 1 :]
    if tail and tail[0] == var_dir.name:
      tail = tail[1:]
  except ValueError:
    tail = src.parts[-2:]
  dst = var_dir / "qa_capture" / Path(*tail)
  if src.resolve() == dst.resolve():
    return dst
  if dst.exists():
    shutil.rmtree(dst)
  dst.parent.mkdir(parents=True, exist_ok=True)
  shutil.move(str(src), str(dst))
  return dst


def _dedupe_csv_rows(csv_path: Path) -> tuple[int, int]:
  if not csv_path.is_file():
    return 0, 0
  with csv_path.open(encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
  seen: set[str] = set()
  kept: list[dict[str, str]] = []
  for row in rows:
    key = "".join((row.get("提示词") or "").split())
    if not key or key in seen:
      continue
    seen.add(key)
    kept.append(row)
  if len(kept) == len(rows):
    return len(rows), 0
  with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(SPOT_CHECK_COLUMNS))
    w.writeheader()
    w.writerows(kept)
  return len(kept), len(rows) - len(kept)


def main() -> int:
  parser = argparse.ArgumentParser(description="清理项目 qa_capture 冗余存储")
  parser.add_argument("--var-dir", required=True, help="项目 var 目录，如 var/雅诗兰黛")
  parser.add_argument("--state-file", default="", help="spot_check_state.json")
  parser.add_argument("--prompts-file", default="", help="签单文件（用于同步去重计数）")
  parser.add_argument("--dry-run", action="store_true")
  args = parser.parse_args()

  var_dir = Path(args.var_dir)
  state_path = Path(args.state_file or var_dir / "spot_check_state.json")
  if not state_path.is_file():
    print(f"state 不存在: {state_path}")
    return 1

  data = json.loads(state_path.read_text(encoding="utf-8"))
  completed: dict[str, str] = data.get("completed") or {}
  keep_sessions = {os.path.normpath(v) for v in completed.values()}

  all_found: dict[str, Path] = {}
  for root in _session_roots(var_dir):
    all_found.update(_find_sessions(root))

  moved = 0
  deleted = 0
  new_completed: dict[str, str] = {}

  for kid, sess in completed.items():
    src = Path(sess)
    if not src.is_dir():
      # try find by date/time suffix under known roots
      tail = "/".join(Path(sess).parts[-2:])
      found = None
      for p in all_found.values():
        if str(p).endswith(tail):
          found = p
          break
      if found:
        src = found
      else:
        print(f"警告: 保留会话缺失 {kid} -> {sess}")
        new_completed[kid] = sess
        continue

    if str(src.resolve()).startswith(str((var_dir / "qa_capture").resolve())):
      new_completed[kid] = str(src)
      continue

    if args.dry_run:
      dst = var_dir / "qa_capture" / src.parts[-2] / src.parts[-1]
      print(f"[dry-run] 迁移 {src} -> {dst}")
      new_completed[kid] = str(dst)
      moved += 1
      continue

    dst = _relocate_session(src, var_dir)
    new_completed[kid] = str(dst)
    moved += 1

  for path_str, path in list(all_found.items()):
    norm = os.path.normpath(str(path.resolve()))
    kept_paths = {os.path.normpath(p) for p in new_completed.values()}
    if norm in kept_paths:
      continue
    if not path.exists():
      continue
    if args.dry_run:
      print(f"[dry-run] 删除冗余 {path}")
    else:
      shutil.rmtree(path, ignore_errors=True)
    deleted += 1

  # 清理空日期目录
  for root in _session_roots(var_dir):
    if not root.is_dir():
      continue
    for day_dir in sorted(root.iterdir()):
      if day_dir.is_dir() and not any(day_dir.iterdir()):
        if args.dry_run:
          print(f"[dry-run] 删除空目录 {day_dir}")
        else:
          day_dir.rmdir()

  csv_path = var_dir / "抽检明细_20260714_APP采集.csv"
  for f in var_dir.glob("抽检明细_*_APP采集.csv"):
    csv_path = f
    break
  kept_rows, removed_rows = _dedupe_csv_rows(csv_path)

  if prompts := args.prompts_file:
    raw = load_signed_prompts(prompts)
    deduped = dedupe_signed_prompts(raw)
    print(f"签单提示词: {len(raw)} -> {len(deduped)}（去重后）")

  if not args.dry_run:
    data["completed"] = new_completed
    state_path.write_text(
      json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

  old_logs = ROOT / "logs" / "qa_capture" / var_dir.name
  if old_logs.is_dir() and not args.dry_run:
    if not any(old_logs.rglob("record.json")):
      shutil.rmtree(old_logs, ignore_errors=True)
      print(f"已删除空目录 {old_logs}")

  print(
    f"完成: 迁移 {moved}，删除冗余会话 {deleted}，"
    f"CSV {kept_rows} 行（去掉重复 {removed_rows}）"
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
