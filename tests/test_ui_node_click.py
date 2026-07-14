# -*- coding: utf-8 -*-
"""ui_node_click 单元测试（无需真机）。"""

from __future__ import annotations

from app.modules.ui_node_click import (
  _overlap_ratio,
  _point_in,
  citation_index_xpath,
  citation_row_xpath,
  title_prefix_match,
)


def test_point_in_and_overlap():
  box = [0, 0, 100, 100]
  assert _point_in(box, 50, 50)
  assert not _point_in(box, 200, 50)
  assert _overlap_ratio([0, 0, 50, 50], [25, 25, 75, 75]) > 0.2
  assert _overlap_ratio([0, 0, 10, 10], [50, 50, 60, 60]) == 0.0


def test_title_prefix_match():
  assert title_prefix_match("OPPO Find N6 产品参数", "OPPO Find N6 产品参数 | 官网")
  assert not title_prefix_match("OPPO Find N6", "vivo X Fold6")


def test_citation_xpath_builders():
  root = '//*[@resource-id="com.larus.nova:id/search_reference_list"]'
  assert 'tv_reference_index' in citation_index_xpath(root, 14)
  assert '@text="14."' in citation_index_xpath(root, 14)
  assert "ll_source_item" in citation_row_xpath(root, 14)
