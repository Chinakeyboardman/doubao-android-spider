# -*- coding: utf-8 -*-
"""引用 URL 可达性探测（单元测试，mock HTTP，无需真机）。

覆盖场景：
- 抽检导出前对 webUrl 做 HEAD 探测，区分「站点 404/5xx」与采集系统故障。
- 结果写回 Citation 的 url_reachable / url_check_* 字段。

运行：
  pytest tests/test_qa_url_reachability.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.qa_hierarchy import Citation
from app.modules.qa_url_reachability import (
  apply_url_reachability,
  probe_url_reachability,
  summarize_unreachable,
)


def _mock_resp(status: int, url: str = "https://example.com/page"):
  r = MagicMock()
  r.status_code = status
  r.url = url
  return r


@patch("app.modules.qa_url_reachability.requests.head")
def test_probe_ok_200(mock_head):
  """用例：HTTP 200 应标记 reachable=True、status=ok。"""
  mock_head.return_value = _mock_resp(200)
  result = probe_url_reachability("https://example.com/a")
  assert result.reachable is True
  assert result.status == "ok"
  assert result.http_status == 200


@patch("app.modules.qa_url_reachability.requests.head")
def test_probe_404_is_site_issue_not_system(mock_head):
  """用例：404 归为站点问题（http_404），note 说明非豆包采集故障。"""
  mock_head.return_value = _mock_resp(404, "https://example.com/missing")
  result = probe_url_reachability("https://example.com/missing")
  assert result.reachable is False
  assert result.status == "http_404"
  assert "404" in result.note
  assert "豆包" in result.note or "站点" in result.note


@patch("app.modules.qa_url_reachability.requests.head")
def test_probe_500_site_error(mock_head):
  """用例：5xx 归为站点错误，reachable=False。"""
  mock_head.return_value = _mock_resp(503)
  result = probe_url_reachability("https://news.site/article")
  assert result.reachable is False
  assert result.status == "http_503"


@patch("app.modules.qa_url_reachability.requests.head")
def test_apply_writes_citation_fields(mock_head):
  """用例：apply_url_reachability 将探测结果写入 Citation 扩展字段。"""
  mock_head.return_value = _mock_resp(404)
  cite = Citation(title="t", url="https://x.com/y", ref_index=3)
  apply_url_reachability(cite)
  assert cite.url_reachable is False
  assert cite.url_check_status == "http_404"
  assert cite.url_http_status == 404
  assert cite.url_check_note


def test_summarize_unreachable():
  """用例：summarize_unreachable 统计已探测条数与不可达条数。"""
  refs = [
    Citation(title="a", url="https://a", url_check_status="ok", url_reachable=True),
    Citation(
      title="b", url="https://b", url_check_status="http_404", url_reachable=False,
    ),
    Citation(title="c", url="https://c"),
  ]
  checked, bad = summarize_unreachable(refs)
  assert checked == 2
  assert bad == 1
