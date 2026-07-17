# -*- coding: utf-8 -*-
"""抽检 CSV 导出与签单提示词加载（单元 / 集成测试）。

覆盖场景：
- 从 CSV / xlsx 加载签单提示词、去重、试点抽样。
- 黄金样本 record → 抽检行字段绑定（意图、引用列表 schema、URL 可达性字段）。
- 已完成 keyword_id 断点续跑。

运行：
  pytest tests/test_qa_spot_check_export.py -q

前置：部分用例依赖 logs/qa_capture 黄金样本或 var/ 下签单文件，缺失时 skip。
"""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import pytest

from app.modules.qa_quality import validate_record_dict
from app.modules.qa_spot_check_export import (
  SPOT_CHECK_COLUMNS,
  SignedPromptRow,
  SpotCheckBatchMeta,
  citations_to_spot_check_list,
  count_unique_completed_keywords,
  dedupe_signed_prompts,
  extract_thinking_narrative,
  load_completed_keyword_ids,
  load_signed_prompts,
  qa_record_to_spot_check_row,
  select_pilot_rows,
)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "logs" / "qa_capture" / "2026-07-10" / "145156" / "record.json"
PROMPTS_CSV = ROOT / "var" / "vivo-x-fold6" / "签单提示词导出_20260710_183049.csv"
PROMPTS_XLSX = ROOT / "var" / "雅诗兰黛" / "签单提示词导出_20260714_000454.xlsx"


def _find_signed_by_intent(intent_name: str) -> SignedPromptRow:
  rows = load_signed_prompts(str(PROMPTS_CSV))
  for row in rows:
    if row.intent_name == intent_name:
      return row
  raise AssertionError(f"未找到意图: {intent_name}")


def test_load_signed_prompts_from_xlsx():
  """用例：雅诗兰黛 xlsx 签单应解析出 32 条且 keyword_id 以 EPN 开头。"""
  if not PROMPTS_XLSX.is_file():
    pytest.skip("雅诗兰黛签单 xlsx 不存在")
  rows = load_signed_prompts(str(PROMPTS_XLSX))
  assert len(rows) == 32
  assert rows[0].project_name
  assert rows[0].keyword_id.startswith("EPN")
  assert rows[0].prompt


def test_dedupe_signed_prompts_keeps_first():
  """用例：相同 prompt 去重时保留第一条（keyword_id=a）。"""
  rows = [
    SignedPromptRow(
      project_name="p",
      keyword_id="a",
      prompt="同一问题？",
      intent_id="i",
      intent_name="n",
      keyword_pack_id="",
      category="",
      cooperation="",
      brand_id="",
      brand_name="",
    ),
    SignedPromptRow(
      project_name="p",
      keyword_id="b",
      prompt="同一问题？",
      intent_id="i2",
      intent_name="n2",
      keyword_pack_id="",
      category="",
      cooperation="",
      brand_id="",
      brand_name="",
    ),
  ]
  out = dedupe_signed_prompts(rows)
  assert len(out) == 1
  assert out[0].keyword_id == "a"


def test_spot_check_columns_count():
  """用例：抽检 CSV 列数固定为 29（与下游表结构契约）。"""
  assert len(SPOT_CHECK_COLUMNS) == 29


def test_select_pilot_rows_covers_multiple_intents():
  """用例：试点抽样 10 条应覆盖至少 8 个不同意图（分布均匀）。"""
  rows = load_signed_prompts(str(PROMPTS_CSV))
  pilot = select_pilot_rows(rows, 10)
  assert len(pilot) == 10
  assert len({r.intent_name for r in pilot}) >= 8


def test_extract_thinking_narrative_keywords_fallback():
  """用例：思考 markdown 含重复标题时，仍能抽出「搜索关键词」叙事段落。"""
  md = (
    "## 搜索 3 个关键词，参考 15 篇资料\n\n"
    "### 思考过程\n\n"
    "搜索 3 个关键词，参考 15 篇资料\n\n"
    "### 搜索 3 个关键词，参考 15 篇资料\n\n"
    "**搜索关键词：** “折叠屏手机选购指南”\n"
  )
  text = extract_thinking_narrative(md)
  assert "搜索关键词" in text
  assert "折叠屏手机选购指南" in text


