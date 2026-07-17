# -*- coding: utf-8 -*-
"""
PC 端 Web 辅助：aweme_id → 多域名 URL 拼装与验证。

核心：19 位 aweme_id 为键，HTTP 模板（iesdouyin / douyin.com/video / jingxuan modal_id 等）
统一经 build_url_candidates → validate_aweme_multi_format → 写 best_verified 原始 URL。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

import requests

AWEME_ID_RE = re.compile(r"^\d{19}$")
_V_DOUYIN_RE = re.compile(r"https?://v\.douyin\.com/[A-Za-z0-9_\-]+/?", re.I)
_SNSSDK_AWEME_IN_TEXT_RE = re.compile(
  r"snssdk(?:1128|1180)://aweme/detail/(\d{19})",
  re.I,
)
_AWEME_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
  re.compile(r"/share/video/(\d{19})", re.I),
  re.compile(r"/video/(\d{19})", re.I),
  re.compile(r"[?&]modal_id=(\d{19})", re.I),
  re.compile(r"[?&]aweme_id=(\d{19})", re.I),
  _SNSSDK_AWEME_IN_TEXT_RE,
)
_CAPTCHA_MARKERS = (
  "验证码",
  "captcha",
  "verify_center",
  "人机验证",
  "ttgcaptcha",
)
_DEFAULT_MOBILE_UA = (
  "Mozilla/5.0 (Linux; Android 13; vivo V2301A) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
_DEFAULT_DESKTOP_UA = (
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 默认 HTTP 模板优先级（best_verified 顺序）
DEFAULT_HTTP_FORMAT_IDS: tuple[str, ...] = (
  "douyin_jingxuan_modal",
  "douyin_video",
  "iesdouyin_share",
  "iesdouyin_share_query",
)

_last_request_at: float = 0.0


@dataclass(frozen=True)
class AwemeUrlFormat:
  """HTTP URL 模板（非深链）。"""

  format_id: str
  label: str
  builder: Callable[[str, str], str]


@dataclass(frozen=True)
class DouyinWebResolveResult:
  """PC Web 解析/验证结果。"""

  aweme_id: str
  share_url: str
  canonical_url: str = ""
  short_url: str = ""
  verified: bool = False
  status: str = "pending"
  http_status: int | None = None
  note: str = ""
  redirect_chain: tuple[str, ...] = field(default_factory=tuple)
  captcha_suspected: bool = False
  strategy: str = ""
  format_id: str = ""


def _build_jingxuan_modal(aid: str, _device_id: str = "") -> str:
  return f"https://www.douyin.com/jingxuan?modal_id={aid}"


def _build_douyin_video(aid: str, _device_id: str = "") -> str:
  return f"https://www.douyin.com/video/{aid}"


def _build_iesdouyin_share(aid: str, _device_id: str = "") -> str:
  return f"https://www.iesdouyin.com/share/video/{aid}"


def _build_iesdouyin_share_query(aid: str, device_id: str = "") -> str:
  base = f"https://www.iesdouyin.com/share/video/{aid}/"
  if device_id:
    qs = urlencode({"did": device_id, "app": "aweme", "utm_source": "copy_link"})
    return f"{base}?{qs}"
  qs = urlencode({"region": "CN", "app": "aweme", "utm_source": "copy_link"})
  return f"{base}?{qs}"


AWEME_URL_FORMATS: dict[str, AwemeUrlFormat] = {
  "douyin_jingxuan_modal": AwemeUrlFormat(
    "douyin_jingxuan_modal", "抖音精选 modal_id", _build_jingxuan_modal,
  ),
  "douyin_video": AwemeUrlFormat(
    "douyin_video", "douyin.com/video", _build_douyin_video,
  ),
  "iesdouyin_share": AwemeUrlFormat(
    "iesdouyin_share", "iesdouyin share", _build_iesdouyin_share,
  ),
  "iesdouyin_share_query": AwemeUrlFormat(
    "iesdouyin_share_query", "iesdouyin share+query", _build_iesdouyin_share_query,
  ),
}


def normalize_aweme_id(value: str) -> str:
  """从 URL / 深链 / 纯数字提取 19 位 aweme_id。"""
  raw = (value or "").strip()
  if AWEME_ID_RE.match(raw):
    return raw
  for pat in _AWEME_ID_PATTERNS:
    m = pat.search(raw)
    if m:
      return m.group(1)
  return ""


def build_url_candidates(
  aweme_id: str,
  *,
  device_id: str = "",
  format_ids: tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
  """按优先级返回 (format_id, url) 列表。"""
  aid = normalize_aweme_id(aweme_id)
  if not aid:
    return []
  order = format_ids or DEFAULT_HTTP_FORMAT_IDS
  out: list[tuple[str, str]] = []
  seen: set[str] = set()
  for fid in order:
    fmt = AWEME_URL_FORMATS.get(fid)
    if not fmt:
      continue
    url = fmt.builder(aid, device_id)
    if url and url not in seen:
      seen.add(url)
      out.append((fid, url))
  return out


def build_url_from_aweme_id(
  aweme_id: str,
  *,
  device_id: str = "",
  format_ids: tuple[str, ...] | None = None,
) -> str:
  """拼装首个候选 URL（未验证）。"""
  cands = build_url_candidates(aweme_id, device_id=device_id, format_ids=format_ids)
  return cands[0][1] if cands else ""


def build_share_url(aweme_id: str, *, device_id: str = "") -> str:
  """兼容旧名：iesdouyin share（带 device_id 时用 query 模板）。"""
  aid = normalize_aweme_id(aweme_id)
  if not aid:
    return ""
  if device_id:
    return _build_iesdouyin_share_query(aid, device_id)
  return _build_iesdouyin_share(aid)


def build_canonical_url(aweme_id: str) -> str:
  aid = normalize_aweme_id(aweme_id)
  return _build_douyin_video(aid) if aid else ""


def is_douyin_video_url(url: str) -> bool:
  """是否抖音视频类 HTTP 链接（含 jingxuan modal / iesdouyin / douyin video）。"""
  raw = (url or "").lower()
  if not raw:
    return False
  if "iesdouyin.com" in raw:
    return True
  if "douyin.com/video/" in raw:
    return True
  if "douyin.com/jingxuan" in raw and "modal_id=" in raw:
    return True
  if "v.douyin.com" in raw:
    return True
  return bool(normalize_aweme_id(url))


def _rate_limit(min_interval_s: float) -> None:
  global _last_request_at
  if min_interval_s <= 0:
    return
  now = time.time()
  wait = min_interval_s - (now - _last_request_at)
  if wait > 0:
    time.sleep(wait)
  _last_request_at = time.time()


def _detect_captcha(text: str, final_url: str) -> bool:
  if normalize_aweme_id(final_url):
    return False
  blob = f"{text}\n{final_url}".lower()
  return any(m.lower() in blob for m in _CAPTCHA_MARKERS if m != "sec_sdk")


def _request(
  url: str,
  *,
  user_agent: str,
  allow_redirects: bool,
  timeout: float,
  device_id: str = "",
  referer: str = "",
) -> requests.Response:
  headers: dict[str, str] = {
    "User-Agent": user_agent,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  }
  if referer:
    headers["Referer"] = referer
  if device_id:
    headers["X-Tt-Did"] = device_id
  return requests.get(
    url,
    headers=headers,
    allow_redirects=allow_redirects,
    timeout=timeout,
  )


def _validate_single_url(
  url: str,
  expected_id: str,
  *,
  format_id: str = "",
  device_id: str = "",
  timeout: float = 12.0,
  min_interval_s: float = 0.8,
  mobile_user_agent: str = _DEFAULT_MOBILE_UA,
  desktop_user_agent: str = _DEFAULT_DESKTOP_UA,
) -> DouyinWebResolveResult:
  """对单个 HTTP URL 做 desktop 302 / follow / mobile 校验。"""
  canonical = build_canonical_url(expected_id)
  _rate_limit(min_interval_s)
  try:
    desk = _request(
      url,
      user_agent=desktop_user_agent,
      allow_redirects=False,
      timeout=timeout,
      device_id=device_id,
      referer="https://www.douyin.com/",
    )
  except requests.exceptions.RequestException as exc:
    return DouyinWebResolveResult(
      aweme_id=expected_id,
      share_url=url,
      canonical_url=canonical,
      verified=False,
      status="connection_error",
      note=f"{format_id} desktop 探测失败: {exc.__class__.__name__}",
      strategy=format_id or "single",
      format_id=format_id,
    )

  chain: list[str] = [url]
  if desk.status_code in (301, 302, 303, 307, 308):
    loc = desk.headers.get("Location", "")
    if loc:
      chain.append(loc)
      final_id = normalize_aweme_id(loc)
      captcha = _detect_captcha("", loc)
      verified = final_id == expected_id and not captcha
      return DouyinWebResolveResult(
        aweme_id=expected_id,
        share_url=url,
        canonical_url=canonical,
        verified=verified,
        status="ok" if verified else ("captcha" if captcha else "id_mismatch"),
        http_status=int(desk.status_code),
        note=f"{format_id} desktop 302" + (" 通过" if verified else " id 不一致"),
        redirect_chain=tuple(chain),
        captcha_suspected=captcha,
        strategy=format_id or "desktop_redirect",
        format_id=format_id,
      )

  _rate_limit(min_interval_s)
  try:
    desk_follow = _request(
      url,
      user_agent=desktop_user_agent,
      allow_redirects=True,
      timeout=timeout,
      device_id=device_id,
      referer="https://www.douyin.com/",
    )
    chain = [h.url for h in desk_follow.history] + [str(desk_follow.url)]
    final_id = normalize_aweme_id(str(desk_follow.url))
    body = desk_follow.text[:12000]
    captcha = _detect_captcha(body, str(desk_follow.url))
    if final_id == expected_id and not captcha:
      return DouyinWebResolveResult(
        aweme_id=expected_id,
        share_url=url,
        canonical_url=canonical,
        verified=True,
        status="ok",
        http_status=int(desk_follow.status_code),
        note=f"{format_id} desktop follow 通过",
        redirect_chain=tuple(chain),
        strategy=format_id or "desktop_follow",
        format_id=format_id,
      )
    id_in_body = expected_id in body or f"modal_id={expected_id}" in body
    if id_in_body and not captcha and 200 <= int(desk_follow.status_code) < 400:
      return DouyinWebResolveResult(
        aweme_id=expected_id,
        share_url=url,
        canonical_url=canonical,
        verified=True,
        status="ok",
        http_status=int(desk_follow.status_code),
        note=f"{format_id} desktop 页内 id 通过",
        redirect_chain=tuple(chain),
        strategy=format_id or "desktop_body",
        format_id=format_id,
      )
  except requests.exceptions.RequestException:
    pass

  _rate_limit(min_interval_s)
  try:
    mob = _request(
      url,
      user_agent=mobile_user_agent,
      allow_redirects=True,
      timeout=timeout,
      device_id=device_id,
      referer=url,
    )
  except requests.exceptions.RequestException as exc:
    return DouyinWebResolveResult(
      aweme_id=expected_id,
      share_url=url,
      canonical_url=canonical,
      verified=False,
      status="connection_error",
      note=f"{format_id} mobile 失败: {exc.__class__.__name__}",
      strategy=format_id or "mobile_page",
      format_id=format_id,
    )

  chain = [h.url for h in mob.history] + [str(mob.url)]
  body = mob.text[:20000]
  captcha = _detect_captcha(body, str(mob.url))
  id_in_body = (
    expected_id in body
    or f"/video/{expected_id}" in body
    or f"modal_id={expected_id}" in body
  )
  verified = not captcha and id_in_body and 200 <= int(mob.status_code) < 400
  return DouyinWebResolveResult(
    aweme_id=expected_id,
    share_url=url,
    canonical_url=canonical,
    verified=verified,
    status="ok" if verified else ("captcha" if captcha else "mobile_unverified"),
    http_status=int(mob.status_code),
    note=f"{format_id} mobile " + ("通过" if verified else "未确认"),
    redirect_chain=tuple(chain),
    captcha_suspected=captcha,
    strategy=format_id or "mobile_page",
    format_id=format_id,
  )


def validate_aweme_multi_format(
  aweme_id: str,
  *,
  device_id: str = "",
  format_ids: tuple[str, ...] | None = None,
  timeout: float = 12.0,
  min_interval_s: float = 0.8,
  mobile_user_agent: str = _DEFAULT_MOBILE_UA,
  desktop_user_agent: str = _DEFAULT_DESKTOP_UA,
) -> DouyinWebResolveResult:
  """级联验证多 HTTP 模板，返回首个 verified 的原始 URL。"""
  aid = normalize_aweme_id(aweme_id)
  if not aid:
    return DouyinWebResolveResult(
      aweme_id="",
      share_url="",
      status="invalid_aweme_id",
      note="aweme_id 须为 19 位数字",
    )

  candidates = build_url_candidates(aid, device_id=device_id, format_ids=format_ids)
  if not candidates:
    return DouyinWebResolveResult(
      aweme_id=aid,
      share_url="",
      status="no_candidates",
      note="无可用 URL 模板",
    )

  last = DouyinWebResolveResult(
    aweme_id=aid,
    share_url=candidates[0][1],
    status="all_failed",
    note="全部格式未通过",
  )
  for fid, url in candidates:
    result = _validate_single_url(
      url,
      aid,
      format_id=fid,
      device_id=device_id,
      timeout=timeout,
      min_interval_s=min_interval_s,
      mobile_user_agent=mobile_user_agent,
      desktop_user_agent=desktop_user_agent,
    )
    last = result
    if result.verified:
      print(f"  [PC Web] 格式 {fid} 验证通过: {url[:80]}")
      return result

  return last


def validate_aweme_via_web(
  aweme_id: str,
  **kwargs: Any,
) -> DouyinWebResolveResult:
  """兼容旧 API：仅验证 iesdouyin share 一种格式。"""
  aid = normalize_aweme_id(aweme_id)
  if not aid:
    return DouyinWebResolveResult(
      aweme_id="",
      share_url="",
      status="invalid_aweme_id",
      note="aweme_id 须为 19 位数字",
    )
  url = _build_iesdouyin_share(aid)
  return _validate_single_url(
    url, aid, format_id="iesdouyin_share", **kwargs,
  )


def expand_short_link(
  short_url: str,
  *,
  timeout: float = 12.0,
  user_agent: str = _DEFAULT_MOBILE_UA,
  min_interval_s: float = 0.8,
) -> DouyinWebResolveResult:
  """v.douyin.com 短链 → aweme_id + 首个候选 URL。"""
  raw = (short_url or "").strip()
  if not _V_DOUYIN_RE.match(raw):
    return DouyinWebResolveResult(
      aweme_id="",
      share_url="",
      status="invalid_short_url",
      note="非 v.douyin.com 短链",
    )
  _rate_limit(min_interval_s)
  try:
    resp = _request(
      raw,
      user_agent=user_agent,
      allow_redirects=True,
      timeout=timeout,
    )
  except requests.exceptions.Timeout:
    return DouyinWebResolveResult(
      aweme_id="",
      share_url=raw,
      status="timeout",
      note="短链展开超时",
    )
  except requests.exceptions.RequestException as exc:
    return DouyinWebResolveResult(
      aweme_id="",
      share_url=raw,
      status="connection_error",
      note=f"短链展开失败: {exc.__class__.__name__}",
    )

  chain = tuple(h.url for h in resp.history) + (str(resp.url),)
  aid = normalize_aweme_id(str(resp.url))
  if not aid:
    aid = normalize_aweme_id(resp.text[:8000])
  share = build_url_from_aweme_id(aid) if aid else ""
  captcha = False if aid else _detect_captcha(resp.text[:12000], str(resp.url))
  verified = bool(aid) and not captcha
  return DouyinWebResolveResult(
    aweme_id=aid,
    share_url=share,
    canonical_url=build_canonical_url(aid) if aid else "",
    short_url=raw.split("?")[0].rstrip("/") + "/",
    verified=verified,
    status="ok" if verified else ("captcha" if captcha else "no_aweme_id"),
    http_status=int(resp.status_code),
    note="短链展开成功" if verified else "短链未解析到 19 位 id",
    redirect_chain=chain,
    captcha_suspected=captcha,
    strategy="expand_short_link",
  )


def resolve_verified_url(
  aweme_id: str,
  *,
  device_id: str = "",
  require_web_verify: bool = True,
  format_ids: tuple[str, ...] | None = None,
  fallback_unverified: bool = True,
  **kwargs: Any,
) -> str:
  """
  从 aweme_id 产出 Citation.url：验证通过则返回 best_verified 原始 URL。
  """
  if not require_web_verify:
    return build_url_from_aweme_id(aweme_id, device_id=device_id, format_ids=format_ids)

  result = validate_aweme_multi_format(
    aweme_id,
    device_id=device_id,
    format_ids=format_ids,
    **kwargs,
  )
  if result.verified and result.share_url:
    return result.share_url
  if fallback_unverified:
    cands = build_url_candidates(
      aweme_id, device_id=device_id, format_ids=format_ids,
    )
    if cands:
      print(f"  [PC Web] 验证未通过，回落未验证 URL: {cands[0][1][:80]}")
      return cands[0][1]
  return ""


# 兼容旧名
resolve_verified_share_url = resolve_verified_url


def extract_aweme_id_from_any_url(url: str) -> str:
  """从任意抖音相关 URL 抽 aweme_id。"""
  raw = (url or "").strip()
  if not raw:
    return ""
  if _V_DOUYIN_RE.match(raw):
    expanded = expand_short_link(raw, min_interval_s=0)
    return expanded.aweme_id
  return normalize_aweme_id(raw)
