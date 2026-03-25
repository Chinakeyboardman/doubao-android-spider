# 从 UI Dump 录制到代码固化：研发全流程

本文面向在本仓库内扩展或维护豆包（`com.larus.nova`）自动化能力时的**标准工作法**：如何用真机 UI 层级（Accessibility / uiautomator2 dump）**录制与观测**，如何把观测结果**映射到模块与选择器**，以及如何**参数化**与**回归验证**。与当前主路径 **`FlowCrawler` + `run_flow_crawl.py`** 对齐。

---

## 1. 为什么要走「录制 → 分析 → 固化」

- **抓包**在本项目中已证明难以稳定拿到业务数据（见 `doc/capture_*.md`）；**UI 自动化**是可行主线。
- 豆包大量内容在 **WebView / 混合视图** 中，`resource-id` 有时稀疏；仅靠猜选择器成本高，**以真机 dump 为准**可减少误触、错页、无限循环等问题。
- **固化**指：把在 XML 里确认过的 **Activity 规则、XPath、bounds 几何、等待条件**写进 `app/modules/*` 与 `app/config/*`，而不是散落在脚本常量里。

---

## 2. 数据从哪来：三条互补渠道

| 渠道 | 脚本/入口 | 产出 | 典型用途 |
|------|-----------|------|----------|
| **轻量 UI 监控** | `recon/ui_spy.py` | `recon/output/ui_spy_log.jsonl` + 可选截图 | 快速扫屏、对比多页面节点摘要；适合给 AI/人读「当前屏有什么」 |
| **全流程操作录制** | `recon/flow_recorder/recorder.py` | `recon/flow_recorder/sessions/<ts>/`（`flow.jsonl`、`hierarchy/*.xml`、截图、`summary.md`） | **对齐用户真实操作顺序**；推断点击/滑动；保留**完整 XML** 供离线解析 |
| **APK 静态分析** | `recon/apk_decompile.py` | `recon/output/` 下报告与反编译树 | Activity 清单、layout 里列表控件、`strings.xml` 关键词；与真机 dump **交叉验证** |

**选用建议**：

- 要回答「这一步之后 Activity 叫什么、页面上有哪些 rid」→ 优先 **flow_recorder**（带完整 hierarchy）。
- 要长期挂着看「什么时候界面变了」→ **ui_spy**（指纹去重，体积小）。
- 要确认包内是否真有某 id/文案 → **apk_decompile** + 真机 dump 对照。

---

## 3. 录制阶段：操作规范

### 3.1 环境

- 真机 USB 调试、`adb devices` 可见；与主项目相同依赖 **`uiautomator2`**（见根目录 `requirements.txt`）。
- 包名固定为 **`com.larus.nova`**（与 `FlowCrawler.PACKAGE`、`navigator.PACKAGE` 一致）。

### 3.2 启动录制

**轻量监控**（默认 1s 一轮，仅界面变化时写一条）：

```bash
python recon/ui_spy.py
# python recon/ui_spy.py --interval 1.5 --no-screenshot
```

**全流程录制**（你手动完成「提问 → 等回复 → 点卡片 → 列表 → 详情 → 返回」整条路径，终端 Ctrl+C 结束）：

```bash
python recon/flow_recorder/recorder.py
# python recon/flow_recorder/recorder.py --interval 0.5 --no-screenshot
```

### 3.3 操作建议（提高 dump 可用性）

1. **每一步等界面静止**再进入下一步（减少半屏 WebView 未加载的「空壳」XML）。
2. 若某步依赖滚动，**多录几次**不同滚动位置（列表中部/底部），便于确认 bounds 变化规律。
3. 对「问题界面」**单独再录一小段**（只操作该页），减小 JSONL/XML 噪声。
4. 记录当时 **App 版本号、机型、分辨率**（profile 与选择器都可能随版本漂移）。

---

## 4. Dump 里要抓哪些字段（分析检查表）

对每条 UI 状态，优先整理：

| 字段 | 作用 | 固化去向 |
|------|------|----------|
| **Activity 全名**（`app_current()`） | 页面状态机 | `app/modules/navigator.py` 的 `_PAGE_RULES` |
| **resource-id** | 稳定点击/区域边界 | `flow_crawler.py` / `chat_ui_heuristics.py` 的 XPath |
| **class**（如 `FrameLayout`、`android.view.View`） | 无 rid 时的启发式 | `find_embedded_product_cards`、列表项采集等 |
| **text / content-desc** | 文案断言、主题命名、发送/停止等 | XPath 或 Python 字符串匹配 |
| **bounds** | 几何过滤、点击中心点 | `GestureProfile` 中的比例阈值 + 代码内 bounds 运算 |
| **clickable** | 区分可点图片与纯展示 | `_collect_applet_items` 等 |

**原则**：能用到 **resource-id** 就不要只靠坐标；坐标与比例放进 **`GestureProfile`**，便于多设备覆盖。

---

## 5. 从观测到代码：分层映射

下面按仓库真实模块说明「你在 XML 里看到的东西」应落在哪一层。

### 5.1 页面识别与导航 — `app/modules/navigator.py`

- **输入**：录制里的 `activity` 字符串（子串即可）。
- **固化**：`Page` 枚举 + `_PAGE_RULES` 中 `(keyword, Page)`；分享层等覆盖用 **rid 列表**（`_SHARE_OVERLAY_RIDS`）辅助。
- **扩展新页面**：新 Activity 关键字 + 必要时新 `Page` 值 + `safe_back_to_chat` / `dismiss_overlay` 行为验证。

