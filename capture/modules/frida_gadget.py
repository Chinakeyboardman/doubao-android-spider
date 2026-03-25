"""Frida Gadget：对已是 apk-mitm 产物的 APK 再注入 Gadget（无 Root），配合本机 frida + mitm。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.utils.step_journal import append_doc_step
from app.utils.utils import ensure_directory, log_error, log_info

from capture.config.config import CAPTURE_CONFIG
from capture.modules.httptoolkit_frida import (
    HttptoolkitFridaError,
    frida_httptoolkit_command_line,
    write_httptoolkit_config_local,
)
from capture.utils.adb_helper import AdbError, adb_available, install_apk, uninstall_package


class FridaGadgetError(RuntimeError):
    """Gadget 注入或依赖缺失。"""


@dataclass
class FridaGadgetPatchResult:
    ok: bool
    input_apk: Path
    output_apk: Path
    message: str = ""


def scripts_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts"


def write_gadget_config(path: Path, *, listen_port: int | None = None) -> Path:
    port = int(listen_port if listen_port is not None else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042))
    cfg = {
        "interaction": {
            "type": "listen",
            "address": "0.0.0.0",
            "port": port,
            "on_load": "resume",
        }
    }
    path = path.resolve()
    ensure_directory(str(path.parent))
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def _which_objection() -> str | None:
    return shutil.which("objection") or shutil.which("objection.exe")


_DEFAULT_SDK_ROOTS = (
    os.environ.get("ANDROID_HOME"),
    os.environ.get("ANDROID_SDK_ROOT"),
    "/opt/homebrew/share/android-commandlinetools",
)


def _latest_build_tools_dir() -> Path | None:
    """在常见 SDK 根目录下查找含 `aapt` 的 build-tools 子目录，取名称排序靠后者（如 35.0.0 > 34.0.0）。"""
    for root in _DEFAULT_SDK_ROOTS:
        if not root:
            continue
        bt = Path(root) / "build-tools"
        if not bt.is_dir():
            continue
        versions: list[Path] = []
        for d in bt.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            if (d / "aapt").is_file():
                versions.append(d)
        if not versions:
            continue
        versions.sort(key=lambda p: p.name, reverse=True)
        return versions[0]
    return None


def _android_build_tools_path() -> Path | None:
    """供 objection 使用的 build-tools 目录（与 `_latest_build_tools_dir` 相同）。"""
    return _latest_build_tools_dir()


def _check_patchapk_deps() -> None:
    if not shutil.which("apktool"):
        raise FridaGadgetError(
            "未找到 apktool。macOS 可: brew install apktool；并确保 JAVA_HOME 指向 JDK（与 apk-mitm 相同）。"
        )
    if not _which_objection():
        raise FridaGadgetError(
            "未找到 objection。请: pip install objection（或 pip install frida-tools objection），确保 objection 在 PATH。"
        )


def _objection_default_output_apk(source_apk: Path) -> Path:
    """objection 1.12+ 将产物写在源 APK 同目录：`name.objection.apk`（无 -o 选项）。"""
    src = source_apk.expanduser().resolve()
    if src.suffix.lower() == ".apk":
        return src.with_suffix(".objection.apk")
    return Path(f"{src}.objection.apk")


def patch_apk_with_objection(
    *,
    source_apk: Path,
    final_output_apk: Path,
    architecture: str,
    gadget_config: Path,
    timeout_sec: int = 3600,
) -> None:
    _check_patchapk_deps()
    src = source_apk.expanduser().resolve()
    final_out = final_output_apk.expanduser().resolve()
    if not src.is_file():
        raise FridaGadgetError(f"源 APK 不存在: {src}")
    ensure_directory(str(final_out.parent))
    if not gadget_config.is_file():
        raise FridaGadgetError(f"Gadget 配置不存在: {gadget_config}")

    objection = _which_objection()
    assert objection
    # objection>=1.12 已移除 -o；产物默认为源路径同目录下 *.objection.apk
    cmd = [
        objection,
        "patchapk",
        "-s",
        str(src),
        "-a",
        architecture,
        "-c",
        str(gadget_config.resolve()),
    ]
    log_info("运行: " + " ".join(cmd))
    env = os.environ.copy()
    tools = _latest_build_tools_dir()
    if tools is not None:
        env["PATH"] = f"{tools}{os.pathsep}{env.get('PATH', '')}"
        log_info(f"PATH 前置 Android build-tools: {tools}")

    proc = subprocess.run(
        cmd,
        cwd=str(src.parent),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise FridaGadgetError(f"objection patchapk 失败 (code={proc.returncode}): {err}")

    objection_out = _objection_default_output_apk(src)
    if not objection_out.is_file():
        raise FridaGadgetError(
            f"未找到 objection 默认输出: {objection_out}（若源路径含多个 .apk 请改用无歧义文件名）"
        )

    if objection_out.resolve() != final_out.resolve():
        shutil.copy2(objection_out, final_out)
        try:
            objection_out.unlink()
        except OSError:
            pass


def frida_mitm_instructions(
    *,
    package_name: str,
    gadget_port: int,
    mitm_port: int,
    device_serial: str | None = None,
    config_note: str = "",
) -> str:
    serial_flag = f"-s {device_serial} " if device_serial else ""
    attach = str(CAPTURE_CONFIG.get("frida_gadget_attach_name") or "Gadget")
    script_mode = str(CAPTURE_CONFIG.get("httptoolkit_frida_script_mode") or "light_plus")
    try:
        frida_httptoolkit_command_line(
            gadget_host="127.0.0.1",
            gadget_port=gadget_port,
            attach_name=attach,
            mode=script_mode,
        )
        frida_cmd = (
            "   python run_capture.py frida\n"
            "   （展开等价长串 -l：python run_capture.py frida-cmd）"
        )
    except HttptoolkitFridaError as e:
        frida_cmd = (
            f"   # 当前无法校验脚本列表: {e}\n"
            f"   # 请执行: python run_capture.py --only-config\n"
            f"   # 再: python run_capture.py frida  或  python run_capture.py frida-cmd\n"
            f"   # 模式见 capture/config/config.py → httptoolkit_frida_script_mode（见 doc/capture_frida_gadget.md）"
        )
    return f"""后续步骤（在 Mac 上执行，按顺序）:

