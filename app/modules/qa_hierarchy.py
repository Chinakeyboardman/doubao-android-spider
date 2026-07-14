# -*- coding: utf-8 -*-
"""
从 dump_hierarchy XML 解析豆包聊天区节点，提取问题/思考/引用/正文。

选择器来源：真机侦察 logs/recon_thread_hierarchy.xml（Honor PCT-AL10）。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from app.config.gesture_profile import GestureProfile

_PKG = "com.larus.nova"
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+|www\.[^\s\"'<>]+", re.I)

# 侦察到的稳定 resource-id / 文案特征
MESSAGE_LIST_RID = f"{_PKG}:id/message_list"
CONTENT_VIEW_RID = f"{_PKG}:id/content_view"
REFERENCE_TITLE_RID = f"{_PKG}:id/tv_reference_title"
REFERENCE_CONTENT_RID = f"{_PKG}:id/tv_reference_content"
REFERENCE_INDEX_RID = f"{_PKG}:id/tv_reference_index"
SOURCE_ITEM_RID = f"{_PKG}:id/ll_source_item"
SEARCH_REF_CONTAINER_RID = f"{_PKG}:id/searchReferenceTitleContainer"
SEARCH_REF_LIST_RID = f"{_PKG}:id/search_reference_list"
SUB_DEEP_THINK_RID = f"{_PKG}:id/sub_deep_think_block_list"
SUB_KEYWORD_REFERENCE_RID = f"{_PKG}:id/sub_keyword_reference"
REFERENCE_WRAPPER_RID = f"{_PKG}:id/ll_reference_title_wrapper"
VIDEO_GROUP_RID_MARKERS = ("md_video_group", "videoTitle", "tvMdBottomCaption")
CITATION_TEXT_KEYWORDS = ("引用", "来源", "参考", "相关视频", "已搜索", "联网搜索", "搜索资料", "资料")
THINKING_TEXT_KEYWORDS = ("思考", "推理", "深度思考", "思考过程", "已思考")
THINKING_DESC_KEYWORDS = ("思考", "推理", "深度思考")
EXPAND_THINKING_TEXTS = ("展开", "查看思考", "查看推理", "展开全部", "展开思考")
_TITLE_BAR_RID_MARKERS = (
    "title_container",
    "back_icon",
    "main_container",
    "subtitle",
    "tts",
    "real_time_call",
    "chat_title",
)
_UI_NOISE_TEXTS = (
    "内容由 AI 生成",
    "对话列表",
    "豆包",
    "打电话",
    "朗读已关闭",
    "朗读",
    "复制",
    "分享",
    "收藏",
    "重新生成",
    "快速",
    "AI 创作",
    "拍题答疑",
    "豆包 P 图",
    "相机",
    "语音输入",
    "更多面板",
    "发消息或按住说话",
    "聊聊新话题",
)
_ACTION_BAR_RID_MARKERS = (
    "msg_action_",
    "action_bar",
    "tv_item_name",
    "fast_button_icon",
    "input_text",
    "action_send",
    "menu_text",
    "menu_sub_text",
    "action_menu_entrance",
    "input_delegate",
    "action_entrance",
    "action_speak",
)


@dataclass
class UiNode:
  """单个 UI 节点摘要。"""

  text: str = ""
  content_desc: str = ""
  resource_id: str = ""
  clazz: str = ""
  clickable: bool = False
  bounds: tuple[int, int, int, int] = (0, 0, 0, 0)

  @property
  def combined(self) -> str:
    return f"{self.text} {self.content_desc}".strip()

  @property
  def center_x(self) -> float:
    return (self.bounds[0] + self.bounds[2]) / 2

  @property
  def width(self) -> int:
    return max(0, self.bounds[2] - self.bounds[0])


@dataclass
class Citation:
  """引用/来源条目。"""

  title: str
  url: str = ""
  source: str = ""
  desc: str = ""
  resource_id: str = ""
  bounds: list[int] = field(default_factory=list)
  ref_index: int = 0
  group: str = ""


@dataclass
class ThinkingSearchGroup:
  """一次搜索引用组（含关键词与引用列表）。"""

  title: str
  keywords: str = ""
  references: list[Citation] = field(default_factory=list)
  key: str = ""  # 消歧：title|y，同标题多轮不合并


@dataclass
class ParsedThinkingPanel:
  """思考面板解析结果。"""

  header: str = ""
  thinking_paragraphs: list[str] = field(default_factory=list)
  thinking_body: str = ""
  search_groups: list[str] = field(default_factory=list)
  groups: list[ThinkingSearchGroup] = field(default_factory=list)
  references: list[Citation] = field(default_factory=list)


@dataclass
class ParsedExchange:
  """从 hierarchy 解析出的结构化问答。"""

  question_text: str = ""
  thinking: str = ""
  answer_body: str = ""
  citations: list[Citation] = field(default_factory=list)
  raw_texts: list[str] = field(default_factory=list)
  raw_nodes: list[dict[str, Any]] = field(default_factory=list)


def _parse_bounds(raw: str) -> tuple[int, int, int, int]:
  m = _BOUNDS_RE.search(raw or "")
  if not m:
    return (0, 0, 0, 0)
  return tuple(int(m.group(i)) for i in range(1, 5))  # type: ignore[return-value]


def _clean_text(text: str) -> str:
  if not text:
    return ""
  return (
    text.replace("&#10;", "\n")
    .replace("&amp;", "&")
    .replace("&lt;", "<")
    .replace("&gt;", ">")
    .strip()
  )


def _is_action_bar_node(node: UiNode) -> bool:
  rid = node.resource_id
  if any(marker in rid for marker in _ACTION_BAR_RID_MARKERS):
    return True
  if node.text in ("复制", "朗读", "收藏", "分享", "重新生成", "快速", "AI 创作"):
    return True
  return False


def _is_user_bubble(node: UiNode, screen_w: int, profile: GestureProfile) -> bool:
  if not node.text or node.bounds[3] <= node.bounds[1]:
    return False
  cx = node.center_x
  if cx >= screen_w * profile.qa_user_bubble_cx_min:
    return True
  if node.bounds[0] >= screen_w * profile.qa_user_bubble_x1_min:
    return True
  return False


def _is_assistant_block(node: UiNode, screen_w: int, profile: GestureProfile) -> bool:
  if not node.text:
    return False
  x1, _, x2, _ = node.bounds
  bw = x2 - x1
  if x1 <= screen_w * profile.qa_assist_x1_max and bw >= screen_w * profile.qa_assist_bw_min:
    return True
  return node.center_x <= screen_w * profile.qa_assist_cx_max


def _is_title_bar_node(node: UiNode) -> bool:
  rid = node.resource_id
  return any(marker in rid for marker in _TITLE_BAR_RID_MARKERS)


def _is_ui_noise(text: str, desc: str = "") -> bool:
  for noise in _UI_NOISE_TEXTS:
    if text == noise or desc == noise:
      return True
  return False


def _message_area_bounds(
  nodes: list[UiNode],
  screen_h: int,
  profile: GestureProfile,
) -> tuple[int, int]:
  """从 message_list / message_list_parent 推断消息区 y 范围。"""
  top = int(screen_h * profile.content_top_fallback)
  bottom = int(screen_h * profile.content_bottom_fallback)
  for node in nodes:
    rid = node.resource_id
    if MESSAGE_LIST_RID in rid or "message_list_parent" in rid:
      y1, y2 = node.bounds[1], node.bounds[3]
      if y2 > y1:
        return y1, y2
  return top, bottom


def _in_message_area(node: UiNode, y_top: int, y_bottom: int) -> bool:
  cy = (node.bounds[1] + node.bounds[3]) // 2
  return y_top <= cy <= y_bottom


def _looks_like_thinking(text: str, desc: str = "") -> bool:
  blob = f"{text} {desc}"
  if len(blob.strip()) <= 4:
    return False
  if text in ("思考", "专家", "快速"):
    return False
  if "已关闭" in desc or "已开启" in desc:
    return False
  if desc.startswith("深度思考"):
    return False
  return any(k in blob for k in THINKING_TEXT_KEYWORDS)


def _looks_like_citation(node: UiNode) -> bool:
  blob = node.combined
  if any(k in blob for k in CITATION_TEXT_KEYWORDS):
    return True
  if any(marker in node.resource_id for marker in VIDEO_GROUP_RID_MARKERS):
    return True
  if _URL_RE.search(blob):
    return True
  if node.clickable and ("抖音视频" in blob or node.content_desc.startswith("抖音")):
    return True
  return False


def _extract_urls(text: str) -> list[str]:
  urls: list[str] = []
  for m in _URL_RE.finditer(text or ""):
    u = m.group(0).rstrip(".,;)")
    if not u.startswith("http"):
      u = "https://" + u
    urls.append(u)
  return urls


def iter_ui_nodes(xml_text: str) -> list[UiNode]:
  """解析 hierarchy XML 为 UiNode 列表（仅 com.larus.nova 包）。"""
  root = ET.fromstring(xml_text)
  nodes: list[UiNode] = []
  for el in root.iter("node"):
    pkg = el.attrib.get("package", "")
    if pkg and pkg != _PKG:
      continue
    text = _clean_text(el.attrib.get("text", ""))
    desc = _clean_text(el.attrib.get("content-desc", ""))
    rid = el.attrib.get("resource-id", "")
    clazz = el.attrib.get("class", "")
    clickable = el.attrib.get("clickable", "false") == "true"
    bounds = _parse_bounds(el.attrib.get("bounds", ""))
    if not any((text, desc, rid)):
      continue
    nodes.append(
      UiNode(
        text=text,
        content_desc=desc,
        resource_id=rid,
        clazz=clazz,
        clickable=clickable,
        bounds=bounds,
      )
    )
  return nodes


def parse_exchange_from_hierarchy(
  xml_text: str,
  *,
  prompt_text: str = "",
  screen_w: int = 1080,
  screen_h: int = 2400,
  profile: GestureProfile | None = None,
) -> ParsedExchange:
  """
  从 hierarchy 提取问题、思考、引用与助手正文候选。
  几何启发式 + 文案/ rid 特征；未命中时 raw_texts 兜底。
  """
  p = profile or GestureProfile()
  all_nodes = iter_ui_nodes(xml_text)
  msg_top, msg_bottom = _message_area_bounds(all_nodes, screen_h, p)
  prompt_norm = "".join((prompt_text or "").split())
  prompt_short = prompt_norm[: min(24, len(prompt_norm))]

  raw_texts: list[str] = []
  raw_nodes: list[dict[str, Any]] = []
  thinking_parts: list[str] = []
  answer_parts: list[str] = []
  question_parts: list[str] = []
  citations: list[Citation] = []
  seen_citation: set[str] = set()
  seen_text: set[str] = set()

  for node in all_nodes:
    if _is_action_bar_node(node) or _is_title_bar_node(node):
      continue
    if not _in_message_area(node, msg_top, msg_bottom):
      continue
    text = node.text
    desc = node.content_desc
    if _is_ui_noise(text, desc):
      continue
    if not text and not desc:
      continue

    for piece in (text, desc):
      if piece and piece not in seen_text and len(piece) >= 2 and not _is_ui_noise(piece):
        seen_text.add(piece)
        raw_texts.append(piece)

    raw_nodes.append(
      {
        "text": text,
        "content_desc": desc,
        "resource_id": node.resource_id,
        "class": node.clazz,
        "clickable": node.clickable,
        "bounds": list(node.bounds),
      }
    )

    if _looks_like_citation(node):
      title = text or desc
      if len(title) > 300 and not any(marker in node.resource_id for marker in VIDEO_GROUP_RID_MARKERS):
        continue
      urls = _extract_urls(f"{text} {desc}")
      key = f"{title}|{urls[0] if urls else ''}|{node.resource_id}"
      if key not in seen_citation and title:
        seen_citation.add(key)
        citations.append(
          Citation(
            title=title[:500],
            url=urls[0] if urls else "",
            source=desc if desc and desc != title else "",
            desc=desc,
            resource_id=node.resource_id,
            bounds=list(node.bounds),
          )
        )

    if _looks_like_thinking(text, desc):
      blob = text or desc
      if blob and blob not in thinking_parts:
        thinking_parts.append(blob)
      continue

    if prompt_short and prompt_short in "".join(text.split()):
      if _is_user_bubble(node, screen_w, p) or len(text) <= len(prompt_text) + 20:
        if text not in question_parts:
          question_parts.append(text)
        continue

    if text and _is_user_bubble(node, screen_w, p) and len(text) >= 4:
      if text not in question_parts:
        question_parts.append(text)
      continue

    if text and _is_assistant_block(node, screen_w, p) and len(text) >= 20:
      if text not in answer_parts:
        answer_parts.append(text)

  question_text = question_parts[-1] if question_parts else (prompt_text or "")
  thinking = "\n\n".join(thinking_parts).strip()
  answer_body = max(answer_parts, key=len, default="")

  return ParsedExchange(
    question_text=question_text,
    thinking=thinking,
    answer_body=answer_body,
    citations=citations,
    raw_texts=raw_texts,
    raw_nodes=raw_nodes,
  )


def expand_thinking_xpaths() -> tuple[str, ...]:
  """可点击的「展开思考头」候选 xpath（勿匹配搜索组标题）。"""
  return (
    '//*[@resource-id="com.larus.nova:id/ll_reference_title"]',
    f'//*[@resource-id="{REFERENCE_TITLE_RID}" and contains(@text,"已完成思考")]',
    f'//*[@resource-id="{REFERENCE_TITLE_RID}" and contains(@text,"搜索")]',
    '//*[contains(@text,"已完成思考") and contains(@text,"篇资料")]',
  )


def _norm_text_key(text: str) -> str:
  return "".join((text or "").split())[:120]


def _infer_source_from_title(title: str) -> str:
  """从标题尾部推断来源（如 _PConline太平洋科技）。"""
  if not title:
    return ""
  if "_" in title:
    tail = title.rsplit("_", 1)[-1].strip()
    if 2 <= len(tail) <= 40:
      return tail
  for marker in (
    "太平洋科技", "中关村在线", "雷科技", "知乎", "抖音",
    "哔哩哔哩", "B站", "IT之家", "网易", "腾讯新闻", "财经头条",
  ):
    if marker in title:
      return marker
  return ""


def _is_search_group_title(text: str) -> bool:
  return bool(text) and "搜索" in text and "篇资料" in text


def _thinking_block_bounds(nodes: list[UiNode]) -> tuple[int, int] | None:
  """定位思考/引用面板的 y 范围（兼容 fast / think 两种布局）。"""
  for marker in (
    SUB_DEEP_THINK_RID,
    SUB_KEYWORD_REFERENCE_RID,
    REFERENCE_WRAPPER_RID,
    "subview_container",
  ):
    for node in nodes:
      if marker in node.resource_id:
        y1, y2 = node.bounds[1], node.bounds[3]
        if y2 > y1:
          return y1, y2
  header_y = 0
  for node in nodes:
    if REFERENCE_TITLE_RID in node.resource_id and node.text:
      header_y = node.bounds[1]
      break
  if header_y > 0:
    return header_y, 10_000
  return None


def _panel_present(nodes: list[UiNode]) -> bool:
  for node in nodes:
    rid = node.resource_id
    if any(
      m in rid
      for m in (
        REFERENCE_WRAPPER_RID,
        SUB_DEEP_THINK_RID,
        SUB_KEYWORD_REFERENCE_RID,
        SOURCE_ITEM_RID,
      )
    ):
      return True
    if REFERENCE_TITLE_RID in rid and node.text:
      if "已完成思考" in node.text or _is_search_group_title(node.text):
        return True
  return False


def _is_thinking_paragraph_node(
  node: UiNode,
  header: str,
  block: tuple[int, int] | None,
) -> bool:
  rid = node.resource_id
  text = node.text
  desc = node.content_desc
  blob = text or desc
  if not blob or len(blob) < 20:
    return False
  if not block:
    return False
  cy = (node.bounds[1] + node.bounds[3]) // 2
  if not (block[0] <= cy <= block[1]):
    return False
  if _is_action_bar_node(node) or _is_title_bar_node(node):
    return False
  if _is_ui_noise(text, desc):
    return False
  if blob == header or "已完成思考" in blob:
    return False
  if "search_reference" in rid or "search_key_words" in rid:
    return False
  if REFERENCE_CONTENT_RID in rid or REFERENCE_INDEX_RID in rid:
    return False
  if any(m in rid for m in (SOURCE_ITEM_RID, SEARCH_REF_LIST_RID, SEARCH_REF_CONTAINER_RID)):
    return False
  if "篇资料" in blob and "搜索" in blob:
    return False
  if blob.startswith(("“", '"')) and ("推荐" in blob or "排行" in blob):
    return False
  return True


def _nearest_group_title(group_titles: list[tuple[str, int]], y: int) -> str:
  best = ""
  best_y = -1
  for title, gy in group_titles:
    if gy <= y and gy > best_y:
      best_y = gy
      best = title
  return best


def parse_thinking_panel(xml_text: str) -> ParsedThinkingPanel:
  """
  从已展开的思考/引用面板 hierarchy 提取思考正文与引用条目。

  选择器来源：真机侦察 logs/recon4_ref_expanded.xml（Honor PCT-AL10）。
  """
  nodes = iter_ui_nodes(xml_text)
  header = ""
  thinking_parts: list[tuple[int, str]] = []
  group_titles: list[tuple[str, int]] = []
  keywords_by_group: dict[str, str] = {}
  ref_candidates: list[tuple[int, Citation]] = []
  seen_para: set[str] = set()

  for node in nodes:
    rid = node.resource_id
    text = node.text
    desc = node.content_desc

    if REFERENCE_TITLE_RID in rid and text:
      if "已完成思考" in text:
        header = text
      if _is_search_group_title(text):
        group_titles.append((text, node.bounds[1]))
        if not header:
          header = text
      continue

    if "search_reference_title" in rid and text:
      group_titles.append((text, node.bounds[1]))
      continue

    if "search_key_words" in rid and text:
      group = _nearest_group_title(group_titles, node.bounds[1])
      if group:
        keywords_by_group[group] = text
      continue

  kw_top = 0
  kw_bottom = 0
  kw_found = False
  for node in nodes:
    if SUB_KEYWORD_REFERENCE_RID in node.resource_id:
      kw_top, kw_bottom = node.bounds[1], node.bounds[3]
      kw_found = True
      break

  if kw_found:
    for node in nodes:
      text = node.text
      if not text or len(text) < 8:
        continue
      cy = (node.bounds[1] + node.bounds[3]) // 2
      if kw_top <= cy <= kw_bottom and ('"' in text or "“" in text):
        group = group_titles[-1][0] if group_titles else (header or "")
        if group and group not in keywords_by_group:
          keywords_by_group[group] = text

  if not _panel_present(nodes):
    return ParsedThinkingPanel()

  block = _thinking_block_bounds(nodes)

  for node in nodes:
    if not _is_thinking_paragraph_node(node, header, block):
      continue
    blob = node.text or node.content_desc
    cy = (node.bounds[1] + node.bounds[3]) // 2
    if block and not (block[0] <= cy <= block[1]):
      continue
    key = _norm_text_key(blob)
    if key in seen_para:
      continue
    seen_para.add(key)
    thinking_parts.append((node.bounds[1], blob))

  index_nodes = [n for n in nodes if REFERENCE_INDEX_RID in n.resource_id and n.text]
  for node in nodes:
    if REFERENCE_CONTENT_RID not in node.resource_id or not node.text:
      continue
    ref_index = 0
    y1 = node.bounds[1]
    for idx_node in index_nodes:
      if abs(idx_node.bounds[1] - y1) <= 10:
        try:
          ref_index = int(idx_node.text.rstrip("."))
        except ValueError:
          pass
        break
    group_key = ""
    best_y = -1
    for gt, gy in group_titles:
      if gy <= y1 and gy > best_y:
        best_y = gy
        group_key = f"{gt}|{gy}"
    if not group_key and group_titles:
      gt, gy = group_titles[-1]
      group_key = f"{gt}|{gy}"
    urls = _extract_urls(f"{node.text} {node.content_desc}")
    ref_candidates.append(
      (
        y1,
        Citation(
          ref_index=ref_index,
          title=node.text[:500],
          url=urls[0] if urls else "",
          source=_infer_source_from_title(node.text),
          desc=node.content_desc,
          resource_id=node.resource_id,
          bounds=list(node.bounds),
          group=group_key,
        ),
      )
    )

  thinking_parts.sort(key=lambda x: x[0])
  ref_candidates.sort(key=lambda x: (x[0], x[1].ref_index))
  references = [c for _, c in ref_candidates]

  groups: list[ThinkingSearchGroup] = []
  search_groups: list[str] = []
  for title, gy in sorted(group_titles, key=lambda x: x[1]):
    gkey = f"{title}|{gy}"
    if gkey in search_groups:
      continue
    search_groups.append(gkey)
    groups.append(
      ThinkingSearchGroup(
        title=title,
        key=gkey,
        keywords=keywords_by_group.get(title, ""),
        references=[r for r in references if r.group == gkey],
      )
    )

  paragraphs = [t for _, t in thinking_parts]
  return ParsedThinkingPanel(
    header=header,
    thinking_paragraphs=paragraphs,
    thinking_body="\n\n".join(paragraphs).strip(),
    search_groups=search_groups,
    groups=groups,
    references=references,
  )


def _is_header_only_group(title: str) -> bool:
  return bool(title) and "已完成思考" in title and "搜索" not in title


def render_thinking_markdown(panel: ParsedThinkingPanel) -> str:
  """将思考面板渲染为结构化 markdown。"""
  lines: list[str] = []
  header = (panel.header or "").strip()
  rendered_group_titles: set[str] = set()

  group_headings: list[str] = []
  for grp in panel.groups or []:
    heading = (grp.title or grp.key.split("|", 1)[0]).strip()
    if _is_header_only_group(heading):
      continue
    if heading and heading not in group_headings:
      group_headings.append(heading)

  if header and (not group_headings or header != group_headings[0]):
    lines.append(f"## {header}")
    lines.append("")

  if panel.thinking_paragraphs:
    lines.append("### 思考过程")
    lines.append("")
    header_key = _norm_text_key(header)
    keyword_keys = {
      _norm_text_key(grp.keywords)
      for grp in (panel.groups or [])
      if grp.keywords
    }
    group_title_keys = {
      _norm_text_key(grp.title or grp.key.split("|", 1)[0])
      for grp in (panel.groups or [])
    }
    for para in panel.thinking_paragraphs:
      pkey = _norm_text_key(para)
      if pkey and (pkey == header_key or pkey in keyword_keys or pkey in group_title_keys):
        continue
      lines.append(para)
      lines.append("")
  elif panel.thinking_body:
    lines.append("### 思考过程")
    lines.append("")
    lines.append(panel.thinking_body)
    lines.append("")

  if panel.groups:
    for grp in panel.groups:
      heading = grp.title or grp.key.split("|", 1)[0]
      if _is_header_only_group(heading):
        continue
      if heading in rendered_group_titles:
        continue
      rendered_group_titles.add(heading)
      lines.append(f"### {heading}")
      lines.append("")
      if grp.keywords:
        lines.append(f"**搜索关键词：** {grp.keywords}")
        lines.append("")
      if grp.references:
        lines.append("**参考资料：**")
        lines.append("")
        for ref in grp.references:
          idx = f"{ref.ref_index}. " if ref.ref_index else "- "
          src = f"（{ref.source}）" if ref.source else ""
          url = f" — {ref.url}" if ref.url else ""
          lines.append(f"{idx}{ref.title}{src}{url}")
        lines.append("")
  elif panel.references:
    lines.append("### 参考资料")
    lines.append("")
    for ref in panel.references:
      idx = f"{ref.ref_index}. " if ref.ref_index else "- "
      lines.append(f"{idx}{ref.title}")
    lines.append("")

  return "\n".join(lines).strip() + "\n"
