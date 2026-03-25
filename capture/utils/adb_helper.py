"""adb 命令封装（子进程），供 capture 流程使用。"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class AdbError(RuntimeError):
    """adb 命令执行失败。"""


def adb_available() -> bool:
    return shutil.which("adb") is not None


def _adb_prefix(serial: str | None) -> list[str]:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    return cmd


def run_adb(
    args: list[str],
    *,
    serial: str | None = None,
    check: bool = True,
    timeout: int | None = 120,
) -> subprocess.CompletedProcess[str]:
    """执行 adb 子命令（不含前缀 `adb`）。"""
    full = _adb_prefix(serial) + args
    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        to = timeout if timeout is not None else "∞"
        cmd_s = " ".join(full)
        hint = (
            f"adb 在 {to}s 内未完成: {cmd_s}。\n"
            "请检查：手机亮屏已解锁、USB 调试授权弹窗已点「允许」、"
            "`adb devices` 状态为 device（非 unauthorized/offline）。\n"
            "可尝试：`adb kill-server && adb start-server`，换 USB 口/线，关闭手机「仅充电」模式。\n"
            "若本机已有拉取的 APK，可跳过设备查询："
            "`python run_capture.py patch --apk /path/to/base.apk --skip-install`。"
        )
        raise AdbError(hint) from e
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise AdbError(f"adb {' '.join(args)} 失败 (code={proc.returncode}): {err}")
    return proc


def get_package_apk_remote_paths(package: str, *, serial: str | None = None) -> list[str]:
    """解析 `pm path` 输出，返回设备上 APK 路径列表（不含 `package:` 前缀）。"""
    # 部分机型 pm path 较慢；华为等若休眠/弹窗未处理易挂死至超时
    proc = run_adb(["shell", "pm", "path", package], serial=serial, check=True, timeout=180)
    out = (proc.stdout or "").strip()
    if not out or "package:" not in out:
        raise AdbError(f"包未安装或无法解析 pm path: {package}\n{out}")
    paths: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            paths.append(line[len("package:") :].strip())
    if not paths:
        raise AdbError(f"未从 pm path 解析到 APK 路径: {out}")
    return paths


def pull_file(remote_path: str, local_path: Path, *, serial: str | None = None) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    run_adb(["pull", remote_path, str(local_path)], serial=serial, check=True, timeout=600)


def uninstall_package(package: str, *, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    """卸载包；若未安装，部分设备仍返回非零，调用方可视情况忽略。"""
    return run_adb(["uninstall", package], serial=serial, check=False, timeout=300)


def install_apk(local_apk: Path, *, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    return run_adb(["install", "-r", str(local_apk)], serial=serial, check=False, timeout=600)


def install_apk_via_download(
    local_apk: Path,
    *,
    serial: str | None = None,
    uninstall_package_name: str | None = None,
) -> tuple[str, subprocess.CompletedProcess[str]]:
    """
    将 APK adb push 到设备主存储的 Download，再 `pm install -r -t`。

    与 `adb install` 传输的总数据量相近，不一定更快；适合：希望先在「下载」里看到文件、
    用手动点击安装，或 push 完成后单独重试 `pm install`。
    """
    apk = Path(local_apk).expanduser().resolve()
    if not apk.is_file():
        raise AdbError(f"本地 APK 不存在: {apk}")

    ext = shell_external_storage(serial=serial).rstrip("/")
    remote_apk = f"{ext}/Download/{apk.name}"

    run_adb(["push", str(apk), remote_apk], serial=serial, check=True, timeout=3600)

    if uninstall_package_name:
        uninstall_package(uninstall_package_name, serial=serial)

    proc = run_adb(
        ["shell", "pm", "install", "-r", "-t", remote_apk],
        serial=serial,
        check=False,
        timeout=600,
    )
    return remote_apk, proc


def install_multiple_apks(
    local_apks: list[Path],
    *,
    serial: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """多 APK 安装（split / bundle 安装会话）。"""
    if not local_apks:
        raise AdbError("install_multiple_apks: 路径列表为空")
    args = ["install-multiple", "-r"] + [str(p) for p in local_apks]
    return run_adb(args, serial=serial, check=False, timeout=600)


def reverse_tcp(
    device_port: int,
    host_port: int,
    *,
    serial: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`adb reverse tcp:device tcp:host`，手机访问 127.0.0.1:device_port 即本机 host_port。"""
    spec = f"tcp:{device_port}"
    return run_adb(
        ["reverse", spec, f"tcp:{host_port}"],
        serial=serial,
        check=True,
        timeout=60,
    )


