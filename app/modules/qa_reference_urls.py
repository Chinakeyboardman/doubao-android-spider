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
  r"snssdk(?:1128|1180)://aweme/detail/(\d+)",
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
# URL 解析阶段日志（保留 reset 供后续扩展）
_ref_list_rows_logged = False


def reset_url_resolve_log_state() -> None:
  """新一轮 URL 解析开始前重置日志状态。"""
  global _ref_list_rows_logged
  _ref_list_rows_logged = False


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
  quick: bool = False,
) -> None:
  """返回聊天页后把引用列表滚回顶部，避免落在底部误点视频。"""
  from app.modules.navigator import Page

  if quick:
    page, _ = nav.current_page()
    if page != Page.CHAT:
      return
    ref_bounds = _get_ref_list_bounds(device, profile)
    if not ref_bounds:
      return
    _, h = display_wh(device, profile=profile)
    if ref_bounds[1] <= int(h * 0.42):
      _log_url(f"{tag} 列表已在屏上部，跳过回顶")
      return
    _scroll_ref_list_to_top(device, profile, rounds=2)
    time.sleep(0.15)
    return

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


def _return_after_fast_url_hit(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  ref_idx: str,
  expected_prompt: str = "",
) -> None:
  """快速命中 URL 后：先收外部抖音，再回聊天；已在 CHAT 时禁止 safe_back。"""
  from app.modules.navigator import Page

  t0 = time.time()
  nav.recover_from_external_douyin(gentle=True)
  page, _ = nav.current_page()
  if page == Page.WEB_DETAIL:
    nav.lite_back_to_chat()
    page, _ = nav.current_page()
  if page != Page.CHAT:
    backs = min(3, profile.qa_resolve_url_max_backs)
    nav.safe_back_to_chat(max_backs=backs)
  elapsed = time.time() - t0
  if elapsed >= 1.0:
    _log_url(f"#{ref_idx} 快速命中后回聊天 {elapsed:.1f}s")
  _reanchor_ref_list_after_back(
    device, profile, nav, tag=f"#{ref_idx} 快速命中", quick=True,
  )
  if expected_prompt:
    _ensure_target_chat_session(
      device, nav, profile, expected_prompt, f"#{ref_idx} 快速命中后",
    )


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


def _log_target_ref_row(
  device: Any,
  *,
  expect_index: int = 0,
  root_xpath: str | None = None,
) -> None:
  """仅打印目标序号对应的可见行（不列举整表）。"""
  if not expect_index:
    return
  root = root_xpath or _detect_ref_list_root_xpath(device)
  try:
    rows = device.xpath(
      f'{root}//*[@resource-id="{SOURCE_ITEM_RID}"]'
    ).all()
  except Exception as exc:
    _log_url(f"列举引用行失败: {exc}")
    return
  for row in rows:
    idx_text, title_text = _read_row_index_and_title(device, row, root_xpath=root)
    if idx_text.rstrip(".") != str(expect_index):
      continue
    fp = _element_fingerprint(row)
    _log_url(
      f"目标行 #{expect_index} index={idx_text!r} title={title_text[:60]!r} "
      f"bounds={_format_bounds(fp['bounds'])}"
    )
    return
  _log_url(f"目标行 #{expect_index} 不在当前可见 {len(rows)} 行内")


