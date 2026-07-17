# -*- coding: utf-8 -*-
"""
豆包 → 抖音跳转与取链：深链优先、Handoff 状态机、登录墙检测。

P0: snssdk1128/1180 + device_id 深链打开详情
P1: AppJump / Permission / Feed 状态推进
P2: 登录墙 → douyin_sms_login.ensure_douyin_logged_in
"""

from __future__ import annotations

import re
import subprocess
import time
from enum import Enum
from typing import Any

from app.config.gesture_profile import GestureProfile
from app.modules.navigator import Navigator, PACKAGE

AWEME_PKG = "com.ss.android.ugc.aweme"
_SNSSDK_AWEME_RE = re.compile(
  r"snssdk(?:1128|1180)://aweme/detail/(\d+)",
  re.I,
)
_DEVICE_ID_IN_TEXT_RE = re.compile(r"device_id=([^&\s\"']+)", re.I)
_LINK_URL_RE = re.compile(r"link_url=(https?://[^\s,}\]]+)")


class HandoffState(str, Enum):
  UNKNOWN = "unknown"
  APP_JUMP = "app_jump"
  RUNTIME_PERMISSION = "runtime_permission"
  LOGIN_WALL = "login_wall"
  FEED_READY = "feed_ready"
  WEB_IN_DOUBAO = "web_in_doubao"
  IN_AWEME = "in_aweme"


def _adb_shell(serial: str | None, cmd: str) -> str:
  full = ["adb"]
  if serial:
    full.extend(["-s", serial])
  full.extend(["shell", cmd])
  try:
    return subprocess.check_output(full, text=True, errors="ignore").strip()
  except (subprocess.CalledProcessError, FileNotFoundError, OSError):
    return ""


def get_android_device_id(serial: str | None = None) -> str:
  """Android secure android_id，用于 snssdk 深链 query。"""
  did = _adb_shell(serial, "settings get secure android_id")
  if did and did.lower() != "null":
    return did
  return ""


def extract_aweme_ids_ordered(text: str) -> list[str]:
  if not text:
    return []
  seen: set[str] = set()
  out: list[str] = []
  for m in _SNSSDK_AWEME_RE.finditer(text):
    vid = m.group(1)
    if vid not in seen:
      seen.add(vid)
      out.append(vid)
  return out


def extract_device_id_from_text(text: str) -> str:
  m = _DEVICE_ID_IN_TEXT_RE.search(text or "")
  return m.group(1) if m else ""


def build_aweme_deeplink(aweme_id: str, device_id: str, scheme: str) -> str:
  base = f"{scheme}://aweme/detail/{aweme_id}"
  if device_id:
    return f"{base}?device_id={device_id}"
  return base


def open_aweme_via_deeplink(
  serial: str | None,
  aweme_id: str,
  device_id: str,
  schemes: tuple[str, ...],
) -> str:
  """am start 打开抖音详情，返回命中的 scheme 或空。"""
  for scheme in schemes:
    uri = build_aweme_deeplink(aweme_id, device_id, scheme)
    cmd = (
      f'am start -a android.intent.action.VIEW -d "{uri}" {AWEME_PKG}'
    )
    out = _adb_shell(serial, cmd)
    if "Error" not in (out or "") and "Exception" not in (out or ""):
      print(f"  [Handoff] 深链打开 {scheme} id={aweme_id}")
      return scheme
  return ""


def detect_handoff_state(device: Any, nav: Navigator) -> HandoffState:
  if nav.is_app_jump_prompt():
    return HandoffState.APP_JUMP
  try:
    cur = device.app_current() or {}
  except Exception:
    return HandoffState.UNKNOWN
  pkg = cur.get("package", "") or ""
  act = cur.get("activity", "") or ""
  act_l = act.lower()
  if PACKAGE in pkg and "WebActivity" in act:
    return HandoffState.WEB_IN_DOUBAO
  if "aweme" in pkg or "ugc.aweme" in act_l:
    if _is_login_wall(device, act):
      return HandoffState.LOGIN_WALL
    if "permission" in act_l or "grantpermissions" in act_l:
      return HandoffState.RUNTIME_PERMISSION
    if "splash" in act_l:
      return HandoffState.RUNTIME_PERMISSION
    return HandoffState.IN_AWEME
  if "permissioncontroller" in pkg:
    return HandoffState.RUNTIME_PERMISSION
  return HandoffState.UNKNOWN


