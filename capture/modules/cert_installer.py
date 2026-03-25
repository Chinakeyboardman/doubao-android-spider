"""mitmproxy 用户 CA 证书：确保本机已生成、adb 推送到手机 Download。"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from capture.config.config import CAPTURE_CONFIG
from capture.utils.adb_helper import (
    AdbError,
    remote_file_exists,
    remote_ls_dir,
    remote_mkdir_p,
    run_adb,
    shell_external_storage,
)


class CertInstallerError(RuntimeError):
    """证书生成或推送失败。"""


@dataclass(frozen=True)
class MitmCaPushResult:
    """推送结果：多路径 + 设备目录列表，便于在文件管理器里定位。"""

    paths: tuple[str, ...]
    external_storage: str
    download_ls: str


def mitm_confdir() -> Path:
    name = str(CAPTURE_CONFIG.get("mitm_confdir_name") or ".mitmproxy")
    return Path.home() / name


def mitm_ca_cert_path() -> Path:
    fn = str(CAPTURE_CONFIG.get("mitm_ca_cert_filename") or "mitmproxy-ca-cert.cer")
    return mitm_confdir() / fn


def ensure_mitmproxy_ca_generated(*, timeout: float = 10.0) -> Path:
    """若 `~/.mitmproxy/mitmproxy-ca-cert.cer` 不存在，短暂启动 mitmdump 以生成证书。"""
    cer = mitm_ca_cert_path()
    if cer.is_file():
        return cer
    if shutil.which("mitmdump") is None:
        raise CertInstallerError("未找到 mitmdump，请先安装 mitmproxy（如 brew install mitmproxy）")
    mitm_confdir().mkdir(parents=True, exist_ok=True)
    boot_port = 18080
    proc = subprocess.Popen(
        ["mitmdump", "--listen-port", str(boot_port), "-q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        end = time.time() + timeout
        while time.time() < end:
            if cer.is_file():
                break
            time.sleep(0.15)
        if not cer.is_file():
            raise CertInstallerError(
                "mitmproxy CA 仍未生成，请在本机手动运行一次: mitmproxy --listen-port 8080"
            )
        return cer
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def push_mitm_ca_to_download(*, serial: str | None = None) -> MitmCaPushResult:
    """
    将 CA 推到设备主存储下的多个目录（默认 Download + Documents），避免部分机型
    「下载」里不刷新或路径与 `/sdcard/Download` 不一致导致找不到文件。
    """
    cer = mitm_ca_cert_path()
    if not cer.is_file():
        raise CertInstallerError(f"本机证书不存在: {cer}，请先执行 ensure_mitmproxy_ca_generated")

    ext = shell_external_storage(serial=serial).rstrip("/")
    subs = CAPTURE_CONFIG.get("device_cert_fallback_subdirs") or ("Download", "Documents")
    if isinstance(subs, str):
        subs = (subs,)

    push_targets: list[str] = []
    for sub in subs:
        sub = str(sub).strip("/")
        push_targets.append(f"{ext}/{sub}/{cer.name}")

    legacy = str(CAPTURE_CONFIG.get("device_cert_push_dir") or "/sdcard/Download").rstrip("/")
    push_targets.append(f"{legacy}/{cer.name}")

    push_targets = _dedupe_paths(push_targets)

    verified: list[str] = []
    last_err: str | None = None
    for remote_path in push_targets:
        parent = str(Path(remote_path).parent)
        try:
            remote_mkdir_p(parent, serial=serial)
            run_adb(["push", str(cer), remote_path], serial=serial, check=True, timeout=120)
        except (AdbError, OSError) as e:
            last_err = str(e)
            continue
        if remote_file_exists(remote_path, serial=serial):
            verified.append(remote_path)

    if not verified:
        diag = remote_ls_dir(f"{ext}/Download", serial=serial)
        raise CertInstallerError(
            "证书已尝试推送到设备，但校验时未找到文件。"
            f" EXTERNAL_STORAGE={ext!r}；最后一次 adb 错误: {last_err!r}。"
            f" 目录列表片段:\n{diag[:800]}"
        )

    download_ls = remote_ls_dir(f"{ext}/Download", serial=serial)
    return MitmCaPushResult(
        paths=tuple(verified),
        external_storage=ext,
        download_ls=download_ls,
    )


def cert_install_user_instructions(result: MitmCaPushResult) -> str:
    """用户级 CA 安装步骤（需在手机上点选）。"""
    paths_txt = "\n".join(f"  - {p}" for p in result.paths)
    return (
        "请在手机上安装用户 CA 证书：设置 → 安全 → 加密与凭据 → 安装证书 → CA 证书，"
        "在文件选择器里进入「内部存储」或「本机」，打开下列任一目录：\n"
        "  • Download（部分系统显示为「下载」）\n"
        "  • Documents（部分系统显示为「文档」）\n"
        "然后选择 mitmproxy-ca-cert.cer。\n"
        "设备上已确认存在的路径：\n"
        f"{paths_txt}\n"
        "若文件管理器仍看不到：在电脑上执行（核对文件是否在机上）\n"
        f"  adb shell ls -la \"{result.external_storage}/Download\"\n"
        "若已安装过 mitmproxy 用户证书可跳过。\n"
        "（以下为设备 Download 目录列表，便于核对文件名）\n"
        f"{result.download_ls[:1200]}"
    )
