# -*- coding: utf-8 -*-
"""qa_reference_urls 单元测试（logcat/dumpsys 文本解析，无需真机）。"""

from __future__ import annotations

from app.modules.chat_ui_heuristics import (
  chat_prompt_conflicts,
  prompt_matches_chat,
  verify_chat_prompt,
)
from app.modules.qa_reference_urls import (
  apply_batch_douyin_urls,
  classify_citation_channel,
  extract_aweme_ids_ordered,
  extract_urls_from_dumpsys_text,
  extract_urls_from_logcat_text,
  is_likely_douyin_citation,
  looks_like_web_citation,
  pick_best_url,
  should_skip_douyin_url_resolve,
  validate_batch_douyin_ids,
  _citation_swipe_budget,
  _citation_xpath_strategies,
  _finalize_douyin_url,
  _scope_xpath_to_ref_list,
  _scroll_direction_for_missing_citation,
  _title_matches,
  _trust_from_strategy,
  _title_xpath_variants,
)
from app.modules.qa_hierarchy import (
  Citation,
  SEARCH_REF_LIST_RID,
  SOURCE_ITEM_RID,
  SUB_KEYWORD_REFERENCE_RID,
)


SAMPLE_DUMPSYS = """
mArguments=Bundle[{link_url=https://www.iesdouyin.com/share/video/7650085520299273595, foo=bar}]
Intent { dat=snssdk1128://detail cmp=com.ss.android.ugc.aweme/.detail.ui.DetailActivity }
https://schemas.android.com/apk/res/android
"""

SAMPLE_JD = """
mArguments=Bundle[{link_url=https://www.jd.com/jiage/9987e939fbf2446cabd4.html?brand=OPPO}]
"""

SAMPLE_LOGCAT_DOUYIN = """
I/am_create_activity( 1611): [0,124420105,97,com.ss.android.ugc.aweme/.app.DeepLinkHandlerActivity,android.intent.action.VIEW,NULL,snssdk1128://aweme/detail/7650085520299273595?refer=web&needlaunchlog=1]
I/am_create_activity( 1611): [0,126436676,97,com.ss.android.ugc.aweme/.detail.ui.DetailActivity,NULL,NULL,snssdk1128://detail,268435456]
"""

SAMPLE_LOGCAT_WEB = """
I/ActivityTaskManager( 1611): START u0 {hwFlg=0x10 cmp=com.larus.nova/com.larus.search.impl.WebActivity (has extras) link_url=https://www.jd.com/jiage/9987.html}
"""


def test_extract_link_url_from_dumpsys():
  urls = extract_urls_from_dumpsys_text(SAMPLE_DUMPSYS)
  assert urls[0] == "https://www.iesdouyin.com/share/video/7650085520299273595"
  assert all("schemas.android.com" not in u for u in urls)


def test_pick_best_url_from_dumpsys():
  urls = extract_urls_from_dumpsys_text(SAMPLE_JD)
  assert pick_best_url(urls).startswith("https://www.jd.com/")


def test_extract_logcat_snssdk_rebuilds_iesdouyin():
  urls = extract_urls_from_logcat_text(SAMPLE_LOGCAT_DOUYIN)
  assert pick_best_url(urls) == (
    "https://www.douyin.com/jingxuan?modal_id=7650085520299273595"
  )


def test_extract_logcat_web_link_url():
  urls = extract_urls_from_logcat_text(SAMPLE_LOGCAT_WEB)
  assert pick_best_url(urls).startswith("https://www.jd.com/")


def test_pick_best_url_takes_last_match():
  text = SAMPLE_LOGCAT_DOUYIN + "\n" + SAMPLE_LOGCAT_WEB
  urls = extract_urls_from_logcat_text(text)
  assert pick_best_url(urls, prefer_last=True).startswith("https://www.jd.com/")
  assert pick_best_url(urls, prefer_last=False).startswith(
    "https://www.douyin.com/jingxuan?modal_id="
  )