0) Frida 使用 HTTP Toolkit 脚本集（`capture/scripts/httptoolkit_intercept/`，AGPL，见该目录 README / LICENSE）:
   {config_note or "（若未自动生成）请在本仓库根目录执行: python run_capture.py --only-config"}
   CA 或 mitm 端口变更后请重新执行上述命令。

1) 安装注入后的 APK（若未用 --install）:
   adb {serial_flag}uninstall {package_name}
   adb {serial_flag}install -r <生成的 *-gadget.apk 或 run_capture 打印的路径>

2) USB 转发 **两个** 端口（方向不同）:
   adb {serial_flag}reverse tcp:{mitm_port} tcp:{mitm_port}
   adb {serial_flag}forward tcp:{gadget_port} tcp:{gadget_port}
   （mitm：手机访问本机，用 reverse；Gadget：本机 frida 访问手机监听端口，用 forward。）

3) 启动 mitm（示例）:
   mitmweb --listen-port {mitm_port} --web-port {CAPTURE_CONFIG.get('mitmweb_ui_port', 8081)} --set block_global=false

4) 手机全局代理仍指向 127.0.0.1:{mitm_port}（一键：`python run_capture.py` 即 capture-start）

5) **先启动豆包**（须前台运行），在**仓库根目录**执行（`-l` 顺序由 `run_capture.py frida` 固定；勿用 `-f` spawn）:
{frida_cmd}

   当前脚本链模式：**{script_mode}**（见 `doc/capture_frida_gadget.md`）。持久改 `capture/config/config.py` → `httptoolkit_frida_script_mode`。

   说明：`frida-ps -H 127.0.0.1:{gadget_port}` 在 objection Gadget 下进程名多为 **Gadget**，故 `-n` 用 **{attach}** 而非包名；若你列表里是别的名字再改 `capture/config/config.py` 的 `frida_gadget_attach_name`。

   仅跑脚本不进 REPL：对 `frida-cmd` 打印的一行末尾加 `-q -t inf`；或用 `frida` 子命令时自行包一层 shell 传参（一般默认进 REPL 即可）。

