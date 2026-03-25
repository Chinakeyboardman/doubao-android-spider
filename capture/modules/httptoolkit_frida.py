"""HTTP Toolkit「frida-interception-and-unpinning」脚本封装（AGPL-3.0，见 scripts 目录 LICENSE）。"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from capture.config.config import CAPTURE_CONFIG
from capture.modules.cert_installer import CertInstallerError, ensure_mitmproxy_ca_generated, mitm_ca_cert_path, mitm_confdir


class HttptoolkitFridaError(RuntimeError):
    """生成 config.local.js 或路径异常。"""


def httptoolkit_intercept_dir() -> Path:
    """`capture/scripts/httptoolkit_intercept/`。"""
    return Path(__file__).resolve().parents[1] / "scripts" / "httptoolkit_intercept"


def config_template_path() -> Path:
    p = httptoolkit_intercept_dir() / "config.template.js"
    if not p.is_file():
        raise HttptoolkitFridaError(f"未找到 httptoolkit 模板: {p}")
    return p


def config_local_path() -> Path:
    return httptoolkit_intercept_dir() / "config.local.js"


def _read_mitm_ca_pem_string() -> str:
    ensure_mitmproxy_ca_generated()
    conf = mitm_confdir()
    pem_file = conf / "mitmproxy-ca-cert.pem"
    if pem_file.is_file():
        return pem_file.read_text(encoding="utf-8").strip()
    cer = mitm_ca_cert_path()
    if not cer.is_file():
        raise CertInstallerError(f"未找到 mitm CA: {cer}")
    raw = cer.read_text(encoding="utf-8", errors="replace").strip()
    if "BEGIN CERTIFICATE" in raw:
        return raw
    tmp_out = conf / "_mitm_export_for_frida.pem"
    proc = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-in", str(cer), "-out", str(tmp_out)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode != 0 or not tmp_out.is_file():
        raise HttptoolkitFridaError(
            "无法将 mitm CA 转为 PEM。请安装 openssl，或确保 ~/.mitmproxy/ 下存在 mitmproxy-ca-cert.pem。"
            + (f"\nopenssl: {(proc.stderr or proc.stdout or '').strip()}" if proc.stderr or proc.stdout else "")
        )
    try:
        return tmp_out.read_text(encoding="utf-8").strip()
    finally:
        try:
            tmp_out.unlink()
        except OSError:
            pass


def write_httptoolkit_config_local(
    *,
    mitm_listen_port: int | None = None,
    mitm_proxy_host: str = "127.0.0.1",
    block_http3: bool | None = None,
) -> Path:
    """
    从本机 `~/.mitmproxy/` CA 生成 `config.local.js`（含 CERT_PEM、PROXY_*、BLOCK_HTTP3）。
    须先于 frida `-l` 链执行一次（CA 或端口变更后重跑）。

    `block_http3`：为 True 时与上游一致（拦 UDP/443，逼 HTTP/2，豆包/TTNet 常整 App「网络错误」）。
    默认取 `CAPTURE_CONFIG['httptoolkit_block_http3']`，一般为 False。
    """
    port = int(mitm_listen_port if mitm_listen_port is not None else CAPTURE_CONFIG.get("mitm_listen_port", 8080))
    bh3 = (
        bool(block_http3)
        if block_http3 is not None
        else bool(CAPTURE_CONFIG.get("httptoolkit_block_http3", False))
    )
    pem = _read_mitm_ca_pem_string()
    if "`" in pem:
        raise HttptoolkitFridaError("CA PEM 中含反引号，无法嵌入 Frida 模板，请换用无反引号证书源。")

    template = config_template_path().read_text(encoding="utf-8")
    patched = re.sub(r"const CERT_PEM = `[^`]*`;", f"const CERT_PEM = `{pem}`;", template, count=1, flags=re.DOTALL)
    if patched == template:
        raise HttptoolkitFridaError("未能替换 config.template.js 中的 CERT_PEM 占位块，请检查上游模板是否变更。")
    patched = re.sub(r"const PROXY_HOST = '[^']*';", f"const PROXY_HOST = '{mitm_proxy_host}';", patched, count=1)
    patched = re.sub(r"const PROXY_PORT = \d+;", f"const PROXY_PORT = {port};", patched, count=1)
    patched_h3, n_h3 = re.subn(
        r"const BLOCK_HTTP3 = (true|false);",
        f"const BLOCK_HTTP3 = {'true' if bh3 else 'false'};",
        patched,
        count=1,
    )
    if n_h3 != 1:
        raise HttptoolkitFridaError("未能替换 config.template.js 中的 BLOCK_HTTP3，请检查上游模板是否变更。")
    patched = patched_h3

    out = config_local_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patched, encoding="utf-8")
    return out.resolve()


def httptoolkit_frida_script_relative_names(*, mode: str | None = None) -> list[str]:
    """
    `-l` 顺序。

    - **light**：不加载 `native-connect-hook` / `android-proxy-override` / `android-system-certificate-injection`，
      仅依赖用户 CA + native-tls-hook + Java unpinning；部分机型仍报网络错误时可换 **light_plus**。
    - **light_plus**（推荐默认）：在 **light** 基础上增加 `android-system-certificate-injection`（Conscrypt 系统信任），
      仍不加载 native-connect / proxy-override，豆包一般比 **full** 稳。
    - **full**：与上游 README Android 全量链一致（易闪退时勿用）。
    """
    m = (mode or CAPTURE_CONFIG.get("httptoolkit_frida_script_mode") or "light_plus").strip().lower()
    if m == "full":
        return [
            "config.local.js",
            "native-connect-hook.js",
            "native-tls-hook.js",
            "android/android-proxy-override.js",
            "android/android-system-certificate-injection.js",
            "android/android-certificate-unpinning.js",
            "android/android-conscrypt-trustmanagerimpl-verifychain.js",
            "android/android-certificate-unpinning-fallback.js",
            "android/android-disable-root-detection.js",
        ]
    if m == "light":
        return [
            "config.local.js",
            "native-tls-hook.js",
            "android/android-certificate-unpinning.js",
            "android/android-conscrypt-trustmanagerimpl-verifychain.js",
            "android/android-certificate-unpinning-fallback.js",
            "android/android-disable-root-detection.js",
        ]
    if m in ("light_plus", "light+"):
        return [
            "config.local.js",
            "native-tls-hook.js",
            "android/android-system-certificate-injection.js",
            "android/android-certificate-unpinning.js",
            "android/android-conscrypt-trustmanagerimpl-verifychain.js",
            "android/android-certificate-unpinning-fallback.js",
            "android/android-disable-root-detection.js",
        ]
    raise HttptoolkitFridaError(
        f"未知 httptoolkit_frida_script_mode: {m!r}（仅支持 light / light_plus / full）"
    )


def httptoolkit_android_script_paths(*, mode: str | None = None) -> list[Path]:
    """按模式解析为绝对路径。"""
    base = httptoolkit_intercept_dir()
    names = httptoolkit_frida_script_relative_names(mode=mode)
    paths = [base / n for n in names]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise HttptoolkitFridaError("缺少 httptoolkit 脚本文件: " + ", ".join(str(p) for p in missing))
    return paths


def frida_httptoolkit_l_args(*, from_cwd: Path | None = None, mode: str | None = None) -> list[str]:
    """返回 `['-l', 'path', ...]` 列表；路径相对 `from_cwd`（默认仓库根）。"""
    repo = from_cwd
    if repo is None:
        repo = httptoolkit_intercept_dir().parents[2]
    out: list[str] = []
    for p in httptoolkit_android_script_paths(mode=mode):
        rel = p.resolve().relative_to(repo.resolve())
        out.extend(["-l", str(rel)])
    return out


def frida_httptoolkit_argv(
    *,
    gadget_host: str,
    gadget_port: int,
    attach_name: str,
    frida_executable: str = "frida",
    from_cwd: Path | None = None,
    mode: str | None = None,
) -> list[str]:
    """构造 Frida 参数列表（须 `cwd` 为仓库根，`-l` 为相对路径）。"""
    repo = from_cwd
    if repo is None:
        repo = httptoolkit_intercept_dir().parents[2]
    return [
        frida_executable,
        "-H",
        f"{gadget_host}:{gadget_port}",
        "-n",
        attach_name,
        *frida_httptoolkit_l_args(from_cwd=repo, mode=mode),
    ]


def frida_httptoolkit_command_line(
    *,
    gadget_host: str,
    gadget_port: int,
    attach_name: str,
    from_cwd: Path | None = None,
    mode: str | None = None,
) -> str:
    """供文档与 gadget-patch 打印的一条 shell 命令（多 `-l`）。`attach_name` 见 `frida-ps -H ...`（Gadget 注入时多为 Gadget）。"""
    repo = from_cwd
    if repo is None:
        repo = httptoolkit_intercept_dir().parents[2]
    argv = frida_httptoolkit_argv(
        gadget_host=gadget_host,
        gadget_port=gadget_port,
        attach_name=attach_name,
        frida_executable="frida",
        from_cwd=repo,
        mode=mode,
    )
    return " ".join(shlex.quote(a) for a in argv)
