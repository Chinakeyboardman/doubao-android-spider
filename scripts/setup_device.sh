#!/usr/bin/env bash
# 新设备环境检查与 uiautomator2 初始化
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# adb：优先 ~/.local/bin，其次 PATH
export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v adb >/dev/null 2>&1; then
  echo "❌ 未找到 adb。请先安装 Android platform-tools 并加入 PATH。"
  echo "   macOS 可执行: curl -fsSL -o /tmp/pt.zip https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
  echo "                unzip -qo /tmp/pt.zip -d /tmp && cp /tmp/platform-tools/adb ~/.local/bin/"
  exit 1
fi

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  echo "❌ 未找到 .venv，请先在项目根目录执行:"
  echo "   python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

SERIAL="${1:-}"
if [[ -n "$SERIAL" ]]; then
  export ANDROID_SERIAL="$SERIAL"
fi

adb_cmd() {
  if [[ -n "$SERIAL" ]]; then
    adb -s "$SERIAL" "$@"
  else
    adb "$@"
  fi
}

echo "==> adb 设备列表"
adb devices -l
if ! adb_cmd get-state >/dev/null 2>&1; then
  echo ""
  echo "❌ 未检测到可用设备。请确认："
  echo "   1. 使用数据线（非仅充电线）连接手机与电脑"
  echo "   2. 手机已开启「开发者选项」→「USB 调试」"
  echo "   3. 手机上弹出「允许 USB 调试」时点允许"
  echo "   4. USB 连接模式选「文件传输 / MTP」"
  exit 1
fi

echo ""
echo "==> 设备信息"
adb_cmd shell getprop ro.product.brand
adb_cmd shell getprop ro.product.model
adb_cmd shell getprop ro.build.version.release

PKG="com.larus.nova"
if adb_cmd shell pm list packages "$PKG" | grep -q "$PKG"; then
  echo "✅ 已安装豆包 ($PKG)"
else
  echo "⚠️  未检测到豆包 ($PKG)，请先安装并登录豆包 APP"
fi

echo ""
echo "==> 初始化 uiautomator2（向手机安装 atx-agent）"
if [[ -n "$SERIAL" ]]; then
  "${ROOT}/.venv/bin/python" -m uiautomator2 init --serial "$SERIAL"
else
  "${ROOT}/.venv/bin/python" -m uiautomator2 init
fi

echo ""
echo "==> 连接测试"
"${ROOT}/.venv/bin/python" - <<'PY'
import uiautomator2 as u2
import os
serial = os.environ.get("ANDROID_SERIAL")
d = u2.connect(serial) if serial else u2.connect()
info = d.info or {}
print(f"✅ u2 已连接: {info.get('productName', '?')} "
      f"{info.get('displayWidth')}x{info.get('displayHeight')}")
PY

echo ""
echo "✅ 环境就绪。可运行:"
echo "   source .venv/bin/activate"
if [[ -n "$SERIAL" ]]; then
  echo "   python run_flow_crawl.py -s $SERIAL --max-cards 1 --max-products-per-card 1"
else
  echo "   python run_flow_crawl.py --max-cards 1 --max-products-per-card 1"
fi
