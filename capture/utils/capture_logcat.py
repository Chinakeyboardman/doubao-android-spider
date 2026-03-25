"""抓包排障：adb logcat 流式读取 + 网络/TLS 相关关键字过滤。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TextIO

from capture.utils.adb_helper import _adb_prefix, adb_available, run_adb

# 行内子串匹配（大小写不敏感）；可按需扩
DEFAULT_NET_KEYWORDS: tuple[str, ...] = (
    "cronet",
    "sscronet",
    "chromium",
    "ttnet",
    "volc",
    "larus",
    "nova",
    "conscrypt",
    "trustmanager",
    "certificat",
    "ssl",
    "tls",
    "handshake",
    "proxy",
    "http_proxy",
    "networksecurity",
    "cleartext",
    "connectivity",
    "socket",
    "failed to connect",
    "unknownhost",
    "unreachable",
    "frida",
    "gadget",
)


def resolve_target_pid(package_name: str, *, serial: str | None) -> int | None:
    """`pidof` 第一个 pid；失败返回 None。"""
    proc = run_adb(["shell", "pidof", package_name], serial=serial, check=False, timeout=15)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    parts = out.split()
    try:
        return int(parts[0])
    except ValueError:
        return None


def clear_logcat(*, serial: str | None) -> None:
    run_adb(["logcat", "-c"], serial=serial, check=False, timeout=30)


def _line_matches(line: str, keywords: tuple[str, ...]) -> bool:
    low = line.lower()
    return any(k.lower() in low for k in keywords)


def stream_filtered_logcat(
    *,
    serial: str | None,
    keywords: tuple[str, ...] | None,
    pid: int | None,
    raw: bool,
    out_stream: TextIO,
    out_file: Path | None,
) -> int:
    """
    前台阻塞读 logcat，按关键字过滤后写入 out_stream；若 out_file 非空则同步追加写入。
    返回 0；Ctrl+C 返回 130。
    """
    if not adb_available():
        print("未找到 adb", file=sys.stderr)
        return 127

    cmd = _adb_prefix(serial) + ["logcat", "-v", "threadtime", "-b", "main", "-b", "system", "-b", "crash"]
    if pid is not None:
        cmd.extend(["--pid", str(pid)])

    fh: TextIO | None = None
    if out_file is not None:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        fh = out_file.open("a", encoding="utf-8", errors="replace")
        fh.write(f"\n--- logcat start cmd={cmd!r} pid={pid!r} raw={raw} ---\n")

    kws = keywords if keywords is not None else DEFAULT_NET_KEYWORDS
    proc: subprocess.Popen[str] | None = None
    exit_rc = 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            if raw or _line_matches(line, kws):
                out_stream.write(line)
                out_stream.flush()
                if fh:
                    fh.write(line)
                    fh.flush()
    except KeyboardInterrupt:
        exit_rc = 130
        print("\n(已停止 logcat)", file=sys.stderr)
    finally:
        if fh:
            fh.close()
        if proc:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
            err = (proc.stderr.read() if proc.stderr else "") or ""
            if exit_rc == 0:
                rc = proc.returncode
                if rc not in (0, None, -15, -9):
                    if err.strip():
                        print(err.strip(), file=sys.stderr)
                    exit_rc = 1
    return exit_rc


def dump_logcat_once(
    *,
    serial: str | None,
    keywords: tuple[str, ...] | None,
    pid: int | None,
    raw: bool,
    out_file: Path,
) -> int:
    """执行 `adb logcat -d` 后过滤写入文件（非流式）。"""
    if not adb_available():
        print("未找到 adb", file=sys.stderr)
        return 127

    cmd = _adb_prefix(serial) + ["logcat", "-d", "-v", "threadtime", "-b", "main", "-b", "system", "-b", "crash"]
    if pid is not None:
        cmd.extend(["--pid", str(pid)])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    if proc.returncode != 0:
        print((proc.stderr or proc.stdout or "logcat -d 失败").strip(), file=sys.stderr)
        return 1

    kws = keywords if keywords is not None else DEFAULT_NET_KEYWORDS
    out_file.parent.mkdir(parents=True, exist_ok=True)
    lines = proc.stdout.splitlines(keepends=True)
    if raw:
        chosen = lines
    else:
        chosen = [ln for ln in lines if _line_matches(ln, kws)]

    out_file.write_text("".join(chosen), encoding="utf-8", errors="replace")
    print(f"已写入 {len(chosen)} 行 -> {out_file}")
    return 0
