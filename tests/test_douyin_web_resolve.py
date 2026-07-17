# -*- coding: utf-8 -*-
"""douyin_web_resolve 单元测试（HTTP mock，无需外网）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.douyin_web_resolve import (
  build_canonical_url,
  build_share_url,
  build_url_candidates,
  build_url_from_aweme_id,
  expand_short_link,
  extract_aweme_id_from_any_url,
  is_douyin_video_url,
  normalize_aweme_id,
  resolve_verified_url,
  validate_aweme_multi_format,
  validate_aweme_via_web,
)

SAMPLE_ID = "7548775039182294330"
JINGXUAN_ID = "7428415093521648905"


def test_normalize_aweme_id_from_url():
  assert normalize_aweme_id(SAMPLE_ID) == SAMPLE_ID
  assert (
    normalize_aweme_id(
      f"https://www.iesdouyin.com/share/video/{SAMPLE_ID}/?app=aweme"
    )
    == SAMPLE_ID
  )
  assert (
    normalize_aweme_id(
      f"https://www.douyin.com/jingxuan?modal_id={JINGXUAN_ID}"
    )
    == JINGXUAN_ID
  )
  assert (
    normalize_aweme_id(f"https://www.douyin.com/video/{SAMPLE_ID}")
    == SAMPLE_ID
  )
  assert (
    normalize_aweme_id(
      f"https://www.douyin.com/foo?aweme_id={SAMPLE_ID}&x=1"
    )
    == SAMPLE_ID
  )


def test_build_share_and_canonical():
  assert build_share_url(SAMPLE_ID) == f"https://www.iesdouyin.com/share/video/{SAMPLE_ID}"
  assert build_canonical_url(SAMPLE_ID) == f"https://www.douyin.com/video/{SAMPLE_ID}"
  assert "did=abc" in build_share_url(SAMPLE_ID, device_id="abc")


def test_build_url_candidates_priority():
  cands = build_url_candidates(JINGXUAN_ID)
  assert cands[0][0] == "douyin_jingxuan_modal"
  assert f"modal_id={JINGXUAN_ID}" in cands[0][1]
  assert build_url_from_aweme_id(JINGXUAN_ID).startswith(
    "https://www.douyin.com/jingxuan?modal_id="
  )


def test_is_douyin_video_url():
  assert is_douyin_video_url(f"https://www.douyin.com/jingxuan?modal_id={JINGXUAN_ID}")
  assert is_douyin_video_url(f"https://www.douyin.com/video/{SAMPLE_ID}")
  assert is_douyin_video_url(f"https://www.iesdouyin.com/share/video/{SAMPLE_ID}")
  assert not is_douyin_video_url("https://www.suning.com/")


def test_extract_aweme_id_from_any_url():
  assert (
    extract_aweme_id_from_any_url(
      f"https://www.douyin.com/jingxuan?modal_id={JINGXUAN_ID}"
    )
    == JINGXUAN_ID
  )
  assert (
    extract_aweme_id_from_any_url(f"https://www.douyin.com/video/{SAMPLE_ID}")
    == SAMPLE_ID
  )


@patch("app.modules.douyin_web_resolve._request")
def test_validate_desktop_302(mock_req):
  resp = MagicMock()
  resp.status_code = 302
  resp.headers = {
    "Location": f"https://www.douyin.com/video/{SAMPLE_ID}?previous_page=app_code_link",
  }
  mock_req.return_value = resp
  result = validate_aweme_via_web(SAMPLE_ID, min_interval_s=0)
  assert result.verified is True
  assert result.format_id == "iesdouyin_share"
  assert result.canonical_url.endswith(SAMPLE_ID)


@patch("app.modules.douyin_web_resolve._validate_single_url")
def test_validate_aweme_multi_format_cascade(mock_validate):
  jingxuan_url = f"https://www.douyin.com/jingxuan?modal_id={JINGXUAN_ID}"
  fail = MagicMock(
    verified=False,
    share_url=jingxuan_url,
    format_id="douyin_jingxuan_modal",
    status="mobile_unverified",
    note="fail",
  )
  ok = MagicMock(
    verified=True,
    share_url=jingxuan_url,
    format_id="douyin_jingxuan_modal",
    status="ok",
    note="ok",
    aweme_id=JINGXUAN_ID,
  )
  mock_validate.side_effect = [fail, ok]
  result = validate_aweme_multi_format(JINGXUAN_ID, min_interval_s=0)
  assert result.verified is True
  assert result.share_url == jingxuan_url
  assert result.format_id == "douyin_jingxuan_modal"
  assert mock_validate.call_count == 2


@patch("app.modules.douyin_web_resolve.validate_aweme_multi_format")
def test_resolve_verified_url_best_verified(mock_multi):
  jingxuan_url = f"https://www.douyin.com/jingxuan?modal_id={JINGXUAN_ID}"
  mock_multi.return_value = MagicMock(
    verified=True,
    share_url=jingxuan_url,
  )
  url = resolve_verified_url(JINGXUAN_ID, require_web_verify=True, min_interval_s=0)
  assert url == jingxuan_url


@patch("app.modules.douyin_web_resolve._request")
def test_expand_short_link(mock_req):
  resp = MagicMock()
  resp.status_code = 200
  resp.history = [MagicMock(url="https://v.douyin.com/JPa1xhq/")]
  resp.url = (
    "https://www.iesdouyin.com/share/video/6883418578486349070/"
    "?utm_source=copy_link"
  )
  resp.text = ""
  mock_req.return_value = resp
  result = expand_short_link("https://v.douyin.com/JPa1xhq/", min_interval_s=0)
  assert result.aweme_id == "6883418578486349070"
  assert result.verified is True
  assert result.short_url.startswith("https://v.douyin.com/")