def test_title_xpath_variants_includes_ref_index():
  variants = _title_xpath_variants("华为 折叠 屏 开年 被 踢馆", ref_index=16)
  assert any('tv_reference_index' in v and '16.' in v for v in variants)
  assert any("华为 折叠 屏" in v for v in variants)


def test_title_xpath_variants_multiple_lengths():
  variants = _title_xpath_variants("HUAWEI Mate X7 规格参数 - 华为官网", ref_index=3)
  assert len(variants) >= 3
  assert any("HUAWEI Mate X7" in v for v in variants)

  assert extract_urls_from_dumpsys_text("") == []
  assert extract_urls_from_logcat_text("") == []
  assert pick_best_url([]) == ""


SAMPLE_LOGCAT_BATCH_DOUYIN = """
I/am_create_activity( 1611): snssdk1128://aweme/detail/1111111111111111111
I/am_create_activity( 1611): snssdk1128://aweme/detail/2222222222222222222
I/am_create_activity( 1611): snssdk1128://aweme/detail/3333333333333333333
"""


def test_extract_aweme_ids_ordered_distinct():
  ids = extract_aweme_ids_ordered(SAMPLE_LOGCAT_BATCH_DOUYIN)
  assert ids == [
    "1111111111111111111",
    "2222222222222222222",
    "3333333333333333333",
  ]


def test_extract_aweme_ids_includes_1180_scheme():
  text = "snssdk1180://aweme/detail/9999999999999999999"
  assert extract_aweme_ids_ordered(text) == ["9999999999999999999"]


def test_validate_batch_douyin_ids_pass_and_fail():
  ids = extract_aweme_ids_ordered(SAMPLE_LOGCAT_BATCH_DOUYIN)
  assert validate_batch_douyin_ids(ids, 3) is True
  assert validate_batch_douyin_ids(ids, 2) is False
  assert validate_batch_douyin_ids(ids[:2] + [ids[1]], 3) is False


def test_apply_batch_douyin_urls_by_citation_order():
  from app.config.gesture_profile import GestureProfile

  citations = [
    Citation(title="a", ref_index=1),
    Citation(title="b", ref_index=2),
    Citation(title="c", ref_index=3, source="中关村在线"),
  ]
  ids = ["1111111111111111111", "2222222222222222222"]
  profile = GestureProfile(qa_douyin_web_validate=False)
  apply_batch_douyin_urls(citations, [0, 1], ids, profile=profile)
  assert "1111111111111111111" in citations[0].url
  assert "2222222222222222222" in citations[1].url
  assert citations[2].url == ""


def test_apply_batch_douyin_urls_pc_web_validate(monkeypatch):
  from app.config.gesture_profile import GestureProfile

  profile = GestureProfile(qa_douyin_web_validate=True)
  citations = [
    Citation(title="#a", ref_index=1),
    Citation(title="#b", ref_index=2),
  ]

  def _fake_url(vid, profile=None):
    return f"https://www.douyin.com/video/{vid}"

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._douyin_url_from_id",
    _fake_url,
  )
  apply_batch_douyin_urls(
    citations,
    [0, 1],
    ["1111111111111111111", "2222222222222222222"],
    profile=profile,
  )
  assert citations[0].url == "https://www.douyin.com/video/1111111111111111111"
  assert citations[1].url == "https://www.douyin.com/video/2222222222222222222"


def test_try_batch_resolve_douyin_pc_web_ids_only(monkeypatch):
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import try_batch_resolve_douyin

  profile = GestureProfile(
    qa_douyin_web_validate=True,
    qa_resolve_accept_app_jump=False,
  )
  citations = [
    Citation(title="#折叠屏1", ref_index=1),
    Citation(title="#折叠屏2", ref_index=2),
    Citation(title="#折叠屏3", ref_index=3),
  ]
  ids = extract_aweme_ids_ordered(SAMPLE_LOGCAT_BATCH_DOUYIN)
  device = MagicMock()
  nav = MagicMock()
  stream = MagicMock()

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._click_citation",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._refresh_citation_bounds",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._return_from_douyin_resolve",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.douyin_handoff.try_resolve_douyin_after_click",
    lambda *_a, **_k: ("", ids[:2]),
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls.collect_aweme_ids_after_open",
    lambda **_k: ids,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._douyin_url_from_id",
    lambda vid, _profile=None: f"https://www.douyin.com/video/{vid}",
  )

  ok = try_batch_resolve_douyin(
    device,
    citations,
    nav=nav,
    profile=profile,
    stream=stream,
  )
  assert ok is True
  assert all(c.url for c in citations)


