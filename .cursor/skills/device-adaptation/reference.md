# 机型适配参考

## 关键文件地图

| 用途 | 路径 |
|------|------|
| 手势/QA 参数 | `app/config/profiles/*.json` |
| profile 加载 | `app/config/profile_loader.py`、`gesture_profile.py` |
| 启动/登录/发送 | `app/modules/flow_crawler.py` |
| SMS 登录 | `app/modules/sms_login.py` |
| 抖音 SMS 同号登录 | `app/modules/douyin_sms_login.py` |
| 豆包→抖音 Handoff / 深链 | `app/modules/douyin_handoff.py` |
| 页面/弹窗/抖音恢复 | `app/modules/navigator.py` |
| 引用 URL / 抖音批量 | `app/modules/qa_reference_urls.py` |
| 问答采集 | `app/modules/qa_capture.py` |
| 适配沙箱脚本 | `var/新设备适配/<机型>/<日期>/run_adapt.sh` |
| 卡住诊断 | `scripts/adapt_diagnose.py` |
| 抖音探针 | `scripts/run_douyin_url_probe.py` |
| 无人值守 | `scripts/run_unattended_spot_check.sh`、`var/<项目>/run_unattended.sh` |

## 子流程 ID（可扩展到 adapt_diagnose）

| ID | 触发 | 处理要点 |
|----|------|----------|
| S02a_privacy_dialog | 欢迎使用豆包 | `com.larus.nova:id/confirm` 同意 |
| S02b_update_dialog | 发现新版本 | `tvDialogCancel` 忽略 |
| S02c_runtime_permission | GrantPermissionsActivity | `permission_allow_button` / 允许 |
| S02d_app_not_foreground | 桌面 launcher | `app_start` 后再 snap |
| S03a_guest_login_half | AccountLoginHalfActivity | navigator 识别 Half |
| S03b_sms_verify | VerificationCodeActivity | 45s 轮询；勿 u2 init 打断 |
| S03c_guest_banner | `tv_login_guide_banner` | 批量前必登录 |
| S04a_input_rid | `id/input` 非 input_text | send_message fallback |
| S04b_send_button | content-desc=发送 | action_send xpath |
| S04c_mode_toggle | 深度思考菜单 | contains 匹配 |
| S04d_new_chat | 无「更多」 | 对话列表 + right_img |
| S04e_expert_mode_quota | 专家模式额度不足 / 今日额度 | 关闭弹窗后 `_open_new_conversation`；抽检用 fast 模式 |
| S05a_app_jump_prompt | AppJumpPrompt / appfilter | wait → accept；坐标兜底 |
| S05b_douyin_runtime_permission | PermissionActivity | _grant_douyin_runtime_permissions 循环 |
| S05c_douyin_login_wall | LoginActivity / 手机号登录 | ensure_douyin_logged_in（同号 SMS） |
| S05d_snssdk_deeplink | logcat snssdk intent | resolve_via_aweme_deeplink + android_id |
| S05e_thinking_panel_collapsed | 解析时 list=无 | prepare_citations_for_url_resolve |

新增机型阻塞：在 `SUBFLOW_RULES` 加 `(pattern, sub_id, hint)` 三元组，并同步 `ADAPT_LOG.md` 表。

## profile 常用字段（QA / 抖音）

```json
{
  "qa_shot_roi_y0": 0.12,
  "qa_shot_roi_y1": 0.88,
  "qa_shot_min_overlap_frac": 0.42,
  "qa_resolve_accept_app_jump": true,
  "qa_resolve_batch_douyin": true,
  "qa_douyin_deeplink_first": true,
  "qa_douyin_deeplink_schemes": ["snssdk1128", "snssdk1180"],
  "qa_douyin_handoff_timeout": 20,
  "qa_douyin_ensure_login_before_batch": true,
  "qa_resolve_batch_douyin_timeout": 12,
  "qa_resolve_url_max_backs": 5
}
```

屏幕参数：从 `adb shell wm size` 写入 profile；vivo 截图用 `screencap` + `pull`，避免 `exec-out` 花屏。

## navigator 抖音相关 API

