# -*- coding: utf-8 -*-
"""多机抽检任务原子认领与 CSV 安全追加。"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Iterator

from app.modules.qa_spot_check_export import (
  SPOT_CHECK_COLUMNS,
  SpotCheckRow,
  ensure_csv_header,
  load_completed_keyword_ids,
)

_CLAIM_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class ClaimRecord:
  """单条任务认领记录。"""

  keyword_id: str
  worker_id: str
  pid: int
  claimed_at: str

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> ClaimRecord:
    return cls(
      keyword_id=str(data.get("keyword_id") or ""),
      worker_id=str(data.get("worker_id") or ""),
      pid=int(data.get("pid") or 0),
      claimed_at=str(data.get("claimed_at") or ""),
    )


def _sanitize_claim_filename(keyword_id: str) -> str:
  safe = _CLAIM_FILENAME_RE.sub("_", keyword_id).strip("._")
  return safe or "unknown"


def claim_path(claims_dir: str, keyword_id: str) -> str:
  return os.path.join(claims_dir, f"{_sanitize_claim_filename(keyword_id)}.json")


def _is_pid_alive(pid: int) -> bool:
  if pid <= 0:
    return False
  try:
    os.kill(pid, 0)
    return True
  except OSError:
    return False


def _read_claim(path: str) -> ClaimRecord | None:
  if not os.path.isfile(path):
    return None
  try:
    with open(path, encoding="utf-8") as f:
      data = json.load(f)
    if not isinstance(data, dict):
      return None
    return ClaimRecord.from_dict(data)
  except (OSError, json.JSONDecodeError, TypeError, ValueError):
    return None


def _write_claim(path: str, record: ClaimRecord) -> None:
  os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
  with open(path, "w", encoding="utf-8") as f:
    json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
    f.flush()
    os.fsync(f.fileno())


def _claim_is_stale(record: ClaimRecord, *, stale_sec: float) -> bool:
  """进程已死可立即重认领；进程仍存活则按 claimed_at + stale_sec 判定挂死。"""
  if _is_pid_alive(record.pid):
    if stale_sec <= 0:
      return False
    if not record.claimed_at:
      return False
    try:
      claimed_ts = datetime.fromisoformat(record.claimed_at).timestamp()
    except ValueError:
      return False
    return (time.time() - claimed_ts) >= stale_sec
  return True


def claim_task(
  claims_dir: str,
  keyword_id: str,
  *,
  worker_id: str,
  stale_sec: float = 3600.0,
) -> bool:
  """
  原子认领任务。成功返回 True；已被他人有效占用返回 False。
  """
  if not keyword_id:
    return False

  os.makedirs(claims_dir, exist_ok=True)
  path = claim_path(claims_dir, keyword_id)
  record = ClaimRecord(
    keyword_id=keyword_id,
    worker_id=worker_id,
    pid=os.getpid(),
    claimed_at=datetime.now().isoformat(timespec="seconds"),
  )

  try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
  except FileExistsError:
    existing: ClaimRecord | None = None
    for _ in range(10):
      existing = _read_claim(path)
      if existing is not None:
        break
      time.sleep(0.02)
    if existing is None:
      return False
    if existing.worker_id == worker_id and _is_pid_alive(existing.pid):
      return True
    if not _claim_is_stale(existing, stale_sec=stale_sec):
      return False
    try:
      os.remove(path)
    except OSError:
      return False
    return claim_task(
      claims_dir,
      keyword_id,
      worker_id=worker_id,
      stale_sec=stale_sec,
    )

  try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
      json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)
      f.flush()
      os.fsync(f.fileno())
    return True
  except OSError:
    try:
      os.remove(path)
    except OSError:
      pass
    return False


def release_task(claims_dir: str, keyword_id: str, *, worker_id: str) -> bool:
  """释放认领（仅允许原 worker 删除）。"""
  path = claim_path(claims_dir, keyword_id)
  existing = _read_claim(path)
  if existing is None:
    return True
  if existing.worker_id != worker_id:
    return False
  try:
    os.remove(path)
    return True
  except OSError:
    return False


def list_claims(claims_dir: str) -> list[ClaimRecord]:
  if not os.path.isdir(claims_dir):
    return []
  out: list[ClaimRecord] = []
  for name in sorted(os.listdir(claims_dir)):
    if not name.endswith(".json"):
      continue
    record = _read_claim(os.path.join(claims_dir, name))
    if record is not None:
      out.append(record)
  return out


@contextmanager
def _csv_file_lock(csv_path: str) -> Iterator[None]:
  ensure_csv_header(csv_path)
  lock_path = f"{csv_path}.lock"
  os.makedirs(os.path.dirname(os.path.abspath(lock_path)) or ".", exist_ok=True)
  with open(lock_path, "a+", encoding="utf-8") as lock_f:
    import fcntl

    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
    try:
      yield
    finally:
      fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def load_completed_keyword_ids_locked(csv_path: str) -> set[str]:
  """在 flock 保护下读取已完成 keyword_id（与追加落盘互斥）。"""
  if not os.path.isfile(csv_path):
    return set()
  with _csv_file_lock(csv_path):
    return load_completed_keyword_ids(csv_path)


def prune_claims_for_completed(claims_dir: str, completed_ids: set[str]) -> list[str]:
  """删除已完成任务上的陈旧 claim 文件，返回被清理的 keyword_id。"""
  if not completed_ids or not os.path.isdir(claims_dir):
    return []
  removed: list[str] = []
  for name in os.listdir(claims_dir):
    if not name.endswith(".json"):
      continue
    path = os.path.join(claims_dir, name)
    record = _read_claim(path)
    if record is None or record.keyword_id not in completed_ids:
      continue
    try:
      os.remove(path)
      removed.append(record.keyword_id)
    except OSError:
      pass
  return removed


def append_csv_row_locked(csv_path: str, row: SpotCheckRow) -> bool:
  """
  在 flock 保护下追加 CSV 行。

  若 keyword_id 已存在则跳过（返回 False），避免多机重复落盘。
  """
  kid = (row.keyword_id or "").strip()
  if not kid:
    return False
  with _csv_file_lock(csv_path):
    if kid in load_completed_keyword_ids(csv_path):
      return False
    with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
      writer = csv.DictWriter(f, fieldnames=list(SPOT_CHECK_COLUMNS))
      writer.writerow(row.to_csv_dict())
      f.flush()
      os.fsync(f.fileno())
  return True
