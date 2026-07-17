#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
豆包商品爬虫入口。

基于 flow_recorder 录制分析的真实导航结构：
  ChatActivity -> AppletActivity (商品列表) -> WebActivity (商品详情 H5)

用法:
  python run_flow_crawl.py                              # 完整流程（默认提示词）
  python run_flow_crawl.py --skip-send                  # 跳过发送（当前屏已有目标回复时）
  python run_flow_crawl.py --prompt "你的问题"          # 自定义提示词
  python run_flow_crawl.py --max-products-per-card 3    # 每张嵌入式卡片最多进 3 个详情
  python run_flow_crawl.py --max-cards 3                # 最多处理几张嵌入式卡片
  python run_flow_crawl.py -s <adb_serial>              # 指定设备
  python run_flow_crawl.py --device-profile huawei_xxx  # 手动指定设备 profile（一般自动识别）
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.flow_crawler import FlowCrawler
from app.config.profile_loader import load_profile
from app.utils.device import DeviceManager
from app.utils.utils import log_info, log_error, install_op_logging, set_op_log_device


def main() -> int:
    install_op_logging()

    parser = argparse.ArgumentParser(description="豆包商品爬虫")
    parser.add_argument("--prompt", type=str, default="请推荐2026年最好用的旗舰手机",
                        help="发送的提示词")
    parser.add_argument("--skip-send", action="store_true",
                        help="跳过发送消息（当前聊天已有回复时）")
    parser.add_argument("--max-products-per-card", type=int, default=5,
                        help="每张嵌入式卡片最多进几个商品详情（默认 5）")
    parser.add_argument("--max-cards", type=int, default=10,
                        help="最多处理几张嵌入式卡片（默认 10）")
    parser.add_argument("-s", "--serial", default=None,
                        help="adb 设备序列号")
    parser.add_argument("--device-profile", default=None,
                        help="手动指定设备 profile（一般自动识别，无需传）")
    parser.add_argument("--sms-token", default=None,
                        help="SMS API Token（或设环境变量 SMS_API_TOKEN）")
    parser.add_argument("--sms-device-id", default=None,
                        help="SMS API 设备标识（默认 doubao_spider）")
    args = parser.parse_args()

    set_op_log_device(args.serial)

    dm = DeviceManager(args.serial)
    device = dm.get_device()

    profile = load_profile(device_name=args.device_profile, device=device)
    crawler = FlowCrawler(device, output_dir="logs", profile=profile)
    result = crawler.run(
        prompt=args.prompt,
        skip_send=args.skip_send,
        max_products_per_card=args.max_products_per_card,
        max_cards=args.max_cards,
        sms_token=args.sms_token or "",
        sms_device_id=args.sms_device_id or "",
    )

    if result["products_captured"] > 0:
        log_info(f"爬取完成：{result['products_captured']} 个商品详情截图")
        return 0
    elif result["reply_text"]:
        log_info("已获取回复但未采集到商品详情")
        return 0
    else:
        log_error("未获取到有效数据")
        return 1


if __name__ == "__main__":
    sys.exit(main())
