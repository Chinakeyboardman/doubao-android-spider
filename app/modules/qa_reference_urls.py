# -*- coding: utf-8 -*-
"""
思考引用真链接解析：logcat（快） / dumpsys（保底） / 网络抓包（零点击，见 qa_reference_net）。

豆包 hierarchy 不含链接；点击后 Intent 出现在 logcat events 或 dumpsys activity。
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any, Literal

from dataclasses import dataclass

from app.config.gesture_profile import GestureProfile, _default_qa_ref_list_probe_xpaths
from app.modules.chat_ui_heuristics import display_wh
from app.modules.navigator import Navigator, PACKAGE
from app.modules.qa_hierarchy import (
  Citation,
  REFERENCE_CONTENT_RID,
  REFERENCE_INDEX_RID,
  SEARCH_REF_LIST_RID,
  SOURCE_ITEM_RID,
  SUB_KEYWORD_REFERENCE_RID,
  parse_thinking_panel,
)
from capture.utils.capture_logcat import LogcatStream, clear_logcat, dump_logcat_tail
from app.modules.ui_node_click import (
  row_index_only_confirmed,
  scroll_citation_index_into_view,
)

_LINK_URL_RE = re.compile(r"link_url=(https?://[^\s,}\]]+)")
_HTTP_RE = re.compile(r"https?://[^\s\"'<>\\,}\]]+", re.I)
_DAT_HTTP_RE = re.compile(r"dat=(https?://[^\s,}\]]+)", re.I)
_SNSSDK_AWEME_RE = re.compile(
  r"snssdk1128://aweme/detail/(\d+)",
  re.I,
)
_START_HTTP_RE = re.compile(
  r"START\s+u\d+\s+\{[^}]*?(https?://[^\s,}\]]+)",
  re.I,
)
_SKIP_URL_MARKERS = (
  "schemas.android.com",
  "android.com",
  "localhost",
  "example.com",
)
_PKG = "com.larus.nova"


def _log_url(msg: str) -> None:
  print(f"[URL] {msg}")


def _format_bounds(bounds: list[int] | tuple[int, ...] | None) -> str:
  if not bounds or len(bounds) != 4:
    return "无"
  return f"[{bounds[0]},{bounds[1]},{bounds[2]},{bounds[3]}]"


def _log_page(nav: Navigator, tag: str) -> None:
  from app.modules.navigator import Page

  page, cur = nav.current_page()
  act = (cur.get("activity") or "").rsplit(".", 1)[-1]
  pkg = cur.get("package") or ""
  _log_url(f"{tag} 页面={page.name} pkg={pkg.split('.')[-1] if pkg else '?'} act={act}")


def _detect_ref_list_root_xpath(
  device: Any,
  profile: GestureProfile | None = None,
) -> str:
  """检测当前屏引用列表容器（think 面板或 fast 内联 sub_keyword_reference）。"""
  p = profile or GestureProfile()
  selectors = p.qa_ref_list_probe_xpaths or _default_qa_ref_list_probe_xpaths()
  for sel in selectors:
    try:
      if device.xpath(sel).exists:
        return sel
    except Exception:
      continue
  return selectors[0]


def _ref_list_root_xpath(
  device: Any | None = None,
  profile: GestureProfile | None = None,
) -> str:
  if device is not None:
    return _detect_ref_list_root_xpath(device, profile)
  p = profile or GestureProfile()
  probes = p.qa_ref_list_probe_xpaths
  return probes[0] if probes else _default_qa_ref_list_probe_xpaths()[0]


def _scope_xpath_to_ref_list(xpath: str, *, root_xpath: str | None = None) -> str:
  """将 xpath 限定在引用列表容器内，避免误点正文/视频。"""
  root = root_xpath or f'//*[@resource-id="{SEARCH_REF_LIST_RID}"]'
  if xpath.startswith("//"):
    return f"{root}{xpath[1:]}"
  return f"{root}//{xpath}"


def _point_in_bounds(x: int, y: int, bounds: list[int], *, margin: int = 6) -> bool:
  x1, y1, x2, y2 = bounds
  return (x1 - margin) <= x <= (x2 + margin) and (y1 - margin) <= y <= (y2 + margin)


def _bounds_center_in_ref_list(
  bounds: list[int] | tuple[int, ...],
  ref_bounds: list[int] | None,
) -> bool:
  if not ref_bounds or len(bounds) != 4:
    return ref_bounds is None
  cx = (bounds[0] + bounds[2]) // 2
  cy = (bounds[1] + bounds[3]) // 2
  return _point_in_bounds(cx, cy, ref_bounds)


def _reanchor_ref_list_after_back(
  device: Any,
  profile: GestureProfile,
  nav: Navigator,
  *,
  tag: str,
) -> None:
  """返回聊天页后把引用列表滚回顶部，避免落在底部误点视频。"""
  _log_page(nav, f"{tag} 返回后")
  ref_bounds = _get_ref_list_bounds(device, profile)
  _log_url(f"{tag} 引用列表 bounds={_format_bounds(ref_bounds)}")
  if ref_bounds:
    _scroll_ref_list_to_top(device, profile, rounds=5)
    time.sleep(0.35)
    ref_bounds = _get_ref_list_bounds(device, profile)
    _log_url(f"{tag} 列表回顶后 bounds={_format_bounds(ref_bounds)}")
  else:
    _log_url(f"{tag} 无引用列表容器，跳过列表回顶")


@dataclass
class CitationClickTarget:
  """精确识别后的可点击引用目标（禁止裸坐标点击）。"""

  strategy: str
  xpath: str
  click_rid: str
  ref_index: int
  index_text: str
  title_text: str
  content_desc: str
  clazz: str
  clickable: bool
  bounds: list[int]
  element: Any


def _element_fingerprint(el: Any) -> dict[str, Any]:
  info = getattr(el, "info", None) or {}
  bounds = getattr(el, "bounds", None)
  bl: list[int] = []
  if bounds:
    bl = [int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])]
  rid = str(info.get("resourceName") or info.get("resourceId") or "")
  return {
    "rid": rid,
    "text": str(info.get("text") or "")[:100],
    "desc": str(info.get("contentDescription") or "")[:100],
    "clazz": str(info.get("className") or "").rsplit(".", 1)[-1],
    "clickable": bool(info.get("clickable")),
    "bounds": bl,
  }


def _log_element(tag: str, fp: dict[str, Any]) -> None:
  _log_url(
    f"{tag} rid={fp['rid']} class={fp['clazz']} clickable={fp['clickable']} "
    f"bounds={_format_bounds(fp['bounds'])} "
    f"text={fp['text'][:72]!r} desc={fp['desc'][:48]!r}"
  )


def _rid_matches(rid: str, full_rid: str) -> bool:
  token = full_rid.rsplit("/", 1)[-1]
  return token in rid or full_rid in rid


def _log_visible_ref_rows(
  device: Any,
  *,
  expect_index: int = 0,
  root_xpath: str | None = None,
) -> None:
  """打印当前屏引用列表内可见行，便于对照序号/标题。"""
  root = root_xpath or _detect_ref_list_root_xpath(device)
  try:
    rows = device.xpath(
      f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]'
    ).all()
  except Exception as exc:
    _log_url(f"列举引用行失败: {exc}")
    return
  _log_url(f"引用列表可见行数={len(rows)} 目标=#{expect_index or '?'}")
  for i, row in enumerate(rows[:12], start=1):
    fp = _element_fingerprint(row)
    idx_text, title_text = _read_row_index_and_title(device, row, root_xpath=root)
    title_text = title_text[:60]
    mark = " <<" if expect_index and idx_text.rstrip(".") == str(expect_index) else ""
    _log_url(
      f"  行{i}{mark} index={idx_text!r} title={title_text!r} "
      f"bounds={_format_bounds(fp['bounds'])}"
    )


def _read_row_index_and_title(
  device: Any,
  row_el: Any,
  *,
  root_xpath: str | None = None,
) -> tuple[str, str]:
  """从引用行读取序号与标题（优先 bounds 对齐，兼容 RecyclerView 子节点不可相对 xpath）。"""
  fp = _element_fingerprint(row_el)
  bounds = fp.get("bounds") or []
  if len(bounds) == 4:
    root = root_xpath or _detect_ref_list_root_xpath(device)
    y1, y2 = bounds[1], bounds[3]
    y_mid = (y1 + y2) // 2
    idx_text = ""
    title_text = ""
    try:
      for node in device.xpath(f'{root}//*[@resource-id="{REFERENCE_INDEX_RID}"]').all():
        nb = node.bounds
        if not nb:
          continue
        cy = (int(nb[1]) + int(nb[3])) // 2
        if y1 - 8 <= cy <= y2 + 8:
          idx_text = str((node.info or {}).get("text") or "").strip()
          if idx_text:
            break
    except Exception:
      pass
    try:
      best = ""
      best_dist = 10_000
      for node in device.xpath(f'{root}//*[@resource-id="{REFERENCE_CONTENT_RID}"]').all():
        nb = node.bounds
        if not nb:
          continue
        cy = (int(nb[1]) + int(nb[3])) // 2
        if y1 - 8 <= cy <= y2 + 8:
          text = str((node.info or {}).get("text") or "").strip()
          dist = abs(cy - y_mid)
          if text and dist < best_dist:
            best = text
            best_dist = dist
      title_text = best
    except Exception:
      pass
    if idx_text or title_text:
      return idx_text, title_text

  idx_text = ""
  title_text = ""
  try:
    idx_el = row_el.xpath(f'.//*[@resource-id="{REFERENCE_INDEX_RID}"]').get(timeout=0.2)
    if idx_el:
      idx_text = str((idx_el.info or {}).get("text") or "").strip()
  except Exception:
    pass
  try:
    title_el = row_el.xpath(f'.//*[@resource-id="{REFERENCE_CONTENT_RID}"]').get(timeout=0.2)
    if title_el:
      title_text = str((title_el.info or {}).get("text") or "").strip()
  except Exception:
    pass
  return idx_text, title_text


def _trust_from_strategy(
  strategy: str,
  citation: Citation,
  index_text: str,
  title_text: str,
) -> tuple[str, str]:
  """xpath 策略已命中时，补全读不到的序号/标题（避免 RecyclerView 子节点读空）。"""
  idx = citation.ref_index or 0
  if idx > 0 and not index_text and strategy.startswith("row_index"):
    index_text = f"{idx}."
  if not title_text and citation.title:
    if strategy.startswith("row_index_title") or strategy == "index_sibling_content":
      title_text = citation.title
  return index_text, title_text


def _title_matches(expected: str, actual: str) -> bool:
  exp = _collapse_ws(expected)
  act = _collapse_ws(actual)
  if not exp or not act:
    return False
  if exp == act or exp in act or act in exp:
    return True
  for n in (24, 16, 10):
    if len(exp) >= n and exp[:n] in act:
      return True
  return False


def _validate_citation_match(
  citation: Citation,
  *,
  index_text: str,
  title_text: str,
  fp: dict[str, Any],
  ref_bounds: list[int] | None,
) -> bool:
  if ref_bounds and fp["bounds"] and not _bounds_center_in_ref_list(fp["bounds"], ref_bounds):
    _log_url(
      f"校验失败 #{citation.ref_index or '?'}：元素不在引用列表内 "
      f"el={_format_bounds(fp['bounds'])} list={_format_bounds(ref_bounds)}"
    )
    return False
  if citation.ref_index > 0:
    want = f"{citation.ref_index}."
    if index_text and index_text != want:
      _log_url(
        f"校验失败 #{citation.ref_index}：序号不匹配 "
        f"期望={want!r} 实际={index_text!r} title={title_text[:40]!r}"
      )
      return False
  if citation.title and title_text and not _title_matches(citation.title, title_text):
    _log_url(
      f"校验失败 #{citation.ref_index or '?'}：标题不匹配 "
      f"期望={citation.title[:40]!r} 实际={title_text[:40]!r}"
    )
    return False
  allowed = (SOURCE_ITEM_RID, REFERENCE_CONTENT_RID, REFERENCE_INDEX_RID)
  if not any(_rid_matches(fp["rid"], rid) for rid in allowed):
    _log_url(
      f"校验失败 #{citation.ref_index or '?'}：resource-id 非引用条目 "
      f"rid={fp['rid']}"
    )
    return False
  return True


def _pick_clickable_element(
  device: Any,
  citation: Citation,
  hit_el: Any,
  *,
  root_xpath: str | None = None,
) -> tuple[Any | None, str]:
  """优先点击 ll_source_item 整行，其次可点击的 content。"""
  fp = _element_fingerprint(hit_el)
  if _rid_matches(fp["rid"], SOURCE_ITEM_RID):
    return hit_el, SOURCE_ITEM_RID

  if citation.ref_index > 0:
    row_xp = _scope_xpath_to_ref_list(
      f'//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{citation.ref_index}."]/ancestor::*'
      f'[@resource-id="{SOURCE_ITEM_RID}"][1]',
      root_xpath=root_xpath,
    )
    try:
      row = device.xpath(row_xp).get(timeout=0.35)
      if row:
        return row, SOURCE_ITEM_RID
    except Exception:
      pass

  if _rid_matches(fp["rid"], REFERENCE_CONTENT_RID):
    if fp["clickable"]:
      return hit_el, REFERENCE_CONTENT_RID
    parent_xp = (
      f'./ancestor::*[@resource-id="{SOURCE_ITEM_RID}"][1]'
    )
    try:
      row = hit_el.xpath(parent_xp).get(timeout=0.25)
      if row:
        return row, SOURCE_ITEM_RID
    except Exception:
      pass
    try:
      content = hit_el.xpath(
        f'.//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
      ).get(timeout=0.2)
      if content and (content.info or {}).get("clickable"):
        return content, REFERENCE_CONTENT_RID
    except Exception:
      pass

  if _rid_matches(fp["rid"], SOURCE_ITEM_RID):
    try:
      content = hit_el.xpath(
        f'.//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
      ).get(timeout=0.2)
      if content:
        return content, REFERENCE_CONTENT_RID
    except Exception:
      pass

  if fp["clickable"]:
    return hit_el, fp["rid"]
  return None, ""


def _citation_xpath_strategies(
  citation: Citation,
  device: Any | None = None,
  profile: GestureProfile | None = None,
) -> list[tuple[str, str]]:
  """按精确度排序的 xpath 策略（限定在当前引用列表容器内）。"""
  out: list[tuple[str, str]] = []
  seen: set[str] = set()
  idx = citation.ref_index or 0
  raw = (citation.title or "").strip()
  root = _ref_list_root_xpath(device, profile)

  def _add(name: str, xp: str) -> None:
    if xp not in seen:
      seen.add(xp)
      out.append((name, xp))

  if idx > 0:
    for n in (24, 18, 12):
      chunk = _xpath_escape(raw[:n])
      if len(chunk) >= 4:
        _add(
          f"row_index_title_{n}",
          f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]'
          f'[.//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{idx}."]'
          f' and .//*[@resource-id="{REFERENCE_CONTENT_RID}"'
          f' and contains(@text,"{chunk}")]]',
        )
    _add(
      "row_index_only",
      f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]'
      f'[.//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{idx}."]]',
    )
    _add(
      "index_sibling_content",
      f'{root}//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{idx}."]'
      f'/following-sibling::*[@resource-id="{REFERENCE_CONTENT_RID}"]',
    )

  for name, xp in [
    (n, _scope_xpath_to_ref_list(v, root_xpath=root))
    for n, v in [
      (f"title_{i}", xp)
      for i, xp in enumerate(_title_xpath_variants(raw, idx))
    ]
  ]:
    _add(name, xp)
  return out


def _find_citation_click_target(
  device: Any,
  citation: Citation,
  *,
  log: bool = False,
  profile: GestureProfile | None = None,
) -> CitationClickTarget | None:
  root_xpath = _detect_ref_list_root_xpath(device, profile)
  ref_bounds = _get_ref_list_bounds(device, profile)
  if log:
    _log_url(
      f"查找目标 #{citation.ref_index or '?'} title={citation.title[:48]!r} "
      f"root={root_xpath[-48:]} list={_format_bounds(ref_bounds)}"
    )
    _log_visible_ref_rows(device, expect_index=citation.ref_index or 0, root_xpath=root_xpath)

  for strategy, xp in _citation_xpath_strategies(citation, device, profile):
    try:
      el = device.xpath(xp).get(timeout=0.45)
    except Exception:
      el = None
    if not el:
      if log:
        _log_url(f"策略未命中 {strategy}")
      continue

    click_el, click_rid = _pick_clickable_element(
      device, citation, el, root_xpath=root_xpath,
    )
    if not click_el:
      if log:
        fp = _element_fingerprint(el)
        _log_element(f"策略 {strategy} 命中但不可点击", fp)
      continue

    fp = _element_fingerprint(click_el)
    index_text = ""
    title_text = ""
    if _rid_matches(fp["rid"], SOURCE_ITEM_RID):
      index_text, title_text = _read_row_index_and_title(
        device, click_el, root_xpath=root_xpath,
      )
    elif _rid_matches(fp["rid"], REFERENCE_CONTENT_RID):
      title_text = fp["text"]
      if citation.ref_index > 0:
        row_xp = _scope_xpath_to_ref_list(
          f'//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{citation.ref_index}."]',
          root_xpath=root_xpath,
        )
        try:
          idx_el = device.xpath(row_xp).get(timeout=0.2)
          if idx_el:
            index_text = str((idx_el.info or {}).get("text") or "")
        except Exception:
          pass

    index_text, title_text = _trust_from_strategy(
      strategy, citation, index_text, title_text,
    )
    if _rid_matches(fp["rid"], SOURCE_ITEM_RID) and not title_text and strategy == "row_index_only":
      confirmed, detail = row_index_only_confirmed(
        device,
        root_xpath=root_xpath,
        ref_index=citation.ref_index,
        expected_title=citation.title or "",
      )
      if not confirmed:
        if log:
          _log_url(f"策略 {strategy} 二次确认失败: {detail}")
        continue
      title_text = detail if isinstance(detail, str) and detail else title_text
      if log:
        _log_url(f"策略 {strategy} 二次确认通过 title={title_text[:40]!r}")

    if not _validate_citation_match(
      citation,
      index_text=index_text,
      title_text=title_text,
      fp=fp,
      ref_bounds=ref_bounds,
    ):
      continue

    if log:
      _log_url(f"策略命中 {strategy} xpath={xp[:96]}")
      _log_element("  命中节点", _element_fingerprint(el))
      _log_element("  点击节点", fp)

    return CitationClickTarget(
      strategy=strategy,
      xpath=xp,
      click_rid=click_rid,
      ref_index=citation.ref_index,
      index_text=index_text,
      title_text=title_text[:120],
      content_desc=fp["desc"],
      clazz=fp["clazz"],
      clickable=fp["clickable"],
      bounds=fp["bounds"],
      element=click_el,
    )

  if log:
    _log_url(
      f"未找到可点击引用 #{citation.ref_index or '?'} "
      f"title={citation.title[:40]!r}"
    )
  return None


def _find_citation_element(
  device: Any,
  citation: Citation,
  *,
  log: bool = False,
  profile: GestureProfile | None = None,
) -> tuple[Any | None, str]:
  target = _find_citation_click_target(device, citation, log=log, profile=profile)
  if not target:
    return None, ""
  return target.element, target.xpath


ResolveMethod = Literal["auto", "logcat", "dumpsys", "net"]
CitationChannel = Literal["douyin", "web", "unknown"]

_CHANNEL_RESOLVE_ORDER: dict[CitationChannel, int] = {
  "web": 0,
  "douyin": 1,
  "unknown": 2,
}


def _device_serial(device: Any) -> str | None:
  serial = getattr(device, "serial", None)
  if serial:
    return str(serial)
  info = getattr(device, "device_info", None) or {}
  return info.get("serial") or info.get("udid")


def _adb_dumpsys(serial: str | None, *args: str) -> str:
  cmd = ["adb"]
  if serial:
    cmd.extend(["-s", serial])
  cmd.extend(["shell", "dumpsys", *args])
  try:
    return subprocess.check_output(cmd, text=True, errors="ignore")
  except (subprocess.CalledProcessError, FileNotFoundError, OSError):
    return ""


def _iesdouyin_url(video_id: str) -> str:
  return f"https://www.iesdouyin.com/share/video/{video_id}"


def extract_urls_from_dumpsys_text(text: str) -> list[str]:
  """从 dumpsys activity 文本提取候选 HTTP URL（去重保序）。"""
  seen: set[str] = set()
  out: list[str] = []
  for m in _LINK_URL_RE.finditer(text or ""):
    url = m.group(1).rstrip(".,)")
    if url not in seen:
      seen.add(url)
      out.append(url)
  for m in _HTTP_RE.finditer(text or ""):
    url = m.group(0).rstrip(".,)")
    if any(skip in url for skip in _SKIP_URL_MARKERS):
      continue
    if url not in seen:
      seen.add(url)
      out.append(url)
  return out


def extract_urls_from_logcat_text(text: str) -> list[str]:
  """
  从 logcat events/main/system 文本提取 URL。

  按在文本中出现的位置排序；pick_best_url 取最后一次命中（最近一次跳转）。
  """
  if not text:
    return []
  hits: list[tuple[int, str]] = []

  def _add(pos: int, url: str) -> None:
    url = url.rstrip(".,)")
    if not url or any(skip in url for skip in _SKIP_URL_MARKERS):
      return
    hits.append((pos, url))

  for m in _LINK_URL_RE.finditer(text):
    _add(m.start(), m.group(1))
  for m in _DAT_HTTP_RE.finditer(text):
    _add(m.start(), m.group(1))
  for m in _START_HTTP_RE.finditer(text):
    _add(m.start(), m.group(1))
  for m in _SNSSDK_AWEME_RE.finditer(text):
    _add(m.start(), _iesdouyin_url(m.group(1)))
  for m in _HTTP_RE.finditer(text):
    _add(m.start(), m.group(0).rstrip(".,)"))

  if not hits:
    return []
  hits.sort(key=lambda x: x[0])
  seen: set[str] = set()
  ordered: list[str] = []
  for _, url in hits:
    if url not in seen:
      seen.add(url)
      ordered.append(url)
  return ordered


def extract_urls_from_dumpsys(serial: str | None = None) -> list[str]:
  text = _adb_dumpsys(serial, "activity", "activities")
  text += "\n" + _adb_dumpsys(serial, "activity", "top")
  return extract_urls_from_dumpsys_text(text)


def pick_best_url(urls: list[str], *, prefer_last: bool = False) -> str:
  if not urls:
    return ""
  https = [u for u in urls if u.startswith("http")]
  if not https:
    return ""
  return https[-1] if prefer_last else https[0]


def extract_aweme_ids_ordered(text: str) -> list[str]:
  """按出现顺序提取 aweme/detail 视频 id（去重保序）。"""
  if not text:
    return []
  seen: set[str] = set()
  out: list[str] = []
  for m in _SNSSDK_AWEME_RE.finditer(text):
    vid = m.group(1)
    if vid not in seen:
      seen.add(vid)
      out.append(vid)
  return out


def validate_batch_douyin_ids(ids: list[str], expected_count: int) -> bool:
  """批量回填前校验：数量一致且 id 互不重复。"""
  if expected_count <= 0:
    return False
  if len(ids) != expected_count:
    return False
  return len(ids) == len(set(ids))


def is_likely_douyin_citation(citation: Citation) -> bool:
  """启发式判断引用是否更可能为抖音视频（非网页）。"""
  src = (citation.source or "").lower()
  if "抖音" in src or "douyin" in src:
    return True
  title = citation.title or ""
  if citation.url and "iesdouyin.com" in citation.url:
    return True
  if "#" in title and not citation.source:
    return True
  lowered = title.lower()
  if any(k in lowered for k in ("douyin", "aweme", "抖音视频")):
    return True
  return False


# 标题/来源含下列特征时一律按网页处理，避免误跳过 URL 解析
_WEB_CITATION_MARKERS = (
  "官网",
  "官方网站",
  ".com",
  ".cn",
  "it之家",
  "ithome",
  "中关村",
  "zol",
  "泡泡网",
  "太平洋",
  "知乎",
  "百科",
  "jd.com",
  "淘宝",
  "tmall",
  "参数",
  "报价",
  "评测",
  "导购",
)


def looks_like_web_citation(citation: Citation) -> bool:
  """启发式：更像网页资讯而非抖音短视频（用于 skip 决策，防误伤）。"""
  title = (citation.title or "").strip()
  src = (citation.source or "").strip()
  blob = f"{title} {src}".lower()
  if any(m in blob for m in _WEB_CITATION_MARKERS):
    return True
  if src and "抖音" not in src and "douyin" not in src.lower():
    if any(k in src for k in ("网", "科技", "之家", "在线", "论坛")):
      return True
  return False


def classify_citation_channel(citation: Citation) -> CitationChannel:
  """
  引用渠道分类（决定先用哪种技术手段，未命中再走笨办法逐条点击）。

  - web：标题/来源具网页特征，优先 logcat/dumpsys 解析 Intent
  - douyin：高置信短视频，可走批量 aweme id 或逐条解析
  - unknown：无规则，仅能通过点击+抓包笨办法尝试
  """
  if looks_like_web_citation(citation):
    return "web"
  if is_likely_douyin_citation(citation):
    return "douyin"
  return "unknown"


def should_skip_douyin_url_resolve(
  citation: Citation,
  profile: GestureProfile,
) -> bool:
  """
  是否跳过该引用的逐条点击 URL 解析。

  仅当「关闭抖音批量且明确只要网页 URL」时跳过高置信抖音条目；
  开启批量后逐条 logcat/dumpsys 作为兜底，不再跳过。
  """
  if not profile.qa_resolve_skip_douyin_per_click:
    return False
  if profile.qa_resolve_batch_douyin:
    return False
  if looks_like_web_citation(citation):
    return False
  return is_likely_douyin_citation(citation)


def poll_logcat_stream_for_url(
  stream: LogcatStream,
  *,
  timeout_s: float = 1.5,
  poll_interval_s: float = 0.2,
) -> str:
  """从常驻 logcat 流 mark 之后轮询第一条 URL。"""
  deadline = time.time() + timeout_s
  while time.time() < deadline:
    urls = extract_urls_from_logcat_text(stream.text_since_mark())
    url = pick_best_url(urls, prefer_last=False)
    if url:
      return url
    time.sleep(poll_interval_s)
  return ""


def poll_logcat_stream_for_aweme_ids(
  stream: LogcatStream,
  *,
  expected_count: int,
  timeout_s: float = 4.0,
  poll_interval_s: float = 0.25,
) -> list[str]:
  """从常驻流等待批量 aweme id，数量校验通过即返回。"""
  deadline = time.time() + timeout_s
  best: list[str] = []
  while time.time() < deadline:
    ids = extract_aweme_ids_ordered(stream.text_since_mark())
    if len(ids) > len(best):
      best = ids
    if validate_batch_douyin_ids(ids, expected_count):
      return ids
    time.sleep(poll_interval_s)
  return best if validate_batch_douyin_ids(best, expected_count) else []


def apply_batch_douyin_urls(citations: list[Citation], indices: list[int], ids: list[str]) -> None:
  """按 citations 列表顺序将批量 id 写回对应条目。"""
  for idx, vid in zip(indices, ids):
    citations[idx].url = _iesdouyin_url(vid)


def _chat_context_ok(
  device: Any,
  expected_prompt: str,
  profile: GestureProfile,
  tag: str,
) -> bool:
  """
  URL 解析期会话校验（宽松）：仅当屏上出现另一条不同提问时判定错位。

  引用解析途中问题气泡本就滚出屏幕，读不到用户气泡不算证据，避免误杀空转。
  """
  if not (expected_prompt or "").strip():
    return True
  from app.modules.chat_ui_heuristics import chat_prompt_conflicts

  conflict, visible = chat_prompt_conflicts(
    device, expected_prompt, profile=profile,
  )
  if not conflict:
    return True
  print(
    f"[问答] URL解析会话错位({tag})：期望 {expected_prompt[:40]!r}，"
    f"屏上 {visible[:40]!r}（疑似落入历史会话）"
  )
  return False


def try_batch_resolve_douyin(
  device: Any,
  citations: list[Citation],
  *,
  nav: Navigator,
  profile: GestureProfile,
  stream: LogcatStream,
) -> bool:
  """
  点开首条抖音引用进入 feed，从 logcat 批量抓齐 aweme id 并按序回填。
  校验不通过返回 False，由调用方回落逐条解析。
  """
  douyin_indices = [
    i for i, c in enumerate(citations) if not c.url and is_likely_douyin_citation(c)
  ]
  if len(douyin_indices) < 2:
    return False

  first = citations[douyin_indices[0]]
  if not _ensure_citation_visible(device, first, profile):
    print("[问答] 抖音批量：首条引用不可见")
    return False

  stream.mark()
  _refresh_citation_bounds(device, first, profile=profile)
  if not _click_citation(device, first, profile=profile):
    print("[问答] 抖音批量：首条引用点击失败")
    return False

  ids = poll_logcat_stream_for_aweme_ids(
    stream,
    expected_count=len(douyin_indices),
    timeout_s=profile.qa_resolve_batch_douyin_timeout,
    poll_interval_s=profile.qa_logcat_stream_poll_interval,
  )
  _log_url(f"抖音批量 logcat ids={len(ids)} 期望={len(douyin_indices)}")
  nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  time.sleep(profile.qa_resolve_url_post_back_sleep)
  _reanchor_ref_list_after_back(device, profile, nav, tag="抖音批量后")

  if validate_batch_douyin_ids(ids, len(douyin_indices)):
    apply_batch_douyin_urls(citations, douyin_indices, ids)
    print(f"[问答] 抖音批量回填 {len(douyin_indices)} 条引用 URL")
    return True

  if len(ids) >= 2:
    apply_count = min(len(ids), len(douyin_indices))
    apply_batch_douyin_urls(citations, douyin_indices[:apply_count], ids[:apply_count])
    print(
      f"[问答] 抖音批量部分回填 {apply_count}/{len(douyin_indices)} 条"
      f"（剩余走逐条 logcat/dumpsys）"
    )
    return False

  print(
    f"[问答] 抖音批量校验失败: 期望 {len(douyin_indices)} 条，"
    f"实际 {len(ids)} 条 distinct id"
  )
  return False


def poll_logcat_for_url(
  *,
  serial: str | None,
  timeout_s: float = 1.5,
  poll_interval_s: float = 0.2,
) -> str:
  """点击后轮询 logcat 尾部，命中 URL 即返回（取第一条，避免历史残留）。"""
  deadline = time.time() + timeout_s
  while time.time() < deadline:
    text = dump_logcat_tail(serial=serial, count=80)
    urls = extract_urls_from_logcat_text(text)
    url = pick_best_url(urls, prefer_last=False)
    if url:
      return url
    time.sleep(poll_interval_s)
  return ""


def resolve_url_via_logcat(
  device: Any,
  *,
  serial: str | None = None,
  poll_timeout_s: float = 1.5,
  poll_interval_s: float = 0.2,
) -> str:
  """点击后从 logcat 快速取 URL（不等页面完全加载）。"""
  serial = serial or _device_serial(device)
  return poll_logcat_for_url(
    serial=serial,
    timeout_s=poll_timeout_s,
    poll_interval_s=poll_interval_s,
  )


def resolve_url_via_dumpsys(
  device: Any,
  *,
  serial: str | None = None,
  wait_s: float = 2.0,
) -> str:
  """点击后从 dumpsys 取 URL（保底，较慢）。"""
  time.sleep(wait_s)
  serial = serial or _device_serial(device)
  urls = extract_urls_from_dumpsys(serial)
  url = pick_best_url(urls, prefer_last=True)
  if url:
    return url
  try:
    cur = device.app_current() or {}
  except Exception:
    return ""
  activity = cur.get("activity", "")
  if "WebActivity" in activity and PACKAGE in cur.get("package", ""):
    try:
      xml = device.dump_hierarchy(compressed=False) or ""
    except Exception:
      xml = ""
    return pick_best_url(extract_urls_from_dumpsys_text(xml), prefer_last=True)
  return ""


def resolve_url_after_click(
  device: Any,
  *,
  serial: str | None = None,
  method: ResolveMethod = "logcat",
  profile: GestureProfile | None = None,
) -> str:
  """按 method 从 logcat / dumpsys 解析单条引用 URL。"""
  p = profile or GestureProfile()
  serial = serial or _device_serial(device)
  if method == "dumpsys":
    return resolve_url_via_dumpsys(device, serial=serial, wait_s=p.qa_resolve_url_wait)
  if method == "logcat":
    return resolve_url_via_logcat(
      device,
      serial=serial,
      poll_timeout_s=p.qa_resolve_logcat_poll_timeout,
      poll_interval_s=p.qa_resolve_logcat_poll_interval,
    )
  # auto: logcat 优先，未命中回落 dumpsys
  url = resolve_url_via_logcat(
    device,
    serial=serial,
    poll_timeout_s=p.qa_resolve_logcat_poll_timeout,
    poll_interval_s=p.qa_resolve_logcat_poll_interval,
  )
  if url:
    return url
  return resolve_url_via_dumpsys(device, serial=serial, wait_s=1.5)


def _collapse_ws(text: str) -> str:
  return re.sub(r"\s+", "", (text or "").strip())


def _xpath_escape(text: str) -> str:
  return (text or "").replace('"', "").replace("'", "")


def _title_xpath(title: str) -> str:
  chunk = _xpath_escape((title or "").strip()[:24])
  if not chunk:
    return f'//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
  return (
    f'//*[@resource-id="{REFERENCE_CONTENT_RID}" and contains(@text,"{chunk}")]'
  )


def _title_xpath_variants(title: str, ref_index: int = 0) -> list[str]:
  """多策略匹配引用标题（含序号兜底）。"""
  variants: list[str] = []
  seen: set[str] = set()

  def _add(xp: str) -> None:
    if xp and xp not in seen:
      seen.add(xp)
      variants.append(xp)

  raw = (title or "").strip()
  for n in (28, 20, 14, 10):
    chunk = _xpath_escape(raw[:n])
    if len(chunk) >= 4:
      _add(
        f'//*[@resource-id="{REFERENCE_CONTENT_RID}" and contains(@text,"{chunk}")]'
      )
  collapsed = _collapse_ws(raw)
  for n in (18, 12, 8):
    chunk = _xpath_escape(collapsed[:n])
    if len(chunk) >= 4:
      _add(
        f'//*[@resource-id="{REFERENCE_CONTENT_RID}" and contains(@text,"{chunk}")]'
      )
  if ref_index > 0:
    _add(
      f'//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{ref_index}."]'
      f'/following-sibling::*[@resource-id="{REFERENCE_CONTENT_RID}"]'
    )
    _add(
      f'//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{ref_index}."]'
      f'/..//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
    )
  return variants or [_title_xpath(title)]


def _viewport_y_band(h: int, profile: GestureProfile | None = None) -> tuple[int, int]:
  p = profile or GestureProfile()
  return int(h * p.qa_resolve_viewport_y0), int(h * p.qa_resolve_viewport_y1)


def _get_ref_list_bounds(
  device: Any,
  profile: GestureProfile | None = None,
) -> list[int] | None:
  try:
    el = device.xpath(_detect_ref_list_root_xpath(device, profile)).get(timeout=0.5)
    if not el:
      return None
    b = el.bounds
    if not b:
      return None
    return [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
  except Exception:
    return None


def _scroll_ref_list(
  device: Any,
  profile: GestureProfile,
  direction: str,
) -> bool:
  """在引用列表容器区域内滚动，避免整页聊天被滑走。"""
  bounds = _get_ref_list_bounds(device, profile)
  w, h = display_wh(device, profile=profile)
  if bounds:
    x1, y1, x2, y2 = bounds
    cx = (x1 + x2) // 2
    span = max(y2 - y1, 80)
    if direction == "up":
      device.swipe(cx, y1 + int(span * 0.28), cx, y1 + int(span * 0.78), 0.22)
    else:
      device.swipe(cx, y1 + int(span * 0.78), cx, y1 + int(span * 0.28), 0.22)
    return True
  if direction == "up":
    return _scroll_chat_up(device, profile)
  return _scroll_chat_down(device, profile)


def _scroll_ref_list_to_top(device: Any, profile: GestureProfile, rounds: int = 6) -> None:
  for _ in range(rounds):
    _scroll_ref_list(device, profile, "up")
    time.sleep(0.22)


def _click_citation(
  device: Any,
  citation: Citation,
  *,
  profile: GestureProfile | None = None,
) -> bool:
  target = _find_citation_click_target(device, citation, log=True, profile=profile)
  if not target:
    _log_url(f"拒绝点击 #{citation.ref_index or '?'}：无精确元素匹配（禁止坐标点击）")
    return False

  _log_url(
    f"执行点击 #{citation.ref_index or '?'} "
    f"strategy={target.strategy} click_rid={target.click_rid.rsplit('/', 1)[-1]} "
    f"index={target.index_text!r} title={target.title_text[:56]!r}"
  )
  try:
    target.element.click()
    _log_url(f"点击成功 #{citation.ref_index or '?'} via {target.strategy}")
    return True
  except Exception as exc:
    _log_url(f"点击失败 #{citation.ref_index or '?'}: {exc}")
    return False


def _scroll_chat_down(device: Any, profile: GestureProfile) -> bool:
  w, h = display_wh(device, profile=profile)
  try:
    device.swipe(
      int(w * 0.5),
      int(h * profile.qa_shot_scroll_start_y),
      int(w * 0.5),
      int(h * profile.qa_shot_scroll_end_y),
      profile.qa_shot_scroll_duration,
    )
    return True
  except Exception as exc:
    print(f"[问答] 下滑失败: {exc}")
    return False


def _scroll_chat_up(device: Any, profile: GestureProfile) -> bool:
  w, h = display_wh(device, profile=profile)
  try:
    device.swipe(
      int(w * 0.5),
      int(h * profile.qa_shot_scroll_end_y),
      int(w * 0.5),
      int(h * profile.qa_shot_scroll_start_y),
      profile.qa_shot_scroll_duration,
    )
    return True
  except Exception as exc:
    print(f"[问答] 上滑失败: {exc}")
    return False


def _citation_center_y(device: Any, citation: Citation) -> int | None:
  el, _ = _find_citation_element(device, citation)
  if el:
    try:
      b = el.bounds
      if b:
        return (int(b[1]) + int(b[3])) // 2
    except Exception:
      pass
  if citation.bounds and len(citation.bounds) == 4:
    return (citation.bounds[1] + citation.bounds[3]) // 2
  return None


def _citation_in_viewport(
  cy: int,
  h: int,
  profile: GestureProfile | None = None,
) -> bool:
  y_min, y_max = _viewport_y_band(h, profile)
  return y_min <= cy <= y_max


def _visible_ref_index_range(
  device: Any,
  profile: GestureProfile | None = None,
) -> tuple[int, int] | None:
  """RecyclerView 当前屏可见引用序号区间（min, max）。"""
  root = _detect_ref_list_root_xpath(device, profile)
  try:
    rows = device.xpath(f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]').all()
  except Exception:
    return None
  indices: list[int] = []
  for row in rows:
    idx_text, _ = _read_row_index_and_title(device, row, root_xpath=root)
    if not idx_text:
      continue
    try:
      indices.append(int(idx_text.rstrip(".")))
    except ValueError:
      continue
  if not indices:
    return None
  return min(indices), max(indices)


def _scroll_direction_for_missing_citation(
  citation: Citation,
  visible_range: tuple[int, int] | None,
) -> str:
  """目标不在 DOM 时，根据可见序号区间决定列表滚动方向。"""
  want = citation.ref_index or 0
  if want > 0 and visible_range:
    lo, hi = visible_range
    if want < lo:
      return "up"
    if want > hi:
      return "down"
  return "down"


def _citation_swipe_budget(
  citation: Citation,
  profile: GestureProfile,
  max_swipes: int | None,
) -> int:
  base = max_swipes if max_swipes is not None else profile.qa_resolve_citation_max_swipes
  idx = citation.ref_index or 0
  if idx <= 0:
    return base
  # 长列表：序号越大需要更多次列表内下滚（回顶后逐条解析）
  return max(base, min(24, (idx + 1) // 2 + 2))


def _ensure_citation_visible(
  device: Any,
  citation: Citation,
  profile: GestureProfile,
  max_swipes: int | None = None,
) -> bool:
  """
  滚动直至目标引用在 RecyclerView DOM 中可精确命中。

  禁止仅凭截图阶段缓存的 bounds 判定可见（虚拟列表超屏条目不在 hierarchy）。
  """
  w, h = display_wh(device, profile=profile)
  y_min, y_max = _viewport_y_band(h, profile)
  has_ref_list = _get_ref_list_bounds(device, profile) is not None
  swipe_limit = _citation_swipe_budget(citation, profile, max_swipes)
  if not has_ref_list:
    # 引用列表容器丢失时勿长滑聊天区（易卡死）；交给上层重新展开思考面板
    swipe_limit = min(swipe_limit, 2)
  ref_bounds = _get_ref_list_bounds(device, profile)
  _log_url(
    f"定位引用 #{citation.ref_index or '?'} "
    f"cached={_format_bounds(citation.bounds)} list={_format_bounds(ref_bounds)} "
    f"swipe_budget={swipe_limit}"
  )
  if not has_ref_list:
    target = _find_citation_click_target(device, citation, profile=profile)
    if not target:
      _log_url(
        f"引用 #{citation.ref_index or '?'} 无列表容器且 DOM 未命中，放弃定位"
      )
      return False
  else:
    # 高价值：先按序号 xpath 滚进 DOM（RecyclerView 虚拟化）
    root_xp = _detect_ref_list_root_xpath(device, profile)
    visible_range = _visible_ref_index_range(device, profile)
    direction = _scroll_direction_for_missing_citation(citation, visible_range)
    if citation.ref_index > 0:
      scrolled = scroll_citation_index_into_view(
        device,
        root_xpath=root_xp,
        ref_index=citation.ref_index,
        container_bounds=ref_bounds,
        direction_hint=direction,
        max_swipes=min(swipe_limit, 16),
        get_container=lambda: _get_ref_list_bounds(device, profile),
      )
      if scrolled:
        target = _find_citation_click_target(device, citation, profile=profile)
        if target:
          citation.bounds = list(target.bounds)
          cy = (citation.bounds[1] + citation.bounds[3]) // 2
          if _citation_in_viewport(cy, h, profile):
            _log_url(
              f"引用 #{citation.ref_index} 序号滚入后已可见 "
              f"cy={cy} band=({y_min},{y_max})"
            )
            return True

  last_range: tuple[int, int] | None = None
  stall_hits = 0

  for swipe_i in range(swipe_limit + 1):
    target = _find_citation_click_target(device, citation, profile=profile)
    if target:
      citation.bounds = list(target.bounds)
      cy = (citation.bounds[1] + citation.bounds[3]) // 2
      if _citation_in_viewport(cy, h, profile):
        _log_url(
          f"引用 #{citation.ref_index or '?'} 已可见 "
          f"cy={cy} band=({y_min},{y_max}) swipe={swipe_i}"
        )
        return True
      direction = "up" if cy < y_min else "down"
      _log_url(
        f"引用 #{citation.ref_index or '?'} DOM 命中但偏出视口，滚列表 {direction} "
        f"cy={cy} band=({y_min},{y_max})"
      )
      if has_ref_list:
        _scroll_ref_list(device, profile, direction)
      elif cy < y_min:
        _scroll_chat_up(device, profile)
      else:
        _scroll_chat_down(device, profile)
      time.sleep(0.22)
      continue

    visible_range = _visible_ref_index_range(device, profile) if has_ref_list else None
    if visible_range and visible_range == last_range:
      stall_hits += 1
    else:
      stall_hits = 0
    last_range = visible_range

    if stall_hits >= 2:
      _log_url(
        f"引用 #{citation.ref_index or '?'} 列表滚动停滞 "
        f"visible={visible_range} target=#{citation.ref_index or '?'}"
      )
      break

    if has_ref_list:
      direction = _scroll_direction_for_missing_citation(citation, visible_range)
      _log_url(
        f"引用 #{citation.ref_index or '?'} DOM 未命中，列表{direction}滚 "
        f"visible={visible_range} swipe={swipe_i}"
      )
      _scroll_ref_list(device, profile, direction)
    elif not _scroll_chat_down(device, profile):
      break
    time.sleep(0.22)

  target = _find_citation_click_target(device, citation, profile=profile)
  if target:
    citation.bounds = list(target.bounds)
    cy = (target.bounds[1] + target.bounds[3]) // 2
    ok = _citation_in_viewport(cy, h, profile)
    _log_url(
      f"引用 #{citation.ref_index or '?'} 最终 DOM 命中 visible={ok} cy={cy}"
    )
    return ok
  _log_url(f"引用 #{citation.ref_index or '?'} 定位失败（DOM 无匹配）")
  return False


def _refresh_bounds_from_hierarchy(device: Any, citations: list[Citation]) -> int:
  """从当前屏 hierarchy 批量刷新引用 bounds，返回命中条数。"""
  try:
    xml = device.dump_hierarchy(compressed=False) or ""
    if not xml:
      return 0
    panel = parse_thinking_panel(xml)
    if not panel.references:
      return 0
    by_index = {r.ref_index: r for r in panel.references if r.ref_index}
    by_title: dict[str, Citation] = {}
    for r in panel.references:
      key = _collapse_ws(r.title)
      if key:
        by_title[key] = r
    hits = 0
    for citation in citations:
      live = None
      if citation.ref_index and citation.ref_index in by_index:
        live = by_index[citation.ref_index]
      else:
        live = by_title.get(_collapse_ws(citation.title))
      if live and live.bounds:
        citation.bounds = list(live.bounds)
        hits += 1
    return hits
  except Exception:
    return 0


def prepare_citations_for_url_resolve(
  device: Any,
  citations: list[Citation],
  *,
  profile: GestureProfile,
) -> None:
  """轻量准备：列表滚到顶后最多几屏 hierarchy 批量刷新 bounds（避免逐条长滑）。"""
  if not citations:
    return
  _scroll_ref_list_to_top(device, profile, rounds=3)
  time.sleep(0.25)
  hits = _refresh_bounds_from_hierarchy(device, citations)
  missing = sum(1 for c in citations if not c.bounds)
  print(f"[问答] 引用 bounds 刷新: {hits}/{len(citations)} 条可见")
  for pass_i in range(profile.qa_resolve_prepare_list_passes):
    if missing <= 0:
      break
    _scroll_ref_list(device, profile, "down")
    time.sleep(0.25)
    hits = _refresh_bounds_from_hierarchy(device, citations)
    missing = sum(1 for c in citations if not c.bounds)
    print(f"[问答] 引用列表下滚 {pass_i + 1}: bounds {hits}/{len(citations)}")


def _refresh_citation_bounds(
  device: Any,
  citation: Citation,
  *,
  profile: GestureProfile | None = None,
) -> None:
  """从当前屏 live 节点刷新 bounds（避免截图后坐标过期）。"""
  el, _ = _find_citation_element(device, citation, profile=profile)
  if not el:
    return
  try:
    b = el.bounds
    if b:
      old = citation.bounds
      citation.bounds = [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
      if old != citation.bounds:
        _log_url(
          f"刷新 bounds #{citation.ref_index or '?'} "
          f"{_format_bounds(old)} -> {_format_bounds(citation.bounds)}"
        )
  except Exception:
    pass


def _resolve_one_citation_url(
  device: Any,
  citation: Citation,
  *,
  nav: Navigator,
  profile: GestureProfile,
  serial: str | None,
  method: ResolveMethod,
  stream: LogcatStream | None = None,
  recent_logcat_urls: list[str] | None = None,
  brute_force: bool = False,
  expected_prompt: str = "",
) -> str:
  """单条引用：mark → 点击 → 从流/logcat 解析 → lite back。"""
  serial = serial or _device_serial(device)
  ref_idx = citation.ref_index or "?"
  channel = classify_citation_channel(citation)
  logcat_timeout = profile.qa_resolve_logcat_poll_timeout * (2.0 if brute_force else 1.0)
  dumpsys_wait = profile.qa_resolve_url_wait * (1.25 if brute_force else 1.0)
  _log_page(nav, f"#{ref_idx} 解析前")
  _log_url(
    f"开始解析 #{ref_idx} channel={channel} method={method} "
    f"brute={brute_force} title={citation.title[:48]!r}"
  )

  if stream is not None:
    stream.mark()
  else:
    clear_logcat(serial=serial)
    time.sleep(profile.qa_logcat_stream_settle)

  _refresh_citation_bounds(device, citation, profile=profile)

  if not _click_citation(device, citation, profile=profile):
    _log_url(f"#{ref_idx} 首次点击失败，滚列表后重试")
    clicked = (
      _ensure_citation_visible(device, citation, profile)
      and _click_citation(device, citation, profile=profile)
    )
    if not clicked:
      _log_url(f"#{ref_idx} 点击失败，放弃")
      return ""

  _log_page(nav, f"#{ref_idx} 点击后")

  if stream is not None and method in ("auto", "logcat"):
    url = poll_logcat_stream_for_url(
      stream,
      timeout_s=logcat_timeout,
      poll_interval_s=profile.qa_resolve_logcat_poll_interval,
    )
  else:
    url = resolve_url_after_click(
      device,
      serial=serial,
      method=method if method != "net" else "auto",
      profile=profile,
    )

  if url:
    _log_url(f"#{ref_idx} logcat 命中 URL: {url[:96]}")
  else:
    _log_url(f"#{ref_idx} logcat 未命中")

  if (
    url
    and recent_logcat_urls is not None
    and url in recent_logcat_urls[-3:]
    and method in ("auto", "logcat")
  ):
    _log_url(f"#{ref_idx} 重复 URL 丢弃: {url[:80]}")
    url = ""

  # logcat 未命中时，先在同屏（WebActivity/抖音）读 dumpsys，避免过早 back 丢 Intent
  if not url and method in ("auto", "logcat"):
    url = resolve_url_via_dumpsys(
      device, serial=serial, wait_s=0.6 if not brute_force else min(dumpsys_wait, 1.0),
    )
    if url:
      _log_url(f"#{ref_idx} dumpsys 同屏命中: {url[:96]}")
    else:
      _log_url(f"#{ref_idx} dumpsys 同屏未命中")

  if not url and method in ("auto", "logcat"):
    from app.modules.navigator import Page

    page_now, _ = nav.current_page()
    if page_now in (Page.WEB_DETAIL, Page.OTHER_APP):
      _log_url(f"#{ref_idx} 详情页 dumpsys 加长等待")
      url = resolve_url_via_dumpsys(
        device, serial=serial, wait_s=dumpsys_wait,
      )
      if url:
        _log_url(f"#{ref_idx} 详情页 dumpsys 命中: {url[:96]}")
      else:
        _log_url(f"#{ref_idx} 详情页 dumpsys 未命中，返回聊天重试")
    elif page_now == Page.CHAT:
      # 仍在聊天页时禁止 lite_back（会误退到抖音等外部 App）
      _log_url(f"#{ref_idx} 仍在 CHAT，等待详情打开后重试")
      time.sleep(1.0)
      page_now, _ = nav.current_page()
      if page_now != Page.CHAT:
        url = resolve_url_via_dumpsys(
          device, serial=serial, wait_s=dumpsys_wait,
        )
      if not url and stream is not None:
        url = poll_logcat_stream_for_url(
          stream,
          timeout_s=logcat_timeout,
          poll_interval_s=profile.qa_resolve_logcat_poll_interval,
        )
      if not url:
        if stream is not None:
          stream.mark()
        else:
          clear_logcat(serial=serial)
          time.sleep(profile.qa_logcat_stream_settle)
        if _click_citation(device, citation, profile=profile):
          time.sleep(0.9 if brute_force else 0.7)
          page_now, _ = nav.current_page()
          if page_now != Page.CHAT:
            url = resolve_url_via_dumpsys(
              device, serial=serial, wait_s=dumpsys_wait,
            )
          elif stream is not None:
            url = poll_logcat_stream_for_url(
              stream,
              timeout_s=logcat_timeout,
              poll_interval_s=profile.qa_resolve_logcat_poll_interval,
            )
    if not url and page_now != Page.CHAT:
      _log_url(f"#{ref_idx} 回落 dumpsys 重试（返回聊天后重点）")
      nav.lite_back_to_chat()
      _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} dumpsys前")
      time.sleep(0.2)
      if stream is not None:
        stream.mark()
      else:
        clear_logcat(serial=serial)
        time.sleep(profile.qa_logcat_stream_settle)
      if _ensure_citation_visible(device, citation, profile) and _click_citation(
        device, citation, profile=profile,
      ):
        url = resolve_url_via_dumpsys(
          device, serial=serial, wait_s=dumpsys_wait,
        )
        if url:
          _log_url(f"#{ref_idx} dumpsys 命中: {url[:96]}")
        else:
          _log_url(f"#{ref_idx} dumpsys 未命中")
    elif not url and page_now == Page.CHAT:
      _log_url(f"#{ref_idx} CHAT 重试后仍未解析到 URL")

  _log_url(f"#{ref_idx} 准备返回聊天页")
  nav.lite_back_to_chat()
  from app.modules.navigator import Page

  page, _ = nav.current_page()
  if page != Page.CHAT:
    _log_url(f"#{ref_idx} lite_back 未到聊天页({page.name})，safe_back")
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} 解析后")
  time.sleep(profile.qa_resolve_url_post_back_sleep)

  if expected_prompt and not _chat_context_ok(
    device, expected_prompt, profile, f"#{ref_idx} 返回后",
  ):
    return ""

  if url and recent_logcat_urls is not None and method in ("auto", "logcat"):
    recent_logcat_urls.append(url)

  return url


def _pending_sorted(citations: list[Citation]) -> list[tuple[int, Citation]]:
  pending: list[tuple[int, Citation]] = [
    (i, c) for i, c in enumerate(citations) if not c.url
  ]
  pending.sort(
    key=lambda x: (
      x[1].ref_index or 9999,
      x[1].bounds[1] if x[1].bounds else 0,
    ),
  )
  return pending


def _resolve_pending_pass(
  device: Any,
  citations: list[Citation],
  pending: list[tuple[int, Citation]],
  *,
  nav: Navigator,
  profile: GestureProfile,
  serial: str | None,
  click_method: ResolveMethod,
  stream: LogcatStream,
  recent_logcat_urls: list[str],
  pass_label: str,
  channels: set[CitationChannel] | None = None,
  brute_force: bool = False,
  apply_skip_policy: bool = False,
  max_refs: int = 0,
  attempts_so_far: int = 0,
  resolved_by_index: dict[int, str],
  expected_prompt: str = "",
) -> int:
  """按 pass 解析 pending 子集；返回累计 attempts。"""
  limit = max_refs if max_refs > 0 else len(citations)
  attempts = attempts_so_far
  for idx, citation in pending:
    if max_refs > 0 and attempts >= limit:
      break
    if citation.url:
      continue
    if not _chat_context_ok(device, expected_prompt, profile, pass_label):
      print(f"[问答] {pass_label} 中止：已离开目标会话")
      break

    channel = classify_citation_channel(citation)
    if channels is not None and channel not in channels:
      continue

    if apply_skip_policy and should_skip_douyin_url_resolve(citation, profile):
      print(
        f"[问答] {pass_label} 跳过抖音引用（技术阶段）: "
        f"#{citation.ref_index or '?'} {citation.title[:40]!r}"
      )
      continue

    if not _ensure_citation_visible(device, citation, profile):
      print(
        f"[问答] {pass_label} 引用不可见，跳过: "
        f"#{citation.ref_index or '?'} {citation.title[:40]!r}"
      )
      continue

    print(
      f"[问答] {pass_label} {attempts + 1}/{limit}: "
      f"#{citation.ref_index or '?'} channel={channel} "
      f"{citation.title[:36]!r}"
    )
    url = _resolve_one_citation_url(
      device,
      citation,
      nav=nav,
      profile=profile,
      serial=serial,
      method=click_method,
      stream=stream,
      recent_logcat_urls=recent_logcat_urls,
      brute_force=brute_force,
      expected_prompt=expected_prompt,
    )

    if expected_prompt and not _chat_context_ok(
      device, expected_prompt, profile, f"{pass_label} #{citation.ref_index or '?'}"
    ):
      print(f"[问答] {pass_label} 中止：点击返回后会话错位")
      break

    if url:
      citation.url = url
      resolved_by_index[idx] = url
      print(
        f"[问答] {pass_label} URL: "
        f"{citation.ref_index or '?'} -> {url[:80]}"
      )
    else:
      print(
        f"[问答] {pass_label} 未解析到 URL: "
        f"#{citation.ref_index or '?'} {citation.title[:40]!r}"
      )

    attempts += 1
  return attempts


def resolve_thinking_reference_urls(
  device: Any,
  citations: list[Citation],
  *,
  profile: GestureProfile | None = None,
  serial: str | None = None,
  max_refs: int = 0,
  method: ResolveMethod = "logcat",
  expected_prompt: str = "",
) -> list[Citation]:
  """
  逐条点击思考引用，解析真实 HTTP 链接写回 Citation.url。

  分三阶段：抖音批量 → 技术逐条（web/douyin 渠道）→ 笨办法补齐剩余。
  method: logcat（默认）、auto（logcat→dumpsys）、dumpsys；net 见 qa_reference_net。
  """
  p = profile or GestureProfile()
  nav = Navigator(device)
  serial = serial or _device_serial(device)
  if not citations:
    return citations

  from app.modules.navigator import Page

  page, _ = nav.current_page()
  if page != Page.CHAT:
    print("[问答] 当前不在聊天页，尝试返回豆包...")
    nav.safe_back_to_chat(max_backs=p.qa_resolve_url_max_backs)
  if not _chat_context_ok(device, expected_prompt, p, "解析入口"):
    print("[问答] URL 解析中止：当前屏不是目标会话")
    return citations

  limit = max_refs if max_refs > 0 else len(citations)
  attempts = 0
  click_method = method if method in ("auto", "logcat", "dumpsys") else "auto"
  recent_logcat_urls: list[str] = []

  stream = LogcatStream(serial=serial)
  stream.start(settle_s=p.qa_logcat_stream_settle)
  resolved_by_index: dict[int, str] = {}
  try:
    pending_before = _pending_sorted(citations)
    if (
      p.qa_resolve_batch_douyin
      and click_method in ("auto", "logcat")
      and len(pending_before) >= 2
      and any(
        classify_citation_channel(c) == "douyin"
        for _, c in pending_before
      )
    ):
      batch_douyin_ok = try_batch_resolve_douyin(
        device,
        citations,
        nav=nav,
        profile=p,
        stream=stream,
      )
      if not batch_douyin_ok:
        print("[问答] 抖音批量未通过校验，回落技术逐条 + 笨办法补齐")
    elif not p.qa_resolve_batch_douyin:
      print("[问答] 抖音批量已关闭，抖音条目走技术逐条/笨办法逐条点击")

    pending = _pending_sorted(citations)
    tech_pending = [
      (i, c)
      for i, c in pending
      if classify_citation_channel(c) in ("web", "douyin")
    ]
    tech_pending.sort(
      key=lambda x: (
        _CHANNEL_RESOLVE_ORDER[classify_citation_channel(x[1])],
        x[1].ref_index or 9999,
        x[1].bounds[1] if x[1].bounds else 0,
      ),
    )
    if tech_pending:
      print(f"[问答] 技术逐条解析 {len(tech_pending)} 条（web/douyin 渠道）")
      attempts = _resolve_pending_pass(
        device,
        citations,
        tech_pending,
        nav=nav,
        profile=p,
        serial=serial,
        click_method=click_method,
        stream=stream,
        recent_logcat_urls=recent_logcat_urls,
        pass_label="技术逐条",
        channels={"web", "douyin"},
        brute_force=False,
        apply_skip_policy=True,
        max_refs=limit,
        attempts_so_far=attempts,
        resolved_by_index=resolved_by_index,
        expected_prompt=expected_prompt,
      )

    pending = _pending_sorted(citations)
    if pending:
      print(f"[问答] 笨办法补齐 {len(pending)} 条剩余无 URL 引用")
      _resolve_pending_pass(
        device,
        citations,
        pending,
        nav=nav,
        profile=p,
        serial=serial,
        click_method=click_method,
        stream=stream,
        recent_logcat_urls=recent_logcat_urls,
        pass_label="笨办法",
        channels=None,
        brute_force=True,
        apply_skip_policy=False,
        max_refs=limit,
        attempts_so_far=attempts,
        resolved_by_index=resolved_by_index,
        expected_prompt=expected_prompt,
      )
  finally:
    stream.stop()

  out = list(citations)
  for i, c in enumerate(out):
    if i in resolved_by_index:
      c.url = resolved_by_index[i]
  return out
