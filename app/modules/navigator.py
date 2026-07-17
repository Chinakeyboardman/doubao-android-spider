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

from app.modules.chat_ui_heuristics import has_chat_ui


class Page(Enum):
    UNKNOWN = auto()
    LOGIN = auto()
    HOME = auto()            # AliasActivity — 新版豆包首页/聊天壳
    CHAT = auto()
    APPLET_LIST = auto()     # AppletActivity — 搜索结果/商品列表
    WEB_DETAIL = auto()      # WebActivity — 商品详情 H5
    SHARE_OVERLAY = auto()   # 分享面板/对话选择等覆盖层
    OTHER_APP = auto()


PACKAGE = "com.larus.nova"

_PAGE_RULES: list[tuple[str, Page]] = [
    ("chat.ChatActivity", Page.CHAT),
    ("home.impl.alias.AliasActivity", Page.HOME),
    ("AccountLoginActivity", Page.LOGIN),
    ("AccountLoginHalfActivity", Page.LOGIN),
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
                if page in (Page.CHAT, Page.HOME) and has_chat_ui(self.d):
                    if self._has_share_overlay():
                        return Page.SHARE_OVERLAY, cur
                    return Page.CHAT, cur
                return page, cur
        if has_chat_ui(self.d):
            if self._has_share_overlay():
                return Page.SHARE_OVERLAY, cur
            return Page.CHAT, cur
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

    def is_guest_chat(self) -> bool:
        """Chat 页且顶部有「立即登录」横幅 → 游客态（发信约 10~20 条限额）。"""
        if not self.is_chat():
            return False
        try:
            el = self.d.xpath(
                '//*[@resource-id="com.larus.nova:id/tv_login_guide_banner"]'
            ).get(timeout=0.5)
            return el is not None
        except Exception:
            return False

    def dismiss_overlay(self) -> bool:
        """关闭分享面板、消息提醒弹窗等覆盖聊天区的浮层。"""
        if self.dismiss_push_reminder_dialog():
            return True
        if self._has_share_overlay():
            print("  [导航] 检测到分享/对话面板，按 back 关闭")
            self.d.press("back")
            time.sleep(1.0)
            return True
        return False

    def dismiss_push_reminder_dialog(self) -> bool:
        """豆包「及时获得消息提醒」半屏弹窗：点关闭，不点「开启通知」。"""
        try:
            title = self.d.xpath(
                '//*[@resource-id="com.larus.nova:id/tv_push_reminder_dialog_title"]'
            ).get(timeout=0.35)
        except Exception:
            title = None
        if not title:
            return False
        for sel in (
            '//*[@resource-id="com.larus.nova:id/iv_push_reminder_dialog_close"]',
            '//*[@content-desc="关闭"]',
        ):
            try:
                el = self.d.xpath(sel).get(timeout=0.5)
            except Exception:
                continue
            if el:
                el.click()
                print("  [导航] 已关闭消息提醒弹窗")
                time.sleep(0.6)
                return True
        print("  [导航] 消息提醒弹窗无关闭按钮，按 back 尝试退出")
        self.d.press("back")
        time.sleep(0.6)
        return True

    _CONSENT_POSITIVE_XPATHS: tuple[str, ...] = (
        '//*[@resource-id="com.android.permissioncontroller:id/permission_allow_button"]',
        '//*[@resource-id="com.android.permissioncontroller:id/permission_allow_foreground_only_button"]',
        '//*[@resource-id="com.android.permissioncontroller:id/permission_allow_one_time_button"]',
        '//*[@resource-id="com.android.packageinstaller:id/permission_allow_button"]',
        '//*[@resource-id="com.android.packageinstaller:id/permission_allow_foreground_only_button"]',
        '//*[@resource-id="com.larus.nova:id/confirm"]',
        '//*[@text="允许"]',
        '//*[@text="仅在使用该应用时允许"]',
        '//*[@text="仅在使用中允许"]',
        '//*[@text="始终允许"]',
        '//*[@text="允许通知"]',
        '//*[@text="开启通知"]',
        '//*[@text="同意"]',
        '//*[@text="确定"]',
        '//*[@text="确认"]',
        '//*[@text="知道了"]',
        '//*[@text="我知道了"]',
        '//*[@text="立即体验"]',
        '//*[@text="立即开启"]',
        '//*[@text="开启"]',
        '//*[@text="去体验"]',
        '//*[@text="开始体验"]',
        '//*[@text="授权"]',
        '//*[@text="去授权"]',
        '//*[@text="继续"]',
        '//*[@text="下一步"]',
        '//*[contains(@text,"立即体验")]',
        '//*[contains(@text,"允许通知")]',
        '//*[contains(@text,"录音") and contains(@text,"允许")]',
        '//*[contains(@text,"麦克风") and contains(@text,"允许")]',
        '//*[contains(@text,"录制音频")]',
    )

    _CONSENT_NEGATIVE_TEXTS: frozenset[str] = frozenset({
        "取消", "拒绝", "暂不", "忽略", "跳过", "以后再说", "不再提示", "关闭",
        "不同意", "暂不需要", "下次再说",
    })

    _CONSENT_HIERARCHY_LABELS: tuple[str, ...] = (
        "允许", "仅在使用该应用时允许", "仅在使用中允许", "始终允许",
        "允许通知", "开启通知", "同意", "确定", "确认", "知道了", "我知道了",
        "立即体验", "立即开启", "开启", "去体验", "开始体验", "授权", "去授权",
        "继续", "下一步",
    )

    def _is_consent_positive_label(self, text: str) -> bool:
        t = (text or "").strip()
        if not t or t in self._CONSENT_NEGATIVE_TEXTS:
            return False
        if any(neg in t for neg in ("取消", "拒绝", "不同意", "暂不", "忽略")):
            return False
        if t in self._CONSENT_HIERARCHY_LABELS:
            return True
        if "立即体验" in t or "允许通知" in t:
            return True
        if "录音" in t and "允许" in t:
            return True
        if "麦克风" in t and "允许" in t:
            return True
        return False

    def _click_consent_from_hierarchy(self) -> bool:
        """系统层弹窗常不在 xpath 树内，从 dump XML 按正向文案坐标点击。"""
        import re

        try:
            xml = self.d.dump_hierarchy(compressed=False) or ""
        except Exception:
            return False
        patterns = (
            r'<node[^>]*text="([^"]*)"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            r'<node[^>]*clickable="true"[^>]*text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        )
        for pat in patterns:
            for m in re.finditer(pat, xml):
                text = (m.group(1) or "").strip()
                if not self._is_consent_positive_label(text):
                    continue
                cx = (int(m.group(2)) + int(m.group(4))) // 2
                cy = (int(m.group(3)) + int(m.group(5))) // 2
                print(f"  [导航] 同意弹窗 hierarchy 点击「{text}」({cx},{cy})")
                self.d.click(cx, cy)
                time.sleep(0.8)
                return True
        return False

    def accept_blocking_prompts(self, *, max_rounds: int = 4) -> bool:
        """识别并同意授权/通知/录音/新功能体验等阻塞弹窗，避免流程卡住。"""
        accepted = False
        for _ in range(max_rounds):
            round_hit = False
            for sel in self._CONSENT_POSITIVE_XPATHS:
                try:
                    el = self.d.xpath(sel).get(timeout=0.35)
                except Exception:
                    continue
                if not el:
                    continue
                text = (el.info.get("text") or "").strip()
                if text and not self._is_consent_positive_label(text):
                    continue
                label = text or sel.rsplit("/", 1)[-1]
                el.click()
                print(f"  [导航] 已同意阻塞弹窗「{label}」")
                time.sleep(0.7)
                accepted = True
                round_hit = True
            if self._click_consent_from_hierarchy():
                accepted = True
                round_hit = True
            if not round_hit:
                break
        return accepted

    def _grant_douyin_runtime_permissions(self) -> bool:
        """抖音冷启动常见 PermissionActivity，需先点允许才能进 feed。"""
        return self.accept_blocking_prompts(max_rounds=3)

    def is_app_jump_prompt(self) -> bool:
        try:
            cur = self.d.app_current() or {}
        except Exception:
            return False
        act = cur.get("activity", "")
        pkg = cur.get("package", "")
        return "AppJumpPrompt" in act or "appfilter" in pkg

    def dismiss_app_jump_prompt(self) -> bool:
        """vivo 等机型：点击引用跳转外部 App 时的「是否打开」系统弹窗。"""
        try:
            cur = self.d.app_current() or {}
        except Exception:
            return False
        act = cur.get("activity", "")
        pkg = cur.get("package", "")
        if "AppJumpPrompt" not in act and "appfilter" not in pkg:
            return False
        print("  [导航] 关闭「是否打开 App」系统弹窗（不打开外部 App）")
        for sel in (
            '//*[@text="取消"]',
            '//*[@text="暂不"]',
            '//*[@text="拒绝"]',
            '//*[contains(@text,"取消")]',
        ):
            try:
                el = self.d.xpath(sel).get(timeout=0.4)
                if el:
                    el.click()
                    time.sleep(0.5)
                    return True
            except Exception:
                continue
        self.d.press("back")
        time.sleep(0.5)
        return True

    def _click_open_from_hierarchy(self) -> bool:
        """系统弹窗常不在 uiautomator xpath 树内，从 dump XML 按文案坐标点击。"""
        import re

        try:
            xml = self.d.dump_hierarchy(compressed=False) or ""
        except Exception:
            return False
        patterns = (
            r'<node[^>]*text="([^"]*)"[^>]*clickable="true"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
            r'<node[^>]*clickable="true"[^>]*text="([^"]*)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        )
        open_labels = ("打开", "立即打开", "允许", "始终打开", "打开应用")
        for pat in patterns:
            for m in re.finditer(pat, xml):
                text = (m.group(1) or "").strip()
                if text not in open_labels and not (
                    "打开" in text and "取消" not in text and "不" not in text
                ):
                    continue
                cx = (int(m.group(2)) + int(m.group(4))) // 2
                cy = (int(m.group(3)) + int(m.group(5))) // 2
                print(f"  [导航] hierarchy 坐标点击「{text}」({cx},{cy})")
                self.d.click(cx, cy)
                time.sleep(1.0)
                return True
        return False

    def _accept_app_jump_by_coordinate(self) -> bool:
        """vivo AppJumpPrompt 兜底：典型双按钮布局，右侧为「打开」。"""
        try:
            w, h = self.d.window_size()
        except Exception:
            return False
        for xr, yr in (
            (0.72, 0.58),
            (0.75, 0.62),
            (0.68, 0.55),
            (0.73, 0.65),
            (0.70, 0.60),
        ):
            cx, cy = int(w * xr), int(h * yr)
            print(f"  [导航] AppJump 坐标尝试 ({cx},{cy})")
            self.d.click(cx, cy)
            time.sleep(0.9)
            if self.is_aweme_foreground() or not self.is_app_jump_prompt():
                return True
        return False

    def accept_app_jump_prompt(self) -> bool:
        """vivo 等机型：点「打开」允许跳转外部 App（抖音 URL 解析必需）。"""
        if not self.is_app_jump_prompt():
            return False
        print("  [导航] 允许「打开 App」系统弹窗（抖音链接解析）")
        for sel in (
            '//*[@text="打开"]',
            '//*[@text="立即打开"]',
            '//*[@text="允许"]',
            '//*[@text="始终打开"]',
            '//*[@text="打开应用"]',
            '//*[@resource-id="android:id/button1"]',
            '//*[@resource-id="android:id/button2"]',
            '//*[contains(@text,"打开")]',
        ):
            try:
                el = self.d.xpath(sel).get(timeout=0.5)
                if el:
                    text = (el.info.get("text") or "").strip()
                    if text in ("打开", "立即打开", "允许", "始终打开", "打开应用") or (
                        "打开" in text and "取消" not in text and "不" not in text
                    ):
                        el.click()
                        time.sleep(1.0)
                        return True
            except Exception:
                continue
        if self._click_open_from_hierarchy():
            return True
        return self._accept_app_jump_by_coordinate()

    def wait_and_accept_app_jump(self, timeout: float = 8.0) -> bool:
        """等待系统跳转弹窗出现后再点「打开」。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_app_jump_prompt():
                return self.accept_app_jump_prompt()
            time.sleep(0.25)
        return False

    def is_aweme_foreground(self) -> bool:
        try:
            cur = self.d.app_current() or {}
        except Exception:
            return False
        pkg = cur.get("package", "")
        act = cur.get("activity", "")
        return "aweme" in pkg or "ugc.aweme" in act

    def wait_for_aweme_foreground(self, timeout: float = 12.0) -> bool:
        """点击「打开」后等待抖音进入前台；处理权限弹窗与跳转确认。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_aweme_foreground():
                self._grant_douyin_runtime_permissions()
                try:
                    cur = self.d.app_current() or {}
                    act = cur.get("activity", "")
                    if "Permission" not in act and "Splash" not in act:
                        print("  [导航] 抖音已进入前台")
                        return True
                except Exception:
                    print("  [导航] 抖音已进入前台")
                    return True
            if self.is_app_jump_prompt():
                self.accept_app_jump_prompt()
            else:
                self._grant_douyin_runtime_permissions()
            time.sleep(0.4)
        return self.is_aweme_foreground()

    def recover_from_external_douyin(self, *, gentle: bool = True) -> bool:
        """从抖音 App / 跳转弹窗回到豆包聊天页。

        gentle=True：优先 back 回豆包任务栈，仅 stuck 时 force-stop 抖音。
        """
        try:
            cur = self.d.app_current() or {}
        except Exception:
            return False
        pkg = cur.get("package", "")
        act = cur.get("activity", "")
        if self.is_app_jump_prompt():
            print("  [导航] 关闭未处理的 App 跳转弹窗")
            if not self.dismiss_app_jump_prompt():
                self.d.press("back")
                time.sleep(0.4)
        if "aweme" not in pkg and "ss.android" not in act.lower():
            if PACKAGE in pkg and self.is_chat():
                return True
            return False
        print("  [导航] 从抖音返回豆包")
        for i in range(4):
            self.d.press("back")
            time.sleep(0.45)
            if self.is_chat():
                print(f"  [导航] back 回豆包（{i + 1} 次）")
                return True
        if gentle and self.is_chat():
            return True
        try:
            self.d.shell("am force-stop com.ss.android.ugc.aweme")
        except Exception:
            pass
        time.sleep(0.4)
        if self.is_chat():
            return True
        try:
            self.d.app_start(PACKAGE)
        except Exception:
            pass
        time.sleep(1.5)
        return self.is_chat()

    _CONVERSATION_SEARCH_RIDS: tuple[str, ...] = (
        "com.larus.nova:id/search_input",
        "com.larus.nova:id/search_input_root",
        "com.larus.nova:id/search_cancel",
        "com.larus.nova:id/search_edit",
        "com.larus.nova:id/layout_search",
    )

    def _conversation_search_open(self) -> bool:
        """会话搜索态：抽屉内 search_input 或 CombineSearchActivity 全屏搜索。"""
        try:
            act = str((self.d.app_current() or {}).get("activity") or "")
            if "CombineSearchActivity" in act:
                return True
        except Exception:
            pass
        for rid in self._CONVERSATION_SEARCH_RIDS:
            try:
                if self.d.xpath(f'//*[@resource-id="{rid}"]').get(timeout=0.25):
                    return True
            except Exception:
                continue
        return False

    def dismiss_conversation_search(self) -> bool:
        """退出会话搜索页（抽屉搜索 / CombineSearchActivity），回到列表或聊天。"""
        if not self._conversation_search_open():
            return False
        print("  [导航] 关闭会话搜索页")
        for sel in (
            '//*[@content-desc="对话列表"]',
            '//*[@resource-id="com.larus.nova:id/back_icon" and @content-desc="对话列表"]',
            '//*[@resource-id="com.larus.nova:id/search_cancel"]',
            '//*[@resource-id="com.larus.nova:id/cancel_modify_area"]',
            '//*[@text="取消"]',
        ):
            try:
                el = self.d.xpath(sel).get(timeout=0.5)
                if el:
                    el.click()
                    time.sleep(0.8)
                    if not self._conversation_search_open():
                        return True
            except Exception:
                continue
        self.d.press("back")
        time.sleep(0.8)
        return not self._conversation_search_open()

    def _open_conversation_drawer(self) -> bool:
        """打开左侧会话抽屉（conversation_list）。"""
        self.dismiss_conversation_search()
        if self._conversation_drawer_open():
            return True
        for sel in (
            '//*[@content-desc="对话列表"]',
            '//*[contains(@content-desc,"对话列表")]',
            '//*[@resource-id="com.larus.nova:id/back_icon"]',
        ):
            try:
                el = self.d.xpath(sel).get(timeout=0.8)
                if el:
                    el.click()
                    time.sleep(1.0)
                    if self._conversation_drawer_open():
                        return True
            except Exception:
                continue
        # 坐标兜底：顶部左侧图标
        try:
            self.d.click(0.06, 0.086)
            time.sleep(1.0)
        except Exception:
            pass
        return self._conversation_drawer_open()

    def _conversation_drawer_open(self) -> bool:
        for rid in (
            "com.larus.nova:id/conversation_list",
            "com.larus.nova:id/conversation_list_container",
        ):
            try:
                if self.d.xpath(f'//*[@resource-id="{rid}"]').get(timeout=0.4):
                    return True
            except Exception:
                continue
        return False

    def reenter_chat_by_text(self, needles: list[str]) -> bool:
        """
        会话抽屉里按「回答正文/标题片段」重进目标会话。

        needles 按可辨识度从高到低给（如回答首句 > prompt 核心词）；
        命中 conversation_list 内含该文本的条目即点入，比标题摘要匹配更稳。
        """
        cleaned = [n.strip() for n in needles if n and len(n.strip()) >= 6]
        if not cleaned:
            return False
        self.dismiss_conversation_search()
        if not self._open_conversation_drawer():
            print("  [导航] 未能打开会话抽屉")
            return False
        list_root = '//*[@resource-id="com.larus.nova:id/conversation_list"]'
        for scroll in range(4):
            for needle in cleaned:
                safe = needle.replace('"', "")[:24]
                xp = f'{list_root}//*[contains(@text,"{safe}")]'
                try:
                    el = self.d.xpath(xp).get(timeout=0.8)
                    if el:
                        el.click()
                        time.sleep(1.2)
                        if self.is_chat():
                            print(f"  [导航] 已按正文片段重进会话: {safe!r}")
                            return True
                except Exception:
                    continue
            try:
                self.d.swipe(0.3, 0.7, 0.3, 0.35, 0.3)
                time.sleep(0.6)
            except Exception:
                break
        return self.is_chat()

    def reenter_chat_by_prompt(self, prompt: str, answer_snippet: str = "") -> bool:
        """
        重进目标会话：优先用回答正文片段（唯一性强），回落 prompt 核心词。
        """
        needles: list[str] = []
        ans = (answer_snippet or "").strip()
        if ans:
            # 回答首句/首段前若干字，去掉 markdown 干扰
            head = ans.lstrip("*# \n").split("\n", 1)[0]
            for n in (head[:20], head[:12]):
                if n and n not in needles:
                    needles.append(n)
        core = (prompt or "").strip()
        for suffix in ("值得买吗？", "值得买吗", "是多少？", "怎么样", "？"):
            if core.endswith(suffix):
                core = core[: -len(suffix)]
                break
        if len(core) >= 4 and core not in needles:
            needles.append(core)
        if needles:
            print(f"  [导航] 尝试重进会话 needles={needles!r}")
            if self.reenter_chat_by_text(needles):
                return True
        return self.is_chat()

    def hard_restart_app(self, *, reason: str = "") -> None:
        """force-stop 豆包后冷启动，清掉 WebActivity 等残留页面栈。"""
        tag = f"（{reason}）" if reason else ""
        print(f"  [导航] 强杀并重启豆包{tag}")
        try:
            self.d.app_stop(PACKAGE)
        except Exception as exc:
            print(f"  [导航] app_stop 失败: {exc}，尝试 am force-stop")
            try:
                self.d.shell(f"am force-stop {PACKAGE}")
            except Exception as exc2:
                print(f"  [导航] force-stop 失败: {exc2}")
        time.sleep(0.6)
        try:
            self.d.app_start(PACKAGE)
        except Exception as exc:
            print(f"  [导航] app_start 失败: {exc}")
        time.sleep(2.0)

    def lite_back_to_chat(self) -> bool:
        """引用 URL 解析用：单次 back + 一次校验，失败再最多补 1 次。"""
        p0, cur0 = self.current_page()
        act0 = (cur0.get("activity") or "").rsplit(".", 1)[-1]
        print(f"  [导航] lite_back 前: {p0.name} act={act0}")
        self.d.press("back")
        time.sleep(0.35)
        p, cur = self.current_page()
        act = (cur.get("activity") or "").rsplit(".", 1)[-1]
        if p == Page.CHAT:
            print(f"  [导航] lite_back 后: {p.name} act={act}")
            return True
        if p == Page.SHARE_OVERLAY:
            print(f"  [导航] lite_back 关闭分享面板")
            self.d.press("back")
            time.sleep(0.35)
            p, cur = self.current_page()
            act = (cur.get("activity") or "").rsplit(".", 1)[-1]
            print(f"  [导航] lite_back 后: {p.name} act={act}")
            return p == Page.CHAT
        if p == Page.OTHER_APP:
            print(f"  [导航] lite_back 落在外部 App({act})，重启豆包")
            self.d.app_start(PACKAGE)
            time.sleep(1.0)
            p, cur = self.current_page()
            act = (cur.get("activity") or "").rsplit(".", 1)[-1]
            print(f"  [导航] lite_back 后: {p.name} act={act}")
            return p == Page.CHAT
        print(f"  [导航] lite_back 补按 back（当前 {p.name} act={act}）")
        self.d.press("back")
        time.sleep(0.35)
        p, cur = self.current_page()
        act = (cur.get("activity") or "").rsplit(".", 1)[-1]
        print(f"  [导航] lite_back 后: {p.name} act={act}")
        return p == Page.CHAT

    def safe_back_to_chat(self, max_backs: int = 6) -> bool:
        """从任意页面安全返回 ChatActivity，自动处理分享面板等覆盖层。"""
        for i in range(max_backs):
            if self.dismiss_conversation_search():
                continue
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
                if self.dismiss_app_jump_prompt():
                    time.sleep(0.4)
                    p, _ = self.current_page()
                    if p == Page.CHAT:
                        print(f"  [导航] 已回到聊天页（关闭跳转弹窗）")
                        return True
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

    def web_detail_scroll_end_hints_visible(self) -> bool:
        """
        商品详情 H5 常见「已滑到底」类文案（子串匹配，用于辅助结束滚动）。
        不同店铺文案差异大，未命中时仍依赖截图稳定判定。
        """
        hints = (
            "没有更多",
            "没有更多了",
            "已经到底",
            "已到底",
            "到底了",
            "没有更多了哦",
            "没有相关",
            "暂无更多",
            "亲，没有",
            "看完了",
        )
        try:
            for n in self.d.xpath("//android.widget.TextView|//android.view.View").all():
                try:
                    txt = (n.info.get("text") or "").strip()
                    if not txt:
                        continue
                    if any(h in txt for h in hints):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False