def test_douyin_batch_enabled_with_pc_web_validate():
  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation

  profile = GestureProfile(
    qa_resolve_batch_douyin=True,
    qa_douyin_web_validate=True,
  )
  pending = [
    Citation(title="#a", ref_index=1),
    Citation(title="#b", ref_index=2),
  ]
  use_batch = (
    profile.qa_resolve_batch_douyin
    and len(pending) >= 2
    and any(classify_citation_channel(c) == "douyin" for c in pending)
  )
  assert use_batch is True


def test_finalize_douyin_url_normalizes_to_preferred_prefix():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(qa_douyin_web_validate=False)
  # iesdouyin 原链 → 归一到偏好首选前缀 jingxuan modal
  out = _finalize_douyin_url(
    "https://www.iesdouyin.com/share/video/7428415093521648905", p,
  )
  assert out == "https://www.douyin.com/jingxuan?modal_id=7428415093521648905"


def test_finalize_douyin_url_keeps_web_and_spu():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(qa_douyin_web_validate=False)
  web = "https://post.m.smzdm.com/p/anv4emev/"
  assert _finalize_douyin_url(web, p) == web
  suning = "https://www.suning.com/item/0071586372/12431678383.html"
  assert _finalize_douyin_url(suning, p) == suning
  spu = "https://douyin_spu_knowledge?spu_name=abc"
  assert _finalize_douyin_url(spu, p) == spu
  assert _finalize_douyin_url("", p) == ""


def test_finalize_douyin_url_respects_profile_order():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(
    qa_douyin_web_validate=False,
    qa_douyin_web_url_formats=("douyin_video", "douyin_jingxuan_modal"),
  )
  out = _finalize_douyin_url(
    "https://www.iesdouyin.com/share/video/7428415093521648905", p,
  )
  assert out == "https://www.douyin.com/video/7428415093521648905"


def test_scope_xpath_to_ref_list():
  xp = '//*[@resource-id="com.larus.nova:id/tv_reference_content"]'
  scoped = _scope_xpath_to_ref_list(xp)
  assert SEARCH_REF_LIST_RID in scoped
  assert "tv_reference_content" in scoped
  sub_root = f'//*[@resource-id="{SUB_KEYWORD_REFERENCE_RID}"]'
  scoped_sub = _scope_xpath_to_ref_list(xp, root_xpath=sub_root)
  assert SUB_KEYWORD_REFERENCE_RID in scoped_sub


def test_citation_xpath_prefers_source_item_row():
  cite = Citation(title="vivo X Fold6 发布", ref_index=3)
  strategies = _citation_xpath_strategies(cite)
  assert strategies
  name, xp = strategies[0]
  assert name.startswith("row_index")
  assert SOURCE_ITEM_RID in xp
  assert SEARCH_REF_LIST_RID in xp
  assert '@text="3."' in xp

def test_citation_xpath_sub_keyword_root():
  cite = Citation(title="vivo X Fold6 发布", ref_index=3)
  sub_root = f'//*[@resource-id="{SUB_KEYWORD_REFERENCE_RID}"]'

  class _MockDevice:
    def xpath(self, sel: str):
      exists = SUB_KEYWORD_REFERENCE_RID in sel and SEARCH_REF_LIST_RID not in sel.replace(
        SUB_KEYWORD_REFERENCE_RID, "",
      )
      return type("Node", (), {"exists": exists})()

  strategies = _citation_xpath_strategies(cite, _MockDevice())
  assert strategies
  assert SUB_KEYWORD_REFERENCE_RID in strategies[0][1]
  assert SEARCH_REF_LIST_RID not in strategies[0][1]


