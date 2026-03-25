# Frida Gadget + mitmproxy（无 Root）

## 依赖（本机）

- 仓库 **`.venv`**：`pip install -r requirements-frida.txt`
- `brew install apktool` + **JDK**（`JAVA_HOME`）
- **objection 需要 `aapt`**（Android build-tools）：`brew install --cask android-commandlinetools` 等；`gadget-patch` 会尝试把 Homebrew 下 build-tools 加入 `PATH`
- `npm install -g apk-mitm`（先做 apk-mitm，再在其产物上注入 Gadget）
- **mitmweb** 在 PATH（如 `brew install mitmproxy`）

## httptoolkit 脚本链：`light` / `light_plus`（默认）/ `full`

| 模式 | 行为 | 适用 |
|------|------|------|
| **light** | **无** native-connect-hook / proxy-override / 系统证书注入；仍有 native-tls-hook、unpinning 等 | 全局代理 + 用户 CA；豆包较稳 |
| **light_plus** | **light** + `android-system-certificate-injection.js` | 默认推荐 |
| **full** | 上游完整链（含 native-connect-hook） | 豆包易崩，慎用 |

- 改模式：仅编辑 **`capture/config/config.py`** → `httptoolkit_frida_script_mode`。
- **`BLOCK_HTTP3`**：由 **`httptoolkit_block_http3`** 控制（生成 `config.local.js` 时写入）。豆包建议 **`False`**；改配置后执行 **`python run_capture.py --only-config`** 重新生成。
- **附加脚本（推荐）**：`python run_capture.py frida`（仓库根执行，自动带齐 `-l`）
- 只打印等价长命令（调试用）：`python run_capture.py frida-cmd`（Gadget 端口：`--gadget-listen-port`）

## 日常抓包（推荐）

默认子命令即 **`capture-start`**，可直接：

```bash
python run_capture.py
```

等价于写 `config.local.js` → USB（reverse + **Gadget forward** + 推 CA + 全局代理）→ 尽量 **后台 mitmweb**（`logs/mitmweb.log`）→ 打印 **一行 Frida**。先**前台打开 Gadget 版豆包**，再在仓库根执行打印的命令。

- 只重写配置、不碰手机：`python run_capture.py --only-config`
- 已开 mitm：`python run_capture.py --no-mitmweb`
- CA/端口未变：`python run_capture.py --skip-httptoolkit-config`

结束：`python run_capture.py proxy-teardown`，并关掉 mitmweb。

## 首次准备顺序

1. `python run_capture.py patch`（或本地 `--apk …`）
2. `python run_capture.py gadget-patch --from-apk …/xxx-patched.apk --gadget-install`（大包可改 `push-install --install-apk …-gadget.apk`）
3. 之后每次抓包：`python run_capture.py` → 按提示装 CA（若尚未）→ 前台豆包 → **`python run_capture.py frida`**。

**重要**：`frida-ps -H 127.0.0.1:27042` 里进程名多为 **`Gadget`**，`-n Gadget`，勿用包名 spawn。

脚本集：**[frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning)**（**AGPL-3.0**），目录 `capture/scripts/httptoolkit_intercept/`。

## mitmweb 里「完全没有任何请求」

按顺序做，**做到哪一步仍无流就停在那一步排查**：

1. **本机**：`mitmweb` 是否在跑、监听端口与配置一致（默认 **8080**）；浏览器能打开 **`http://127.0.0.1:8081`**。
2. **手机**：`python run_capture.py` 后，系统 **HTTP 代理** 是否为 **`127.0.0.1:8080`**；`adb reverse tcp:8080 tcp:8080` 仍存在（未提前 `proxy-teardown`）。
3. **验证代理是否生效**：手机 **Chrome 打开任意 https 网站**，mitmweb Flow 里应出现记录。若 **浏览器也完全没有** → 问题在 **代理/reverse/本机 mitm**，与豆包、Frida 无关。
4. **私网 DNS**：`adb shell settings put global private_dns_mode off` 后重试。
5. **关掉其它 VPN / 第二代理**，避免抢路由。
6. **豆包**：须 **apk-mitm 改包**；若 Flow 里 **只有浏览器没有豆包**，再考虑 **Frida + `--only-config`**、TLS 失败等（见下节）。

若 **浏览器有流、豆包始终无流**：多为 **应用不走系统代理** 或 **证书/ pinning**；可试 **关 Frida 仅代理** 看是否有明文 HTTP，或确认已装 **用户 CA** 且 Frida 链为 **light_plus**。

### 基础项都确认过仍异常时（进阶）

