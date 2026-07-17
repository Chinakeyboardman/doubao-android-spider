# -*- coding: utf-8 -*-
"""douyin_handoff 单元测试：深链拼装、aweme id 解析、Handoff 状态检测。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.douyin_handoff import (
  HandoffState,
  build_aweme_deeplink,
  detect_handoff_state,
  extract_aweme_ids_ordered,
  extract_device_id_from_text,
  open_aweme_via_deeplink,
)
from app.modules.navigator import PACKAGE


def test_build_aweme_deeplink_with_device_id():
  url = build_aweme_deeplink("7650085520299273595", "abc123device", "snssdk1128")
  assert url == "snssdk1128://aweme/detail/7650085520299273595?device_id=abc123device"


def test_build_aweme_deeplink_without_device_id():
  url = build_aweme_deeplink("7650085520299273595", "", "snssdk1180")
  assert url == "snssdk1180://aweme/detail/7650085520299273595"


def test_extract_aweme_ids_1128_and_1180():
  text = """
  snssdk1128://aweme/detail/1111111111111111111
  snssdk1180://aweme/detail/2222222222222222222
  snssdk1128://aweme/detail/1111111111111111111
  """
  ids = extract_aweme_ids_ordered(text)
  assert ids == ["1111111111111111111", "2222222222222222222"]


def test_extract_device_id_from_logcat_intent():
  text = 'Intent { dat=snssdk1128://aweme/detail/1?device_id=deadbeef&refer=web }'
  assert extract_device_id_from_text(text) == "deadbeef"


def test_detect_handoff_app_jump():
  nav = MagicMock()
  nav.is_app_jump_prompt.return_value = True
  state = detect_handoff_state(MagicMock(), nav)
  assert state == HandoffState.APP_JUMP


def test_detect_handoff_web_in_doubao():
  nav = MagicMock()
  nav.is_app_jump_prompt.return_value = False
  device = MagicMock()
  device.app_current.return_value = {
    "package": PACKAGE,
    "activity": "com.larus.nova.main.MainActivity$WebActivity",
  }
  state = detect_handoff_state(device, nav)
  assert state == HandoffState.WEB_IN_DOUBAO


def test_detect_handoff_runtime_permission():
  nav = MagicMock()
  nav.is_app_jump_prompt.return_value = False
  device = MagicMock()
  device.app_current.return_value = {
    "package": "com.ss.android.ugc.aweme",
    "activity": "com.ss.android.ugc.aweme.splash.PermissionActivity",
  }
  device.xpath.return_value.get.return_value = None
  state = detect_handoff_state(device, nav)
  assert state == HandoffState.RUNTIME_PERMISSION


@patch("app.modules.douyin_handoff._adb_shell")
def test_open_aweme_via_deeplink_tries_schemes(mock_shell):
  mock_shell.side_effect = ["Starting: Intent", "Error: Activity not found"]
  hit = open_aweme_via_deeplink(
    "serial1",
    "7650085520299273595",
    "dev123",
    ("snssdk1128", "snssdk1180"),
  )
  assert hit == "snssdk1128"
  assert mock_shell.call_count == 1


@patch("app.modules.douyin_handoff.time.sleep")
@patch("app.modules.douyin_handoff._read_url_from_logcat_dumpsys")
@patch("app.modules.douyin_handoff.poll_aweme_ids_from_stream")
@patch("app.modules.douyin_handoff.detect_handoff_state")
def test_try_resolve_for_batch_skips_pc_web_early_return(
  mock_detect,
  mock_poll_ids,
  mock_read_url,
  _mock_sleep,
):
  from app.config.gesture_profile import GestureProfile
  from app.modules.douyin_handoff import HandoffState, try_resolve_douyin_after_click

  mock_poll_ids.return_value = ["1111111111111111111"]
  mock_detect.return_value = HandoffState.UNKNOWN
  mock_read_url.return_value = ("", ["1111111111111111111"])
  profile = GestureProfile(
    qa_douyin_web_validate=True,
    qa_douyin_deeplink_first=False,
    qa_resolve_accept_app_jump=False,
  )
  with patch(
    "app.modules.qa_reference_urls._douyin_url_from_id",
    return_value="https://www.douyin.com/video/1111111111111111111",
  ) as mock_web:
    url, ids = try_resolve_douyin_after_click(
      MagicMock(),
      MagicMock(),
      profile,
      serial="s",
      stream=MagicMock(),
      for_batch=True,
    )
  mock_web.assert_not_called()
  assert url == ""
  assert ids == ["1111111111111111111"]
