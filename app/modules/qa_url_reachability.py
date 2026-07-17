# -*- coding: utf-8 -*-
"""
引用 URL 可达性探测：区分「解析到链接」与「链接可正常访问」。

404/5xx 等记为豆包或站点侧问题，不计入采集系统错误。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from app.modules.qa_hierarchy import Citation

_DEFAULT_UA = (
  "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
  "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


@dataclass(frozen=True)
class UrlReachResult:
  """HTTP 探测结果（非采集系统故障）。"""

  reachable: bool
  status: str
  http_status: int | None = None
  note: str = ""
  final_url: str = ""


def _normalize_probe_url(url: str) -> str:
  u = (url or "").strip()
  if u.startswith("www."):
    return f"https://{u}"
  return u


def probe_url_reachability(
  url: str,
  *,
  timeout: float = 10.0,
  user_agent: str = _DEFAULT_UA,
) -> UrlReachResult:
  """
  轻量 HTTP 探测：HEAD → 必要时 GET，跟随重定向。

  status 示例：ok / http_404 / http_403 / timeout / connection_error / invalid_url
  """
  raw = _normalize_probe_url(url)
  if not raw.startswith(("http://", "https://")):
    return UrlReachResult(
      reachable=False,
      status="invalid_url",
      note="非 http(s) 链接，未探测",
    )
  if not urlparse(raw).netloc:
    return UrlReachResult(
      reachable=False,
      status="invalid_url",
      note="URL 无主机名",
    )

  headers = {"User-Agent": user_agent}
  try:
    resp = requests.head(
      raw, allow_redirects=True, timeout=timeout, headers=headers,
    )
    if resp.status_code in (405, 501):
      resp = requests.get(
        raw,
        allow_redirects=True,
        timeout=timeout,
        headers=headers,
        stream=True,
      )
      resp.close()
  except requests.exceptions.Timeout:
    return UrlReachResult(
      reachable=False,
      status="timeout",
      note="请求超时（可能为站点慢或网络问题，非采集系统错误）",
    )
  except requests.exceptions.SSLError as exc:
    return UrlReachResult(
      reachable=False,
      status="ssl_error",
      note=f"SSL 错误: {exc.__class__.__name__}",
    )
  except requests.exceptions.RequestException as exc:
    return UrlReachResult(
      reachable=False,
      status="connection_error",
      note=f"连接失败: {exc.__class__.__name__}",
    )

  code = int(resp.status_code)
  final = str(resp.url or raw)
  if 200 <= code < 400:
    return UrlReachResult(
      reachable=True,
      status="ok",
      http_status=code,
      note="可访问",
      final_url=final,
    )
  if code == 404:
    return UrlReachResult(
      reachable=False,
      status="http_404",
      http_status=code,
      note="页面不存在(404)，豆包/站点问题",
      final_url=final,
    )
  if code == 410:
    return UrlReachResult(
      reachable=False,
      status="http_410",
      http_status=code,
      note="页面已删除(410)，豆包/站点问题",
      final_url=final,
    )
  if code == 403:
    return UrlReachResult(
      reachable=False,
      status="http_403",
      http_status=code,
      note="访问被拒绝(403)，可能为站点风控",
      final_url=final,
    )
  if code >= 500:
    return UrlReachResult(
      reachable=False,
      status=f"http_{code}",
      http_status=code,
      note=f"服务端错误({code})，豆包/站点问题",
      final_url=final,
    )
  return UrlReachResult(
    reachable=False,
    status=f"http_{code}",
    http_status=code,
    note=f"HTTP {code}",
    final_url=final,
  )


def apply_url_reachability(
  citation: Citation,
  *,
  timeout: float = 10.0,
) -> UrlReachResult:
  """探测并写回 Citation 的可达性字段。"""
  if not citation.url:
    citation.url_reachable = None
    citation.url_http_status = 0
    citation.url_check_status = ""
    citation.url_check_note = ""
    return UrlReachResult(reachable=False, status="no_url", note="无 URL")

  result = probe_url_reachability(citation.url, timeout=timeout)
  citation.url_reachable = result.reachable
  citation.url_http_status = result.http_status or 0
  citation.url_check_status = result.status
  citation.url_check_note = result.note
  return result


def citation_reachability_line(citation: Citation) -> str:
  """日志用单行摘要。"""
  idx = citation.ref_index or "?"
  if not citation.url:
    return f"#{idx} 无 URL"
  if not citation.url_check_status:
    return f"#{idx} 未探测"
  flag = "可达" if citation.url_reachable else "不可达"
  code = citation.url_http_status or "-"
  return (
    f"#{idx} {flag} status={citation.url_check_status} "
    f"http={code} {citation.url_check_note}"
  )


def summarize_unreachable(refs: list[Citation | dict[str, Any]]) -> tuple[int, int]:
  """返回 (已探测数, 不可达数)。"""
  checked = 0
  bad = 0
  for ref in refs:
    if isinstance(ref, Citation):
      c = ref
    else:
      c = Citation(
        title=str(ref.get("title") or ""),
        url=str(ref.get("url") or ""),
        url_reachable=ref.get("url_reachable"),
        url_http_status=int(ref.get("url_http_status") or 0),
        url_check_status=str(ref.get("url_check_status") or ""),
        url_check_note=str(ref.get("url_check_note") or ""),
      )
    if not c.url or not c.url_check_status:
      continue
    checked += 1
    if c.url_reachable is False:
      bad += 1
  return checked, bad
