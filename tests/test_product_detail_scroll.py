# -*- coding: utf-8 -*-
"""
商品详情 WebActivity：从当前屏（假定已在页面最上方）纵向滑到底。

滑动几何与 `FlowCrawler._capture_detail` 一致（GestureProfile.fc_detail_scroll_*）。

产出目录：`logs/test_sessions/detail_scroll_<时间戳>/`
  - run.log           完整日志
  - analysis.jsonl    每步：fine / blur-coarse / dHash 汉明距 / 到底文案 / 双路稳定计数
  - summary.json      汇总与停止原因（roi_metric / ui_end_hint / max_swipes）
  - screenshots/      full_* / roi_*

停止条件（满足其一即成功）：
  1) 连续 2 次「截图指标静止」：严格 fine，或略松的 fine+模糊色差+dHash 组合（经历史 ROI 序列校验不易误判）
  2) 连续 2 次检测到常见「没有更多」类到底文案（`Navigator.web_detail_scroll_end_hints_visible`）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from app.config.gesture_profile import GestureProfile
from app.modules.chat_ui_heuristics import display_wh
from app.modules.navigator import Navigator, Page
from app.modules.web_detail_capture import adaptive_post_swipe_sleep, metric_quiet, roi_pair_metrics

# 环境变量未设置时，与 GestureProfile / web_detail_capture 默认值对齐
_DEFAULT_MAX_SWIPES = 100
_DEFAULT_POST_SWIPE_SLEEP = 0.95
_DEFAULT_STABLE_MEAN_DIFF = 4.0
# 仅靠 fine 的略宽门槛（仍很严，防动效）
_DEFAULT_ALT_FINE_MAX = 10.0
# 组合宽松：需同时满足，在历史 30 帧连续对上无误判
_DEFAULT_RELAX_FINE_MAX = 36.0
_DEFAULT_RELAX_COARSE_MAX = 20.0
_DEFAULT_RELAX_DHASH_MAX = 26

_DEFAULT_ROI_Y0 = 0.14
_DEFAULT_ROI_Y1 = 0.86
_DEFAULT_ROI_X0 = 0.06
_DEFAULT_ROI_X1 = 0.94

_MID_SCROLL_DUP_FINE = 4.0


@dataclass
class ScrollStepRecord:
    step_index: int
    event: str
    full_png: str
    roi_png: str
    roi_mean_diff_vs_prev: float | None
    roi_fingerprint: str
    metric_streak: int = 0
    hint_streak: int = 0
    roi_coarse_diff_vs_prev: float | None = None
    roi_dhash_hamming_vs_prev: int | None = None
    end_hint_visible: bool = False
    metric_quiet: bool = False
    metric_quiet_reason: str = ""
    flags: list[str] = field(default_factory=list)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _new_session_dir() -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = _repo_root() / "logs" / "test_sessions" / f"detail_scroll_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "screenshots").mkdir(exist_ok=True)
    return d


def _setup_logger(session_dir: Path) -> logging.Logger:
    name = f"detail_scroll_{session_dir.name}"
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(session_dir / "run.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(ch)
    log.propagate = False
    return log


def _roi_tuple(
    size: tuple[int, int],
    x0_r: float,
    x1_r: float,
    y0_r: float,
    y1_r: float,
) -> tuple[int, int, int, int]:
    w, h = size
    left = max(0, min(w, int(w * x0_r)))
    right = max(0, min(w, int(w * x1_r)))
    top = max(0, min(h, int(h * y0_r)))
    bottom = max(0, min(h, int(h * y1_r)))
    if bottom <= top or right <= left:
        return 0, 0, w, h
    return left, top, right, bottom


def _extract_roi_image(src: Path, x0_r: float, x1_r: float, y0_r: float, y1_r: float) -> Image.Image:
    with Image.open(src) as im:
        box = _roi_tuple(im.size, x0_r, x1_r, y0_r, y1_r)
        return im.crop(box).convert("L")


def _roi_fingerprint(roi_gray: Image.Image, resize: tuple[int, int] = (48, 64)) -> str:
    small = roi_gray.resize(resize, Image.Resampling.BILINEAR)
    return hashlib.sha256(small.tobytes()).hexdigest()[:20]


def _save_roi_preview(src: Path, dest: Path, x0_r: float, x1_r: float, y0_r: float, y1_r: float) -> None:
    with Image.open(src) as im:
        box = _roi_tuple(im.size, x0_r, x1_r, y0_r, y1_r)
        im.crop(box).save(dest, format="PNG")


def _swipe_detail_down(device: Any, profile: GestureProfile, log: logging.Logger) -> None:
    w, h = display_wh(device, profile=profile)
    x = int(w * 0.5)
    y0 = int(h * profile.fc_detail_scroll_start_y)
    y1 = int(h * profile.fc_detail_scroll_end_y)
    log.debug(
        "执行详情页下滑手势: swipe (%s,%s)->(%s,%s) duration=%s",
        x,
        y0,
        x,
        y1,
        profile.fc_detail_scroll_duration,
    )
    device.swipe(x, y0, x, y1, profile.fc_detail_scroll_duration)


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _scroll_detail_until_stable(
    device: Any,
    nav: Navigator,
    profile: GestureProfile,
    session_dir: Path,
    log: logging.Logger,
) -> tuple[int, bool, list[ScrollStepRecord], dict[str, Any]]:
    """与 `web_detail_capture` 共用 ROI 指标与 `metric_quiet`；本函数额外写会话日志与 ROI 预览。"""
    shots = session_dir / "screenshots"
    analysis_path = session_dir / "analysis.jsonl"
    prev_png = session_dir / "_prev_full.png"
    curr_png = session_dir / "_curr_full.png"

    x0_r = profile.fc_detail_roi_x0
    x1_r = profile.fc_detail_roi_x1
    y0_r = profile.fc_detail_roi_y0
    y1_r = profile.fc_detail_roi_y1
    max_swipes = profile.fc_detail_bottom_max_swipes
    stab_req = max(1, profile.fc_detail_bottom_stable_swipes)
    base_sleep = profile.fc_detail_bottom_post_swipe_sleep

    records: list[ScrollStepRecord] = []
    fingerprint_history: list[tuple[int, str]] = []
    flags_all: list[str] = []
    mean_diffs: list[float] = []
    coarse_diffs: list[float] = []
    dhash_hams: list[int] = []

    log.info(
        "ROI 裁剪: x=[%.3f,%.3f] y=[%.3f,%.3f]；最多 %d 次滑动；停稳基准 %.2fs（自适应调整）",
        x0_r,
        x1_r,
        y0_r,
        y1_r,
        max_swipes,
        base_sleep,
    )
    log.info(
        "静止判定-A: strict<=%.3f 或 alt_fine<=%.3f 或 combo(fine<=%.1f & coarse<=%.1f & dham<=%d)，连续 %d 次",
        profile.fc_detail_bottom_strict_fine,
        profile.fc_detail_bottom_alt_fine,
        profile.fc_detail_bottom_relax_fine,
        profile.fc_detail_bottom_relax_coarse,
        profile.fc_detail_bottom_relax_dhash,
        stab_req,
    )
    log.info("静止判定-B: 到底文案连续 %d 次可见", stab_req)

    device.screenshot(str(prev_png))
    _save_roi_preview(prev_png, shots / "roi_000.png", x0_r, x1_r, y0_r, y1_r)
    shutil.copyfile(prev_png, shots / "full_000.png")
    roi0 = _extract_roi_image(prev_png, x0_r, x1_r, y0_r, y1_r)
    fp0 = _roi_fingerprint(roi0)
    fingerprint_history.append((0, fp0))
    hint0 = nav.web_detail_scroll_end_hints_visible()
    r0 = ScrollStepRecord(
        step_index=0,
        event="initial",
        full_png="screenshots/full_000.png",
        roi_png="screenshots/roi_000.png",
        roi_mean_diff_vs_prev=None,
        roi_fingerprint=fp0,
        end_hint_visible=hint0,
    )
    records.append(r0)
    _append_jsonl(analysis_path, {**asdict(r0), "strict_fine_max": profile.fc_detail_bottom_strict_fine})
    log.info("初始帧 full_000 / roi_000，指纹=%s，到底文案可见=%s", fp0, hint0)

    metric_streak = 0
    hint_streak = 0
    last_fine_for_sleep: float | None = None

    def _build_summary(stopped_by: str, si: int) -> dict[str, Any]:
        out: dict[str, Any] = {
            "total_swipes": si,
            "stopped_by": stopped_by,
            "mean_diffs": mean_diffs,
            "coarse_diffs": coarse_diffs,
            "dhash_hammings": dhash_hams,
            "unique_roi_fingerprints": len({fp for _, fp in fingerprint_history}),
            "flags": flags_all,
            "roi_box_ratios": {"x0": x0_r, "x1": x1_r, "y0": y0_r, "y1": y1_r},
            "thresholds": {
                "strict_fine": profile.fc_detail_bottom_strict_fine,
                "alt_fine": profile.fc_detail_bottom_alt_fine,
                "relax_fine": profile.fc_detail_bottom_relax_fine,
                "relax_coarse": profile.fc_detail_bottom_relax_coarse,
                "relax_dhash": profile.fc_detail_bottom_relax_dhash,
            },
            "analysis_note": (
                "若 stopped_by=max_swipes 且 mean_diffs 全程偏高，多为超长列表未到物理底部，"
                "可增大 profile.fc_detail_bottom_max_swipes 或设备 JSON 覆盖；"
                "主流程与 `web_detail_capture.capture_web_detail_screenshots` 一致。"
            ),
        }
        if mean_diffs:
            out["mean_diff_stats"] = {
                "min": min(mean_diffs),
                "max": max(mean_diffs),
                "avg": sum(mean_diffs) / len(mean_diffs),
            }
        if coarse_diffs:
            out["coarse_diff_stats"] = {
                "min": min(coarse_diffs),
                "max": max(coarse_diffs),
                "avg": sum(coarse_diffs) / len(coarse_diffs),
            }
        return out

    for n in range(max_swipes):
        si = n + 1
        log.info("---------- 第 %d / %d 次滑动 ----------", si, max_swipes)
        _swipe_detail_down(device, profile, log)
        time.sleep(adaptive_post_swipe_sleep(last_fine_for_sleep, base_sleep))
        if not nav.is_web_detail():
            log.error("已离开 WebActivity")
            pytest.fail("滑动过程中已离开 WebActivity 商品详情页")

        device.screenshot(str(curr_png))
        shutil.copyfile(curr_png, shots / f"full_{si:03d}.png")
        _save_roi_preview(curr_png, shots / f"roi_{si:03d}.png", x0_r, x1_r, y0_r, y1_r)

        fine, coarse, dham = roi_pair_metrics(str(prev_png), str(curr_png), profile)
        last_fine_for_sleep = fine

        mean_diffs.append(fine)
        coarse_diffs.append(coarse)
        dhash_hams.append(dham)

        mq, mq_reason = metric_quiet(fine, coarse, dham, profile)
        if mq:
            metric_streak += 1
        else:
            metric_streak = 0

        hint_now = nav.web_detail_scroll_end_hints_visible()
        if hint_now:
            hint_streak += 1
        else:
            hint_streak = 0

        roi_curr = _extract_roi_image(curr_png, x0_r, x1_r, y0_r, y1_r)
        fp_curr = _roi_fingerprint(roi_curr)
        step_flags: list[str] = []

        prev_fine = mean_diffs[-2] if len(mean_diffs) >= 2 else None
        skip_dup_hist = prev_fine is not None and prev_fine > 58.0

        for prev_i, prev_fp in fingerprint_history:
            if prev_fp == fp_curr and prev_i <= si - 2:
                if skip_dup_hist:
                    log.debug(
                        "跳过 roi_duplicate_history（step%d 上一拍 fine=%.2f 视为回弹过渡）",
                        si,
                        prev_fine,
                    )
                    continue
                msg = (
                    f"roi_duplicate_history:step{si} 与 step{prev_i} ROI 指纹相同"
                    "（可能回弹/重复截图/循环内容）"
                )
                step_flags.append(msg)
                flags_all.append(msg)
                log.warning(msg)

        if fine <= _MID_SCROLL_DUP_FINE and metric_streak == 0 and hint_streak == 0 and si > 1:
            msg = "suspected_missed_scroll:滑动后 ROI 仍极像上一帧，可能漏滑"
            step_flags.append(msg)
            flags_all.append(msg)
            log.warning("%s (fine=%.4f)", msg, fine)

        log.info(
            "指标 fine=%.3f coarse(blur)=%.3f dHash_ham=%d | metric_quiet=%s(%s) streak_m=%d | "
            "end_hint=%s streak_h=%d",
            fine,
            coarse,
            dham,
            mq,
            mq_reason or "-",
            metric_streak,
            hint_now,
            hint_streak,
        )

        stop_reason: str | None = None
        if metric_streak >= stab_req:
            stop_reason = "roi_metric_stable"
        elif hint_streak >= stab_req:
            stop_reason = "ui_end_hint"

        rec = ScrollStepRecord(
            step_index=si,
            event="after_swipe_stable_stop" if stop_reason else "after_swipe",
            full_png=f"screenshots/full_{si:03d}.png",
            roi_png=f"screenshots/roi_{si:03d}.png",
            roi_mean_diff_vs_prev=fine,
            roi_coarse_diff_vs_prev=coarse,
            roi_dhash_hamming_vs_prev=dham,
            end_hint_visible=hint_now,
            metric_quiet=mq,
            metric_quiet_reason=mq_reason,
            metric_streak=metric_streak,
            hint_streak=hint_streak,
            roi_fingerprint=fp_curr,
            flags=step_flags,
        )
        records.append(rec)
        _append_jsonl(analysis_path, {**asdict(rec)})
        fingerprint_history.append((si, fp_curr))
        shutil.copyfile(curr_png, prev_png)

        if stop_reason:
            log.info("停止：%s（第 %d 次滑动后）", stop_reason, si)
            return si, True, records, _build_summary(stop_reason, si)

    log.error("达到最大滑动次数仍未满足静止或到底文案条件")
    return max_swipes, False, records, _build_summary("max_swipes", max_swipes)


@pytest.mark.integration
def test_scroll_product_detail_top_to_bottom(tmp_path: Path, u2_device: Any, gesture_profile: GestureProfile) -> None:
    """
    环境变量摘要：
      DOUBAO_DETAIL_MAX_SWIPES / DOUBAO_DETAIL_POST_SWIPE_SLEEP
      DOUBAO_DETAIL_STABLE_MEAN_DIFF / DOUBAO_DETAIL_ALT_FINE_MAX
      DOUBAO_DETAIL_RELAX_FINE_MAX / RELAX_COARSE / RELAX_DHASH
      DOUBAO_DETAIL_ROI_*_RATIO
    """
    _ = tmp_path

    session_dir = _new_session_dir()
    log = _setup_logger(session_dir)
    log.info("会话目录: %s", session_dir)

    nav = Navigator(u2_device)
    page, cur = nav.current_page()
    log.info("当前 Activity: %s", cur.get("activity", ""))
    if page is not Page.WEB_DETAIL:
        pytest.skip("当前不是商品详情 WebActivity；请先打开详情并停在页面最上方后再运行")

    loaded = nav.wait_web_detail_loaded(timeout=15)
    log.info("wait_web_detail_loaded => %s", loaded)
    assert loaded, "详情页在超时内未视为加载完成"

    gesture_profile.fc_detail_bottom_max_swipes = int(
        os.environ.get("DOUBAO_DETAIL_MAX_SWIPES", str(_DEFAULT_MAX_SWIPES))
    )
    gesture_profile.fc_detail_bottom_post_swipe_sleep = float(
        os.environ.get("DOUBAO_DETAIL_POST_SWIPE_SLEEP", str(_DEFAULT_POST_SWIPE_SLEEP))
    )
    gesture_profile.fc_detail_bottom_strict_fine = float(
        os.environ.get("DOUBAO_DETAIL_STABLE_MEAN_DIFF", str(_DEFAULT_STABLE_MEAN_DIFF))
    )
    gesture_profile.fc_detail_bottom_alt_fine = float(
        os.environ.get("DOUBAO_DETAIL_ALT_FINE_MAX", str(_DEFAULT_ALT_FINE_MAX))
    )
    gesture_profile.fc_detail_bottom_relax_fine = float(
        os.environ.get("DOUBAO_DETAIL_RELAX_FINE_MAX", str(_DEFAULT_RELAX_FINE_MAX))
    )
    gesture_profile.fc_detail_bottom_relax_coarse = float(
        os.environ.get("DOUBAO_DETAIL_RELAX_COARSE_MAX", str(_DEFAULT_RELAX_COARSE_MAX))
    )
    gesture_profile.fc_detail_bottom_relax_dhash = int(
        os.environ.get("DOUBAO_DETAIL_RELAX_DHASH_MAX", str(_DEFAULT_RELAX_DHASH_MAX))
    )
    gesture_profile.fc_detail_roi_y0 = float(os.environ.get("DOUBAO_DETAIL_ROI_TOP_RATIO", str(_DEFAULT_ROI_Y0)))
    gesture_profile.fc_detail_roi_y1 = float(
        os.environ.get("DOUBAO_DETAIL_ROI_BOTTOM_RATIO", str(_DEFAULT_ROI_Y1))
    )
    gesture_profile.fc_detail_roi_x0 = float(os.environ.get("DOUBAO_DETAIL_ROI_LEFT_RATIO", str(_DEFAULT_ROI_X0)))
    gesture_profile.fc_detail_roi_x1 = float(
        os.environ.get("DOUBAO_DETAIL_ROI_RIGHT_RATIO", str(_DEFAULT_ROI_X1))
    )

    max_swipes = gesture_profile.fc_detail_bottom_max_swipes
    swipes, stable, _records, summary = _scroll_detail_until_stable(
        u2_device,
        nav,
        gesture_profile,
        session_dir,
        log,
    )

    summary_path = session_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("已写入 summary.json，共 %d 次滑动，stopped_by=%s", swipes, summary["stopped_by"])
    log.info("ROI 唯一指纹数=%s，告警=%d", summary["unique_roi_fingerprints"], len(summary["flags"]))
    if summary["flags"]:
        for f in summary["flags"]:
            log.warning("[汇总告警] %s", f)

    assert stable, (
        f"在 {max_swipes} 次滑动后仍未判定到底；会话目录 {session_dir}（run.log / summary.json）"
    )
    assert nav.is_web_detail(), "结束后应仍在商品详情页"
    log.info("测试通过，会话目录: %s", session_dir)
