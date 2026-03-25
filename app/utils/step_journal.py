"""步骤执行记录：追加写入仓库 `doc/` 下同一 Markdown（capture 与爬虫共用）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config.config import PATH_CONFIG
from app.utils.utils import ensure_directory, log_error


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def doc_step_journal_path() -> Path:
    rel = PATH_CONFIG.get("step_journal", "doc/capture_step_journal.md")
    return (_repo_root() / Path(rel)).resolve()


def append_doc_step(step: str, status: str, detail: str = "") -> None:
    """向 `doc/` 下固定 md 追加一条步骤记录（失败时写日志，不抛出）。"""
    path = doc_step_journal_path()
    try:
        ensure_directory(str(path.parent))
        if not path.is_file():
            path.write_text(
                "# 步骤执行记录\n\n"
                "本文件由 **`python run_capture.py`**（APK / capture）与 **`python run.py`** / "
                "**`DoubaoSpider`**（爬虫）在各自步骤执行时自动追加；可在文末人工补充说明。\n\n"
                "---\n",
                encoding="utf-8",
            )
        now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        block = f"\n## {now}\n\n"
        block += f"- **步骤**: {step}\n"
        block += f"- **结果**: {status}\n"
        if detail.strip():
            block += f"- **说明**: {detail.strip()}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
    except OSError as e:
        log_error(f"步骤日志写入失败 {path}: {e}")
