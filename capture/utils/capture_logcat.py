"""抓包排障：adb logcat 流式读取 + 网络/TLS 相关关键字过滤。"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
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


def dump_logcat_buffers(
    *,
    serial: str | None,
    buffers: tuple[str, ...] = ("events", "main", "system"),
) -> str:
    """执行 `adb logcat -d` 读取指定 buffer，返回拼接文本。"""
    if not adb_available():
        return ""
    parts: list[str] = []
    for buf in buffers:
        cmd = _adb_prefix(serial) + ["logcat", "-d", "-v", "brief", "-b", buf]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            if proc.stdout:
                parts.append(f"\n--- buffer:{buf} ---\n")
                parts.append(proc.stdout)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "".join(parts)


def dump_logcat_tail(
    *,
    serial: str | None,
    count: int = 80,
    buffers: tuple[str, ...] = ("events", "main", "system"),
) -> str:
    """读取各 buffer 最近 count 行（点击后取 Intent 更干净）。"""
    if not adb_available():
        return ""
    parts: list[str] = []
    for buf in buffers:
        cmd = _adb_prefix(serial) + [
            "logcat", "-d", "-t", str(count), "-v", "brief", "-b", buf,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
            if proc.stdout:
                parts.append(f"\n--- buffer:{buf} ---\n")
                parts.append(proc.stdout)
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "".join(parts)


class LogcatStream:
  """
  常驻 adb logcat 子进程，解析引用 URL 时只读 mark 之后的新增行，避免逐条 clear/spawn。
  """

  def __init__(self, *, serial: str | None = None) -> None:
    self.serial = serial
    self._lines: list[str] = []
    self._lock = threading.Lock()
    self._read_pos = 0
    self._proc: subprocess.Popen[str] | None = None
    self._thread: threading.Thread | None = None
    self._running = False

  def start(self, *, settle_s: float = 0.15) -> None:
    if self._running:
      return
    if not adb_available():
      return
    clear_logcat(serial=self.serial)
    if settle_s > 0:
      time.sleep(settle_s)
    cmd = _adb_prefix(self.serial) + [
      "logcat",
      "-v",
      "brief",
      "-b",
      "events",
      "-b",
      "main",
      "-b",
      "system",
    ]
    self._proc = subprocess.Popen(
      cmd,
      stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,
      text=True,
      bufsize=1,
    )
    self._running = True
    self._thread = threading.Thread(target=self._reader, daemon=True)
    self._thread.start()

  def _reader(self) -> None:
    proc = self._proc
    if not proc or not proc.stdout:
      return
    try:
      for line in proc.stdout:
        if not self._running:
          break
        with self._lock:
          self._lines.append(line)
    except (OSError, ValueError):
      pass

  def stop(self) -> None:
    self._running = False
    proc = self._proc
    self._proc = None
    if proc:
      try:
        if proc.stdout:
          proc.stdout.close()
      except OSError:
        pass
      if proc.poll() is None:
        proc.terminate()
      try:
        proc.wait(timeout=5)
      except subprocess.TimeoutExpired:
        proc.kill()
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=2)

  def mark(self) -> None:
    with self._lock:
      self._read_pos = len(self._lines)

  def text_since_mark(self) -> str:
    with self._lock:
      return "".join(self._lines[self._read_pos:])

  def __enter__(self) -> LogcatStream:
    self.start()
    return self

  def __exit__(self, *args: object) -> None:
    self.stop()
