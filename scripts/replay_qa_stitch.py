#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用已有 shot_*.png 离线重拼长图（v2 重叠估计 + 诊断报告）。

示例：
  .venv/bin/python scripts/replay_qa_stitch.py \\
    var/vivo-x-fold6/spot_check/20260714/qa_capture/2026-07-16/130041
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.profile_loader import load_profile
from app.modules.detail_strip_stitch import (
    crop_fullscreen_to_detail_content,
    stitch_content_strips_vertical_v2,
)


def _qa_profile(device_profile: str):
    p = load_profile(device_name=device_profile)
    return replace(
        p,
        fc_detail_roi_y0=p.qa_shot_roi_y0,
        fc_detail_roi_y1=p.qa_shot_roi_y1,
        fc_detail_strip_roi_x0=0.0,
        fc_detail_strip_roi_x1=1.0,
    )


def _list_shots(session_dir: Path, prefix: str) -> list[Path]:
    paths = sorted(session_dir.glob(f"{prefix}_*.png"))
    return [p for p in paths if p.stem[len(prefix) + 1 :].isdigit()]


def _write_report(
    session_dir: Path,
    *,
    shot_paths: list[Path],
    legacy_h: int,
    v2_h: int,
    diagnoses: list,
) -> None:
    lines = [
        "# 长图拼接离线重拼报告（v2）",
        "",
        f"- 会话目录: `{session_dir}`",
        f"- 生成时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- 输入帧: {len(shot_paths)} 张 `shot_*.png`",
        f"- 旧方案高度: **{legacy_h}px** → `full.png`（已有）",
        f"- v2 高度: **{v2_h}px** → `full_stitch_v2.png`",
        f"- 去掉重复高度: **{legacy_h - v2_h}px**（v2 更短）",
        "",
        "## 逐对诊断",
        "",
        "| 帧对 | v2重叠 | v2占比 | 匹配分 | 旧重叠 | 诊断 |",
        "|------|--------|--------|--------|--------|------|",
    ]
    diag_cn = {
        "ok": "正常",
        "near_duplicate": "近重复帧（滑动几乎没动）",
        "gap_risk": "漏截风险（重叠过小）",
        "weak_match": "匹配置信低",
    }
    for d in diagnoses:
        lines.append(
            f"| {d.above_frame}→{d.below_frame} "
            f"| {d.overlap_px}px | {d.overlap_frac:.1%} "
            f"| {d.match_score:.1f} | {d.legacy_overlap_px}px "
            f"| {diag_cn.get(d.diagnosis, d.diagnosis)} |"
        )

    near_dup = sum(1 for d in diagnoses if d.diagnosis == "near_duplicate")
    gap = sum(1 for d in diagnoses if d.diagnosis == "gap_risk")
    fixed = sum(
        1 for d in diagnoses
        if d.legacy_overlap_px + 80 < d.overlap_px and d.diagnosis == "near_duplicate"
    )
    lines.extend(
        [
            "",
            "## 摘要",
            "",
            f"- 近重复帧对: **{near_dup}**（采集层应丢帧，本次仅修正拼接）",
            f"- 漏截风险帧对: **{gap}**",
            f"- 旧算法明显低估重叠的帧对: **{fixed}**",
            "",
            "## 肉眼验收",
            "",
            "1. 对比 `full.png` 与 `full_stitch_v2.png`，看重复段落是否消失。",
            "2. 查表中带「近重复帧」的行：v2 应把重叠提高到 60%+。",
            "3. 若 v2 仍有个别重复，多半是相邻 shot 内容不同但旧帧被误保留（采集问题）。",
            "",
        ]
    )
    (session_dir / "STITCH_REPORT_v2.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="离线重拼 QA 长图（v2）")
    parser.add_argument("session_dir", help="qa_capture 会话目录（含 shot_*.png）")
    parser.add_argument(
        "--device-profile",
        default="vivo_v2301a",
        help="gesture profile（默认 vivo_v2301a）",
    )
    parser.add_argument(
        "--prefix",
        default="shot",
        help="截图前缀（默认 shot，不含 shot_think）",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir).resolve()
    if not session_dir.is_dir():
        print(f"目录不存在: {session_dir}", file=sys.stderr)
        return 1

    shot_paths = _list_shots(session_dir, args.prefix)
    if len(shot_paths) < 1:
        print(f"未找到 {args.prefix}_*.png", file=sys.stderr)
        return 1

    profile = _qa_profile(args.device_profile)
    labels = [p.name for p in shot_paths]
    crops = [crop_fullscreen_to_detail_content(str(p), profile) for p in shot_paths]

    legacy, _ = stitch_content_strips_vertical_v2(
        crops,
        use_v2_overlap=False,
        frame_labels=labels,
    )
    v2_img, diagnoses = stitch_content_strips_vertical_v2(
        crops,
        use_v2_overlap=True,
        frame_labels=labels,
    )

    out_v2 = session_dir / "full_stitch_v2.png"
    v2_img.save(out_v2, format="PNG", optimize=True)

    diag_path = session_dir / "stitch_diagnosis_v2.json"
    payload = {
        "session_dir": str(session_dir),
        "device_profile": args.device_profile,
        "shot_count": len(shot_paths),
        "legacy_height_px": legacy.height,
        "v2_height_px": v2_img.height,
        "height_delta_px": legacy.height - v2_img.height,
        "pairs": [d.to_dict() for d in diagnoses],
    }
    diag_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _write_report(
        session_dir,
        shot_paths=shot_paths,
        legacy_h=legacy.height,
        v2_h=v2_img.height,
        diagnoses=diagnoses,
    )

    print(f"输入: {len(shot_paths)} 帧")
    print(f"旧方案高度: {legacy.height}px")
    print(f"v2 高度: {v2_img.height}px（-{legacy.height - v2_img.height}px 重复）")
    print(f"输出: {out_v2}")
    print(f"诊断: {diag_path}")
    print(f"报告: {session_dir / 'STITCH_REPORT_v2.md'}")
    for d in diagnoses:
        flag = ""
        if d.legacy_overlap_px + 80 < d.overlap_px:
            flag = " ↑修正"
        print(
            f"  {d.above_frame}→{d.below_frame}: "
            f"v2={d.overlap_px}px({d.overlap_frac:.0%}) "
            f"legacy={d.legacy_overlap_px}px "
            f"{d.diagnosis}{flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
