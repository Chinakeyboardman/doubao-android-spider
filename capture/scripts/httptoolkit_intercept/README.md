# HTTP Toolkit — Frida interception & unpinning（第三方，AGPL-3.0）

本目录脚本来自 **[httptoolkit/frida-interception-and-unpinning](https://github.com/httptoolkit/frida-interception-and-unpinning)**，用于 Android 上配合 mitmproxy 等做 **HTTPS 拦截、证书注入、绕过证书固定**（含 Cronet/复杂栈场景）。

- **许可证**：见同目录 `LICENSE`（**AGPL-3.0**）。若你分发包含这些脚本的衍生作品，须遵守 AGPL。
- **来源版本**：由仓库维护者从上游拷贝；升级时请对照上游 release 替换 `android/`、`native-*.js`、`config.template.js`。
- **本地配置**：勿编辑模板里的证书。在项目根执行：
  ```bash
  python run_capture.py --only-config
  ```
  会依据 `~/.mitmproxy/` 的 mitm CA 与 `capture/config` 中的 mitm 端口生成 **`config.local.js`**（已加入 `.gitignore`，勿提交）。

## 本仓库的 `light` / `light_plus` / `full` 加载链

默认 **`light_plus`**（`capture/config/config.py` → `httptoolkit_frida_script_mode`）：

- **不含** `native-connect-hook.js`（避免把所有 TCP 重定向到代理，**豆包/TTNet 易闪退**）
- **不含** `android-proxy-override.js`（与 **全局 HTTP 代理** 重复风险）
- **`light_plus`** 相对 **`light`** 会多加载 `android-system-certificate-injection.js`
- **含** `config.local.js`、`native-tls-hook.js`、unpinning、fallback、root 检测绕过等

**`BLOCK_HTTP3`**：由 `capture/config/config.py` 的 **`httptoolkit_block_http3`** 写入 `config.local.js`（默认 **`false`**，豆包更稳）；改后执行 `python run_capture.py --only-config`。

**`full`** 与上游 Android 推荐顺序一致（含 native-connect-hook）；仅在你明确需要且能接受 App 不稳定时使用。

本仓库在 upstream 链上额外增加 **`android/android-conscrypt-trustmanagerimpl-verifychain.js`**，缓解 `TrustManagerImpl.verifyChain` 抛「Trust anchor not found」而 fallback 无法自动 patch 的情况（见 Frida 控制台该报错时）。

打印当前一行 Frida 命令：

```bash
python run_capture.py frida
python run_capture.py frida-cmd
```

详见 **`doc/capture_frida_gadget.md`**。
