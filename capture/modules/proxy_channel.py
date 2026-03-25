"""Step 3：USB 抓包通道 — adb reverse + 全局代理 + CA 推送。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from app.utils.step_journal import append_doc_step

from capture.config.config import CAPTURE_CONFIG
from capture.modules.cert_installer import (
    CertInstallerError,
    cert_install_user_instructions,
    ensure_mitmproxy_ca_generated,
    push_mitm_ca_to_download,
)
from capture.modules.proxy_server import mitmweb_command_line
from capture.utils.adb_helper import (
    AdbError,
    adb_available,
    clear_global_http_proxy,
    forward_remove_tcp,
    forward_tcp,
    reverse_remove_tcp,
    reverse_tcp,
    set_global_http_proxy,
)


@dataclass
class ProxyChannelResult:
    ok: bool
    message: str = ""


def setup_usb_capture_channel(
    *,
    device_serial: str | None = None,
    listen_port: int | None = None,
    push_cert: bool = True,
    set_proxy: bool = True,
    reverse: bool = True,
    gadget_forward: bool = False,
    gadget_listen_port: int | None = None,
    show_mitmweb_start_hint: bool = True,
) -> ProxyChannelResult:
    """
    1. 确保本机 mitm CA 存在
    2. 可选：adb push CA 到 Download
    3. adb reverse tcp:listen_port tcp:listen_port
    4. 可选：adb forward tcp:GADGET tcp:GADGET（本机 frida 连 Gadget，与 reverse 方向相反）
    5. settings put global http_proxy 127.0.0.1:listen_port
    """
    port = int(listen_port if listen_port is not None else CAPTURE_CONFIG.get("mitm_listen_port", 8080))
    web_port = int(CAPTURE_CONFIG.get("mitmweb_ui_port", 8081))
    gadget_port = int(
        gadget_listen_port if gadget_listen_port is not None else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042)
    )
    lines: list[str] = []

    if not adb_available():
        return ProxyChannelResult(False, "未找到 adb，请安装 Android platform-tools 并连接设备。")

    try:
        ensure_mitmproxy_ca_generated()
    except CertInstallerError as e:
        append_doc_step("Step3 USB 抓包通道", "失败", str(e))
        return ProxyChannelResult(False, str(e))

    if push_cert:
        try:
            remote = push_mitm_ca_to_download(serial=device_serial)
            lines.append(f"已推送 CA 到设备: {remote}")
            lines.append(cert_install_user_instructions(remote))
        except (CertInstallerError, AdbError) as e:
            append_doc_step("Step3 USB 抓包通道", "失败", f"推送证书: {e}")
            return ProxyChannelResult(False, f"推送证书失败: {e}")

    if reverse:
        try:
            reverse_tcp(port, port, serial=device_serial)
            lines.append(f"已 adb reverse tcp:{port} tcp:{port}")
        except AdbError as e:
            append_doc_step("Step3 USB 抓包通道", "失败", f"reverse: {e}")
            return ProxyChannelResult(False, f"adb reverse 失败: {e}")

    if gadget_forward:
        try:
            forward_tcp(gadget_port, gadget_port, serial=device_serial)
            lines.append(f"已 adb forward tcp:{gadget_port} tcp:{gadget_port}（本机 frida -H 127.0.0.1:{gadget_port}）")
        except AdbError as e:
            append_doc_step("Step3 USB 抓包通道", "失败", f"gadget forward: {e}")
            return ProxyChannelResult(False, f"adb forward (Frida Gadget) 失败: {e}")

    if set_proxy:
        try:
            set_global_http_proxy("127.0.0.1", port, serial=device_serial)
            lines.append(f"已设置全局代理 127.0.0.1:{port}")
        except AdbError as e:
            append_doc_step("Step3 USB 抓包通道", "失败", f"代理: {e}")
            return ProxyChannelResult(False, f"设置全局代理失败: {e}")

    if show_mitmweb_start_hint:
        if shutil.which("mitmweb") is None:
            lines.append("提示: 未找到 mitmweb，请安装 mitmproxy 后在本机运行抓包进程。")
        else:
            lines.append("在本机另开终端运行（保持运行）:")
            lines.append(f"  {mitmweb_command_line(listen_port=port, web_port=web_port)}")
            lines.append(f"浏览器打开 http://127.0.0.1:{web_port} 查看流量。")

    lines.append("验证: 手机浏览器访问 HTTPS 站点，mitmweb 中应出现解密后的请求。")
    lines.append("结束抓包请执行: python run_capture.py proxy-teardown")

    msg = "\n".join(lines)
    append_doc_step(
        "Step3 USB 抓包通道",
        "成功",
        f"listen_port={port}; push_cert={push_cert}; reverse={reverse}; set_proxy={set_proxy}; "
        f"gadget_forward={gadget_forward}; gadget_listen_port={gadget_port if gadget_forward else 'n/a'}",
    )
    return ProxyChannelResult(True, msg)


def teardown_usb_capture_channel(
    *,
    device_serial: str | None = None,
    listen_port: int | None = None,
    clear_proxy: bool = True,
    remove_reverse: bool = True,
    remove_gadget_forward: bool = True,
    gadget_listen_port: int | None = None,
) -> ProxyChannelResult:
    port = int(listen_port if listen_port is not None else CAPTURE_CONFIG.get("mitm_listen_port", 8080))
    gadget_port = int(
        gadget_listen_port if gadget_listen_port is not None else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042)
    )
    if not adb_available():
        return ProxyChannelResult(False, "未找到 adb。")
    lines: list[str] = []
    try:
        if clear_proxy:
            clear_global_http_proxy(serial=device_serial)
            lines.append("已清除全局 http_proxy（:0）。")
        if remove_reverse:
            reverse_remove_tcp(port, serial=device_serial)
            lines.append(f"已尝试移除 reverse tcp:{port}（若不存在可忽略 adb 提示）。")
        if remove_gadget_forward:
            forward_remove_tcp(gadget_port, serial=device_serial)
            lines.append(f"已尝试移除 forward tcp:{gadget_port}（若不存在可忽略 adb 提示）。")
    except AdbError as e:
        append_doc_step("Step3 USB 抓包通道 teardown", "失败", str(e))
        return ProxyChannelResult(False, str(e))
    msg = "\n".join(lines)
    append_doc_step(
        "Step3 USB 抓包通道 teardown",
        "成功",
        f"listen_port={port}; remove_gadget_forward={remove_gadget_forward}",
    )
    return ProxyChannelResult(True, msg)
