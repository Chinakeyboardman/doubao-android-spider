# -*- coding: utf-8 -*-
"""问答长截图：裁切复制栏、两段拼接（无需真机）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PIL import Image

from app.config.gesture_profile import GestureProfile
from app.modules.detail_strip_stitch import (
  crop_fullscreen_to_detail_content,
  stitch_content_strips_vertical_v2,
  stitch_qa_shot_segments,
)
from app.modules.qa_capture import DoubaoQaCapture


ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "logs" / "qa_capture" / "2026-07-10" / "145156"


def _qa_profile() -> GestureProfile:
  p = GestureProfile()
  return replace(
    p,
    fc_detail_roi_y0=p.qa_shot_roi_y0,
    fc_detail_roi_y1=p.qa_shot_roi_y1,
    fc_detail_strip_roi_x0=0.0,
    fc_detail_strip_roi_x1=1.0,
  )


def test_crop_shot_trims_above_copy_bar() -> None:
  """含复制栏的帧应裁到栏上方，降低拼接重复。"""
  cap = DoubaoQaCapture.__new__(DoubaoQaCapture)
  profile = _qa_profile()
  im = Image.new("RGB", (1080, 2400), color=(200, 200, 200))
  path = ROOT / "tests" / "_tmp_qa_shot_trim.png"
  path.parent.mkdir(parents=True, exist_ok=True)
  im.save(path)

  full_crop = crop_fullscreen_to_detail_content(str(path), profile)
  # 复制栏在 ROI 中部：应明显裁短
  trimmed = cap._crop_shot_for_stitch(str(path), profile, copy_bar_top_y=1200)
  assert trimmed.height < full_crop.height
  assert trimmed.height >= 80

  path.unlink(missing_ok=True)


def test_answer_and_think_segments_stitch_separately() -> None:
  """回答段与思考段应分段拼接；full 不含跨段假重叠。"""
  session = (
    ROOT
    / "var"
    / "vivo-x-fold6"
    / "spot_check"
    / "20260714"
    / "qa_capture"
    / "2026-07-16"
    / "162739"
  )
  if not session.is_dir():
    return

  from app.modules.detail_strip_stitch import stitch_qa_shot_segments

  profile = _qa_profile()
  answer_paths = sorted(
    p for p in session.glob("shot_*.png") if not p.name.startswith("shot_think")
  )
  think_paths = sorted(session.glob("shot_think_*.png"))
  if len(answer_paths) < 2 or not think_paths:
    return

  answer_crops = [crop_fullscreen_to_detail_content(str(p), profile) for p in answer_paths]
  think_crops = [crop_fullscreen_to_detail_content(str(p), profile) for p in think_paths]

  answer_img, think_img, diagnoses = stitch_qa_shot_segments(
    answer_crops,
    think_crops,
    answer_labels=[p.name for p in answer_paths],
    think_labels=[p.name for p in think_paths],
  )
  assert think_img is not None

  # 旧行为：跨段强行拼在一起会在 shot 末帧↔shot_think 首帧估出 ~90% 假重叠
  merged_labels = [p.name for p in answer_paths] + [p.name for p in think_paths]
  merged_crops = answer_crops + think_crops
  bad_merged, bad_diag = stitch_content_strips_vertical_v2(
    merged_crops,
    frame_labels=merged_labels,
  )
  boundary = next(
    d for d in bad_diag
    if d.below_frame.startswith("shot_think")
    and not d.above_frame.startswith("shot_think")
  )
  assert boundary.overlap_frac > 0.5

  # 分段后回答长图应短于错误合并版，且高于单屏
  assert answer_img.height < bad_merged.height
  assert answer_img.height > answer_crops[0].height * 1.5
  assert think_img.height > 0
  assert not any(d.diagnosis == "segment_boundary" for d in diagnoses)

  bad_merged.close()
  answer_img.close()
  think_img.close()
  for c in answer_crops + think_crops:
    c.close()
