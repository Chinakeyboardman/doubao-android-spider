# -*- coding: utf-8 -*-
"""Navigator 阻塞弹窗同意逻辑单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.modules.navigator import Navigator


class NavigatorConsentPromptsTest(unittest.TestCase):
  def setUp(self) -> None:
    self.nav = Navigator(MagicMock())

  def test_positive_labels(self) -> None:
    pos = (
      "允许",
      "仅在使用该应用时允许",
      "允许通知",
      "立即体验",
      "去授权",
      "请允许录音权限",
    )
    for t in pos:
      self.assertTrue(self.nav._is_consent_positive_label(t), t)

  def test_negative_labels(self) -> None:
    neg = ("取消", "拒绝", "忽略", "暂不", "不同意", "以后再说")
    for t in neg:
      self.assertFalse(self.nav._is_consent_positive_label(t), t)

  def test_dismiss_push_reminder_dialog_clicks_close(self) -> None:
    title_el = MagicMock()
    close_el = MagicMock()
    self.nav.d.xpath = MagicMock(
      side_effect=lambda sel: MagicMock(
        get=MagicMock(
          side_effect=(
            lambda timeout=0: title_el
            if "tv_push_reminder_dialog_title" in sel
            else (close_el if "iv_push_reminder_dialog_close" in sel else None)
          )
        )
      )
    )
    self.assertTrue(self.nav.dismiss_push_reminder_dialog())
    close_el.click.assert_called_once()

  def test_dismiss_push_reminder_dialog_absent(self) -> None:
    self.nav.d.xpath = MagicMock(
      return_value=MagicMock(get=MagicMock(return_value=None))
    )
    self.assertFalse(self.nav.dismiss_push_reminder_dialog())

if __name__ == "__main__":
  unittest.main()
