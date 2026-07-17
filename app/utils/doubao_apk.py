"""豆包 APK 本地仓库：版本解析、manifest 索引、安装前比对。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.config import DOUBAO_CONFIG

PACKAGE = DOUBAO_CONFIG["package_name"]
_APK_NAME_RE = re.compile(
    rf"^{re.escape(PACKAGE)}_(?P<name>[\d.]+?)(?:_(?P<code>\d+))?\.apk$",
    re.IGNORECASE,
)
_AAPT_BADGING_RE = re.compile(
    r"package: name='([^']*)' versionCode='(\d+)' versionName='([^']*)'"
)
_DUMPSYS_VERSION_RE = re.compile(
    r"versionName=([^\s]+).*?versionCode=(\d+)",
    re.DOTALL,
)
_LARGE_APK_BYTES = 150 * 1024 * 1024


@dataclass(frozen=True)
class ApkVersion:
    """单个 APK 版本信息。"""

    version_name: str
    version_code: int | None = None
    file: str = ""
    size_bytes: int = 0
    sha256: str = ""
    source: str = ""
    downloaded_at: str = ""
    note: str = ""

    def sort_key(self) -> tuple[int, str]:
        code = self.version_code if self.version_code is not None else -1
        return (code, self.version_name)

    def display(self) -> str:
        code = self.version_code if self.version_code is not None else "?"
        return f"{self.version_name} (code={code})"


@dataclass
class ApkManifest:
    """var/apk/<package>/manifest.json 结构。"""

    package: str = PACKAGE
    default_version: str = ""
    versions: list[ApkVersion] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "default_version": self.default_version,
            "versions": [asdict(v) for v in self.versions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApkManifest:
        versions: list[ApkVersion] = []
        for item in data.get("versions") or []:
            if not isinstance(item, dict):
                continue
            versions.append(
                ApkVersion(
                    version_name=str(item.get("version_name") or ""),
                    version_code=_coerce_int(item.get("version_code")),
                    file=str(item.get("file") or ""),
                    size_bytes=int(item.get("size_bytes") or 0),
                    sha256=str(item.get("sha256") or ""),
                    source=str(item.get("source") or ""),
                    downloaded_at=str(item.get("downloaded_at") or ""),
                    note=str(item.get("note") or ""),
                )
            )
        # 兼容旧版单条 manifest（扁平字段）
        if not versions and data.get("version_name"):
            versions.append(
                ApkVersion(
                    version_name=str(data.get("version_name") or ""),
                    version_code=_coerce_int(data.get("version_code")),
                    file=str(data.get("file") or ""),
                    size_bytes=int(data.get("size_bytes") or 0),
                    sha256=str(data.get("sha256") or ""),
                    source=str(data.get("source") or ""),
                    downloaded_at=str(data.get("downloaded_at") or ""),
                    note=str(data.get("note") or ""),
                )
            )
        default = str(data.get("default_version") or "")
        if not default and versions:
            default = pick_latest(versions).version_name
        return cls(package=str(data.get("package") or PACKAGE), default_version=default, versions=versions)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def apk_store_dir(root: Path | None = None) -> Path:
    base = root or repo_root()
    return base / "var" / "apk" / PACKAGE


def manifest_path(root: Path | None = None) -> Path:
    return apk_store_dir(root) / "manifest.json"


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_aapt() -> str | None:
    found = shutil.which("aapt")
    if found:
        return found
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    candidates: list[Path] = []
    if sdk:
        candidates.extend(Path(sdk).glob("build-tools/*/aapt"))
    candidates.extend(Path.home().glob("Library/Android/sdk/build-tools/*/aapt"))
    for path in sorted(candidates, reverse=True):
        if path.is_file():
            return str(path)
    return None


def _parse_apk_with_aapt(apk_path: Path) -> ApkVersion | None:
    aapt = _find_aapt()
    if not aapt:
        return None
    proc = subprocess.run(
        [aapt, "dump", "badging", str(apk_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        return None
    m = _AAPT_BADGING_RE.search(proc.stdout or "")
    if not m:
        return None
    pkg, code, name = m.group(1), int(m.group(2)), m.group(3)
    if pkg != PACKAGE:
        return None
    return ApkVersion(version_name=name, version_code=code)


def _parse_apk_with_pyaxmlparser(apk_path: Path) -> ApkVersion | None:
    try:
        from pyaxmlparser import APK  # type: ignore[import-untyped]
    except ImportError:
        return None
    try:
        apk = APK(str(apk_path))
    except Exception:
        return None
    if apk.package != PACKAGE:
        return None
    code = _coerce_int(apk.version_code)
    name = str(apk.version_name or "")
    if not name:
        return None
    return ApkVersion(version_name=name, version_code=code)


def _parse_apk_from_filename(apk_path: Path) -> ApkVersion | None:
    m = _APK_NAME_RE.match(apk_path.name)
    if not m:
        return None
    return ApkVersion(
        version_name=m.group("name"),
        version_code=_coerce_int(m.group("code")),
    )


def parse_apk_file(apk_path: Path) -> ApkVersion:
    """从 APK 文件解析版本；依次尝试 aapt、pyaxmlparser、文件名。"""
    apk_path = apk_path.resolve()
    if not apk_path.is_file():
        raise FileNotFoundError(f"APK 不存在: {apk_path}")

    for parser in (_parse_apk_with_aapt, _parse_apk_with_pyaxmlparser, _parse_apk_from_filename):
        info = parser(apk_path)
        if info and info.version_name:
            rel = _relative_apk_path(apk_path)
            return ApkVersion(
                version_name=info.version_name,
                version_code=info.version_code,
                file=rel,
                size_bytes=apk_path.stat().st_size,
                sha256=_sha256_file(apk_path),
            )
    raise ValueError(f"无法解析 APK 版本: {apk_path.name}（请命名为 {PACKAGE}_<version>.apk）")


def _relative_apk_path(apk_path: Path, root: Path | None = None) -> str:
    store = apk_store_dir(root)
    try:
        return str(apk_path.resolve().relative_to(store.resolve()))
    except ValueError:
        return apk_path.name


def compare_versions(a: ApkVersion, b: ApkVersion) -> int:
    """比较版本：1 表示 a 更新，-1 表示 b 更新，0 表示相同。"""
    if a.version_code is not None and b.version_code is not None:
        if a.version_code > b.version_code:
            return 1
        if a.version_code < b.version_code:
            return -1
        return 0
    if a.version_name == b.version_name:
        return 0
    # 按点分数字段比较 versionName
    def parts(name: str) -> list[int]:
        out: list[int] = []
        for seg in name.split("."):
            try:
                out.append(int(seg))
            except ValueError:
                out.append(0)
        return out

    pa, pb = parts(a.version_name), parts(b.version_name)
    for i in range(max(len(pa), len(pb))):
        va = pa[i] if i < len(pa) else 0
        vb = pb[i] if i < len(pb) else 0
        if va > vb:
            return 1
        if va < vb:
            return -1
    return 0


def pick_latest(versions: list[ApkVersion]) -> ApkVersion:
    if not versions:
        raise ValueError("版本列表为空")
    return max(versions, key=lambda v: v.sort_key())


def load_manifest(root: Path | None = None) -> ApkManifest:
    path = manifest_path(root)
    if not path.is_file():
        return ApkManifest()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ApkManifest()
    return ApkManifest.from_dict(data)


def save_manifest(manifest: ApkManifest, root: Path | None = None) -> Path:
    store = apk_store_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    path = manifest_path(root)
    path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def scan_store(root: Path | None = None) -> ApkManifest:
    """扫描 var/apk/<package>/ 下所有 .apk，刷新 manifest.json。"""
    store = apk_store_dir(root)
    store.mkdir(parents=True, exist_ok=True)
    (store / "pulled").mkdir(parents=True, exist_ok=True)

    existing = {v.file: v for v in load_manifest(root).versions if v.file}
    found: dict[str, ApkVersion] = {}

    for apk_path in sorted(store.rglob("*.apk")):
        rel = _relative_apk_path(apk_path, root)
        try:
            info = parse_apk_file(apk_path)
        except (OSError, ValueError):
            continue
        old = existing.get(rel)
        found[rel] = ApkVersion(
            version_name=info.version_name,
            version_code=info.version_code or (old.version_code if old else None),
            file=rel,
            size_bytes=info.size_bytes,
            sha256=info.sha256,
            source=old.source if old else "",
            downloaded_at=old.downloaded_at if old else _now_iso(),
            note=old.note if old else "",
        )

    versions = sorted(found.values(), key=lambda v: v.sort_key(), reverse=True)
    manifest = load_manifest(root)
    manifest.versions = versions
    if not manifest.default_version and versions:
        manifest.default_version = versions[0].version_name
    elif manifest.default_version:
        names = {v.version_name for v in versions}
        if manifest.default_version not in names and versions:
            manifest.default_version = versions[0].version_name
    save_manifest(manifest, root)
    return manifest


def resolve_apk_path(version_name: str | None = None, root: Path | None = None) -> Path:
    manifest = load_manifest(root)
    if not manifest.versions:
        scan_store(root)
        manifest = load_manifest(root)
    if not manifest.versions:
        raise FileNotFoundError(f"未在 {apk_store_dir(root)} 找到任何 APK，请先放入 {PACKAGE}_<version>.apk")

    target_name = version_name or manifest.default_version
    chosen: ApkVersion | None = None
    if target_name:
        for v in manifest.versions:
            if v.version_name == target_name:
                chosen = v
                break
    if chosen is None:
        chosen = pick_latest(manifest.versions)

    apk = apk_store_dir(root) / chosen.file
    if not apk.is_file():
        raise FileNotFoundError(f"manifest 指向的 APK 不存在: {apk}")
    return apk


def _adb_prefix(serial: str | None) -> list[str]:
    cmd = ["adb"]
    if serial:
        cmd.extend(["-s", serial])
    return cmd


def _run_adb(args: list[str], *, serial: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _adb_prefix(serial) + args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def get_installed_version(*, serial: str | None = None) -> ApkVersion | None:
    """读取设备上已安装豆包的 versionName / versionCode。"""
    proc = _run_adb(["shell", "dumpsys", "package", PACKAGE], serial=serial, timeout=180)
    if proc.returncode != 0:
        return None
    text = proc.stdout or ""
    if f"Package [{PACKAGE}]" not in text and PACKAGE not in text:
        return None
    # dumpsys 可能有多处 versionName，取第一个主包块
    m = _DUMPSYS_VERSION_RE.search(text)
    if not m:
        return None
    return ApkVersion(version_name=m.group(1), version_code=int(m.group(2)))


def is_package_installed(*, serial: str | None = None) -> bool:
    proc = _run_adb(["shell", "pm", "list", "packages", PACKAGE], serial=serial, timeout=60)
    return proc.returncode == 0 and PACKAGE in (proc.stdout or "")


@dataclass(frozen=True)
class InstallDecision:
    """安装前判断结果。"""

    action: str  # skip | install | warn_newer_device
    reason: str
    target: ApkVersion | None = None
    installed: ApkVersion | None = None


def decide_install(
    target: ApkVersion,
    installed: ApkVersion | None,
    *,
    force: bool = False,
) -> InstallDecision:
    if installed is None:
        return InstallDecision("install", "设备未安装豆包", target=target, installed=None)
    cmp = compare_versions(target, installed)
    if cmp == 0:
        if force:
            return InstallDecision("install", "强制重装同版本", target=target, installed=installed)
        return InstallDecision("skip", "设备已是目标版本", target=target, installed=installed)
    if cmp < 0:
        return InstallDecision(
            "warn_newer_device",
            f"设备版本 {installed.display()} 高于目标 {target.display()}",
            target=target,
            installed=installed,
        )
    return InstallDecision(
        "install",
        f"设备 {installed.display()} → 目标 {target.display()}",
        target=target,
        installed=installed,
    )


def install_apk(
    apk_path: Path,
    *,
    serial: str | None = None,
    uninstall_first: bool = False,
) -> tuple[bool, str]:
    """安装 APK；大包走 push Download + pm install。"""
    apk_path = apk_path.resolve()
    if not apk_path.is_file():
        return False, f"APK 不存在: {apk_path}"

    if uninstall_first:
        _run_adb(["uninstall", PACKAGE], serial=serial, timeout=300)

    size = apk_path.stat().st_size
    if size >= _LARGE_APK_BYTES:
        ext_proc = _run_adb(["shell", "sh", "-c", 'echo -n "$EXTERNAL_STORAGE"'], serial=serial)
        ext = (ext_proc.stdout or "").strip() or "/storage/emulated/0"
        remote = f"{ext.rstrip('/')}/Download/{apk_path.name}"
        push = _run_adb(["push", str(apk_path), remote], serial=serial, timeout=3600)
        if push.returncode != 0:
            err = (push.stderr or push.stdout or "").strip()
            return False, f"adb push 失败: {err}"
        proc = _run_adb(["shell", "pm", "install", "-r", "-t", remote], serial=serial, timeout=600)
    else:
        proc = _run_adb(["install", "-r", str(apk_path)], serial=serial, timeout=600)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return False, f"安装失败: {err}"
    return True, "安装成功"


def pull_installed_apk(
    *,
    serial: str | None = None,
    root: Path | None = None,
) -> ApkVersion:
    """从设备拉取当前豆包 base.apk 到 pulled/ 并登记 manifest。"""
    installed = get_installed_version(serial=serial)
    if installed is None:
        raise RuntimeError("设备未安装豆包，无法 pull")

    proc = _run_adb(["shell", "pm", "path", PACKAGE], serial=serial, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or "pm path 失败")
    remote = ""
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            remote = line[len("package:") :].strip()
            break
    if not remote:
        raise RuntimeError("未解析到设备 APK 路径")

    store = apk_store_dir(root)
    pulled_dir = store / "pulled"
    pulled_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{installed.version_code}" if installed.version_code else ""
    filename = f"{PACKAGE}_{installed.version_name}{suffix}.apk"
    dest = pulled_dir / filename

    pull = _run_adb(["pull", remote, str(dest)], serial=serial, timeout=600)
    if pull.returncode != 0:
        err = (pull.stderr or pull.stdout or "").strip()
        raise RuntimeError(f"adb pull 失败: {err}")

    info = parse_apk_file(dest)
    info = ApkVersion(
        version_name=info.version_name,
        version_code=info.version_code or installed.version_code,
        file=_relative_apk_path(dest, root),
        size_bytes=info.size_bytes,
        sha256=info.sha256,
        source="adb pull",
        downloaded_at=_now_iso(),
        note="从设备拉取",
    )
    manifest = scan_store(root)
    replaced = False
    new_versions: list[ApkVersion] = []
    for v in manifest.versions:
        if v.file == info.file:
            new_versions.append(info)
            replaced = True
        else:
            new_versions.append(v)
    if not replaced:
        new_versions.append(info)
    manifest.versions = sorted(new_versions, key=lambda x: x.sort_key(), reverse=True)
    save_manifest(manifest, root)
    return info


def format_status_report(*, serial: str | None = None, root: Path | None = None) -> str:
    """生成设备与本地 APK 仓库的版本对照文本。"""
    lines: list[str] = []
    store = apk_store_dir(root)
    manifest = load_manifest(root)
    if manifest.versions:
        lines.append("本地 APK 仓库 (%s):" % store)
        for v in manifest.versions:
            mark = " *" if v.version_name == manifest.default_version else ""
            lines.append(f"  - {v.display()}  [{v.file}]{mark}")
    else:
        lines.append(f"本地 APK 仓库为空: {store}")
        lines.append(f"  请将 APK 命名为 {PACKAGE}_<version>.apk 放入该目录后执行 scan")

    if is_package_installed(serial=serial):
        installed = get_installed_version(serial=serial)
        if installed:
            lines.append(f"设备已安装: {installed.display()}")
            if manifest.default_version:
                try:
                    target = resolve_apk_path(manifest.default_version, root)
                    target_info = parse_apk_file(target)
                    decision = decide_install(target_info, installed)
                    lines.append(f"对照默认版本 {manifest.default_version}: {decision.reason} ({decision.action})")
                except (OSError, ValueError, FileNotFoundError):
                    pass
        else:
            lines.append("设备已安装豆包，但未能读取版本号")
    else:
        lines.append("设备未安装豆包")
    return "\n".join(lines)