### 5.2 聊天区布局与复制 — `app/modules/chat_ui_heuristics.py`

- **输入**：聊天页 XML 里 `title_container`、`message_list_parent`、`splitter`、`input_text`、`msg_action_copy`、`fast_button_icon` 等节点。
- **固化**：
  - 内容区上下界：`_CONTENT_TOP_SELECTORS` / `_CONTENT_BOTTOM_SELECTORS`。
  - 复制：`MSG_ACTION_COPY_XPATH`；回复正文候选：`iter_text_view_like_nodes`、`collect_reply_text_candidates`。
- **几何**：`content_top_y` / `content_bottom_y` 失败时的 fallback 比例在 **`GestureProfile`**（如 `content_top_fallback`）。

### 5.3 全流程编排 — `app/modules/flow_crawler.py`

- **输入**：端到端录制中各步的 Activity 与关键 rid（输入框、发送、停止生成、回底、列表、Web 详情）。
- **固化**：
  - 发送：`send_message` 内 XPath 列表。
  - 等待完成：`wait_reply_done` 对「停止」与正文稳定的判定。
  - **嵌入式商品卡片**：多为无 rid 的 `FrameLayout`，用 **宽高比 + 内容区 bounds + 与带 rid 节点 bounds 去重**（`find_embedded_product_cards`）；调参对应 **`GestureProfile` 中 `fc_card_*` 等字段**。
  - **Applet 列表**：`View` 文本 + 可点 `Image` 的几何关联（`_collect_applet_items`）；阈值在 **`fc_title_*`、`fc_title_image_max_dy`** 等。
  - **详情页**：`_capture_detail` 的纵向滑动与 `_extract_visible_texts` 的节点类型。

### 5.4 登录 — `app/modules/sms_login.py` + `flow_crawler.handle_login_if_needed`

- **输入**：登录相关 Activity（`AccountLoginActivity` 等）与手机号、验证码输入框 rid（以你录制 XML 为准）。
- **固化**：XPath、勾选协议、等待进入 `ChatActivity` 的超时与重试。

### 5.5 多设备参数 — `app/config/gesture_profile.py` + `profile_loader.py`

- **输入**：同一操作在不同分辨率上 bounds 比例差异、滑动是否误触侧边按钮、卡片高度比例等。
- **固化**：为机型建 `app/config/profiles/<key>.json`，由 `load_profile(device=...)` 自动叠加；不要在业务模块里写死像素。

---

## 6. 固化代码的标准工作流（可当作 PR 自检清单）

1. **复现路径**：用 flow_recorder 录一条最小复现（或用户提供的失败路径）。
2. **定位层级**：是 **页错了**（Navigator）、**区域错了**（chat_ui_heuristics）、还是 **业务步骤错了**（flow_crawler）。
3. **查 rid**：在 `hierarchy/*.xml` 或 ui_spy 的 `screen_elements` 里搜 `resource-id`、关键文案。
4. **改代码**：优先 XPath / 页面规则；几何只改 **GestureProfile 字段**。
5. **加保护**：新选择器加 **短 timeout**、失败分支打日志；避免 `press("back")` 误退出对话（历史问题见项目迭代记录）。
6. **真机回归**：`python run_flow_crawl.py`（必要时 `--skip-send`、`--max-cards`、`--max-products-per-card` 缩小范围）。
7. **文档**：若行为或产出目录有变，更新 `doc/real_flow_analysis.md` 或根 `README.md` 相关小节（与代码一致）。

---

## 7. 与静态分析（APK）的配合方式

```bash
python recon/apk_decompile.py
```

- 从 **AndroidManifest** 拉 Activity 列表，与 `navigator._PAGE_RULES` 对照，避免拼错类名。
- 在 **layout** 中搜 `RecyclerView` 等，理解列表实现；真机上仍要以 **dump 的 class/bounds** 为准（WebView 内可能看不到完整业务树）。
- `strings.xml` 中的文案可帮助写 **contains(@text,...)**，但需注意多语言与版本变化。

---

## 8. 入口与延伸阅读

| 主题 | 文档/代码 |
|------|-----------|
| 端到端步骤与产出目录 | [doc/real_flow_analysis.md](real_flow_analysis.md) |
| 项目总览与命令 | [根目录 README.md](../README.md) |
| 爬虫 CLI | `run_flow_crawl.py` |
| 录制器实现细节 | `recon/flow_recorder/recorder.py`、`recon/ui_spy.py` |

---

## 9. 已知局限（避免过度相信单次 dump）

- **WebView 内部 DOM** 不一定全部映射到原生节点；详情页可能只有少量 `android.view.View` 文本，其余依赖截图与滚动。
- **嵌入式卡片** 无稳定 rid 时，依赖几何启发式，版本或皮肤变化可能导致漏检/重复；需用录制 XML 调 `GestureProfile` 与过滤逻辑。
- **同一物理卡片** 滚动后 bounds 可能偏移，代码侧用 **容差去重**（如 `FlowCrawler._bounds_near`）；录制时注意观察数值波动范围。

按上述流程，可以把「我在真机上看到的现象」系统性地收敛为：**Navigator 状态 + XPath + GestureProfile + 回归命令**，便于多人协作与多设备部署。
