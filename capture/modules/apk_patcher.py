"""从设备提取豆包 APK，经 apk-mitm 去 SSL pinning，再安装修改版。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.step_journal import append_doc_step
from app.utils.utils import ensure_directory, log_error, log_info

from capture.config.config import CAPTURE_CONFIG
from capture.utils.adb_helper import (
    AdbError,
    adb_available,
    get_package_apk_remote_paths,
    install_apk,
    install_multiple_apks,
    pull_file,
    uninstall_package,
)


def _safe_local_name(remote_path: str) -> str:
    base = remote_path.rstrip("/").split("/")[-1]
    if not base.endswith(".apk"):
        base = f"{base}.apk"
    return re.sub(r"[^a-zA-Z0-9._-]", "_", base) or "pulled.apk"


def _sort_install_order(paths: list[Path]) -> list[Path]:
    """install-multiple 要求 base 在前。"""

    def key(p: Path) -> tuple[int, str]:
        name = p.name.lower()
        is_base = 0 if "base" in name else 1
        return (is_base, name)

    return sorted(paths, key=key)


def _resolve_java_home() -> str | None:
    """macOS 上 `/usr/bin/java` 常为占位；优先 JAVA_HOME，其次 Homebrew openjdk。"""
    env_jh = (os.environ.get("JAVA_HOME") or "").strip()
    if env_jh:
        p = Path(env_jh)
        if (p / "bin" / "java").is_file():
            return str(p)
    for c in (
        Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"),
        Path("/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"),
        Path("/opt/homebrew/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
        Path("/usr/local/opt/openjdk/libexec/openjdk.jdk/Contents/Home"),
    ):
        if (c / "bin" / "java").is_file():
            return str(c)
    return None


def _env_for_apk_mitm() -> dict[str, str]:
    env = os.environ.copy()
    jh = _resolve_java_home()
    if jh:
        env["JAVA_HOME"] = jh
        env["PATH"] = f"{jh}/bin:{env.get('PATH', '')}"
    return env


@dataclass
class ApkPatchResult:
    """APK 重打包流程结果。"""

    ok: bool
    package_name: str
    workspace: Path
    pulled_paths: list[Path] = field(default_factory=list)
    patched_paths: list[Path] = field(default_factory=list)
    message: str = ""

    def patched_single(self) -> Path | None:
        if len(self.patched_paths) == 1:
            return self.patched_paths[0]
        return None


class ApkPatcher:
    """编排：pull → apk-mitm → uninstall → install。"""

    def __init__(self, workspace_root: Path | None = None) -> None:
        root = workspace_root or CAPTURE_CONFIG["apk_workspace_root"]
        self._workspace = Path(root).resolve()
        self._package: str = CAPTURE_CONFIG["package_name"]
        self._apk_mitm_cmd: str = CAPTURE_CONFIG["apk_mitm_command"]

    def _resolve_workspace(self) -> Path:
        ensure_directory(str(self._workspace))
        return self._workspace

    def _which_apk_mitm(self) -> str | None:
        return shutil.which(self._apk_mitm_cmd)

    def _run_apk_mitm(self, apk_path: Path) -> Path:
        """对单个 APK 执行 apk-mitm，返回生成的 *-patched.apk 路径。"""
        cmd_path = self._which_apk_mitm()
        if not cmd_path:
            raise AdbError(
                f"未找到命令 `{self._apk_mitm_cmd}`，请先安装: npm install -g apk-mitm"
            )
        if not _resolve_java_home():
            raise AdbError(
                "未检测到可用 JDK（apk-mitm 需要 Java）。请安装: "
                "brew install openjdk@17，并在 shell 中 export JAVA_HOME，"
                "或执行: sudo ln -sfn /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk "
                "/Library/Java/JavaVirtualMachines/openjdk-17.jdk"
            )
        log_info(f"运行 apk-mitm: {apk_path.name}")
        proc = subprocess.run(
            [cmd_path, str(apk_path)],
            cwd=str(apk_path.parent),
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
            env=_env_for_apk_mitm(),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise AdbError(f"apk-mitm 失败 (code={proc.returncode}): {err}")
        expected = apk_path.with_name(f"{apk_path.stem}-patched.apk")
        if not expected.is_file():
            raise AdbError(f"apk-mitm 未生成预期文件: {expected}")
        return expected

    def run(
        self,
        *,
        device_serial: str | None = None,
        skip_uninstall: bool = False,
        skip_install: bool = False,
        pull_only: bool = False,
        source_apks: list[Path] | None = None,
    ) -> ApkPatchResult:
        ws = self._resolve_workspace()
        pkg = self._package
        append_doc_step(
            "初始化工作目录",
            "成功",
            f"workspace={ws}; package={pkg}; pull_only={pull_only}; "
            f"skip_install={skip_install}; skip_uninstall={skip_uninstall}; "
            f"source_apks={'是' if source_apks else '否'}",
        )

        if not adb_available():
            msg = "未找到 adb，请安装 Android platform-tools 并加入 PATH"
            log_error(msg)
            append_doc_step("检查 adb", "失败", msg)
            return ApkPatchResult(False, pkg, ws, message=msg)
        append_doc_step("检查 adb", "成功", "adb 在 PATH 中可用")

        pulled: list[Path] = []
        if source_apks:
            for src in source_apks:
                p = Path(src).expanduser().resolve()
                if not p.is_file():
                    msg = f"本地 APK 不存在: {p}"
                    log_error(msg)
                    append_doc_step("准备原版 APK（本地复制）", "失败", msg)
                    return ApkPatchResult(False, pkg, ws, message=msg)
                dest = ws / p.name
                if p.resolve() != dest.resolve():
                    log_info(f"复制 APK 到工作目录: {p} -> {dest}")
                    shutil.copy2(p, dest)
                    pulled.append(dest)
                else:
                    pulled.append(p)
            append_doc_step(
                "准备原版 APK（本地复制）",
                "成功",
                f"共 {len(pulled)} 个: " + ", ".join(str(x) for x in pulled),
            )
        else:
            try:
                remote_list = get_package_apk_remote_paths(pkg, serial=device_serial)
            except AdbError as e:
                log_error(str(e))
                append_doc_step("解析设备上 APK 路径（pm path）", "失败", str(e))
                return ApkPatchResult(False, pkg, ws, message=str(e))
            append_doc_step(
                "解析设备上 APK 路径（pm path）",
                "成功",
                f"共 {len(remote_list)} 个: " + "; ".join(remote_list),
            )

            for remote in remote_list:
                local_name = _safe_local_name(remote)
                local_path = ws / local_name
                log_info(f"拉取 APK: {remote} -> {local_path}")
                try:
                    pull_file(remote, local_path, serial=device_serial)
                except AdbError as e:
                    log_error(str(e))
                    append_doc_step(
                        "adb pull 原版 APK",
                        "失败",
                        f"remote={remote}; 已得到 {len(pulled)} 个; 错误: {e}",
                    )
                    return ApkPatchResult(False, pkg, ws, pulled_paths=pulled, message=str(e))
                pulled.append(local_path)
            append_doc_step(
                "adb pull 原版 APK",
                "成功",
                f"共 {len(pulled)} 个: " + ", ".join(str(x) for x in pulled),
            )

        if pull_only:
            log_info("pull_only=True，跳过 apk-mitm 与安装")
            append_doc_step("流程分支", "跳过", "pull_only=True，未执行 apk-mitm 与安装")
            return ApkPatchResult(True, pkg, ws, pulled_paths=pulled, message="仅拉取完成")

        patched: list[Path] = []
        for apk in pulled:
            try:
                out = self._run_apk_mitm(apk)
                patched.append(out)
                append_doc_step(
                    "apk-mitm 重打包（去 SSL pinning）",
                    "成功",
                    f"{apk.name} → {out.name}",
                )
            except AdbError as e:
                log_error(str(e))
                append_doc_step(
                    "apk-mitm 重打包（去 SSL pinning）",
                    "失败",
                    f"当前文件: {apk}; 错误: {e}",
                )
                return ApkPatchResult(
                    False, pkg, ws, pulled_paths=pulled, patched_paths=patched, message=str(e)
                )

        if skip_install:
            log_info("skip_install=True，跳过卸载与安装")
            append_doc_step(
                "流程分支",
                "跳过",
                "skip_install=True，未卸载/未安装；patched: "
                + ", ".join(p.name for p in patched),
            )
            return ApkPatchResult(
                True,
                pkg,
                ws,
                pulled_paths=pulled,
                patched_paths=patched,
                message="已生成 patched APK，未安装",
            )

        if not skip_uninstall:
            log_info(f"卸载已安装包: {pkg}")
            u = uninstall_package(pkg, serial=device_serial)
            ur = u.returncode
            uout = (u.stdout or u.stderr or "").strip()
            append_doc_step(
                "卸载设备上已安装包",
                "成功" if ur == 0 else "失败",
                f"package={pkg}; exit={ur}" + (f"; {uout}" if uout else ""),
            )
        else:
            append_doc_step("卸载设备上已安装包", "跳过", "skip_uninstall=True")

        ordered = _sort_install_order(patched)
        log_info(f"安装修改版 APK（{len(ordered)} 个文件）…")
        if len(ordered) == 1:
            proc = install_apk(ordered[0], serial=device_serial)
        else:
            proc = install_multiple_apks(ordered, serial=device_serial)

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            msg = f"安装失败 (code={proc.returncode}): {err}"
            log_error(msg)
            append_doc_step(
                "adb install 修改版 APK",
                "失败",
                f"文件数={len(ordered)}; {msg}",
            )
            return ApkPatchResult(
                False,
                pkg,
                ws,
                pulled_paths=pulled,
                patched_paths=patched,
                message=msg,
            )

        append_doc_step(
            "adb install 修改版 APK",
            "成功",
            f"文件数={len(ordered)}: " + ", ".join(p.name for p in ordered),
        )
        log_info("✅ 修改版已安装，请在手机上打开豆包并重新登录")
        log_info(
            "若登录提示「授权失败 / 应用签名与配置不一致」：重打包会换成调试签名，"
            "服务端或 SDK 校验原签名时会失败；可改用官方 APK + Frida 仅去 SSL pinning，"
            "或继续用官方包做 UI 自动化（不经 mitm 解密接口）。"
        )
        append_doc_step(
            "APK 重打包流程结束",
            "成功",
            "修改版已安装；若遇授权/签名问题见运行日志中的说明。",
        )
        return ApkPatchResult(
            True,
            pkg,
            ws,
            pulled_paths=pulled,
            patched_paths=patched,
            message="安装完成",
        )
