# -*- coding: utf-8 -*-
"""
设备手势/几何参数集中配置。

所有字段默认值 = 当前代码中的硬编码值，不改行为。
多设备部署时可通过 JSON profile 覆盖（见 profile_loader.py）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_qa_ref_list_probe_xpaths() -> tuple[str, ...]:
  """引用列表容器探测顺序（真机侦察后可在 profile JSON 覆盖）。"""
  pkg = "com.larus.nova"
  return (
    f'//*[@resource-id="{pkg}:id/search_reference_list"]',
    f'//*[@resource-id="{pkg}:id/sub_keyword_reference"]'
    f"//androidx.recyclerview.widget.RecyclerView",
    f'//*[@resource-id="{pkg}:id/sub_keyword_reference"]',
  )


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
    # Web 详情触底：最大滑动次数、停稳、ROI 与多指标静止阈值（见 web_detail_capture）
    fc_detail_bottom_max_swipes: int = 100
    fc_detail_bottom_post_swipe_sleep: float = 0.95
    fc_detail_bottom_stable_swipes: int = 2
    fc_detail_bottom_strict_fine: float = 4.0
    fc_detail_bottom_alt_fine: float = 10.0
    fc_detail_bottom_relax_fine: float = 36.0
    fc_detail_bottom_relax_coarse: float = 20.0
    fc_detail_bottom_relax_dhash: int = 26
    fc_detail_roi_x0: float = 0.06
    fc_detail_roi_x1: float = 0.94
    fc_detail_roi_y0: float = 0.14
    fc_detail_roi_y1: float = 0.86
    # 长条截图横向裁剪（默认全宽，不误切左右商品图）；垂向仍用 fc_detail_roi_y0/y1
    fc_detail_strip_roi_x0: float = 0.0
    fc_detail_strip_roi_x1: float = 1.0
    # 长条拼接：手势限制在内容区内（相对内容区顶边的比例 0~1）
    fc_detail_strip_swipe_y_start_ratio: float = 0.86
    fc_detail_strip_swipe_y_end_ratio: float = 0.18
    fc_detail_strip_swipe_duration: float = 0.30
    fc_reply_top_scroll_start_y: float = 0.60
    fc_reply_top_scroll_end_y: float = 0.38
    fc_reply_top_scroll_duration: float = 0.20
    fc_scroll_down_to_cards_start_y: float = 0.35
    fc_scroll_down_to_cards_end_y: float = 0.70
    fc_scroll_down_to_cards_duration: float = 0.25
    fc_card_top_visible_y: float = 0.25
    fc_title_pixel_min_y: int = 200
    fc_title_image_max_dy: int = 200
    # 豆包卡在 WebActivity 等：连续 N 次软恢复失败后 force-stop 冷启动
    fc_app_hard_restart_stuck: int = 3

    # ── qa_capture: 问答归档几何启发式 ──
    qa_user_bubble_cx_min: float = 0.55
    qa_user_bubble_x1_min: float = 0.35
    qa_assist_x1_max: float = 0.22
    qa_assist_bw_min: float = 0.45
    qa_assist_cx_max: float = 0.52
    qa_scroll_top_start_y: float = 0.35
    qa_scroll_top_end_y: float = 0.70
    qa_scroll_top_duration: float = 0.25
    qa_scroll_top_rounds: int = 8
    # 问答长截图：聊天内容区 ROI + 滑动参数
    qa_shot_roi_y0: float = 0.12
    qa_shot_roi_y1: float = 0.83
    qa_shot_max_frames: int = 25
    qa_shot_scroll_start_y: float = 0.72
    qa_shot_scroll_end_y: float = 0.32
    qa_shot_scroll_duration: float = 0.28
    qa_shot_post_swipe_sleep: float = 0.55
    qa_shot_quiet_rounds: int = 2
    # 长截图：message_list 内单次下滑比例（旧逻辑约 0.50，过大易漏段）
    qa_shot_list_swipe_frac: float = 0.30
    # 相对可见内容区 ROI 的最大下滑像素比例（保证帧间重叠）
    qa_shot_scroll_advance_frac: float = 0.45
    qa_shot_list_swipe_duration: float = 0.38
    # 相邻 shot 重叠低于该比例时打日志（约为 ROI 高度）
    qa_shot_min_overlap_frac: float = 0.28
    qa_think_panel_scroll_rounds: int = 12
    qa_expand_collect_max_rounds: int = 20
    qa_expand_refs_wait: float = 3.0
    qa_expand_refs_poll_interval: float = 0.25
    qa_expand_group_click_sleep: float = 1.2
    qa_resolve_url_wait: float = 3.5
    qa_resolve_url_post_back_sleep: float = 0.5
    qa_resolve_url_max_backs: int = 5
    # URL 解析整阶段 wall-clock 预算（秒）；0=不限制
    qa_resolve_url_phase_budget_sec: float = 480.0
    # 单条任务内会话恢复（含 hard_restart）次数上限；0=不限制
    qa_resolve_recover_max_per_task: int = 3
    # URL 解析期会话守卫：False=完全不做错位校验/恢复（60710 轻量模式）
    qa_resolve_session_guard: bool = True
    # 判定错位前二次确认（隔 reconfirm_sleep 再读一次），消除瞬时误判
    qa_resolve_session_guard_reconfirm: bool = True
    qa_resolve_session_guard_reconfirm_sleep: float = 0.6
    # True=快速逐条后不再跑笨办法（配合 allow_partial 提速）
    qa_resolve_skip_brute_pass: bool = False
    # True=60710 单遍：逐条 logcat→dumpsys→lite_back，无快速/笨办法分两趟
    qa_resolve_simple_mode: bool = False
    qa_resolve_url_max_refs: int = 0
    qa_resolve_citation_max_swipes: int = 12
    qa_resolve_prepare_list_passes: int = 8
    qa_resolve_logcat_poll_timeout: float = 1.5
    qa_resolve_logcat_poll_interval: float = 0.2
    qa_resolve_batch_douyin: bool = True
    qa_resolve_batch_douyin_timeout: float = 6.0
    qa_resolve_skip_douyin_per_click: bool = True
    # vivo 等：点抖音引用时系统弹「是否打开 App」，需点「打开」才能 logcat 抓 aweme id
    qa_resolve_accept_app_jump: bool = False
    # 抖音 Handoff：深链优先、状态机超时、scheme 列表
    qa_douyin_handoff_timeout: float = 20.0
    qa_douyin_deeplink_first: bool = True
    qa_douyin_deeplink_schemes: tuple[str, ...] = ("snssdk1128", "snssdk1180")
    qa_douyin_ensure_login_before_batch: bool = True
    # PC Web 辅助：logcat 抽到 aweme_id 后 HTTP 验证再写 iesdouyin（可跳过手机开抖音）
    qa_douyin_web_validate: bool = True
    qa_douyin_web_validate_interval: float = 0.8
    qa_douyin_web_validate_fallback: bool = True
    qa_douyin_web_url_formats: tuple[str, ...] = (
        "douyin_jingxuan_modal",
        "douyin_video",
        "iesdouyin_share",
        "iesdouyin_share_query",
    )
    # 笨办法解析到 URL 后 HTTP 探测可达性（404 等单独记录，不算系统错误）
    qa_url_reachability_check: bool = True
    qa_url_reachability_timeout: float = 10.0
    qa_url_reachability_brute_only: bool = True
    qa_logcat_stream_settle: float = 0.15
    qa_logcat_stream_poll_interval: float = 0.25
    # URL 解析：引用条目视为「在屏内」的垂直 band（比例，避开状态栏/输入栏）
    qa_resolve_viewport_y0: float = 0.18
    qa_resolve_viewport_y1: float = 0.82
    # 引用列表容器 xpath 探测顺序（think 面板 / fast 内联布局）
    qa_ref_list_probe_xpaths: tuple[str, ...] = field(
        default_factory=_default_qa_ref_list_probe_xpaths
    )
