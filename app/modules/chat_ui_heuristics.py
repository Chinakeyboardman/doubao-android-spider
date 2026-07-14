# -*- coding: utf-8 -*-
"""
聊天区布局启发式：通过真实 resource-id 定位区域，适配不同分辨率/机型。

布局结构（实测 com.larus.nova 各版本一致）：
  status_bar → title_container → message_list_parent → splitter → input(包含 action_bar + input_text)
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from app.config.gesture_profile import GestureProfile

# 按优先级定位内容区底部（越上面越精确）
_CHAT_INPUT_SELECTORS = (
    '//*[@resource-id="com.larus.nova:id/input_text"]',
    '//*[@resource-id="com.larus.nova:id/input"]',
    '//*[@resource-id="com.larus.nova:id/action_send"]',
)
_CONTENT_BOTTOM_SELECTORS = (
    '//*[@resource-id="com.larus.nova:id/splitter"]',
    '//*[@resource-id="com.larus.nova:id/message_list_parent"]',
    '//*[@resource-id="com.larus.nova:id/input"]',
    '//*[@resource-id="com.larus.nova:id/input_text"]',
)
_CONTENT_TOP_SELECTORS = (
    '//*[@resource-id="com.larus.nova:id/title_container"]',
    '//*[@resource-id="com.larus.nova:id/message_list_parent"]',
)
# 用于直接点击复制（比长按更可靠）
MSG_ACTION_COPY_XPATH = '//*[@resource-id="com.larus.nova:id/msg_action_copy"]'
# 回底按钮（精确 id）
FAST_BUTTON_ICON_XPATH = '//*[@resource-id="com.larus.nova:id/fast_button_icon"]'


def display_wh(device: Any, profile: GestureProfile | None = None) -> tuple[int, int]:
    p = profile or GestureProfile()
    info = device.info
    w = int(info.get("displayWidth") or p.default_screen_width)
    h = int(info.get("displayHeight") or p.default_screen_height)
    return w, h


def has_chat_ui(device: Any) -> bool:
    """豆包前台时是否存在聊天输入区（兼容 AliasActivity 与 ChatActivity）。"""
    for sel in _CHAT_INPUT_SELECTORS:
        try:
            el = device.xpath(sel).get(timeout=0.35)
            if el:
                return True
        except Exception:
            continue
    return False


def _get_bounds_y(device: Any, selectors: tuple[str, ...], idx: int) -> Optional[int]:
    """取第一个命中选择器的 bounds[idx]（idx: 1=top, 3=bottom）。"""
    for sel in selectors:
        try:
            el = device.xpath(sel).get(timeout=0.35)
            if el:
                b = el.bounds
                if b and len(b) >= 4:
                    return int(b[idx])
        except Exception:
            continue
    return None


def content_top_y(device: Any, h: int, profile: GestureProfile | None = None) -> int:
    """消息列表顶部 y（title_container 底边或 message_list_parent 顶边）。"""
    p = profile or GestureProfile()
    # title_container 底边
    y = _get_bounds_y(device, _CONTENT_TOP_SELECTORS[:1], 3)
    if y is not None:
        return y
    # message_list_parent 顶边
    y = _get_bounds_y(device, _CONTENT_TOP_SELECTORS[1:], 1)
    if y is not None:
        return y
    return int(h * p.content_top_fallback)


def content_bottom_y(device: Any, h: int, profile: GestureProfile | None = None) -> int:
    """消息内容允许的最大 y（splitter 顶边 > message_list_parent 底边 > input 顶边）。"""
    p = profile or GestureProfile()
    # splitter 顶边 — 最精确的分界线
    y = _get_bounds_y(device, _CONTENT_BOTTOM_SELECTORS[:1], 1)
    if y is not None:
        return y
    # message_list_parent 底边
    y = _get_bounds_y(device, _CONTENT_BOTTOM_SELECTORS[1:2], 3)
    if y is not None:
        return y
    # input 容器顶边
    y = _get_bounds_y(device, _CONTENT_BOTTOM_SELECTORS[2:3], 1)
    if y is not None:
        return max(int(h * p.content_bottom_min_ratio), y - p.content_bottom_input_offset)
    # input_text 顶边（最后兜底）
    y = _get_bounds_y(device, _CONTENT_BOTTOM_SELECTORS[3:], 1)
    if y is not None:
        return max(int(h * p.content_bottom_min_ratio), y - p.content_bottom_input_text_offset)
    return int(h * p.content_bottom_fallback)


def norm_prompt_keys(prompt_text: str) -> tuple[str, str]:
    key = "".join((prompt_text or "").split())
    short_key = key[: min(24, len(key))] if key else ""
    return key, short_key


def iter_text_view_like_nodes(device: Any) -> Iterator[Any]:
    """遍历常见「消息文案」节点（不同 ROM/组件可能不是 android.widget.TextView）。"""
    seen: set[tuple[str, tuple[Any, ...]]] = set()
    selectors = (
        "//android.widget.TextView",
        "//*[contains(@class,'AppCompatTextView')]",
        "//*[contains(@class,'MaterialTextView')]",
    )
    for sel in selectors:
        try:
            for n in device.xpath(sel).all():
                try:
                    txt = (n.info.get("text") or "").strip()
                    b = n.bounds
                    if not b or len(b) < 4:
                        continue
                    key = (txt[:120], tuple(int(x) for x in b))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield n
                except Exception:
                    continue
        except Exception:
            continue


def user_bubble_geometry_score(
    w: int, h: int, bounds: tuple[int, int, int, int],
    inf: Optional[dict] = None, profile: GestureProfile | None = None,
) -> int:
    """
    用户侧气泡几何分（越高越像「右侧自己的话」），不依赖单一机型像素。
    助手长文通常：靠左 x1 小 + 宽度大；用户气泡：整体中心偏右。
    """
    p = profile or GestureProfile()
    x1, y1, x2, y2 = bounds
    bw, bh = x2 - x1, y2 - y1
    if bh < p.bubble_min_bh or bw < p.bubble_min_bw:
        return -10
    cx = (x1 + x2) / 2
    s = 0
    if cx >= w * p.bubble_cx_threshold_1:
        s += 2
    if x2 >= w * p.bubble_x2_threshold:
        s += 2
    if x1 >= w * p.bubble_x1_threshold:
        s += 1
    if cx >= w * p.bubble_cx_threshold_2:
        s += 1
    # 强惩罚：典型助手块（贴左且很宽）
    if x1 <= w * p.bubble_assist_x1_strong and bw >= w * p.bubble_assist_bw_strong:
        s -= 7
    elif x1 <= w * p.bubble_assist_x1_weak and bw >= w * p.bubble_assist_bw_weak:
        s -= 4
    if inf:
        if bool(inf.get("clickable")):
            s += 1
        rid = (inf.get("resourceName") or "").strip()
        if rid == "":
            s += 1
    return s


def is_likely_user_query_bubble(
    w: int,
    h: int,
    bounds: tuple[int, int, int, int],
    inf: dict,
    short_key: str,
    text: str,
    profile: GestureProfile | None = None,
) -> bool:
    if not short_key:
        return False
    norm = "".join(text.split())
    if short_key not in norm:
        return False
    return user_bubble_geometry_score(w, h, bounds, inf, profile=profile) >= 3


def get_query_anchor_bounds(
    device: Any, prompt_text: str,
    profile: GestureProfile | None = None,
) -> Optional[tuple[int, int, int, int]]:
    """当前屏内、最靠下的「含 prompt 文案的用户气泡」bounds。"""
    _, short_key = norm_prompt_keys(prompt_text)
    if not short_key:
        return None
    w, h = display_wh(device, profile=profile)
    candidates: list[tuple[int, tuple[int, int, int, int]]] = []
    try:
        for n in iter_text_view_like_nodes(device):
            try:
                inf = n.info
                txt = (inf.get("text") or "").strip()
                if not txt:
                    continue
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                bounds = (int(b[0]), int(b[1]), int(b[2]), int(b[3]))
                if not is_likely_user_query_bubble(w, h, bounds, inf, short_key, txt, profile=profile):
                    continue
                candidates.append((bounds[3], bounds))
            except Exception:
                continue
    except Exception:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def query_anchor_visible(device: Any, prompt_text: str) -> bool:
    return get_query_anchor_bounds(device, prompt_text) is not None


_MSG_ACTION_COPY_SELECTORS = (
    MSG_ACTION_COPY_XPATH,
    '//*[@resource-id="com.larus.nova:id/msg_action_copy_text"]',
)

_COPY_TEXT_SELECTORS = (
    '//*[@text="复制"]',
    '//*[@text="复制文本"]',
)

_ACTION_BAR_BAD_KEYWORDS = (
    "收藏", "喜欢", "favorite", "collect", "bookmark",
    "分享", "share", "点赞", "like", "重新生成",
)


def _is_action_bar_copy(device: Any, node: Any) -> bool:
    """判断候选「复制」节点是否确实是操作栏的复制按钮（排除收藏等）。"""
    try:
        inf = node.info
        text = (inf.get("text") or "").strip()
        desc = (inf.get("contentDescription") or "").strip()
        rid = (inf.get("resourceName") or "").strip()
        combined = f"{text} {desc} {rid}".lower()
        if any(k in combined for k in _ACTION_BAR_BAD_KEYWORDS):
            return False
        if "复制" in text or "复制" in desc or "copy" in rid.lower():
            return True
    except Exception:
        pass
    return False


def try_click_copy_button(
    device: Any, max_retries: int = 3,
    profile: GestureProfile | None = None,
) -> bool:
    """
    点击消息操作栏的「复制」按钮（rid 精确 → 文本兜底）。
    多次重试：每次先轻微下滑让操作栏露出。
    """
    import time

    p = profile or GestureProfile()

    for attempt in range(max_retries):
        # 精确 resource-id
        for sel in _MSG_ACTION_COPY_SELECTORS:
            try:
                btn = device.xpath(sel).get(timeout=0.8)
                if btn and _is_action_bar_copy(device, btn):
                    btn.click()
                    time.sleep(0.5)
                    return True
            except Exception:
                continue

        # 文本兜底：找「复制」但排除误伤
        for sel in _COPY_TEXT_SELECTORS:
            try:
                nodes = device.xpath(sel).all()
                for n in nodes:
                    if _is_action_bar_copy(device, n):
                        n.click()
                        time.sleep(0.5)
                        return True
            except Exception:
                continue

        if attempt + 1 < max_retries:
            try:
                w, h = display_wh(device, profile=profile)
                device.swipe(
                    int(w * 0.5), int(h * p.copy_retry_swipe_start_y),
                    int(w * 0.5), int(h * p.copy_retry_swipe_end_y),
                    p.copy_retry_swipe_duration,
                )
                time.sleep(0.6)
            except Exception:
                pass

    return False


_NOVA_RID_EXCLUDE_PREFIXES = (
    "com.larus.nova:id/tv_item_name",
    "com.larus.nova:id/title",
    "com.larus.nova:id/subtitle",
    "com.android.systemui:",
)


def collect_reply_text_candidates(
    device: Any,
    prompt_text: str = "",
    min_len: int = 20,
    profile: GestureProfile | None = None,
) -> list[tuple[str, int, tuple[int, int, int, int]]]:
    """
    收集疑似助手回复的长文本节点，按底边 y 降序（靠下优先）。
    排除：输入区以下、含 prompt 的用户气泡、action bar 项（快速/AI创作等）。
    """
    w, h = display_wh(device, profile=profile)
    _, short_key = norm_prompt_keys(prompt_text)
    bottom_limit = content_bottom_y(device, h, profile=profile)
    top_limit = content_top_y(device, h, profile=profile)
    prompt_strip = (prompt_text or "").strip()

    results: list[tuple[str, int, tuple[int, int, int, int]]] = []
    try:
        for n in iter_text_view_like_nodes(device):
            try:
                inf = n.info
                text = (inf.get("text") or "").strip()
                if len(text) < min_len:
                    continue
                if prompt_strip and text == prompt_strip:
                    continue
                rid = (inf.get("resourceName") or "").strip()
                if any(rid.startswith(pfx) for pfx in _NOVA_RID_EXCLUDE_PREFIXES):
                    continue
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                bounds = (x1, y1, x2, y2)
                if y1 >= bottom_limit:
                    continue
                if y2 <= top_limit:
                    continue
                if short_key and short_key in "".join(text.split()):
                    if is_likely_user_query_bubble(w, h, bounds, inf, short_key, text, profile=profile):
                        continue
                results.append((text, y2, bounds))
            except Exception:
                continue
    except Exception:
        return []

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def assistant_block_likelihood(
    w: int, bounds: tuple[int, int, int, int],
    profile: GestureProfile | None = None,
) -> int:
    """越高越像左侧/全宽助手正文（用于排序加权，非硬过滤）。"""
    p = profile or GestureProfile()
    x1, _, x2, _ = bounds
    bw = x2 - x1
    cx = (x1 + x2) / 2
    s = 0
    if x1 <= int(w * p.assist_block_x1_max):
        s += 2
    if bw >= int(w * p.assist_block_bw_min):
        s += 2
    if cx <= w * p.assist_block_cx_max:
        s += 1
    return s
