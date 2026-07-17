# -*- coding: utf-8 -*-
"""
通过 SMS API 自动获取手机号和验证码，完成豆包登录。

API 文档：doc/获取可登录手机号和验证码.md
流程（单次尝试，重试/换号编排见 flow_crawler.handle_login_if_needed）：
  1. 调 /api/phone/get 获取可用手机号
  2. 在 AccountLoginActivity 勾选隐私协议 + 点「手机号登录」
  3. 在 PhoneLoginActivity 输入手机号 → 点「下一步」
  4. 等待短信到达（sms_wait_seconds）
  5. 调 /api/messages/latest 获取 6 位验证码（兼容纯文本与 JSON）
  6. 在 VerificationCodeActivity 输入验证码 → 校验跳转 ChatActivity

稳定性要点：
  - 验证码框（edit_solid）在部分机型 send_keys 无效，按 set_text / send_keys /
    keyevent / adb input 依次兜底，并以「是否跳出验证码页」作为成功判据。
  - 任一失败路径都会 report_phone_occupied，避免号池复用死号。
  - 手机号残留（旧号绑定验证码页）由上层杀后台换号处理。
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests

from app.modules.navigator import Navigator, Page

SMS_API_BASE = "https://sms.guangyinai.com"
SMS_PLATFORM = "doubao"

_DEFAULT_TOKEN = os.environ.get("SMS_API_TOKEN", "")


VERIFY_CODE_RID = "com.larus.nova:id/edit_solid"
VERIFY_ACTIVITY_KEY = "VerificationCodeActivity"


class SmsLoginError(RuntimeError):
    pass


def _on_verify_page(device: Any) -> bool:
    try:
        cur = device.app_current() or {}
    except Exception:
        return False
    return VERIFY_ACTIVITY_KEY in cur.get("activity", "")


def enter_verification_code(device: Any, code: str, nav: Optional[Navigator] = None) -> bool:
    """在 VerificationCodeActivity 输入 6 位验证码。

    多机型兼容：edit_solid 在 vivo 等机型 send_keys/set_text 常无效，按顺序兜底。
    成功判据（任一即可）：框内出现 6 位数字，或已跳出验证码页（自动提交）。
    """
    obj = device(resourceId=VERIFY_CODE_RID)
    if not obj.wait(timeout=8):
        print("[登录] 验证码页 edit_solid 未出现（可能已跳转）")
        return _left_verify_page(device, nav)

    try:
        device.set_input_ime(True)
    except Exception:
        pass

    obj.click()
    time.sleep(0.5)
    try:
        device.clear_text()
    except Exception:
        pass

    def _digits_ok() -> bool:
        try:
            text = (device(resourceId=VERIFY_CODE_RID).info.get("text") or "").strip()
        except Exception:
            return False
        return len([c for c in text if c.isdigit()]) >= 6

    for method, action in (
        ("set_text", lambda: device(resourceId=VERIFY_CODE_RID).set_text(code)),
        ("send_keys", lambda: device.send_keys(code)),
        ("keyevent", lambda: [device.shell(f"input keyevent {7 + int(ch)}") for ch in code]),
        ("adb_input", lambda: device.shell(f"input text {code}")),
    ):
        try:
            action()
        except Exception as exc:
            print(f"[登录] 验证码输入 {method} 失败: {exc}")
            continue
        time.sleep(1.0)
        # 自动提交后应离开验证码页
        if _left_verify_page(device, nav):
            print(f"[登录] 验证码已提交 ({method})")
            return True
        if _digits_ok():
            print(f"[登录] 验证码已填入 ({method})，等待提交")
            time.sleep(2.5)
            if _left_verify_page(device, nav):
                return True
        else:
            print(f"[登录] {method} 未生效，尝试下一种输入方式")

    # 兜底：可能已填入但未自动跳转，再等一会
    time.sleep(2.0)
    return _left_verify_page(device, nav)


def _left_verify_page(device: Any, nav: Optional[Navigator]) -> bool:
    """离开验证码页即视为提交成功（优先用 nav 判断是否到聊天页）。"""
    if nav is not None:
        try:
            if nav.is_chat() and not nav.is_guest_chat():
                return True
        except Exception:
            pass
    return not _on_verify_page(device)


class SmsApiClient:
    """SMS API 封装。"""

    def __init__(self, token: str = "", device_id: str = "default_device"):
        self.token = token or _DEFAULT_TOKEN
        self.device_id = device_id
        if not self.token:
            raise SmsLoginError(
                "SMS API Token 未设置。请设置环境变量 SMS_API_TOKEN 或传入 token 参数。"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def get_phone(self) -> str:
        """获取可用手机号（去掉 +86 前缀）。"""
        url = f"{SMS_API_BASE}/api/phone/get"
        params = {"device_id": self.device_id, "platform": SMS_PLATFORM}
        resp = requests.get(url, params=params, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            error = data.get("error", "unknown")
            raise SmsLoginError(f"获取手机号失败: {error}")
        phone = data["phoneNumber"]
        if phone.startswith("+86"):
            phone = phone[3:]
        return phone

    def _extract_code(self, resp: requests.Response) -> Optional[str]:
        """解析验证码：兼容纯文本与 JSON {\"code\":\"123456\"}。"""
        text = (resp.text or "").strip()
        if text.isdigit() and len(text) == 6:
            return text
        try:
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        for key in ("code", "message", "sms_code", "verification_code"):
            val = data.get(key)
            if isinstance(val, str) and val.isdigit() and len(val) == 6:
                return val
        return None

    def get_sms_code(self, phone: str, max_retries: int = 8, retry_interval: int = 8) -> str:
        """获取短信验证码（重试直到拿到 6 位数字）。"""
        key = f"{phone}_{SMS_PLATFORM}"
        url = f"{SMS_API_BASE}/api/messages/latest"
        params = {"key": key, "deviceId": self.device_id}
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, params=params, headers=self._headers(), timeout=15)
                if resp.status_code == 200:
                    code = self._extract_code(resp)
                    if code:
                        return code
                elif resp.status_code == 404:
                    pass
            except Exception:
                pass
            if attempt + 1 < max_retries:
                print(f"  [SMS] 验证码未到达，{retry_interval}s 后重试 ({attempt + 2}/{max_retries})")
                time.sleep(retry_interval)
        raise SmsLoginError(f"获取验证码超时（重试 {max_retries} 次）")

    def report_phone_occupied(self, phone: str) -> None:
        """通知 API 该手机号被占用/限速。"""
        url = f"{SMS_API_BASE}/api/phone/bid"
        body = {
            "deviceId": self.device_id,
            "phoneNumber": phone,
            "platform": SMS_PLATFORM,
        }
        try:
            requests.post(url, json=body, headers=self._headers(), timeout=10)
        except Exception:
            pass


