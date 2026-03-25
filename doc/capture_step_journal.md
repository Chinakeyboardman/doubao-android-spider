# 步骤执行记录

本文件由 **`python run_capture.py`**（APK / capture）与 **`python run.py`** / **`DoubaoSpider`**（爬虫）在各自步骤执行时自动追加；可在文末人工补充说明。

---

## 2026-03-23 18:16:44 CST

- **步骤**: 迁移验证
- **结果**: 成功
- **说明**: journal=/Users/chenjiawei/WWW/guangyingai/doubao-app-spider/doc/capture_step_journal.md

## 2026-03-23 18:24:56 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True

## 2026-03-23 18:34:27 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080

## 2026-03-23 18:51:33 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080

## 2026-03-23 19:33:46 CST

- **步骤**: 计划文档与 Frida 依赖
- **结果**: 成功
- **说明**: 更新 .cursor/plans/doubao_spider_analysis plan；.venv 安装 frida-tools 14.6.1 + objection 1.12.3；requirements-frida 约束 <15

## 2026-03-23 19:34:10 CST

- **步骤**: 本机 apktool
- **结果**: 成功
- **说明**: brew install apktool（objection patchapk 依赖）

## 2026-03-23 19:35:11 CST

- **步骤**: run_capture 命令行入口
- **结果**: 成功
- **说明**: argv=['patch', '--skip-install']

## 2026-03-23 19:35:11 CST

- **步骤**: 初始化工作目录
- **结果**: 成功
- **说明**: workspace=/Users/chenjiawei/WWW/guangyingai/doubao-app-spider/logs/captures/apk; package=com.larus.nova; pull_only=False; skip_install=True; skip_uninstall=False; source_apks=否

## 2026-03-23 19:35:11 CST

- **步骤**: 检查 adb
- **结果**: 成功
- **说明**: adb 在 PATH 中可用

## 2026-03-23 19:39:54 CST

- **步骤**: run_capture 命令行入口
- **结果**: 成功
- **说明**: argv=['patch', '--apk', 'logs/captures/apk/base.apk', '--skip-install']

## 2026-03-23 19:39:54 CST

- **步骤**: 初始化工作目录
- **结果**: 成功
- **说明**: workspace=/Users/chenjiawei/WWW/guangyingai/doubao-app-spider/logs/captures/apk; package=com.larus.nova; pull_only=False; skip_install=True; skip_uninstall=False; source_apks=是

## 2026-03-23 19:39:54 CST

- **步骤**: 检查 adb
- **结果**: 成功
- **说明**: adb 在 PATH 中可用

## 2026-03-23 19:39:54 CST

- **步骤**: 准备原版 APK（本地复制）
- **结果**: 成功
- **说明**: 共 1 个: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/logs/captures/apk/base.apk

## 2026-03-23 19:42:55 CST

- **步骤**: apk-mitm 重打包（去 SSL pinning）
- **结果**: 成功
- **说明**: base.apk → base-patched.apk

## 2026-03-23 19:42:55 CST

- **步骤**: 流程分支
- **结果**: 跳过
- **说明**: skip_install=True，未卸载/未安装；patched: base-patched.apk

## 2026-03-23 19:44:45 CST

- **步骤**: Frida Gadget 注入 APK
- **结果**: 失败
- **说明**: objection patchapk 失败 (code=2): Usage: objection patchapk [OPTIONS]
Try 'objection patchapk --help' for help.

Error: No such option: -o

## 2026-03-23 19:48:41 CST

- **步骤**: Frida Gadget 注入 APK
- **结果**: 失败
- **说明**: objection patchapk 失败 (code=2): Usage: objection patchapk [OPTIONS]
Try 'objection patchapk --help' for help.

Error: No such option: -o

## 2026-03-23 19:57:54 CST

- **步骤**: Frida Gadget 注入 APK
- **结果**: 失败
- **说明**: 未找到 objection 默认输出: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/logs/captures/apk/base-patched.objection.apk（若源路径含多个 .apk 请改用无歧义文件名）

