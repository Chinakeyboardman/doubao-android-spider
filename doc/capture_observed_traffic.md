# 抓包中常见请求备忘（非业务主链）

以下为 mitm 里常见的**配置/监控/地图 SDK** 流量，与「商品/聊天」等业务 API 不同；复现接口时可作环境指纹参考。

## 1. 字节系监控 / 配置（明文 JSON）

- **URL**：`POST https://mon.zijieapi.com/monitor_web/settings/hybrid-settings`
- **协议**：常见 HTTP/2
- **请求体**：JSON，字段含 `aid`、`device_id`、`version_code`、`sdk_version`、机型等。
- **响应**：常见 `errno: 200` 且 `errmsg` 为空表示 **成功**（字节系接口里 **200 常表示业务成功**，勿与 HTTP 状态码混淆）。
- **`data` 大意**：
  - **`bid_info`**：各业务线的 **Hybrid / Lynx / WebView 监控采样配置**（`hit_sample`、`event_name_sample` 等控制哪些埋点上报、采样率）；里面会出现 **`nova_482431`**、`doubao_z_project`、`flow_doubao_ruyi`、`pigeon_lynx`（含 IM 相关 monitor 名）等 **与豆包/容器相关的桶**，属 **可观测性配置**，不是聊天/商品的业务 JSON API。
  - **`host_list`**：Hybrid 场景下一大份 **域名白名单/放行列表**（字节系站点合集），体积大属正常。
- **和「豆包能不能用」的关系**：此接口 **成功** 只说明 **监控/Hybrid 策略拉取正常**；**失败** 时部分 **内嵌 H5/Lynx 页或埋点** 可能异常，但 **很多原生接口仍可工作**——是否卡死整 App 要看客户端实现，**不能单凭这一条断定主因**。
- **注意**：`device_id` 等请勿写入对外文档或截图。

## 2. 华为云侧指标（二进制体）

- **URL**：`POST https://metrics1-drcn.dt.dbankcloud.cn/common/hmshioperqrt`
- **体**：多为 **压缩后的二进制**（非 JSON）。你贴的内容以 `x\x9c` 开头，常见于 **zlib/raw deflate** 一类压缩载荷（具体以响应头 `Content-Encoding` / 实际 framing 为准）。
- **含义**：**华为云 / dbank** 方向的上报或运营类接口，路径名多为混淆缩写；业务含义需对照华为 SDK 文档或逆向，一般**不是豆包业务接口**。

## 3. 高德离线数据（gzip 二进制）

- **URL**：`POST http://offline.aps.amap.com/LoadOfflineData/repeatData`
- **体**：十六进制以 **`1f 8b`** 开头 → **gzip** 压缩；解压后多为 **Protobuf 或自定义二进制**，不是 UTF-8 文本。
- **含义**：**高德地图 SDK** 离线包/增量同步，与豆包业务通常无直接关系。

### 在 mitm 里显示「请求失败」时，一般是不是「关键」？

**多数情况下不是。** 走全局 HTTP 代理时，高德这类 **大包体、长连接、二进制同步** 容易 **超时、被上游重置、或返回非 2xx**；SDK 往往 **失败即重试或降级**，**不一定会让豆包主流程（聊天/推荐等）整页挂掉**。

更靠谱的判断方式：

1. 看 **豆包业务域名**（如含 `larus`、`volc`、`byte` 等，以你包为准）是否有 **大量 200**；若有，说明主链路透代理是通的。
2. 若 **只有** 地图/监控类失败、业务域正常 → 把 `repeatData` 当 **噪音** 即可。
3. 若 **业务域也 TLS 失败 / 无流** → 优先查 **Frida 链、CA、BLOCK_HTTP3、mitm 是否在跑**，而不是盯高德这一条。

mitmweb 里请展开该条看 **具体错误**（`502 Bad Gateway`、`Connection reset`、`timeout`、`TLS …` 等），不同提示对应代理/网络/SDK 检测，不能一概而论。

## 解压 / 粗看二进制体的做法

- mitmweb：看 **Request/Response** 的 **Hex**；若标了 `Content-Encoding: gzip`，部分版本可自动解。
- 本机：`xxd` / 导出 raw body 后 `gunzip` 或 Python `gzip.decompress` / `zlib.decompress` 试解（需去掉可能的自定义头）。
- 仍不可读：按 **Protobuf** 猜测字段需结合 `.proto` 或反编译。

## 与爬虫目标的关系

若要找 **商品、会话、搜索** 等接口，建议在 Flow 里按 **宿主域名**（如含 `larus`、`volc`、`doubao`、业务网关等，以你当前包为准）筛选，上述三类多为**噪音或基础设施**；**`offline.aps.amap.com` 失败可忽略**，除非你的目标就是地图 SDK 本身。