1. **mitmweb 是否误过滤**：Flow 顶部 **Filter 清空**；换 **Events** 看是否有连接事件；必要时换 **终端跑 `mitmproxy`**（TUI）排除 Web UI 问题。
2. **本机 8080 是否真收到手机流量**：抓包会话时在电脑执行 `lsof -iTCP:8080 -sTCP:LISTEN`；另开终端 `nc -l 8080` **不要与 mitm 同时占用**，仅作思路——应保证 **只有 mitm 监听 8080**。
3. **代理是否被系统改掉**：操作豆包过程中执行 `adb shell settings get global http_proxy`，应稳定为 **`127.0.0.1:8080`**（或你的端口）。
4. **华为/多用户**：关闭 **网络加速 / 私人 DNS / 并行空间**；证书装在 **当前用户**；部分机型「WLAN 代理」与 `settings global http_proxy` 不一致时，到 **设置 → WLAN → 当前网络 → 代理** 手填一次 **127.0.0.1:8080** 与 `run_capture` 对照。
5. **Cronet 仍不走代理**：仅代理+改包仍无豆包流时，只能 **依赖 Frida**（当前 **light_plus**）；看 Frida 控制台是否报错、是否在 **豆包已在前台后** 才 attach；可对比 **另一款普通 OkHttp App** 在同代理下是否有流，以区分「整机无流」与「仅豆包无流」。
6. **仍无任何 TCP 进 mitm**：查电脑防火墙是否拦 **8080 入站**（来自 adb reverse 转发的来源）；换 USB 口/线、关手机「仅充电」。

## 安卓日志辅助排障（上不了网 / TLS）

在仓库根执行（**另开终端**，与 Frida 并行）：

```bash
python run_capture.py logcat
```

- 默认：**尝试只跟 `com.larus.nova` 的 pid**（须先**前台打开豆包**）；取不到 pid 时改为**全机日志 + 关键字过滤**（cronet、ssl、conscrypt、proxy、larus 等）。
- **`--logcat-clear`**：开始前 `adb logcat -c`。
- **`--logcat-output logs/net.log`**：同步写入文件。
- **`--logcat-all`**：不过滤（流量极大）。
- **`--logcat-dump`**：执行一次 `logcat -d` 后退出，默认写入 `logs/capture_logcat_dump.txt`。
- 若本机 adb 过旧不支持 `--pid`，加 **`--logcat-no-pid`**。

注意：`python run_capture.py frida` 中间**必须有空格**。

## 仅豆包无流、浏览器有流（场景 B）

说明 **全局代理 + reverse 已生效**；豆包侧多为 **不走系统代理或 TLS 校验**，需 **改包（apk-mitm）+ 用户 CA + Frida（light_plus）** 同时满足。

建议顺序：

1. **确认安装的是改包**（经 apk-mitm 的 APK），商店原版常 **完全不进 mitm**。
2. **用户 CA**：设置里 **VPN 和应用** 用途的 mitm 证书已安装；换证书后重跑 `python run_capture.py --only-config` 并重启豆包。
3. **Frida 顺序**：**先冷启动豆包并前台停留**，再在仓库根 **attach**（勿 `-f` spawn）；`frida-ps -H 127.0.0.1:27042` 里用 **`-n Gadget`**。无 Frida 时豆包 **零流** 可属正常。
4. **看 Frida 输出**：是否出现 **unpinning / SSL_CTX_set_custom_verify** 等；若脚本报错或进程崩，先保持 **light_plus**，勿贸然 **full**。
5. **mitm 里搜 TLS 失败**：过滤 **`certificate` / `handshake` / `Client TLS`**，若有 **豆包域名** 但失败，说明 **有连到代理但校验没过**，重点查 CA + Frida 是否生效。
6. **WLAN 代理**：华为等机型在 **设置 → WLAN → 当前网络 → 代理** 手填 **127.0.0.1:8080**，与 `settings get global http_proxy` 对照一致。
7. **仍无任豆包相关连接**：最后才考虑 **`httptoolkit_frida_script_mode: full`**（易闪退）；或接受 **UI/无障碍** 路线，不强依赖 HTTPS 明文。

## 浏览器正常、豆包提示网络异常

**8081** = 本机 mitmweb UI；手机代理 = **8080**（或 `--listen-port`）。

| 现象 | 建议 |
|------|------|
| Flow 里**没有**豆包 HTTPS | 改包/代理/私网 DNS；`adb shell settings put global private_dns_mode off` |
| **TLS / certificate** 失败 | 已 `--only-config`；挂 **light_plus** Frida；冷启动豆包 |
| Frida 报 **`Trust anchor for certification path not found`**、`TrustManagerImpl->verifyChain` | 已在链中加入 **`android-conscrypt-trustmanagerimpl-verifychain.js`**（`light_plus` / `light` / `full` 均会加载）；重附 Frida 后再试；仍失败则核对 **config.local.js 与当前 mitm CA 一致**（`python run_capture.py --only-config`） |
| Frida 已挂上仍整页网络错 | 查 **BLOCK_HTTP3**（改 `config.py` 后 `--only-config`）、mitm 是否运行、可先关 Frida 试 |

## 其它

- 许可证见 `capture/scripts/httptoolkit_intercept/LICENSE`。
- apk-mitm + objection 双签名可能影响服务端校验，自行权衡。