- `is_app_jump_prompt()` — `AppJump` 或 `appfilter` 包
- `wait_and_accept_app_jump(timeout)` — 轮询直到弹窗再点
- `accept_app_jump_prompt()` — xpath → hierarchy 坐标 → 右侧典型坐标
- `wait_for_aweme_foreground(timeout)` — 含权限允许
- `recover_from_external_douyin(gentle=True)` — 优先 back，stuck 才 force-stop
- `reenter_chat_by_prompt(prompt)` — 对话列表按 prompt 前缀重进
- `hard_restart_app(reason)` — 会话漂移/WebActivity 残留

## qa_reference_urls 解析管线

```
ensure_douyin_logged_in（批次前一次）
prepare_citations_for_url_resolve
  → try_batch_resolve_douyin（深链/Handoff + feed 批量）
  → 技术逐条（深链优先 per click）
  → 笨办法逐条点击
  → 每条/批量后 _chat_context_ok + reenter_chat_by_prompt
```

Handoff / 深链（`douyin_handoff.py`）：

1. 点击后 logcat 抽 `aweme_id`
2. `am start` snssdk1128/1180 + `device_id=android_id`
3. `advance_handoff`：AppJump → Permission → LoginWall(SMS) → Feed
4. `recover_from_external_douyin(gentle=True)` + 会话校验

## 双机并行抽检

Honor + vivo 同时跑：

- 各项目独立 `SPOT_CHECK_SCREEN_WORKER`（如 `spotcheck_vivo_worker` / `spotcheck_estee_worker`）
- `run_unattended_spot_check.sh` 的 `start_worker` 用 `pgrep -f "...-s ${SERIAL}"` 判重
- **勿**对全局 `run_qa_spot_check.py` 做 `pkill`（会杀掉另一台）
- 强制全抖音链接：`SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS=0`（雅诗兰黛 `run_unattended.sh` 已设）

## 代码风格（与本仓库一致）

- 说明性注释、日志：**中文**
- 选择器：resource-id → text/content-desc → xpath contains；多策略顺序尝试
- 改动范围：只改阻塞路径，不顺手重构
- 测试：`.venv/bin/python -m pytest tests/test_*.py -q`

## 常见失败模式 → 对策

| 现象 | 对策 |
|------|------|
| 发送无反应 | S04a/b；检查是否在语音模式 |
| 验证码页卡死 | 杀豆包 + SMS 换号；JSON code 解析 |
| 引用点击无效 | 面板未展开 → prepare；bounds 过期 → 刷新 |
| AppJump 一直停 | wait_and_accept；坐标兜底；dump hierarchy |
| logcat ids=0 | 同号登录抖音；多滑 feed；延长 batch_timeout |
| 解析后会话错位 | `hard_restart_app`；`ensure_expected_chat` |
| 长图漏截 | 加大重叠阈值或减小滑动步长（profile） |
| 专家模式额度不足弹窗卡住 | 关闭弹窗 → 新建对话；日志常伴「创建新对话失败」「未获取到有效问答数据」；见下 |

### 现场案例：专家模式额度不足（2026-07-15 xfold6 多机抽检）

- **现象**：某台 vivo 停摆，屏上「专家模式额度不足」类对话框阻塞；手动返回并新建对话后恢复。
- **日志**：`spot_check_run.log` **未出现**弹窗原文；关联症状如下：
  - `[问答] 创建新对话失败: #(XPath('//*[@content-desc="更多"]'))`（弹窗挡住「更多」入口）
  - `[抽检] 失败 …: 未获取到有效问答数据`（约 21:38–21:41，`KPN653914…EC301/302/299`）
  - 同期 `10AE3B0DSU0063K` 有 `device not found`（拔线）与 `AdbBroadcastError` 清输入框失败
- **diagnose**：`bash run_adapt.sh diagnose S04_send_qa`，hierarchy 搜 `专家|额度不足`
- **恢复**：点「知道了」/关闭 → `_open_new_conversation` 或对话列表新建；批量抽检保持 `--mode fast`，避免误触专家/深度思考
- **待自动化**：`accept_blocking_prompts` 识别并关闭此类额度弹窗（正向按钮仅「知道了」，不点开通）
