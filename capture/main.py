"""逆向分析流程入口类（与 app/main.DoubaoSpider 职责分离）。"""

from pathlib import Path

from capture.modules.apk_patcher import ApkPatcher, ApkPatchResult
from capture.modules.frida_gadget import FridaGadgetPatchResult, inject_frida_gadget_into_apk
from capture.modules.proxy_channel import ProxyChannelResult, setup_usb_capture_channel, teardown_usb_capture_channel


class CaptureRunner:
    """编排 capture 子流程（APK 重打包、后续抓包等）。"""

    def setup_usb_capture_channel(
        self,
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
        """Step 3：adb reverse、全局代理、推送 mitm CA（用户证书需在手机上安装）。"""
        return setup_usb_capture_channel(
            device_serial=device_serial,
            listen_port=listen_port,
            push_cert=push_cert,
            set_proxy=set_proxy,
            reverse=reverse,
            gadget_forward=gadget_forward,
            gadget_listen_port=gadget_listen_port,
            show_mitmweb_start_hint=show_mitmweb_start_hint,
        )

    def teardown_usb_capture_channel(
        self,
        *,
        device_serial: str | None = None,
        listen_port: int | None = None,
        clear_proxy: bool = True,
        remove_reverse: bool = True,
        remove_gadget_forward: bool = True,
        gadget_listen_port: int | None = None,
    ) -> ProxyChannelResult:
        """关闭 Step 3 代理、mitm 的 reverse，默认同时移除 Gadget 的 adb forward。"""
        return teardown_usb_capture_channel(
            device_serial=device_serial,
            listen_port=listen_port,
            clear_proxy=clear_proxy,
            remove_reverse=remove_reverse,
            remove_gadget_forward=remove_gadget_forward,
            gadget_listen_port=gadget_listen_port,
        )

    def patch_doubao_apk(
        self,
        *,
        device_serial: str | None = None,
        skip_uninstall: bool = False,
        skip_install: bool = False,
        pull_only: bool = False,
        workspace_root: Path | None = None,
        source_apks: list[Path] | None = None,
    ) -> ApkPatchResult:
        """提取豆包 APK → apk-mitm → 安装修改版（可由参数跳过部分步骤）。"""
        patcher = ApkPatcher(workspace_root=workspace_root)
        return patcher.run(
            device_serial=device_serial,
            skip_uninstall=skip_uninstall,
            skip_install=skip_install,
            pull_only=pull_only,
            source_apks=source_apks,
        )

    def inject_frida_gadget(
        self,
        *,
        source_apk: Path,
        output_apk: Path | None = None,
        architecture: str | None = None,
        gadget_config: Path | None = None,
        gadget_listen_port: int | None = None,
        device_serial: str | None = None,
        install: bool = False,
    ) -> FridaGadgetPatchResult:
        """在 apk-mitm 产物上注入 Frida Gadget（objection）。"""
        return inject_frida_gadget_into_apk(
            source_apk=source_apk,
            output_apk=output_apk,
            architecture=architecture,
            gadget_config_path=gadget_config,
            listen_port=gadget_listen_port,
            device_serial=device_serial,
            install=install,
        )
