# -*- coding: utf-8 -*-
"""
从 JSON 文件加载设备 profile 并合并到 GestureProfile dataclass。

加载顺序（后覆盖前）：
  1. GestureProfile() 默认值
  2. profiles/default.json（若存在）
  3. 自动识别设备 → profiles/{auto_key}.json（若存在）
  4. 显式指定 device_name → profiles/{device_name}.json（若存在，最高优先级）

自动识别逻辑：连接 u2 设备后读取 ro.product.brand / ro.product.model，
拼成 `brand_model`（小写、空格转下划线）作为 profile 文件名去匹配。
"""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any, get_args, get_origin

from app.config.gesture_profile import GestureProfile


def _coerce_field_value(ft: Any, v: Any) -> Any:
    """将 JSON 值转为 dataclass 字段类型。"""
    origin = get_origin(ft)
    if origin is tuple:
      if isinstance(v, list):
        return tuple(str(x) for x in v)
      if isinstance(v, tuple):
        return v
      return v
    if origin is list:
      if isinstance(v, list):
        return v
      return v
    try:
      if ft in ("int", int):
        return int(v)
      if ft in ("float", float):
        return float(v)
      if ft in ("bool", bool):
        return bool(v)
    except (TypeError, ValueError):
      pass
    return v


def _profiles_dir() -> Path:
    return Path(__file__).resolve().parent / "profiles"


def _overlay(profile: GestureProfile, data: dict) -> GestureProfile:
    """将 dict 中存在的 key 覆盖到 profile 对应字段（类型自动转换）。"""
    field_map = {f.name: f for f in fields(profile)}
    for k, v in data.items():
        if k not in field_map:
            continue
        ft = field_map[k].type
        v = _coerce_field_value(ft, v)
        setattr(profile, k, v)
    return profile


def _load_json_overlay(profile: GestureProfile, path: Path) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _overlay(profile, data)
    except Exception:
        pass


def _sanitize_key(s: str) -> str:
    """品牌/型号 → 合法文件名片段（小写、空格与特殊字符转下划线）。"""
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def detect_device_profile_key(device: Any) -> str | None:
    """
    从已连接的 u2 device 读取品牌+型号，拼出 profile key。
    返回如 "huawei_mate_60_pro"、"xiaomi_14" 等；读取失败返回 None。
    """
    try:
        brand = (device.shell("getprop ro.product.brand").output or "").strip()
        model = (device.shell("getprop ro.product.model").output or "").strip()
        if not brand and not model:
            return None
        key = _sanitize_key(f"{brand}_{model}" if brand and model else (brand or model))
        return key or None
    except Exception:
        return None


def detect_device_info(device: Any) -> dict[str, str]:
    """读取设备基本信息（品牌、型号、Android 版本、分辨率），用于日志输出。"""
    info: dict[str, str] = {}
    props = {
        "brand": "ro.product.brand",
        "model": "ro.product.model",
        "android": "ro.build.version.release",
        "sdk": "ro.build.version.sdk",
    }
    for k, prop in props.items():
        try:
            info[k] = (device.shell(f"getprop {prop}").output or "").strip()
        except Exception:
            info[k] = ""
    try:
        di = device.info or {}
        info["width"] = str(di.get("displayWidth", ""))
        info["height"] = str(di.get("displayHeight", ""))
    except Exception:
        pass
    return info


def load_profile(device_name: str | None = None, device: Any = None) -> GestureProfile:
    """
    构建 GestureProfile。
    - 总是先叠加 default.json。
    - 若传入 device（u2 实例）且未指定 device_name，自动识别品牌型号匹配 profile。
    - 若指定 device_name，作为最高优先级叠加。
    """
    profile = GestureProfile()
    pdir = _profiles_dir()

    _load_json_overlay(profile, pdir / "default.json")

    auto_key: str | None = None
    if device is not None and not device_name:
        auto_key = detect_device_profile_key(device)
        if auto_key:
            matched = pdir / f"{auto_key}.json"
            if matched.is_file():
                print(f"📱 自动匹配设备 profile: {auto_key}")
                _load_json_overlay(profile, matched)
            else:
                print(f"📱 设备识别为 {auto_key}，无对应 profile，使用默认值")

    if device_name:
        _load_json_overlay(profile, pdir / f"{device_name}.json")

    return profile


def available_profiles() -> list[str]:
    """列出 profiles/ 下所有可用 profile 名（不含 .json 后缀）。"""
    pdir = _profiles_dir()
    if not pdir.is_dir():
        return []
    return sorted(p.stem for p in pdir.glob("*.json"))
