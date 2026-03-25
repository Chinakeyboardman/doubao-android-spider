"""mitmproxy / mitmweb 启动参数建议（本机另开终端运行）。"""

from __future__ import annotations

from pathlib import Path


def mitmweb_argv(*, listen_port: int, web_port: int) -> list[str]:
    """与方案 Step 3 对齐：监听手机经 reverse 转发的端口，Web UI 查看流量。"""
    return [
        "mitmweb",
        "--listen-port",
        str(listen_port),
        "--web-port",
        str(web_port),
        "--set",
        "block_global=false",
    ]


def mitmweb_command_line(*, listen_port: int, web_port: int) -> str:
    """可复制的 shell 命令字符串。"""
    return " ".join(mitmweb_argv(listen_port=listen_port, web_port=web_port))


def mitmdump_record_argv(*, listen_port: int, outfile: Path) -> list[str]:
    """Step 4：与 mitmweb 一致监听端口，并把会话写入 .mitm 文件。"""
    return [
        "mitmdump",
        "--listen-port",
        str(listen_port),
        "--set",
        "block_global=false",
        "-w",
        str(outfile),
    ]