def _log_visible_ref_rows(
  device: Any,
  *,
  expect_index: int = 0,
  root_xpath: str | None = None,
) -> None:
  """兼容旧调用：等同 _log_target_ref_row。"""
  _log_target_ref_row(
    device, expect_index=expect_index, root_xpath=root_xpath,
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
  ref_idx = citation.ref_index or "?"
  if log:
    _log_url(
      f"查找 #{ref_idx} title={citation.title[:48]!r} "
      f"list={_format_bounds(ref_bounds)}"
    )

  for strategy, xp in _citation_xpath_strategies(citation, device, profile):
    try:
      el = device.xpath(xp).get(timeout=0.45)
    except Exception:
      el = None
    if not el:
      continue

    click_el, click_rid = _pick_clickable_element(
      device, citation, el, root_xpath=root_xpath,
    )
    if not click_el:
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
        continue
      title_text = detail if isinstance(detail, str) and detail else title_text

    if not _validate_citation_match(
      citation,
      index_text=index_text,
      title_text=title_text,
      fp=fp,
      ref_bounds=ref_bounds,
    ):
      continue

    if log:
      _log_url(
        f"命中 #{ref_idx} via {strategy} index={index_text!r} "
        f"title={title_text[:48]!r} bounds={_format_bounds(fp['bounds'])}"
      )

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
  from app.modules.douyin_web_resolve import build_share_url

  return build_share_url(video_id)


def _douyin_url_from_id(
  video_id: str,
  profile: GestureProfile | None = None,
) -> str:
  """从 aweme_id 拼装并 PC 多格式验证，返回 best_verified 原始 URL。"""
  from app.modules.douyin_web_resolve import (
    build_url_from_aweme_id,
    resolve_verified_url,
  )

  p = profile or GestureProfile()
  raw_id = (video_id or "").strip()
  if not raw_id:
    return ""
  format_ids = p.qa_douyin_web_url_formats or None
  if not p.qa_douyin_web_validate:
    return build_url_from_aweme_id(raw_id, format_ids=format_ids)
  url = resolve_verified_url(
    raw_id,
    require_web_verify=True,
    format_ids=format_ids,
    min_interval_s=p.qa_douyin_web_validate_interval,
    fallback_unverified=p.qa_douyin_web_validate_fallback,
  )
  if url:
    _log_url(f"PC Web id={raw_id} → {url[:80]}")
    return url
  if p.qa_douyin_web_validate_fallback:
    fb = build_url_from_aweme_id(raw_id, format_ids=format_ids)
    _log_url(f"PC Web 验证未通过 id={raw_id}，回落 {fb[:80]}")
    return fb
  _log_url(f"PC Web 验证未通过 id={raw_id}，丢弃")
  return ""


def _iesdouyin_url_verified(
  video_id: str,
  profile: GestureProfile | None = None,
) -> str:
  """兼容旧名。"""
  return _douyin_url_from_id(video_id, profile)


def _finalize_douyin_url(
  raw_url: str,
  profile: GestureProfile | None = None,
) -> str:
  """
  把真机已解析到的链接归一为偏好的抖音双前缀（jingxuan/video）。

  - 含 19 位 aweme_id（来自 WebActivity/dumpsys/logcat，id 必有效）→ 按
    profile `qa_douyin_web_url_formats` 首选前缀即时拼装，无额外 HTTP 开销。
  - 非抖音（网页/SPU 深链等）→ 原样返回。
  - 拼装失败兜底保留原链（笨方法保底，绝不丢链）。
  """
  from app.modules.douyin_web_resolve import (
    build_url_from_aweme_id,
    normalize_aweme_id,
  )

  raw = (raw_url or "").strip()
  if not raw:
    return raw
  # 仅对抖音族链接归一，避免误伤网页/SPU（防止 web URL 中偶发长数字被当 id）
  low = raw.lower()
  is_douyin_like = (
    "douyin" in low
    or "iesdouyin" in low
    or "snssdk" in low
    or raw.isdigit()
  )
  if not is_douyin_like:
    return raw
  p = profile or GestureProfile()
  aid = normalize_aweme_id(raw)
  if not aid:
    return raw
  built = build_url_from_aweme_id(
    aid, format_ids=p.qa_douyin_web_url_formats or None,
  )
  if built and built != raw:
    _log_url(f"归一抖音双前缀 id={aid} → {built[:80]}")
    return built
  return built or raw


def _douyin_stitch_format_ids(profile: GestureProfile) -> tuple[str, ...]:
  """拼接验证仅试 jingxuan / video（profile 可覆盖）。"""
  fmts = profile.qa_douyin_web_url_formats
  if fmts:
    return fmts
  return ("douyin_jingxuan_modal", "douyin_video")


def stitch_verify_douyin_url(aweme_id: str, profile: GestureProfile) -> str:
  """
  有 19 位 aweme_id 时拼 jingxuan/video，并用 HTTP 模拟请求快验。
  验证失败且 fallback 开启时仍返回首个拼接 URL（不丢链）。
  """
  from app.modules.douyin_web_resolve import (
    build_url_from_aweme_id,
    normalize_aweme_id,
    validate_aweme_multi_format,
  )

  aid = normalize_aweme_id(aweme_id)
  if not aid:
    return ""
  format_ids = _douyin_stitch_format_ids(profile)
  if profile.qa_douyin_web_validate:
    result = validate_aweme_multi_format(
      aid,
      format_ids=format_ids,
      min_interval_s=profile.qa_douyin_web_validate_interval,
    )
    if result.verified and result.share_url:
      _log_url(
        f"拼接验证通过 id={aid} fmt={result.format_id}: {result.share_url[:80]}"
      )
      return result.share_url
    if profile.qa_douyin_web_validate_fallback:
      fb = build_url_from_aweme_id(aid, format_ids=format_ids)
      _log_url(f"拼接验证未过 id={aid}，回落 {fb[:80]} ({result.note})")
      return fb
    _log_url(f"拼接验证未过 id={aid}，丢弃 ({result.note})")
    return ""
  fb = build_url_from_aweme_id(aid, format_ids=format_ids)
  if fb:
    _log_url(f"拼接 id={aid} → {fb[:80]}（未开 HTTP 验证）")
  return fb


def _gather_aweme_id_on_screen(
  *,
  serial: str | None,
  stream: LogcatStream | None,
  ref_idx: str,
) -> str:
  """点击后同屏从 logcat/dumpsys 抽 aweme_id（不进抖音也可）。"""
  from app.modules.douyin_web_resolve import normalize_aweme_id

  chunks: list[str] = []
  if stream is not None:
    chunks.append(stream.text_since_mark())
  chunks.append(dump_logcat_tail(serial=serial, count=200))
  try:
    chunks.append(_adb_dumpsys(serial, "activity", "activities"))
    chunks.append(_adb_dumpsys(serial, "activity", "top"))
  except Exception:
    pass
  merged = "\n".join(chunks)
  ids = extract_aweme_ids_ordered(merged)
  if ids:
    _log_url(f"#{ref_idx} 同屏 aweme_id={ids[0]}")
    return ids[0]
  for u in extract_urls_from_dumpsys_text(merged):
    aid = normalize_aweme_id(u)
    if aid:
      _log_url(f"#{ref_idx} 同屏 URL→aweme_id={aid}")
      return aid
  return ""


def _try_douyin_click_in_for_url(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  serial: str | None,
  stream: LogcatStream | None,
  ref_idx: str,
) -> str:
  """豆包 Web 未抽到 id 时，允许点进抖音 App 再收 aweme_id 并拼接验证。"""
  from app.modules.navigator import Page

  _log_url(f"#{ref_idx} 抖音 Web 未得 id，尝试点进 App")
  page_now, _ = nav.current_page()
  if profile.qa_resolve_accept_app_jump:
    nav.wait_and_accept_app_jump(timeout=6.0)
  if page_now == Page.CHAT:
    time.sleep(0.8)
  nav.wait_for_aweme_foreground(timeout=8.0)
  timeout = min(10.0, max(6.0, profile.qa_resolve_batch_douyin_timeout))
  ids = collect_aweme_ids_after_open(
    stream=stream,
    serial=serial,
    timeout_s=timeout,
  )
  url = ""
  if ids:
    url = stitch_verify_douyin_url(ids[0], profile)
    if url:
      _log_url(f"#{ref_idx} 点进后 id={ids[0]} → {url[:96]}")
  nav.recover_from_external_douyin(gentle=True)
  nav.lite_back_to_chat()
  return url


def _apply_douyin_stitch_after_click(
  url: str,
  *,
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  serial: str | None,
  stream: LogcatStream | None,
  citation: Citation,
  ref_idx: str,
  method: ResolveMethod,
) -> str:
  """同屏抽 id 拼接验证；抖音条仍无链则点进 App。"""
  from app.modules.douyin_web_resolve import is_douyin_video_url, normalize_aweme_id

  aid = normalize_aweme_id(url) if url else ""
  if not aid:
    aid = _gather_aweme_id_on_screen(
      serial=serial, stream=stream, ref_idx=ref_idx,
    )
  if aid:
    stitched = stitch_verify_douyin_url(aid, profile)
    if stitched:
      return stitched
  if url and not is_douyin_video_url(url):
    return url
  if not is_likely_douyin_citation(citation) and not aid:
    return url
  if url and is_douyin_video_url(url):
    return _finalize_douyin_url(url, profile)
  if method not in ("auto", "logcat", "dumpsys"):
    return url
  click_in = _try_douyin_click_in_for_url(
    device,
    nav,
    profile,
    serial=serial,
    stream=stream,
    ref_idx=ref_idx,
  )
  return click_in or url


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
    from app.modules.douyin_web_resolve import build_url_from_aweme_id

    _add(m.start(), build_url_from_aweme_id(m.group(1)))
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


def collect_aweme_ids_after_open(
  *,
  stream: LogcatStream | None,
  serial: str | None,
  timeout_s: float = 12.0,
  poll_interval_s: float = 0.25,
  expected_count: int = 0,
) -> list[str]:
  """点击并允许打开抖音后，从 logcat 流 / tail / dumpsys 收集 aweme id。"""
  deadline = time.time() + timeout_s
  best: list[str] = []
  while time.time() < deadline:
    chunks: list[str] = []
    if stream is not None:
      chunks.append(stream.text_since_mark())
    chunks.append(dump_logcat_tail(serial=serial, count=300))
    try:
      chunks.append(_adb_dumpsys(serial, "activity", "activities"))
      chunks.append(_adb_dumpsys(serial, "activity", "top"))
    except Exception:
      pass
    merged = "\n".join(chunks)
    ids = extract_aweme_ids_ordered(merged)
    if len(ids) > len(best):
      best = ids
    if expected_count and validate_batch_douyin_ids(ids, expected_count):
      return ids
    time.sleep(poll_interval_s)
  return best


def validate_batch_douyin_ids(ids: list[str], expected_count: int) -> bool:
  """批量回填前校验：数量一致且 id 互不重复。"""
  if expected_count <= 0:
    return False
  if len(ids) != expected_count:
    return False
  return len(ids) == len(set(ids))


def is_likely_douyin_citation(citation: Citation) -> bool:
  """启发式判断引用是否更可能为抖音视频（非网页）。"""
  from app.modules.douyin_web_resolve import is_douyin_video_url

  src = (citation.source or "").lower()
  if "抖音" in src or "douyin" in src:
    return True
  title = citation.title or ""
  if citation.url and is_douyin_video_url(citation.url):
    return True
  if "#" in title and not citation.source:
    return True
  if "|||" in title:
    return True
  if "！！" in title and len(title) < 120:
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
  "新闻网",
  "大河网",
  "凤凰网",
  "苏宁易购",
  "suning.com",
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


def _ensure_chat_after_resolve(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  tag: str,
  expected_prompt: str = "",
) -> bool:
  """
  解析单条后稳健回到聊天页（兼容荣耀等较慢机型的 WebActivity/评论页多层回退）。

  lite_back 一次不够时，温和回豆包 + safe_back 循环补齐，避免误判漂移触发重启。
  回聊天后若提供 expected_prompt，校验是否仍在目标会话（错位则重进）。
  """
  from app.modules.navigator import Page

  p, _ = nav.current_page()
  if p == Page.CHAT:
    if expected_prompt:
      return _ensure_target_chat_session(
        device, nav, profile, expected_prompt, tag,
      )
    return True
  nav.lite_back_to_chat()
  p, _ = nav.current_page()
  if p == Page.CHAT:
    if expected_prompt:
      return _ensure_target_chat_session(
        device, nav, profile, expected_prompt, tag,
      )
    return True
  _log_url(f"{tag} lite_back 未回聊天页({p.name})，safe_back 兜底")
  nav.recover_from_external_douyin(gentle=True)
  nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  p, _ = nav.current_page()
  if p != Page.CHAT:
    return False
  if expected_prompt:
    return _ensure_target_chat_session(
      device, nav, profile, expected_prompt, tag,
    )
  return True


# 会话恢复时用于在会话抽屉里辨识目标会话的回答正文片段（唯一性强于标题摘要）
_EXPECTED_ANSWER_SNIPPET: str = ""


def set_expected_answer_snippet(text: str) -> None:
  """由采集主流程在 URL 解析前设置：目标会话回答正文的可辨识片段。"""
  global _EXPECTED_ANSWER_SNIPPET
  _EXPECTED_ANSWER_SNIPPET = (text or "").strip()


def _apply_session_guard_env(profile: GestureProfile) -> None:
  """环境变量覆盖 URL 解析提速项（无需改 profile 即可切换 60710 轻量模式）。

  QA_URL_SESSION_GUARD=0 → 关闭错位校验/恢复。
  QA_URL_SESSION_RECONFIRM=0 → 关闭二次确认。
  QA_URL_SKIP_BRUTE=0 → 快速逐条后对剩余无 URL 条目跑笨办法补齐。
  QA_URL_SKIP_BRUTE=1 → 快速逐条后不跑笨办法补齐。
  QA_URL_PHASE_BUDGET_SEC=0 → 关闭 URL 阶段 wall-clock 上限。
  QA_URL_SIMPLE=0 → 三阶段（批量抖音可选 → 快速逐条 → 笨办法补齐）。
  QA_URL_SIMPLE=1 → 60710 单遍 URL（logcat→dumpsys→lite_back，无笨办法第二遍）。
  QA_URL_SKIP_DOUYIN=0 → 不跳过抖音引用（逐条点击取视频链）。
  QA_DOUYIN_STITCH_VERIFY=1 → aweme_id 拼 jingxuan/video 并 HTTP 快验。
  """
  import os

  skip_douyin = os.environ.get("QA_URL_SKIP_DOUYIN", "").strip().lower()
  if skip_douyin in ("0", "false", "no", "off"):
    profile.qa_resolve_skip_douyin_per_click = False
    print("[问答] 抖音引用不跳过（QA_URL_SKIP_DOUYIN=0，逐条取视频链）")
  elif skip_douyin in ("1", "true", "yes", "on"):
    profile.qa_resolve_skip_douyin_per_click = True

  stitch = os.environ.get("QA_DOUYIN_STITCH_VERIFY", "").strip().lower()
  if stitch in ("1", "true", "yes", "on"):
    profile.qa_douyin_web_validate = True
    profile.qa_douyin_web_url_formats = (
      "douyin_jingxuan_modal",
      "douyin_video",
    )
    print("[问答] 抖音拼接 HTTP 快验已开启（jingxuan/video）")
  elif stitch in ("0", "false", "no", "off"):
    profile.qa_douyin_web_validate = False

  simple = os.environ.get("QA_URL_SIMPLE", "").strip().lower()
  if simple in ("1", "true", "yes", "on"):
    profile.qa_resolve_simple_mode = True
    print("[问答] URL 单遍模式（QA_URL_SIMPLE=1，60710 流程）")
  elif simple in ("0", "false", "no", "off"):
    profile.qa_resolve_simple_mode = False
    print("[问答] URL 全量模式（快速逐条 + 笨办法补齐，QA_URL_SIMPLE=0）")

  guard = os.environ.get("QA_URL_SESSION_GUARD", "").strip().lower()
  if guard in ("0", "false", "no", "off"):
    profile.qa_resolve_session_guard = False
    print("[问答] 会话守卫已关闭（QA_URL_SESSION_GUARD=0，60710 轻量模式）")
  elif guard in ("1", "true", "yes", "on"):
    profile.qa_resolve_session_guard = True

  reconfirm = os.environ.get("QA_URL_SESSION_RECONFIRM", "").strip().lower()
  if reconfirm in ("0", "false", "no", "off"):
    profile.qa_resolve_session_guard_reconfirm = False
  elif reconfirm in ("1", "true", "yes", "on"):
    profile.qa_resolve_session_guard_reconfirm = True

  skip_brute = os.environ.get("QA_URL_SKIP_BRUTE", "").strip().lower()
  if skip_brute in ("1", "true", "yes", "on"):
    profile.qa_resolve_skip_brute_pass = True
    print("[问答] 已跳过笨办法补齐（QA_URL_SKIP_BRUTE=1）")
  elif skip_brute in ("0", "false", "no", "off"):
    profile.qa_resolve_skip_brute_pass = False
    print("[问答] 笨办法补齐已开启（QA_URL_SKIP_BRUTE=0，未解析条目将逐条重点击）")

  budget = os.environ.get("QA_URL_PHASE_BUDGET_SEC", "").strip()
  if budget:
    try:
      profile.qa_resolve_url_phase_budget_sec = float(budget)
      if float(budget) <= 0:
        print("[问答] URL 阶段预算已关闭（QA_URL_PHASE_BUDGET_SEC=0）")
    except ValueError:
      pass


def _chat_context_ok(
  device: Any,
  expected_prompt: str,
  profile: GestureProfile,
  tag: str,
  *,
  force: bool = False,
) -> bool:
  """
  URL 解析期会话校验（宽松）：仅当屏上出现另一条不同提问时判定错位。

  引用解析途中问题气泡本就滚出屏幕，读不到用户气泡不算证据，避免误杀空转。
  目标回答正文锚点仍在屏上时视为未错位。

  - `qa_resolve_session_guard=False` 且未 force：跳过（60710 轻量模式）。
  - `force=True` 或 `_ensure_target_chat_session` 内调用：始终校验（回退后防落历史会话）。
  - 判定错位前默认二次确认：单次 hierarchy dump 可能残缺，隔一小段再读一次，
    两次都冲突才判错位，消除瞬时误判导致的空转/强杀。
  """
  if not (expected_prompt or "").strip():
    return True
  if not force and not getattr(profile, "qa_resolve_session_guard", True):
    return True
  from app.modules.chat_ui_heuristics import chat_prompt_conflicts

  conflict, visible = chat_prompt_conflicts(
    device,
    expected_prompt,
    profile=profile,
    answer_snippet=_EXPECTED_ANSWER_SNIPPET,
  )
  if not conflict:
    return True

  if getattr(profile, "qa_resolve_session_guard_reconfirm", True):
    time.sleep(
      max(0.0, getattr(profile, "qa_resolve_session_guard_reconfirm_sleep", 0.6))
    )
    conflict2, visible2 = chat_prompt_conflicts(
      device,
      expected_prompt,
      profile=profile,
      answer_snippet=_EXPECTED_ANSWER_SNIPPET,
    )
    if not conflict2:
      _log_url(f"{tag} 会话错位二次确认为误判（屏上 {visible[:24]!r}），忽略")
      return True
    visible = visible2 or visible

  print(
    f"[问答] URL解析会话错位({tag})：期望 {expected_prompt[:40]!r}，"
    f"屏上 {visible[:40]!r}（疑似落入历史会话）"
  )
  return False


def _reenter_target_chat(
  nav: Navigator,
  expected_prompt: str,
  *,
  tag: str,
) -> bool:
  """按回答片段 / prompt 重进目标会话（不 back，避免误退当前 CHAT）。"""
  snippet = _EXPECTED_ANSWER_SNIPPET
  nav.dismiss_conversation_search()
  if nav.reenter_chat_by_prompt(expected_prompt, snippet):
    print(f"[问答] 已重进目标会话 ({tag})")
    return True
  return False


def _ensure_target_chat_session(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  expected_prompt: str,
  tag: str,
) -> bool:
  """
  抖音/Web 返回后确保仍在目标会话。

  已在 CHAT 但错位时直接重进，禁止 safe_back（易误退到历史会话）。
  """
  if not (expected_prompt or "").strip():
    return True
  if _chat_context_ok(device, expected_prompt, profile, tag, force=True):
    return True

  from app.modules.navigator import Page

  page, _ = nav.current_page()
  if page != Page.CHAT:
    nav.recover_from_external_douyin(gentle=True)
    nav.lite_back_to_chat()
    page, _ = nav.current_page()
    if page != Page.CHAT:
      nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
    if _chat_context_ok(device, expected_prompt, profile, f"{tag} 回聊天", force=True):
      return True

  if _reenter_target_chat(nav, expected_prompt, tag=tag):
    _reanchor_ref_list_after_back(device, profile, nav, tag=f"{tag} 重进")
    return _chat_context_ok(device, expected_prompt, profile, f"{tag} 重进后", force=True)
  return False


def _recover_chat_context(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  expected_prompt: str,
  tag: str,
) -> bool:
  """会话错位时逐级恢复：重进 → 非 CHAT 时 safe_back → 强杀重启兜底。"""
  if not (expected_prompt or "").strip():
    return True
  if _chat_context_ok(device, expected_prompt, profile, tag):
    return True
  print(f"[问答] 尝试恢复会话 ({tag})")

  if _ensure_target_chat_session(device, nav, profile, expected_prompt, tag):
    print(f"[问答] 会话已恢复 ({tag})")
    return True

  nav.hard_restart_app(reason=f"URL解析会话错位({tag})")
  time.sleep(2.0)
  if _reenter_target_chat(nav, expected_prompt, tag=f"{tag} 重启后"):
    _reanchor_ref_list_after_back(device, profile, nav, tag=f"{tag} 重启后")
  ok = _chat_context_ok(device, expected_prompt, profile, f"{tag} 重启后")
  if ok:
    print(f"[问答] 会话已恢复 ({tag}，重启)")
  return ok


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


def apply_batch_douyin_urls(
  citations: list[Citation],
  indices: list[int],
  ids: list[str],
  *,
  profile: GestureProfile | None = None,
) -> None:
  """按 citations 列表顺序将批量 id 写回对应条目。"""
  for idx, vid in zip(indices, ids):
    citations[idx].url = _douyin_url_from_id(vid, profile)


def _return_from_douyin_resolve(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  expected_prompt: str = "",
) -> bool:
  """从抖音温和回豆包，校验会话，必要时按 prompt 重进。"""
  nav.recover_from_external_douyin(gentle=True)
  time.sleep(profile.qa_resolve_url_post_back_sleep)
  _reanchor_ref_list_after_back(device, profile, nav, tag="抖音批量后")
  if expected_prompt and not _chat_context_ok(
    device, expected_prompt, profile, "抖音批量后",
  ):
    if _reenter_target_chat(nav, expected_prompt, tag="抖音批量后"):
      _reanchor_ref_list_after_back(device, profile, nav, tag="重进会话后")
      return _chat_context_ok(device, expected_prompt, profile, "重进会话")
    print("[问答] 抖音返回后会话错位且重进失败")
    return False
  return True


def try_batch_resolve_douyin(
  device: Any,
  citations: list[Citation],
  *,
  nav: Navigator,
  profile: GestureProfile,
  stream: LogcatStream,
  expected_prompt: str = "",
  sms_token: str = "",
  sms_device_id: str = "",
) -> bool:
  """
  点开首条抖音引用进入 feed，从 logcat 批量抓齐 aweme id 并按序回填。
  校验不通过返回 False，由调用方回落逐条解析。
  """
  from app.modules.douyin_handoff import try_resolve_douyin_after_click

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

  time.sleep(0.4)
  serial = _device_serial(device)
  douyin_count = len(douyin_indices)
  if profile.qa_douyin_web_validate:
    feed_swipes = 0
  elif profile.qa_resolve_accept_app_jump:
    feed_swipes = max(3, min(douyin_count, 8))
  else:
    feed_swipes = 0
  url, ids = try_resolve_douyin_after_click(
    device,
    nav,
    profile,
    serial=serial,
    stream=stream,
    sms_token=sms_token,
    sms_device_id=sms_device_id,
    batch_feed_swipes=feed_swipes,
    for_batch=True,
  )
  nav.recover_from_external_douyin(gentle=True)

  def _apply_batch_if_complete(id_list: list[str]) -> bool:
    if not validate_batch_douyin_ids(id_list, douyin_count):
      return False
    apply_batch_douyin_urls(citations, douyin_indices, id_list, profile=profile)
    if profile.qa_douyin_web_validate:
      print(f"[问答] 抖音批量 PC Web 验证回填 {douyin_count} 条")
    elif url:
      print(f"[问答] 抖音深链/Handoff 批量回填 {douyin_count} 条")
    else:
      print(f"[问答] 抖音批量回填 {douyin_count} 条引用 URL")
    _return_from_douyin_resolve(device, nav, profile, expected_prompt=expected_prompt)
    return True

  if _apply_batch_if_complete(ids):
    return True

  if (
    profile.qa_resolve_accept_app_jump
    and not profile.qa_douyin_web_validate
    and not validate_batch_douyin_ids(ids, douyin_count)
  ):
    nav.wait_and_accept_app_jump(timeout=8.0)
    if nav.wait_for_aweme_foreground(timeout=12.0):
      try:
        w, h = device.window_size()
        swipe_n = max(3, min(douyin_count, 8))
        for _ in range(swipe_n):
          device.swipe(int(w * 0.5), int(h * 0.72), int(w * 0.5), int(h * 0.38), 0.35)
          time.sleep(1.0)
      except Exception:
        pass
    elif nav.is_app_jump_prompt():
      _log_url("抖音批量：跳转弹窗仍在，保存 hierarchy 供排查")
      try:
        device.dump_hierarchy()
      except Exception:
        pass

  batch_timeout = max(profile.qa_resolve_batch_douyin_timeout, 18.0)
  ids = collect_aweme_ids_after_open(
    stream=stream,
    serial=serial,
    timeout_s=batch_timeout,
    poll_interval_s=profile.qa_logcat_stream_poll_interval,
    expected_count=douyin_count,
  )
  _log_url(f"抖音批量 logcat ids={len(ids)} 期望={douyin_count}")
  _return_from_douyin_resolve(device, nav, profile, expected_prompt=expected_prompt)

  if _apply_batch_if_complete(ids):
    return True

  if len(ids) >= 2:
    apply_count = min(len(ids), len(douyin_indices))
    apply_batch_douyin_urls(
      citations, douyin_indices[:apply_count], ids[:apply_count], profile=profile,
    )
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
  target = _find_citation_click_target(
    device,
    citation,
    log=True,
    profile=profile,
  )
  if not target:
    _log_url(f"拒绝点击 #{citation.ref_index or '?'}：无精确元素匹配（禁止坐标点击）")
    return False

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
  global _ref_list_rows_logged
  if not _ref_list_rows_logged and citations:
    first_idx = next((c.ref_index for c in citations if c.ref_index), 0)
    _log_url(f"引用列表待解析 {len(citations)} 条，首条=#{first_idx or '?'}")
    if first_idx:
      root = _detect_ref_list_root_xpath(device, profile)
      _log_target_ref_row(device, expect_index=first_idx, root_xpath=root)
    _ref_list_rows_logged = True
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


def _try_fast_url_after_click(
  device: Any,
  nav: Navigator,
  *,
  profile: GestureProfile,
  serial: str | None,
  stream: LogcatStream | None,
  method: ResolveMethod,
  ref_idx: str,
  channel: CitationChannel,
) -> str:
  """快速路径：logcat aweme_id / link_url 优先，仅短 dumpsys；不做加长重试。"""
  from app.modules.navigator import Page

  logcat_timeout = profile.qa_resolve_logcat_poll_timeout
  short_dumpsys = min(0.6, profile.qa_resolve_url_wait * 0.35)

  time.sleep(0.2)

  if stream is not None and method in ("auto", "logcat"):
    ids = extract_aweme_ids_ordered(stream.text_since_mark())
    if ids:
      url = _douyin_url_from_id(ids[0], profile)
      if url:
        _log_url(f"#{ref_idx} 快速 logcat id={ids[0]} → {url[:96]}")
        return url
    url = poll_logcat_stream_for_url(
      stream,
      timeout_s=logcat_timeout,
      poll_interval_s=profile.qa_resolve_logcat_poll_interval,
    )
    if url:
      _log_url(f"#{ref_idx} 快速 logcat url → {url[:96]}")
      return url

  page_now, _ = nav.current_page()
  if page_now == Page.WEB_DETAIL and method in ("auto", "logcat", "dumpsys"):
    if stream is not None:
      ids = extract_aweme_ids_ordered(stream.text_since_mark())
      if ids:
        url = _douyin_url_from_id(ids[0], profile)
        if url:
          _log_url(
            f"#{ref_idx} 快速 WebActivity logcat id={ids[0]} → {url[:96]}"
          )
          return url
    url = resolve_url_via_dumpsys(device, serial=serial, wait_s=short_dumpsys)
    if url:
      _log_url(f"#{ref_idx} 快速 WebActivity dumpsys → {url[:96]}")
      return url

  if (
    channel == "douyin"
    and profile.qa_douyin_web_validate
    and stream is not None
    and method in ("auto", "logcat")
  ):
    ids = extract_aweme_ids_ordered(stream.text_since_mark())
    if ids:
      url = _douyin_url_from_id(ids[0], profile)
      if url:
        _log_url(f"#{ref_idx} 快速 PC Web logcat id={ids[0]} → {url[:96]}")
        return url

  return ""


def _try_restore_ref_panel(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  ref_idx: str,
) -> bool:
  """引用列表容器消失时，在 CHAT 内上滚尝试恢复思考面板（禁止 back）。"""
  from app.modules.navigator import Page

  if _get_ref_list_bounds(device, profile) is not None:
    return True
  _log_url(f"#{ref_idx} 引用列表不可见，CHAT 内上滚恢复面板")
  for round_i in range(5):
    if _get_ref_list_bounds(device, profile) is not None:
      _log_url(f"#{ref_idx} 引用列表已恢复（上滚 {round_i} 次）")
      return True
    page, _ = nav.current_page()
    if page != Page.CHAT:
      return False
    _scroll_chat_up(device, profile)
    time.sleep(0.28)
  return _get_ref_list_bounds(device, profile) is not None


def _after_fast_miss(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  ref_idx: str,
  expected_prompt: str = "",
) -> None:
  """快速路径未命中：已在 CHAT 时禁止 lite_back（会误退会话），仅校验/恢复面板。"""
  from app.modules.navigator import Page

  page, _ = nav.current_page()
  if page == Page.CHAT:
    _log_url(f"#{ref_idx} 快速未命中且仍在 CHAT，跳过 back")
    if expected_prompt and not _chat_context_ok(
      device, expected_prompt, profile, f"#{ref_idx} 快速未命中",
    ):
      _log_url(f"#{ref_idx} 会话错位，尝试按 prompt 恢复")
      _ensure_target_chat_session(
        device, nav, profile, expected_prompt, f"#{ref_idx} 快速未命中",
      )
    else:
      _try_restore_ref_panel(device, nav, profile, ref_idx=ref_idx)
    time.sleep(profile.qa_resolve_url_post_back_sleep * 0.5)
    return
  _back_to_chat_after_resolve(
    device, nav, profile, ref_idx=ref_idx, expected_prompt=expected_prompt,
  )


def _back_to_chat_after_resolve(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  ref_idx: str,
  expected_prompt: str = "",
  tag: str = "",
) -> None:
  """单条解析结束（成功或失败）后回到聊天页。"""
  from app.modules.navigator import Page

  page, _ = nav.current_page()
  if page == Page.CHAT:
    _log_url(f"#{ref_idx} 已在 CHAT，跳过 lite_back")
    if expected_prompt:
      _ensure_target_chat_session(
        device, nav, profile, expected_prompt, f"#{ref_idx} 返回后",
      )
    else:
      _try_restore_ref_panel(device, nav, profile, ref_idx=ref_idx)
    time.sleep(profile.qa_resolve_url_post_back_sleep)
    return

  _log_url(f"#{ref_idx} 准备返回聊天页")
  nav.recover_from_external_douyin(gentle=True)
  nav.lite_back_to_chat()
  page, _ = nav.current_page()
  if page != Page.CHAT:
    _log_url(f"#{ref_idx} lite_back 未到聊天页({page.name})，safe_back")
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  _reanchor_ref_list_after_back(
    device, profile, nav, tag=tag or f"#{ref_idx} 解析后",
  )
  time.sleep(profile.qa_resolve_url_post_back_sleep)
  if expected_prompt:
    _ensure_target_chat_session(
      device, nav, profile, expected_prompt, f"#{ref_idx} 返回后",
    )


def _resolve_one_citation_url_simple(
  device: Any,
  citation: Citation,
  *,
  nav: Navigator,
  profile: GestureProfile,
  serial: str | None,
  method: ResolveMethod,
  stream: LogcatStream | None = None,
  recent_logcat_urls: list[str] | None = None,
) -> str:
  """60710 单遍：mark → 点击 → logcat → 同屏 dumpsys → lite_back（无 handoff/会话恢复）。"""
  from app.modules.navigator import Page

  serial = serial or _device_serial(device)
  ref_idx = citation.ref_index or "?"
  _log_page(nav, f"#{ref_idx} 解析前")
  _log_url(
    f"开始解析 #{ref_idx} method={method} "
    f"douyin={is_likely_douyin_citation(citation)} title={citation.title[:48]!r}"
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
      timeout_s=profile.qa_resolve_logcat_poll_timeout,
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

  if not url and method in ("auto", "logcat"):
    url = resolve_url_via_dumpsys(device, serial=serial, wait_s=0.6)
    if url:
      _log_url(f"#{ref_idx} dumpsys 同屏命中: {url[:96]}")
    else:
      _log_url(f"#{ref_idx} dumpsys 同屏未命中")

  if not url and method in ("auto", "logcat"):
    page_now, _ = nav.current_page()
    if page_now in (Page.WEB_DETAIL, Page.OTHER_APP):
      _log_url(f"#{ref_idx} 详情页 dumpsys 加长等待")
      url = resolve_url_via_dumpsys(
        device, serial=serial, wait_s=profile.qa_resolve_url_wait,
      )
      if url:
        _log_url(f"#{ref_idx} 详情页 dumpsys 命中: {url[:96]}")
    elif page_now == Page.CHAT:
      _log_url(f"#{ref_idx} 仍在 CHAT，等待详情打开后重试")
      time.sleep(1.0)
      page_now, _ = nav.current_page()
      if page_now != Page.CHAT:
        url = resolve_url_via_dumpsys(
          device, serial=serial, wait_s=profile.qa_resolve_url_wait,
        )
      if not url and stream is not None:
        url = poll_logcat_stream_for_url(
          stream,
          timeout_s=profile.qa_resolve_logcat_poll_timeout,
          poll_interval_s=profile.qa_resolve_logcat_poll_interval,
        )
    if not url and page_now != Page.CHAT:
      _log_url(f"#{ref_idx} 回落 dumpsys 重试（返回聊天后重点）")
      nav.lite_back_to_chat()
      _reanchor_ref_list_after_back(
        device, profile, nav, tag=f"#{ref_idx} dumpsys前", quick=True,
      )
      time.sleep(0.2)
      if stream is not None:
        stream.mark()
      if _ensure_citation_visible(device, citation, profile) and _click_citation(
        device, citation, profile=profile,
      ):
        url = resolve_url_via_dumpsys(
          device, serial=serial, wait_s=profile.qa_resolve_url_wait,
        )

  url = _apply_douyin_stitch_after_click(
    url,
    device=device,
    nav=nav,
    profile=profile,
    serial=serial,
    stream=stream,
    citation=citation,
    ref_idx=str(ref_idx),
    method=method,
  )

  _log_url(f"#{ref_idx} 准备返回聊天页")
  nav.lite_back_to_chat()
  page, _ = nav.current_page()
  if page != Page.CHAT:
    _log_url(f"#{ref_idx} lite_back 未到聊天页({page.name})，safe_back")
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)
  _reanchor_ref_list_after_back(
    device, profile, nav, tag=f"#{ref_idx} 解析后", quick=True,
  )
  time.sleep(profile.qa_resolve_url_post_back_sleep)

  if url:
    from app.modules.douyin_web_resolve import is_douyin_video_url, normalize_aweme_id

    if is_douyin_video_url(url) or normalize_aweme_id(url):
      pass
    else:
      url = _finalize_douyin_url(url, profile)
    if recent_logcat_urls is not None and method in ("auto", "logcat"):
      recent_logcat_urls.append(url)

  return url


def _resolve_thinking_reference_urls_simple(
  device: Any,
  citations: list[Citation],
  *,
  profile: GestureProfile,
  serial: str | None,
  max_refs: int,
  method: ResolveMethod,
) -> list[Citation]:
  """60710 单遍主循环：可选抖音批量 → 逐条解析引用 N/M。"""
  from app.modules.navigator import Page

  nav = Navigator(device)
  serial = serial or _device_serial(device)
  page, _ = nav.current_page()
  if page != Page.CHAT:
    print("[问答] 当前不在聊天页，尝试返回豆包...")
    nav.safe_back_to_chat(max_backs=profile.qa_resolve_url_max_backs)

  limit = max_refs if max_refs > 0 else len(citations)
  attempts = 0
  click_method = method if method in ("auto", "logcat", "dumpsys") else "auto"
  recent_logcat_urls: list[str] = []

  stream = LogcatStream(serial=serial)
  stream.start(settle_s=profile.qa_logcat_stream_settle)
  resolved_by_index: dict[int, str] = {}
  try:
    if (
      profile.qa_resolve_batch_douyin
      and click_method in ("auto", "logcat")
      and sum(1 for c in citations if not c.url) >= 2
    ):
      batch_ok = try_batch_resolve_douyin(
        device, citations, nav=nav, profile=profile, stream=stream,
      )
      if not batch_ok:
        print("[问答] 抖音批量未通过校验，回落逐条 logcat/dumpsys")
    elif not profile.qa_resolve_batch_douyin and profile.qa_resolve_skip_douyin_per_click:
      print("[问答] 抖音批量已关闭，仅解析网页类引用 URL（抖音条目保留无链接）")
    elif not profile.qa_resolve_batch_douyin:
      print("[问答] 抖音批量已关闭，抖音条目走点击→aweme_id 拼接验证（必要时点进 App）")

    pending = _pending_sorted(citations)
    for idx, citation in pending:
      if max_refs > 0 and attempts >= limit:
        break
      if citation.url:
        continue
      print(
        f"[问答] 解析引用 {attempts + 1}/{limit}: "
        f"#{citation.ref_index or '?'} {citation.title[:36]!r}"
      )
      if should_skip_douyin_url_resolve(citation, profile):
        print(
          f"[问答] 跳过抖音引用 URL 逐条点击（保留条目）: "
          f"#{citation.ref_index or '?'} {citation.title[:40]!r}"
        )
        continue
      if not _ensure_citation_visible(device, citation, profile):
        print(f"[问答] 引用不可见，跳过 URL: {citation.title[:40]!r}")
        continue

      url = _resolve_one_citation_url_simple(
        device,
        citation,
        nav=nav,
        profile=profile,
        serial=serial,
        method=click_method,
        stream=stream,
        recent_logcat_urls=recent_logcat_urls,
      )
      if url:
        citation.url = url
        resolved_by_index[idx] = url
        print(
          f"[问答] 引用 URL ({click_method}): "
          f"{citation.ref_index or '?'} -> {url[:80]}"
        )
      else:
        print(f"[问答] 未解析到 URL: {citation.title[:40]!r}")
      attempts += 1
  finally:
    stream.stop()

  out = list(citations)
  for i, c in enumerate(out):
    if i in resolved_by_index:
      c.url = resolved_by_index[i]
  return out


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
  sms_token: str = "",
  sms_device_id: str = "",
) -> str:
  """单条引用：mark → 点击 → 从流/logcat 解析 → lite back。"""
  from app.modules.douyin_handoff import try_resolve_douyin_after_click

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

  if not brute_force:
    url = _try_fast_url_after_click(
      device,
      nav,
      profile=profile,
      serial=serial,
      stream=stream,
      method=method,
      ref_idx=ref_idx,
      channel=channel,
    )
    if (
      not url
      and channel == "douyin"
      and method in ("auto", "logcat")
      and not profile.qa_douyin_web_validate
    ):
      handoff_url, _ = try_resolve_douyin_after_click(
        device,
        nav,
        profile,
        serial=serial,
        stream=stream,
        sms_token=sms_token,
        sms_device_id=sms_device_id,
        batch_feed_swipes=0,
      )
      if handoff_url:
        url = handoff_url
        _log_url(f"#{ref_idx} 快速 Handoff/深链命中: {url[:96]}")
    if (
      url
      and recent_logcat_urls is not None
      and url in recent_logcat_urls[-3:]
      and method in ("auto", "logcat")
    ):
      _log_url(f"#{ref_idx} 重复 URL 丢弃: {url[:80]}")
      url = ""
    if url:
      url = _finalize_douyin_url(url, profile)
      _return_after_fast_url_hit(
        device, nav, profile, ref_idx=ref_idx, expected_prompt=expected_prompt,
      )
      if recent_logcat_urls is not None and method in ("auto", "logcat"):
        recent_logcat_urls.append(url)
      return url
    _log_url(f"#{ref_idx} 快速路径未命中，留待笨办法")
    _after_fast_miss(
      device, nav, profile, ref_idx=ref_idx, expected_prompt=expected_prompt,
    )
    return ""

  time.sleep(0.3)
  from app.modules.navigator import Page

  page_after_click, _ = nav.current_page()
  if page_after_click == Page.WEB_DETAIL and method in ("auto", "logcat"):
    url = ""
    if stream is not None:
      ids = extract_aweme_ids_ordered(stream.text_since_mark())
      if ids:
        url = _douyin_url_from_id(ids[0], profile)
        if url:
          _log_url(f"#{ref_idx} WebActivity logcat id={ids[0]} → {url[:96]}")
    if not url:
      url = resolve_url_via_dumpsys(device, serial=serial, wait_s=1.2)
      if url:
        _log_url(f"#{ref_idx} 点击后 WebActivity dumpsys: {url[:96]}")
    if url:
      url = _finalize_douyin_url(url, profile)
      _ensure_chat_after_resolve(
        device, nav, profile, tag=f"#{ref_idx} web直出", expected_prompt=expected_prompt,
      )
      _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} web直出")
      if expected_prompt and not _chat_context_ok(
        device, expected_prompt, profile, f"#{ref_idx} web直出后",
      ):
        _recover_chat_context(
          device, nav, profile, expected_prompt, f"#{ref_idx} web直出",
        )
      return url

  allow_app_jump = (
    profile.qa_resolve_accept_app_jump and not profile.qa_douyin_web_validate
  )

  if channel == "douyin" and method in ("auto", "logcat") and not profile.qa_douyin_web_validate:
    handoff_url, _ = try_resolve_douyin_after_click(
      device,
      nav,
      profile,
      serial=serial,
      stream=stream,
      sms_token=sms_token,
      sms_device_id=sms_device_id,
      batch_feed_swipes=0,
    )
    if handoff_url:
      handoff_url = _finalize_douyin_url(handoff_url, profile)
      _log_url(f"#{ref_idx} Handoff/深链命中: {handoff_url[:96]}")
      nav.recover_from_external_douyin(gentle=True)
      nav.lite_back_to_chat()
      _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} handoff后")
      if expected_prompt and not _chat_context_ok(
        device, expected_prompt, profile, f"#{ref_idx} handoff后",
      ):
        _reenter_target_chat(nav, expected_prompt, tag=f"#{ref_idx} handoff后")
      return handoff_url

  if (
    channel == "douyin"
    and profile.qa_douyin_web_validate
    and method in ("auto", "logcat")
    and stream is not None
  ):
    ids = extract_aweme_ids_ordered(stream.text_since_mark())
    if ids:
      url = _douyin_url_from_id(ids[0], profile)
      if url:
        _log_url(f"#{ref_idx} PC Web logcat id={ids[0]} → {url[:96]}")
        _ensure_chat_after_resolve(
          device, nav, profile, tag=f"#{ref_idx} logcat id", expected_prompt=expected_prompt,
        )
        _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} logcat id")
        if expected_prompt and not _chat_context_ok(
          device, expected_prompt, profile, f"#{ref_idx} logcat id",
        ):
          _recover_chat_context(
            device, nav, profile, expected_prompt, f"#{ref_idx} logcat",
          )
        return url

  time.sleep(0.1)
  if channel == "douyin" and allow_app_jump and method in ("auto", "logcat"):
    from app.modules.navigator import Page

    page_now, _ = nav.current_page()
    if page_now == Page.WEB_DETAIL:
      url = resolve_url_via_dumpsys(device, serial=serial, wait_s=1.0)
      if url:
        _log_url(f"#{ref_idx} 豆包 WebActivity 同屏命中: {url[:96]}")
        url = _finalize_douyin_url(url, profile)
        _ensure_chat_after_resolve(
          device, nav, profile, tag=f"#{ref_idx} web后", expected_prompt=expected_prompt,
        )
        _reanchor_ref_list_after_back(device, profile, nav, tag=f"#{ref_idx} web后")
        return url
    nav.wait_and_accept_app_jump(timeout=6.0)
    nav.wait_for_aweme_foreground(timeout=8.0)

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

  if (
    not url
    and channel == "douyin"
    and allow_app_jump
    and method in ("auto", "logcat")
  ):
    if nav.accept_app_jump_prompt():
      time.sleep(0.8)
    if stream is not None:
      url = poll_logcat_stream_for_url(
        stream,
        timeout_s=logcat_timeout * 2,
        poll_interval_s=profile.qa_resolve_logcat_poll_interval,
      )
    if not url:
      log_text = (
        stream.text_since_mark()
        if stream is not None
        else dump_logcat_tail(serial=serial, count=120)
      )
      aweme_ids = extract_aweme_ids_ordered(log_text)
      if aweme_ids:
        url = _douyin_url_from_id(aweme_ids[0], profile)
        _log_url(f"#{ref_idx} 打开抖音后 aweme id 重建 URL")

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

  if url:
    url = _finalize_douyin_url(url, profile)

  _back_to_chat_after_resolve(
    device, nav, profile, ref_idx=ref_idx, expected_prompt=expected_prompt,
  )

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
  sms_token: str = "",
  sms_device_id: str = "",
  phase_deadline: float | None = None,
  recover_state: list[int] | None = None,
) -> int:
  """按 pass 解析 pending 子集；返回累计 attempts。"""
  limit = max_refs if max_refs > 0 else len(citations)
  attempts = attempts_so_far
  recover_max = profile.qa_resolve_recover_max_per_task
  recover_used = recover_state if recover_state is not None else [0]

  def _phase_timed_out() -> bool:
    if phase_deadline is None:
      return False
    if time.time() >= phase_deadline:
      budget = profile.qa_resolve_url_phase_budget_sec
      print(
        f"[问答] {pass_label} 中止：URL 阶段超时"
        f"（预算 {budget:.0f}s），返回已解析 partial"
      )
      return True
    return False

  for idx, citation in pending:
    if _phase_timed_out():
      break
    if max_refs > 0 and attempts >= limit:
      break
    if citation.url:
      continue
    if not _chat_context_ok(device, expected_prompt, profile, pass_label):
      if recover_max > 0 and recover_used[0] >= recover_max:
        print(
          f"[问答] {pass_label} 中止：会话恢复次数达上限"
          f"（{recover_max}）"
        )
        break
      recover_used[0] += 1
      if not _recover_chat_context(
        device,
        nav,
        profile,
        expected_prompt,
        pass_label,
      ):
        print(f"[问答] {pass_label} 中止：已离开目标会话")
        break
      if not _chat_context_ok(
        device, expected_prompt, profile, f"{pass_label} 恢复后",
      ):
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
      sms_token=sms_token,
      sms_device_id=sms_device_id,
    )

    if url:
      citation.url = url
      resolved_by_index[idx] = url
      print(
        f"[问答] {pass_label} URL: "
        f"{citation.ref_index or '?'} -> {url[:80]}"
      )
      if profile.qa_url_reachability_check and (
        brute_force or not profile.qa_url_reachability_brute_only
      ):
        from app.modules.qa_url_reachability import (
          apply_url_reachability,
          citation_reachability_line,
        )

        apply_url_reachability(
          citation, timeout=profile.qa_url_reachability_timeout,
        )
        print(f"[URL可达] {pass_label} {citation_reachability_line(citation)}")
    else:
      print(
        f"[问答] {pass_label} 未解析到 URL: "
        f"#{citation.ref_index or '?'} {citation.title[:40]!r}"
      )

    if expected_prompt and not _chat_context_ok(
      device, expected_prompt, profile, f"{pass_label} #{citation.ref_index or '?'}"
    ):
      if recover_max > 0 and recover_used[0] >= recover_max:
        print(
          f"[问答] {pass_label} 中止：会话恢复次数达上限"
          f"（{recover_max}）"
        )
        break
      recover_used[0] += 1
      if not _recover_chat_context(
        device,
        nav,
        profile,
        expected_prompt,
        f"{pass_label} #{citation.ref_index or '?'}",
      ):
        print(f"[问答] {pass_label} 中止：无法恢复目标会话")
        break

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
  expected_answer: str = "",
  sms_token: str = "",
  sms_device_id: str = "",
) -> list[Citation]:
  """
  逐条点击思考引用，解析真实 HTTP 链接写回 Citation.url。

  分三阶段：抖音批量 → 快速逐条（logcat/短 dumpsys）→ 笨办法补齐剩余无 URL。
  method: logcat（默认）、auto（logcat→dumpsys）、dumpsys；net 见 qa_reference_net。
  expected_answer：目标会话回答正文片段，用于错位后精准重进会话。
  """
  set_expected_answer_snippet(expected_answer)
  reset_url_resolve_log_state()
  p = profile or GestureProfile()
  _apply_session_guard_env(p)

  if getattr(p, "qa_resolve_simple_mode", False):
    serial = serial or _device_serial(device)
    return _resolve_thinking_reference_urls_simple(
      device,
      citations,
      profile=p,
      serial=serial,
      max_refs=max_refs,
      method=method,
    )

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
  phase_budget = p.qa_resolve_url_phase_budget_sec
  phase_deadline = (
    time.time() + phase_budget if phase_budget > 0 else None
  )
  recover_state = [0]

  def _phase_timed_out(tag: str = "") -> bool:
    if phase_deadline is None:
      return False
    if time.time() >= phase_deadline:
      suffix = f"（{tag}）" if tag else ""
      print(
        f"[问答] URL 阶段超时{suffix}（预算 {phase_budget:.0f}s），"
        "返回已解析 partial"
      )
      return True
    return False

  stream = LogcatStream(serial=serial)
  stream.start(settle_s=p.qa_logcat_stream_settle)
  resolved_by_index: dict[int, str] = {}
  try:
    need_douyin_login = (
      p.qa_douyin_ensure_login_before_batch
      and not p.qa_douyin_web_validate
      and click_method in ("auto", "logcat")
    )
    if need_douyin_login:
      from app.modules.douyin_sms_login import ensure_douyin_logged_in

      ensure_douyin_logged_in(
        device, nav, token=sms_token, device_id=sms_device_id,
      )
    elif p.qa_douyin_web_validate and click_method in ("auto", "logcat"):
      print("[问答] PC Web 验证已开启，跳过批次前抖音 SMS 登录")
    if nav.is_aweme_foreground() or nav.is_app_jump_prompt():
      print("[问答] URL 解析前仍在外部 App，尝试温和回豆包...")
      nav.recover_from_external_douyin(gentle=True)
      if expected_prompt:
        nav.reenter_chat_by_prompt(expected_prompt)
      time.sleep(p.qa_resolve_url_post_back_sleep)
    pending_before = _pending_sorted(citations)
    use_batch = (
      p.qa_resolve_batch_douyin
      and click_method in ("auto", "logcat")
      and len(pending_before) >= 2
      and any(
        classify_citation_channel(c) == "douyin"
        for _, c in pending_before
      )
    )
    if use_batch:
      if p.qa_douyin_web_validate:
        print("[问答] PC Web 模式：batch 收 aweme id，回填时 PC 多格式验证")
      if not _phase_timed_out("批量前"):
        batch_douyin_ok = try_batch_resolve_douyin(
          device,
          citations,
          nav=nav,
          profile=p,
          stream=stream,
          expected_prompt=expected_prompt,
          sms_token=sms_token,
          sms_device_id=sms_device_id,
        )
        if not batch_douyin_ok:
          print("[问答] 抖音批量未通过校验，回落快速逐条 + 笨办法补齐")
    elif not p.qa_resolve_batch_douyin:
      print("[问答] 抖音批量已关闭，抖音条目走快速逐条/笨办法逐条点击")

    if _phase_timed_out("快速逐条前"):
      return citations

    pending = _pending_sorted(citations)
    resolved_before = len(citations) - len(pending)
    tech_pending = list(pending)
    tech_pending.sort(
      key=lambda x: (
        x[1].ref_index or 9999,
        x[1].bounds[1] if x[1].bounds else 0,
      ),
    )
    if tech_pending:
      print(
        f"[问答] 快速逐条 {len(tech_pending)} 条"
        f"（已有 URL {resolved_before}/{len(citations)}，"
        "logcat/短 dumpsys，失败留笨办法）"
      )
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
        pass_label="快速",
        channels=None,
        brute_force=False,
        apply_skip_policy=False,
        max_refs=limit,
        attempts_so_far=0,
        resolved_by_index=resolved_by_index,
        expected_prompt=expected_prompt,
        sms_token=sms_token,
        sms_device_id=sms_device_id,
        phase_deadline=phase_deadline,
        recover_state=recover_state,
      )

    if _phase_timed_out("笨办法前"):
      return citations

    if getattr(p, "qa_resolve_skip_brute_pass", False):
      pending = _pending_sorted(citations)
      if pending:
        print(
          f"[问答] 跳过笨办法补齐 {len(pending)} 条"
          "（qa_resolve_skip_brute_pass / QA_URL_SKIP_BRUTE）"
        )
      return citations

    pending = _pending_sorted(citations)
    if pending:
      resolved_fast = len(citations) - len(pending)
      print(
        f"[问答] 笨办法补齐 {len(pending)} 条"
        f"（快速已得 {resolved_fast}/{len(citations)}）"
      )
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
        max_refs=0,
        attempts_so_far=0,
        resolved_by_index=resolved_by_index,
        expected_prompt=expected_prompt,
        sms_token=sms_token,
        sms_device_id=sms_device_id,
        phase_deadline=phase_deadline,
        recover_state=recover_state,
      )
  finally:
    stream.stop()

  out = list(citations)
  for i, c in enumerate(out):
    if i in resolved_by_index:
      c.url = resolved_by_index[i]

  from app.modules.qa_url_reachability import summarize_unreachable

  checked, bad = summarize_unreachable(out)
  if checked:
    print(
      f"[URL可达] 汇总: 已探测 {checked} 条，不可达 {bad} 条"
      "（404/5xx 等为豆包或站点问题，非采集系统错误）"
    )
  return out
