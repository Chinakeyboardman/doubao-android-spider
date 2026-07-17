# -*- coding: utf-8 -*-
"""Navigator 会话抽屉「搜索」页关闭逻辑单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.navigator import Navigator


def test_dismiss_conversation_search_clicks_cancel() -> None:
  d = MagicMock()
  nav = Navigator(d)
  cancel = MagicMock()
  with patch.object(nav, "_conversation_search_open", side_effect=[True, False]):
    with patch.object(d, "xpath", return_value=MagicMock(get=MagicMock(return_value=cancel))):
      assert nav.dismiss_conversation_search() is True
  cancel.click.assert_called_once()


def test_open_conversation_drawer_skips_when_search_then_list_visible() -> None:
  d = MagicMock()
  nav = Navigator(d)
  with patch.object(nav, "dismiss_conversation_search") as dismiss:
    with patch.object(nav, "_conversation_drawer_open", return_value=True):
      assert nav._open_conversation_drawer() is True
  dismiss.assert_called_once()


def test_conversation_search_open_detects_combine_search_activity() -> None:
  d = MagicMock()
  d.app_current.return_value = {
    "activity": "com.larus.search.impl.combine.CombineSearchActivity",
  }
  nav = Navigator(d)
  assert nav._conversation_search_open() is True
