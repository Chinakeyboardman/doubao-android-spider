# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path

from app.modules.qa_quality import (
  DEFAULT_MIN_URL_RESOLVE_RATIO,
  GOLDEN_MODE,
  GOLDEN_PROMPT,
  _min_urls_required,
  validate_qa_session,
  validate_record_dict,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "logs" / "qa_capture" / "2026-07-10" / "145156" / "record.json"


def _refs_with_urls(n: int, total: int) -> list[dict]:
  return [{"url": f"http://ex/{i}"} if i < n else {"url": ""} for i in range(total)]


def _passing_session_kwargs(**overrides):
  base = dict(
    session_dir="/tmp/t",
    answer_body="x" * 80,
    thinking="思考内容",
    screenshots=["/tmp/a.png"],
    stitched_screenshot=__file__,
    mode="fast",
  )
  base.update(overrides)
  return base


def test_golden_sample_passes_quality():
  if not GOLDEN.is_file():
    return
  record = json.loads(GOLDEN.read_text(encoding="utf-8"))
  report = validate_record_dict(record)
  assert report.ref_count >= 10
  assert report.url_count == report.ref_count
  assert report.ok
  assert record.get("mode") == GOLDEN_MODE
  assert record.get("prompt") == GOLDEN_PROMPT


def test_empty_record_fails():
  report = validate_record_dict({})
  assert not report.ok
  assert report.score < 50


def test_min_urls_required_half():
  assert _min_urls_required(10, 0.5) == 5
  assert _min_urls_required(18, 0.5) == 9
  assert _min_urls_required(11, 0.5) == 6


def test_url_ratio_exactly_half_passes():
  report = validate_qa_session(
    **_passing_session_kwargs(
      thinking_references=_refs_with_urls(5, 10),
    ),
    min_url_resolve_ratio=DEFAULT_MIN_URL_RESOLVE_RATIO,
    require_all_urls=False,
  )
  assert report.url_count == 5
  assert report.ref_count == 10
  assert report.ok


def test_url_ratio_more_than_half_missing_fails():
  report = validate_qa_session(
    **_passing_session_kwargs(
      thinking_references=_refs_with_urls(4, 10),
    ),
    min_url_resolve_ratio=DEFAULT_MIN_URL_RESOLVE_RATIO,
    require_all_urls=False,
  )
  assert not report.ok
  url_check = next(c for c in report.checks if c[0] == "引用 URL")
  assert not url_check[1]


def test_no_search_refs_with_good_answer_passes():
  from app.modules.qa_spot_check_export import quality_grade_from_report

  report = validate_qa_session(
    **_passing_session_kwargs(
      thinking="",
      thinking_references=[],
    ),
    allow_no_references=True,
  )
  assert report.ref_count == 0
  assert report.ok
  assert report.score >= 80
  assert quality_grade_from_report(report) == "A"


def test_allow_partial_douyin_applies_ratio_to_douyin_only_refs():
  """仅抖音引用时 allow_partial 与 50% 比例联合判定，不再被全量比例抢先否决。"""
  refs = [
    {"title": f"#折叠屏推荐话题{i}", "url": f"https://www.iesdouyin.com/v/{i}" if i < 5 else ""}
    for i in range(9)
  ]
  report = validate_qa_session(
    **_passing_session_kwargs(thinking_references=refs),
    allow_missing_douyin_urls=True,
    min_url_resolve_ratio=DEFAULT_MIN_URL_RESOLVE_RATIO,
    require_all_urls=False,
  )
  assert report.url_count == 5
  assert report.ref_count == 9
  assert report.ok