def _select_phone_login(device: Any) -> None:
    """AccountLoginActivity：勾选隐私协议 + 点「手机号登录」。"""
    try:
        checkbox = device.xpath(
            '//*[@resource-id="com.larus.nova:id/select_privacy_circle_view"]'
        ).get(timeout=3)
        if checkbox and not checkbox.info.get("checked", False):
            checkbox.click()
            time.sleep(0.5)
            print("[登录] 已勾选隐私协议")
    except Exception:
        pass

    for sel in (
        '//*[@text="手机号登录"]',
        '//*[@resource-id="com.larus.nova:id/button_login"]',
    ):
        try:
            btns = device.xpath(sel).all()
        except Exception:
            btns = []
        for btn in btns or []:
            text = (btn.info.get("text") or "").strip()
            if "button_login" in sel and "手机号" not in text:
                continue
            btn.click()
            print("[登录] 已点击「手机号登录」")
            time.sleep(1.5)
            return


def _input_phone_and_next(device: Any, phone: str) -> bool:
    """PhoneLoginActivity：输入手机号 → 点「下一步」。"""
    try:
        phone_input = device.xpath(
            '//*[@resource-id="com.larus.nova:id/phone_number"]'
        ).get(timeout=3)
        if not phone_input:
            print("[登录] 未找到手机号输入框")
            return False
        phone_input.click()
        time.sleep(0.3)
        try:
            device.clear_text()
        except Exception:
            pass
        device.send_keys(phone)
        time.sleep(0.5)
        print("[登录] 已输入手机号")
    except Exception as e:
        print(f"[登录] 输入手机号失败: {e}")
        return False

    try:
        next_btn = device.xpath(
            '//*[@resource-id="com.larus.nova:id/button_login" and @text="下一步"]'
        ).get(timeout=2)
        if not next_btn:
            next_btn = device.xpath(
                '//*[@resource-id="com.larus.nova:id/button_login"]'
            ).get(timeout=2)
        if not next_btn:
            print("[登录] 未找到「下一步」按钮")
            return False
        next_btn.click()
        print("[登录] 已点击「下一步」")
        time.sleep(2)
        return True
    except Exception as e:
        print(f"[登录] 点击下一步失败: {e}")
        return False


def auto_login(
    device: Any,
    nav: Navigator,
    token: str = "",
    device_id: str = "default_device",
    sms_wait_seconds: int = 55,
) -> bool:
    """单次全自动登录豆包（需当前已在登录页）。

    返回 True 仅当最终落到「非游客」聊天页。失败时 report_phone_occupied，
    上层负责杀后台换号重试（旧手机号会残留在验证码页）。
    """
    page, _ = nav.current_page()
    if page == Page.CHAT and not nav.is_guest_chat():
        print("[登录] 已在聊天页，无需登录")
        return True
    if page != Page.LOGIN:
        print(f"[登录] 当前页面 {page.name}，非登录页")
        return False

    api = SmsApiClient(token=token, device_id=device_id)

    print("[登录] 正在获取可用手机号...")
    try:
        phone = api.get_phone()
    except (SmsLoginError, requests.RequestException) as e:
        print(f"[登录] 获取手机号失败: {e}")
        return False
    print(f"[登录] 获取到手机号: {phone[:3]}****{phone[-4:]}")

    _select_phone_login(device)

    page, _ = nav.current_page()
    if page != Page.LOGIN:
        print(f"[登录] 点击手机号登录后页面异常: {page.name}")
        api.report_phone_occupied(phone)
        return False

    if not _input_phone_and_next(device, phone):
        api.report_phone_occupied(phone)
        return False

    print(f"[登录] 等待短信验证码到达（{sms_wait_seconds}s）...")
    time.sleep(sms_wait_seconds)

    # 等待期间可能已自动填码并跳转 Chat
    if nav.is_chat() and not nav.is_guest_chat():
        print("[登录] 已进入聊天页（验证码或已自动填入）")
        return True

    print("[登录] 正在获取验证码...")
    try:
        code = api.get_sms_code(phone)
    except SmsLoginError as e:
        print(f"[登录] {e}")
        api.report_phone_occupied(phone)
        return False
    print(f"[登录] 获取到验证码: {code}")

    if not enter_verification_code(device, code, nav=nav):
        print("[登录] 验证码输入未能跳转")
        api.report_phone_occupied(phone)
        return False

    if nav.wait_for_page(Page.CHAT, timeout=15) and not nav.is_guest_chat():
        print("[登录] 登录成功！")
        return True

    print("[登录] 登录后未到达非游客聊天页")
    api.report_phone_occupied(phone)
    return False
