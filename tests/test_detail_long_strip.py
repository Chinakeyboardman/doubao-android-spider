# -*- coding: utf-8 -*-
"""
商品详情长图集成测试：
- 条图横向默认全宽，仅裁顶/底固定栏；
- 多块拼接多张 longstrip_XXX.png，直至触底探测连续静止。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app.config.gesture_profile import GestureProfile
from app.modules.detail_strip_stitch import (
    capture_detail_content_strip_sequence,
    capture_detail_long_strips_until_bottom,
    crop_fullscreen_to_detail_content,
)
from app.modules.navigator import Navigator, Page


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _session_dir(prefix: str = "detail_strip") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = _repo_root() / "logs" / "test_sessions" / f"{prefix}_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.mark.integration
def test_detail_five_strip_stitch(
    tmp_path: Path,
    u2_device: object,
    gesture_profile: GestureProfile,
) -> None:
    """
    商品详情：多张长图覆盖整页直至底部。

    - 横向保留全宽（fc_detail_strip_roi_x0/x1，默认 0~1）
    - 每块默认 5 张条图拼一张 longstrip_XXX.png，多块直到连续触底探测静止
    """
    _ = tmp_path

    nav = Navigator(u2_device)
    page, cur = nav.current_page()
    if page is not Page.WEB_DETAIL:
        pytest.skip("请先打开商品详情 WebActivity 并滚到顶部后再运行本用例")

    assert nav.wait_web_detail_loaded(timeout=15), "详情未加载完成"

    if os.environ.get("DOUBAO_STRIP_ROI_TOP"):
        gesture_profile.fc_detail_roi_y0 = float(os.environ["DOUBAO_STRIP_ROI_TOP"])
    if os.environ.get("DOUBAO_STRIP_ROI_BOTTOM"):
        gesture_profile.fc_detail_roi_y1 = float(os.environ["DOUBAO_STRIP_ROI_BOTTOM"])
    if os.environ.get("DOUBAO_STRIP_ROI_LEFT"):
        gesture_profile.fc_detail_strip_roi_x0 = float(os.environ["DOUBAO_STRIP_ROI_LEFT"])
    if os.environ.get("DOUBAO_STRIP_ROI_RIGHT"):
        gesture_profile.fc_detail_strip_roi_x1 = float(os.environ["DOUBAO_STRIP_ROI_RIGHT"])

    frames_per_chunk = int(os.environ.get("DOUBAO_STRIP_FRAMES_PER_CHUNK", "5"))
    max_chunks = int(os.environ.get("DOUBAO_STRIP_MAX_CHUNKS", "40"))
    post_sleep = float(os.environ.get("DOUBAO_STRIP_POST_SWIPE_SLEEP", "0.88"))
    bottom_probes = int(os.environ.get("DOUBAO_STRIP_BOTTOM_QUIET_PROBES", "2"))
    keep_full = os.environ.get("DOUBAO_STRIP_KEEP_FULL", "").lower() in ("1", "true", "yes")

    # 单块快速调试：仅跑 1 块、不跑触底循环
    single_chunk_only = os.environ.get("DOUBAO_STRIP_SINGLE_CHUNK_ONLY", "").lower() in ("1", "true", "yes")

    out_dir = str(_session_dir())

    meta: dict = {
        "activity": cur.get("activity", ""),
        "strip_roi_x": [gesture_profile.fc_detail_strip_roi_x0, gesture_profile.fc_detail_strip_roi_x1],
        "strip_roi_y": [gesture_profile.fc_detail_roi_y0, gesture_profile.fc_detail_roi_y1],
        "frames_per_chunk": frames_per_chunk,
        "max_chunks": max_chunks,
        "single_chunk_only": single_chunk_only,
    }

    long_paths: list[str] = []
    run_meta: dict = {}

    try:
        if single_chunk_only:
            strip_paths, stitched_path = capture_detail_content_strip_sequence(
                u2_device,
                nav,
                gesture_profile,
                out_dir,
                num_frames=frames_per_chunk,
                post_swipe_sleep=post_sleep,
                keep_full_screenshots=keep_full,
            )
            meta["mode"] = "single_chunk"
            meta["strip_paths"] = [str(Path(p).relative_to(out_dir)) for p in strip_paths]
            meta["stitched"] = str(Path(stitched_path).relative_to(out_dir))
            long_paths = [stitched_path]
        else:
            long_paths, run_meta = capture_detail_long_strips_until_bottom(
                u2_device,
                nav,
                gesture_profile,
                out_dir,
                frames_per_chunk=frames_per_chunk,
                max_chunks=max_chunks,
                post_swipe_sleep=post_sleep,
                bottom_quiet_probes=bottom_probes,
                keep_full_screenshots=keep_full,
            )
            meta["mode"] = "until_bottom"
            meta.update(run_meta)
    except Exception as exc:
        pytest.fail(f"采集失败: {exc}")

    assert long_paths, "应至少有一张长图"
    for lp in long_paths:
        assert Path(lp).is_file(), lp

    if not single_chunk_only:
        assert meta.get("stopped_by") == "bottom_probe", (
            f"预期触底结束，实际 stopped_by={meta.get('stopped_by')}；"
            f"若页极长可调大 DOUBAO_STRIP_MAX_CHUNKS"
        )
        assert len(long_paths) >= 1

    # 首张条图宽度应与「条图横向 ROI」一致（默认全宽）
    first_strips = sorted(Path(out_dir).glob("**/strip_01.png"))
    if first_strips:
        with Image.open(first_strips[0]) as s0:
            strip_w = s0.width
        try:
            dw = int((u2_device.info.get("displayWidth") or 1080))
        except Exception:
            dw = 1080
        exp_w = int(dw * (gesture_profile.fc_detail_strip_roi_x1 - gesture_profile.fc_detail_strip_roi_x0))
        assert abs(strip_w - exp_w) <= 2, f"条图宽度应接近 {exp_w}，实际 {strip_w}"

    Path(out_dir, "strip_run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_crop_fullscreen_to_detail_content_unit(tmp_path: Path, gesture_profile: GestureProfile) -> None:
    """纯本地：假全屏图，条图横向默认全宽。"""
    from PIL import ImageDraw

    w, h = 720, 1600
    im = Image.new("RGB", (w, h), (200, 200, 210))
    dr = ImageDraw.Draw(im)
    dr.rectangle((0, 0, w, int(h * gesture_profile.fc_detail_roi_y0)), fill=(10, 10, 30))
    dr.rectangle((0, int(h * gesture_profile.fc_detail_roi_y1), w, h), fill=(30, 10, 10))
    p = tmp_path / "fake_full.png"
    im.save(p)

    out = crop_fullscreen_to_detail_content(p, gesture_profile)
    assert out.width > 0 and out.height > 0
    ew = int(w * (gesture_profile.fc_detail_strip_roi_x1 - gesture_profile.fc_detail_strip_roi_x0))
    eh = int(h * (gesture_profile.fc_detail_roi_y1 - gesture_profile.fc_detail_roi_y0))
    assert abs(out.width - ew) <= 4
    assert abs(out.height - eh) <= 4
