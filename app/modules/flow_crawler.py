# -*- coding: utf-8 -*-
"""
基于真实操作流程的完整爬虫（与 flow_recorder 观测一致）。

端到端步骤（与 `run()` 一致）：
  1. 启动应用；若落在 Applet/Web/分享层，则 back 回聊天。
  2. 登录：`handle_login_if_needed` — 已登录则跳过；否则人工等待或 `login_via_api`。
  3. 发送提示词 → `wait_reply_done`（停止按钮消失 + 正文稳定）。
  4. 回到底部 → `copy_reply`（优先点 `msg_action_copy`，否则取长文本候选）。
  5. 嵌入式商品卡片：`_scroll_to_reply_top` 后向下轻扫，用「可见性边沿」状态机
     发现卡片（见 `run` 内注释）；每张卡片点击进入 Applet 列表。
  6. 列表内 `_crawl_list_page`：按标题去重，最多 `max_products_per_card` 个详情，
     每个详情 `_capture_detail` 按 ROI 触底判定滚动截图后 back 回列表；列表结束后 `safe_back_to_chat`。

产出目录（`output_dir` 默认 `logs`）：
  crawl_<时间戳>/
    reply.txt                    # 复制的回复全文（若有）
    summary.json                 # 汇总：卡片数、各卡片标题、截图路径等
    <卡片标题或 card_N>/         # Applet 顶栏关键词或占位名
      <商品标题>/                # 单个详情的多张 detail_XX.png

嵌入式卡片的识别是启发式的（无 rid 的空白 FrameLayout），不同机型/版本可能需要调阈值。
"""

from __future__ import annotations

import json as _json
import os
import re
import time
from datetime import datetime
from typing import Any, Optional

from app.config.gesture_profile import GestureProfile
from app.modules.navigator import Navigator, Page
from app.modules.web_detail_capture import capture_web_detail_screenshots
from app.modules.sms_login import auto_login
from app.modules.chat_ui_heuristics import (
    collect_reply_text_candidates,
    content_bottom_y,
    content_top_y,
    display_wh,
    try_click_copy_button,
)


def _safe_dirname(name: str, max_len: int = 60) -> str:
    """把文案转成合法且简短的目录名。"""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', '_', name.strip())
    s = re.sub(r'_+', '_', s).strip('_')
    return s[:max_len] or "unnamed"


