# PC Web 抖音多格式链接验证探针报告

- 时间: 20260716_105547
- 输出目录: `doc/reports/douyin_web_resolve/20260716_105547`
- overall_ok: **True**

## 结论摘要

- aweme_id 多格式级联 1/1 通过
- best_verified 优先 jingxuan modal_id → douyin.com/video → iesdouyin
- 格式矩阵见各 aweme_id 小节
- v.douyin.com 无法从 aweme_id 正向生成（仅反向展开可行）

## aweme_id 多格式级联（best_verified）

| aweme_id | verified | format_id | share_url | status | note |
|----------|----------|-----------|-----------|--------|------|
| `7639194080937700005` | True | douyin_jingxuan_modal | https://www.douyin.com/jingxuan?modal_id=763919408093770 | ok | douyin_jingxuan_modal desktop follow 通过 |

## 格式矩阵（单格式探测）

### `7639194080937700005`

| format_id | verified | http | note |
|-----------|----------|------|------|
| douyin_jingxuan_modal | True | 200 | douyin_jingxuan_modal desktop follow 通过 |
| douyin_video | True | 200 | douyin_video desktop follow 通过 |
| iesdouyin_share | True | 302 | iesdouyin_share desktop 302 通过 |
| iesdouyin_share_query | True | 302 | iesdouyin_share_query desktop 302 通过 |


## 深链 vs HTTP 对照

| 侧 | 模板 | 说明 |
|----|------|------|
| HTTP | `douyin_jingxuan_modal` | 抖音精选 modal_id |
| HTTP | `douyin_video` | douyin.com/video |
| HTTP | `iesdouyin_share` | iesdouyin share |
| HTTP | `iesdouyin_share_query` | iesdouyin share+query |
| 深链 | `snssdk1128://aweme/detail/{id}` | logcat / am start |
| 深链 | `snssdk1180://aweme/detail/{id}?device_id=` | 备用 scheme |

## v.douyin 短链反向展开

- 输入: `https://v.douyin.com/JPa1xhq/`
  - verified: True, aweme_id: `6883418578486349070`
  - share_url: https://www.douyin.com/jingxuan?modal_id=6883418578486349070
  - chain: https://v.douyin.com/JPa1xhq/ → https://www.iesdouyin.com/share/video/6883418578486349070/?app=aweme&mid=6883418927515421454&region=CN&titleType=title&u_code=0&utm_campaign=client_share&utm_medium=android&utm_source=copy_link

## 方案说明

1. **关键输入**：19 位 `aweme_id`（logcat / dumpsys / WebActivity）。
2. **HTTP 模板**：jingxuan modal_id → douyin.com/video → iesdouyin share。
3. **best_verified**：级联验证，首个通过的**原始 URL** 写入 Citation.url。
4. **短链**：`v.douyin.com` 仅反向展开，不能从 aweme_id 正向 HTTP 生成。
5. **profile**：`qa_douyin_web_url_formats` 控制启用格式顺序。
