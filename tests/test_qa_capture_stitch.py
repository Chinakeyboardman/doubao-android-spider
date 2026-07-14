# -*- coding: utf-8 -*-
"""问答长图拼接：回答正文 shot_* 帧拼接（无需真机）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app.config.gesture_profile import GestureProfile
from app.modules.detail_strip_stitch import (
  crop_fullscreen_to_detail_content,
  stitch_content_strips_vertical,
)


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "logs" / "qa_capture" / "2026-07-10" / "145156"
BROKEN = ROOT / "logs" / "qa_capture" / "2026-07-12" / "065256"


def _qa_profile() -> GestureProfile:
  p = GestureProfile()
  return replace(
    p,
    fc_detail_roi_y0=p.qa_shot_roi_y0,
    fc_detail_roi_y1=p.qa_shot_roi_y1,
    fc_detail_strip_roi_x0=0.0,
    fc_detail_strip_roi_x1=1.0,
  )


def test_shot_frames_stitch_taller_than_single_screen() -> None:
  """黄金样本多屏 shot_* 拼接应明显高于单屏。"""
  if not GOLDEN.is_dir():
    return

  profile = _qa_profile()
  shot_paths = sorted(GOLDEN.glob("shot_*.png"))
  if len(shot_paths) < 2:
    return

  crops = [crop_fullscreen_to_detail_content(str(p), profile) for p in shot_paths]
  single = crops[0]
  stitched = stitch_content_strips_vertical(crops)

  assert stitched.height > single.height * 2


def test_longshot_uses_shot_not_expand_refs() -> None:
  """
  full.png 应来自 shot_*（外层滚动），不应误用 screen_expand_*_refs_*（嵌套引用滚动）。
  旧 bug session 仅有 1 张 shot 但有多张 expand_refs；黄金样本 shot 数应 >= expand_refs。
  """
  if not GOLDEN.is_dir() or not BROKEN.is_dir():
    return

  golden_shots = len(list(GOLDEN.glob("shot_*.png")))
  golden_expand_refs = len(list(GOLDEN.glob("screen_expand_*_refs_*.png")))
  broken_shots = len(list(BROKEN.glob("shot_*.png")))
  broken_expand_refs = len(list(BROKEN.glob("screen_expand_*_refs_*.png")))

  assert golden_shots >= 2
  assert golden_shots >= golden_expand_refs or golden_expand_refs == 0
  assert broken_expand_refs > broken_shots


def test_replay_golden_full_png_height() -> None:
  """黄金样本 record 的 full.png 应高于单屏 shot_01。"""
  if not GOLDEN.is_dir():
    return

  profile = _qa_profile()
  full = GOLDEN / "full.png"
  shot = GOLDEN / "shot_01.png"
  if not full.is_file() or not shot.is_file():
    return

  full_crop = crop_fullscreen_to_detail_content(str(full), profile)
  shot_crop = crop_fullscreen_to_detail_content(str(shot), profile)
  assert full_crop.height > shot_crop.height * 1.8
