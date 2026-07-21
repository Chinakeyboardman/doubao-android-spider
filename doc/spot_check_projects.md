# 抽检项目运行指南（vivo-x-fold6 / 雅诗兰黛）

豆包 APP **签单提示词 → 抽检明细 CSV** 的无人值守批量采集。通用引擎在 `scripts/run_unattended_spot_check.sh`；**各项目包装脚本在 `var/<项目>/`（不入 git）**。

## 项目对照

| 项 | vivo-x-fold6 | 雅诗兰黛 |
|----|--------------|----------|
| 目录 | `var/vivo-x-fold6/` | `var/雅诗兰黛/` |
| 签单输入 | `签单提示词导出_20260710_183049.csv` | `签单提示词导出_20260714_000454.xlsx` |
| 条数 | 123 | 32 |
| 多机入口 | `run_multi.sh` | `run_multi.sh` |
| 单机入口 | `run_unattended.sh` | `run_unattended.sh` |
| screen worker | `spotcheck_vivo_worker` | `spotcheck_estee_worker` |
| 抖音 URL | `SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS=1` | **`=0`（必须全量）** |
| 机型 profile | `vivo_v2301a` / `honor_pct_al10` | 同上 |

## 在另一台机器跑（通用步骤）

1. **克隆仓库**并安装依赖（见根目录 `README.md` 快速开始）。
2. **同步 `var/<项目>/`**：签单文件必拷；若续跑则连同 `spot_check/<批次>/` 的 state、CSV、claims。
3. **配置 `.env`**：`SMS_API_TOKEN=...`（豆包 SMS 登录）。
4. **`adb devices`** 确认手机已授权；新机执行 `python -m uiautomator2 init`。
5. **编辑 `run_multi.sh`**：`SPOT_CHECK_SERIALS`、SMS 映射、批次目录。
6. **冒烟 1 条**再全量：

```bash
.venv/bin/python run_qa_spot_check.py \
  -s <serial> \
  --prompts-file var/雅诗兰黛/签单提示词导出_20260714_000454.xlsx \
  --out-csv var/雅诗兰黛/spot_check/<批次>/抽检明细_APP采集.csv \
  --state-file var/雅诗兰黛/spot_check/<批次>/spot_check_state.json \
  --out-dir var/雅诗兰黛/spot_check/<批次> \
  --pilot 1 --mode fast --strict
```

7. **启动无人值守**：

```bash
# 雅诗兰黛多机
bash var/雅诗兰黛/run_multi.sh start

# vivo-x-fold6 多机
bash var/vivo-x-fold6/run_multi.sh start
```

## 常用子命令

| 命令 | 作用 |
|------|------|
| `start` | 起全部 worker + monitor + watchdog |
| `stop` | 停本项目全部 worker（按 serial 精准，不杀别项目） |
| `restart` | 只重启 worker（monitor 保持） |
| `status` | 完成数 / claims / 各机 pid |
| `logs` | `tail -f` 主日志 |

## 多机协作（claims）

与 xfold6 相同机制（`app/modules/spot_check_claims.py`）：

- 共享 `spot_check/<批次>/claims/` 目录，按 `关键词编号` 原子认领
- 共享 `抽检明细_APP采集.csv`，`fcntl` 追加防写坏
- `detail_id` 确定性生成，避免 state 竞争
- 环境变量：`SPOT_CHECK_USE_CLAIMS=1`、`SPOT_CHECK_SERIALS="sn1 sn2 sn3"`

## 雅诗兰黛特有规范

1. **`SPOT_CHECK_ALLOW_PARTIAL_DOUYIN_URLS=0`**：每条引用的抖音链接必须解析成功，不可用批量跳过。
2. **签单为 xlsx**：`--prompts-file`（非 `--prompts-csv`）。
3. **冒烟必做**：先 `scripts/run_douyin_url_probe.py` 或 `--pilot 1 --strict`，确认抖音 handoff 通再 `run_multi.sh start`。
4. **SMS**：每台手机独立 `SPOT_CHECK_SMS_<SERIAL>`，须在短信平台绑定真实号码。

## 产出与断点

| 文件 | 说明 |
|------|------|
| `抽检明细_APP采集.csv` | 29 列业务明细，每关键词一行 |
| `spot_check_state.json` | 已完成 `关键词编号` → session 目录 |
| `spot_check_failures.jsonl` | 质量不达标记录 |
| `qa_capture/` | 单条完整问答归档（record.json、截图等） |

续跑：`run_multi.sh` / `run_unattended.sh` 默认带 `--resume --purge-incomplete`。

## 双项目并行（同一台 Mac 接多机）

Honor 跑雅诗兰黛 + 三台 vivo 跑 xfold6 时：

- 各用独立 `SPOT_CHECK_SCREEN_WORKER` 前缀（已隔离）
- **禁止** `pkill -f run_qa_spot_check.py`
- 分别 `bash var/雅诗兰黛/run_multi.sh stop` 与 `bash var/vivo-x-fold6/run_multi.sh stop`

## 相关文档

- [`doc/qa_capture.md`](qa_capture.md) — QA 采集字段、质量校验、profile 适配
- [`.cursor/skills/device-adaptation/`](.cursor/skills/device-adaptation/) — 新机适配 S01–S07
- 本地速查：`var/雅诗兰黛/README.md`、`var/vivo-x-fold6/run_multi.sh` 头部注释