def test_title_matches_fuzzy():
  assert _title_matches(
    "vivo X Fold6正式发布:天玑9500",
    "vivo X Fold6正式发布:天玑9500超能版+2亿像素",
  )
  assert not _title_matches("OPPO Find N6", "vivo X Fold6")


def test_trust_from_strategy_fills_index():
  cite = Citation(title="测试标题很长", ref_index=4)
  idx, title = _trust_from_strategy("row_index_title_18", cite, "", "")
  assert idx == "4."
  assert title == cite.title


def test_is_likely_douyin_citation_heuristic():
  assert is_likely_douyin_citation(Citation(title="#折叠屏 #推荐", source=""))
  assert is_likely_douyin_citation(
    Citation(title="面霜测评！！|||混油皮亲测", source=""),
  )
  assert not is_likely_douyin_citation(
    Citation(title="中关村在线评测", source="中关村在线"),
  )
  assert not is_likely_douyin_citation(
    Citation(
      title="vivo X Fold6发布：7999元起！AI轻办公神器，重新定义折叠屏生产力属性",
      source="",
    ),
  )


def test_should_skip_douyin_url_resolve_keeps_web_citations():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(
    qa_resolve_skip_douyin_per_click=True,
    qa_resolve_batch_douyin=False,
  )
  douyin = Citation(title="#折叠屏对比 #推荐", source="")
  web = Citation(title="OPPO Find N6 产品参数 | OPPO 官方网站", source="")
  web_hash = Citation(title="万元预算推荐:OPPO Find N6领衔_PConline太平洋科技", source="")
  assert should_skip_douyin_url_resolve(douyin, p)
  assert not should_skip_douyin_url_resolve(web, p)
  assert not should_skip_douyin_url_resolve(web_hash, p)
  assert looks_like_web_citation(web)


def test_should_skip_douyin_off_when_batch_enabled():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(
    qa_resolve_skip_douyin_per_click=True,
    qa_resolve_batch_douyin=True,
  )
  douyin = Citation(title="#折叠屏对比 #推荐", source="")
  assert not should_skip_douyin_url_resolve(douyin, p)


def test_should_skip_douyin_off_when_profile_disabled():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(qa_resolve_skip_douyin_per_click=False)
  cite = Citation(title="#仅话题标签", source="")
  assert not should_skip_douyin_url_resolve(cite, p)


def test_scroll_direction_for_missing_citation():
  cite = Citation(title="t", ref_index=14)
  assert _scroll_direction_for_missing_citation(cite, (1, 12)) == "down"
  assert _scroll_direction_for_missing_citation(cite, (15, 18)) == "up"
  assert _scroll_direction_for_missing_citation(cite, None) == "down"


def test_prompt_matches_chat():
  exp = "AI折叠手机2026年推荐几款？"
  assert prompt_matches_chat(exp, exp)
  assert prompt_matches_chat(exp, "AI折叠手机2026年推荐几款")
  assert not prompt_matches_chat(exp, "聊聊新话题")
  assert not prompt_matches_chat(exp, "AI功能强大的折叠屏手机推荐哪款？")


class _FakeDevice:
  """最小化设备：dump_hierarchy 返回指定 XML（空=当前屏读不到气泡）。"""

  def __init__(self, xml: str = ""):
    self.info = {"displayWidth": 1080, "displayHeight": 2400}
    self._xml = xml

  def dump_hierarchy(self, compressed: bool = False) -> str:
    return self._xml


def test_verify_chat_prompt_tolerates_missing_bubble():
  """回归：问题已滚出屏幕（读不到用户气泡）不应判定会话错位。"""
  dev = _FakeDevice("")
  assert verify_chat_prompt(dev, "AI折叠手机2026年推荐几款？")
  conflict, visible = chat_prompt_conflicts(dev, "AI折叠手机2026年推荐几款？")
  assert conflict is False
  assert visible == ""


def test_verify_chat_prompt_empty_expected_passes():
  dev = _FakeDevice("")
  assert verify_chat_prompt(dev, "")
  assert chat_prompt_conflicts(dev, "")[0] is False


