# -*- coding: utf-8 -*-
"""qa_hierarchy 单元测试（使用侦察样本 XML，无需真机）。"""

from __future__ import annotations

from pathlib import Path

from app.modules.qa_hierarchy import (
  Citation,
  ParsedThinkingPanel,
  ThinkingSearchGroup,
  parse_exchange_from_hierarchy,
  parse_thinking_panel,
  render_thinking_markdown,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_THREAD = ROOT / "logs" / "recon_thread_hierarchy.xml"
SAMPLE_THINKING = ROOT / "logs" / "recon4_ref_expanded.xml"


def test_parse_recon_thread_hierarchy():
  if not SAMPLE_THREAD.is_file():
    return
  xml = SAMPLE_THREAD.read_text(encoding="utf-8")
  parsed = parse_exchange_from_hierarchy(
    xml,
    prompt_text="商务手机",
    screen_w=1080,
  )
  assert parsed.answer_body
  assert len(parsed.raw_texts) > 5
  assert any("相关视频" in c.title or "抖音" in c.desc for c in parsed.citations)


def test_parse_empty_xml():
  parsed = parse_exchange_from_hierarchy("<hierarchy></hierarchy>")
  assert parsed.question_text == ""
  assert parsed.raw_texts == []


def test_parse_thinking_panel_with_references():
  if not SAMPLE_THINKING.is_file():
    return
  xml = SAMPLE_THINKING.read_text(encoding="utf-8")
  panel = parse_thinking_panel(xml)
  assert "已完成思考" in panel.header or panel.thinking_body
  assert len(panel.references) >= 5
  assert len(panel.thinking_paragraphs) >= 1
  assert panel.groups, "应解析出至少一个搜索引用组"
  assert all(g.title for g in panel.groups)
  assert any(r.group for r in panel.references), "引用应带 group 字段"
  web_refs = [r for r in panel.references if r.source or "太平洋" in r.title]
  assert web_refs, "应至少解析出一条带来源/网页标题的引用"
  assert any(r.ref_index > 0 for r in panel.references)


def test_render_thinking_markdown_dedupes_header_and_keywords():
  title = "搜索 3 个关键词，参考 13 篇资料"
  keywords = "“折叠屏推荐 2026”、“主流折叠屏对比”"
  panel = ParsedThinkingPanel(
    header=title,
    thinking_paragraphs=[title, keywords],
    groups=[
      ThinkingSearchGroup(
        title=title,
        key=f"{title}|316",
        keywords=keywords,
        references=[Citation(ref_index=1, title="引用一")],
      ),
    ],
  )
  md = render_thinking_markdown(panel)
  assert md.count(f"### {title}") == 1
  assert md.count("**搜索关键词：**") == 1
  assert md.count("**参考资料：**") == 1


def test_render_thinking_markdown():
  if not SAMPLE_THINKING.is_file():
    return
  panel = parse_thinking_panel(SAMPLE_THINKING.read_text(encoding="utf-8"))
  md = render_thinking_markdown(panel)
  assert "### 思考过程" in md or "##" in md
  assert "搜索" in md
  for grp in panel.groups:
    assert grp.title in md
