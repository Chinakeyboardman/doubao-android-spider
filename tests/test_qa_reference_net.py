# -*- coding: utf-8 -*-
"""qa_reference_net 单元测试（mitm 落盘 JSON 解析，无需真机）。"""

from __future__ import annotations

import json
from pathlib import Path

from app.modules.qa_hierarchy import Citation
from app.modules.qa_reference_net import (
  align_net_urls_to_citations,
  load_net_references_from_dir,
  parse_reference_json_text,
)


SAMPLE_PAYLOAD = {
  "data": {
    "references": [
      {
        "title": "1500元左右的智能手机价格报价行情 - 京东",
        "link_url": "https://www.jd.com/jiage/9987.html",
        "source": "京东",
      },
      {
        "title": "六月份1500左右各品牌手机大推荐",
        "doc_id": "7650085520299273595",
      },
    ]
  }
}


def test_parse_reference_json_text():
  refs = parse_reference_json_text(json.dumps(SAMPLE_PAYLOAD))
  assert len(refs) >= 2
  jd = [r for r in refs if "京东" in r.title][0]
  assert jd.url.startswith("https://www.jd.com/")
  dy = [r for r in refs if "六月份" in r.title][0]
  assert "7650085520299273595" in dy.url


def test_align_net_urls_to_citations():
  citations = [
    Citation(title="1500元左右的智能手机价格报价行情 - 京东", ref_index=6),
    Citation(title="六月份1500左右各品牌手机大推荐！ 1500元档", ref_index=1),
  ]
  net_refs = parse_reference_json_text(json.dumps(SAMPLE_PAYLOAD))
  out = align_net_urls_to_citations(citations, net_refs)
  assert out[0].url.startswith("https://www.jd.com/")
  assert "7650085520299273595" in out[1].url


def test_load_net_references_from_dir(tmp_path: Path):
  (tmp_path / "sample.json").write_text(
    json.dumps({"body": json.dumps(SAMPLE_PAYLOAD)}),
    encoding="utf-8",
  )
  refs = load_net_references_from_dir(tmp_path)
  assert len(refs) >= 2