def test_resolve_simple_mode_uses_single_pass(monkeypatch):
  """simple_mode 应走单遍解析，不进入快速/笨办法分两趟。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import resolve_thinking_reference_urls

  called = {"simple": False}

  def _fake_simple(*_a, **_k):
    called["simple"] = True
    return _k.get("citations") or _a[1]

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_thinking_reference_urls_simple",
    _fake_simple,
  )
  cites = [Citation(title="a", ref_index=1)]
  resolve_thinking_reference_urls(
    MagicMock(),
    cites,
    profile=GestureProfile(qa_resolve_simple_mode=True),
  )
  assert called["simple"]


def test_stitch_verify_douyin_url_uses_http_verify(monkeypatch):
  from app.config.gesture_profile import GestureProfile
  from app.modules.douyin_web_resolve import DouyinWebResolveResult
  from app.modules.qa_reference_urls import stitch_verify_douyin_url

  aid = "6853846152920550659"

  def _fake_validate(aweme_id, **kwargs):
    assert aweme_id == aid
    assert kwargs["format_ids"] == ("douyin_jingxuan_modal", "douyin_video")
    return DouyinWebResolveResult(
      aweme_id=aid,
      share_url=f"https://www.douyin.com/jingxuan?modal_id={aid}",
      verified=True,
      status="ok",
      format_id="douyin_jingxuan_modal",
    )

  monkeypatch.setattr(
    "app.modules.douyin_web_resolve.validate_aweme_multi_format",
    _fake_validate,
  )
  p = GestureProfile(
    qa_douyin_web_validate=True,
    qa_douyin_web_url_formats=("douyin_jingxuan_modal", "douyin_video"),
  )
  url = stitch_verify_douyin_url(aid, p)
  assert "modal_id=" in url


def test_apply_session_guard_env_enables_brute_and_full_mode(monkeypatch):
  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _apply_session_guard_env

  monkeypatch.setenv("QA_URL_SIMPLE", "0")
  monkeypatch.setenv("QA_URL_SKIP_BRUTE", "0")
  monkeypatch.setenv("QA_URL_SESSION_GUARD", "1")
  p = GestureProfile(
    qa_resolve_simple_mode=True,
    qa_resolve_skip_brute_pass=True,
    qa_resolve_session_guard=False,
  )
  _apply_session_guard_env(p)
  assert p.qa_resolve_simple_mode is False
  assert p.qa_resolve_skip_brute_pass is False
  assert p.qa_resolve_session_guard is True


def test_apply_session_guard_env_enables_douyin_capture(monkeypatch):
  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _apply_session_guard_env

  monkeypatch.setenv("QA_URL_SKIP_DOUYIN", "0")
  monkeypatch.setenv("QA_DOUYIN_STITCH_VERIFY", "1")
  p = GestureProfile(
    qa_resolve_skip_douyin_per_click=True,
    qa_douyin_web_validate=False,
  )
  _apply_session_guard_env(p)
  assert p.qa_resolve_skip_douyin_per_click is False
  assert p.qa_douyin_web_validate is True
  assert p.qa_douyin_web_url_formats == (
    "douyin_jingxuan_modal",
    "douyin_video",
  )


def test_chat_context_guard_disabled_skips_check(monkeypatch):
  """守卫关闭时即使冲突也判为在目标会话（60710 轻量模式）。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _chat_context_ok

  called = {"n": 0}

  def _conflict(*_a, **_k):
    called["n"] += 1
    return True, "别的问题"

  monkeypatch.setattr(
    "app.modules.chat_ui_heuristics.chat_prompt_conflicts", _conflict,
  )
  profile = GestureProfile(qa_resolve_session_guard=False)
  assert _chat_context_ok(MagicMock(), "目标提问？", profile, "t") is True
  assert called["n"] == 0


