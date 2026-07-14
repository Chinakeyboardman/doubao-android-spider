# -*- coding: utf-8 -*-
"""qa_reference_urls 单元测试（logcat/dumpsys 文本解析，无需真机）。"""

from __future__ import annotations

from app.modules.qa_hierarchy import Citation
from app.modules.qa_reference_urls import (
  apply_batch_douyin_urls,
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
  assert pick_best_url(urls) == "https://www.iesdouyin.com/share/video/7650085520299273595"


def test_extract_logcat_web_link_url():
  urls = extract_urls_from_logcat_text(SAMPLE_LOGCAT_WEB)
  assert pick_best_url(urls).startswith("https://www.jd.com/")


def test_pick_best_url_takes_last_match():
  text = SAMPLE_LOGCAT_DOUYIN + "\n" + SAMPLE_LOGCAT_WEB
  urls = extract_urls_from_logcat_text(text)
  assert pick_best_url(urls, prefer_last=True).startswith("https://www.jd.com/")
  assert pick_best_url(urls, prefer_last=False).startswith("https://www.iesdouyin.com/")


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


def test_validate_batch_douyin_ids_pass_and_fail():
  ids = extract_aweme_ids_ordered(SAMPLE_LOGCAT_BATCH_DOUYIN)
  assert validate_batch_douyin_ids(ids, 3) is True
  assert validate_batch_douyin_ids(ids, 2) is False
  assert validate_batch_douyin_ids(ids[:2] + [ids[1]], 3) is False


def test_apply_batch_douyin_urls_by_citation_order():
  citations = [
    Citation(title="a", ref_index=1),
    Citation(title="b", ref_index=2),
    Citation(title="c", ref_index=3, source="中关村在线"),
  ]
  ids = ["111", "222"]
  apply_batch_douyin_urls(citations, [0, 1], ids)
  assert citations[0].url.endswith("/111")
  assert citations[1].url.endswith("/222")
  assert citations[2].url == ""


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

  p = GestureProfile(qa_resolve_skip_douyin_per_click=True)
  douyin = Citation(title="#折叠屏对比 #推荐", source="")
  web = Citation(title="OPPO Find N6 产品参数 | OPPO 官方网站", source="")
  web_hash = Citation(title="万元预算推荐:OPPO Find N6领衔_PConline太平洋科技", source="")
  assert should_skip_douyin_url_resolve(douyin, p)
  assert not should_skip_douyin_url_resolve(web, p)
  assert not should_skip_douyin_url_resolve(web_hash, p)
  assert looks_like_web_citation(web)


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


def test_citation_swipe_budget_scales_with_index():
  from app.config.gesture_profile import GestureProfile

  p = GestureProfile(qa_resolve_citation_max_swipes=5)
  low = _citation_swipe_budget(Citation(title="a", ref_index=3), p, None)
  high = _citation_swipe_budget(Citation(title="b", ref_index=18), p, None)
  assert low == 5
  assert high > low
  assert high <= 24