6) 在 mitmweb 中确认 TLS 与业务请求正常。

上游仓库（更新脚本时对照）: https://github.com/httptoolkit/frida-interception-and-unpinning/
"""


def inject_frida_gadget_into_apk(
    *,
    source_apk: Path,
    output_apk: Path | None = None,
    architecture: str | None = None,
    gadget_config_path: Path | None = None,
    listen_port: int | None = None,
    device_serial: str | None = None,
    install: bool = False,
) -> FridaGadgetPatchResult:
    """
    对 **apk-mitm 已生成的 *-patched.apk** 再执行 objection patchapk。
    """
    src = source_apk.expanduser().resolve()
    arch = architecture or CAPTURE_CONFIG.get("frida_gadget_arch", "arm64-v8a")
    port = int(listen_port if listen_port is not None else CAPTURE_CONFIG.get("frida_gadget_listen_port", 27042))
    pkg = str(CAPTURE_CONFIG.get("package_name", "com.larus.nova"))

    if output_apk is None:
        out = src.with_name(f"{src.stem}-gadget.apk")
    else:
        out = output_apk.expanduser().resolve()

    ws = src.parent
    cfg_path = gadget_config_path or (ws / "frida-gadget.config.json")
    mitm_port = int(CAPTURE_CONFIG.get("mitm_listen_port", 8080))

    try:
        write_gadget_config(cfg_path, listen_port=port)
        patch_apk_with_objection(
            source_apk=src,
            final_output_apk=out,
            architecture=str(arch),
            gadget_config=cfg_path,
        )
    except FridaGadgetError as e:
        log_error(str(e))
        append_doc_step("Frida Gadget 注入 APK", "失败", str(e))
        return FridaGadgetPatchResult(False, src, out, str(e))

    append_doc_step(
        "Frida Gadget 注入 APK",
        "成功",
        f"arch={arch}; gadget_port={port}; in={src.name}; out={out.name}",
    )

    try:
        write_httptoolkit_config_local(mitm_listen_port=mitm_port)
        cfg_note = "已写入 capture/scripts/httptoolkit_intercept/config.local.js（来自 ~/.mitmproxy CA + 配置端口）。"
    except Exception as e:
        cfg_note = f"未能自动写入 config.local.js: {e}。请执行: python run_capture.py --only-config"

    msg = frida_mitm_instructions(
        package_name=pkg,
        gadget_port=port,
        mitm_port=mitm_port,
        device_serial=device_serial,
        config_note=cfg_note,
    )

    def _install_fail_note(exc_or_err: str) -> str:
        return (
            f"{msg}\n\n※ 注入已成功，APK 已生成:\n{out}\n"
            f"安装未完成，请手机亮屏、USB 调试正常后执行:\n"
            f"  adb uninstall {pkg}\n"
            f"  adb install -r {out}\n"
            f"原因: {exc_or_err}"
        )

    if install:
        if not adb_available():
            err = "请求 --install 但未找到 adb"
            append_doc_step("Frida Gadget APK adb install", "失败", err)
            return FridaGadgetPatchResult(True, src, out, _install_fail_note(err))
        try:
            # 与 apk-mitm / objection 签名不一致时，-r 覆盖会失败，先卸载旧包
            u = uninstall_package(pkg, serial=device_serial)
            log_info(f"adb uninstall {pkg} exit={u.returncode}")
            proc = install_apk(out, serial=device_serial)
            if proc.returncode != 0:
                e = (proc.stderr or proc.stdout or "").strip()
                append_doc_step("Frida Gadget APK adb install", "失败", e)
                return FridaGadgetPatchResult(True, src, out, _install_fail_note(e or "adb install 非零退出"))
            append_doc_step("Frida Gadget APK adb install", "成功", out.name)
        except AdbError as e:
            append_doc_step("Frida Gadget APK adb install", "失败", str(e))
            return FridaGadgetPatchResult(True, src, out, _install_fail_note(str(e)))

    return FridaGadgetPatchResult(True, src, out, msg)