def test_chat_context_force_check_when_guard_off(monkeypatch):
  """回退后 force=True 时仍校验（Honor safe_back 防落历史会话）。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _chat_context_ok

  monkeypatch.setattr(
    "app.modules.chat_ui_heuristics.chat_prompt_conflicts",
    lambda *_a, **_k: (True, "历史提问"),
  )
  monkeypatch.setattr("app.modules.qa_reference_urls.time.sleep", lambda *_a: None)
  profile = GestureProfile(
    qa_resolve_session_guard=False,
    qa_resolve_verify_chat_after_back=True,
  )
  assert _chat_context_ok(MagicMock(), "目标提问？", profile, "t", force=True) is False


def test_chat_context_reconfirm_clears_transient(monkeypatch):
  """首读冲突、二次读不冲突 → 判定误判，返回 True。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _chat_context_ok

  seq = [(True, "瞬时残缺"), (False, "")]

  def _conflict(*_a, **_k):
    return seq.pop(0)

  monkeypatch.setattr(
    "app.modules.chat_ui_heuristics.chat_prompt_conflicts", _conflict,
  )
  monkeypatch.setattr("app.modules.qa_reference_urls.time.sleep", lambda *_a: None)
  profile = GestureProfile(
    qa_resolve_session_guard=True,
    qa_resolve_session_guard_reconfirm=True,
  )
  assert _chat_context_ok(MagicMock(), "目标提问？", profile, "t") is True
  assert seq == []


def test_chat_context_reconfirm_confirms_real_mismatch(monkeypatch):
  """两次都冲突 → 判定真错位，返回 False。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _chat_context_ok

  monkeypatch.setattr(
    "app.modules.chat_ui_heuristics.chat_prompt_conflicts",
    lambda *_a, **_k: (True, "另一条历史提问"),
  )
  monkeypatch.setattr("app.modules.qa_reference_urls.time.sleep", lambda *_a: None)
  profile = GestureProfile(
    qa_resolve_session_guard=True,
    qa_resolve_session_guard_reconfirm=True,
  )
  assert _chat_context_ok(MagicMock(), "目标提问？", profile, "t") is False


def test_chat_prompt_conflicts_tolerates_answer_anchor():
  """引用解析期：目标回答锚点在屏上时不应因历史提问判错位。"""
  xml = (
    '<hierarchy><node text="按预算和用途，给你挑了2026年热门新款，'
    '覆盖性价比、游戏、影像、全能旗舰" /></hierarchy>'
  )
  dev = _FakeDevice(xml)
  exp = "看比赛用什么手机拍视频清晰？"
  conflict, _ = chat_prompt_conflicts(
    dev,
    exp,
    answer_snippet="按预算和用途，给你挑了2026年热门新款",
  )
  assert conflict is False


def test_resolve_pending_pass_recovers_at_loop_start(monkeypatch):
  """循环开头会话错位时应先恢复，而非直接中止。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import _resolve_pending_pass

  profile = GestureProfile(
    qa_resolve_recover_max_per_task=2,
    qa_url_reachability_check=False,
  )
  citations = [Citation(title="a", ref_index=1)]
  pending = [(0, citations[0])]
  checks = {"n": 0}
  recover_calls = {"n": 0}

  def _context_ok(_device, _prompt, _profile, tag):
    checks["n"] += 1
    return checks["n"] > 1

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._chat_context_ok",
    _context_ok,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._recover_chat_context",
    lambda *_a, **_k: recover_calls.__setitem__("n", recover_calls["n"] + 1) or True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_one_citation_url",
    lambda *_a, **_k: "https://example.com/x",
  )

  attempts = _resolve_pending_pass(
    MagicMock(),
    citations,
    pending,
    nav=MagicMock(),
    profile=profile,
    serial="s",
    click_method="logcat",
    stream=MagicMock(),
    recent_logcat_urls=[],
    pass_label="笨办法",
    brute_force=True,
    max_refs=0,
    attempts_so_far=0,
    resolved_by_index={},
    expected_prompt="观赛拍摄手机推荐哪款好？",
    recover_state=[0],
  )
  assert recover_calls["n"] == 1
  assert attempts == 1
  assert citations[0].url == "https://example.com/x"

  web = Citation(title="OPPO Find N6 产品参数 | OPPO 官方网站", source="")
  douyin = Citation(title="#折叠屏对比 #推荐", source="")
  unknown = Citation(title="vivo X Fold6发布：7999元起", source="")
  assert classify_citation_channel(web) == "web"
  assert classify_citation_channel(douyin) == "douyin"
  assert classify_citation_channel(unknown) == "unknown"
  assert classify_citation_channel(
    Citation(title="IT之家评测", source="IT之家"),
  ) == "web"
  assert classify_citation_channel(
    Citation(title="雅诗兰黛面霜家族！！|||一篇速通", source=""),
  ) == "douyin"
  assert classify_citation_channel(
    Citation(title="深度解析-大河新闻网", source=""),
  ) == "web"


