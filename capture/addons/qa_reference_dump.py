# -*- coding: utf-8 -*-
"""
mitmproxy addon：落盘豆包业务响应，供 qa_reference_net 零点击解析引用 URL。

用法:
  mitmdump -s capture/addons/qa_reference_dump.py \\
    --set qa_ref_dump_dir=logs/qa_capture_net

或配合 run_capture.py 启动 mitm 后手动加载本脚本。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from mitmproxy import ctx, http

_HOST_MARKERS = (
  "larus",
  "volc",
  "doubao",
  "byte",
  "snssdk",
  "iesdouyin",
  "zijieapi",
)
_BODY_MARKERS = (
  "reference",
  "link_url",
  "doc_id",
  "aweme",
  "search_reference",
  "tv_reference",
  "iesdouyin",
  "snssdk",
)


def _dump_dir() -> Path:
  raw = getattr(ctx.options, "qa_ref_dump_dir", None) or os.environ.get(
    "QA_REF_DUMP_DIR", "logs/qa_capture_net"
  )
  p = Path(str(raw)).expanduser()
  p.mkdir(parents=True, exist_ok=True)
  return p


def _should_capture(flow: http.HTTPFlow) -> bool:
  host = (flow.request.pretty_host or "").lower()
  if not any(m in host for m in _HOST_MARKERS):
    return False
  if not flow.response or not flow.response.content:
    return False
  try:
    body = flow.response.get_text(strict=False) or ""
  except Exception:
    body = ""
  if len(body) < 20:
    return False
  low = body.lower()
  return any(m in low for m in _BODY_MARKERS)


def _safe_name(s: str) -> str:
  return re.sub(r"[^\w.\-]+", "_", s)[:80]


class QaReferenceDump:
  def response(self, flow: http.HTTPFlow) -> None:
    if not _should_capture(flow):
      return
    try:
      body = flow.response.get_text(strict=False) or ""
    except Exception:
      return
    host = flow.request.pretty_host or "unknown"
    path_hash = hashlib.md5(
      f"{flow.request.method}:{flow.request.pretty_url}:{body[:200]}".encode(),
      usedforsecurity=False,
    ).hexdigest()[:12]
    ts = time.strftime("%Y%m%d_%H%M%S")
    fname = f"{ts}_{_safe_name(host)}_{path_hash}.json"
    out = {
      "captured_at": time.time(),
      "method": flow.request.method,
      "url": flow.request.pretty_url,
      "host": host,
      "status_code": flow.response.status_code,
      "body": body,
    }
    target = _dump_dir() / fname
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log.info(f"qa_reference_dump: {fname} ({len(body)} bytes)")


addons = [QaReferenceDump()]
