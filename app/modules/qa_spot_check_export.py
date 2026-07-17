# -*- coding: utf-8 -*-
"""签单提示词 → 抽检明细 CSV 映射与读写。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.modules.qa_hierarchy import Citation
from app.modules.qa_quality import QaQualityReport

SPOT_CHECK_COLUMNS: tuple[str, ...] = (
  "项目名称",
  "抽检日期",
  "明细ID",
  "抽检明细编号",
  "任务编号",
  "任务批次编号",
  "提示词",
  "渠道关键词",
  "关键词编号",
  "AI平台代码",
  "AI平台名称",
  "终端平台",
  "任务状态",
  "抽检时间",
  "修改时间",
  "回答字数",
  "思考字数",
  "引用条数",
  "质量分级",
  "意图名称",
  "意图编号",
  "词包编号",
  "一级分类",
  "合作标识",
  "品牌编号",
  "品牌名称",
  "AI回答正文",
  "AI思考内容",
  "引用列表",
)

AI_PLATFORM_CODE = "DB"
AI_PLATFORM_NAME = "豆包"
TERMINAL_PLATFORM = "APP"
TASK_STATUS_DONE = "4"

SIGNED_REQUIRED_COLUMNS = (
  "项目名称",
  "提示词编号",
  "提示词",
  "意图编号",
  "意图名称",
  "词包编号",
  "合作标识",
  "品牌编号",
  "品牌名称",
)

_TITLE_SOURCE_RE = re.compile(r"^(?P<title>.+?)[（(](?P<source>[^）)]+)[）)]\s*$")
_THINKING_SECTION_RE = re.compile(
  r"###\s*思考过程\s*\n+(.*?)(?=\n###\s|\Z)",
  re.DOTALL,
)
_KEYWORD_LINE_RE = re.compile(r"\*\*搜索关键词：\*\*\s*(.+)", re.MULTILINE)
_SEARCH_PLACEHOLDER_RE = re.compile(r"^搜索\s*\d+\s*个关键词")

_DOMAIN_SOURCE_MAP: dict[str, str] = {
  "pconline.com.cn": "太平洋科技",
  "it168.com": "IT168",
  "zhidx.com": "智东西",
  "zol.com.cn": "中关村在线",
  "ifeng.com": "凤凰网",
  "toutiao.com": "今日头条",
  "iesdouyin.com": "抖音",
  "douyin.com": "抖音",
  "phonearena.com": "PhoneArena",
  "chinaaet.cn": "电子技术应用",
}


@dataclass
class SignedPromptRow:
  """签单提示词导出行。"""

  project_name: str
  keyword_id: str
  prompt: str
  intent_id: str
  intent_name: str
  keyword_pack_id: str
  category: str
  cooperation: str
  brand_id: str
  brand_name: str
  raw: dict[str, str] = field(default_factory=dict)

  @classmethod
  def from_csv_row(cls, row: dict[str, str]) -> SignedPromptRow:
    category = (
      row.get("一级分类(PP/PL/CN)", "").strip()
      or row.get("词包一级分类", "").strip()
    )
    return cls(
      project_name=row.get("项目名称", "").strip(),
      keyword_id=row.get("提示词编号", "").strip(),
      prompt=row.get("提示词", "").strip(),
      intent_id=row.get("意图编号", "").strip(),
      intent_name=row.get("意图名称", "").strip(),
      keyword_pack_id=row.get("词包编号", "").strip(),
      category=category,
      cooperation=row.get("合作标识", "").strip(),
      brand_id=row.get("品牌编号", "").strip(),
      brand_name=row.get("品牌名称", "").strip(),
      raw=dict(row),
    )


@dataclass
class SpotCheckRow:
  """抽检明细一行（29 列）。"""

  project_name: str = ""
  check_date: str = ""
  detail_id: int = 0
  detail_code: str = ""
  task_code: str = ""
  batch_code: str = ""
  prompt: str = ""
  channel_keyword: str = ""
  keyword_id: str = ""
  ai_platform_code: str = AI_PLATFORM_CODE
  ai_platform_name: str = AI_PLATFORM_NAME
  terminal_platform: str = TERMINAL_PLATFORM
  task_status: str = TASK_STATUS_DONE
  checked_at: str = ""
  modified_at: str = ""
  answer_chars: int = 0
  thinking_chars: int = 0
  citation_count: int = 0
  quality_grade: str = ""
  intent_name: str = ""
  intent_id: str = ""
  keyword_pack_id: str = ""
  category: str = ""
  cooperation: str = ""
  brand_id: str = ""
  brand_name: str = ""
  answer_body: str = ""
  thinking_body: str = ""
  citations_json: str = ""

  def to_csv_dict(self) -> dict[str, Any]:
    return {
      "项目名称": self.project_name,
      "抽检日期": self.check_date,
      "明细ID": self.detail_id,
      "抽检明细编号": self.detail_code,
      "任务编号": self.task_code,
      "任务批次编号": self.batch_code,
      "提示词": self.prompt,
      "渠道关键词": self.channel_keyword,
      "关键词编号": self.keyword_id,
      "AI平台代码": self.ai_platform_code,
      "AI平台名称": self.ai_platform_name,
      "终端平台": self.terminal_platform,
      "任务状态": self.task_status,
      "抽检时间": self.checked_at,
      "修改时间": self.modified_at,
      "回答字数": self.answer_chars,
      "思考字数": self.thinking_chars,
      "引用条数": self.citation_count,
      "质量分级": self.quality_grade,
      "意图名称": self.intent_name,
      "意图编号": self.intent_id,
      "词包编号": self.keyword_pack_id,
      "一级分类": self.category,
      "合作标识": self.cooperation,
      "品牌编号": self.brand_id,
      "品牌名称": self.brand_name,
      "AI回答正文": self.answer_body,
      "AI思考内容": self.thinking_body,
      "引用列表": self.citations_json,
    }


@dataclass
class SpotCheckBatchMeta:
  """批次元信息（任务编号、明细 ID 计数）。"""

  task_code: str
  next_detail_id: int = 900001
  check_date: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> SpotCheckBatchMeta:
    return cls(
      task_code=str(data.get("task_code") or ""),
      next_detail_id=int(data.get("next_detail_id") or 900001),
      check_date=str(data.get("check_date") or ""),
    )


def _normalize_signed_cell(value: Any) -> str:
  if value is None:
    return ""
  if isinstance(value, float) and value != value:
    return ""
  if hasattr(value, "strftime"):
    try:
      return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
      return str(value).strip()
  return str(value).strip()


def _normalize_signed_row_dict(row: dict[str, Any]) -> dict[str, str]:
  return {str(k).strip(): _normalize_signed_cell(v) for k, v in row.items()}


def _parse_signed_prompt_rows(
  raw_rows: list[dict[str, Any]],
  *,
  source_path: str,
) -> list[SignedPromptRow]:
  if not raw_rows:
    raise ValueError(f"签单文件无数据行: {source_path}")

  fieldnames = list(raw_rows[0].keys())
  missing = [c for c in SIGNED_REQUIRED_COLUMNS if c not in fieldnames]
  if missing:
    raise ValueError(f"签单文件缺少列: {missing}（{source_path}）")

  rows: list[SignedPromptRow] = []
  for i, raw in enumerate(raw_rows, start=2):
    signed = SignedPromptRow.from_csv_row(_normalize_signed_row_dict(raw))
    if not signed.prompt or not signed.keyword_id:
      raise ValueError(f"签单文件第 {i} 行缺少提示词或提示词编号（{source_path}）")
    rows.append(signed)
  return rows


def _load_signed_prompt_rows_from_csv(csv_path: str) -> list[dict[str, Any]]:
  with open(csv_path, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    if not reader.fieldnames:
      raise ValueError(f"签单 CSV 无表头: {csv_path}")
    return list(reader)


def _load_signed_prompt_rows_from_xlsx(xlsx_path: str) -> list[dict[str, Any]]:
  try:
    import openpyxl
  except ImportError as exc:
    raise ImportError("读取 xlsx 需要 openpyxl，请 pip install openpyxl") from exc

  wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
  try:
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
      raise ValueError(f"签单 xlsx 无表头: {xlsx_path}")
    columns = [str(c).strip() if c is not None else "" for c in header]
    out: list[dict[str, Any]] = []
    for row in rows_iter:
      if row is None:
        continue
      if all(cell is None or str(cell).strip() == "" for cell in row):
        continue
      out.append(dict(zip(columns, row)))
    return out
  finally:
    wb.close()


def load_signed_prompts(path: str) -> list[SignedPromptRow]:
  """读取签单提示词 CSV 或 xlsx。"""
  if not os.path.isfile(path):
    raise FileNotFoundError(f"签单提示词文件不存在: {path}")

  ext = os.path.splitext(path)[1].lower()
  if ext == ".csv":
    raw_rows = _load_signed_prompt_rows_from_csv(path)
  elif ext in (".xlsx", ".xlsm", ".xltx"):
    raw_rows = _load_signed_prompt_rows_from_xlsx(path)
  else:
    raise ValueError(f"不支持的签单文件格式: {path}（仅 .csv / .xlsx）")

  return _parse_signed_prompt_rows(raw_rows, source_path=path)


def dedupe_signed_prompts(rows: list[SignedPromptRow]) -> list[SignedPromptRow]:
  """相同提示词（归一化后）仅保留首条，避免重复采集与冗余存储。"""
  seen: set[str] = set()
  out: list[SignedPromptRow] = []
  for row in rows:
    key = "".join((row.prompt or "").split())
    if not key or key in seen:
      continue
    seen.add(key)
    out.append(row)
  return out


def select_pilot_rows(rows: list[SignedPromptRow], n: int = 10) -> list[SignedPromptRow]:
  """按意图 round-robin 选取试点行，覆盖尽量多意图。"""
  if n <= 0 or n >= len(rows):
    return list(rows)

  by_intent: dict[str, list[SignedPromptRow]] = defaultdict(list)
  for row in rows:
    by_intent[row.intent_name].append(row)

  intent_names = sorted(by_intent.keys())
  picked: list[SignedPromptRow] = []
  idx = 0
  while len(picked) < n:
    intent = intent_names[idx % len(intent_names)]
    pool = by_intent[intent]
    pick_index = idx // len(intent_names)
    if pick_index < len(pool):
      candidate = pool[pick_index]
      if candidate not in picked:
        picked.append(candidate)
    idx += 1
    if idx > len(rows) * 2:
      break
  return picked[:n]


def make_task_code(check_date: str) -> str:
  digest = hashlib.md5(check_date.encode("utf-8")).hexdigest()[:32].upper()
  return f"TN{digest}"


def make_detail_code(keyword_id: str) -> str:
  suffix = re.sub(r"[^A-Za-z0-9]", "", keyword_id)[-32:]
  if len(suffix) < 32:
    suffix = hashlib.md5(keyword_id.encode("utf-8")).hexdigest()[:32].upper()
  return f"TD{suffix.upper()}"


def extract_thinking_narrative(thinking_md: str) -> str:
  """从 thinking markdown 提取抽检用思考正文。"""
  text = (thinking_md or "").strip()
  if not text:
    return ""

  match = _THINKING_SECTION_RE.search(text)
  narrative = match.group(1).strip() if match else ""
  if narrative and not _SEARCH_PLACEHOLDER_RE.match(narrative):
    return narrative

  keywords = [m.group(1).strip() for m in _KEYWORD_LINE_RE.finditer(text)]
  if keywords:
    return "；".join(f"搜索关键词：{kw}" for kw in keywords)

  if narrative:
    return narrative

  header_match = re.match(r"^##\s*(.+)", text, re.MULTILINE)
  if header_match:
    return header_match.group(1).strip()
  return text[:500]


def _parse_title_source(title: str) -> tuple[str, str]:
  m = _TITLE_SOURCE_RE.match((title or "").strip())
  if not m:
    return (title or "").strip(), ""
  return m.group("title").strip(), m.group("source").strip()


def _source_from_url(url: str) -> str:
  if not url:
    return ""
  host = urlparse(url).netloc.lower().replace("www.", "")
  for domain, name in _DOMAIN_SOURCE_MAP.items():
    if domain in host:
      return name
  return host.split(".")[0] if host else ""


def _normalize_citation(ref: Citation | dict[str, Any]) -> Citation:
  if isinstance(ref, Citation):
    return ref
  return Citation(
    title=str(ref.get("title") or ""),
    url=str(ref.get("url") or ""),
    source=str(ref.get("source") or ""),
    desc=str(ref.get("desc") or ""),
    ref_index=int(ref.get("ref_index") or 0),
    group=str(ref.get("group") or ""),
    url_reachable=ref.get("url_reachable"),
    url_http_status=int(ref.get("url_http_status") or 0),
    url_check_status=str(ref.get("url_check_status") or ""),
    url_check_note=str(ref.get("url_check_note") or ""),
  )


def citations_to_spot_check_list(refs: list[Citation | dict[str, Any]]) -> list[dict[str, Any]]:
  """thinking_references → xlsx 引用列表结构。"""
  out: list[dict[str, Any]] = []
  sorted_refs = sorted(
    [_normalize_citation(r) for r in refs],
    key=lambda r: r.ref_index if r.ref_index > 0 else 10_000,
  )
  for i, ref in enumerate(sorted_refs, start=1):
    title, parsed_source = _parse_title_source(ref.title)
    source = ref.source.strip() or parsed_source or _source_from_url(ref.url)
    out.append(
      {
        "source": source,
        "title": title or ref.title,
        "urlNum": ref.ref_index if ref.ref_index > 0 else i,
        "webUrl": ref.url,
        "webUrlReachable": ref.url_reachable,
        "urlCheckStatus": ref.url_check_status or "",
        "urlHttpStatus": ref.url_http_status or None,
        "urlCheckNote": ref.url_check_note or "",
      }
    )
  return out


def citations_to_spot_check_json(refs: list[Citation | dict[str, Any]]) -> str:
  return json.dumps(citations_to_spot_check_list(refs), ensure_ascii=False)


def quality_grade_from_report(report: QaQualityReport) -> str:
  if report.ok and report.ref_count > 0 and report.url_count == report.ref_count:
    return "S"
  if report.ok and report.ref_count == 0:
    return "A"
  if report.ok and report.score >= 80:
    return "A"
  if report.score >= 50:
    return "B"
  return "F"


def build_detail_id_index(
  rows: list[SignedPromptRow],
  *,
  base: int = 900001,
) -> dict[str, int]:
  """为去重后的签单行生成稳定 detail_id（多机并发避免 state 竞争）。"""
  return {row.keyword_id: base + idx for idx, row in enumerate(rows)}


def qa_record_to_spot_check_row(
  signed: SignedPromptRow,
  *,
  answer_body: str,
  thinking: str,
  thinking_references: list[Citation | dict[str, Any]],
  quality_report: QaQualityReport,
  meta: SpotCheckBatchMeta,
  captured_at: datetime | None = None,
  detail_id: int | None = None,
) -> SpotCheckRow:
  """将签单行 + 采集结果映射为抽检明细行。"""
  ts = captured_at or datetime.now()
  ts_str = ts.strftime("%Y-%m-%d %H:%M:%S")
  thinking_body = extract_thinking_narrative(thinking)
  citations_json = citations_to_spot_check_json(thinking_references)

  detail_id_val = detail_id if detail_id is not None else meta.next_detail_id
  if detail_id is None:
    meta.next_detail_id += 1

  return SpotCheckRow(
    project_name=signed.project_name,
    check_date=meta.check_date or ts.strftime("%Y-%m-%d"),
    detail_id=detail_id_val,
    detail_code=make_detail_code(signed.keyword_id),
    task_code=meta.task_code,
    batch_code="",
    prompt=signed.prompt,
    channel_keyword=signed.prompt,
    keyword_id=signed.keyword_id,
    checked_at=ts_str,
    modified_at=ts_str,
    answer_chars=len(answer_body or ""),
    thinking_chars=len(thinking_body),
    citation_count=len(thinking_references or []),
    quality_grade=quality_grade_from_report(quality_report),
    intent_name=signed.intent_name,
    intent_id=signed.intent_id,
    keyword_pack_id=signed.keyword_pack_id,
    category=signed.category,
    cooperation=signed.cooperation,
    brand_id=signed.brand_id,
    brand_name=signed.brand_name,
    answer_body=answer_body or "",
    thinking_body=thinking_body,
    citations_json=citations_json,
  )


def load_completed_keyword_ids(csv_path: str) -> set[str]:
  """从已有抽检 CSV 读取已完成的关键词编号。"""
  if not os.path.isfile(csv_path):
    return set()
  done: set[str] = set()
  with open(csv_path, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
      kid = (row.get("关键词编号") or "").strip()
      if kid:
        done.add(kid)
  return done


def count_unique_completed_keywords(csv_path: str) -> int:
  """抽检进度：按唯一关键词编号计数（不以 CSV 行数为准）。"""
  return len(load_completed_keyword_ids(csv_path))


def spot_check_csv_stats(csv_path: str) -> tuple[int, int]:
  """返回 (唯一已完成 keyword 数, CSV 数据行数)。"""
  if not os.path.isfile(csv_path):
    return 0, 0
  kids: set[str] = set()
  rows = 0
  with open(csv_path, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
      rows += 1
      kid = (row.get("关键词编号") or "").strip()
      if kid:
        kids.add(kid)
  return len(kids), rows


def load_failure_counts(failures_path: str) -> dict[str, int]:
  """从 failures.jsonl 统计各 keyword_id 失败次数（用于认领时优先新任务）。"""
  if not failures_path or not os.path.isfile(failures_path):
    return {}
  counts: dict[str, int] = {}
  with open(failures_path, encoding="utf-8") as f:
    for line in f:
      line = line.strip()
      if not line:
        continue
      try:
        obj = json.loads(line)
      except json.JSONDecodeError:
        continue
      kid = str(obj.get("keyword_id") or "").strip()
      if kid:
        counts[kid] = counts.get(kid, 0) + 1
  return counts


def ensure_csv_header(csv_path: str) -> None:
  """若文件不存在则写入表头。"""
  if os.path.isfile(csv_path):
    return
  os.makedirs(os.path.dirname(os.path.abspath(csv_path)) or ".", exist_ok=True)
  with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(SPOT_CHECK_COLUMNS))
    writer.writeheader()


def append_csv_row(csv_path: str, row: SpotCheckRow) -> None:
  """追加一行抽检明细（立即落盘）。"""
  ensure_csv_header(csv_path)
  with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(SPOT_CHECK_COLUMNS))
    writer.writerow(row.to_csv_dict())
    f.flush()
    os.fsync(f.fileno())


def load_batch_meta(state_path: str, check_date: str) -> SpotCheckBatchMeta:
  if os.path.isfile(state_path):
    with open(state_path, encoding="utf-8") as f:
      data = json.load(f)
    meta = SpotCheckBatchMeta.from_dict(data.get("batch") or {})
    if meta.task_code:
      return meta
  return SpotCheckBatchMeta(
    task_code=make_task_code(check_date),
    next_detail_id=900001,
    check_date=check_date,
  )


def save_batch_meta(state_path: str, meta: SpotCheckBatchMeta, completed: dict[str, str]) -> None:
  os.makedirs(os.path.dirname(os.path.abspath(state_path)) or ".", exist_ok=True)
  payload = {
    "batch": meta.to_dict(),
    "completed": completed,
  }
  with open(state_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)


def load_completed_sessions(state_path: str) -> dict[str, str]:
  if not os.path.isfile(state_path):
    return {}
  with open(state_path, encoding="utf-8") as f:
    data = json.load(f)
  completed = data.get("completed") or {}
  return {str(k): str(v) for k, v in completed.items()}


@dataclass
class SpotCheckPurgeResult:
  """清理抽检 CSV 中引用/URL 不达标的行。"""

  kept_keyword_ids: list[str] = field(default_factory=list)
  removed_keyword_ids: list[str] = field(default_factory=list)
  removed_reasons: dict[str, str] = field(default_factory=dict)


def _csv_row_url_stats(row: dict[str, str]) -> tuple[int, int]:
  """返回 (引用条数, 有 webUrl 的条数)。"""
  try:
    ref_count = int(row.get("引用条数") or 0)
  except ValueError:
    ref_count = 0
  cites_raw = (row.get("引用列表") or "").strip()
  if not cites_raw:
    return ref_count, 0
  try:
    cites = json.loads(cites_raw)
  except json.JSONDecodeError:
    return ref_count, 0
  if not isinstance(cites, list):
    return ref_count, 0
  if ref_count <= 0:
    ref_count = len(cites)
  url_count = sum(
    1 for c in cites
    if isinstance(c, dict) and str(c.get("webUrl") or "").startswith("http")
  )
  return ref_count, url_count


def _row_should_purge(
  row: dict[str, str],
  *,
  min_url_resolve_ratio: float,
  require_refs: bool,
  allow_no_references: bool = True,
) -> str | None:
  ref_count, url_count = _csv_row_url_stats(row)
  if require_refs and ref_count <= 0:
    if allow_no_references:
      try:
        answer_chars = int(row.get("回答字数") or 0)
      except ValueError:
        answer_chars = 0
      if answer_chars >= 80:
        return None
    return "引用条数为 0"
  if url_count <= 0:
    return None
  if ref_count > 0 and url_count < ref_count * min_url_resolve_ratio:
    return f"URL 不足 {url_count}/{ref_count}（低于 {min_url_resolve_ratio:.0%}）"
  return None


def purge_incomplete_spot_check_rows(
  csv_path: str,
  *,
  state_path: str = "",
  failures_path: str = "",
  min_url_resolve_ratio: float = 0.5,
  require_refs: bool = True,
  allow_no_references: bool = True,
) -> SpotCheckPurgeResult:
  """
  删除抽检 CSV 中引用或 URL 不达标的行，并同步 state/failures。

  仅改 CSV 与断点状态；logs/qa_capture 会话目录保留不动。
  """
  result = SpotCheckPurgeResult()
  if not os.path.isfile(csv_path):
    return result

  with open(csv_path, encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)
    fieldnames = list(reader.fieldnames or SPOT_CHECK_COLUMNS)
    all_rows = list(reader)

  kept_rows: list[dict[str, str]] = []
  for row in all_rows:
    kid = (row.get("关键词编号") or "").strip()
    reason = _row_should_purge(
      row,
      min_url_resolve_ratio=min_url_resolve_ratio,
      require_refs=require_refs,
      allow_no_references=allow_no_references,
    )
    if reason:
      if kid:
        result.removed_keyword_ids.append(kid)
        result.removed_reasons[kid] = reason
      continue
    if kid:
      result.kept_keyword_ids.append(kid)
    kept_rows.append(row)

  if not result.removed_keyword_ids:
    return result

  with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(kept_rows)

  if state_path and os.path.isfile(state_path):
    with open(state_path, encoding="utf-8") as f:
      data = json.load(f)
    completed = {
      str(k): str(v)
      for k, v in (data.get("completed") or {}).items()
      if str(k) not in result.removed_keyword_ids
    }
    meta = SpotCheckBatchMeta.from_dict(data.get("batch") or {})
    if not kept_rows:
      meta.next_detail_id = 900001
    save_batch_meta(state_path, meta, completed)

  if failures_path and os.path.isfile(failures_path):
    removed = set(result.removed_keyword_ids)
    lines_out: list[str] = []
    with open(failures_path, encoding="utf-8") as f:
      for line in f:
        line = line.strip()
        if not line:
          continue
        try:
          obj = json.loads(line)
        except json.JSONDecodeError:
          lines_out.append(line)
          continue
        if str(obj.get("keyword_id") or "") not in removed:
          lines_out.append(line)
    with open(failures_path, "w", encoding="utf-8") as f:
      for line in lines_out:
        f.write(line + "\n")

  return result