def test_citation_swipe_budget_scales_with_index():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(qa_resolve_citation_max_swipes=5)
  low = _citation_swipe_budget(Citation(title="a", ref_index=3), p, None)
  high = _citation_swipe_budget(Citation(title="b", ref_index=18), p, None)
  assert low == 5
  assert high > low
  assert high <= 24


def test_try_fast_url_after_click_logcat_id_first(monkeypatch):
  """快速路径应优先 logcat aweme_id，不先跑长 dumpsys。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_reference_urls import _try_fast_url_after_click

  profile = GestureProfile(qa_douyin_web_validate=False)
  stream = MagicMock()
  stream.text_since_mark.return_value = SAMPLE_LOGCAT_DOUYIN
  nav = MagicMock()
  nav.current_page.return_value = (MagicMock(name="CHAT"), "")

  dumpsys_called: list[float] = []

  def _fake_dumpsys(device, *, serial, wait_s):
    dumpsys_called.append(wait_s)
    return ""

  monkeypatch.setattr(
    "app.modules.qa_reference_urls.resolve_url_via_dumpsys",
    _fake_dumpsys,
  )
  monkeypatch.setattr("app.modules.qa_reference_urls.time.sleep", lambda *_: None)

  url = _try_fast_url_after_click(
    MagicMock(),
    nav,
    profile=profile,
    serial="serial",
    stream=stream,
    method="logcat",
    ref_idx="3",
    channel="douyin",
  )
  assert url == "https://www.douyin.com/jingxuan?modal_id=7650085520299273595"
  assert dumpsys_called == []


def test_brute_pass_not_blocked_by_fast_attempts(monkeypatch):
  """快速阶段耗尽 attempts 后，笨办法仍应处理剩余 pending。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import _resolve_pending_pass

  profile = GestureProfile()
  citations = [Citation(title=f"t{i}", ref_index=i) for i in range(1, 4)]
  pending = [(i, citations[i]) for i in range(3)]
  nav = MagicMock()
  stream = MagicMock()
  calls: list[bool] = []

  def _fake_resolve(*_args, brute_force=False, **_kwargs):
    calls.append(brute_force)
    return "" if brute_force else "https://example.com/a"

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_one_citation_url",
    _fake_resolve,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._chat_context_ok",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )

  fast_attempts = _resolve_pending_pass(
    MagicMock(),
    citations,
    pending,
    nav=nav,
    profile=profile,
    serial="s",
    click_method="logcat",
    stream=stream,
    recent_logcat_urls=[],
    pass_label="快速",
    max_refs=3,
    attempts_so_far=0,
    resolved_by_index={},
  )
  assert fast_attempts == 3
  assert sum(1 for c in citations if c.url) == 3

  for i, c in enumerate(citations):
    c.url = ""

  brute_calls: list[bool] = []
  def _fake_brute(*_args, brute_force=False, **_kwargs):
    brute_calls.append(brute_force)
    return "https://example.com/brute"

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_one_citation_url",
    _fake_brute,
  )
  _resolve_pending_pass(
    MagicMock(),
    citations,
    pending,
    nav=nav,
    profile=profile,
    serial="s",
    click_method="logcat",
    stream=stream,
    recent_logcat_urls=[],
    pass_label="笨办法",
    brute_force=True,
    max_refs=0,
    attempts_so_far=0,
    resolved_by_index={},
  )
  assert brute_calls == [True, True, True]
  assert all(c.url == "https://example.com/brute" for c in citations)


