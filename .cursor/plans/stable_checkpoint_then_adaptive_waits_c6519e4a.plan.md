---
name: stable checkpoint then adaptive waits
overview: 先把当前已优化且稳定的代码提交、打 tag v0.1.0 并 push 作为安全检查点；随后在稳定性优先的前提下，仅将有明确 UI 就绪信号的"硬等待"改成带兜底超时的条件轮询，完成判定等完整性关键逻辑保持不变。
todos:
  - id: gitignore-var
    content: 在 .gitignore 增加 var/ 忽略运行产物
    status: completed
  - id: commit-source
    content: git add 源码并提交（复核 var/ 未被暂存）
    status: completed
  - id: tag-push
    content: 打 tag v0.1.0 并 push main 与 tag 到 origin
    status: completed
  - id: poll-helper
    content: 在 app/utils/utils.py 增加 poll_until 轮询助手
    status: completed
  - id: convert-newconv
    content: _open_new_conversation/_run_capture_body 新会话就绪改条件轮询（带兜底）
    status: completed
  - id: convert-mode
    content: _select_mode 开菜单/选中改条件轮询（带兜底）
    status: completed
  - id: convert-postreply
    content: wait_reply_done 后的 settle 改为轮询复制按钮就绪（带兜底）
    status: completed
  - id: verify
    content: 跑测试 + 重启 worker 核对计时与采集完整性，异常则回滚 v0.1.0
    status: completed
isProject: false
---

# 稳定检查点 + 保守的自适应等待

## 阶段 0：安全检查点（先做，风险最低）

worker 正在运行且往 `var/` 写数据；因 `var/` 将被忽略，提交不受影响，无需停 worker。

1. 编辑 [.gitignore](.gitignore)，新增忽略运行产物目录：
   - `var/`（当前 722MB 采集产物，不入库）
   - （`logs/`、`.venv/`、`*.apk`、`__pycache__/` 已忽略，无需重复）
2. `git add -A` 暂存源代码与配置（`app/`、`scripts/`、`tests/`、`run_*.py`、`doc/`、`requirements.txt`、`pytest.ini`、`app/config/profiles/*`、`capture/addons/` 等；`var/` 已被忽略不会进入）。
3. 提交：消息概述"QA 抽检长图拼接修复、无引用早退与检测合并提速、存储目录统一到 var/、分阶段计时"。
4. 打标签 `git tag v0.1.0`（标记当前"已优化且稳定"状态，供回滚）。
5. 推送：`git push origin main` 且 `git push origin v0.1.0`。

提交前会先 `git status` 复核，确认 `var/` 确实未被暂存（避免误传 722MB）。

## 阶段 1：把"硬等待"改成条件轮询（仅限有可靠就绪信号者）

核心原则（每处都遵守，保证不比现状差）：
- 轮询到"就绪信号"立即继续；若在兜底超时内未等到，则退回等待"与原固定 sleep 等长"的时间后照常继续（不改变原有失败兜底路径）。
- 兜底超时上限 >= 原 `sleep` 值；轮询间隔小（~0.15s）；必要处保留极小 settle。

### 新增通用助手
在 [app/utils/utils.py](app/utils/utils.py) 增加轻量函数：

```python
def poll_until(predicate, timeout, interval=0.15, settle=0.0) -> bool:
    """在 timeout 内轮询 predicate()，为真则(可选 settle 后)返回 True；超时返回 False。"""
```

两个模块复用它。

### 改造点（均带兜底，signal 不出现则退回原等待）
- [app/modules/qa_capture.py](app/modules/qa_capture.py) `_open_new_conversation()` 点击新建对话后的 `time.sleep(2.0)` → 轮询输入框 `input_text` 出现（新会话就绪），超时 2.5s 兜底。
- [app/modules/qa_capture.py](app/modules/qa_capture.py) `_run_capture_body()` 新建对话后的 `time.sleep(1.2)` → 与上面的输入框就绪判定合并/替代。
- [app/modules/qa_capture.py](app/modules/qa_capture.py) `_select_mode()`：开菜单后 `time.sleep(0.7)` → 轮询菜单项(`menu_text`/`tv_item_name`)出现；选中后 `time.sleep(0.6)` → 轮询菜单浮层消失。
- [app/modules/qa_capture.py](app/modules/qa_capture.py) `_run_capture_body()` `wait_reply_done` 之后的 `time.sleep(1.0)` → 轮询复制按钮 `msg_action_copy`/操作栏出现（即可进入采集），超时 1.5s 兜底。

### 明确不改动（完整性/稳定性关键）
- [app/modules/flow_crawler.py](app/modules/flow_crawler.py) `wait_reply_done()` 完成判定逻辑（"停止"消失 + 正文连续 3 次不变）——这是回答完整性的保障，保持原样。
- `start_app()`、`handle_login_if_needed()` 等启动/登录恢复循环（已是轮询，且涉及异常恢复）。
- 长截图静止判定循环（已自适应）。
- 不复用对话；不启用 `compressed=True`。

## 阶段 2：验证（稳定性回归）
1. `pytest`（至少 `tests/test_qa_capture_stitch.py`、`tests/test_qa_quality.py`、`tests/test_qa_spot_check_export.py`）全绿。
2. 重启 worker 跑 1-2 条，对比 `[计时]` 分阶段耗时，并人工核对：模式确实切到"快速"、回答正文/长截图完整、无截断或漏切模式。
3. 若出现任何不稳定迹象，直接 `git reset --hard v0.1.0` 回滚（检查点已在阶段 0 建立）。

预期收益：新会话+切模式+发送、以及回复后 settle 这几处减少若干秒固定等待，最坏情况与现状持平。