# -*- coding: utf-8 -*-
"""spot_check_claims 单元测试。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app.modules.qa_spot_check_export import SpotCheckRow
from app.modules.spot_check_claims import (
  ClaimRecord,
  append_csv_row_locked,
  claim_task,
  list_claims,
  load_completed_keyword_ids_locked,
  prune_claims_for_completed,
  release_task,
)


class SpotCheckClaimsTest(unittest.TestCase):
  def setUp(self) -> None:
    self.tmp = tempfile.TemporaryDirectory()
    self.claims_dir = os.path.join(self.tmp.name, "claims")
    os.makedirs(self.claims_dir, exist_ok=True)

  def tearDown(self) -> None:
    self.tmp.cleanup()

  def test_claim_success_and_duplicate(self) -> None:
    ok = claim_task(self.claims_dir, "kid-001", worker_id="w-a")
    self.assertTrue(ok)
    self.assertEqual(len(list_claims(self.claims_dir)), 1)

    dup = claim_task(self.claims_dir, "kid-001", worker_id="w-b")
    self.assertFalse(dup)

  def test_release_and_reclaim(self) -> None:
    self.assertTrue(claim_task(self.claims_dir, "kid-002", worker_id="w-a"))
    self.assertFalse(release_task(self.claims_dir, "kid-002", worker_id="w-b"))
    self.assertTrue(release_task(self.claims_dir, "kid-002", worker_id="w-a"))
    self.assertTrue(claim_task(self.claims_dir, "kid-002", worker_id="w-b"))

  def test_stale_claim_takeover(self) -> None:
    path = os.path.join(self.claims_dir, "kid-003.json")
    stale_at = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    record = ClaimRecord(
      keyword_id="kid-003",
      worker_id="dead-worker",
      pid=999999,
      claimed_at=stale_at,
    )
    with open(path, "w", encoding="utf-8") as f:
      import json

      json.dump(record.to_dict(), f)

    ok = claim_task(
      self.claims_dir,
      "kid-003",
      worker_id="w-new",
      stale_sec=60.0,
    )
    self.assertTrue(ok)
    claims = list_claims(self.claims_dir)
    self.assertEqual(len(claims), 1)
    self.assertEqual(claims[0].worker_id, "w-new")

  def test_dead_pid_claim_immediate_takeover(self) -> None:
    path = os.path.join(self.claims_dir, "kid-006.json")
    recent_at = datetime.now().isoformat(timespec="seconds")
    record = ClaimRecord(
      keyword_id="kid-006",
      worker_id="crashed-worker",
      pid=999999,
      claimed_at=recent_at,
    )
    with open(path, "w", encoding="utf-8") as f:
      import json

      json.dump(record.to_dict(), f)

    ok = claim_task(
      self.claims_dir,
      "kid-006",
      worker_id="w-new",
      stale_sec=3600.0,
    )
    self.assertTrue(ok)
    claims = list_claims(self.claims_dir)
    self.assertEqual(claims[0].worker_id, "w-new")

  def test_append_csv_row_locked(self) -> None:
    csv_path = os.path.join(self.tmp.name, "out.csv")
    row = SpotCheckRow(keyword_id="kid-004", prompt="p", answer_body="a")
    self.assertTrue(append_csv_row_locked(csv_path, row))
    self.assertFalse(append_csv_row_locked(csv_path, row))
    with open(csv_path, encoding="utf-8-sig") as f:
      content = f.read()
    self.assertEqual(content.count("kid-004"), 1)

  def test_prune_claims_for_completed(self) -> None:
    self.assertTrue(claim_task(self.claims_dir, "kid-done", worker_id="w-a"))
    self.assertTrue(claim_task(self.claims_dir, "kid-open", worker_id="w-b"))
    removed = prune_claims_for_completed(self.claims_dir, {"kid-done"})
    self.assertEqual(removed, ["kid-done"])
    remaining = {c.keyword_id for c in list_claims(self.claims_dir)}
    self.assertEqual(remaining, {"kid-open"})

  def test_load_completed_keyword_ids_locked(self) -> None:
    csv_path = os.path.join(self.tmp.name, "locked.csv")
    row = SpotCheckRow(keyword_id="kid-lock", prompt="p", answer_body="a")
    append_csv_row_locked(csv_path, row)
    done = load_completed_keyword_ids_locked(csv_path)
    self.assertIn("kid-lock", done)

  def test_concurrent_claim_only_one_winner(self) -> None:
    import threading

    claims_dir = self.claims_dir
    results: list[tuple[str, bool]] = []
    lock = threading.Lock()

    def _worker(worker_id: str) -> None:
      ok = claim_task(claims_dir, "kid-005", worker_id=worker_id)
      with lock:
        results.append((worker_id, ok))

    threads = [threading.Thread(target=_worker, args=(f"w-{i}",)) for i in range(8)]
    for t in threads:
      t.start()
    for t in threads:
      t.join(timeout=10)

    winners = [wid for wid, ok in results if ok]
    self.assertEqual(len(winners), 1)
    self.assertEqual(len(list_claims(self.claims_dir)), 1)


if __name__ == "__main__":
  unittest.main()
