# -*- coding: utf-8 -*-
"""问答采集产出质量校验（与黄金样本 145156 对齐）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 已验证可全量解析引用 URL 的推荐路径（Honor PCT-AL10）
GOLDEN_PROMPT = "折叠屏手机推荐"
GOLDEN_MODE = "fast"

_MIN_ANSWER_LEN = 80
_MIN_THINKING_REFS = 1
# 抽检：超过该比例引用未解析到 URL 则整条不可用（无需连通性校验）
DEFAULT_MIN_URL_RESOLVE_RATIO = 0.5


def _min_urls_required(ref_count: int, max_missing_ratio: float) -> int:
  """至少须有条目带 URL；允许缺失数 = floor(ref_count * ratio)。"""
  if ref_count <= 0:
    return 0
  max_missing = int(ref_count * max_missing_ratio)
  return ref_count - max_missing


def _url_resolve_passes(
  url_count: int,
  ref_count: int,
  *,
  max_missing_ratio: float | None,
  require_all_urls: bool,
  allow_missing_douyin_urls: bool,
  thinking_references: list,
) -> tuple[bool, str]:
  if ref_count <= 0:
    return False, "0 条引用"

  if allow_missing_douyin_urls:
    from app.modules.qa_hierarchy import Citation
    from app.modules.qa_reference_urls import is_likely_douyin_citation

    def _as_citation(r: object) -> Citation:
      if isinstance(r, Citation):
        return r
      if isinstance(r, dict):
        return Citation(
          title=str(r.get("title") or ""),
          url=str(r.get("url") or ""),
          source=str(r.get("source") or ""),
          ref_index=int(r.get("ref_index") or 0),
        )
      return Citation(title="")

    required_refs = [
      r for r in thinking_references
      if not is_likely_douyin_citation(_as_citation(r))
    ]
    required_url_count = sum(1 for r in required_refs if _ref_has_url(r))
    if required_refs:
      if max_missing_ratio is not None:
        min_required = _min_urls_required(len(required_refs), max_missing_ratio)
        ok = required_url_count >= min_required
        detail = (
          f"网页 {required_url_count}/{len(required_refs)}（至少 {min_required} 条有链接），"
          f"合计 {url_count}/{ref_count}"
        )
      else:
        ok = required_url_count == len(required_refs)
        detail = f"网页 {required_url_count}/{len(required_refs)}，合计 {url_count}/{ref_count}"
      return ok, detail
    if max_missing_ratio is not None:
      min_required = _min_urls_required(ref_count, max_missing_ratio)
      ok = url_count >= min_required
      detail = f"{url_count}/{ref_count}（仅抖音引用，至少 {min_required} 条有链接）"
      return ok, detail
    ok = url_count > 0
    detail = f"{url_count}/{ref_count}（仅抖音引用）"
    return ok, detail

  if max_missing_ratio is not None:
    min_required = _min_urls_required(ref_count, max_missing_ratio)
    ok = url_count >= min_required
    detail = (
      f"{url_count}/{ref_count}（缺失>{int(ref_count * max_missing_ratio)} 条即失败，"
      f"至少 {min_required} 条有链接）"
    )
    return ok, detail

  if require_all_urls:
    ok = url_count == ref_count
    return ok, f"{url_count}/{ref_count}（要求全量）"

  ok = url_count > 0
  return ok, f"{url_count}/{ref_count}"


@dataclass
class QaQualityReport:
  session_dir: str
  ok: bool
  score: int
  checks: list[tuple[str, bool, str]] = field(default_factory=list)
  url_count: int = 0
  ref_count: int = 0

  def lines(self) -> list[str]:
    out = [
      f"[质量] 目录: {self.session_dir}",
      f"[质量] 得分: {self.score}/100  {'通过' if self.ok else '未通过'}",
    ]
    for name, passed, detail in self.checks:
      mark = "✓" if passed else "✗"
      out.append(f"  {mark} {name}: {detail}")
    if self.ref_count:
      out.append(f"[质量] 引用 URL: {self.url_count}/{self.ref_count}")
    return out


def _ref_has_url(ref: object) -> bool:
  return bool(getattr(ref, "url", None) or (isinstance(ref, dict) and ref.get("url")))


def validate_qa_session(
  *,
  session_dir: str,
  answer_body: str,
  thinking: str,
  thinking_references: list,
  screenshots: list[str],
  stitched_screenshot: str,
  mode: str = "fast",
  require_all_urls: bool = True,
  allow_missing_douyin_urls: bool = False,
  min_url_resolve_ratio: float | None = None,
  allow_no_references: bool = True,
) -> QaQualityReport:
  """按黄金样本标准校验一次采集产出。"""
  checks: list[tuple[str, bool, str]] = []
  score = 0

  ans_ok = len(answer_body or "") >= _MIN_ANSWER_LEN
  checks.append(("正文", ans_ok, f"{len(answer_body or '')} 字"))
  if ans_ok:
    score += 25

  ref_count = len(thinking_references or [])
  no_search_refs = ref_count == 0

  think_ok = bool((thinking or "").strip())
  if not think_ok and no_search_refs and allow_no_references and ans_ok:
    think_ok = True
    checks.append(("思考 markdown", think_ok, "0 字（本次无联网思考块）"))
    score += 15
  else:
    checks.append(("思考 markdown", think_ok, f"{len(thinking or '')} 字"))
    if think_ok:
      score += 15

  if no_search_refs and allow_no_references and ans_ok:
    refs_ok = True
    checks.append(("思考引用", refs_ok, "0 条（本次无联网引用，如实记录）"))
    score += 20
    url_count = 0
    url_ok = True
    checks.append(("引用 URL", url_ok, "无引用可解析"))
    score += 30
  else:
    refs_ok = ref_count >= _MIN_THINKING_REFS
    checks.append(("思考引用", refs_ok, f"{ref_count} 条"))
    if refs_ok:
      score += 20

    url_count = sum(1 for r in (thinking_references or []) if _ref_has_url(r))
    if ref_count:
      url_ok, url_detail = _url_resolve_passes(
        url_count,
        ref_count,
        max_missing_ratio=min_url_resolve_ratio,
        require_all_urls=require_all_urls,
        allow_missing_douyin_urls=allow_missing_douyin_urls,
        thinking_references=thinking_references or [],
      )
      checks.append(("引用 URL", url_ok, url_detail))
      if url_ok:
        score += 30
    else:
      url_ok = False

  shot_ok = len(screenshots or []) >= 1
  checks.append(("分屏截图", shot_ok, f"{len(screenshots or [])} 张"))
  if shot_ok:
    score += 5

  stitch_ok = bool(stitched_screenshot) and os.path.isfile(stitched_screenshot)
  checks.append(("拼接长图", stitch_ok, stitched_screenshot or "无"))
  if stitch_ok:
    score += 5

  golden_path = mode == GOLDEN_MODE
  checks.append(
    ("推荐模式 fast",
     golden_path,
     f"当前 {mode!r}（黄金样本为 fast）"),
  )

  ok = ans_ok and think_ok and shot_ok and stitch_ok and refs_ok and url_ok
  return QaQualityReport(
    session_dir=session_dir,
    ok=ok,
    score=score,
    checks=checks,
    url_count=url_count if ref_count else 0,
    ref_count=ref_count,
  )


def validate_record_dict(
  record: dict,
  *,
  require_all_urls: bool = True,
  min_url_resolve_ratio: float | None = None,
) -> QaQualityReport:
  refs = record.get("thinking_references") or []

  class _Ref:
    def __init__(self, d: dict):
      self.url = d.get("url") or ""

  return validate_qa_session(
    session_dir=record.get("session_dir") or "",
    answer_body=record.get("answer_body") or "",
    thinking=record.get("thinking") or "",
    thinking_references=[_Ref(r) if isinstance(r, dict) else r for r in refs],
    screenshots=record.get("screenshots") or [],
    stitched_screenshot=record.get("stitched_screenshot") or "",
    mode=record.get("mode") or "fast",
    require_all_urls=require_all_urls,
    min_url_resolve_ratio=min_url_resolve_ratio,
  )
