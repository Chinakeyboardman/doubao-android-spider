# -*- coding: utf-8 -*-
"""回答正文选取逻辑（无需真机）。"""

from __future__ import annotations

from app.modules.qa_capture import DoubaoQaCapture


def _cap() -> DoubaoQaCapture:
  return DoubaoQaCapture.__new__(DoubaoQaCapture)


def test_pick_prefers_long_answer_over_reference_title():
  cap = _cap()
  ref = "七月份各品牌1500左右最值得买的游戏、拍照手机大推荐！ #游戏手机 #千元机 #"
  answer = "2026年1500元学生党手机推荐\n\n.. 红米 Note14 Pro（约1299元）\n\n核心：天玑7300"
  picked = cap._pick_best_answer_body(
    ref,
    answer,
    prompt="请推荐手机",
    ref_titles=[ref],
  )
  assert "红米" in picked
  assert len(picked) > len(ref)


def test_pick_early_clipboard_over_late_reference():
  cap = _cap()
  early = "x" * 500
  late = "六月份1500左右各品牌手机大推荐！ #千元机 #性价比手机 #"
  picked = cap._pick_best_answer_body(
    early,
    late,
    prompt="test",
    ref_titles=[late],
  )
  assert picked == early
