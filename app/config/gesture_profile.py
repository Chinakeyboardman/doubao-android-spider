# -*- coding: utf-8 -*-
"""
设备手势/几何参数集中配置。

所有字段默认值 = 当前代码中的硬编码值，不改行为。
多设备部署时可通过 JSON profile 覆盖（见 profile_loader.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GestureProfile:
    """屏幕手势与元素检测参数（比例值基于屏幕宽高，跨分辨率自适应）。"""

    # ── 屏幕默认尺寸（device.info 缺失时的回退值） ──
    default_screen_width: int = 1080
    default_screen_height: int = 2400

    # ── 安全滑动 x 比例（避开屏幕正中操作栏按钮） ──
    safe_swipe_x_ratio: float = 0.25

    # ── 回到底部（response_capture._scroll_to_bottom） ──
    scroll_bottom_start_y: float = 0.40
    scroll_bottom_end_y: float = 0.72
    scroll_bottom_duration: float = 0.25

    # ── stabilize 定位最新问答区域 ──
    stabilize_start_y: float = 0.40
    stabilize_end_y: float = 0.72
    stabilize_duration: float = 0.25
    stabilize_anchor_min_y: float = 0.48

    # ── 露出回底按钮（轻微上滑） ──
    reveal_btn_start_y: float = 0.76
    reveal_btn_end_y: float = 0.60
    reveal_btn_duration: float = 0.10

    # ── 商品卡片反向滚动扫描（手指下滑 = 显示上方内容 = 向 query 方向滚） ──
    card_scan_start_y: float = 0.42
    card_scan_end_y: float = 0.58
    card_scan_duration: float = 0.30

    # ── 关闭弹窗（点击空白区域） ──
    dismiss_x_ratio: float = 0.50
    dismiss_y_ratio: float = 0.08

    # ── 长按时长 ──
    long_click_duration: float = 0.8

    # ── 底部按钮校验：输入框位置 ──
    input_box_min_y_ratio: float = 0.50

    # ── 形状匹配圆形回底按钮 ──
    btn_center_x_min: float = 0.20
    btn_center_x_max: float = 0.80
    btn_bottom_max_y: float = 0.90
    btn_shape_min_px: int = 56
    btn_shape_max_px: int = 300
    btn_shape_ratio_min: float = 0.72
    btn_shape_ratio_max: float = 1.38
    btn_center_near_mid: float = 0.15

    # ── 横向商品卡片检测 ──
    card_min_width_ratio: float = 0.26
    card_min_width_px: int = 220
    card_max_height_ratio: float = 0.22
    card_max_height_px: int = 320
    card_min_height_ratio: float = 0.03
    card_min_height_px: int = 48
    card_aspect_min: float = 2.0

    # ── 内容区域 fallback 比例 ──
    content_top_fallback: float = 0.12
    content_bottom_fallback: float = 0.83
    content_bottom_input_offset: int = 4
    content_bottom_input_text_offset: int = 16
    content_bottom_min_ratio: float = 0.28

    # ── 用户气泡几何判断 ──
    bubble_min_bh: int = 18
    bubble_min_bw: int = 32
    bubble_cx_threshold_1: float = 0.46
    bubble_x2_threshold: float = 0.66
    bubble_x1_threshold: float = 0.16
    bubble_cx_threshold_2: float = 0.52
    bubble_assist_x1_strong: float = 0.13
    bubble_assist_bw_strong: float = 0.64
    bubble_assist_x1_weak: float = 0.16
    bubble_assist_bw_weak: float = 0.58

    # ── 助手回复块判断 ──
    assist_block_x1_max: float = 0.20
    assist_block_bw_min: float = 0.52
    assist_block_cx_max: float = 0.48

    # ── try_click_copy_button 内滑动（露出操作栏） ──
    copy_retry_swipe_start_y: float = 0.45
    copy_retry_swipe_end_y: float = 0.82
    copy_retry_swipe_duration: float = 0.14

    # ── card_click_worker: 回到底部 ──
    jump_bottom_start_y: float = 0.45
    jump_bottom_end_y: float = 0.85
    jump_bottom_duration: float = 0.15

    # ── card_click_worker: 极慢上滑 ──
    slow_scroll_start_y: float = 0.65
    slow_scroll_min_end_y: float = 0.20
    slow_scroll_duration: float = 0.35
    slow_scroll_pct_early: float = 0.05
    slow_scroll_pct_mid: float = 0.08
    slow_scroll_pct_late: float = 0.10
    slow_scroll_early_rounds: int = 5
    slow_scroll_mid_rounds: int = 15

    # ── card_click_worker: 消息区域 ──
    msg_area_top: float = 0.10

    # ── card_click_worker: 隐形卡片 ──
    invisible_card_min_w_ratio: float = 0.60
    invisible_card_min_h_ratio: float = 0.08
    follow_up_left_margin: float = 0.06

    # ── product_list_capture: 列表滚动 ──
    list_scroll_down_start_y: float = 0.72
    list_scroll_down_end_y: float = 0.28
    list_scroll_down_duration: float = 0.25
    list_scroll_up_start_y: float = 0.28
    list_scroll_up_end_y: float = 0.78
    list_scroll_up_duration: float = 0.25

    # ── product_list_capture: 列表项检测 ──
    list_item_top_y: float = 0.12
    list_item_bottom_y: float = 0.92
    list_item_min_w_ratio: float = 0.15
    list_item_max_w_ratio: float = 0.95
    list_item_min_h_ratio: float = 0.04
    list_item_max_h_ratio: float = 0.25

    # ── product_detail_capture: 详情页滚动 ──
    detail_scroll_start_y: float = 0.75
    detail_scroll_end_y: float = 0.25
    detail_scroll_duration: float = 0.30

    # ── chat_window: 聊天栏布局 ──
    chat_bar_y_min_ratio: float = 0.55

    # ── flow_crawler: 各种滚动 ──
    fc_scroll_down_start_y: float = 0.75
    fc_scroll_down_end_y: float = 0.35
    fc_scroll_down_duration: float = 0.20
    fc_card_min_h_ratio: float = 0.12
    fc_card_max_h_ratio: float = 0.25
    fc_card_min_w_ratio: float = 0.55
    fc_title_min_w_ratio: float = 0.40
    fc_title_min_h_ratio: float = 0.05
    fc_title_min_y_ratio: float = 0.10
    fc_detail_scroll_start_y: float = 0.75
    fc_detail_scroll_end_y: float = 0.25
    fc_detail_scroll_duration: float = 0.30
    fc_reply_top_scroll_start_y: float = 0.60
    fc_reply_top_scroll_end_y: float = 0.38
    fc_reply_top_scroll_duration: float = 0.20
    fc_scroll_down_to_cards_start_y: float = 0.35
    fc_scroll_down_to_cards_end_y: float = 0.70
    fc_scroll_down_to_cards_duration: float = 0.25
    fc_card_top_visible_y: float = 0.25
    fc_title_pixel_min_y: int = 200
    fc_title_image_max_dy: int = 200
