# -*- coding: utf-8 -*-
"""问答长截图：帧间重叠不足会导致漏截（非拼接算法问题）。

覆盖场景：
- 真机长截图滑动步长过大时，相邻帧重叠低于 profile.qa_shot_min_overlap_frac，
  正文落在两帧缝隙之间（vivo-x-fold6 会话 203935 坏样本）。

运行：
  pytest tests/test_qa_longshot_overlap.py -q

前置：本地存在 var/vivo-x-fold6/spot_check/20260714/.../203935/shot_*.png；
无样本时跳过（不 fail）。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.config.profile_loader import load_profile
from app.modules.detail_strip_stitch import (
  crop_fullscreen_to_detail_content,
  estimate_vertical_overlap_px,
)


ROOT = Path(__file__).resolve().parents[1]


def _qa_profile():
  p = load_profile(device_name="vivo_x_fold6")
  return replace(
    p,
    fc_detail_roi_y0=p.qa_shot_roi_y0,
    fc_detail_roi_y1=p.qa_shot_roi_y1,
    fc_detail_strip_roi_x0=0.0,
    fc_detail_strip_roi_x1=1.0,
  )


def test_session_203935_shot12_overlap_too_small_indicates_capture_gap() -> None:
  """用例：203935 会话 shot_01→shot_02 重叠远低于阈值，证明是采集步长问题而非拼接 bug。

  步骤：裁剪 ROI 后 estimate_vertical_overlap_px(shot_01, shot_02)。
  断言：overlap < min_ok 且 < 帧高 20%（坏样本特征；样本修复后本用例会失败提醒）。
  """
  session = (
    ROOT
    / "var"
    / "vivo-x-fold6"
    / "spot_check"
    / "20260714"
    / "qa_capture"
    / "2026-07-14"
    / "203935"
  )
  if not session.is_dir():
    return

  profile = _qa_profile()
  s1 = session / "shot_01.png"
  s2 = session / "shot_02.png"
  if not s1.is_file() or not s2.is_file():
    return

  c1 = crop_fullscreen_to_detail_content(str(s1), profile)
  c2 = crop_fullscreen_to_detail_content(str(s2), profile)
  overlap = estimate_vertical_overlap_px(c1, c2)
  min_ok = int(c1.height * profile.qa_shot_min_overlap_frac)
  # 记录坏样本特征：重叠明显低于阈值
  assert overlap < min_ok, (
    f"样本已修复或阈值过严: overlap={overlap}px min={min_ok}px"
  )
  assert overlap < int(c1.height * 0.20), f"shot1->2 重叠应 <20%: {overlap}px"
