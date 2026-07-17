# -*- coding: utf-8 -*-
"""Navigator 强杀重启（单元测试，无需真机）。

覆盖场景：
- URL 解析后会话漂移、卡在 WebActivity / 外部 App 时，通过 force-stop 清栈再冷启动。

运行：
  pytest tests/test_navigator_hard_restart.py -q
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.navigator import Navigator, PACKAGE


def test_hard_restart_app_force_stops_then_starts() -> None:
  """用例：hard_restart_app 应先 app_stop 再 app_start 豆包包名。

  断言：各调用一次且参数为 PACKAGE；不依赖 UI 真机。
  """
  d = MagicMock()
  nav = Navigator(d)
  nav.hard_restart_app(reason="test")
  d.app_stop.assert_called_once_with(PACKAGE)
  d.app_start.assert_called_once_with(PACKAGE)
