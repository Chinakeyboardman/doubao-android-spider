# -*- coding: utf-8 -*-
"""
WebActivity 商品详情：纵向滑动截图直至触底。

与 `FlowCrawler._capture_detail` 使用同一套手势（GestureProfile.fc_detail_scroll_*）；
触底判定：ROI 内多指标「连续静止」或常见「没有更多」类 UI 文案（Navigator）。

ROI 排除固定顶栏/底栏，避免整屏动效误判；接近底部时略延长停稳时间（自适应 sleep）。
"""

from __future__ import annotations

import os
import shutil
import time
from typing import Any, Callable

from PIL import Image, ImageFilter

from app.config.gesture_profile import GestureProfile
from app.modules.chat_ui_heuristics import display_wh
from app.modules.navigator import Navigator


def _roi_box(
    size: tuple[int, int],
    p: GestureProfile,
) -> tuple[int, int, int, int]:
    w, h = size
    left = max(0, min(w, int(w * p.fc_detail_roi_x0)))
    right = max(0, min(w, int(w * p.fc_detail_roi_x1)))
    top = max(0, min(h, int(h * p.fc_detail_roi_y0)))
    bottom = max(0, min(h, int(h * p.fc_detail_roi_y1)))
    if bottom <= top or right <= left:
        return 0, 0, w, h
    return left, top, right, bottom


def _roi_gray_from_png(path: str, p: GestureProfile) -> Image.Image:
    with Image.open(path) as im:
        box = _roi_box(im.size, p)
        return im.crop(box).convert("L")


def roi_pair_metrics(prev_png: str, curr_png: str, profile: GestureProfile) -> tuple[float, float, int]:
    """返回 (fine 均差, 模糊 coarse 均差, dHash 汉明距)。"""
    ra = _roi_gray_from_png(prev_png, profile)
    rb = _roi_gray_from_png(curr_png, profile)
    if ra.size != rb.size:
        rb = rb.resize(ra.size, Image.Resampling.BILINEAR)
    fine = _mean_abs_diff_resized(ra, rb, (72, 96))
    coarse = _coarse_mean_abs_diff(ra, rb)
    dham = _hamming64(_dhash64_roi(ra), _dhash64_roi(rb))
    return fine, coarse, dham


def _mean_abs_diff_resized(
    roi_a: Image.Image, roi_b: Image.Image, size: tuple[int, int],
) -> float:
    a = roi_a.resize(size, Image.Resampling.BILINEAR)
    b = roi_b.resize(size, Image.Resampling.BILINEAR)
    p1, p2 = a.tobytes(), b.tobytes()
    n = len(p1)
    return sum(abs(p1[i] - p2[i]) for i in range(n)) / n


def _coarse_mean_abs_diff(roi_a: Image.Image, roi_b: Image.Image) -> float:
    if roi_a.size != roi_b.size:
        roi_b = roi_b.resize(roi_a.size, Image.Resampling.BILINEAR)
    a = roi_a.filter(ImageFilter.GaussianBlur(radius=2)).resize((16, 20), Image.Resampling.BILINEAR).convert("L")
    b = roi_b.filter(ImageFilter.GaussianBlur(radius=2)).resize((16, 20), Image.Resampling.BILINEAR).convert("L")
    p1, p2 = a.tobytes(), b.tobytes()
    n = len(p1)
    return sum(abs(p1[i] - p2[i]) for i in range(n)) / n


def _dhash64_roi(roi_gray: Image.Image) -> int:
    im = roi_gray.resize((9, 8), Image.Resampling.LANCZOS)
    px = im.load()
    out = 0
    bit = 0
    for y in range(8):
        for x in range(8):
            if px[x, y] > px[x + 1, y]:
                out |= 1 << bit
            bit += 1
    return out


def _hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def metric_quiet(
    fine: float,
    coarse: float,
    dham: int,
    profile: GestureProfile,
) -> tuple[bool, str]:
    if fine <= profile.fc_detail_bottom_strict_fine:
        return True, "fine_le_strict"
    if fine <= profile.fc_detail_bottom_alt_fine:
        return True, "fine_le_alt"
    if (
        fine <= profile.fc_detail_bottom_relax_fine
        and coarse <= profile.fc_detail_bottom_relax_coarse
        and dham <= profile.fc_detail_bottom_relax_dhash
    ):
        return True, "fine_coarse_dhash_combo"
    return False, ""


def adaptive_post_swipe_sleep(last_fine: float | None, base: float) -> float:
    """
    长列表中段略缩短等待；接近静止（fine 较低）略延长，减轻 WebView 未落稳导致的误判。
    """
    if last_fine is None:
        return base
    if last_fine < 22.0:
        return min(1.18, base + 0.22)
    if last_fine < 35.0:
        return min(1.05, base + 0.08)
    if last_fine > 58.0:
        return max(0.72, base - 0.18)
    return base


def capture_web_detail_screenshots(
    device: Any,
    nav: Navigator,
    profile: GestureProfile,
    detail_dir: str,
    *,
    on_after_screenshot: Callable[[str], None] | None = None,
) -> tuple[list[str], str]:
    """
    首张截图为 detail_01.png，之后每次下滑再截 detail_XX，直至触底或上限。

    返回 (截图绝对路径列表, 停止原因):
      roi_metric_stable / ui_end_hint / max_swipes / left_web
    """
    os.makedirs(detail_dir, exist_ok=True)
    curr_tmp = os.path.join(detail_dir, "_cap_curr.png")

    first = os.path.join(detail_dir, "detail_01.png")
    try:
        device.screenshot(first)
    except Exception:
        return [], "screenshot_failed"

    paths: list[str] = [first]
    if on_after_screenshot:
        on_after_screenshot(first)

    prev_path = first
    last_fine: float | None = None
    metric_streak = 0
    hint_streak = 0
    req = max(1, profile.fc_detail_bottom_stable_swipes)
    max_sw = max(1, profile.fc_detail_bottom_max_swipes)
    base_sleep = profile.fc_detail_bottom_post_swipe_sleep

    w, h = display_wh(device, profile=profile)
    x = int(w * 0.5)
    sy = int(h * profile.fc_detail_scroll_start_y)
    ey = int(h * profile.fc_detail_scroll_end_y)
    dur = profile.fc_detail_scroll_duration

    for _ in range(max_sw):
        device.swipe(x, sy, x, ey, dur)
        time.sleep(adaptive_post_swipe_sleep(last_fine, base_sleep))
        if not nav.is_web_detail():
            return paths, "left_web"

        try:
            device.screenshot(curr_tmp)
        except Exception:
            break

        fine, coarse, dham = roi_pair_metrics(prev_path, curr_tmp, profile)
        last_fine = fine

        mq, _ = metric_quiet(fine, coarse, dham, profile)
        metric_streak = metric_streak + 1 if mq else 0

        hint = nav.web_detail_scroll_end_hints_visible()
        hint_streak = hint_streak + 1 if hint else 0

        next_name = f"detail_{len(paths) + 1:02d}.png"
        out = os.path.join(detail_dir, next_name)
        shutil.copyfile(curr_tmp, out)
        paths.append(out)
        if on_after_screenshot:
            on_after_screenshot(out)

        if metric_streak >= req:
            try:
                os.remove(curr_tmp)
            except OSError:
                pass
            return paths, "roi_metric_stable"
        if hint_streak >= req:
            try:
                os.remove(curr_tmp)
            except OSError:
                pass
            return paths, "ui_end_hint"

        prev_path = out

    try:
        os.remove(curr_tmp)
    except OSError:
        pass
    return paths, "max_swipes"