## 2026-03-23 20:03:30 CST

- **步骤**: Frida Gadget 注入 APK
- **结果**: 成功
- **说明**: arch=arm64-v8a; gadget_port=27042; in=base-patched.apk; out=base-patched-gadget.apk

## 2026-03-23 20:05:30 CST

- **步骤**: Frida Gadget APK adb install
- **结果**: 失败
- **说明**: adb 在 120s 内未完成: adb uninstall com.larus.nova。
请检查：手机亮屏已解锁、USB 调试授权弹窗已点「允许」、`adb devices` 状态为 device（非 unauthorized/offline）。
可尝试：`adb kill-server && adb start-server`，换 USB 口/线，关闭手机「仅充电」模式。
若本机已有拉取的 APK，可跳过设备查询：`python run_capture.py patch --apk /path/to/base.apk --skip-install`。

## 2026-03-23 20:06:52 CST

- **步骤**: Frida Gadget 注入与安装
- **结果**: 部分完成
- **说明**: base-patched-gadget.apk 已生成；adb 安装因超时未完成，可手动 install

## 2026-03-23 20:20:00 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 失败
- **说明**: adb 在 30s 内未完成: adb shell settings put global http_proxy :0。
请检查：手机亮屏已解锁、USB 调试授权弹窗已点「允许」、`adb devices` 状态为 device（非 unauthorized/offline）。
可尝试：`adb kill-server && adb start-server`，换 USB 口/线，关闭手机「仅充电」模式。
若本机已有拉取的 APK，可跳过设备查询：`python run_capture.py patch --apk /path/to/base.apk --skip-install`。

## 2026-03-23 20:20:32 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080

## 2026-03-23 20:26:37 CST

- **步骤**: 人工补记 — gadget 版安装与 Step3b 状态
- **结果**: 进行中
- **说明**: 已安装 gadget 版豆包（`logs/captures/apk/base-patched-gadget.apk`，apk-mitm + objection）；计划 Step3b 注入/安装链路完成。本次抓包会话 **开始**: 2026-03-23 20:26 CST；**结束**: 待 Step4 首次抓包（mitmweb + 可选 frida）完成后补记一行。

## 2026-03-23 20:31:53 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True

## 2026-03-23 20:36:47 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True

## 2026-03-23 20:39:21 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True

## 2026-03-23 21:06:04 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080

## 2026-03-23 21:09:11 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-23 21:19:51 CST

- **步骤**: httptoolkit-config
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-23 21:20:50 CST

- **步骤**: httptoolkit-config
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-23 21:23:49 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-23 21:24:04 CST

- **步骤**: httptoolkit-config
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-23 21:24:05 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-23 21:35:06 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:35:51 CST

- **步骤**: httptoolkit-config
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-23 21:35:51 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:36:29 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-23 21:37:11 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:37:19 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:37:19 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:39:47 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light; gadget_port=27042

## 2026-03-23 21:41:50 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 00:28:41 CST

- **步骤**: frida-cmd
- **结果**: 成功
- **说明**: mode=light_plus; gadget_port=27042

## 2026-03-24 00:28:43 CST

- **步骤**: httptoolkit-config
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-24 00:28:52 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-24 00:38:26 CST

- **步骤**: Frida config.local.js
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-24 00:38:27 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-24 00:38:27 CST

- **步骤**: capture-start
- **结果**: 成功
- **说明**: mitm=8080; web_ui=8081

## 2026-03-24 00:39:45 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 00:39:46 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 00:39:55 CST

- **步骤**: Frida config.local.js
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-24 00:39:55 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-24 00:39:55 CST

- **步骤**: capture-start
- **结果**: 成功
- **说明**: mitm=8080; web_ui=8081

## 2026-03-24 01:01:05 CST

- **步骤**: frida attach
- **结果**: 启动
- **说明**: mode=light_plus; gadget_port=27042

