#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""适配卡住时：解析 hierarchy + activity，输出 notes.md 与子流程建议。"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# 已知阻塞 → 建议子流程（随真机侦察追加）
SUBFLOW_RULES: list[tuple[str, str, str]] = [
    (
        r"bbk\.launcher2|com\.android\.launcher",
        "S02d_app_not_foreground",
        "豆包不在前台（桌面）→ app_start 或 monkey 拉起后再 snap",
    ),
    (
        r"confirm|欢迎使用豆包",
        "S02a_privacy_dialog",
        "隐私弹窗 → 点击 com.larus.nova:id/confirm「同意」",
    ),
    (
        r"tvDialogCancel|发现新版本",
        "S02b_update_dialog",
        "版本更新 → 点击 tvDialogCancel「忽略」",
    ),
    (
        r"permission_allow|GrantPermissions",
        "S02c_runtime_permission",
        "运行时权限 → permission_allow_button /「允许」",
    ),
    (
        r"AccountLoginHalfActivity|tv_login_guide_banner",
        "S03a_guest_login_half",
        "游客 Chat → Half 登录页；navigator 需识别 HalfActivity",
    ),
    (
        r"VerificationCodeActivity|edit_solid",
        "S03b_sms_verify",
        "SMS 验证码页 → 延长等待/避免 u2 装包打断 45s 窗口",
    ),
    (
        r'tv_login_guide_banner|立即登录',
        "S03c_guest_banner",
        "游客横幅 → 批量采集前必点 banner 进登录（游客约 10~20 条限额）",
    ),
    (
        r'resource-id="com\.larus\.nova:id/input"',
        "S04a_input_rid",
        "输入框 rid 为 input（非 input_text）→ send_message 已加 fallback",
    ),
    (
        r"AppJumpPrompt|appfilter|是否打开",
        "S05a_app_jump_prompt",
        "豆包跳转抖音弹窗 → wait_and_accept_app_jump / hierarchy 坐标兜底",
    ),
    (
        r"PermissionActivity|permissioncontroller|GrantPermissions",
        "S05b_douyin_runtime_permission",
        "抖音运行时权限 → _grant_douyin_runtime_permissions 循环点「允许」",
    ),
    (
        r"LoginActivity|手机号登录|验证码登录",
        "S05c_douyin_login_wall",
        "抖音登录墙 → douyin_sms_login.ensure_douyin_logged_in（同号 SMS）",
    ),
    (
        r"snssdk(?:1128|1180)://aweme/detail/",
        "S05d_snssdk_deeplink",
        "logcat 含 snssdk 深链 intent → resolve_via_aweme_deeplink + device_id",
    ),
    (
        r'content-desc="发送"|action_send',
        "S04b_send_button",
        "发送按钮 content-desc=发送 → xpath 用 @content-desc",
    ),
    (
        r'content-desc="深度思考',
        "S04c_mode_toggle",
        "模式入口 content-desc 含「深度思考，已关闭」→ contains 匹配",
    ),
    (
        r'content-desc="创建新对话"|对话列表',
        "S04d_new_chat",
        "新建对话 → 优先「对话列表」+ right_img，非「更多」菜单",
    ),
    (
        r"专家模式|额度不足|专家.*额度|今日.*额度",
        "S04e_expert_mode_quota",
        "专家模式额度不足弹窗 → 点「知道了」/关闭后新建对话（_open_new_conversation）；勿点开通/订阅",
    ),
    (
        r"ADB_KEYBOARD_CLEAR_TEXT|未找到发送按钮|input_text",
        "S04f_chat_input_send",
        "聊天输入/发送失败 → 新机先点 action_input「文本输入」切模式，再 input_text.set_text",
    ),
]


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _parse_nodes(xml_text: str, limit: int = 80) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not xml_text.strip():
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out

    def walk(node: ET.Element) -> None:
        if len(out) >= limit:
            return
        if node.tag == "node":
            rid = node.get("resource-id") or ""
            text = (node.get("text") or "").strip()
            desc = (node.get("content-desc") or "").strip()
            clickable = node.get("clickable") == "true"
            if rid or text or desc:
                if clickable or text or desc:
                    out.append(
                        {
                            "rid": rid[:120],
                            "text": text[:80],
                            "desc": desc[:80],
                            "clickable": str(clickable),
                        }
                    )
        for child in list(node):
            walk(child)

    walk(root)
    return out


def diagnose(step_dir: Path, step_label: str = "") -> str:
    activity = _read_text(step_dir / "activity.txt").strip()
    hierarchy = _read_text(step_dir / "hierarchy.xml")
    combined = f"{activity}\n{hierarchy}"
    nodes = _parse_nodes(hierarchy)

    matched: list[str] = []
    for pattern, sub_id, hint in SUBFLOW_RULES:
        if re.search(pattern, combined, re.I):
            matched.append(f"- **{sub_id}**: {hint}")

    lines = [
        f"# diagnose — {step_label or step_dir.name}",
        "",
        "## 证据",
        f"- screen: `{step_dir / 'screen.png'}`（vivo 请用 pull 截图，勿 exec-out）",
        f"- preview: `{step_dir / 'screen_preview.jpg'}`（缩略图，IDE 更易打开）",
        f"- hierarchy: `{step_dir / 'hierarchy.xml'}`",
        f"- activity: `{step_dir / 'activity.txt'}`",
        "",
        "## 屏幕文字摘要（无需看图）",
    ]
    texts = sorted(
        {
            (node.get("text") or "").strip()
            for node in _parse_nodes(hierarchy, limit=200)
            if (node.get("text") or "").strip()
        }
        | {
            (node.get("desc") or "").strip()
            for node in _parse_nodes(hierarchy, limit=200)
            if (node.get("desc") or "").strip()
        }
    )
    for t in texts[:25]:
        lines.append(f"- {t}")

    lines.extend(["", "## Activity", "```", activity or "(无 activity.txt)", "```", ""])

    lines.extend(["", "## 可交互节点（节选）"])
    for n in nodes[:40]:
        parts = [p for p in (n["rid"], n["text"], n["desc"]) if p]
        if parts:
            lines.append(f"- {' | '.join(parts)} (click={n['clickable']})")

    lines.extend(["", "## 建议子流程（规则匹配）"])
    if matched:
        lines.extend(matched)
    else:
        lines.append("- （无规则命中）请结合 **screen.png 视觉理解** 补充 notes.md")

    lines.extend(
        [
            "",
            "## 流程反思",
            "原主线若假设「Honor 已登录 Chat + input_text + 更多菜单」，vivo 新机需插入：",
            "1. 启动前：隐私 / 更新 / 权限 子流程",
            "2. 登录：HalfActivity + 游客横幅（SMS 惰性）",
            "3. 发送：input + content-desc 发送 + 对话列表新建会话",
            "",
            "## 视觉理解（人工/Agent 填写）",
            "- 看 screen.png：当前屏上阻塞是什么？应点哪里？",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="适配 diagnose")
    parser.add_argument("step_dir", type=Path, help="steps/Sxx_xxx 目录")
    parser.add_argument("--label", default="", help="步骤标签")
    args = parser.parse_args()
    text = diagnose(args.step_dir.resolve(), args.label)
    out = args.step_dir.resolve() / "notes.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[diagnose] 已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