def _is_login_wall(device: Any, activity: str) -> bool:
  act_l = (activity or "").lower()
  if any(k in act_l for k in ("login", "register", "account", "verify")):
    return True
  for sel in (
    '//*[contains(@text,"登录")]',
    '//*[contains(@text,"手机号登录")]',
    '//*[contains(@text,"验证码登录")]',
  ):
    try:
      if device.xpath(sel).get(timeout=0.3):
        return True
    except Exception:
      continue
  return False


def advance_handoff(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  sms_token: str = "",
  sms_device_id: str = "",
  for_feed: bool = False,
) -> HandoffState:
  """推进 Handoff 直至 Feed 就绪或超时。"""
  deadline = time.time() + profile.qa_douyin_handoff_timeout
  last = HandoffState.UNKNOWN
  while time.time() < deadline:
    state = detect_handoff_state(device, nav)
    last = state
    if state == HandoffState.APP_JUMP:
      nav.wait_and_accept_app_jump(timeout=2.0)
    elif state == HandoffState.RUNTIME_PERMISSION:
      nav._grant_douyin_runtime_permissions()
      for _ in range(3):
        nav._grant_douyin_runtime_permissions()
        time.sleep(0.3)
    elif state == HandoffState.LOGIN_WALL:
      if profile.qa_douyin_web_validate:
        print("  [Handoff] 登录墙 + PC Web 模式，中止手机 Handoff")
        return HandoffState.LOGIN_WALL
      from app.modules.douyin_sms_login import ensure_douyin_logged_in

      ensure_douyin_logged_in(
        device,
        nav,
        token=sms_token,
        device_id=sms_device_id,
      )
    elif state in (HandoffState.IN_AWEME, HandoffState.FEED_READY):
      if nav.wait_for_aweme_foreground(timeout=2.0):
        return HandoffState.FEED_READY
    elif state == HandoffState.WEB_IN_DOUBAO:
      return HandoffState.WEB_IN_DOUBAO
    time.sleep(0.35)
  return last


def poll_aweme_ids_from_stream(
  stream: Any | None,
  serial: str | None,
  *,
  timeout_s: float = 2.5,
  poll_interval_s: float = 0.2,
) -> list[str]:
  from capture.utils.capture_logcat import dump_logcat_tail

  deadline = time.time() + timeout_s
  best: list[str] = []
  while time.time() < deadline:
    chunks: list[str] = []
    if stream is not None:
      chunks.append(stream.text_since_mark())
    chunks.append(dump_logcat_tail(serial=serial, count=120))
    merged = "\n".join(chunks)
    ids = extract_aweme_ids_ordered(merged)
    if len(ids) > len(best):
      best = ids
    if ids:
      return ids
    time.sleep(poll_interval_s)
  return best


def _read_url_from_logcat_dumpsys(
  serial: str | None,
  stream: Any | None,
  wait_s: float = 1.2,
) -> tuple[str, list[str]]:
  from app.modules.qa_reference_urls import (
    _adb_dumpsys,
    _iesdouyin_url,
    extract_urls_from_dumpsys_text,
    extract_urls_from_logcat_text,
    pick_best_url,
  )

  time.sleep(wait_s)
  chunks: list[str] = []
  if stream is not None:
    chunks.append(stream.text_since_mark())
  from capture.utils.capture_logcat import dump_logcat_tail

  chunks.append(dump_logcat_tail(serial=serial, count=200))
  chunks.append(_adb_dumpsys(serial, "activity", "top"))
  merged = "\n".join(chunks)
  ids = extract_aweme_ids_ordered(merged)
  urls = extract_urls_from_logcat_text(merged)
  http = extract_urls_from_dumpsys_text(merged)
  urls.extend(http)
  url = pick_best_url(urls, prefer_last=True)
  if not url and ids:
    url = build_url_from_aweme_id(ids[0])
  return url, ids


