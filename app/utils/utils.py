import os
import time
import logging
from datetime import datetime
from typing import Callable

# 配置日志
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
    格式化消息
    """
    return f"[{get_current_time()}] {message}"

def log_info(message):
    """
    记录信息日志
    """
    logger.info(message)

def log_error(message):
    """
    记录错误日志
    """
    logger.error(message)

def log_warning(message):
    """
    记录警告日志
    """
    logger.warning(message)
