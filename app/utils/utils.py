import os
import time
import logging

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