def test_batch_feed_swipes_zero_when_pc_web_validate(monkeypatch):
  """PC Web 验证开启时批量路径不得滑抖音 feed。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import try_batch_resolve_douyin

  profile = GestureProfile(
    qa_douyin_web_validate=True,
    qa_resolve_accept_app_jump=True,
  )
  citations = [
    Citation(title="#a", ref_index=1),
    Citation(title="#b", ref_index=2),
  ]
  captured: dict[str, int] = {}

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._click_citation",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._refresh_citation_bounds",
    lambda *_a, **_k: None,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._return_from_douyin_resolve",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls.collect_aweme_ids_after_open",
    lambda **_k: [],
  )

  def _capture_swipes(*_a, **kwargs):
    captured["batch_feed_swipes"] = kwargs.get("batch_feed_swipes", -1)
    return "", []

  monkeypatch.setattr(
    "app.modules.douyin_handoff.try_resolve_douyin_after_click",
    _capture_swipes,
  )

  try_batch_resolve_douyin(
    MagicMock(),
    citations,
    nav=MagicMock(),
    profile=profile,
    stream=MagicMock(),
  )
  assert captured.get("batch_feed_swipes") == 0


def test_resolve_phase_timeout_returns_partial(monkeypatch):
  """URL 阶段 wall-clock 超时后应返回已解析 partial，不无限空转。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import resolve_thinking_reference_urls

  profile = GestureProfile(
    qa_resolve_batch_douyin=False,
    qa_resolve_url_phase_budget_sec=0.01,
  )
  citations = [
    Citation(title="web1", ref_index=1, source="网易"),
    Citation(title="web2", ref_index=2, source="腾讯"),
  ]
  call_count = {"n": 0}

  def _slow_resolve(*_a, **_k):
    call_count["n"] += 1
    import time
    time.sleep(0.05)
    return ""

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_one_citation_url",
    _slow_resolve,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._chat_context_ok",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls.LogcatStream",
    lambda **_k: MagicMock(start=MagicMock(), stop=MagicMock(), mark=MagicMock()),
  )

  out = resolve_thinking_reference_urls(
    MagicMock(),
    citations,
    profile=profile,
    serial="test",
    expected_prompt="test prompt",
  )
  assert len(out) == 2
  assert call_count["n"] < 4


def test_recover_max_per_task_stops_pass(monkeypatch):
  """单条任务会话恢复次数达上限后应中止 pass。"""
  from unittest.mock import MagicMock

  from app.config.gesture_profile import GestureProfile
  from app.modules.qa_hierarchy import Citation
  from app.modules.qa_reference_urls import _resolve_pending_pass

  profile = GestureProfile(qa_resolve_recover_max_per_task=1)
  citations = [
    Citation(title="a", ref_index=1),
    Citation(title="b", ref_index=2),
  ]
  pending = [(0, citations[0]), (1, citations[1])]
  recover_calls = {"n": 0}

  def _context_ok(_device, _prompt, _profile, tag):
    # 解析后校验模拟错位；循环开头 tag 为 pass_label 不含 #
    if "#" in str(tag):
      return False
    return True

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._chat_context_ok",
    _context_ok,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._ensure_citation_visible",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "app.modules.qa_reference_urls._resolve_one_citation_url",
    lambda *_a, **_k: "https://example.com/x",
  )

  def _recover(*_a, **_k):
    recover_calls["n"] += 1
    return False

  monkeypatch.setattr(
    "app.modules.qa_reference_urls._recover_chat_context",
    _recover,
  )

  attempts = _resolve_pending_pass(
    MagicMock(),
    citations,
    pending,
    nav=MagicMock(),
    profile=profile,
    serial="s",
    click_method="logcat",
    stream=MagicMock(),
    recent_logcat_urls=[],
    pass_label="快速",
    max_refs=0,
    attempts_so_far=0,
    resolved_by_index={},
    expected_prompt="prompt",
    recover_state=[0],
  )
  assert recover_calls["n"] == 1
  assert attempts <= 1
