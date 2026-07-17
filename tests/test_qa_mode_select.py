# -*- coding: utf-8 -*-
"""模式选择：fast 不得落到专家；额度提示识别。"""

from app.modules.qa_capture import DoubaoQaCapture, QA_QUOTA_ANSWER_MARKERS


def test_quota_answer_markers_match_real_copy():
  body = (
    "本月免费额度已用完，暂时无法使用专业版功能，"
    "先使用快速模式和我聊聊别的吧。开通豆包专业版，免等待，继续为你服务。"
  )
  assert any(m in body for m in QA_QUOTA_ANSWER_MARKERS)


def test_answer_looks_like_quota_block():
  cap = DoubaoQaCapture.__new__(DoubaoQaCapture)
  assert cap._answer_looks_like_quota_block(
    "本月免费额度已用完，暂时无法使用专业版功能"
  )
  assert not cap._answer_looks_like_quota_block("vivo X Fold6 长焦表现不错")
