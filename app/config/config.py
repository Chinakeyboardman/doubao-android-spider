# 豆包APP配置
DOUBAO_CONFIG = {
    "package_name": "com.larus.nova",
    "chat_activity": "com.larus.bmhome.chat.ChatActivity",
    "timeout": 60,
    "wait_time": 3,
    # 聊天输入条特征：提示文案（用于判断是否在聊天页，可多种子串）
    "chat_hint_substrings": ("发信息", "按住说话"),
}

# 设备配置（手势/几何参数已迁移到 gesture_profile.py + profiles/*.json）
DEVICE_CONFIG = {
    "default_device_id": None,
}

# 路径配置
PATH_CONFIG = {
    "screenshots": "screenshots",
    "logs": "logs",
    # 相对仓库根：capture 与爬虫各步骤共用同一记录文件
    "step_journal": "doc/capture_step_journal.md",
}
