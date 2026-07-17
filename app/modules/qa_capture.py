# -*- coding: utf-8 -*-
"""
豆包问答完整采集：问题 + 思考 + 引用 + 正文 + 分屏截图 + hierarchy 兜底。

复用 FlowCrawler 的启动/登录/发送/等待/复制，不进入商品卡/详情页。
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from PIL import Image

from app.config.gesture_profile import GestureProfile
from app.config.profile_loader import detect_device_info
from app.modules.chat_ui_heuristics import (
  collect_reply_text_candidates,
  display_wh,
  get_query_anchor_bounds,
  iter_text_view_like_nodes,
  chat_prompt_conflicts,
  norm_prompt_keys,
  read_visible_user_prompt,
  verify_chat_prompt,
)
from app.modules.detail_strip_stitch import (
  crop_fullscreen_to_detail_content,
  estimate_vertical_overlap_v2,
  stitch_qa_shot_segments,
)
from app.modules.flow_crawler import FlowCrawler
from app.modules.qa_hierarchy import (
  Citation,
  ParsedExchange,
  ParsedThinkingPanel,
  ThinkingSearchGroup,
  _is_header_only_group,
  _is_search_group_title,
  expand_thinking_xpaths,
  parse_exchange_from_hierarchy,
  parse_thinking_panel,
  render_thinking_markdown,
)
from app.modules.qa_reference_urls import ResolveMethod, resolve_thinking_reference_urls
from app.modules.qa_reference_net import resolve_urls_from_net_dump
from app.modules.web_detail_capture import metric_quiet, roi_pair_metrics
from app.utils.utils import build_session_dir, poll_until

DEEP_THINK_TOGGLE_XPATH = '//*[contains(@content-desc,"深度思考")]'
NEW_CHAT_XPATHS_STRICT = (
  '//*[@content-desc="创建新对话"]',
)
NEW_CHAT_XPATHS = (
  '//*[@content-desc="创建新对话"]',
  '//*[@resource-id="com.larus.nova:id/right_img"]',
)
BACK_TO_LIST_XPATHS = (
  '//*[@content-desc="对话列表"]',
  '//*[@resource-id="com.larus.nova:id/back_icon"]',
)
MODE_MENU_XPATH = DEEP_THINK_TOGGLE_XPATH
QA_MODE_LABELS: dict[str, str] = {"fast": "快速", "think": "思考"}
# 输入栏模式芯片可能显示的文案（点开菜单用）；选 fast 时绝不可点「专家」
QA_MODE_CHIP_TEXTS: tuple[str, ...] = ("快速", "思考", "专家")
QA_QUOTA_ANSWER_MARKERS: tuple[str, ...] = (
  "免费额度已用完",
  "专家模式额度",
  "专业版功能",
  "开通豆包专业版",
  "额度不足",
)
REFERENCE_HEADER_XPATHS = (
  '//*[@resource-id="com.larus.nova:id/ll_reference_title"]',
  '//*[@resource-id="com.larus.nova:id/tv_reference_title"]',
  '//*[contains(@text,"已完成思考")]',
)
SEARCH_REF_CONTAINER_XPATH = '//*[@resource-id="com.larus.nova:id/searchReferenceTitleContainer"]'


@dataclass
class QaRecord:
  """一次问答采集的完整产出。"""

  prompt: str
  session_dir: str
  question_text: str = ""
  thinking: str = ""
  answer_body: str = ""
  citations: list[Citation] = field(default_factory=list)
  thinking_references: list[Citation] = field(default_factory=list)
  raw_texts: list[str] = field(default_factory=list)
  raw_nodes: list[dict[str, Any]] = field(default_factory=list)
  screenshots: list[str] = field(default_factory=list)
  stitched_screenshot: str = ""
  hierarchy_xml: str = ""
  captured_at: str = ""
  device_info: dict[str, str] = field(default_factory=dict)
  mode: str = "fast"
  deep_think_enabled: bool = False

  def to_dict(self) -> dict[str, Any]:
    data = asdict(self)
    data["citations"] = [asdict(c) for c in self.citations]
    data["thinking_references"] = [asdict(c) for c in self.thinking_references]
    return data


class DoubaoQaCapture:
  """问答归档采集器（独立于电商详情流程）。"""

  def __init__(
    self,
    device: Any,
    output_dir: str = "logs",
    profile: GestureProfile | None = None,
    project_slug: str = "",
  ):
    self.d = device
    self.output_dir = output_dir
    self.project_slug = (project_slug or "").strip()
    self.p = profile or GestureProfile()
    self._crawler = FlowCrawler(device, output_dir=output_dir, profile=self.p)

  def _ensure_chat(self) -> bool:
    return self._crawler._ensure_chat()

  def _is_reference_title_text(self, text: str, ref_titles: list[str]) -> bool:
    """判断文本是否更像引用条目标题，而非助手回答正文。"""
    if not text:
      return True
    norm = "".join(text.split())[:100]
    for rt in ref_titles:
      rt_norm = "".join((rt or "").split())[:100]
      if not rt_norm:
        continue
      if norm == rt_norm or norm in rt_norm or rt_norm in norm:
        return True
    if "搜索" in text and "篇资料" in text:
      return True
    if len(text) < 200 and text.count("#") >= 3 and "推荐" in text:
      return True
    return False

  def _pick_best_answer_body(
    self,
    *candidates: str,
    prompt: str,
    ref_titles: list[str] | None = None,
  ) -> str:
    """从多路候选中选取最可信的助手回答正文。"""
    ref_titles = ref_titles or []
    prompt_norm = "".join((prompt or "").split())
    valid: list[str] = []
    for raw in candidates:
      text = (raw or "").strip()
      if len(text) < 20:
        continue
      if prompt_norm and prompt_norm in "".join(text.split()) and len(text) <= len(prompt) + 30:
        continue
      if self._is_reference_title_text(text, ref_titles):
        continue
      valid.append(text)
    if valid:
      return max(valid, key=len)
    # 兜底：取最长非空（避免完全无正文）
    nonempty = [(raw or "").strip() for raw in candidates if (raw or "").strip()]
    return max(nonempty, key=len, default="")

  def _capture_answer_body_early(self, session_dir: str, prompt: str) -> str:
    """回复完成后立即复制正文（须在展开思考/截图之前；回复完成时已在底部）。"""
    print("[问答] 采集回答正文（展开思考前）...")
    self._dismiss_overlays()
    time.sleep(0.3)

    parsed_early = ParsedExchange()
    xml_path, _ = self._dump_raw(session_dir, "answer_early")
    if xml_path and os.path.isfile(xml_path):
      w, h = display_wh(self.d, profile=self.p)
      with open(xml_path, encoding="utf-8") as f:
        parsed_early = parse_exchange_from_hierarchy(
          f.read(),
          prompt_text=prompt,
          screen_w=w,
          screen_h=h,
          profile=self.p,
        )

    clipboard = self._crawler.copy_reply()
    body = self._pick_best_answer_body(
      clipboard,
      parsed_early.answer_body,
      prompt=prompt,
      ref_titles=[],
    )
    if parsed_early.raw_texts:
      longest_raw = max(parsed_early.raw_texts, key=len)
      body = self._pick_best_answer_body(
        body,
        longest_raw,
        prompt=prompt,
        ref_titles=[],
      )
    if body:
      print(f"[问答] 早期正文: {len(body)} 字")
    return body

  def _scroll_exchange_into_view(self) -> None:
    """长回复时向上轻扫，尽量露出思考/引用区域。"""
    w, h = display_wh(self.d, profile=self.p)
    for _ in range(self.p.qa_scroll_top_rounds):
      self.d.swipe(
        int(w * 0.5),
        int(h * self.p.qa_scroll_top_start_y),
        int(w * 0.5),
        int(h * self.p.qa_scroll_top_end_y),
        self.p.qa_scroll_top_duration,
      )
      time.sleep(0.4)

  def _thinking_panel_on_screen(self) -> bool:
    """当前屏是否可见思考/搜索引用头（单次 union 查询，避免多次 hierarchy 抓取）。"""
    try:
      return bool(
        self.d.xpath(
          '//*[@resource-id="com.larus.nova:id/searchReferenceTitleContainer"'
          " or @resource-id=\"com.larus.nova:id/ll_reference_title\""
          " or @resource-id=\"com.larus.nova:id/tv_reference_title\"]"
        ).get(timeout=0.2)
      )
    except Exception:
      return False

  def _any_visible_refs_on_screen(self) -> bool:
    try:
      return bool(
        self.d.xpath('//*[@resource-id="com.larus.nova:id/ll_source_item"]').all()
      )
    except Exception:
      return False

  def _collect_panel_dump(
    self,
    session_dir: str,
    tag: str,
    panels: list[ParsedThinkingPanel],
  ) -> tuple[ParsedThinkingPanel | None, str]:
    xml_path, shot_path = self._dump_raw(session_dir, tag)
    if not xml_path or not os.path.isfile(xml_path):
      return None, shot_path
    with open(xml_path, encoding="utf-8") as f:
      panel = parse_thinking_panel(f.read())
    if panel.thinking_paragraphs or panel.references or panel.header or panel.groups:
      panels.append(panel)
      return panel, shot_path
    return None, shot_path

  def _scroll_message_to_top(self) -> None:
    """滚到当前对话顶部，便于展开思考头与截图。"""
    w, h = display_wh(self.d, profile=self.p)
    for _ in range(self.p.qa_scroll_top_rounds + 4):
      if self._thinking_panel_on_screen():
        print("[问答] 到顶时可见思考面板")
        break
      self.d.swipe(
        int(w * 0.5),
        int(h * 0.40),
        int(w * 0.5),
        int(h * 0.78),
        self.p.qa_scroll_top_duration,
      )
      time.sleep(0.35)

  def _dismiss_overlays(self) -> None:
    """关闭模式菜单、分享层、隐私弹窗等覆盖聊天区的浮层。"""
    try:
      self._crawler._dismiss_blocking_dialogs()
    except Exception:
      pass
    for sel in (
      '//*[@resource-id="com.larus.nova:id/menu_text"]',
      '//*[@resource-id="com.larus.nova:id/menu_sub_text"]',
    ):
      try:
        if self.d.xpath(sel).get(timeout=0.3):
          self.d.press("back")
          time.sleep(0.5)
          print("[问答] 已关闭模式菜单浮层")
          break
      except Exception:
        continue
    self._crawler.nav.dismiss_overlay()
    try:
      if self._crawler.nav.dismiss_push_reminder_dialog():
        print("[问答] 已关闭消息提醒弹窗")
    except Exception:
      pass
    try:
      self._crawler.nav.accept_blocking_prompts(max_rounds=2)
    except Exception:
      pass

  def _input_box_ready(self) -> bool:
    """聊天输入框是否可见（新会话就绪信号）。"""
    for sel in (
      '//*[@resource-id="com.larus.nova:id/input_text"]',
      '//*[@resource-id="com.larus.nova:id/input"]',
      '//*[@content-desc="文本输入"]',
      '//*[contains(@class,"EditText")]',
    ):
      try:
        if self.d.xpath(sel).get(timeout=0.1):
          return True
      except Exception:
        continue
    return False

  def _wait_new_conversation_ready(self) -> None:
    """等待新会话落地（输入框出现）。就绪即继续，未就绪则退回原固定等待时长。

    timeout 取原固定 sleep(2.0)，保证最坏情况不劣于改动前；settle 仅在
    提前就绪时生效，用于规避新旧页切换的竞态。
    """
    poll_until(self._input_box_ready, timeout=2.0, interval=0.15, settle=0.3)

  def _open_new_conversation(self) -> bool:
    """创建新对话，落到干净 ChatActivity。"""
    self._dismiss_overlays()
    for sel in NEW_CHAT_XPATHS_STRICT:
      try:
        el = self.d.xpath(sel).get(timeout=1.5)
        if el:
          el.click()
          self._wait_new_conversation_ready()
          print("[问答] 已点击创建新对话")
          if self._looks_like_stale_chat():
            print("[问答] 新建后仍见历史问题气泡，退回列表重试")
            break
          return True
      except Exception:
        continue

    for sel in BACK_TO_LIST_XPATHS:
      try:
        el = self.d.xpath(sel).get(timeout=1.0)
        if el:
          el.click()
          time.sleep(1.5)
          break
      except Exception:
        continue

    for sel in NEW_CHAT_XPATHS:
      try:
        el = self.d.xpath(sel).get(timeout=2.0)
        if el:
          el.click()
          self._wait_new_conversation_ready()
          print("[问答] 已从列表创建新对话")
          if self._looks_like_stale_chat():
            print("[问答] 从列表新建后仍见历史问题，放弃")
            return False
          return True
      except Exception:
        continue

    # 聊天页 overflow 菜单里的「创建新对话」
    try:
      more = self.d.xpath('//*[@content-desc="更多"]').get(timeout=0.8)
      if more:
        more.click()
        time.sleep(0.6)
      item = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/menu_text" and @text="创建新对话"]'
      ).get(timeout=1.0)
      if item:
        item.click()
        self._wait_new_conversation_ready()
        print("[问答] 已通过菜单创建新对话")
        return True
    except Exception as exc:
      print(f"[问答] 创建新对话失败: {exc}")
    return False

  def _looks_like_stale_chat(self) -> bool:
    """新建对话后若屏上已有较长用户问题，说明可能误入了历史会话。"""
    visible = read_visible_user_prompt(self.d, profile=self.p)
    return bool(visible and len(visible) >= 8)

  def _ensure_expected_chat(self, prompt: str, *, phase: str) -> bool:
    """会话校验（宽松）：仅当屏上出现另一条不同提问时判定错位。

    读不到用户气泡（问题已滚出屏幕）视为正常，不中止。
    """
    conflict, visible = chat_prompt_conflicts(self.d, prompt, profile=self.p)
    if not conflict:
      return True
    print(
      f"[问答] 会话错位({phase})：期望 {prompt[:48]!r}，"
      f"屏上 {visible[:48]!r}（疑似落入历史会话）"
    )
    return False

  def _read_current_mode_label(self) -> str:
    """读输入栏当前模式芯片文案（快速/思考/专家）。"""
    for text in QA_MODE_CHIP_TEXTS:
      try:
        el = self.d.xpath(
          f'//*[@resource-id="com.larus.nova:id/tv_item_name" and @text="{text}"]'
        ).get(timeout=0.35)
        if el:
          return text
      except Exception:
        continue
    for text in QA_MODE_CHIP_TEXTS:
      try:
        el = self.d.xpath(
          f'//*[contains(@content-desc,"{text}")]'
        ).get(timeout=0.25)
        if el:
          return text
      except Exception:
        continue
    return ""

  def _answer_looks_like_quota_block(self, text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and any(m in t for m in QA_QUOTA_ANSWER_MARKERS)

  def _select_mode(self, mode: str) -> bool:
    """按 resource-id + 精确文案切换模式（禁止坐标/模糊 content-desc 点选）。

    App 升级后菜单项位置会变，「深度思考」desc 也可能绑到专家；
    只点 menu_text=快速/思考，并读芯片校验，绝不可落在专家。
    """
    label = QA_MODE_LABELS.get(mode, QA_MODE_LABELS["fast"])
    self._dismiss_overlays()
    current = self._read_current_mode_label()
    if current == label:
      print(f"[问答] 当前已是模式: {label}")
      return True
    if mode == "fast" and current == "专家":
      print("[问答] 当前误在专家模式，强制切回快速")

    # 优先点输入栏模式芯片打开菜单（文案精确），避免点「深度思考」desc 误触专家
    opened = False
    chip_or = " or ".join(f'@text="{t}"' for t in QA_MODE_CHIP_TEXTS)
    open_selectors = (
      f'//*[@resource-id="com.larus.nova:id/tv_item_name" and ({chip_or})]',
      MODE_MENU_XPATH,
    )
    for sel in open_selectors:
      try:
        toggle = self.d.xpath(sel).get(timeout=1.5)
        if not toggle:
          continue
        toggle.click()
        poll_until(
          lambda: bool(
            self.d.xpath(
              '//*[@resource-id="com.larus.nova:id/menu_text"'
              f' and @text="{label}"]'
            ).get(timeout=0.1)
          ),
          timeout=1.0,
          interval=0.1,
          settle=0.15,
        )
        opened = bool(
          self.d.xpath(
            '//*[@resource-id="com.larus.nova:id/menu_text"'
            f' and @text="{label}"]'
          ).get(timeout=0.3)
        )
        if opened:
          break
        # 菜单未出现：可能点成了别的入口，收起后试下一个
        self._dismiss_overlays()
      except Exception:
        continue
    if not opened:
      print("[问答] 未找到模式菜单（仅按精确文案打开，不用坐标）")
      return False

    try:
      # 菜单内只点 menu_text 精确匹配；禁止点相邻「专家」
      item = self.d.xpath(
        f'//*[@resource-id="com.larus.nova:id/menu_text" and @text="{label}"]'
      ).get(timeout=1.5)
      if not item:
        print(f"[问答] 模式菜单未找到「{label}」")
        self._dismiss_overlays()
        return False
      try:
        got = (item.info.get("text") or "").strip()
      except Exception:
        got = ""
      if got != label:
        print(f"[问答] 拒绝点击：期望 {label!r}，节点文案 {got!r}")
        self._dismiss_overlays()
        return False
      # 若菜单里同时能看到专家，确认我们点的不是那一行
      expert = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/menu_text" and @text="专家"]'
      ).get(timeout=0.2)
      if expert and mode == "fast":
        try:
          eb = expert.bounds
          ib = item.bounds
          if eb and ib and abs(int(eb[1]) - int(ib[1])) < 8:
            print("[问答] 拒绝点击：快速与专家 bounds 重叠，疑似点歪")
            self._dismiss_overlays()
            return False
        except Exception:
          pass
      item.click()
      poll_until(
        lambda: not self.d.xpath(
          '//*[@resource-id="com.larus.nova:id/menu_text"'
          f' and @text="{label}"]'
        ).get(timeout=0.1),
        timeout=0.8,
        interval=0.1,
        settle=0.2,
      )
      self._dismiss_overlays()
      verified = self._read_current_mode_label()
      if verified == label:
        print(f"[问答] 已选择模式: {label}（校验通过）")
        return True
      if mode == "fast" and verified == "专家":
        print("[问答] 模式校验失败：仍在专家（疑似 App 升级后点歪）")
        return False
      if verified and verified != label:
        print(f"[问答] 模式校验失败：期望 {label}，实际 {verified}")
        return False
      print(f"[问答] 已选择模式: {label}（芯片未读到，按点击成功）")
      return True
    except Exception as exc:
      print(f"[问答] 切换模式失败: {exc}")
      self._dismiss_overlays()
      return False

  def _expand_thinking_blocks(self) -> None:
    """尝试点击消息区内的「展开思考」控件（跳过输入栏/菜单）。"""
    w, h = display_wh(self.d, profile=self.p)
    bottom_limit = int(h * self.p.content_bottom_fallback)
    for sel in expand_thinking_xpaths():
      try:
        nodes = self.d.xpath(sel).all()
      except Exception:
        continue
      for node in nodes:
        try:
          inf = node.info
          rid = (inf.get("resourceName") or "")
          if any(x in rid for x in ("menu_text", "menu_sub", "tv_item_name", "action_bar")):
            continue
          b = node.bounds
          if not b or int(b[1]) >= bottom_limit:
            continue
          txt = (inf.get("text") or "") + (inf.get("contentDescription") or "")
          if txt.strip() in ("思考", "专家", "快速"):
            continue
          node.click()
          time.sleep(0.6)
          print(f"[问答] 已点击展开思考: {txt[:30]!r}")
          return
        except Exception:
          continue

  def _read_question_from_ui(self, prompt: str) -> str:
    """从用户气泡读回问题原文。"""
    bounds = get_query_anchor_bounds(self.d, prompt, profile=self.p)
    if bounds:
      x1, y1, _, _ = bounds
      best = ""
      for n in iter_text_view_like_nodes(self.d):
        try:
          inf = n.info
          txt = (inf.get("text") or "").strip()
          b = n.bounds
          if not txt or not b:
            continue
          bx1, by1 = int(b[0]), int(b[1])
          if abs(bx1 - x1) <= 60 and abs(by1 - y1) <= 80 and len(txt) > len(best):
            best = txt
        except Exception:
          continue
      if best:
        print(f"[问答] 从 UI 读回问题，长度={len(best)}")
        return best

    w, _ = display_wh(self.d, profile=self.p)
    _, short_key = norm_prompt_keys(prompt)
    if not short_key:
      return prompt
    for n in iter_text_view_like_nodes(self.d):
      try:
        inf = n.info
        txt = (inf.get("text") or "").strip()
        b = n.bounds
        if not txt or not b or short_key not in "".join(txt.split()):
          continue
        cx = (int(b[0]) + int(b[2])) / 2
        if cx >= w * self.p.qa_user_bubble_cx_min:
          print(f"[问答] 几何匹配用户气泡问题，长度={len(txt)}")
          return txt
      except Exception:
        continue
    return prompt

  def _dump_raw(self, session_dir: str, tag: str) -> tuple[str, str]:
    """保存 hierarchy XML 与截图，返回路径。"""
    xml_path = os.path.join(session_dir, f"hierarchy_{tag}.xml")
    shot_path = os.path.join(session_dir, f"screen_{tag}.png")
    xml_text = ""
    try:
      xml_text = self.d.dump_hierarchy(compressed=False) or ""
      with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_text)
    except OSError as exc:
      print(f"[问答] 保存 hierarchy 失败: {exc}")
    try:
      self.d.screenshot(shot_path)
    except OSError as exc:
      print(f"[问答] 截图失败: {exc}")
      shot_path = ""
    return xml_path if xml_text else "", shot_path

  def _message_list_roi_frac(self) -> tuple[float, float]:
    """优先用 message_list 实际 bounds 收紧 ROI，减少顶底固定栏进帧。"""
    y0, y1 = self.p.qa_shot_roi_y0, self.p.qa_shot_roi_y1
    try:
      el = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/message_list"]'
      ).get(timeout=0.4)
      if not el:
        return y0, y1
      b = el.bounds
      if not b:
        return y0, y1
      _w, sh = display_wh(self.d, profile=self.p)
      if sh <= 0:
        return y0, y1
      list_y0 = max(0.0, min(1.0, (int(b[1]) + 4) / sh))
      list_y1 = max(0.0, min(1.0, (int(b[3]) - 4) / sh))
      merged_y0 = max(y0, list_y0)
      merged_y1 = min(y1, list_y1)
      if merged_y1 - merged_y0 >= 0.20:
        return merged_y0, merged_y1
    except Exception:
      pass
    return y0, y1

  def _qa_shot_profile(self) -> GestureProfile:
    """聊天区长截图 ROI（message_list 动态 bounds + profile 顶底比例）。"""
    from dataclasses import replace

    roi_y0, roi_y1 = self._message_list_roi_frac()
    return replace(
      self.p,
      fc_detail_roi_y0=roi_y0,
      fc_detail_roi_y1=roi_y1,
      fc_detail_strip_roi_x0=0.0,
      fc_detail_strip_roi_x1=1.0,
    )

  def _swipe_chat_down(self) -> None:
    w, h = display_wh(self.d, profile=self.p)
    self.d.swipe(
      int(w * 0.5),
      int(h * self.p.qa_shot_scroll_start_y),
      int(w * 0.5),
      int(h * self.p.qa_shot_scroll_end_y),
      self.p.qa_shot_scroll_duration,
    )

  def _qa_shot_roi_height_px(self) -> int:
    """问答长截图可见内容区高度（像素）。"""
    _w, sh = display_wh(self.d, profile=self.p)
    y0, y1 = self._message_list_roi_frac()
    return max(280, int(sh * (y1 - y0)))

  def _copy_bar_top_screen_y(self) -> int | None:
    """回答底部复制/分享操作栏顶边（屏幕坐标），不可见时 None。"""
    try:
      el = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/msg_action_copy"]'
      ).get(timeout=0.2)
      if not el:
        return None
      b = el.bounds
      if not b:
        return None
      return int(b[1])
    except Exception:
      return None

  def _crop_shot_for_stitch(
    self,
    path: str,
    profile: GestureProfile,
    copy_bar_top_y: int | None,
  ) -> Image.Image:
    """裁 ROI；若本帧含复制栏则裁到栏上方，避免拼接重复底栏。"""
    crop = crop_fullscreen_to_detail_content(path, profile)
    if copy_bar_top_y is None:
      return crop
    try:
      with Image.open(path) as im:
        _w, sh = im.size
      roi_top = max(0, min(sh, int(sh * profile.fc_detail_roi_y0)))
      trim_h = copy_bar_top_y - roi_top - 8
      if 80 < trim_h < crop.height:
        return crop.crop((0, 0, crop.width, trim_h))
    except Exception:
      pass
    return crop

  def _swipe_message_list_up(self, *, scale: float = 1.0) -> None:
    """外层 message_list 内上滑（回退），用于重叠不足时退回再小步下滑。"""
    roi_h = self._qa_shot_roi_height_px()
    max_advance = max(160, int(roi_h * self.p.qa_shot_scroll_advance_frac))
    duration = self.p.qa_shot_list_swipe_duration
    try:
      el = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/message_list"]'
      ).get(timeout=0.6)
      if el:
        b = el.bounds
        if b:
          x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
          cx = (x1 + x2) // 2
          ch = max(y2 - y1, 120)
          swipe_px = int(min(ch * self.p.qa_shot_list_swipe_frac, max_advance) * scale)
          swipe_px = max(120, min(swipe_px, ch - 16))
          y_start = y1 + int(ch * 0.32)
          y_end = y_start + swipe_px
          y_start = max(y1 + 8, min(y2 - 8, y_start))
          y_end = max(y1 + 8, min(y2 - 8, y_end))
          if y_end <= y_start:
            y_end = min(y2 - 8, y_start + swipe_px)
          self.d.swipe(cx, y_start, cx, y_end, duration)
          return
    except Exception:
      pass
    w, sh = display_wh(self.d, profile=self.p)
    cx = w // 2
    swipe_px = int(min(
      sh * (self.p.qa_shot_scroll_end_y - self.p.qa_shot_scroll_start_y),
      max_advance,
    ) * scale)
    swipe_px = max(120, swipe_px)
    y_start = int(sh * self.p.qa_shot_scroll_end_y)
    y_end = y_start + swipe_px
    self.d.swipe(cx, y_start, cx, y_end, duration)

  def _swipe_message_list_down(self, *, scale: float = 1.0) -> None:
    """
    在外层 message_list 内下滑，避免误滚嵌套引用列表。

    单次滑动距离受 ROI 高度上限约束，避免一帧滑过整段正文（漏截）。
    """
    roi_h = self._qa_shot_roi_height_px()
    max_advance = max(160, int(roi_h * self.p.qa_shot_scroll_advance_frac))
    duration = self.p.qa_shot_list_swipe_duration
    try:
      el = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/message_list"]'
      ).get(timeout=0.6)
      if el:
        b = el.bounds
        if b:
          x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
          cx = (x1 + x2) // 2
          ch = max(y2 - y1, 120)
          swipe_px = int(min(ch * self.p.qa_shot_list_swipe_frac, max_advance) * scale)
          swipe_px = max(120, min(swipe_px, ch - 16))
          y_start = y1 + int(ch * 0.76)
          y_end = y_start - swipe_px
          y_start = max(y1 + 8, min(y2 - 8, y_start))
          y_end = max(y1 + 8, min(y2 - 8, y_end))
          if y_end >= y_start:
            y_end = max(y1 + 8, y_start - swipe_px)
          self.d.swipe(cx, y_start, cx, y_end, duration)
          return
    except Exception:
      pass
    # 回退：按整屏比例滑，但仍受 ROI 上限约束
    w, sh = display_wh(self.d, profile=self.p)
    cx = w // 2
    swipe_px = int(min(
      sh * (self.p.qa_shot_scroll_start_y - self.p.qa_shot_scroll_end_y),
      max_advance,
    ) * scale)
    swipe_px = max(120, swipe_px)
    y_start = int(sh * self.p.qa_shot_scroll_start_y)
    y_end = y_start - swipe_px
    self.d.swipe(cx, y_start, cx, y_end, duration)

  def _answer_bottom_visible(self) -> bool:
    """当前屏是否已滚到回答底部（复制/分享操作栏可见）。"""
    try:
      el = self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/msg_action_copy"]'
      ).get(timeout=0.3)
      if not el:
        return False
      b = el.bounds
      if not b:
        return False
      w, h = display_wh(self.d, profile=self.p)
      cy = (int(b[1]) + int(b[3])) // 2
      roi_top = int(h * self._message_list_roi_frac()[0])
      roi_bot = int(h * self._message_list_roi_frac()[1])
      return roi_top <= cy <= roi_bot
    except Exception:
      return False

  def _capture_message_longshot(
    self,
    session_dir: str,
    *,
    shot_prefix: str = "shot",
    label: str = "回答",
    stop_at_answer_bottom: bool = True,
    stop_when_panel_gone: bool = False,
    scroll_to_top_first: bool = True,
    align_thinking_first: bool = False,
  ) -> tuple[list[str], list[int | None]]:
    """
    外层 message_list 多帧长截图；引用列表保持折叠（仅滚外层）。

    返回 (截图路径列表, 各帧复制栏顶边 y；无栏为 None)。
    """
    profile = self._qa_shot_profile()
    kept_paths: list[str] = []
    copy_bar_tops: list[int | None] = []
    tmp_path = os.path.join(session_dir, f"_{shot_prefix}_tmp.png")
    quiet_hits = 0
    panel_gone_hits = 0
    overlap_retries = 0

    if scroll_to_top_first:
      self._scroll_message_to_top()
      time.sleep(0.5)
    if align_thinking_first:
      self._scroll_to_thinking_panel(max_rounds=6)
      time.sleep(0.4)

    for round_i in range(self.p.qa_shot_max_frames):
      try:
        self.d.screenshot(tmp_path)
      except OSError as exc:
        print(f"[问答] {label}长截图失败: {exc}")
        break

      copy_top = self._copy_bar_top_screen_y()

      if kept_paths:
        try:
          prev_crop = self._crop_shot_for_stitch(
            kept_paths[-1], profile, copy_bar_tops[-1],
          )
          curr_crop = self._crop_shot_for_stitch(tmp_path, profile, copy_top)
          overlap_est = estimate_vertical_overlap_v2(prev_crop, curr_crop)
          min_overlap = int(prev_crop.height * self.p.qa_shot_min_overlap_frac)
          if overlap_est.diagnosis == "near_duplicate":
            print(
              f"[问答] {label}长截图近重复帧（重叠 {overlap_est.overlap_px}px"
              f" / {overlap_est.overlap_frac:.0%}），跳过本帧并加大滑动"
            )
            self._swipe_message_list_down(scale=1.25)
            time.sleep(self.p.qa_shot_post_swipe_sleep)
            continue
          if (
            overlap_est.diagnosis == "gap_risk"
            and overlap_est.overlap_px < min_overlap
          ):
            if overlap_retries < 2:
              overlap_retries += 1
              print(
                f"[问答] {label}长截图重叠 {overlap_est.overlap_px}px < {min_overlap}px，"
                f"回退并缩小步长重试({overlap_retries}/2)"
              )
              self._swipe_message_list_up(scale=0.5)
              time.sleep(0.32)
              self._swipe_message_list_down(scale=0.58)
              time.sleep(self.p.qa_shot_post_swipe_sleep)
              continue
            print(
              f"[问答] 警告：{label}帧间重叠 {overlap_est.overlap_px}px < {min_overlap}px，"
              "长图可能漏截正文"
            )
        except Exception as exc:
          print(f"[问答] {label}长截图重叠检测跳过: {exc}")
      overlap_retries = 0

      if kept_paths:
        fine, coarse, dham = roi_pair_metrics(kept_paths[-1], tmp_path, profile)
        quiet, reason = metric_quiet(fine, coarse, dham, profile)
        if quiet:
          quiet_hits += 1
          print(
            f"[问答] {label}长截图静止 {quiet_hits}/{self.p.qa_shot_quiet_rounds} ({reason})"
          )
          if quiet_hits >= self.p.qa_shot_quiet_rounds:
            break
          if round_i >= self.p.qa_shot_max_frames - 1:
            break
          self._swipe_message_list_down()
          time.sleep(self.p.qa_shot_post_swipe_sleep)
          continue
        quiet_hits = 0

      out = os.path.join(session_dir, f"{shot_prefix}_{len(kept_paths) + 1:02d}.png")
      try:
        shutil.copy2(tmp_path, out)
      except OSError as exc:
        print(f"[问答] 保存截图失败: {exc}")
        break
      kept_paths.append(out)
      copy_bar_tops.append(copy_top)

      if stop_at_answer_bottom and self._answer_bottom_visible():
        print(f"[问答] {label}长截图：已见回答底部操作栏，停止")
        break

      if stop_when_panel_gone:
        if not self._thinking_panel_on_screen() and not self._any_visible_refs_on_screen():
          panel_gone_hits += 1
          if panel_gone_hits >= self.p.qa_shot_quiet_rounds:
            print(f"[问答] {label}长截图：思考/引用已滚出屏，停止")
            break
        else:
          panel_gone_hits = 0

      if round_i >= self.p.qa_shot_max_frames - 1:
        break

      self._swipe_message_list_down()
      time.sleep(self.p.qa_shot_post_swipe_sleep)

    print(f"[问答] {label}长截图完成: {len(kept_paths)} 屏")
    if os.path.isfile(tmp_path):
      try:
        os.remove(tmp_path)
      except OSError:
        pass
    return kept_paths, copy_bar_tops

  def _capture_answer_longshot(self, session_dir: str) -> tuple[list[str], list[int | None]]:
    """回答正文长截图（引用折叠，滚外层 message_list）。"""
    return self._capture_message_longshot(
      session_dir,
      shot_prefix="shot",
      label="回答",
      stop_at_answer_bottom=True,
      stop_when_panel_gone=False,
      scroll_to_top_first=True,
      align_thinking_first=False,
    )

  def _capture_thinking_longshot(
    self, session_dir: str, panel: ParsedThinkingPanel,
  ) -> tuple[list[str], list[int | None]]:
    """展开后补截思考/引用区，与回答段拼接为 full.png。"""
    if not (
      panel.references or panel.groups or panel.thinking_paragraphs
    ):
      return [], []
    print("[问答] 展开态补截思考/引用长图...")
    return self._capture_message_longshot(
      session_dir,
      shot_prefix="shot_think",
      label="思考/引用",
      stop_at_answer_bottom=False,
      stop_when_panel_gone=True,
      scroll_to_top_first=False,
      align_thinking_first=True,
    )

  def _ensure_thinking_header_expanded(self) -> bool:
    """展开思考头（已展开则不再点击，避免折叠）。"""
    try:
      if self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/sub_deep_think_block_list"]'
      ).get(timeout=0.4):
        return True
    except Exception:
      pass
    try:
      if self.d.xpath(SEARCH_REF_CONTAINER_XPATH).get(timeout=0.3):
        return True
    except Exception:
      pass
    for sel in expand_thinking_xpaths():
      try:
        el = self.d.xpath(sel).get(timeout=1.0)
        if not el:
          continue
        txt = (el.info.get("text") or "") + (el.info.get("contentDescription") or "")
        if "搜索" in txt and "关键词" in txt:
          continue
        el.click()
        time.sleep(0.7)
        print(f"[问答] 已展开思考头: {txt[:40]!r}")
        return True
      except Exception:
        continue
    return False

  def _get_search_group_title(self, container: Any) -> str:
    """从搜索组容器读取 search_reference_title（勿误取已完成思考头）。"""
    try:
      cb = container.bounds
      if not cb:
        return ""
      cx1, cy1, cx2, cy2 = int(cb[0]), int(cb[1]), int(cb[2]), int(cb[3])
      best = ""
      best_dist = 9999
      for node in self.d.xpath(
        '//*[@resource-id="com.larus.nova:id/search_reference_title"]'
      ).all():
        nb = node.bounds
        if not nb:
          continue
        nx1, ny1, nx2, ny2 = int(nb[0]), int(nb[1]), int(nb[2]), int(nb[3])
        if nx1 >= cx2 or nx2 <= cx1 or ny1 >= cy2 or ny2 <= cy1:
          if abs(ny1 - cy1) > 80:
            continue
        title = (node.info.get("text") or "").strip()
        if not title or not _is_search_group_title(title):
          continue
        dist = abs(ny1 - cy1)
        if dist < best_dist:
          best_dist = dist
          best = title
      return best
    except Exception:
      pass
    return ""

  def _group_expansion_key(self, container: Any, title: str) -> str:
    try:
      b = container.bounds
      if b:
        return f"{title or 'search_group'}|{int(b[1])}"
    except Exception:
      pass
    return title or "search_group"

  def _group_has_visible_refs(self, container: Any) -> bool:
    """判断搜索组下方是否已有可见引用条目（已展开）。"""
    try:
      bounds = container.bounds
      if not bounds:
        return False
      y_top = int(bounds[1])
      y_bottom = int(bounds[3]) + 900
      for item in self.d.xpath('//*[@resource-id="com.larus.nova:id/ll_source_item"]').all():
        ib = item.bounds
        if not ib:
          continue
        if y_top <= int(ib[1]) <= y_bottom:
          return True
    except Exception:
      return False
    return False

  def _wait_for_group_refs(self, container: Any) -> bool:
    """点击搜索组后轮询，直至引用条目出现在容器下方。"""
    deadline = time.time() + self.p.qa_expand_refs_wait
    while time.time() < deadline:
      if self._group_has_visible_refs(container):
        return True
      time.sleep(self.p.qa_expand_refs_poll_interval)
    return self._group_has_visible_refs(container)

  def _has_unexpanded_search_groups(self, expanded_keys: set[str]) -> bool:
    """当前屏是否仍有未展开（无可见引用）的搜索组。"""
    try:
      containers = self.d.xpath(SEARCH_REF_CONTAINER_XPATH).all()
    except Exception:
      containers = []
    for container in containers:
      title = self._get_search_group_title(container)
      key = self._group_expansion_key(container, title)
      if key in expanded_keys and self._group_has_visible_refs(container):
        continue
      if not self._group_has_visible_refs(container):
        return True
    return False

  def _scroll_deep_think_panel(self) -> None:
    """在思考块区域内小幅下滑，露出更多搜索轮/引用条目。"""
    w, h = display_wh(self.d, profile=self.p)
    for _ in range(3):
      self.d.swipe(int(w * 0.5), int(h * 0.55), int(w * 0.5), int(h * 0.42), 0.22)
      time.sleep(0.28)

  def _scroll_to_thinking_panel(self, max_rounds: int | None = None) -> bool:
    """从当前屏向上扫，定位思考/引用面板。max_rounds 可按回答屏数自适应。"""
    w, h = display_wh(self.d, profile=self.p)
    rounds = max_rounds if max_rounds is not None else self.p.qa_scroll_top_rounds + 8
    for _ in range(max(1, rounds)):
      if self._thinking_panel_on_screen():
        print("[问答] 已定位思考面板")
        return True
      self.d.swipe(
        int(w * 0.5),
        int(h * 0.40),
        int(w * 0.5),
        int(h * 0.72),
        self.p.qa_scroll_top_duration,
      )
      time.sleep(0.3)
    return False

  def _expand_visible_search_groups(self, expanded_keys: set[str]) -> int:
    """展开当前屏可见且未展开的搜索引用组，返回本次展开数量。"""
    expanded_count = 0
    containers: list[Any] = []
    try:
      containers = self.d.xpath(SEARCH_REF_CONTAINER_XPATH).all()
    except Exception:
      containers = []

    if not containers:
      try:
        hdr = self.d.xpath(
          f'//*[@resource-id="com.larus.nova:id/tv_reference_title"]'
        ).get(timeout=0.5)
        if hdr:
          txt = (hdr.info.get("text") or "").strip()
          if "已完成思考" in txt or _is_search_group_title(txt):
            return expanded_count
      except Exception:
        pass
      try:
        if self.d.xpath(
          '//*[@resource-id="com.larus.nova:id/sub_deep_think_block_list"]'
        ).get(timeout=0.5):
          return expanded_count
      except Exception:
        pass
      try:
        bar = self.d.xpath('//*[@resource-id="com.larus.nova:id/ll_reference_title"]').get(
          timeout=0.8
        )
        if bar and not self._group_has_visible_refs(bar):
          title = self._get_search_group_title(bar) or "fast_reference_group"
          key = self._group_expansion_key(bar, title)
          if key not in expanded_keys:
            bar.click()
            time.sleep(self.p.qa_expand_group_click_sleep)
            self._wait_for_group_refs(bar)
            expanded_keys.add(key)
            expanded_count += 1
            print(f"[问答] 已展开 fast 模式引用头: {title[:40]!r}")
      except Exception:
        pass

    for container in containers:
      try:
        title = self._get_search_group_title(container)
        key = self._group_expansion_key(container, title)
        if not title:
          try:
            xml = self.d.dump_hierarchy(compressed=False) or ""
            panel = parse_thinking_panel(xml)
            for grp in panel.groups:
              gk = grp.key or grp.title
              if gk not in expanded_keys and not grp.references:
                title = grp.title
                key = gk
                break
          except Exception:
            pass
        if key in expanded_keys and self._group_has_visible_refs(container):
          continue
        if self._group_has_visible_refs(container):
          expanded_keys.add(key)
          if title:
            print(f"[问答] 搜索组已有引用可见: {title[:40]!r}")
          continue
        container.click()
        time.sleep(self.p.qa_expand_group_click_sleep)
        self._wait_for_group_refs(container)
        expanded_keys.add(key)
        expanded_count += 1
        print(f"[问答] 已展开搜索组: {title[:40]!r}")
      except Exception as exc:
        print(f"[问答] 展开搜索组失败: {exc}")
    return expanded_count

  def _is_answer_shot_path(self, path: str) -> bool:
    base = os.path.basename(path)
    return base.startswith("shot_") and not base.startswith("shot_think_")

  def _is_think_shot_path(self, path: str) -> bool:
    return os.path.basename(path).startswith("shot_think_")

  def _stitch_shot_paths(
    self,
    session_dir: str,
    kept_paths: list[str],
    profile: GestureProfile,
    tmp_path: str,
    *,
    copy_bar_tops: list[int | None] | None = None,
  ) -> str:
    """将分屏截图拼接为 full.png（仅回答段；思考/引用段另存 full_thinking.png）。"""
    stitched_path = ""
    if not kept_paths:
      return stitched_path

    answer_paths: list[str] = []
    think_paths: list[str] = []
    answer_tops: list[int | None] = []
    think_tops: list[int | None] = []
    tops = copy_bar_tops if copy_bar_tops is not None else [None] * len(kept_paths)
    if len(tops) < len(kept_paths):
      tops = tops + [None] * (len(kept_paths) - len(tops))

    for path, top in zip(kept_paths, tops):
      if self._is_think_shot_path(path):
        think_paths.append(path)
        think_tops.append(top)
      elif self._is_answer_shot_path(path):
        answer_paths.append(path)
        answer_tops.append(top)

    if not answer_paths:
      answer_paths = list(kept_paths)
      answer_tops = list(tops)
      think_paths = []
      think_tops = []

    answer_crops: list[Image.Image] = []
    think_crops: list[Image.Image] = []
    diagnoses: list = []
    try:
      for p, copy_top in zip(answer_paths, answer_tops):
        answer_crops.append(self._crop_shot_for_stitch(p, profile, copy_top))
      for p, copy_top in zip(think_paths, think_tops):
        think_crops.append(self._crop_shot_for_stitch(p, profile, copy_top))

      answer_img, think_img, diagnoses = stitch_qa_shot_segments(
        answer_crops,
        think_crops,
        answer_labels=[os.path.basename(p) for p in answer_paths],
        think_labels=[os.path.basename(p) for p in think_paths],
      )
      answer_h = answer_img.height
      think_h = think_img.height if think_img is not None else 0
      stitched_path = os.path.join(session_dir, "full.png")
      answer_img.save(stitched_path, format="PNG", optimize=True)
      if think_img is not None:
        think_path = os.path.join(session_dir, "full_thinking.png")
        think_img.save(think_path, format="PNG", optimize=True)
        think_img.close()
      answer_img.close()

      diag_path = os.path.join(session_dir, "stitch_diagnosis.json")
      with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(
          {
            "stitched_height_px": answer_h,
            "thinking_height_px": think_h,
            "frame_count": len(answer_paths) + len(think_paths),
            "answer_frame_count": len(answer_paths),
            "thinking_frame_count": len(think_paths),
            "pairs": [d.to_dict() for d in diagnoses],
          },
          f,
          ensure_ascii=False,
          indent=2,
        )
      answer_n = len(answer_paths)
      think_n = len(think_paths)
      near_dup_n = sum(1 for d in diagnoses if d.diagnosis == "near_duplicate")
      seg = f"回答{answer_n}屏"
      if think_n:
        seg += f"（思考{think_n}屏→full_thinking.png）"
      dup_note = f"，近重复拼接修正 {near_dup_n} 对" if near_dup_n else ""
      print(f"[问答] 已拼接长图: {stitched_path}（{seg}{dup_note}）")
    except Exception as exc:
      print(f"[问答] 拼接长图失败: {exc}")
    finally:
      for im in answer_crops + think_crops:
        try:
          im.close()
        except Exception:
          pass
      if os.path.isfile(tmp_path):
        try:
          os.remove(tmp_path)
        except OSError:
          pass
    return stitched_path

  def _expand_and_collect_panels(
    self, session_dir: str, shot_count: int = 0
  ) -> ParsedThinkingPanel:
    """
    数据展开采集：展开思考头/搜索组 + 引用列表内滚动 + hierarchy dump 合并。
    回答长截图已先行完成；思考/引用长截图在展开后由调用方补截。

    无联网引用（fast 模式常见）时尽早短路，避免无谓的上滚与 hierarchy dump。
    """
    panels: list[ParsedThinkingPanel] = []
    expanded_keys: set[str] = set()
    max_rounds = self.p.qa_expand_collect_max_rounds

    # 从回答底部一次性向上定位思考/引用面板；上滑轮次按回答屏数自适应，
    # 长答案保留足够上滚余量（≈原 _scroll_message_to_top + _scroll_to_thinking_panel
    # 合计上限），短答案则大幅收敛以省时。
    locate_rounds = self.p.qa_scroll_top_rounds + 16
    if shot_count > 0:
      locate_rounds = min(locate_rounds, max(8, shot_count * 4 + 6))
    located = self._scroll_to_thinking_panel(max_rounds=locate_rounds)
    if (
      not located
      and not self._thinking_panel_on_screen()
      and not self._any_visible_refs_on_screen()
    ):
      print("[问答] 无联网思考/引用面板，跳过数据展开")
      return ParsedThinkingPanel()
    if not self._ensure_thinking_header_expanded():
      print("[问答] 当前屏未见思考头，继续向下扫描")
    time.sleep(0.4)

    for round_i in range(max_rounds):
      if round_i > 0 and not self._thinking_panel_on_screen():
        if not self._any_visible_refs_on_screen() and not self._has_unexpanded_search_groups(
          expanded_keys
        ):
          print("[问答] 本屏无思考面板且无可见引用，停止数据展开")
          break

      self._expand_visible_search_groups(expanded_keys)
      time.sleep(0.35)
      self._collect_panel_dump(session_dir, f"expand_{round_i + 1:02d}", panels)

      if self._any_visible_refs_on_screen():
        ref_quiet_hits = 0
        prev_ref_count = sum(len(p.references) for p in panels)
        for ref_i in range(min(12, self.p.qa_think_panel_scroll_rounds)):
          if not self._thinking_panel_on_screen() and not self._any_visible_refs_on_screen():
            break
          self._scroll_visible_ref_lists()
          time.sleep(0.3)
          panel, _ = self._collect_panel_dump(
            session_dir,
            f"expand_{round_i + 1:02d}_refs_{ref_i + 1:02d}",
            panels,
          )
          cur = sum(len(p.references) for p in panels)
          if panel and cur > prev_ref_count:
            ref_quiet_hits = 0
            prev_ref_count = cur
          else:
            ref_quiet_hits += 1
            if ref_quiet_hits >= self.p.qa_shot_quiet_rounds:
              break
      else:
        self._expand_visible_search_groups(expanded_keys)
        time.sleep(0.35)
        self._collect_panel_dump(
          session_dir,
          f"expand_{round_i + 1:02d}_retry",
          panels,
        )

      if round_i >= max_rounds - 1:
        break
      self._swipe_message_list_down()
      time.sleep(self.p.qa_shot_post_swipe_sleep)

    merged = self._merge_thinking_panels(panels)
    # 仅当确有引用/搜索组时才回顶重展开（为后续 URL 解析做准备）；
    # 无引用时省去一次上滚扫描。
    if merged.references or merged.groups:
      self._scroll_message_to_top()
      time.sleep(0.4)
      if self._thinking_panel_on_screen():
        self._ensure_thinking_header_expanded()
        self._expand_visible_search_groups(expanded_keys)
        time.sleep(0.35)
    print(
      f"[问答] 数据展开完成: 思考段落 {len(merged.thinking_paragraphs)} 段，"
      f"搜索组 {len(merged.groups)} 个，引用 {len(merged.references)} 条"
    )
    return merged

  def _sweep_expand_and_capture(
    self,
    session_dir: str,
  ) -> tuple[ParsedThinkingPanel, list[str], str]:
    """兼容旧探针：回答长截图 → 展开 → 思考/引用补截 → 拼接。"""
    answer_paths, answer_tops = self._capture_answer_longshot(session_dir)
    panel = self._expand_and_collect_panels(session_dir, shot_count=len(answer_paths))
    think_paths, think_tops = self._capture_thinking_longshot(session_dir, panel)
    all_paths = answer_paths + think_paths
    all_tops = answer_tops + think_tops
    profile = self._qa_shot_profile()
    tmp_path = os.path.join(session_dir, "_shot_tmp.png")
    stitched = self._stitch_shot_paths(
      session_dir, all_paths, profile, tmp_path, copy_bar_tops=all_tops,
    )
    return panel, all_paths, stitched

  def _merge_thinking_panels(self, panels: list[ParsedThinkingPanel]) -> ParsedThinkingPanel:
    merged = ParsedThinkingPanel()
    para_keys: list[str] = []
    group_map: dict[str, Any] = {}
    refs: list[Citation] = []
    seen_ref: set[str] = set()

    for panel in panels:
      if panel.header and not merged.header:
        merged.header = panel.header
      for para in panel.thinking_paragraphs:
        key = "".join(para.split())[:120]
        if key and key not in para_keys:
          para_keys.append(key)
          merged.thinking_paragraphs.append(para)
      for grp in panel.groups:
        gkey = grp.key or grp.title
        if gkey not in group_map:
          group_map[gkey] = {"title": grp.title, "keywords": grp.keywords, "refs": []}
        if grp.keywords and not group_map[gkey]["keywords"]:
          group_map[gkey]["keywords"] = grp.keywords
        for ref in grp.references:
          rkey = f"{ref.group}|{ref.ref_index}|{ref.title[:80]}"
          if rkey in seen_ref:
            continue
          seen_ref.add(rkey)
          group_map[gkey]["refs"].append(ref)
          refs.append(ref)
      for ref in panel.references:
        rkey = f"{ref.group}|{ref.ref_index}|{ref.title[:80]}"
        if rkey in seen_ref:
          continue
        seen_ref.add(rkey)
        refs.append(ref)
        if ref.group and not _is_header_only_group(ref.group.split("|", 1)[0]):
          if ref.group not in group_map:
            title_part = ref.group.split("|", 1)[0]
            group_map[ref.group] = {
              "title": title_part,
              "keywords": "",
              "refs": [],
            }
          group_map[ref.group]["refs"].append(ref)

    merged.thinking_body = "\n\n".join(merged.thinking_paragraphs).strip()
    if merged.header and merged.thinking_body and merged.header not in merged.thinking_body:
      merged.thinking_body = f"{merged.header}\n\n{merged.thinking_body}".strip()
    elif merged.header and not merged.thinking_body:
      merged.thinking_body = merged.header

    merged.groups = []
    merged.search_groups = []
    for gkey, data in group_map.items():
      merged.search_groups.append(gkey)
      grp_refs = sorted(
        data["refs"],
        key=lambda c: (c.ref_index or 9999, c.title),
      )
      merged.groups.append(
        ThinkingSearchGroup(
          title=data.get("title") or gkey.split("|", 1)[0],
          key=gkey,
          keywords=data.get("keywords", ""),
          references=grp_refs,
        )
      )
    merged.references = sorted(refs, key=lambda c: (c.group, c.ref_index or 9999, c.title))
    # 按标题去重（多屏 dump 可能重复采集）
    deduped: list[Citation] = []
    seen_titles: set[str] = set()
    for ref in merged.references:
      key = "".join((ref.title or "").split())[:100]
      if key in seen_titles:
        continue
      seen_titles.add(key)
      deduped.append(ref)
    merged.references = deduped
    return merged

  def _sync_urls_to_panel(
    self,
    panel: ParsedThinkingPanel,
    refs: list[Citation],
  ) -> None:
    by_key = {
      (r.ref_index, (r.title or "")[:80]): r.url
      for r in refs
      if r.url
    }
    for r in panel.references:
      url = by_key.get((r.ref_index, (r.title or "")[:80]))
      if url:
        r.url = url
    for grp in panel.groups:
      for r in grp.references:
        url = by_key.get((r.ref_index, (r.title or "")[:80]))
        if url:
          r.url = url

  def _restore_ref_panel_visible(self) -> bool:
    """URL 解析/回落前尽量恢复可见的引用列表面板。"""
    from app.modules.qa_reference_urls import _get_ref_list_bounds

    shot_count = 4
    locate_rounds = min(
      self.p.qa_scroll_top_rounds + 16,
      max(8, shot_count * 4 + 6),
    )
    self._scroll_to_thinking_panel(max_rounds=locate_rounds)
    self._ensure_thinking_header_expanded()
    self._expand_visible_search_groups(set())
    time.sleep(0.5)
    if _get_ref_list_bounds(self.d, self.p) is not None:
      return True
    # 再轻扫一轮外层列表，引用区可能在当前屏下方
    self._swipe_message_list_down()
    time.sleep(0.35)
    self._ensure_thinking_header_expanded()
    self._expand_visible_search_groups(set())
    time.sleep(0.35)
    return _get_ref_list_bounds(self.d, self.p) is not None

  def _prepare_refs_for_url_resolve(
    self, thinking_refs: list[Citation], *, expected_prompt: str = "",
  ) -> bool:
    """解析 URL 前滚回思考面板、重新展开引用并刷新 bounds。"""
    from app.modules.qa_reference_urls import prepare_citations_for_url_resolve, _get_ref_list_bounds

    self._ensure_chat()
    if expected_prompt and not self._ensure_expected_chat(
      expected_prompt, phase="URL解析前",
    ):
      return False
    if not self._restore_ref_panel_visible():
      print("[问答] 引用面板恢复失败，尝试滚顶后再展开...")
      self._scroll_message_to_top()
      time.sleep(0.4)
      self._restore_ref_panel_visible()
    if _get_ref_list_bounds(self.d, self.p) is None:
      print("[问答] 警告: 引用列表仍不可见，URL 解析可能跳过部分条目")
    prepare_citations_for_url_resolve(self.d, thinking_refs, profile=self.p)
    return True

  def _resolve_reference_urls(
    self,
    thinking_refs: list[Citation],
    *,
    method: ResolveMethod = "logcat",
    net_dump_dir: str = "",
    net_since_mtime: float | None = None,
    expected_prompt: str = "",
    expected_answer: str = "",
    sms_token: str = "",
    sms_device_id: str = "",
  ) -> list[Citation]:
    if not thinking_refs:
      return thinking_refs

    if method == "net":
      dump_dir = net_dump_dir or os.path.join(self.output_dir, "qa_capture_net")
      refs = resolve_urls_from_net_dump(
        thinking_refs,
        dump_dir,
        since_mtime=net_since_mtime,
      )
      missing = [r for r in refs if not r.url]
      if missing:
        print(f"[问答] 网络抓包未覆盖 {len(missing)} 条，回落 logcat 逐条点击...")
        if not self._prepare_refs_for_url_resolve(refs, expected_prompt=expected_prompt):
          return refs
        return resolve_thinking_reference_urls(
          self.d,
          refs,
          profile=self.p,
          max_refs=self.p.qa_resolve_url_max_refs,
          method="logcat",
          expected_prompt=expected_prompt,
          expected_answer=expected_answer,
          sms_token=sms_token,
          sms_device_id=sms_device_id,
        )
      return refs

    if not self._prepare_refs_for_url_resolve(
      thinking_refs, expected_prompt=expected_prompt,
    ):
      return thinking_refs
    return resolve_thinking_reference_urls(
      self.d,
      thinking_refs,
      profile=self.p,
      max_refs=self.p.qa_resolve_url_max_refs,
      method=method,
      expected_prompt=expected_prompt,
      expected_answer=expected_answer,
      sms_token=sms_token,
      sms_device_id=sms_device_id,
    )

  def _scroll_visible_ref_lists(self) -> None:
    """在已展开的引用列表区域内下滑，露出更多引用条目。"""
    for sel in self.p.qa_ref_list_probe_xpaths:
      try:
        el = self.d.xpath(sel).get(timeout=0.6)
        if el:
          b = el.bounds
          if b:
            x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
            cx = (x1 + x2) // 2
            span = max(y2 - y1, 80)
            self.d.swipe(
              cx,
              y1 + int(span * 0.82),
              cx,
              y1 + int(span * 0.22),
              0.24,
            )
            time.sleep(0.28)
            return
      except Exception:
        continue
    if self._any_visible_refs_on_screen():
      try:
        items = self.d.xpath(
          '//*[@resource-id="com.larus.nova:id/ll_source_item"]'
        ).all()
        if items:
          mid = items[len(items) // 2]
          b = mid.bounds
          if b:
            x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
            cx = (x1 + x2) // 2
            span = max(y2 - y1, 80)
            self.d.swipe(
              cx,
              y1 + int(span * 0.82),
              cx,
              y1 + int(span * 0.22),
              0.24,
            )
            time.sleep(0.28)
            return
      except Exception:
        pass
    if not self._thinking_panel_on_screen():
      return
    w, h = display_wh(self.d, profile=self.p)
    self.d.swipe(int(w * 0.5), int(h * 0.62), int(w * 0.5), int(h * 0.48), 0.22)
    time.sleep(0.28)

  def _merge_parsed(
    self,
    parsed: ParsedExchange,
    clipboard_body: str,
    prompt: str,
    thinking_panel: ParsedThinkingPanel | None,
    early_answer_body: str = "",
  ) -> tuple[str, str, str, list[Citation], list[Citation], list[str]]:
    question = parsed.question_text or prompt
    thinking = parsed.thinking
    if thinking_panel and (
      thinking_panel.header or thinking_panel.groups or thinking_panel.references
    ):
      md = render_thinking_markdown(thinking_panel)
      if md.strip():
        thinking = md
      elif thinking_panel.thinking_body:
        thinking = thinking_panel.thinking_body
    ref_titles = (
      [r.title for r in thinking_panel.references]
      if thinking_panel and thinking_panel.references
      else []
    )
    early = (early_answer_body or "").strip()
    if early and len(early) >= 80:
      answer = early
    else:
      answer = self._pick_best_answer_body(
        early_answer_body,
        clipboard_body,
        parsed.answer_body,
        prompt=prompt,
        ref_titles=ref_titles,
      )
    if not answer:
      cands = collect_reply_text_candidates(
        self.d, prompt_text=prompt, min_len=20, profile=self.p,
      )
      filtered = [
        t for t, _, _ in cands
        if not self._is_reference_title_text(t, ref_titles)
      ]
      if filtered:
        answer = max(filtered, key=len)
      elif cands:
        answer = max(cands, key=lambda x: len(x[0]))[0]
    citations = list(parsed.citations)
    thinking_refs = list(thinking_panel.references) if thinking_panel else []
    raw_texts = list(parsed.raw_texts)
    return question, thinking, answer, citations, thinking_refs, raw_texts

  def _save_record(self, record: QaRecord) -> str:
    """写入 record.json 与拆分文本文件。"""
    session = record.session_dir
    os.makedirs(session, exist_ok=True)
    record_path = os.path.join(session, "record.json")
    with open(record_path, "w", encoding="utf-8") as f:
      json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

    if record.question_text:
      with open(os.path.join(session, "question.txt"), "w", encoding="utf-8") as f:
        f.write(record.question_text + "\n")
    if record.thinking:
      thinking_md = record.thinking
      with open(os.path.join(session, "thinking.md"), "w", encoding="utf-8") as f:
        f.write(thinking_md)
      with open(os.path.join(session, "thinking.txt"), "w", encoding="utf-8") as f:
        f.write(thinking_md)
    if record.answer_body:
      with open(os.path.join(session, "answer.txt"), "w", encoding="utf-8") as f:
        f.write(record.answer_body + "\n")
    if record.citations:
      with open(os.path.join(session, "citations.json"), "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in record.citations], f, ensure_ascii=False, indent=2)
    if record.thinking_references:
      with open(os.path.join(session, "thinking_references.json"), "w", encoding="utf-8") as f:
        json.dump(
          [asdict(c) for c in record.thinking_references],
          f,
          ensure_ascii=False,
          indent=2,
        )
    if record.raw_texts:
      with open(os.path.join(session, "raw_texts.json"), "w", encoding="utf-8") as f:
        json.dump(record.raw_texts, f, ensure_ascii=False, indent=2)

    if record.hierarchy_xml:
      with open(os.path.join(session, "hierarchy_final.xml"), "w", encoding="utf-8") as f:
        f.write(record.hierarchy_xml)

    print(f"[问答] 已保存: {record_path}")
    return record_path

  def run(
    self,
    prompt: str = "请简要介绍2026年旗舰手机选购要点，并列出参考来源",
    skip_send: bool = False,
    mode: Literal["fast", "think"] = "fast",
    enable_deep_think: bool | None = None,
    resolve_urls: bool = True,
    resolve_method: ResolveMethod = "logcat",
    net_dump_dir: str = "",
    sms_token: str = "",
    sms_device_id: str = "",
  ) -> QaRecord:
    # 兼容旧参数：显式 enable_deep_think 优先于 mode
    if enable_deep_think is not None:
      mode = "think" if enable_deep_think else "fast"

    session_dir = build_session_dir(
      self.output_dir, "qa_capture", project=self.project_slug
    )
    capture_started_at = time.time()
    record = QaRecord(
      prompt=prompt,
      session_dir=session_dir,
      captured_at=datetime.now().isoformat(timespec="seconds"),
      device_info=detect_device_info(self.d),
      mode=mode,
      deep_think_enabled=(mode == "think"),
    )
    saved = False

    try:
      result = self._run_capture_body(
        record=record,
        prompt=prompt,
        skip_send=skip_send,
        mode=mode,
        resolve_urls=resolve_urls,
        resolve_method=resolve_method,
        net_dump_dir=net_dump_dir,
        sms_token=sms_token,
        sms_device_id=sms_device_id,
        capture_started_at=capture_started_at,
      )
      saved = True
      return result
    finally:
      if not saved and (
        record.answer_body
        or record.thinking
        or record.thinking_references
        or record.raw_texts
      ):
        try:
          self._save_record(record)
          print(f"[问答] 异常中断，已保存部分产出: {session_dir}")
        except OSError as exc:
          print(f"[问答] 部分产出保存失败: {exc}")

  def _run_capture_body(
    self,
    *,
    record: QaRecord,
    prompt: str,
    skip_send: bool,
    mode: Literal["fast", "think"],
    resolve_urls: bool,
    resolve_method: ResolveMethod,
    net_dump_dir: str,
    sms_token: str,
    sms_device_id: str,
    capture_started_at: float,
  ) -> QaRecord:
    session_dir = record.session_dir
    _phase_t0 = time.time()
    _phase_last = [_phase_t0]

    def _phase(label: str) -> None:
      now = time.time()
      print(
        f"[计时] {label}: {now - _phase_last[0]:.1f}s "
        f"(累计 {now - _phase_t0:.1f}s)"
      )
      _phase_last[0] = now

    if not self._crawler.start_app():
      print("[问答] 启动失败")
      self._save_record(record)
      return record

    if not self._crawler.handle_login_if_needed(
      sms_token=sms_token,
      device_id=sms_device_id,
    ):
      print("[问答] 登录失败")
      self._save_record(record)
      return record
    _phase("启动+登录")

    if not skip_send:
      if not self._open_new_conversation():
        print("[问答] 创建新对话失败，继续在当前会话采集")
      # 等待模式切换入口就绪；就绪即继续，未就绪退回原固定等待时长
      poll_until(
        lambda: bool(self.d.xpath(MODE_MENU_XPATH).get(timeout=0.1)),
        timeout=1.2,
        interval=0.15,
        settle=0.2,
      )
      for attempt in range(3):
        if self._select_mode(mode):
          break
        if attempt < 2:
          print(f"[问答] 模式切换重试 {attempt + 2}/3...")
          time.sleep(0.8)
      else:
        print(f"[问答] 警告: 未能切换到 {mode} 模式，继续发送")
      # fast 模式下若仍显示专家，再强制切一次，避免耗专业版额度
      if mode == "fast" and self._read_current_mode_label() == "专家":
        print("[问答] 发送前仍检测到专家模式，再次强制切快速")
        if not self._select_mode("fast"):
          print("[问答] 无法离开专家模式，中止本轮以免空耗额度")
          self._save_record(record)
          return record
      if not self._crawler.send_message(prompt):
        print("[问答] 发送失败，尝试重启豆包后重发...")
        from app.modules.navigator import Navigator
        Navigator(self.d).hard_restart_app(reason="发送失败")
        time.sleep(2.0)
        if (
          self._crawler.start_app()
          and self._crawler.handle_login_if_needed(
            sms_token=sms_token, device_id=sms_device_id,
          )
          and self._open_new_conversation()
          and self._select_mode(mode)
          and self._crawler.send_message(prompt)
        ):
          print("[问答] 重启后发送成功")
        else:
          self._save_record(record)
          return record
      _phase("新会话+切模式+发送")
      if not self._crawler.wait_reply_done(timeout=180):
        print("[问答] 等待回复超时，继续尝试采集当前屏内容")
      _phase("等待回复完成")
      if not self._ensure_expected_chat(prompt, phase="回复完成后"):
        from app.modules.navigator import Navigator
        nav = Navigator(self.d)
        if nav.reenter_chat_by_prompt(prompt):
          print("[问答] 会话错位后已重进，继续采集")
        else:
          print("[问答] 会话可能错位，仍继续采集（避免空数据中止）")

    # 等待回答操作栏（复制按钮）就绪后再采集；就绪即继续，未就绪退回原固定等待时长
    poll_until(
      lambda: bool(
        self.d.xpath('//*[@resource-id="com.larus.nova:id/msg_action_copy"]').get(timeout=0.1)
      ),
      timeout=1.0,
      interval=0.15,
      settle=0.15,
    )
    self._ensure_chat()
    self._dismiss_overlays()

    early_answer_body = self._capture_answer_body_early(session_dir, prompt)
    _phase("早期正文采集")
    if self._answer_looks_like_quota_block(early_answer_body):
      print(
        "[问答] 检测到专家/专业版额度提示，中止本轮采集并重试"
        f"（正文前缀: {early_answer_body[:48]!r}）"
      )
      # 关掉额度弹窗，避免挡住下一轮新建对话
      try:
        from app.modules.navigator import Navigator
        Navigator(self.d).accept_blocking_prompts(max_rounds=3)
      except Exception:
        pass
      record.answer_body = early_answer_body
      self._save_record(record)
      return record

    answer_paths, answer_tops = self._capture_answer_longshot(session_dir)
    _phase("回答长截图")
    thinking_panel = self._expand_and_collect_panels(
      session_dir, shot_count=len(answer_paths),
    )
    _phase("展开+数据采集")
    think_paths, think_tops = self._capture_thinking_longshot(session_dir, thinking_panel)
    if think_paths:
      _phase("思考/引用长截图")
    shot_paths = answer_paths + think_paths
    copy_bar_tops = answer_tops + think_tops
    profile = self._qa_shot_profile()
    tmp_path = os.path.join(session_dir, "_shot_tmp.png")
    stitched = self._stitch_shot_paths(
      session_dir, shot_paths, profile, tmp_path, copy_bar_tops=copy_bar_tops,
    )
    _phase("长图拼接")
    record.screenshots = shot_paths
    record.stitched_screenshot = stitched

    self._dismiss_overlays()
    time.sleep(0.4)

    # 在思考面板仍可见时先解析引用 URL，再 dump 最终 hierarchy
    thinking_refs_for_resolve = list(thinking_panel.references) if thinking_panel else []
    if resolve_urls and thinking_refs_for_resolve:
      need = [r for r in thinking_refs_for_resolve if not r.url]
      if need:
        print(
          f"[问答] 解析引用真实链接（{len(need)} 条，method={resolve_method}）..."
        )
        thinking_refs_for_resolve = self._resolve_reference_urls(
          thinking_refs_for_resolve,
          method=resolve_method,
          net_dump_dir=net_dump_dir,
          net_since_mtime=capture_started_at if resolve_method == "net" else None,
          expected_prompt=prompt,
          expected_answer=early_answer_body,
          sms_token=sms_token,
          sms_device_id=sms_device_id,
        )
        self._sync_urls_to_panel(thinking_panel, thinking_refs_for_resolve)

    if resolve_urls and thinking_refs_for_resolve:
      _phase("解析引用URL")

    xml_path, shot_path = self._dump_raw(session_dir, "final")
    xml_text = ""
    if xml_path and os.path.isfile(xml_path):
      with open(xml_path, encoding="utf-8") as f:
        xml_text = f.read()
    record.hierarchy_xml = xml_text
    if shot_path and shot_path not in record.screenshots:
      record.screenshots.append(shot_path)

    w, h = display_wh(self.d, profile=self.p)
    parsed = (
      parse_exchange_from_hierarchy(
        xml_text,
        prompt_text=prompt,
        screen_w=w,
        screen_h=h,
        profile=self.p,
      )
      if xml_text
      else ParsedExchange()
    )

    clipboard_body = self._crawler.copy_reply()
    question, thinking, answer, citations, thinking_refs, raw_texts = self._merge_parsed(
      parsed, clipboard_body, prompt, thinking_panel, early_answer_body=early_answer_body,
    )
    if thinking_refs_for_resolve:
      by_key = {
        (r.ref_index, (r.title or "")[:80]): r
        for r in thinking_refs_for_resolve
      }
      for i, ref in enumerate(thinking_refs):
        key = (ref.ref_index, (ref.title or "")[:80])
        resolved = by_key.get(key)
        if resolved and resolved.url:
          thinking_refs[i].url = resolved.url
    # 兜底：最终 hierarchy 若含引用面板，再合并一次
    if xml_text:
      final_panel = parse_thinking_panel(xml_text)
      if final_panel.references or final_panel.groups:
        thinking_panel = self._merge_thinking_panels([thinking_panel, final_panel])
        question, thinking, answer, citations, thinking_refs, raw_texts = self._merge_parsed(
          parsed,
          clipboard_body,
          prompt,
          thinking_panel,
          early_answer_body=early_answer_body,
        )
    if resolve_urls and thinking_refs:
      need = [r for r in thinking_refs if not r.url]
      if need:
        print(
          f"[问答] 回落解析剩余引用 URL（{len(need)} 条，method={resolve_method}）..."
        )
        self._ensure_chat()
        can_fallback = self._ensure_expected_chat(prompt, phase="回落URL解析前")
        if can_fallback and not self._restore_ref_panel_visible():
          print("[问答] 回落前引用列表不可见，再次尝试恢复面板...")
          self._scroll_message_to_top()
          time.sleep(0.4)
          self._restore_ref_panel_visible()
        from app.modules.qa_reference_urls import _get_ref_list_bounds

        if can_fallback and _get_ref_list_bounds(self.d, self.p) is not None:
          thinking_refs = self._resolve_reference_urls(
            thinking_refs,
            method=resolve_method,
            net_dump_dir=net_dump_dir,
            net_since_mtime=capture_started_at if resolve_method == "net" else None,
            expected_prompt=prompt,
            expected_answer=early_answer_body,
            sms_token=sms_token,
            sms_device_id=sms_device_id,
          )
        elif not can_fallback:
          print("[问答] 回落解析跳过：会话已错位（避免在历史对话上空转）")
        else:
          print("[问答] 回落解析跳过：无法恢复引用列表（避免空转卡死）")
        if thinking_panel.references or thinking_panel.groups:
          self._sync_urls_to_panel(thinking_panel, thinking_refs)
          md = render_thinking_markdown(thinking_panel)
          if md.strip():
            thinking = md
    elif thinking_panel and (thinking_panel.references or thinking_panel.groups):
      md = render_thinking_markdown(thinking_panel)
      if md.strip():
        thinking = md
    if not question or question == prompt:
      question = self._read_question_from_ui(prompt)

    record.question_text = question
    record.thinking = thinking
    record.answer_body = answer
    record.citations = citations
    record.thinking_references = thinking_refs
    record.raw_texts = raw_texts
    record.raw_nodes = parsed.raw_nodes

    self._save_record(record)
    _phase("最终dump+解析合并+保存")

    print(f"\n{'=' * 60}")
    print(f"[问答完成] 目录: {session_dir}")
    print(f"  模式: {mode}")
    print(f"  问题长度: {len(record.question_text)}")
    print(f"  思考长度: {len(record.thinking)}")
    print(f"  正文长度: {len(record.answer_body)}")
    print(f"  引用条数: {len(record.citations)}")
    print(f"  思考引用: {len(record.thinking_references)}")
    url_count = sum(1 for r in record.thinking_references if r.url)
    if record.thinking_references:
      print(f"  引用 URL: {url_count}/{len(record.thinking_references)}")
    print(f"  分屏截图: {len(record.screenshots)}")
    print(f"  拼接长图: {'有' if record.stitched_screenshot else '无'}")
    print(f"{'=' * 60}")
    if not self._crawler.nav.is_chat():
      print("[问答] 采集结束，预清理回聊天页（供下一条抽检）...")
      self._ensure_chat()
    return record
