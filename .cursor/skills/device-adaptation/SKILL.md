---
name: device-adaptation
description: >-
  豆包 APP 爬虫新机/新机型真机适配方法论：沙箱步骤、diagnose 证据链、profile 优先、
  子流程拆分、登录与抖音 URL 取链、探针验证、固化进主流程。在用户提到新设备适配、
  vivo/OPPO/Honor 新机、run_adapt、gesture profile、AppJump、抖音链接、spot_check
  双机并行、或 var/新设备适配 时使用。
---

# 新机型真机适配

## 原则（先读）

1. **证据先于改码**：卡住 → `snap` + `diagnose` + 看 `screen.png`，再动 Python。
2. **profile 优先于业务**：分辨率、滑动 ROI、超时、机型开关先写 `app/config/profiles/<机型>.json`，再改 `flow_crawler` / `qa_capture`。
3. **最小 diff 固化**：沙箱验证通过的子流程，合并进 `flow_crawler` / `navigator` / `sms_login` / `qa_reference_urls`，勿留一次性脚本逻辑。
4. **专项探针闭环**：抖音链接等硬指标用 `scripts/run_douyin_url_probe.py` 反复测到 `overall_ok=true` 再开批量抽检。
5. **双机并行**：按 `ADB_SERIAL` 启 worker，勿 `pkill` 全量 `run_qa_spot_check.py`；雅诗兰黛等强制链接批次设 `SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS=0`。

## 沙箱布局

```
var/新设备适配/<profile_key>/<YYYYMMDD>/
  run_adapt.sh          # step 1-7 / diagnose / migrate
  ADAPT_LOG.md          # 里程碑与子流程表
  steps/S0x_*/          # screen.png hierarchy.xml activity.txt notes.md
  douyin_url_probe/     # 探针产出（可选）
```

复制模板：参考 `var/新设备适配/vivo_v2301a/20260715/`。

## 标准步骤（S01–S07）

| 步骤 | 验证点 | 常见子流程 |
|------|--------|------------|
| S01 连通 | adb、device_info、profile 自动匹配 | — |
| S02 启动 | 到 Chat 或可控登录前状态 | S02a 隐私 / S02b 更新 / S02c 权限 |
| S03 登录 | 非游客、可发消息 | S03b SMS / S03c 游客横幅必点 |
| S04 发问答 | 发送+等回复+早期正文 | S04a input rid / S04b 发送 / S04d 新建会话 |
| S05 引用 URL | 思考引用 + **抖音 iesdouyin 齐** | S05a 抖音 SMS / S05b AppJump / S05c Permission / S05d 深链 |
| S06 长截图 | 重叠率、拼接无漏截 | 见 `test_qa_longshot_overlap` |
| S07 pilot | `migrate` → `var/<项目>/run_unattended.sh` | 独立 screen 名 + serial |

命令：

```bash
bash var/新设备适配/<机型>/<日期>/run_adapt.sh step 3
bash var/新设备适配/<机型>/<日期>/run_adapt.sh diagnose S04_send_qa
bash var/新设备适配/<机型>/<日期>/run_adapt.sh migrate   # 生成项目 run_unattended.sh
```

## 卡住时的四步法

```
1. ./run_adapt.sh diagnose S0x_xxx   → notes.md + 规则匹配子流程
2. 打开 screen.png（uidump 看不到的系统层）
3. 判断：缺子流程？selector 错？还是 profile 参数？
4. 最小修复 → 重跑同一步 → 记入 ADAPT_LOG.md
```

引擎：`scripts/adapt_diagnose.py`（`SUBFLOW_RULES` 可追加机型规则）。

## 有效改码顺序（优化阶梯）

按优先级尝试，**每级跑通一步再进下一级**：