def reverse_remove_tcp(port: int, *, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    """`adb reverse --remove tcp:<port>`。"""
    return run_adb(
        ["reverse", "--remove", f"tcp:{port}"],
        serial=serial,
        check=False,
        timeout=60,
    )


def forward_tcp(
    host_port: int,
    device_port: int,
    *,
    serial: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """`adb forward tcp:host tcp:device`：本机连接 127.0.0.1:host_port 即访问设备上的 device_port（如 Frida Gadget listen）。"""
    return run_adb(
        ["forward", f"tcp:{host_port}", f"tcp:{device_port}"],
        serial=serial,
        check=True,
        timeout=60,
    )


def forward_remove_tcp(port: int, *, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    """`adb forward --remove tcp:<port>`（本机侧端口，与 `forward tcp:PORT ...` 一致）。"""
    return run_adb(
        ["forward", "--remove", f"tcp:{port}"],
        serial=serial,
        check=False,
        timeout=60,
    )


def set_global_http_proxy(host: str, port: int, *, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    """`settings put global http_proxy host:port`（需设备支持）。"""
    value = f"{host}:{port}"
    return run_adb(
        ["shell", "settings", "put", "global", "http_proxy", value],
        serial=serial,
        check=True,
        timeout=30,
    )


def clear_global_http_proxy(*, serial: str | None = None) -> subprocess.CompletedProcess[str]:
    """取消全局 HTTP 代理（`:0`）。"""
    return run_adb(
        ["shell", "settings", "put", "global", "http_proxy", ":0"],
        serial=serial,
        check=True,
        timeout=30,
    )


def get_global_http_proxy(*, serial: str | None = None) -> str:
    """当前 `global http_proxy` 值（空字符串表示未设置或读失败）。"""
    proc = run_adb(
        ["shell", "settings", "get", "global", "http_proxy"],
        serial=serial,
        check=False,
        timeout=30,
    )
    return (proc.stdout or "").strip()


def shell_external_storage(*, serial: str | None = None) -> str:
    """设备上主存储根路径（多为 `/storage/emulated/0`），用于拼接 Download / Documents。"""
    proc = run_adb(
        ["shell", "sh", "-c", 'echo -n "$EXTERNAL_STORAGE"'],
        serial=serial,
        check=False,
        timeout=30,
    )
    path = (proc.stdout or "").strip()
    if not path or path in ("null", "None"):
        return "/storage/emulated/0"
    return path


def remote_mkdir_p(remote_dir: str, *, serial: str | None = None) -> None:
    """`mkdir -p`（路径需无 shell 元字符）。"""
    run_adb(["shell", "mkdir", "-p", remote_dir], serial=serial, check=True, timeout=30)


def remote_file_exists(remote_path: str, *, serial: str | None = None) -> bool:
    proc = run_adb(
        ["shell", "test", "-f", remote_path],
        serial=serial,
        check=False,
        timeout=30,
    )
    return proc.returncode == 0


def remote_ls_dir(remote_dir: str, *, serial: str | None = None) -> str:
    """列目录（用于推送后给用户核对）；失败时返回空串。"""
    proc = run_adb(["shell", "ls", "-la", remote_dir], serial=serial, check=False, timeout=30)
    return (proc.stdout or proc.stderr or "").strip()
