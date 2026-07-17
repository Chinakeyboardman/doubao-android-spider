# -*- coding: utf-8 -*-
"""聊天输入/发送启发式单元测试。"""

from __future__ import annotations

import unittest

from app.modules.chat_ui_heuristics import chat_input_contains


class ChatInputTest(unittest.TestCase):
    def test_chat_input_contains_prefix(self) -> None:
        class _El:
            def __init__(self, text: str) -> None:
                self.info = {"text": text}

        class _Dev:
            def xpath(self, sel: str) -> "_XPath":
                return _XPath()

        class _XPath:
            def get(self, timeout: float = 0) -> _El:
                return _El("大折叠手机综合体验最好推荐哪款")

        self.assertTrue(
            chat_input_contains(_Dev(), "大折叠手机综合体验最好推荐哪款，好用又不贵？")
        )


if __name__ == "__main__":
    unittest.main()
