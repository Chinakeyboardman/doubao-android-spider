# -*- coding: utf-8 -*-
"""
UI 节点树精准点击：只点 uiautomator 元素，禁止裸坐标。

高价值能力：
1. 列表内滚到 xpath 命中（RecyclerView 虚拟化）
2. 按 bounds 反查可点击节点（替代 d.click(cx,cy)）
3. 引用 row_index_only 二次确认（可见行标题对齐）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from app.config.gesture_profile import GestureProfile
from app.modules.chat_ui_heuristics import display_wh
from app.modules.qa_hierarchy import (
  REFERENCE_CONTENT_RID,
  REFERENCE_INDEX_RID,
  SOURCE_ITEM_RID,
)


@dataclass
class NodeClickResult:
  ok: bool
  strategy: str
  message: str
  bounds: list[int] | None = None
  rid: str = ""
  text: str = ""


def _log(msg: str) -> None:
  print(f"[节点点击] {msg}")


def _bounds_of(el: Any) -> list[int] | None:
  try:
    b = el.bounds
    if b and len(b) >= 4:
      return [int(b[0]), int(b[1]), int(b[2]), int(b[3])]
  except Exception:
    return None
  return None


def _center(bounds: list[int] | tuple[int, ...]) -> tuple[int, int]:
  return (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2


def _point_in(bounds: list[int] | tuple[int, ...], x: int, y: int, *, margin: int = 8) -> bool:
  return (
    bounds[0] - margin <= x <= bounds[2] + margin
    and bounds[1] - margin <= y <= bounds[3] + margin
  )


def _overlap_ratio(a: list[int] | tuple[int, ...], b: list[int] | tuple[int, ...]) -> float:
  ax1, ay1, ax2, ay2 = a
  bx1, by1, bx2, by2 = b
  ix1, iy1 = max(ax1, bx1), max(ay1, by1)
  ix2, iy2 = min(ax2, bx2), min(ay2, by2)
  if ix2 <= ix1 or iy2 <= iy1:
    return 0.0
  inter = (ix2 - ix1) * (iy2 - iy1)
  area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
  return inter / area_a


def click_element(el: Any, *, tag: str = "") -> NodeClickResult:
  """对已定位元素执行 click；禁止坐标兜底。"""
  if el is None:
    return NodeClickResult(False, "none", f"{tag} 无元素，拒绝坐标点击")
  bounds = _bounds_of(el)
  info = getattr(el, "info", None) or {}
  rid = str(info.get("resourceName") or info.get("resourceId") or "")
  text = str(info.get("text") or info.get("contentDescription") or "")[:80]
  try:
    el.click()
    _log(f"点击成功 {tag} rid={rid.rsplit('/', 1)[-1]} bounds={bounds} text={text!r}")
    return NodeClickResult(True, "element.click", "ok", bounds=bounds, rid=rid, text=text)
  except Exception as exc:
    _log(f"点击失败 {tag}: {exc}（不回落坐标）")
    return NodeClickResult(False, "element.click", str(exc), bounds=bounds, rid=rid, text=text)


def find_clickable_covering_bounds(
  device: Any,
  target_bounds: list[int] | tuple[int, ...],
  *,
  prefer_clickable: bool = True,
  min_overlap: float = 0.35,
) -> Any | None:
  """
  在当前 hierarchy 中找覆盖 target_bounds 的可点击节点。
  用于替代「算中心点再 d.click(cx,cy)」。
  """
  if len(target_bounds) != 4:
    return None
  tx, ty = _center(target_bounds)
  best: Any | None = None
  best_score = -1.0
  try:
    nodes = device.xpath("//*").all()
  except Exception:
    return None

  for el in nodes:
    info = getattr(el, "info", None) or {}
    clickable = bool(info.get("clickable"))
    if prefer_clickable and not clickable:
      continue
    b = _bounds_of(el)
    if not b:
      continue
    if not _point_in(b, tx, ty) and _overlap_ratio(b, target_bounds) < min_overlap:
      continue
    # 面积越小越精确（避免点到整屏容器）
    area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    overlap = _overlap_ratio(b, target_bounds)
    score = overlap * 1_000_000 / area
    if clickable:
      score += 1000
    if score > best_score:
      best_score = score
      best = el
  return best


def click_bounds_via_node(
  device: Any,
  target_bounds: list[int] | tuple[int, ...],
  *,
  tag: str = "bounds",
) -> NodeClickResult:
  """用节点树反查后点击；找不到则拒绝，绝不 d.click(x,y)。"""
  el = find_clickable_covering_bounds(device, target_bounds)
  if not el:
    _log(f"拒绝坐标点击 {tag}：bounds={list(target_bounds)} 无覆盖可点节点")
    return NodeClickResult(
      False,
      "refuse_coordinate",
      "无覆盖可点节点",
      bounds=list(target_bounds),
    )
  return click_element(el, tag=tag)


def scroll_container(
  device: Any,
  bounds: list[int],
  direction: str,
  *,
  duration: float = 0.22,
) -> bool:
  """在容器 bounds 内 swipe（仅滚动，不是点击）。"""
  if len(bounds) != 4:
    return False
  x1, y1, x2, y2 = bounds
  cx = (x1 + x2) // 2
  span = max(y2 - y1, 80)
  try:
    if direction == "up":
      device.swipe(cx, y1 + int(span * 0.28), cx, y1 + int(span * 0.78), duration)
    else:
      device.swipe(cx, y1 + int(span * 0.78), cx, y1 + int(span * 0.28), duration)
    return True
  except Exception as exc:
    _log(f"容器滚动失败: {exc}")
    return False


def scroll_until_xpath(
  device: Any,
  xpath: str,
  *,
  container_bounds: list[int] | None,
  direction_hint: str = "down",
  max_swipes: int = 12,
  settle_s: float = 0.22,
  get_container: Callable[[], list[int] | None] | None = None,
) -> Any | None:
  """
  滚动直至 xpath 命中（RecyclerView 虚拟列表）。
  命中后返回元素；失败返回 None。
  """
  last_sig: str | None = None
  stall = 0
  for i in range(max_swipes + 1):
    try:
      el = device.xpath(xpath).get(timeout=0.4)
    except Exception:
      el = None
    if el:
      _log(f"xpath 命中 swipe={i} xp={xpath[:72]}")
      return el

    bounds = container_bounds
    if get_container is not None:
      bounds = get_container() or bounds
    if not bounds:
      _log(f"无容器 bounds，停止滚动 swipe={i}")
      return None

    sig = f"{bounds}"
    if sig == last_sig:
      stall += 1
      if stall >= 2:
        _log(f"滚动停滞，放弃 xpath={xpath[:72]}")
        return None
    else:
      stall = 0
    last_sig = sig

    _log(f"xpath 未命中，列表{direction_hint}滚 swipe={i}")
    if not scroll_container(device, bounds, direction_hint):
      return None
    time.sleep(settle_s)
  return None


def citation_index_xpath(root_xpath: str, ref_index: int) -> str:
  return (
    f'{root_xpath}//*[@resource-id="{REFERENCE_INDEX_RID}" '
    f'and @text="{ref_index}."]'
  )


def citation_row_xpath(root_xpath: str, ref_index: int) -> str:
  return (
    f'{root_xpath}//*[@resource-id="{SOURCE_ITEM_RID}"]'
    f'[.//*[@resource-id="{REFERENCE_INDEX_RID}" and @text="{ref_index}."]]'
  )


def scroll_citation_index_into_view(
  device: Any,
  *,
  root_xpath: str,
  ref_index: int,
  container_bounds: list[int] | None,
  direction_hint: str = "down",
  max_swipes: int = 12,
  get_container: Callable[[], list[int] | None] | None = None,
) -> Any | None:
  """滚到引用序号节点进入 DOM，再返回对应行（优先 ll_source_item）。"""
  if ref_index <= 0:
    return None
  idx_xp = citation_index_xpath(root_xpath, ref_index)
  hit = scroll_until_xpath(
    device,
    idx_xp,
    container_bounds=container_bounds,
    direction_hint=direction_hint,
    max_swipes=max_swipes,
    get_container=get_container,
  )
  if not hit:
    return None
  row_xp = citation_row_xpath(root_xpath, ref_index)
  try:
    row = device.xpath(row_xp).get(timeout=0.4)
    if row:
      return row
  except Exception:
    pass
  return hit


def visible_row_title_for_index(
  device: Any,
  *,
  root_xpath: str,
  ref_index: int,
) -> str:
  """从当前屏可见行读取指定序号的标题（bounds Y 对齐）。"""
  want = f"{ref_index}."
  try:
    rows = device.xpath(f'{root_xpath}//*[@resource-id="{SOURCE_ITEM_RID}"]').all()
  except Exception:
    return ""
  for row in rows:
    b = _bounds_of(row)
    if not b:
      continue
    y1, y2 = b[1], b[3]
    idx_text = ""
    title_text = ""
    try:
      for node in device.xpath(
        f'{root_xpath}//*[@resource-id="{REFERENCE_INDEX_RID}"]'
      ).all():
        nb = _bounds_of(node)
        if not nb:
          continue
        cy = (nb[1] + nb[3]) // 2
        if y1 - 8 <= cy <= y2 + 8:
          idx_text = str((node.info or {}).get("text") or "").strip()
          break
    except Exception:
      pass
    if idx_text != want:
      continue
    try:
      best = ""
      best_dist = 10_000
      mid = (y1 + y2) // 2
      for node in device.xpath(
        f'{root_xpath}//*[@resource-id="{REFERENCE_CONTENT_RID}"]'
      ).all():
        nb = _bounds_of(node)
        if not nb:
          continue
        cy = (nb[1] + nb[3]) // 2
        if y1 - 8 <= cy <= y2 + 8:
          text = str((node.info or {}).get("text") or "").strip()
          dist = abs(cy - mid)
          if text and dist < best_dist:
            best, best_dist = text, dist
      title_text = best
    except Exception:
      pass
    return title_text
  return ""


def title_prefix_match(expected: str, actual: str) -> bool:
  exp = "".join((expected or "").split())
  act = "".join((actual or "").split())
  if not exp or not act:
    return False
  if exp == act or exp in act or act in exp:
    return True
  for n in (24, 16, 10):
    if len(exp) >= n and exp[:n] in act:
      return True
  return False


def row_index_only_confirmed(
  device: Any,
  *,
  root_xpath: str,
  ref_index: int,
  expected_title: str,
) -> tuple[bool, str]:
  """
  row_index_only 策略二次确认：可见行标题须与期望标题对齐。
  可见行标题为空时拒绝（避免点错行）。
  """
  if ref_index <= 0:
    return False, "无序号"
  live = visible_row_title_for_index(
    device, root_xpath=root_xpath, ref_index=ref_index,
  )
  if not live:
    return False, f"可见行 #{ref_index} 标题为空，拒绝 row_index_only"
  if expected_title and not title_prefix_match(expected_title, live):
    return False, (
      f"可见行标题不匹配 期望={expected_title[:36]!r} 实际={live[:36]!r}"
    )
  return True, live


def viewport_band(
  h: int,
  profile: GestureProfile | None = None,
) -> tuple[int, int]:
  p = profile or GestureProfile()
  return int(h * p.qa_resolve_viewport_y0), int(h * p.qa_resolve_viewport_y1)


def element_in_viewport(
  el: Any,
  device: Any,
  profile: GestureProfile | None = None,
) -> bool:
  b = _bounds_of(el)
  if not b:
    return False
  _, h = display_wh(device, profile=profile)
  y0, y1 = viewport_band(h, profile)
  cy = (b[1] + b[3]) // 2
  return y0 <= cy <= y1
