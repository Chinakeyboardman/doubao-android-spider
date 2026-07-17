# PC Web 抖音链接验证探针报告

- 时间: 20260715_180338
- 输出目录: `doc/reports/douyin_web_resolve/20260715_180338`
- overall_ok: **True**

## 结论摘要

- aweme_id 验证 4/4 通过
- desktop 302 → douyin.com/video/{id} 为推荐验证路径
- v.douyin.com 无法从 aweme_id 正向生成（仅反向展开可行）
- 验证通过后可固化到 qa_reference_urls（qa_douyin_web_validate）

## aweme_id → iesdouyin 验证

| aweme_id | verified | status | strategy | canonical | note |
|----------|----------|--------|----------|-----------|------|
| `7548775039182294330` | True | ok | desktop_redirect | https://www.douyin.com/video/7548775039182294330 | desktop 302 验证通过 |
| `7650085520299273595` | True | ok | desktop_redirect | https://www.douyin.com/video/7650085520299273595 | desktop 302 验证通过 |
| `7356065400192355620` | True | ok | desktop_redirect | https://www.douyin.com/video/7356065400192355620 | desktop 302 验证通过 |
| `6883418578486349070` | True | ok | desktop_redirect | https://www.douyin.com/video/6883418578486349070 | desktop 302 验证通过 |

## v.douyin 短链反向展开

- 输入: `https://v.douyin.com/JPa1xhq/`
  - verified: True, aweme_id: `6883418578486349070`
  - share_url: https://www.iesdouyin.com/share/video/6883418578486349070
  - chain: https://v.douyin.com/JPa1xhq/ → https://www.iesdouyin.com/share/video/6883418578486349070/?app=aweme&mid=6883418927515421454&region=CN&titleType=title&u_code=0&utm_campaign=client_share&utm_medium=android&utm_source=copy_link

## 方案说明

1. **关键输入**：19 位 `aweme_id`（来自手机 logcat / snssdk intent，非 device_id alone）。
2. **拼装**：`https://www.iesdouyin.com/share/video/{aweme_id}`（可选 `?did=`）。
3. **PC 验证**：desktop 302 → `douyin.com/video/{id}` 即视为视频存在。
4. **短链限制**：`v.douyin.com` 仅 App 分享时生成，**不能**从 aweme_id 正向 HTTP 生成；可反向展开校验。
5. **风控**：请求间隔 ≥0.8s，mobile UA + Referer；遇 captcha 标记 `captcha_suspected`。

## 主流程接入

- `qa_douyin_web_validate: true` 时，logcat 抽到 aweme_id 后 PC 验证再写 `Citation.url`。
- 验证通过可跳过手机端打开抖音 App（仍保留 handoff 作 fallback）。
