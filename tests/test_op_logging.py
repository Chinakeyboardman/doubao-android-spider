# -*- coding: utf-8 -*-
"""操作日志时间戳。"""

from __future__ import annotations

import builtins
import logging

from app.utils import utils as u


def test_install_op_logging_prefixes_print(monkeypatch):
  captured: list[tuple] = []

  def fake_print(*args, **kwargs):
    captured.append(args)

  monkeypatch.setattr(builtins, "print", fake_print)
  u._OP_LOG_INSTALLED = False
  u._orig_print = fake_print

  u.install_op_logging()
  builtins.print("[问答] 测试操作")

  assert len(captured) == 1
  line = captured[0][0]
  assert isinstance(line, str)
  assert u._OP_TS_RE.match(line)
  assert "[问答] 测试操作" in line


def test_format_message_idempotent():
  u.reset_op_log_device()
  once = u.format_message("[URL] hit")
  twice = u.format_message(once)
  assert once == twice


def test_set_op_log_device_prefixes_print_and_logging(monkeypatch, caplog):
  captured: list[tuple] = []

  def fake_print(*args, **kwargs):
    captured.append(args)

  monkeypatch.setattr(builtins, "print", fake_print)
  u._OP_LOG_INSTALLED = False
  u._orig_print = fake_print
  u.reset_op_log_device()

  u.install_op_logging()
  u.set_op_log_device("10ADBY1Z7C0042Z")
  builtins.print("[问答] 测试操作")

  assert len(captured) == 1
  line = captured[0][0]
  assert "[SN=10ADBY1Z7C0042Z]" in line
  assert "[问答] 测试操作" in line

  with caplog.at_level(logging.INFO, logger=u.logger.name):
    u.log_info("认领成功")

  assert caplog.records
  assert "[SN=10ADBY1Z7C0042Z]" in caplog.records[0].message
  assert "认领成功" in caplog.records[0].message
