#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豆包 APK 仓库管理：扫描、列表、安装、拉取、版本比对。

目录约定（均在 var/ 下，不入 git）::

    var/apk/com.larus.nova/
      manifest.json                 # 版本索引 + default_version
      com.larus.nova_14.1.0.apk     # 正式包（推荐平铺在包目录）
      pulled/                       # adb pull 暂存

示例::

    .venv/bin/python scripts/doubao_apk.py scan
    .venv/bin/python scripts/doubao_apk.py list -s <serial>
    .venv/bin/python scripts/doubao_apk.py install --version 14.1.0 -s <serial>
    .venv/bin/python scripts/doubao_apk.py pull -s <serial>
    .venv/bin/python scripts/doubao_apk.py set-default 14.1.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.doubao_apk import (  # noqa: E402
    decide_install,
    format_status_report,
    install_apk,
    load_manifest,
    parse_apk_file,
    pull_installed_apk,
    resolve_apk_path,
    save_manifest,
    scan_store,
)


def _add_serial(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--serial", default=None, help="adb 设备序列号")


def cmd_scan(_args: argparse.Namespace) -> int:
    manifest = scan_store()
    print(f"已扫描 {len(manifest.versions)} 个版本 → {manifest_path_display()}")
    for v in manifest.versions:
        mark = " (默认)" if v.version_name == manifest.default_version else ""
        print(f"  {v.display()}  {v.file}{mark}")
    return 0


def manifest_path_display() -> str:
    from app.utils.doubao_apk import manifest_path

    return str(manifest_path())


def cmd_list(args: argparse.Namespace) -> int:
    print(format_status_report(serial=args.serial))
    return 0


def cmd_set_default(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    names = {v.version_name for v in manifest.versions}
    if args.version not in names:
        print(f"未找到版本 {args.version}，请先 scan。已有: {', '.join(sorted(names)) or '无'}", file=sys.stderr)
        return 1
    manifest.default_version = args.version
    save_manifest(manifest)
    print(f"默认版本已设为 {args.version}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    try:
        apk_path = resolve_apk_path(args.version)
        target = parse_apk_file(apk_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    from app.utils.doubao_apk import get_installed_version

    installed = get_installed_version(serial=args.serial)
    decision = decide_install(target, installed, force=args.force)

    print(f"目标: {target.display()}  ({apk_path.name})")
    if installed:
        print(f"设备: {installed.display()}")
    else:
        print("设备: 未安装")
    print(f"判断: {decision.reason}")

    if decision.action == "skip":
        print("✅ 跳过安装")
        return 0
    if decision.action == "warn_newer_device" and not args.force:
        print("⚠️  设备版本更高，若仍要降级请加 --force", file=sys.stderr)
        return 2

    ok, msg = install_apk(apk_path, serial=args.serial, uninstall_first=args.uninstall_first)
    if ok:
        print(f"✅ {msg}")
        return 0
    print(f"❌ {msg}", file=sys.stderr)
    return 1


def cmd_pull(args: argparse.Namespace) -> int:
    try:
        info = pull_installed_apk(serial=args.serial)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1
    print(f"✅ 已拉取 {info.display()} → {info.file}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="豆包 APK 多版本仓库管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描 var/apk 下 APK 并更新 manifest.json")
    p_scan.set_defaults(func=cmd_scan)

    p_list = sub.add_parser("list", help="列出本地仓库与设备版本对照")
    _add_serial(p_list)
    p_list.set_defaults(func=cmd_list)

    p_default = sub.add_parser("set-default", help="设置默认安装/适配版本")
    p_default.add_argument("version", help="versionName，如 14.1.0")
    p_default.set_defaults(func=cmd_set_default)

    p_install = sub.add_parser("install", help="按默认或指定版本安装到设备")
    _add_serial(p_install)
    p_install.add_argument("--version", default=None, help="versionName；默认 manifest.default_version")
    p_install.add_argument("--force", action="store_true", help="强制安装（含同版本重装、设备更高版本降级）")
    p_install.add_argument("--uninstall-first", action="store_true", help="安装前先卸载")
    p_install.set_defaults(func=cmd_install)

    p_pull = sub.add_parser("pull", help="从设备拉取当前豆包 APK 到 pulled/")
    _add_serial(p_pull)
    p_pull.set_defaults(func=cmd_pull)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
