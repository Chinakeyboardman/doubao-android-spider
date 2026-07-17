# PC Web 抖音链接验证

本目录存放 `scripts/run_douyin_web_resolve_probe.py` 探针产出。

## 快速运行

```bash
.venv/bin/python scripts/run_douyin_web_resolve_probe.py
```

## 最新报告

见子目录 `20260715_180338/REPORT.md`（aweme_id 验证 4/4 通过，短链反向 1/1）。

## 方案要点

| 步骤 | 说明 |
|------|------|
| 输入 | 19 位 `aweme_id`（手机 logcat / snssdk intent） |
| 拼装 | `https://www.iesdouyin.com/share/video/{id}` |
| PC 验证 | desktop 302 → `douyin.com/video/{id}` |
| 短链 | `v.douyin.com` **仅可反向展开**，不能从 id 正向生成 |
| 主流程 | `qa_douyin_web_validate: true` → `_iesdouyin_url_verified` |

## 主流程开关

`app/config/profiles/vivo_v2301a.json`:

- `qa_douyin_web_validate`
- `qa_douyin_web_validate_interval`
- `qa_douyin_web_validate_fallback`

## 终端监控

```bash
bash var/雅诗兰黛/run_unattended.sh logs   # tail -f spot_check_run.log
bash var/雅诗兰黛/run_unattended.sh status
```
