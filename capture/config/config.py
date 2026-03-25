"""逆向流程默认路径与命令（包名与 app 配置对齐）。"""

from pathlib import Path

from app.config.config import DOUBAO_CONFIG

CAPTURE_CONFIG = {
    "package_name": DOUBAO_CONFIG["package_name"],
    "apk_workspace_root": Path("logs/captures/apk"),
    "apk_mitm_command": "apk-mitm",
    # Step 3：USB 抓包（mitmproxy + adb reverse 8080→本机 + 全局代理；Frida Gadget 用 adb forward，见 doc/capture_frida_gadget.md）
    "mitm_listen_port": 8080,
    "mitmweb_ui_port": 8081,
    "mitm_confdir_name": ".mitmproxy",
    "mitm_ca_cert_filename": "mitmproxy-ca-cert.cer",
    # 首选目录（若设备上 $EXTERNAL_STORAGE 可解析，实际推送会用其下的 Download / Documents）
    "device_cert_push_dir": "/sdcard/Download",
    "device_cert_fallback_subdirs": ("Download", "Documents"),
    # Frida Gadget（objection patchapk；无 Root）
    "frida_gadget_listen_port": 27042,
    "frida_gadget_arch": "arm64-v8a",
    # objection 注入后，本机 `frida-ps -H 127.0.0.1:<port>` 进程名多为 Gadget，勿用包名附加
    "frida_gadget_attach_name": "Gadget",
    # httptoolkit Frida：`light_plus` = light + 系统证书注入（仍无 native-connect）；`light` 更省脚本；`full` 见上游 README
    "httptoolkit_frida_script_mode": "light_plus",
    # httptoolkit config.local.js：`False` = 不拦 UDP/443（HTTP/3），豆包/TTNet 不易整 App 网络错误；`True` 为上游默认（强退 QUIC）
    "httptoolkit_block_http3": False,
}
