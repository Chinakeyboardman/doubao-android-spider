# -*- coding: utf-8 -*-
"""
从 mitm 抓包落盘 JSON 中零点击解析思考引用 URL。

配合 capture/addons/qa_reference_dump.py 使用：
  mitmdump -s capture/addons/qa_reference_dump.py --set qa_ref_dump_dir=logs/qa_capture_net

qa_capture --resolve-method net 会在采集结束后扫描 dump 目录并对齐标题。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.modules.qa_hierarchy import Citation
from app.modules.qa_reference_urls import _iesdouyin_url

_TITLE_KEYS = ("title", "name", "content", "text", "reference_title", "ref_title")
_URL_KEYS = ("url", "link", "link_url", "web_url", "jump_url", "href")
_DOC_KEYS = ("doc_id", "docid", "item_id", "itemid", "aweme_id", "video_id", "group_id")
_SOURCE_KEYS = ("source", "site", "domain", "author", "from")


@dataclass
class NetReference:
  title: str = ""
  url: str = ""
  doc_id: str = ""
  source: str = ""
  raw: dict[str, Any] = field(default_factory=dict)


def _norm_title_key(title: str) -> str:
  return "".join((title or "").split())[:80]


def _pick_str(obj: dict[str, Any], keys: tuple[str, ...]) -> str:
  for k in keys:
    v = obj.get(k)
    if isinstance(v, str) and v.strip():
      return v.strip()
  return ""


def _walk_for_references(node: Any, out: list[NetReference]) -> None:
  if isinstance(node, dict):
    title = _pick_str(node, _TITLE_KEYS)
    url = _pick_str(node, _URL_KEYS)
    doc_id = _pick_str(node, {*_DOC_KEYS})
    source = _pick_str(node, _SOURCE_KEYS)
    if doc_id and not url:
      url = _iesdouyin_url(doc_id)
    if title and (url or doc_id):
      out.append(
        NetReference(
          title=title[:500],
          url=url,
          doc_id=doc_id,
          source=source,
          raw=node,
        )
      )
    for v in node.values():
      _walk_for_references(v, out)
  elif isinstance(node, list):
    for item in node:
      _walk_for_references(item, out)


def parse_reference_json_text(text: str) -> list[NetReference]:
  refs: list[NetReference] = []
  try:
    data = json.loads(text)
  except json.JSONDecodeError:
    for m in _LINK_URL_RE.finditer(text):
      refs.append(NetReference(url=m.group(1)))
    for m in _SNSSDK_RE.finditer(text):
      refs.append(NetReference(url=_iesdouyin_url(m.group(1)), doc_id=m.group(1)))
    return refs
  if isinstance(data, dict) and isinstance(data.get("body"), str):
    body = data["body"]
    try:
      _walk_for_references(json.loads(body), refs)
    except json.JSONDecodeError:
      for m in _LINK_URL_RE.finditer(body):
        refs.append(NetReference(url=m.group(1)))
      for m in _SNSSDK_RE.finditer(body):
        refs.append(NetReference(url=_iesdouyin_url(m.group(1)), doc_id=m.group(1)))
  _walk_for_references(data, refs)
  return _dedupe_net_refs(refs)


_LINK_URL_RE = re.compile(r"link_url=(https?://[^\s,}\"]+)")
_SNSSDK_RE = re.compile(r"snssdk1128://aweme/detail/(\d+)", re.I)


def _dedupe_net_refs(refs: list[NetReference]) -> list[NetReference]:
  seen: set[str] = set()
  out: list[NetReference] = []
  for r in refs:
    key = f"{_norm_title_key(r.title)}|{r.url}|{r.doc_id}"
    if key in seen:
      continue
    seen.add(key)
    out.append(r)
  return out


def load_net_references_from_dir(
  dump_dir: str | Path,
  *,
  since_mtime: float | None = None,
) -> list[NetReference]:
  """扫描 mitm addon 落盘目录，合并所有引用条目。"""
  root = Path(dump_dir)
  if not root.is_dir():
    return []
  refs: list[NetReference] = []
  for path in sorted(root.glob("*.json")):
    if since_mtime is not None and path.stat().st_mtime < since_mtime:
      continue
    try:
      text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
      continue
    refs.extend(parse_reference_json_text(text))
  return _dedupe_net_refs(refs)


def _title_match_score(citation_title: str, net_title: str) -> int:
  a = _norm_title_key(citation_title)
  b = _norm_title_key(net_title)
  if not a or not b:
    return 0
  if a == b:
    return 100
  if a in b or b in a:
    return 80
  prefix = min(20, len(a), len(b))
  if a[:prefix] == b[:prefix]:
    return 60
  return 0


def align_net_urls_to_citations(
  citations: list[Citation],
  net_refs: list[NetReference],
) -> list[Citation]:
  """按标题相似度将网络抓包引用 URL 写回 Citation。"""
  if not citations or not net_refs:
    return citations
  used: set[int] = set()
  for citation in citations:
    if citation.url:
      continue
    best_idx = -1
    best_score = 0
    for i, nr in enumerate(net_refs):
      if i in used:
        continue
      score = _title_match_score(citation.title, nr.title)
      if nr.url and score > best_score:
        best_score = score
        best_idx = i
    if best_idx >= 0 and best_score >= 60:
      nr = net_refs[best_idx]
      citation.url = nr.url
      if nr.source and not citation.source:
        citation.source = nr.source
      used.add(best_idx)
  return citations


def resolve_urls_from_net_dump(
  citations: list[Citation],
  dump_dir: str | Path,
  *,
  since_mtime: float | None = None,
) -> list[Citation]:
  """零点击：从 mitm 落盘目录解析并写回引用 URL。"""
  net_refs = load_net_references_from_dir(dump_dir, since_mtime=since_mtime)
  if not net_refs:
    print(f"[问答] 网络抓包目录无引用数据: {dump_dir}")
    return citations
  print(f"[问答] 网络抓包解析到 {len(net_refs)} 条候选引用")
  return align_net_urls_to_citations(citations, net_refs)
