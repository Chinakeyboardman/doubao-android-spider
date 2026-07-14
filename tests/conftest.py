# -*- coding: utf-8 -*-
"""pytest 共用 fixture：设备与手势 profile（与 run_flow_crawl 一致）。"""

from __future__ import annotations

import os

import pytest
import uiautomator2 as u2

from app.config.profile_loader import load_profile


def _adb_serial() -> str | None:
    return os.environ.get("ADB_SERIAL") or os.environ.get("ANDROID_SERIAL") or None


@pytest.fixture(scope="module")
def u2_device():
    serial = _adb_serial()
    try:
        dev = u2.connect(serial) if serial else u2.connect()
    except (OSError, RuntimeError, ValueError) as exc:
        pytest.skip(f"无法连接设备（可设置 ADB_SERIAL / ANDROID_SERIAL）: {exc}")
    return dev


@pytest.fixture(scope="module")
def gesture_profile(u2_device):
    name = os.environ.get("DOUBAO_DEVICE_PROFILE")
    return load_profile(device_name=name, device=u2_device)
