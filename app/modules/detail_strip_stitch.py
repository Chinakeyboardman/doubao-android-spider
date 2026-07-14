# -*- coding: utf-8 -*-
"""
商品详情长图：全屏截图后裁掉固定顶栏/底栏，仅在内容区内滑动，多帧纵向拼接。

- 横向默认全宽（fc_detail_strip_roi_x0/x1），垂向去顶底栏（fc_detail_roi_y0/y1）；与 web 触底指纹用的窄横条 ROI 分离。
- 支持多块采集：每块拼一张 longstrip_XXX.png，直至连续触底探测静止。
- 裁剪、缩放、拼接仅依赖 PIL；重叠高度由「底条 vs 顶条」均差搜索估计。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image

from app.config.gesture_profile import GestureProfile
from app.modules.chat_ui_heuristics import display_wh
from app.modules.navigator import Navigator
from app.modules.web_detail_capture import (
    adaptive_post_swipe_sleep,
    metric_quiet,
    roi_pair_metrics,
)

log = logging.getLogger(__name__)


def _strip_roi_box_pixels(w: int, h: int, profile: GestureProfile) -> tuple[int, int, int, int]:
    """
    长条截图用的内容区：横向 fc_detail_strip_roi_x0/x1（默认全宽），
    垂向 fc_detail_roi_y0/y1（去顶栏、底栏）。
    """
    left = max(0, min(w, int(w * profile.fc_detail_strip_roi_x0)))
    right = max(0, min(w, int(w * profile.fc_detail_strip_roi_x1)))
    top = max(0, min(h, int(h * profile.fc_detail_roi_y0)))
    bottom = max(0, min(h, int(h * profile.fc_detail_roi_y1)))
    if bottom <= top or right <= left:
        return 0, 0, w, h
    return left, top, right, bottom


def crop_fullscreen_to_detail_content(
    image: Image.Image | str | Path,
    profile: GestureProfile,
) -> Image.Image:
    """
    从整屏截图中裁出中间商品详情区（去掉顶部标题栏、底部导航栏等固定区域）。

    横向默认全宽（fc_detail_strip_roi_x0/x1），垂向去顶底栏（fc_detail_roi_y0/y1）。
    返回 RGB 图像副本，调用方可再保存。
    """
    try:
        if isinstance(image, (str, Path)):
            with Image.open(image) as im:
                rgb = im.convert("RGB")
                w, h = rgb.size
                box = _strip_roi_box_pixels(w, h, profile)
                return rgb.crop(box).copy()
        rgb = image.convert("RGB")
        w, h = rgb.size
        box = _strip_roi_box_pixels(w, h, profile)
        return rgb.crop(box).copy()
    except OSError as e:
        log.exception("裁剪内容区失败（文件或图像损坏）: %s", e)
        raise
    except Exception as e:
        log.exception("裁剪内容区失败: %s", e)
        raise


def swipe_detail_content_only(device: Any, profile: GestureProfile) -> None:
    """
    在**内容区竖条内**执行上滑手势（与主流程一致：手指从下往上拖，页面向下滚）。

    起点/终点相对「内容区」高度比例配置在 GestureProfile，避免手势落在顶栏/底栏上，
    减小滑过头导致的重复与错位；滑动幅度略小于一整屏内容高，为拼接保留重叠带。
    """
    try:
        w, h = display_wh(device, profile=profile)
        left, top, right, bottom = _strip_roi_box_pixels(w, h, profile)
        ch = bottom - top
        if ch < 80:
            log.warning("内容区高度过小 (%s)，回退为整屏手势", ch)
            cx = w // 2
            device.swipe(
                cx,
                int(h * profile.fc_detail_scroll_start_y),
                cx,
                int(h * profile.fc_detail_scroll_end_y),
                profile.fc_detail_scroll_duration,
            )
            return

        cx = (left + right) // 2
        # 相对内容区顶边的比例 → 绝对 y
        y_start = top + int(ch * profile.fc_detail_strip_swipe_y_start_ratio)
        y_end = top + int(ch * profile.fc_detail_strip_swipe_y_end_ratio)
        y_start = max(top + 8, min(bottom - 8, y_start))
        y_end = max(top + 8, min(bottom - 8, y_end))
        if y_end >= y_start:
            y_end = max(top + 8, y_start - int(ch * 0.55))

        device.swipe(cx, y_start, cx, y_end, profile.fc_detail_strip_swipe_duration)
    except Exception as e:
        log.exception("内容区内滑动失败: %s", e)
        raise


def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    """两图须同尺寸；内部缩到小图再比，加快搜索。"""
    a = a.convert("L").resize((48, 48), Image.Resampling.BILINEAR)
    b = b.convert("L").resize((48, 48), Image.Resampling.BILINEAR)
    p1, p2 = a.tobytes(), b.tobytes()
    n = len(p1)
    if n == 0:
        return 999.0
    return sum(abs(p1[i] - p2[i]) for i in range(n)) / n


def estimate_vertical_overlap_px(
    image_above: Image.Image,
    image_below: Image.Image,
    *,
    min_overlap: int = 16,
    max_overlap_frac: float = 0.48,
    step_px: int = 3,
    bad_diff_threshold: float = 28.0,
    fallback_frac: float = 0.18,
) -> int:
    """
    估计「上一张（画面上方）」与「下一张（画面下方）」之间的垂直重叠像素高度。

    原理：滚动后，上一张底端与下一张顶端应有一段相同内容；在 [min, max] 范围内枚举重叠高度，
    取底条/顶条缩小后均差最小者。若匹配质量差（动效/曝光差），返回保守默认重叠。
    """
    try:
        a = image_above.convert("RGB")
        b = image_below.convert("RGB")
        w1, h1 = a.size
        w2, h2 = b.size
        w = min(w1, w2)
        if w <= 0:
            return 0
        if w1 != w:
            a = a.resize((w, max(1, int(h1 * w / w1))), Image.Resampling.BILINEAR)
            h1 = a.size[1]
        if w2 != w:
            b = b.resize((w, max(1, int(h2 * w / w2))), Image.Resampling.BILINEAR)
            h2 = b.size[1]

        max_oh = int(min(h1, h2) * max_overlap_frac)
        max_oh = max(min_overlap, max_oh)
        best_oh = min_overlap
        best_score = 1e9

        for oh in range(min_overlap, max_oh + 1, step_px):
            if oh > h1 or oh > h2:
                break
            strip_top = a.crop((0, h1 - oh, w, h1))
            strip_bot = b.crop((0, 0, w, oh))
            score = _mean_abs_diff(strip_top, strip_bot)
            if score < best_score:
                best_score = score
                best_oh = oh

        if best_score > bad_diff_threshold:
            fb = int(min(h1, h2) * fallback_frac)
            log.debug(
                "重叠匹配质量一般(score=%.2f)，回退重叠=%d",
                best_score,
                fb,
            )
            return max(min_overlap, min(fb, min(h1, h2) // 2))

        return best_oh
    except Exception as e:
        log.warning("估计重叠失败，使用默认: %s", e)
        mh = min(image_above.height, image_below.height)
        return max(min_overlap, int(mh * fallback_frac))


def stitch_content_strips_vertical(crops: list[Image.Image]) -> Image.Image:
    """
    将多张已裁好的「仅内容区」竖条，按顺序自上而下拼接为一张长图。

    相邻两张之间用 estimate_vertical_overlap_px 去重后再纵向拼接。
    """
    if not crops:
        raise ValueError("crops 不能为空")
    try:
        out = crops[0].convert("RGB").copy()
        for nxt in crops[1:]:
            nxt = nxt.convert("RGB")
            if nxt.width != out.width:
                nxt = nxt.resize((out.width, max(1, int(nxt.height * out.width / nxt.width))), Image.Resampling.BILINEAR)
            oh = estimate_vertical_overlap_px(out, nxt)
            oh = max(0, min(oh, nxt.height - 1, out.height))
            strip = nxt.crop((0, oh, nxt.width, nxt.height))
            new_h = out.height + strip.height
            canvas = Image.new("RGB", (out.width, new_h), (255, 255, 255))
            canvas.paste(out, (0, 0))
            canvas.paste(strip, (0, out.height))
            out = canvas
        return out
    except Exception as e:
        log.exception("拼接长图失败: %s", e)
        raise


def capture_detail_content_strip_sequence(
    device: Any,
    nav: Navigator,
    profile: GestureProfile,
    output_dir: str,
    *,
    num_frames: int = 5,
    post_swipe_sleep: float = 0.85,
    keep_full_screenshots: bool = False,
) -> tuple[list[str], str]:
    """
    在 Web 详情页：连续 num_frames 次「全屏截图 → 裁内容区 → 内容区内滑动」，
    最后拼接为 strip_stitched.png。

    :param output_dir: 输出目录（不存在则创建）
    :param keep_full_screenshots: 为 True 时额外保存 _full_XX.png 便于调试
    :return: (各条内容区 PNG 路径列表, 拼接长图路径)
    """
    if num_frames < 1:
        raise ValueError("num_frames 至少为 1")

    os.makedirs(output_dir, exist_ok=True)
    strips_dir = os.path.join(output_dir, "strips")
    os.makedirs(strips_dir, exist_ok=True)

    strip_paths: list[str] = []
    full_tmp = os.path.join(output_dir, "_capture_full_tmp.png")

    try:
        for i in range(num_frames):
            try:
                device.screenshot(full_tmp)
            except Exception as e:
                log.error("第 %d 帧截图失败: %s", i + 1, e)
                raise

            if keep_full_screenshots:
                try:
                    dup_path = os.path.join(output_dir, f"_full_{i+1:02d}.png")
                    shutil.copy2(full_tmp, dup_path)
                except OSError:
                    log.debug("保留全屏调试图失败，已忽略")

            try:
                cropped = crop_fullscreen_to_detail_content(full_tmp, profile)
            except Exception:
                raise

            strip_path = os.path.join(strips_dir, f"strip_{i+1:02d}.png")
            try:
                cropped.save(strip_path, format="PNG", optimize=True)
            except OSError as e:
                log.error("保存条图失败 %s: %s", strip_path, e)
                raise
            strip_paths.append(strip_path)

            if i >= num_frames - 1:
                break

            if not nav.is_web_detail():
                log.warning("第 %d 帧后已离开 WebActivity，停止续滑", i + 1)
                break

            try:
                swipe_detail_content_only(device, profile)
            except Exception:
                raise
            time.sleep(post_swipe_sleep)

        # 拼接
        loaded: list[Image.Image] = []
        try:
            for p in strip_paths:
                im = Image.open(p)
                loaded.append(im.convert("RGB").copy())
                im.close()
            stitched = stitch_content_strips_vertical(loaded)
        finally:
            for im in loaded:
                try:
                    im.close()
                except Exception:
                    pass

        stitched_path = os.path.join(output_dir, "strip_stitched.png")
        try:
            stitched.save(stitched_path, format="PNG", optimize=True)
        except OSError as e:
            log.error("保存拼接图失败: %s", e)
            raise

        return strip_paths, stitched_path
    finally:
        try:
            if os.path.isfile(full_tmp):
                os.remove(full_tmp)
        except OSError:
            pass


def _probe_bottom_after_scroll(
    device: Any,
    nav: Navigator,
    profile: GestureProfile,
    probe_dir: str,
) -> bool:
    """
    再执行一次内容区滑动后：若全屏截图在「全宽 + 原垂向 ROI」下与滑前几乎无变化，
    认为已触底（与 web_detail_capture 的 metric_quiet 一致）。
    """
    if not nav.is_web_detail():
        log.info("触底探测：已非 WebActivity，视为结束")
        return True
    os.makedirs(probe_dir, exist_ok=True)
    pa = os.path.join(probe_dir, "_probe_before.png")
    pb = os.path.join(probe_dir, "_probe_after.png")
    try:
        device.screenshot(pa)
        swipe_detail_content_only(device, profile)
        time.sleep(
            adaptive_post_swipe_sleep(None, profile.fc_detail_bottom_post_swipe_sleep)
        )
        if not nav.is_web_detail():
            return True
        device.screenshot(pb)
    except Exception as e:
        log.warning("触底探测截图失败: %s", e)
        return False
    # 比较时用全宽 ROI，避免左右被裁导致误判仍在滚动
    pw = replace(profile, fc_detail_roi_x0=0.0, fc_detail_roi_x1=1.0)
    try:
        fine, coarse, dham = roi_pair_metrics(pa, pb, pw)
        mq, reason = metric_quiet(fine, coarse, dham, profile)
        log.info(
            "触底探测 fine=%.2f coarse=%.2f dham=%d -> quiet=%s (%s)",
            fine,
            coarse,
            dham,
            mq,
            reason,
        )
        return bool(mq)
    except Exception as e:
        log.warning("触底探测算指标失败: %s", e)
        return False
    finally:
        for fp in (pa, pb):
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass


def capture_detail_long_strips_until_bottom(
    device: Any,
    nav: Navigator,
    profile: GestureProfile,
    output_dir: str,
    *,
    frames_per_chunk: int = 5,
    max_chunks: int = 40,
    post_swipe_sleep: float = 0.88,
    bottom_quiet_probes: int = 2,
    keep_full_screenshots: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    """
    分多「块」采集详情：每块内 frames_per_chunk 次截图并拼成一张长图 longstrip_XXX.png，
    块之间继续向下滑，直到连续 bottom_quiet_probes 次「再滑几乎不动」判定触底。

    每块目录 chunk_XXX/strips/ 下保留条图与 strip_stitched.png；根目录 longstrip_XXX.png 为块长图副本。
    写 longstrip_meta.json 汇总路径与停止原因。
    """
    if frames_per_chunk < 1:
        raise ValueError("frames_per_chunk 至少为 1")
    if bottom_quiet_probes < 1:
        raise ValueError("bottom_quiet_probes 至少为 1")

    os.makedirs(output_dir, exist_ok=True)
    long_paths: list[str] = []
    quiet_streak = 0
    chunk_idx = 0
    stopped_by = "max_chunks"

    try:
        while chunk_idx < max_chunks:
            chunk_idx += 1
            chunk_dir = os.path.join(output_dir, f"chunk_{chunk_idx:03d}")
            try:
                _, stitched = capture_detail_content_strip_sequence(
                    device,
                    nav,
                    profile,
                    chunk_dir,
                    num_frames=frames_per_chunk,
                    post_swipe_sleep=post_swipe_sleep,
                    keep_full_screenshots=keep_full_screenshots,
                )
            except Exception:
                log.exception("第 %d 块采集失败", chunk_idx)
                stopped_by = "chunk_error"
                break

            if not os.path.isfile(stitched):
                log.error("第 %d 块未生成拼接图", chunk_idx)
                stopped_by = "no_stitched"
                break

            long_name = os.path.join(output_dir, f"longstrip_{chunk_idx:03d}.png")
            shutil.copy2(stitched, long_name)
            long_paths.append(long_name)
            log.info("已生成长图 %s（第 %d 块）", long_name, chunk_idx)

            if _probe_bottom_after_scroll(device, nav, profile, output_dir):
                quiet_streak += 1
                if quiet_streak < bottom_quiet_probes:
                    if _probe_bottom_after_scroll(device, nav, profile, output_dir):
                        quiet_streak += 1
                    else:
                        quiet_streak = 0
            else:
                quiet_streak = 0

            log.info("触底探测 streak=%d（目标连续 %d）", quiet_streak, bottom_quiet_probes)
            if quiet_streak >= bottom_quiet_probes:
                stopped_by = "bottom_probe"
                break

        meta: dict[str, Any] = {
            "longstrip_paths": long_paths,
            "chunks_completed": chunk_idx,
            "stopped_by": stopped_by,
            "frames_per_chunk": frames_per_chunk,
            "max_chunks": max_chunks,
            "bottom_quiet_probes": bottom_quiet_probes,
            "strip_roi_x": [profile.fc_detail_strip_roi_x0, profile.fc_detail_strip_roi_x1],
            "strip_roi_y": [profile.fc_detail_roi_y0, profile.fc_detail_roi_y1],
        }
        meta_path = os.path.join(output_dir, "longstrip_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return long_paths, meta
    except Exception:
        log.exception("capture_detail_long_strips_until_bottom 异常中断")
        raise