class FlowCrawler:
    """基于真实导航结构的全流程爬虫。"""

    PACKAGE = "com.larus.nova"

    def __init__(self, device: Any, output_dir: str = "logs", profile: GestureProfile | None = None):
        self.d = device
        self.nav = Navigator(device)
        self.output_dir = output_dir
        self.p = profile or GestureProfile()
        os.makedirs(output_dir, exist_ok=True)

    # ==================== 启动与登录 ====================

    def _device_serial(self) -> str | None:
        serial = getattr(self.d, "serial", None)
        if serial:
            return str(serial)
        try:
            info = self.d.info or {}
            return info.get("serial") or info.get("udid")
        except Exception:
            return None

    def _recover_external_app(self) -> None:
        """从抖音 ANR/外部 App 拉回豆包。"""
        print("  [启动] 外部 App/ANR，尝试 back + 结束抖音 + 重启豆包")
        for _ in range(3):
            try:
                self.d.press("back")
            except Exception:
                pass
            time.sleep(0.4)
        try:
            self.d.shell("am force-stop com.ss.android.ugc.aweme")
        except Exception as exc:
            print(f"  [启动] force-stop 抖音失败: {exc}")
        time.sleep(0.5)
        try:
            self.d.app_start(self.PACKAGE)
        except Exception:
            pass
        time.sleep(2.0)

    def start_app(self, max_wait: int = 20) -> bool:
        print("[启动] 打开豆包...")
        try:
            cur = self.d.app_current() or {}
        except Exception:
            cur = {}
        pkg = str(cur.get("package", ""))
        if self.PACKAGE not in pkg:
            if "aweme" in pkg or "ss.android" in pkg:
                self._recover_external_app()
            else:
                self.d.app_start(self.PACKAGE)
                time.sleep(1.5)
        else:
            p0, _ = self.nav.current_page()
            if p0 in (Page.WEB_DETAIL, Page.APPLET_LIST, Page.OTHER_APP, Page.SHARE_OVERLAY):
                print(f"  [启动] 上次停留在 {p0.name}，先尝试回到聊天页")
                if p0 == Page.OTHER_APP:
                    self._recover_external_app()
                else:
                    self.nav.safe_back_to_chat(max_backs=8)
                    if not self.nav.is_chat():
                        self.d.app_start(self.PACKAGE)
                        time.sleep(2.0)

        web_stuck = 0
        for i in range(max_wait):
            time.sleep(1)
            p, cur = self.nav.current_page()
            act = cur.get("activity", "")
            print(f"  [{i+1}s] {act} -> {p.name}")
            if p in (Page.CHAT, Page.LOGIN):
                return True
            if p == Page.HOME:
                print("  [启动] 已在豆包首页壳，视为就绪")
                return True
            if p == Page.OTHER_APP:
                web_stuck = 0
                self._recover_external_app()
                continue
            if p in (Page.APPLET_LIST, Page.WEB_DETAIL, Page.SHARE_OVERLAY, Page.UNKNOWN):
                self.nav.safe_back_to_chat(max_backs=4)
                if self.nav.is_chat():
                    return True
                if p == Page.WEB_DETAIL:
                    web_stuck += 1
                    if web_stuck >= 3:
                        print("  [启动] WebActivity 仍卡住，重启豆包")
                        self.d.app_start(self.PACKAGE)
                        time.sleep(2.0)
                        web_stuck = 0
                continue
        return False

    def login_via_api(self, phone: str, code: str) -> bool:
        """通过测试手机号+验证码自动登录（预留 API 接口）。"""
        if not self.nav.is_login():
            return True
        try:
            btn = self.d.xpath('//*[@resource-id="com.larus.nova:id/button_login" and contains(@text,"手机号")]').get(timeout=3)
            if btn:
                btn.click()
                time.sleep(1.5)
        except Exception:
            pass
        try:
            phone_el = self.d.xpath('//*[@resource-id="com.larus.nova:id/phone_number"]').get(timeout=3)
            if phone_el:
                phone_el.click()
                time.sleep(0.3)
                self.d.send_keys(phone)
                time.sleep(0.5)
            next_btn = self.d.xpath('//*[@resource-id="com.larus.nova:id/button_login"]').get(timeout=2)
            if next_btn:
                next_btn.click()
                time.sleep(2)
        except Exception:
            pass
        try:
            code_el = self.d.xpath('//*[@resource-id="com.larus.nova:id/edit_solid"]').get(timeout=3)
            if code_el:
                code_el.click()
                time.sleep(0.3)
                self.d.send_keys(code)
                time.sleep(3)
        except Exception:
            pass
        return self.nav.wait_for_page(Page.CHAT, timeout=15)

    def handle_login_if_needed(
        self,
        phone: str = "",
        code: str = "",
        sms_token: str = "",
        device_id: str = "",
    ) -> bool:
        if not self.nav.is_login():
            return True
        # 优先：已提供手机号+验证码（手动调试用）
        if phone and code:
            print(f"[登录] 使用预设手机号+验证码登录: phone={phone[:3]}***")
            return self.login_via_api(phone, code)
        # 自动：通过 SMS API 获取手机号和验证码
        token = sms_token or os.environ.get("SMS_API_TOKEN", "")
        if token:
            print("[登录] 检测到登录页，使用 SMS API 自动登录...")
            dev_id = device_id or os.environ.get("SMS_DEVICE_ID", "doubao_spider")
            return auto_login(self.d, self.nav, token=token, device_id=dev_id)
        # 兜底：等待人工登录
        print("[登录] 检测到登录页，未配置 SMS_API_TOKEN，请在手机上手动完成登录...")
        for i in range(120):
            time.sleep(2)
            if self.nav.is_chat():
                print(f"[登录] 登录成功（等待 {i*2}s）")
                return True
            if not self.nav.is_login():
                time.sleep(3)
                if self.nav.is_chat():
                    return True
        print("[登录] 等待超时")
        return False

    # ==================== 聊天 ====================

    def _ensure_chat(self) -> bool:
        p, _ = self.nav.current_page()
        if p == Page.CHAT:
            return True
        if p == Page.OTHER_APP:
            self._recover_external_app()
            return self.nav.is_chat()
        if p == Page.SHARE_OVERLAY:
            self.nav.dismiss_overlay()
        if p in (Page.WEB_DETAIL, Page.APPLET_LIST):
            self.nav.safe_back_to_chat(max_backs=8)
            if self.nav.is_chat():
                return True
            print("  [导航] 仍未回聊天页，重启豆包")
            self.d.app_start(self.PACKAGE)
            time.sleep(2.0)
            return self.nav.is_chat()
        return self.nav.safe_back_to_chat(max_backs=8)

    def send_message(self, text: str) -> bool:
        if not self._ensure_chat():
            return False
        for sel in ['//*[@resource-id="com.larus.nova:id/input_text"]', '//*[contains(@class,"EditText")]']:
            try:
                el = self.d.xpath(sel).get(timeout=2)
                if el:
                    el.click()
                    time.sleep(0.3)
                    self.d.send_keys(text)
                    time.sleep(0.3)
                    break
            except Exception:
                continue
        else:
            print("[发送] 未找到输入框")
            return False
        for sel in ['//*[@resource-id="com.larus.nova:id/action_send"]', '//*[@contentDescription="发送"]']:
            try:
                btn = self.d.xpath(sel).get(timeout=1.5)
                if btn:
                    btn.click()
                    print(f"[发送] 已发送: {text[:40]}")
                    return True
            except Exception:
                continue
        print("[发送] 未找到发送按钮")
        return False

    def wait_reply_done(self, timeout: int = 120) -> bool:
        """生成中会出现「停止」类控件；消失后正文连续若干次不变即认为完成。"""
        print("[等待] 等待 AI 回复完成...")
        start = time.time()
        stable, last_text = 0, ""
        while time.time() - start < timeout:
            gen = False
            for sel in ('//*[@text="停止"]', '//*[contains(@text,"停止")]'):
                try:
                    if self.d.xpath(sel).get(timeout=0.4):
                        gen = True
                        break
                except Exception:
                    pass
            if gen:
                stable = 0
                time.sleep(2)
                continue
            cands = collect_reply_text_candidates(self.d, min_len=30, profile=self.p)
            cur = cands[0][0] if cands else ""
            if cur and cur == last_text:
                stable += 1
                if stable >= 3:
                    print("[等待] 回复已完成")
                    return True
            else:
                stable = 0
                last_text = cur
            time.sleep(2)
        print("[等待] 超时")
        return False

    def copy_reply(self) -> str:
        """优先系统复制按钮，避免把短 follow-up 建议当成主回复。"""
        before = self._clipboard()
        if try_click_copy_button(self.d, profile=self.p):
            time.sleep(0.5)
            after = self._clipboard()
            if after and after != before and len(after) >= 10:
                print(f"[复制] 成功，长度={len(after)}")
                return after
        cands = collect_reply_text_candidates(self.d, min_len=20, profile=self.p)
        if cands:
            longest = max(cands, key=lambda x: len(x[0]))
            if len(longest[0]) >= 40:
                print(f"[复制] 兜底取最长文本，长度={len(longest[0])}")
                return longest[0]
        return ""

    def _clipboard(self) -> str:
        try:
            return (self.d.clipboard or "").strip()
        except Exception:
            return ""

    def _scroll_to_bottom(self):
        """先点 `fast_button_icon`，失败则向上滑列表，保证看到最新消息区。"""
        try:
            btn = self.d.xpath('//*[@resource-id="com.larus.nova:id/fast_button_icon"]').get(timeout=0.8)
            if btn:
                btn.click()
                time.sleep(1)
                print("  [滚动] 点击了「回到底部」按钮")
                return
        except Exception:
            pass
        w, h = display_wh(self.d, profile=self.p)
        for _ in range(4):
            self.d.swipe(
                int(w * 0.5), int(h * self.p.fc_scroll_down_start_y),
                int(w * 0.5), int(h * self.p.fc_scroll_down_end_y),
                self.p.fc_scroll_down_duration,
            )
            time.sleep(0.5)

    # ==================== 嵌入式商品卡片 ====================

    def find_embedded_product_cards(self) -> list[dict[str, Any]]:
        """当前视区内、聊天内容区内的嵌入式商品卡占位（多为无 rid 的空白 FrameLayout）。

        过滤要点：落在 title~splitter 之间；宽度约半屏以上；高度在约 12%~25% 屏高；
        排除与任意带 resource-id 的节点同 bounds 的格子（避免点到消息装饰/容器）；
        大块套小块时只保留外层（按面积排序后做包含关系去重）。
        """
        w, h = display_wh(self.d, profile=self.p)
        bot = content_bottom_y(self.d, h, profile=self.p)
        top = content_top_y(self.d, h, profile=self.p)
        min_h = int(h * self.p.fc_card_min_h_ratio)

        # 所有带 rid 的节点外接矩形：卡片占位不应与这些 bounds 完全重合
        known: set[tuple[int, int, int, int]] = set()
        try:
            for n in self.d.xpath('//*[string-length(@resource-id)>0]').all():
                try:
                    b = n.bounds
                    if b and len(b) >= 4:
                        known.add((int(b[0]), int(b[1]), int(b[2]), int(b[3])))
                except Exception:
                    pass
        except Exception:
            pass

        cards = []
        try:
            for n in self.d.xpath("//android.widget.FrameLayout").all():
                inf = n.info
                if (inf.get("resourceName") or "").strip():
                    continue
                if (inf.get("text") or "").strip() or (inf.get("contentDescription") or "").strip():
                    continue
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                bw, bh = x2 - x1, y2 - y1
                if y1 < top or y2 > bot:
                    continue
                if bw < w * self.p.fc_card_min_w_ratio or bh < min_h or bh > int(h * self.p.fc_card_max_h_ratio):
                    continue
                bt = (x1, y1, x2, y2)
                if bt in known:
                    continue
                cards.append({"bounds": bt, "size": (bw, bh)})
        except Exception:
            pass
        # 同一卡片可能有多层 FrameLayout，只保留不被其它候选完全包含的那层
        dedup = []
        for c in sorted(cards, key=lambda x: x["size"][0] * x["size"][1], reverse=True):
            cb = c["bounds"]
            if not any(cb[0] >= d["bounds"][0] and cb[1] >= d["bounds"][1]
                       and cb[2] <= d["bounds"][2] and cb[3] <= d["bounds"][3] for d in dedup):
                dedup.append(c)
        return dedup

    # ==================== AppletActivity 商品列表 ====================

    def _get_applet_title(self) -> str:
        """用 WebView 内靠上的 `android.view.View` 文本作列表页主题，用于日志目录命名。"""
        try:
            for n in self.d.xpath("//android.view.View").all():
                inf = n.info
                text = (inf.get("text") or "").strip()
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                y1 = int(b[1])
                if y1 < self.p.fc_title_pixel_min_y and text and 3 < len(text) < 60:
                    return text
        except Exception:
            pass
        return ""

    def _collect_applet_items(self) -> list[dict[str, Any]]:
        """收集 AppletActivity 商品列表项，按标题去重。"""
        items: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        try:
            all_nodes = self.d.xpath('//*').all()
            w, h = display_wh(self.d, profile=self.p)
            # 先收集所有 View 文本节点（商品标题、价格、店铺等）
            text_nodes: list[dict] = []
            for n in all_nodes:
                inf = n.info
                cls = (inf.get("className") or "")
                text = (inf.get("text") or "").strip()
                if not text or len(text) < 5:
                    continue
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                bw = x2 - x1
                # 商品标题特征：View 节点，宽度 > 40% 屏宽，包含【】或较长描述
                if "View" in cls and bw > w * self.p.fc_title_min_w_ratio and len(text) > 10:
                    text_nodes.append({"text": text, "y1": y1, "bounds": (x1, y1, x2, y2)})

            # 收集可点击的 Image（商品图片，是实际的点击入口）
            for n in all_nodes:
                inf = n.info
                cls = (inf.get("className") or "")
                if "Image" not in cls or not bool(inf.get("clickable")):
                    continue
                b = n.bounds
                if not b or len(b) < 4:
                    continue
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                bw, bh = x2 - x1, y2 - y1
                if bh < int(h * self.p.fc_title_min_h_ratio) or y2 < int(h * self.p.fc_title_min_y_ratio):
                    continue

                # 找与此图片关联的标题（y 坐标在图片下方 ±max_dy px 内的最近 View 文本）
                title = ""
                for tn in text_nodes:
                    dy = tn["y1"] - y2
                    if 0 <= dy < self.p.fc_title_image_max_dy:
                        title = tn["text"]
                        break

                if not title:
                    title = (inf.get("text") or "")[:60]

                # 用标题去重
                title_key = re.sub(r'\s+', '', title)[:40]
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                items.append({
                    "title": title[:80],
                    "bounds": (x1, y1, x2, y2),
                    "image_text": (inf.get("text") or "")[:60],
                })
        except Exception:
            pass
        print(f"[列表] 发现 {len(items)} 个商品（已去重）")
        for i, it in enumerate(items):
            print(f"  {i+1}. {it['title'][:50]}")
        return items

    def _crawl_list_page(self, card_dir: str, max_products: int, result: dict) -> int:
        """在 AppletActivity 内遍历商品列表，逐个进详情截图，不退出到聊天。"""
        time.sleep(3)
        items = self._collect_applet_items()
        if not items:
            time.sleep(3)
            items = self._collect_applet_items()

        captured = 0
        visited_titles: set[str] = set()

        for pi, item in enumerate(items):
            if captured >= max_products:
                break
            title_key = re.sub(r'\s+', '', item["title"])[:40]
            if title_key in visited_titles:
                print(f"  [跳过] 已访问: {item['title'][:30]}")
                continue
            visited_titles.add(title_key)

            print(f"\n  [商品 {pi+1}] {item['title'][:50]}")
            b = item["bounds"]
            cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
            self.d.click(cx, cy)
            time.sleep(2.5)

            pg, _ = self.nav.current_page()
            if pg == Page.SHARE_OVERLAY:
                self.nav.dismiss_overlay()
                pg, _ = self.nav.current_page()

            if pg == Page.WEB_DETAIL:
                product_dir_name = _safe_dirname(item["title"][:40]) or f"product_{pi+1}"
                product_dir = os.path.join(card_dir, product_dir_name)
                detail = self._capture_detail(product_dir, captured + 1)
                result["details"].append(detail)
                if detail["ok"]:
                    captured += 1
                    result["products_captured"] += 1
                # back 回列表
                self.d.press("back")
                time.sleep(1.5)
                pg, _ = self.nav.current_page()
                if pg == Page.SHARE_OVERLAY:
                    self.nav.dismiss_overlay()
                    pg, _ = self.nav.current_page()
                if pg != Page.APPLET_LIST:
                    print(f"  [商品] 返回后不在列表页（{pg.name}），终止")
                    break
            else:
                print(f"  [商品] 未进入详情（{pg.name}）")
                if pg != Page.APPLET_LIST:
                    self.d.press("back")
                    time.sleep(1)
                    if not self.nav.is_applet_list():
                        break

        return captured

    # ==================== 商品详情截图 ====================

    def _extract_visible_texts(self) -> list[str]:
        """提取当前屏幕上所有 TextView / View 的文字内容。"""
        texts: list[str] = []
        try:
            for n in self.d.xpath("//android.widget.TextView|//android.view.View").all():
                try:
                    txt = (n.info.get("text") or "").strip()
                    if txt and len(txt) > 1:
                        texts.append(txt)
                except Exception:
                    continue
        except Exception:
            pass
        return texts

    def _capture_detail(self, detail_dir: str, index: int) -> dict[str, Any]:
        result: dict[str, Any] = {
            "index": index,
            "ok": False,
            "screenshots": [],
            "texts": [],
            "dir": detail_dir,
            "detail_scroll_stop": "",
        }
        if not self.nav.wait_web_detail_loaded(timeout=15):
            print(f"  [详情 {index}] 页面未加载完毕")
            return result
        time.sleep(1.5)
        os.makedirs(detail_dir, exist_ok=True)
        all_texts: list[str] = []
        seen_texts: set[str] = set()

        def _after_shot(_path: str) -> None:
            for txt in self._extract_visible_texts():
                if txt not in seen_texts:
                    seen_texts.add(txt)
                    all_texts.append(txt)

        paths, stopped = capture_web_detail_screenshots(
            self.d,
            self.nav,
            self.p,
            detail_dir,
            on_after_screenshot=_after_shot,
        )
        result["detail_scroll_stop"] = stopped
        if not paths:
            print(f"  [详情 {index}] 详情截图失败")
        else:
            for i, _p in enumerate(paths):
                print(f"  [详情 {index}] 截图 {i + 1}")
            print(f"  [详情 {index}] 滚动结束: {stopped}，合计 {len(paths)} 张")

        if all_texts:
            text_path = os.path.join(detail_dir, "detail_text.txt")
            try:
                with open(text_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(all_texts) + "\n")
                print(f"  [详情 {index}] 提取 {len(all_texts)} 条文本")
            except Exception:
                pass

        result["ok"] = len(paths) > 0
        result["screenshots"] = paths
        result["texts"] = all_texts
        return result

    # ==================== 主流程 ====================

    @staticmethod
    def _bounds_near(a: tuple[int, int, int, int], b: tuple[int, int, int, int], tol: int = 40) -> bool:
        """两组 bounds 是否在容差范围内（同一张卡片滑动后 y 可能偏移几十像素）。"""
        return all(abs(a[i] - b[i]) <= tol for i in range(4))

    def _is_clicked(self, bounds: tuple[int, int, int, int], clicked: set[tuple[int, int, int, int]]) -> bool:
        return any(self._bounds_near(bounds, cb) for cb in clicked)

    def run(
        self,
        prompt: str = "请推荐2026年最好用的旗舰手机",
        skip_send: bool = False,
        max_products_per_card: int = 5,
        max_cards: int = 10,
        sms_token: str = "",
        sms_device_id: str = "",
    ) -> dict[str, Any]:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = os.path.join(self.output_dir, f"crawl_{ts}")
        os.makedirs(session_dir, exist_ok=True)

        result: dict[str, Any] = {
            "prompt": prompt,
            "session_dir": session_dir,
            "reply_text": "",
            "embedded_cards_count": 0,
            "products_captured": 0,
            "cards": [],
            "details": [],
        }

        # 1. 启动
        if not self.start_app():
            print("[流程] 启动失败")
            return result

        # 2. 登录
        if not self.handle_login_if_needed(sms_token=sms_token, device_id=sms_device_id):
            print("[流程] 登录失败")
            return result

        # 3. 发送
        if not skip_send:
            if not self.send_message(prompt):
                return result
            if not self.wait_reply_done(timeout=120):
                print("[流程] 等待回复超时")

        # 4. 复制回复
        time.sleep(1)
        self._scroll_to_bottom()
        time.sleep(0.5)
        self._ensure_chat()
        reply = self.copy_reply()
        result["reply_text"] = reply
        if reply:
            path = os.path.join(session_dir, "reply.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(reply)
            print(f"[流程] 回复已保存: {path}（长度={len(reply)}）")

        # 5. 从回复顶部向下扫描，边发现卡片边处理（避免先「一次性收集」再点：
        #    多张卡尺寸相近时按面积去重会误合并；边扫边点用「刚进入视区」边沿计数更稳）
        self._ensure_chat()
        self._scroll_to_reply_top()
        time.sleep(0.5)

        card_index = 0
        clicked_bounds: set[tuple[int, int, int, int]] = set()
        was_visible = False
        no_card_streak = 0
        w_scr, h_scr = display_wh(self.d, profile=self.p)

        for _ in range(40):
            if card_index >= max_cards:
                print(f"[流程] 已达最大卡片数 {max_cards}，停止扫描")
                break

            if not self.nav.is_chat():
                self._ensure_chat()

            raw_cards = self.find_embedded_product_cards()
            cards = [c for c in raw_cards if not self._is_clicked(c["bounds"], clicked_bounds)]
            is_visible = len(cards) > 0

            if is_visible and not was_visible:
                card_index += 1
                no_card_streak = 0
                c = cards[0]
                b = c["bounds"]
                clicked_bounds.add(b)
                cx, cy = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2

                print(f"\n{'='*60}")
                print(f"[流程] 卡片 {card_index}  bounds={b}")
                print(f"{'='*60}")
                self.d.click(cx, cy)
                time.sleep(3)

                pg, _ = self.nav.current_page()
                if pg == Page.SHARE_OVERLAY:
                    self.nav.dismiss_overlay()
                    pg, _ = self.nav.current_page()

                if pg == Page.APPLET_LIST:
                    # 获取页面标题作为卡片目录名
                    time.sleep(2)
                    applet_title = self._get_applet_title()
                    card_dir_name = _safe_dirname(applet_title) if applet_title else f"card_{card_index}"
                    card_dir = os.path.join(session_dir, card_dir_name)

                    card_info = {"card_index": card_index, "title": applet_title, "dir": card_dir}

                    print(f"  [卡片] 标题: {applet_title!r}")
                    captured = self._crawl_list_page(card_dir, max_products_per_card, result)
                    card_info["products_captured"] = captured
                    result["cards"].append(card_info)

                    self.nav.safe_back_to_chat()
                elif pg == Page.WEB_DETAIL:
                    card_dir = os.path.join(session_dir, f"card_{card_index}")
                    detail = self._capture_detail(os.path.join(card_dir, "direct_detail"), result["products_captured"] + 1)
                    result["details"].append(detail)
                    if detail["ok"]:
                        result["products_captured"] += 1
                    result["cards"].append({"card_index": card_index, "title": "", "products_captured": 1 if detail["ok"] else 0})
                    self.nav.safe_back_to_chat()
                elif pg == Page.CHAT:
                    print(f"  [卡片] 点击后仍在聊天页，可能未命中")
                else:
                    print(f"  [卡片] 意外页面 {pg.name}")
                    self.nav.safe_back_to_chat()

                # 处理完一张后强制 False，下一轮需再次「从不可见变可见」才计下一张
                time.sleep(1)
                was_visible = False
                continue

            if is_visible:
                was_visible = True
                no_card_streak = 0
            else:
                was_visible = False
                no_card_streak += 1
                if no_card_streak >= 3:
                    print("[流程] 连续 3 轮无新卡片，停止扫描")
                    break

            # 在聊天列表内向下浏览当前 AI 条（手指上滑 = 内容上移）
            self.d.swipe(
                int(w_scr * 0.5), int(h_scr * self.p.fc_reply_top_scroll_start_y),
                int(w_scr * 0.5), int(h_scr * self.p.fc_reply_top_scroll_end_y),
                self.p.fc_reply_top_scroll_duration,
            )
            time.sleep(0.6)

        result["embedded_cards_count"] = card_index

        # 6. 保存汇总
        summary_path = os.path.join(session_dir, "summary.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            safe = dict(result)
            safe["details"] = [{k: v for k, v in d.items() if k != "node"} for d in result["details"]]
            _json.dump(safe, f, ensure_ascii=False, indent=2)

        total_in_lists = sum(c.get("products_captured", 0) for c in result["cards"])
        print(f"\n{'='*60}")
        print(f"[完成] 产出目录: {session_dir}")
        print(f"       回复长度={len(reply)}")
        print(f"       商品卡片数={card_index}")
        print(f"       已截图商品详情={result['products_captured']}")
        print(f"{'='*60}")
        return result

    def _scroll_to_reply_top(self):
        """先到底部再向上滑：长回复里嵌入卡若在条目中上部，只在底部会扫不到。"""
        self._scroll_to_bottom()
        time.sleep(0.5)
        w, h = display_wh(self.d, profile=self.p)
        for _ in range(15):
            self.d.swipe(
                int(w * 0.5), int(h * self.p.fc_scroll_down_to_cards_start_y),
                int(w * 0.5), int(h * self.p.fc_scroll_down_to_cards_end_y),
                self.p.fc_scroll_down_to_cards_duration,
            )
            time.sleep(0.6)
            cands = collect_reply_text_candidates(self.d, min_len=30, profile=self.p)
            if cands:
                top_cands = [c for c in cands if c[2][1] < int(h * self.p.fc_card_top_visible_y)]
                if top_cands:
                    print("  [滚动] 已到达回复顶部")
                    return
        print("  [滚动] 回复顶部定位完成")