def resolve_via_aweme_deeplink(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  serial: str | None,
  stream: Any | None = None,
  aweme_id: str = "",
) -> str:
  """
  点击豆包引用后：抽 aweme_id → snssdk 深链 + device_id → 读 link/重建 iesdouyin。
  """
  from app.modules.douyin_web_resolve import build_url_from_aweme_id
  from app.modules.qa_reference_urls import _douyin_url_from_id

  serial = serial or ""
  if not aweme_id:
    ids = poll_aweme_ids_from_stream(stream, serial, timeout_s=2.5)
    if not ids:
      return ""
    aweme_id = ids[0]

  device_id = get_android_device_id(serial)
  if not device_id and stream is not None:
    device_id = extract_device_id_from_text(stream.text_since_mark())

  schemes = profile.qa_douyin_deeplink_schemes or ("snssdk1128", "snssdk1180")
  hit = open_aweme_via_deeplink(serial, aweme_id, device_id, schemes)
  if not hit:
    return _douyin_url_from_id(aweme_id, profile)

  advance_handoff(
    device,
    nav,
    profile,
    for_feed=False,
  )
  url, ids = _read_url_from_logcat_dumpsys(serial, stream, wait_s=1.0)
  nav.lite_back_to_chat()
  if url:
    print(f"  [Handoff] 深链命中 URL: {url[:80]}")
    return url
  if aweme_id:
    return _douyin_url_from_id(aweme_id, profile)
  if ids:
    return _douyin_url_from_id(ids[0], profile)
  return ""


def try_resolve_douyin_after_click(
  device: Any,
  nav: Navigator,
  profile: GestureProfile,
  *,
  serial: str | None,
  stream: Any | None = None,
  sms_token: str = "",
  sms_device_id: str = "",
  batch_feed_swipes: int = 0,
  for_batch: bool = False,
) -> tuple[str, list[str]]:
  """
  豆包点击抖音引用后的统一入口：PC Web 验证 → 深链 → Handoff+feed → 返回 (url, aweme_ids)。

  for_batch=True 时跳过单条 PC Web 早退，继续收齐多条 aweme id 供批量回填。
  """
  url = ""
  ids = poll_aweme_ids_from_stream(stream, serial, timeout_s=2.0)
  state = detect_handoff_state(device, nav)
  if state == HandoffState.WEB_IN_DOUBAO:
    url, ids = _read_url_from_logcat_dumpsys(serial, stream, wait_s=0.8)
    if url and not for_batch:
      print(f"  [Handoff] 豆包 WebActivity 命中: {url[:80]}")
      return url, ids
  if ids and profile.qa_douyin_web_validate and not for_batch:
    from app.modules.qa_reference_urls import _douyin_url_from_id

    web_url = _douyin_url_from_id(ids[0], profile)
    if web_url:
      print(f"  [Handoff] PC Web 验证 id={ids[0]}，跳过手机开抖音")
      return web_url, ids

  if profile.qa_douyin_deeplink_first and not for_batch:
    url = resolve_via_aweme_deeplink(
      device, nav, profile, serial=serial, stream=stream,
    )
    if url:
      ids = poll_aweme_ids_from_stream(stream, serial, timeout_s=0.8)
      return url, ids

  if profile.qa_resolve_accept_app_jump:
    nav.wait_and_accept_app_jump(timeout=8.0)
    state = advance_handoff(
      device,
      nav,
      profile,
      sms_token=sms_token,
      sms_device_id=sms_device_id,
      for_feed=True,
    )
    if state == HandoffState.WEB_IN_DOUBAO:
      url, ids = _read_url_from_logcat_dumpsys(serial, stream, wait_s=0.8)
      if not for_batch:
        return url, ids
    if nav.wait_for_aweme_foreground(timeout=12.0) and batch_feed_swipes > 0:
      try:
        w, h = device.window_size()
        for _ in range(batch_feed_swipes):
          device.swipe(int(w * 0.5), int(h * 0.72), int(w * 0.5), int(h * 0.38), 0.35)
          time.sleep(1.0)
      except Exception:
        pass

  url, ids = _read_url_from_logcat_dumpsys(serial, stream, wait_s=1.5)
  if url and not for_batch:
    return url, ids
  ids = poll_aweme_ids_from_stream(stream, serial, timeout_s=2.0)
  if ids:
    if for_batch:
      return "", ids
    from app.modules.qa_reference_urls import _douyin_url_from_id

    return _douyin_url_from_id(ids[0], profile), ids
  return "", ids
