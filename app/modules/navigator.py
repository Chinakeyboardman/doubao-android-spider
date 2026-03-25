# -*- coding: utf-8 -*-
"""
页面导航与识别模块：基于 Activity + resource-id 判断当前所在页面，
提供跨页面跳转与安全返回方法。

基于 flow_recorder 录制的真实导航结构：
  ChatActivity ↔ AppletActivity (搜索/商品列表) ↔ WebActivity (商品详情)
"""

from __future__ import annotations

import time
from enum import Enum, auto
from typing import Any, Optional


class Page(Enum):
    UNKNOWN = auto()
    LOGIN = auto()
    CHAT = auto()
    APPLET_LIST = auto()     # AppletActivity — 搜索结果/商品列表
    WEB_DETAIL = auto()      # WebActivity — 商品详情 H5
    SHARE_OVERLAY = auto()   # 分享面板/对话选择等覆盖层
    OTHER_APP = auto()


PACKAGE = "com.larus.nova"

_PAGE_RULES: list[tuple[str, Page]] = [
    ("chat.ChatActivity", Page.CHAT),
    ("AccountLoginActivity", Page.LOGIN),
    ("PhoneLoginActivity", Page.LOGIN),
    ("VerificationCodeActivity", Page.LOGIN),
    ("applet.view.AppletActivity", Page.APPLET_LIST),
    ("search.impl.WebActivity", Page.WEB_DETAIL),
]


class Navigator:
    """统一的页面识别与导航控制器。"""

    def __init__(self, device: Any):
        self.d = device

    # 分享/对话选择面板特征 rid
    _SHARE_OVERLAY_RIDS = (
        "com.larus.nova:id/share_layout",
        "com.larus.nova:id/panel_bottom_share_lay",
        "com.larus.nova:id/panel_bottom_share_con",
        "com.larus.nova:id/share_dialog",
        "com.larus.nova:id/dialog_share",
    )
    # 对话选择面板（"发送给"弹窗）
    _DIALOG_SELECT_RIDS = (
        "com.larus.nova:id/dialog_root",
        "com.larus.nova:id/dialogRootView",
    )

    def current_page(self) -> tuple[Page, dict[str, str]]:
        """返回 (Page 枚举, {"package": ..., "activity": ...})。"""
        try:
            cur = self.d.app_current() or {}
        except Exception:
            return Page.UNKNOWN, {}
        pkg = cur.get("package", "")
        act = cur.get("activity", "")
        if PACKAGE not in pkg:
            return Page.OTHER_APP, cur
        for keyword, page in _PAGE_RULES:
            if keyword in act:
                # 即使 Activity 是 Chat，也可能有分享面板覆盖在上面
                if page == Page.CHAT and self._has_share_overlay():
                    return Page.SHARE_OVERLAY, cur
                return page, cur
        return Page.UNKNOWN, cur

    def _has_share_overlay(self) -> bool:
        """检测当前是否有分享面板/对话选择弹窗覆盖。"""
        for rid in self._SHARE_OVERLAY_RIDS + self._DIALOG_SELECT_RIDS:
            try:
                el = self.d.xpath(f'//*[@resource-id="{rid}"]').get(timeout=0.2)
                if el:
                    return True
            except Exception:
                continue
        return False

    def wait_for_page(self, target: Page, timeout: float = 10) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            p, _ = self.current_page()
            if p == target:
                return True
            time.sleep(0.5)
        return False

    def is_chat(self) -> bool:
        p, _ = self.current_page()
        return p == Page.CHAT

    def is_applet_list(self) -> bool:
        p, _ = self.current_page()
        return p == Page.APPLET_LIST

    def is_web_detail(self) -> bool:
        p, _ = self.current_page()
        return p == Page.WEB_DETAIL

    def is_login(self) -> bool:
        p, _ = self.current_page()
        return p == Page.LOGIN

    def dismiss_overlay(self) -> bool:
        """如果有分享面板/对话选择弹窗，按 back 关闭并返回 True。"""
        if self._has_share_overlay():
            print("  [导航] 检测到分享/对话面板，按 back 关闭")
            self.d.press("back")
            time.sleep(1.0)
            return True
        return False

    def safe_back_to_chat(self, max_backs: int = 6) -> bool:
        """从任意页面安全返回 ChatActivity，自动处理分享面板等覆盖层。"""
        for i in range(max_backs):
            p, _ = self.current_page()
            if p == Page.CHAT:
                print(f"  [导航] 已回到聊天页（{i} 次 back）")
                return True
            if p == Page.SHARE_OVERLAY:
                print(f"  [导航] 关闭分享面板（第 {i+1} 次）")
                self.d.press("back")
                time.sleep(1.0)
                continue
            if p == Page.OTHER_APP:
                self.d.app_start(PACKAGE)
                time.sleep(2)
                continue
            self.d.press("back")
            time.sleep(1.2)
        p, _ = self.current_page()
        return p == Page.CHAT

    def wait_web_detail_loaded(self, timeout: float = 15) -> bool:
        """等待 WebActivity 商品详情加载完毕（progress_bar 消失或出现购物车类文案）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_web_detail():
                return False
            # progress_bar 消失
            try:
                pb = self.d.xpath('//*[@resource-id="com.larus.nova:id/progress_bar"]').get(timeout=0.3)
                if pb:
                    txt = (pb.info.get("text") or "").strip()
                    try:
                        val = float(txt)
                        if val >= 99:
                            return True
                    except ValueError:
                        pass
                    time.sleep(0.5)
                    continue
            except Exception:
                pass
            # 已无 progress_bar
            return True
        return True

    def has_product_detail_signals(self) -> bool:
        """当前页面是否有商品详情特征（加入购物车、去抢购等）。"""
        signals = ("加入购物车", "去抢购", "立即购买", "进店逛逛")
        try:
            for n in self.d.xpath("//android.view.View").all():
                try:
                    txt = (n.info.get("text") or "").strip()
                    if any(s in txt for s in signals):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False