1. **profile 字段**：`qa_shot_*`、`qa_resolve_*`、`qa_resolve_accept_app_jump`、`qa_resolve_batch_douyin`、超时。
2. **navigator 子流程**：弹窗 dismiss/accept、权限、`wait_and_accept_app_jump`、`wait_for_aweme_foreground`、坐标/hierarchy 兜底。
3. **flow_crawler**：`handle_login_if_needed`（游客→SMS、残留验证码杀进程换号）、`send_message` 多 xpath、`start_app` 阻塞对话框。
4. **qa_reference_urls / douyin_handoff**：`prepare_citations_for_url_resolve`、**深链优先** `resolve_via_aweme_deeplink`、`advance_handoff` 状态机、批量抖音滑 feed、`recover_from_external_douyin(gentle=True)` + `reenter_chat_by_prompt`。
5. **douyin_sms_login**：批次前 / LoginWall 时 `ensure_douyin_logged_in`（与豆包同 `SMS_DEVICE_ID`）。
6. **探针 / 单测**：`run_douyin_url_probe.py` 策略 A/B/C/**D**、pytest 顶部文档化用例。

禁止：未 diagnose 就大面积改通用逻辑；坐标点击引用（生产 `_click_citation` 禁止无 DOM 坐标）。

## 登录（批量必做）

- SMS **惰性触发**：仅 Login Activity 取号；已登录 Chat 零 SMS。
- **游客态**：`tv_login_guide_banner` → 必进 SMS（约 10–20 条限额，批量不够）。
- 卡在旧验证码页：`force-stop` 豆包 → 换号重试（最多 3 次）。
- 验证码 API：兼容纯文本与 `{"code":"123456"}` JSON。
- 抖音若需登录：**与豆包同手机号**（同 `SMS_DEVICE_ID` 池）。

## 抖音链接（最高优先级）

### PC Web 辅助验证（无需手机开抖音）

手机 logcat 抽到 **19 位 aweme_id** 后，PC 可 HTTP 验证并拼装 iesdouyin：

```bash
.venv/bin/python scripts/run_douyin_web_resolve_probe.py
# 报告: doc/reports/douyin_web_resolve/<timestamp>/REPORT.md
```

- desktop 302 → `douyin.com/video/{id}` 即视为视频存在
- `v.douyin.com` **仅可反向展开**，不能从 id 正向生成
- profile：`qa_douyin_web_validate: true`（见 `douyin_web_resolve.py`）

### 手机端 Handoff / 深链

解析顺序（生产 `resolve_thinking_reference_urls`）：

```
批次前 ensure_douyin_logged_in（一次）
→ 深链 snssdk1128/1180 + android_id（P0）
→ Handoff 状态机 AppJump / Permission / LoginWall（P1+P2）
→ feed 批量 logcat（B）
→ 逐条 logcat/dumpsys（C）
→ 每条返回后 reenter_chat + _chat_context_ok
```

```bash
.venv/bin/python scripts/run_douyin_url_probe.py -s <SERIAL> --prompt "<含抖音引用的提示词>"
```

通过标准：至少策略 A 或 **D** `resolved_url` 含 `iesdouyin`；批量路径 B `filled_count` 接近抖音引用数。

Handoff 状态图（`app/modules/douyin_handoff.py`）：

```
点击引用 → logcat 抽 aweme_id
  ├─ 有 id → 深链 am start → 读 link_url / 重建 iesdouyin
  └─ 无 id → AppJump → Permission → LoginWall(SMS) → Feed 滑动
→ lite_back / gentle recover → _chat_context_ok → 错位则 reenter_chat_by_prompt
```

机型要点（vivo 等）：

- 点击引用后可能出现 `AppJumpPromptActivity`（`appfilter`）→ **先等弹窗再点「打开」**，xpath 失败用 hierarchy 坐标 / 典型右侧按钮坐标。
- 进抖音后处理 `PermissionActivity` → 点「允许」（多页循环）。
- 登录墙 → **抖音同号 SMS**（`run_adapt.sh step 5a` / S05a diagnose）。
- URL 解析前调用 `prepare_citations_for_url_resolve`（采集后面板常收起）。
- 批量：进 feed 后按抖音条数多滑几屏再 `collect_aweme_ids_after_open`；部分 id 时剩余走逐条深链。

profile 示例：`qa_douyin_deeplink_first: true`，`qa_douyin_deeplink_schemes: ["snssdk1128","snssdk1180"]`，`qa_resolve_accept_app_jump: true`，`qa_resolve_batch_douyin: true`，`qa_douyin_ensure_login_before_batch: true`。

## 终端监控

```bash
bash var/<项目>/run_unattended.sh status   # CSV 进度 + log tail
bash var/<项目>/run_unattended.sh logs     # tail -f spot_check_run.log
```

`restart` / `start` 仅停止**本机 serial** worker，不杀 Honor 并行任务。

沙箱 S01–S07 通过后：

1. 确认 `app/config/profiles/<key>.json` 已提交。
2. `run_adapt.sh migrate` → 项目 `var/<品牌>/run_unattended.sh`（设 `ADB_SERIAL`、`SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS=0` 若强制全链接）。
3. 启动：`bash var/<项目>/run_unattended.sh start`（脚本内按 serial 判重，可与 Honor 并行）。
4. 在 `ADAPT_LOG.md` 写里程碑，**删临时 walkthrough**，保留 `steps/` 快照作回归证据。

## 测试与文档

- 新行为加 **pytest**：模块顶 doc（场景/命令/前置）+ 每个 `test_*` 顶 doc（目的/断言）。
- 真机用例：`scripts/run_douyin_url_probe.py` 内 A/B/C/**D** 策略表（见该文件 module doc）。

## 更多细节

- 子流程 ID 全表、文件地图、vivo 案例： [reference.md](reference.md)
- 里程碑示例： [examples.md](examples.md)
