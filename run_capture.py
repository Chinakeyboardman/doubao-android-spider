#!/usr/bin/env python3
"""
豆包逆向抓包入口（精简版）。

默认 `capture-start`：写 Frida 用 config.local.js、USB 通道（reverse + Gadget forward + 代理 + 推 CA）、
尽量后台启动 mitmweb。附加脚本用短命令 **`python run_capture.py frida`**。
**`logcat`**：过滤网络/TLS 相关 adb 日志辅助排障。其它见 `python run_capture.py -h`。
"""

import argparse
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.utils.step_journal import append_doc_step
from capture.main import CaptureRunner
from capture.modules.cert_installer import CertInstallerError
from app.config.config import DOUBAO_CONFIG
from capture.config.config import CAPTURE_CONFIG
from capture.utils.adb_helper import AdbError, adb_available, install_apk_via_download


def _mitm_listen_port(args: Any) -> int:
    return int(
        args.listen_port if args.listen_port is not None else CAPTURE_CONFIG.get("mitm_listen_port", 8080)
    )


def _frida_executable(repo_root: Path) -> str:
    v = repo_root / ".venv" / "bin" / "frida"
    if v.is_file():
        return str(v)
    w = shutil.which("frida")
    return w if w else "frida"


def _tcp_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def _run_httptoolkit_config_step(args: Any) -> int:
    from capture.modules.httptoolkit_frida import HttptoolkitFridaError, write_httptoolkit_config_local

    try:
        out = write_httptoolkit_config_local(mitm_listen_port=args.listen_port, block_http3=None)
    except (HttptoolkitFridaError, CertInstallerError) as e:
        append_doc_step("Frida config.local.js", "失败", str(e))
        print(e, file=sys.stderr)
        return 1
    append_doc_step("Frida config.local.js", "成功", str(out))
    print(out)
    print("已写入 config.local.js（勿提交 git）。CA 或 mitm 端口变了请重跑本步骤。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="豆包逆向：APK 改包 / USB 抓包 + Frida（默认一键 capture-start）")
    parser.add_argument(
        "action",
        nargs="?",
        default="capture-start",
        choices=[
            "capture-start",
            "proxy-teardown",
            "patch",
            "gadget-patch",
            "push-install",
            "frida",
            "frida-cmd",
            "logcat",
        ],
        help="capture-start=一键抓包准备（默认）；proxy-teardown=关代理并卸 reverse/forward；"
        "patch=拉取/apk-mitm 豆包；gadget-patch=注入 Frida Gadget；push-install=大 APK 推 Download 再安装；"
        "frida=启动 Frida+httptoolkit -l；frida-cmd=打印等价长命令；"
        "logcat=流式过滤网络/TLS 相关日志（Ctrl+C 停）",
    )
    parser.add_argument("-s", "--serial", dest="device_serial", default=None, help="adb 设备序列号")
    parser.add_argument(
        "--listen-port",
        type=int,
        default=None,
        metavar="PORT",
        help="mitm 监听端口（默认 8080，与 capture/config 一致）",
    )
    parser.add_argument(
        "--only-config",
        action="store_true",
        help="capture-start：只生成 config.local.js（不碰手机、不启 mitm）",
    )
    parser.add_argument(
        "--skip-httptoolkit-config",
        action="store_true",
        help="capture-start：跳过写 config.local.js（CA/端口未变时）",
    )
    parser.add_argument(
        "--no-mitmweb",
        action="store_true",
        help="capture-start：不后台启动 mitmweb",
    )
    parser.add_argument(
        "--apk",
        dest="apk_paths",
        nargs="+",
        default=None,
        metavar="PATH",
        help="patch：使用本机已有 APK，跳过 adb pull",
    )
    parser.add_argument("--skip-install", action="store_true", help="patch：生成 patched 后不 adb install")
    parser.add_argument("--skip-uninstall", action="store_true", help="patch / push-install：安装前不卸载豆包")
    parser.add_argument("--from-apk", dest="gadget_source_apk", default=None, metavar="PATH", help="gadget-patch：*-patched.apk")
    parser.add_argument("--gadget-install", action="store_true", help="gadget-patch：完成后 adb install")
    parser.add_argument(
        "--gadget-listen-port",
        type=int,
        default=None,
        metavar="PORT",
        help="Gadget 端口（默认 27042）",
    )
    parser.add_argument("--install-apk", dest="push_install_apk", default=None, metavar="PATH", help="push-install：本机 APK")
    parser.add_argument(
        "--logcat-output",
        default=None,
        metavar="PATH",
        help="logcat：同时追加写入该文件（默认仅 stdout）",
    )
    parser.add_argument("--logcat-clear", action="store_true", help="logcat：开始前 adb logcat -c 清缓冲")
    parser.add_argument("--logcat-all", action="store_true", help="logcat：不过滤关键字（日志洪流）")
    parser.add_argument(
        "--logcat-dump",
        action="store_true",
        help="logcat：只执行一次 logcat -d 后退出（非流式）",
    )
    parser.add_argument(
        "--logcat-no-pid",
        action="store_true",
        help="logcat：不按豆包进程 pid 过滤（默认会尝试 com.larus.nova）",
    )
    args = parser.parse_args()

    if args.action == "gadget-patch" and not args.gadget_source_apk:
        parser.error("gadget-patch 需要 --from-apk")
    if args.action == "push-install" and not args.push_install_apk:
        parser.error("push-install 需要 --install-apk")

    runner = CaptureRunner()

    if args.action == "patch":
        append_doc_step("run_capture 命令行入口", "成功", f"argv={sys.argv[1:]!r}")
        source = [Path(p) for p in args.apk_paths] if args.apk_paths else None
        result = runner.patch_doubao_apk(
            device_serial=args.device_serial,
            skip_uninstall=args.skip_uninstall,
            skip_install=args.skip_install,
            pull_only=False,
            source_apks=source,
        )
        if result.message:
            print(result.message)
        if result.patched_paths:
            for p in result.patched_paths:
                print(f"patched: {p}")
        return 0 if result.ok else 1

    if args.action == "proxy-teardown":
        r = runner.teardown_usb_capture_channel(
            device_serial=args.device_serial,
            listen_port=args.listen_port,
            remove_gadget_forward=True,
            gadget_listen_port=args.gadget_listen_port,
        )
        if r.message:
            print(r.message)
        return 0 if r.ok else 1

    if args.action == "gadget-patch":
        r = runner.inject_frida_gadget(
            source_apk=Path(args.gadget_source_apk),
            output_apk=None,
            architecture=None,
            gadget_config=None,
            gadget_listen_port=args.gadget_listen_port,
            device_serial=args.device_serial,
            install=args.gadget_install,
        )
        if r.message:
            print(r.message)
        if r.output_apk.is_file():
            print(f"\ngadget apk: {r.output_apk}")
        return 0 if r.ok else 1

    if args.action == "logcat":
        from capture.utils.capture_logcat import (
            DEFAULT_NET_KEYWORDS,
            clear_logcat,
            dump_logcat_once,
            resolve_target_pid,
            stream_filtered_logcat,
        )

        if not adb_available():
            print("未找到 adb", file=sys.stderr)
            return 127
        serial = args.device_serial
        pkg = DOUBAO_CONFIG["package_name"]
        pid = None if args.logcat_no_pid else resolve_target_pid(pkg, serial=serial)
        out_path = Path(args.logcat_output).expanduser() if args.logcat_output else None
        if args.logcat_clear:
            clear_logcat(serial=serial)
        if pid is not None:
            print(f"logcat: 过滤 pid={pid} ({pkg})", file=sys.stderr)
        else:
            print(
                f"logcat: 未取到 {pkg} 的 pid（请先前台打开豆包），按关键字过滤全机日志",
                file=sys.stderr,
            )
        kws = None if args.logcat_all else DEFAULT_NET_KEYWORDS
        append_doc_step("logcat", "开始", f"dump={args.logcat_dump}; pid={pid}; file={out_path}")
        if args.logcat_dump:
            repo = Path(__file__).resolve().parent
            default_dump = repo / "logs" / "capture_logcat_dump.txt"
            path = out_path or default_dump
            return dump_logcat_once(serial=serial, keywords=kws, pid=pid, raw=args.logcat_all, out_file=path)
        return stream_filtered_logcat(
            serial=serial,
            keywords=kws,
            pid=pid,
            raw=args.logcat_all,
            out_stream=sys.stdout,
            out_file=out_path,
        )

    if args.action == "frida":
        repo_root = Path(__file__).resolve().parent
        from capture.modules.httptoolkit_frida import HttptoolkitFridaError, frida_httptoolkit_argv

        attach = str(CAPTURE_CONFIG.get("frida_gadget_attach_name") or "Gadget")
        gport = int(
            args.gadget_listen_port
            if args.gadget_listen_port is not None
            else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042)
        )
        mode = str(CAPTURE_CONFIG.get("httptoolkit_frida_script_mode") or "light_plus")
        try:
            argv = frida_httptoolkit_argv(
                gadget_host="127.0.0.1",
                gadget_port=gport,
                attach_name=attach,
                frida_executable=_frida_executable(repo_root),
                from_cwd=repo_root,
                mode=mode,
            )
        except HttptoolkitFridaError as e:
            print(e, file=sys.stderr)
            print("请先: python run_capture.py --only-config", file=sys.stderr)
            return 1
        append_doc_step("frida attach", "启动", f"mode={mode}; gadget_port={gport}")
        try:
            proc = subprocess.run(argv, cwd=str(repo_root))
        except FileNotFoundError:
            print("未找到 frida 可执行文件，请: pip install frida-tools 或使用 .venv", file=sys.stderr)
            return 127
        return int(proc.returncode) if proc.returncode is not None else 1

    if args.action == "frida-cmd":
        from capture.modules.httptoolkit_frida import HttptoolkitFridaError, frida_httptoolkit_command_line

        mode = str(CAPTURE_CONFIG.get("httptoolkit_frida_script_mode") or "light_plus")
        attach = str(CAPTURE_CONFIG.get("frida_gadget_attach_name") or "Gadget")
        gport = int(
            args.gadget_listen_port
            if args.gadget_listen_port is not None
            else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042)
        )
        try:
            line = frida_httptoolkit_command_line(
                gadget_host="127.0.0.1",
                gadget_port=gport,
                attach_name=attach,
                mode=mode,
            )
        except HttptoolkitFridaError as e:
            print(e, file=sys.stderr)
            print("请先: python run_capture.py --only-config", file=sys.stderr)
            return 1
        append_doc_step("frida-cmd", "成功", f"mode={mode}; gadget_port={gport}")
        fe = _frida_executable(Path(__file__).resolve().parent)
        print(line.replace("frida ", shlex.quote(fe) + " ", 1) if line.startswith("frida ") else line)
        return 0

    if args.action == "push-install":
        if not adb_available():
            print("未找到 adb", file=sys.stderr)
            return 1
        pkg = DOUBAO_CONFIG["package_name"]
        uninstall_name = None if args.skip_uninstall else pkg
        apk_path = Path(args.push_install_apk).expanduser().resolve()
        try:
            remote, proc = install_apk_via_download(
                apk_path,
                serial=args.device_serial,
                uninstall_package_name=uninstall_name,
            )
        except AdbError as e:
            print(e, file=sys.stderr)
            return 1
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            print(err or "pm install 失败", file=sys.stderr)
            return 1
        print(f"已推送并安装: {remote}")
        return 0

    # capture-start（默认）
    if args.only_config:
        return _run_httptoolkit_config_step(args)

    repo_root = Path(__file__).resolve().parent
    mport = _mitm_listen_port(args)
    uiport = int(CAPTURE_CONFIG.get("mitmweb_ui_port", 8081))

    if not args.skip_httptoolkit_config:
        code = _run_httptoolkit_config_step(args)
        if code != 0:
            return code
    else:
        print("(已跳过写 config.local.js)")

    r = runner.setup_usb_capture_channel(
        device_serial=args.device_serial,
        listen_port=args.listen_port,
        push_cert=True,
        set_proxy=True,
        reverse=True,
        gadget_forward=True,
        gadget_listen_port=args.gadget_listen_port,
        show_mitmweb_start_hint=False,
    )
    if r.message:
        print(r.message)
    if not r.ok:
        append_doc_step("capture-start", "失败", "USB 通道未成功")
        return 1

    if not args.no_mitmweb:
        if _tcp_port_open("127.0.0.1", mport):
            print(f"本机 {mport} 已在监听，跳过启动 mitmweb。UI 一般为 http://127.0.0.1:{uiport}")
        else:
            mitmweb_bin = shutil.which("mitmweb")
            if not mitmweb_bin:
                print(
                    "未找到 mitmweb（brew install mitmproxy）。请手动:\n"
                    f"  mitmweb --listen-port {mport} --web-port {uiport} --set block_global=false",
                    file=sys.stderr,
                )
            else:
                log_path = repo_root / "logs" / "mitmweb.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as logf:
                    logf.write("\n--- capture-start ---\n")
                    proc = subprocess.Popen(
                        [
                            mitmweb_bin,
                            "--listen-port",
                            str(mport),
                            "--web-port",
                            str(uiport),
                            "--set",
                            "block_global=false",
                        ],
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        cwd=str(repo_root),
                        start_new_session=True,
                    )
                print(f"已后台 mitmweb pid={proc.pid}，日志: {log_path}\n浏览器: http://127.0.0.1:{uiport}")

    append_doc_step("capture-start", "成功", f"mitm={mport}; web_ui={uiport}")
    print("\n先前台打开 Gadget 版豆包，在仓库根执行（勿手抄长串 -l）：")
    print("  python run_capture.py frida")
    print("调试需完整 shell 一行时再: python run_capture.py frida-cmd")
    print("\n结束: python run_capture.py proxy-teardown（并关 mitmweb）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
