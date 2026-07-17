# -*- coding: utf-8 -*-
"""
抖音 App 同号 SMS 登录（与豆包共用 SMS API / device_id 池）。

触发：批次 URL 解析前、Handoff 检测到登录墙。
成功判据：离开登录页，可进 feed 或深链可开详情。
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from app.modules.navigator import Navigator
from app.modules.sms_login import (
  SmsApiClient,
  SmsLoginError,
  enter_verification_code,
)

AWEME_PKG = "com.ss.android.ugc.aweme"
_LOGIN_OK_CACHE: dict[str, bool] = {}


def _on_douyin_login_page(device: Any) -> bool:
  try:
    cur = device.app_current() or {}
  except Exception:
    return False
  act = (cur.get("activity") or "").lower()
  if any(k in act for k in ("login", "register", "account", "verify")):
    return True
  for sel in (
    '//*[contains(@text,"手机号登录")]',
    '//*[contains(@text,"验证码登录")]',
    '//*[contains(@text,"登录")]',
  ):
    try:
      el = device.xpath(sel).get(timeout=0.4)
      if el:
        text = (el.info.get("text") or "").strip()
        if "登录" in text:
          return True
    except Exception:
      continue
  return False


def _is_douyin_logged_in(device: Any) -> bool:
  try:
    device.app_start(AWEME_PKG)
    time.sleep(2.5)
  except Exception:
    pass
  if _on_douyin_login_page(device):
    return False
  try:
    cur = device.app_current() or {}
  except Exception:
    return False
  pkg = cur.get("package", "")
  return "aweme" in pkg


def _open_douyin_phone_login(device: Any) -> bool:
  try:
    device.app_start(AWEME_PKG)
    time.sleep(2.0)
  except Exception:
    pass
  for sel in (
    '//*[@text="手机号登录"]',
    '//*[contains(@text,"手机号登录")]',
    '//*[@text="登录"]',
    '//*[contains(@text,"登录")]',
  ):
    try:
      el = device.xpath(sel).get(timeout=1.0)
      if el:
        text = (el.info.get("text") or "").strip()
        if sel.endswith('登录"]') and "手机号" not in text and "验证码" not in text:
          continue
        el.click()
        print("[抖音登录] 已点击登录入口")
        time.sleep(1.5)
        return True
    except Exception:
      continue
  return _on_douyin_login_page(device)


def _input_douyin_phone(device: Any, phone: str) -> bool:
  for sel in (
    '//*[@resource-id="com.ss.android.ugc.aweme:id/et"]',
    '//*[contains(@resource-id,"phone")]',
    '//*[contains(@resource-id,"mobile")]',
    '//*[@class="android.widget.EditText"]',
  ):
    try:
      el = device.xpath(sel).get(timeout=1.5)
      if el:
        el.click()
        time.sleep(0.3)
        try:
          device.clear_text()
        except Exception:
          pass
        device.send_keys(phone)
        time.sleep(0.5)
        print("[抖音登录] 已输入手机号")
        for btn in (
          '//*[@text="获取验证码"]',
          '//*[@text="下一步"]',
          '//*[contains(@text,"验证")]',
        ):
          try:
            b = device.xpath(btn).get(timeout=0.8)
            if b:
              b.click()
              time.sleep(1.5)
              return True
          except Exception:
            continue
        return True
    except Exception:
      continue
  return False


def auto_login_douyin(
  device: Any,
  nav: Navigator,
  *,
  token: str = "",
  device_id: str = "default_device",
  sms_wait_seconds: int = 55,
) -> bool:
  """单次抖音 SMS 登录。"""
  if not _open_douyin_phone_login(device):
    print("[抖音登录] 未进入登录页")
    return False

  api = SmsApiClient(token=token, device_id=device_id)
  try:
    phone = api.get_phone()
  except (SmsLoginError, requests.RequestException) as exc:
    print(f"[抖音登录] 获取手机号失败: {exc}")
    return False
  print(f"[抖音登录] 获取到手机号: {phone[:3]}****{phone[-4:]}")

  if not _input_douyin_phone(device, phone):
    api.report_phone_occupied(phone)
    print("[抖音登录] 输入手机号失败")
    return False

  print(f"[抖音登录] 等待验证码（{sms_wait_seconds}s）...")
  time.sleep(sms_wait_seconds)

  if not _on_douyin_login_page(device):
    print("[抖音登录] 已离开登录页（可能自动登录）")
    return True

  try:
    code = api.get_sms_code(phone)
  except SmsLoginError as exc:
    print(f"[抖音登录] {exc}")
    api.report_phone_occupied(phone)
    return False

  if not enter_verification_code(device, code, nav=nav):
    api.report_phone_occupied(phone)
    print("[抖音登录] 验证码输入失败")
    return False

  time.sleep(2.0)
  if not _on_douyin_login_page(device):
    print("[抖音登录] 登录成功")
    return True

  api.report_phone_occupied(phone)
  print("[抖音登录] 登录后仍在登录页")
  return False


def ensure_douyin_logged_in(
  device: Any,
  nav: Navigator,
  *,
  token: str = "",
  device_id: str = "",
  serial: str | None = None,
) -> bool:
  """批次/Handoff 前确保抖音已登录（同号 SMS）；按 serial 缓存。"""
  from app.modules.qa_reference_urls import _device_serial

  serial = serial or _device_serial(device) or "default"
  if _LOGIN_OK_CACHE.get(serial):
    return True

  token = token or os.environ.get("SMS_API_TOKEN", "")
  device_id = device_id or os.environ.get("SMS_DEVICE_ID", "doubao-crawler-vivo-v2301")

  if _is_douyin_logged_in(device):
    print("[抖音登录] 抖音已登录，跳过")
    _LOGIN_OK_CACHE[serial] = True
    return True

  print("[抖音登录] 抖音未登录，开始同号 SMS 登录...")
  for attempt in range(3):
    if auto_login_douyin(
      device, nav, token=token, device_id=device_id,
    ):
      _LOGIN_OK_CACHE[serial] = True
      try:
        device.press("home")
        time.sleep(0.5)
        nav.d.app_start("com.larus.nova")
        time.sleep(1.5)
      except Exception:
        pass
      return True
    if attempt < 2:
      print(f"[抖音登录] 重试 {attempt + 2}/3...")
      time.sleep(1.0)

  print("[抖音登录] 失败：请手动用豆包同号登录抖音后重试（S05a diagnose）")
  return False
