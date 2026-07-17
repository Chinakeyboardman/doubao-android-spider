import os
import re
import time
import logging
import builtins
from datetime import datetime
from typing import Callable, TextIO

_OP_TS_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
_SN_TAG_RE = re.compile(r"\[SN=[^\]]+\]")
_OP_LOG_INSTALLED = False
_OP_DEVICE_SN = ""
_orig_print = builtins.print


def device_log_tag() -> str:
    """当前进程绑定的 adb 序列号标签，如 `` [SN=10ADBY1Z7C0042Z]``。"""
    if not _OP_DEVICE_SN:
        return ""
    return f" [SN={_OP_DEVICE_SN}]"


def set_op_log_device(serial: str | None) -> None:
    """绑定本 worker 的 adb SN，后续 print / logging 均带 ``[SN=...]``。"""
    global _OP_DEVICE_SN
    _OP_DEVICE_SN = (serial or "").strip()


def reset_op_log_device() -> None:
    """测试用：清除 SN 绑定。"""
    global _OP_DEVICE_SN
    _OP_DEVICE_SN = ""

# 配置日志（入口脚本可调用 install_op_logging 统一格式）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/doubao_spider.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def ensure_directory(path):
    """
    确保目录存在
    """
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        logger.info(f"创建目录: {path}")
    return path


def poll_until(
    predicate: Callable[[], bool],
    timeout: float,
    interval: float = 0.15,
    settle: float = 0.0,
) -> bool:
    """在 timeout 秒内轮询 predicate()，为真则（可选 settle 后）返回 True；超时返回 False。

    用于把"固定等待"替换为"轮询到就绪即继续"：调用方应把 timeout 设为
    >= 原固定 sleep 时长，并在返回 False 时保留原有兜底行为，从而保证
    最坏情况不劣于改动前（稳定性/完整性优先）。predicate 内部异常按未就绪处理。
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            if predicate():
                if settle > 0:
                    time.sleep(settle)
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(interval)


def build_session_dir(
    base: str,
    script: str,
    when: datetime | None = None,
    project: str = "",
) -> str:
    """
    产出分层会话目录：<base>/<script>/[<project>/]<YYYY-MM-DD>/<HHMMSS>/。

    当 base 已是项目根目录（basename == project）时不再重复追加 project。

    示例：
      build_session_dir("logs", "qa_capture")
        -> logs/qa_capture/2026-07-10/111530/
      build_session_dir("logs", "qa_capture", project="雅诗兰黛")
        -> logs/qa_capture/雅诗兰黛/2026-07-10/111530/
      build_session_dir("var/雅诗兰黛", "qa_capture", project="雅诗兰黛")
        -> var/雅诗兰黛/qa_capture/2026-07-10/111530/
    """
    ts = when or datetime.now()
    day = ts.strftime("%Y-%m-%d")
    clock = ts.strftime("%H%M%S")
    parts = [base, script]
    if project:
        base_name = os.path.basename(os.path.normpath(base))
        if base_name != project:
            parts.append(project)
    parts.extend([day, clock])
    return ensure_directory(os.path.join(*parts))

def get_timestamp():
    """
    获取时间戳
    """
    return int(time.time())

def get_current_time():
    """
    获取当前时间
    """
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())


def format_message(message):
    """
    格式化消息：时间戳 + 可选 ``[SN=...]`` + 正文。
    """
    if not isinstance(message, str):
        message = str(message)
    if _SN_TAG_RE.search(message) and _OP_TS_RE.match(message):
        return message
    tag = device_log_tag()
    if _OP_TS_RE.match(message):
        if not tag or _SN_TAG_RE.search(message):
            return message
        m = _OP_TS_RE.match(message)
        return f"{m.group(0)}{tag}{message[m.end():]}"
    prefix = f"[{get_current_time()}]"
    return f"{prefix}{tag} {message}" if tag else f"{prefix} {message}"


def _format_first_print_arg(first: str) -> str:
    if _SN_TAG_RE.search(first) and _OP_TS_RE.match(first):
        return first
    if _OP_TS_RE.match(first):
        return format_message(first)
    return format_message(first)


def op_print(*args, file: TextIO | None = None, **kwargs) -> None:
    """带时间戳的操作日志输出（与 install_op_logging 的 print 钩子格式一致）。"""
    global _orig_print
    if args:
        first = args[0]
        if isinstance(first, str):
            args = (_format_first_print_arg(first),) + args[1:]
    _orig_print(*args, file=file, **kwargs)


def install_op_logging() -> None:
    """
    为真机操作日志统一加时间戳：劫持 builtins.print，并统一 root logger 格式。

    在 run_qa_spot_check / run_qa_capture 等入口 main() 开头调用一次即可。
    """
    global _OP_LOG_INSTALLED, _orig_print
    if _OP_LOG_INSTALLED:
        return
    _OP_LOG_INSTALLED = True

    _orig_print = builtins.print

    def _ts_print(*args, **kwargs):
        if args:
            first = args[0]
            if isinstance(first, str):
                args = (_format_first_print_arg(first),) + args[1:]
        _orig_print(*args, **kwargs)

    builtins.print = _ts_print

    fmt = logging.Formatter(
        "[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(fmt)
    for name in ("uiautomator2", "urllib3", "PIL"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(fmt)


def _tag_log_message(message: str) -> str:
    tag = device_log_tag().strip()
    if tag and "[SN=" not in message:
        return f"{tag} {message}"
    return message


def log_info(message):
    """
    记录信息日志
    """
    logger.info(_tag_log_message(str(message)))


def log_error(message):
    """
    记录错误日志
    """
    logger.error(_tag_log_message(str(message)))


def log_warning(message):
    """
    记录警告日志
    """
    logger.warning(_tag_log_message(str(message)))