## 2026-03-24 01:01:36 CST

- **步骤**: frida attach
- **结果**: 启动
- **说明**: mode=light_plus; gadget_port=27042

## 2026-03-24 01:02:47 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 01:02:52 CST

- **步骤**: Frida config.local.js
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-24 01:02:52 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-24 01:02:52 CST

- **步骤**: capture-start
- **结果**: 成功
- **说明**: mitm=8080; web_ui=8081

## 2026-03-24 01:03:00 CST

- **步骤**: frida attach
- **结果**: 启动
- **说明**: mode=light_plus; gadget_port=27042

## 2026-03-24 01:07:14 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 01:07:37 CST

- **步骤**: Frida config.local.js
- **结果**: 成功
- **说明**: /Users/chenjiawei/WWW/guangyingai/doubao-app-spider/capture/scripts/httptoolkit_intercept/config.local.js

## 2026-03-24 01:07:38 CST

- **步骤**: Step3 USB 抓包通道
- **结果**: 成功
- **说明**: listen_port=8080; push_cert=True; reverse=True; set_proxy=True; gadget_forward=True; gadget_listen_port=27042

## 2026-03-24 01:07:38 CST

- **步骤**: capture-start
- **结果**: 成功
- **说明**: mitm=8080; web_ui=8081

## 2026-03-24 01:07:43 CST

- **步骤**: frida attach
- **结果**: 启动
- **说明**: mode=light_plus; gadget_port=27042

## 2026-03-24 01:08:02 CST

- **步骤**: logcat
- **结果**: 开始
- **说明**: dump=False; pid=29575; file=logs/net_debug.log

## 2026-03-24 01:09:38 CST

- **步骤**: Step3 USB 抓包通道 teardown
- **结果**: 成功
- **说明**: listen_port=8080; remove_gadget_forward=True

## 2026-03-24 01:27:45 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 01:27:45 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 01:27:47 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 01:27:51 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 01:28:56 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 01:33:32 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 01:33:32 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 01:33:34 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 01:33:37 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 01:34:41 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 17:34:30 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:34:30 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:34:30 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 失败
- **说明**: start_app 返回 False

## 2026-03-24 17:34:30 CST

- **步骤**: 流程收尾
- **结果**: 成功
- **说明**: 调用 doubao_app.stop_app()

## 2026-03-24 17:34:30 CST

- **步骤**: run_full_crawl 结束
- **结果**: 失败
- **说明**: 返回 False

## 2026-03-24 17:44:01 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:44:01 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:44:03 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 17:44:06 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 17:45:09 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 17:51:01 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:51:01 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:51:03 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 17:51:06 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 17:51:44 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 17:52:30 CST

- **步骤**: 流程收尾
- **结果**: 成功
- **说明**: 调用 doubao_app.stop_app()

## 2026-03-24 17:58:11 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:58:11 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 17:58:13 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 17:58:16 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 17:59:22 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 17:59:57 CST

- **步骤**: 流程收尾
- **结果**: 成功
- **说明**: 调用 doubao_app.stop_app()

## 2026-03-24 18:45:31 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 18:45:31 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 18:45:33 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 18:45:35 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 18:46:42 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 18:49:17 CST

- **步骤**: 步骤4 抓取回复内容
- **结果**: 成功
- **说明**: text_path=logs/doubao_reply_20260324_184917.txt; rich_path=logs/doubao_reply_20260324_184917.md; 卡片数=4

## 2026-03-24 18:55:18 CST

- **步骤**: 流程收尾
- **结果**: 成功
- **说明**: 调用 doubao_app.stop_app()

## 2026-03-24 19:01:46 CST

- **步骤**: run_full_crawl 入口
- **结果**: 成功
- **说明**: list_visit_all=True; prompt 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 19:01:46 CST

- **步骤**: DoubaoSpider.run 开始
- **结果**: 成功
- **说明**: enable_post_process=True; enable_card_click=True; test_message 预览='请推荐2026年最好用的旗舰手机'

