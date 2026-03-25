# -*- coding: utf-8 -*-
"""
通过 SMS API 自动获取手机号和验证码，完成豆包登录。

API 文档：doc/获取可登录手机号和验证码.md
流程：
  1. 调 /api/phone/get 获取可用手机号
  2. 在 AccountLoginActivity 点「手机号登录」+ 勾选隐私协议
  3. 在 PhoneLoginActivity 输入手机号 → 点「下一步」
  4. 等待 45 秒（短信到达时间）
  5. 调 /api/messages/latest 获取 6 位验证码（重试 5 次，间隔 5 秒）
  6. 在 VerificationCodeActivity 输入验证码 → 自动跳转 ChatActivity
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


class SmsLoginError(RuntimeError):
    pass


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

    def get_sms_code(self, phone: str, max_retries: int = 5, retry_interval: int = 5) -> str:
        """获取短信验证码（重试直到拿到 6 位数字）。"""
        key = f"{phone}_{SMS_PLATFORM}"
        url = f"{SMS_API_BASE}/api/messages/latest"
        params = {"key": key, "deviceId": self.device_id}
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, params=params, headers=self._headers(), timeout=15)
                if resp.status_code == 200:
                    code = resp.text.strip()
                    if code.isdigit() and len(code) == 6:
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


def auto_login(
    device: Any,
    nav: Navigator,
    token: str = "",
    device_id: str = "default_device",
    sms_wait_seconds: int = 45,
) -> bool:
    """
    全自动登录豆包。如果当前不在登录页，直接返回 True。

    流程：
      AccountLoginActivity → 勾选隐私 + 点「手机号登录」
      PhoneLoginActivity → 输入手机号 → 点「下一步」
      等 sms_wait_seconds 秒
      VerificationCodeActivity → 输入验证码 → 等待跳转 ChatActivity
    """
    page, _ = nav.current_page()
    if page == Page.CHAT:
        print("[登录] 已在聊天页，无需登录")
        return True
    if page != Page.LOGIN:
        print(f"[登录] 当前页面 {page.name}，非登录页")
        return False

    api = SmsApiClient(token=token, device_id=device_id)

    # 1. 获取手机号
    print("[登录] 正在获取可用手机号...")
    try:
        phone = api.get_phone()
    except SmsLoginError as e:
        print(f"[登录] {e}")
        return False
    print(f"[登录] 获取到手机号: {phone[:3]}****{phone[-4:]}")

    # 2. AccountLoginActivity: 勾选隐私协议 + 点击「手机号登录」
    try:
        checkbox = device.xpath(
            '//*[@resource-id="com.larus.nova:id/select_privacy_circle_view"]'
        ).get(timeout=3)
        if checkbox:
            if not checkbox.info.get("checked", False):
                checkbox.click()
                time.sleep(0.5)
                print("[登录] 已勾选隐私协议")
    except Exception:
        pass

    try:
        btns = device.xpath(
            '//*[@resource-id="com.larus.nova:id/button_login"]'
        ).all()
        for btn in btns:
            text = (btn.info.get("text") or "").strip()
            if "手机号" in text:
                btn.click()
                print("[登录] 已点击「手机号登录」")
                time.sleep(1.5)
                break
    except Exception:
        pass

    # 3. PhoneLoginActivity: 输入手机号
    page, _ = nav.current_page()
    if page != Page.LOGIN:
        print(f"[登录] 点击后页面异常: {page.name}")
        return False

    try:
        phone_input = device.xpath(
            '//*[@resource-id="com.larus.nova:id/phone_number"]'
        ).get(timeout=3)
        if phone_input:
            phone_input.click()
            time.sleep(0.3)
            device.send_keys(phone)
            time.sleep(0.5)
            print(f"[登录] 已输入手机号")
    except Exception as e:
        print(f"[登录] 输入手机号失败: {e}")
        api.report_phone_occupied(phone)
        return False

    try:
        next_btn = device.xpath(
            '//*[@resource-id="com.larus.nova:id/button_login" and @text="下一步"]'
        ).get(timeout=2)
        if not next_btn:
            next_btn = device.xpath(
                '//*[@resource-id="com.larus.nova:id/button_login"]'
            ).get(timeout=2)
        if next_btn:
            next_btn.click()
            print("[登录] 已点击「下一步」")
            time.sleep(2)
    except Exception as e:
        print(f"[登录] 点击下一步失败: {e}")
        return False

    # 4. 等待短信到达
    print(f"[登录] 等待短信验证码到达（{sms_wait_seconds}s）...")
    time.sleep(sms_wait_seconds)

    # 5. 获取验证码
    print("[登录] 正在获取验证码...")
    try:
        code = api.get_sms_code(phone)
    except SmsLoginError as e:
        print(f"[登录] {e}")
        api.report_phone_occupied(phone)
        return False
    print(f"[登录] 获取到验证码: {code}")

    # 6. VerificationCodeActivity: 输入验证码
    try:
        code_input = device.xpath(
            '//*[@resource-id="com.larus.nova:id/edit_solid"]'
        ).get(timeout=5)
        if code_input:
            code_input.click()
            time.sleep(0.3)
            device.send_keys(code)
            print("[登录] 已输入验证码")
            time.sleep(3)
    except Exception as e:
        print(f"[登录] 输入验证码失败: {e}")
        return False

    # 7. 等待跳转到聊天页
    if nav.wait_for_page(Page.CHAT, timeout=15):
        print("[登录] 登录成功！")
        return True

    print("[登录] 登录后未到达聊天页")
    return False