def test_citations_to_spot_check_list_schema():
  """用例：引用列表 JSON 含 webUrl 与 URL 可达性四字段（含 urlNum）。"""
  if not GOLDEN.is_file():
    pytest.skip("黄金样本不存在")
  record = json.loads(GOLDEN.read_text(encoding="utf-8"))
  cites = citations_to_spot_check_list(record["thinking_references"])
  assert cites
  first = cites[0]
  assert set(first.keys()) == {
    "source", "title", "urlNum", "webUrl",
    "webUrlReachable", "urlCheckStatus", "urlHttpStatus", "urlCheckNote",
  }
  assert first["webUrl"].startswith("http")


def test_qa_record_to_spot_check_row_intent_binding():
  """用例：record + 签单行 → CSV 字典，意图/词包/平台字段与签单一致。"""
  if not GOLDEN.is_file():
    pytest.skip("黄金样本不存在")
  if not PROMPTS_CSV.is_file():
    pytest.skip("签单 CSV 不存在")

  record = json.loads(GOLDEN.read_text(encoding="utf-8"))
  signed = _find_signed_by_intent("折叠手机推荐")
  report = validate_record_dict(record)

  meta = SpotCheckBatchMeta(task_code="TNTEST", next_detail_id=900001, check_date="2026-07-10")
  row = qa_record_to_spot_check_row(
    signed,
    answer_body=record["answer_body"],
    thinking=record["thinking"],
    thinking_references=record["thinking_references"],
    quality_report=report,
    meta=meta,
  )

  data = row.to_csv_dict()
  assert set(data.keys()) == set(SPOT_CHECK_COLUMNS)
  assert data["意图名称"] == signed.intent_name
  assert data["意图编号"] == signed.intent_id
  assert data["词包编号"] == signed.keyword_pack_id
  assert data["关键词编号"] == signed.keyword_id
  assert data["AI平台代码"] == "DB"
  assert data["终端平台"] == "APP"

  cites = json.loads(data["引用列表"])
  assert isinstance(cites, list)
  assert all("urlNum" in c and "webUrl" in c for c in cites)


def test_load_completed_keyword_ids_roundtrip():
  """用例：写入抽检 CSV 后 load_completed_keyword_ids 能读回 keyword_id 用于 --resume。"""
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "out.csv"
    from app.modules.qa_spot_check_export import append_csv_row, SpotCheckRow

    row = SpotCheckRow(
      project_name="vivo-X Fold6",
      check_date="2026-07-10",
      detail_id=1,
      detail_code="TD1",
      task_code="TN1",
      prompt="测试问",
      channel_keyword="测试问",
      keyword_id="KPN123",
      checked_at="2026-07-10 12:00:00",
      modified_at="2026-07-10 12:00:00",
      quality_grade="S",
      intent_name="测试意图",
      intent_id="UI1",
      keyword_pack_id="KC1",
      category="PL",
      cooperation="Cooperative",
      brand_id="BCN1",
      brand_name="vivo-X Fold6",
      answer_body="正文",
      thinking_body="思考",
      citations_json="[]",
    )
    append_csv_row(str(path), row)
    done = load_completed_keyword_ids(str(path))
    assert "KPN123" in done
    assert count_unique_completed_keywords(str(path)) == 1


def test_load_failure_counts():
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "failures.jsonl"
    path.write_text(
      '{"keyword_id":"K1","error":"x"}\n'
      '{"keyword_id":"K1","error":"y"}\n'
      '{"keyword_id":"K2","error":"z"}\n',
      encoding="utf-8",
    )
    from app.modules.qa_spot_check_export import load_failure_counts

    counts = load_failure_counts(str(path))
    assert counts == {"K1": 2, "K2": 1}