## 2026-03-24 19:01:48 CST

- **步骤**: 步骤1 打开豆包 APP
- **结果**: 成功

## 2026-03-24 19:01:50 CST

- **步骤**: 步骤2 识别聊天窗口
- **结果**: 成功

## 2026-03-24 19:02:56 CST

- **步骤**: 步骤3 输入并发送消息
- **结果**: 成功

## 2026-03-24 19:03:43 CST

- **步骤**: DoubaoSpider.run
- **结果**: 失败
- **说明**: ('Unknown RPC error: -32001 java.lang.SecurityException', (270, 970, 270, 1339, 60), 'java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission\n\tat android.os.Parcel.createException(Parcel.java:2091)\n\tat android.os.Parcel.readException(Parcel.java:2059)\n\tat android.os.Parcel.readException(Parcel.java:2007)\n\tat android.view.IWindowManager$Stub$Proxy.injectInputAfterTransactionsApplied(IWindowManager.java:4933)\n\tat android.app.UiAutomationConnection.injectInputEvent(UiAutomationConnection.java:131)\n\tat android.app.UiAutomation.injectInputEvent(UiAutomation.java:597)\n\tat androidx.test.uiautomator.InteractionController.injectEventSync(InteractionController.java:494)\n\tat androidx.test.uiautomator.InteractionController.touchMove(InteractionController.java:259)\n\tat androidx.test.uiautomator.InteractionController.swipe(InteractionController.java:334)\n\tat androidx.test.uiautomator.InteractionController.swipe(InteractionController.java:303)\n\tat androidx.test.uiautomator.UiDevice.swipe(UiDevice.java:631)\n\tat com.wetest.uia2.stub.AutomatorServiceImpl.swipe(AutomatorServiceImpl.java:228)\n\tat java.lang.reflect.Method.invoke(Native Method)\n\tat com.googlecode.jsonrpc4j.JsonRpcBasicServer.invoke(JsonRpcBasicServer.java:467)\n\tat com.googlecode.jsonrpc4j.JsonRpcBasicServer.handleObject(JsonRpcBasicServer.java:352)\n\tat com.googlecode.jsonrpc4j.JsonRpcBasicServer.handleJsonNodeRequest(JsonRpcBasicServer.java:283)\n\tat com.googlecode.jsonrpc4j.JsonRpcBasicServer.handleRequest(JsonRpcBasicServer.java:251)\n\tat com.wetest.uia2.stub.AutomatorHttpServer.serve(AutomatorHttpServer.java:101)\n\tat fi.iki.elonen.NanoHTTPD.serve(NanoHTTPD.java:2244)\n\tat fi.iki.elonen.NanoHTTPD$HTTPSession.execute(NanoHTTPD.java:945)\n\tat fi.iki.elonen.NanoHTTPD$ClientHandler.run(NanoHTTPD.java:192)\n\tat java.lang.Thread.run(Thread.java:932)\nCaused by: android.os.RemoteException: Remote stack trace:\n\tat com.android.server.input.InputManagerService.injectInputEventInternal(libmapleservices.so:5168324)\n\tat com.android.server.wm.WindowManagerService.injectInputAfterTransactionsApplied(libmapleservices.so:8309176)\n\tat android.view.IWindowManager$Stub.onTransact(libmapleframework.so:5274440)\n\tat com.android.server.wm.WindowManagerService.onTransact(libmapleservices.so:6749436)\n\tat com.android.server.wm.HwWindowManagerService.onTransact(libmaplehwServices.so:2340720)\ncallee: null 1613/2923\n\n')

## 2026-03-24 19:03:43 CST

- **步骤**: 流程收尾
- **结果**: 成功
- **说明**: 调用 doubao_app.stop_app()

## 2026-03-24 19:03:44 CST

- **步骤**: run_full_crawl 结束
- **结果**: 失败
- **说明**: 返回 False
