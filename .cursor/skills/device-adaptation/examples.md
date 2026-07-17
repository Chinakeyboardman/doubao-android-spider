# 机型适配案例：vivo V2301A（2026-07-15）

设备：`10ADBY1Z7C0042Z`，1260×2800，profile `vivo_v2301a.json`。

## 里程碑

| 阶段 | 结果 | 固化位置 |
|------|------|----------|
| S02 启动 | 隐私/更新/权限子流程 | `flow_crawler._dismiss_blocking_dialogs` |
| S03 登录 | 游客横幅 → SMS 188****5447 | `handle_login_if_needed` + `sms_login` |
| S04 问答 | input/发送/新建会话 fallback | `send_message`、`_open_new_conversation` |
| S05 抖音 | 探针 A 拿到 iesdouyin | `navigator` AppJump + `qa_reference_urls` 批量时序 |
| 抽检 | 雅诗兰黛 32 条，`ALLOW_PARTIAL=0` | `var/雅诗兰黛/run_unattended.sh` |

## 抖音探针成功样本

```
策略 A wait_accept_collect: ok=True
url=https://www.iesdouyin.com/share/video/7548775039182294330
```

报告目录：`var/新设备适配/vivo_v2301a/douyin_url_probe/20260715_171023/`

要点：`accepted_prompt=false` 时也可能经 WebActivity 再进抖音；关键是 `wait_for_aweme_foreground` + logcat 收 id。

## 命令复现

```bash
# 沙箱单步
bash var/新设备适配/vivo_v2301a/20260715/run_adapt.sh step 4
bash var/新设备适配/vivo_v2301a/20260715/run_adapt.sh diagnose S04_send_qa

# 抖音探针
set -a && source .env && set +a
.venv/bin/python scripts/run_douyin_url_probe.py -s 10ADBY1Z7C0042Z \
  --prompt "雅诗兰黛智妍面霜值得买吗？"

# 雅诗兰黛抽检（与 Honor 并行）
bash var/雅诗兰黛/run_unattended.sh start
bash var/雅诗兰黛/run_unattended.sh status
tail -f var/雅诗兰黛/spot_check/20260715/spot_check_run.log
```

## 教训摘要

1. **先 diagnose 再改码** — AppJump 按钮不在 u2 xpath 树里是常态。
2. **采集 ≠ 可解析** — `capturer.run()` 后面板收起，URL 阶段必须 `_prepare_panel` / `prepare_citations_for_url_resolve`。
3. **游客不能跑批量** — 必须 S03c 进真实登录。
4. **探针先于抽检** — 避免 32 条全失败才发现抖音路径不通。
