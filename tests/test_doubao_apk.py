"""doubao_apk 版本解析与安装判断单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.utils.doubao_apk import (
    ApkManifest,
    ApkVersion,
    compare_versions,
    decide_install,
    parse_apk_file,
    scan_store,
)


def test_compare_versions_by_code() -> None:
    a = ApkVersion(version_name="14.1.0", version_code=14010001)
    b = ApkVersion(version_name="12.5.0", version_code=12050000)
    assert compare_versions(a, b) == 1
    assert compare_versions(b, a) == -1
    assert compare_versions(a, a) == 0


def test_compare_versions_by_name_when_code_missing() -> None:
    a = ApkVersion(version_name="14.1.0")
    b = ApkVersion(version_name="12.5.0")
    assert compare_versions(a, b) == 1


def test_decide_install_skip_same_version() -> None:
    v = ApkVersion(version_name="14.1.0", version_code=14010001)
    d = decide_install(v, v)
    assert d.action == "skip"


def test_decide_install_upgrade() -> None:
    target = ApkVersion(version_name="14.1.0", version_code=14010001)
    installed = ApkVersion(version_name="12.5.0", version_code=12050000)
    d = decide_install(target, installed)
    assert d.action == "install"


def test_decide_install_warn_downgrade() -> None:
    target = ApkVersion(version_name="12.5.0", version_code=12050000)
    installed = ApkVersion(version_name="14.1.0", version_code=14010001)
    d = decide_install(target, installed)
    assert d.action == "warn_newer_device"


def test_manifest_legacy_single_entry() -> None:
    data = {
        "package": "com.larus.nova",
        "version_name": "14.1.0",
        "version_code": 14010001,
        "file": "com.larus.nova_14.1.0.apk",
    }
    m = ApkManifest.from_dict(data)
    assert len(m.versions) == 1
    assert m.versions[0].version_name == "14.1.0"
    assert m.default_version == "14.1.0"


def test_parse_apk_from_filename(tmp_path: Path) -> None:
    apk = tmp_path / "com.larus.nova_9.8.7_9980700.apk"
    apk.write_bytes(b"PK\x03\x04" + b"\x00" * 32)
    info = parse_apk_file(apk)
    assert info.version_name == "9.8.7"
    assert info.version_code == 9980700


def test_scan_store_finds_apks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "var" / "apk" / "com.larus.nova"
    store.mkdir(parents=True)
    apk = store / "com.larus.nova_1.0.0.apk"
    apk.write_bytes(b"PK\x03\x04dummy")

    monkeypatch.setattr("app.utils.doubao_apk.repo_root", lambda: tmp_path)
    manifest = scan_store(tmp_path)
    assert len(manifest.versions) == 1
    assert manifest.versions[0].version_name == "1.0.0"

    saved = json.loads((store / "manifest.json").read_text(encoding="utf-8"))
    assert saved["default_version"] == "1.0.0"
    assert saved["versions"][0]["file"] == "com.larus.nova_1.0.0.apk"
