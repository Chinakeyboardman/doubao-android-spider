#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 jadx 反编译项目根目录的 APK，并生成结构化 Markdown 报告。

依赖：系统 PATH 中可执行 `jadx`（macOS: brew install jadx）。

用法（仓库根目录）:
  python recon/apk_decompile.py
  python recon/apk_decompile.py --apk /path/to/app.apk --out recon/output
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _find_jadx() -> str | None:
    return shutil.which("jadx")


def _run_jadx(jadx_bin: str, apk: Path, decompile_dir: Path, timeout_sec: int) -> None:
    decompile_dir.parent.mkdir(parents=True, exist_ok=True)
    if decompile_dir.is_dir():
        shutil.rmtree(decompile_dir)
    cmd = [
        jadx_bin,
        "-d",
        str(decompile_dir),
        "--show-bad-code",
        "--no-res",
        str(apk),
    ]
    # --no-res 加快首跑；若需要 layout/strings 再跑全量（去掉 --no-res）
    print("执行:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, timeout=timeout_sec)


def _run_jadx_with_resources(jadx_bin: str, apk: Path, decompile_dir: Path, timeout_sec: int) -> None:
    decompile_dir.parent.mkdir(parents=True, exist_ok=True)
    if decompile_dir.is_dir():
        shutil.rmtree(decompile_dir)
    cmd = [
        jadx_bin,
        "-d",
        str(decompile_dir),
        "--show-bad-code",
        str(apk),
    ]
    print("执行（含 resources）:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, timeout=timeout_sec)


def _parse_manifest_activities(manifest_path: Path) -> tuple[list[str], list[str]]:
    """返回 (activity 全名列表, application 相关包名提示)。"""
    activities: list[str] = []
    if not manifest_path.is_file():
        return activities, []
    try:
        tree = ET.parse(manifest_path)
        root = tree.getroot()
    except ET.ParseError:
        return activities, []

    ns = {"android": "http://schemas.android.com/apk/res/android"}
    pkg = root.get("package") or ""

    for act in root.findall(".//activity"):
        name = act.get(f"{{{ns['android']}}}name") or act.get("android:name")
        if not name:
            continue
        if name.startswith("."):
            name = pkg + name
        elif "." not in name:
            name = f"{pkg}.{name}" if pkg else name
        activities.append(name)

    return activities, [pkg] if pkg else []


def _grep_layout_widgets(layout_dir: Path, patterns: tuple[str, ...], limit: int) -> list[str]:
    hits: list[str] = []
    if not layout_dir.is_dir():
        return hits
    res_dir = layout_dir.parent
    for p in sorted(layout_dir.glob("*.xml")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(pat in text for pat in patterns):
            hits.append(str(p.relative_to(res_dir)))
            if len(hits) >= limit:
                break
    return hits


def _snippet_strings_xml(strings_path: Path, keywords: tuple[str, ...], max_lines: int) -> list[str]:
    lines_out: list[str] = []
    if not strings_path.is_file():
        return lines_out
    try:
        lines = strings_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return lines_out
    for line in lines:
        if any(k in line for k in keywords):
            lines_out.append(line.strip()[:200])
            if len(lines_out) >= max_lines:
                break
    return lines_out


def _find_manifest(decompile_dir: Path) -> Path | None:
    candidates = list(decompile_dir.rglob("AndroidManifest.xml"))
    for c in candidates:
        if "resources" in c.parts or "res" in c.parts:
            return c
    return candidates[0] if candidates else None


def write_report(
    *,
    decompile_dir: Path,
    report_path: Path,
    apk: Path,
    jadx_used_resources: bool,
) -> None:
    manifest = _find_manifest(decompile_dir)
    activities, pkg_hints = _parse_manifest_activities(manifest) if manifest else ([], [])

    layout_dir = decompile_dir / "resources" / "res" / "layout"
    res_base = decompile_dir / "resources" / "res"
    strings_path = decompile_dir / "resources" / "res" / "values" / "strings.xml"

    list_hits = _grep_layout_widgets(
        layout_dir,
        ("RecyclerView", "ListView", "GridView", "ViewPager2"),
        limit=80,
    )

    product_kw = ("商品", "购买", "详情", "价格", "shop", "product", "goods", "mall", "cart")
    string_hits = _snippet_strings_xml(strings_path, product_kw, max_lines=40)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# APK 结构侦察报告（jadx）",
        "",
        f"- **APK**: `{apk}`",
        f"- **反编译输出目录**: `{decompile_dir}`",
        f"- **含 resources**: {jadx_used_resources}",
        f"- **Manifest**: `{manifest or '未找到'}`",
        "",
        "## Package / Application",
        "",
    ]
    if pkg_hints:
        lines.append(f"- `package`: `{pkg_hints[0]}`")
    lines.append("")
    lines.append("## Activity 列表（AndroidManifest）")
    lines.append("")
    if activities:
        for a in sorted(set(activities)):
            lines.append(f"- `{a}`")
    else:
        lines.append("_未解析到 activity（检查是否使用带 resources 的反编译或 manifest 路径）。_")
    lines.extend(["", "## Layout 中含列表控件的文件（抽样）", ""])
    if list_hits:
        for h in list_hits:
            lines.append(f"- `{h}`")
    else:
        lines.append("_未找到 layout 目录或未命中 RecyclerView/ListView（需带 resources 的 jadx 输出）。_")
    lines.extend(["", "## strings.xml 中含商品相关关键字的行（抽样）", ""])
    if string_hits:
        for s in string_hits:
            lines.append(f"- `{s}`")
    else:
        lines.append("_未找到 strings.xml 或无匹配行。_")
    lines.extend(
        [
            "",
            "---",
            "",
            "下一步：结合 `recon/ui_spy.py` 在真机操作列表/详情页，对照 resource-id 与 Activity。",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入报告: {report_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="jadx 反编译 APK 并生成 recon 报告")
    parser.add_argument(
        "--apk",
        type=Path,
        default=None,
        help="APK 路径（默认仓库根 doubao_original.apk）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出根目录（默认 recon/output）",
    )
    parser.add_argument(
        "--skip-jadx",
        action="store_true",
        help="跳过 jadx，仅基于已有反编译目录生成报告",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="jadx 超时秒数（大 APK 可调大）",
    )
    args = parser.parse_args()

    root = _repo_root()
    apk = (args.apk or (root / "doubao_original.apk")).resolve()
    out_root = (args.out or (root / "recon" / "output")).resolve()
    decompile_dir = out_root / "jadx_decompiled"
    report_path = out_root / "app_structure_report.md"

    if not apk.is_file():
        print(f"未找到 APK: {apk}", file=sys.stderr)
        return 2

    jadx_bin = _find_jadx()
    if not args.skip_jadx:
        if not jadx_bin:
            print("未在 PATH 找到 jadx。请安装: brew install jadx", file=sys.stderr)
            return 127
        print("首次建议全量反编译（含 resources），耗时较长…")
        try:
            _run_jadx_with_resources(jadx_bin, apk, decompile_dir, timeout_sec=args.timeout)
        except subprocess.TimeoutExpired:
            print("jadx 超时。可加 --timeout 或稍后重试。", file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as e:
            print(f"jadx 失败: {e}", file=sys.stderr)
            return 1
        jadx_res = True
    else:
        if not decompile_dir.is_dir():
            print(f"--skip-jadx 但目录不存在: {decompile_dir}", file=sys.stderr)
            return 2
        jadx_res = (decompile_dir / "resources").is_dir()

    write_report(
        decompile_dir=decompile_dir,
        report_path=report_path,
        apk=apk,
        jadx_used_resources=jadx_res,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
