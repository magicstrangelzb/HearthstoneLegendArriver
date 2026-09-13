"""炉石存活检测：进程消失（闪退）与 Power.log 停滞（卡死）。

回归背景（issue 反馈）：挂机时炉石在换牌界面闪退，脚本完全没察觉，继续对失效
画面做 OCR，空转近 3 小时才被手动停止。修复后：
  * Hearthstone.exe 进程消失（本轮见过它才算“消失”）→ 判定退出（fatal）；
  * 对局中 Power.log 长时间无新增 → 先告警，更久则判定无响应（fatal）；
  * 主菜单/匹配阶段的日志本来就安静，绝不能因此误判。
"""

import os
import sys
import unittest

from src.safety.hearthstone_liveness import (
    HearthstoneLiveness, hearthstone_process_running, list_process_names)


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


BASE_CFG = {
    "enabled": True,
    "process_grace_seconds": 6.0,
    "log_stale_warn_seconds": 120.0,
    "log_stale_stop_seconds": 300.0,
}


class _MonitorFixture(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.running = True
        self.age = None
        self.cfg = dict(BASE_CFG)
        self.monitor = HearthstoneLiveness(
            process_checker=lambda: self.running,
            log_path_provider=lambda: "Power.log" if self.age is not None else None,
            settings_provider=lambda: dict(self.cfg),
            clock=self.clock,
            file_age=lambda _path: self.age,
            cache_seconds=0.0)


class ProcessLivenessTests(_MonitorFixture):
    def test_never_seen_process_is_not_a_crash(self):
        """脚本刚启动、炉石本来就没开：不能判定成闪退（脚本会自己去拉起炉石）。"""
        self.running = False

        self.assertIsNone(self.monitor.check())
        self.assertEqual("idle", self.monitor.sample()["status"])

    def test_process_gone_after_grace_is_fatal(self):
        self.assertIsNone(self.monitor.check())          # 见过进程

        self.running = False
        self.clock.advance(3)
        self.assertIsNone(self.monitor.check())          # 第一次发现“没了”只是开始计时
        self.clock.advance(3)
        self.assertIsNone(self.monitor.check())          # 宽限期（6s）内不判死

        self.clock.advance(4)
        event = self.monitor.check()
        self.assertIsNotNone(event)
        self.assertEqual("process_gone", event["kind"])
        self.assertTrue(event["fatal"])
        self.assertIn("炉石已退出", event["message"])
        self.assertEqual("gone", self.monitor.sample()["status"])

    def test_same_failure_is_reported_only_once(self):
        self.monitor.check()
        self.running = False
        self.clock.advance(30)
        self.assertIsNone(self.monitor.check())          # 开始计时
        self.clock.advance(10)

        self.assertIsNotNone(self.monitor.check())
        self.assertIsNone(self.monitor.check())   # 不刷屏

    def test_process_coming_back_before_grace_is_not_reported(self):
        self.monitor.check()
        self.running = False
        self.clock.advance(2)
        self.assertIsNone(self.monitor.check())

        self.running = True
        self.assertIsNone(self.monitor.check())
        self.clock.advance(600)

        self.assertIsNone(self.monitor.check())

    def test_unknown_process_state_is_not_fatal(self):
        """进程枚举失败（返回 None）时绝不能当成“已退出”。"""
        self.monitor = HearthstoneLiveness(
            process_checker=lambda: None,
            log_path_provider=lambda: None,
            settings_provider=lambda: dict(BASE_CFG),
            clock=self.clock, cache_seconds=0.0)

        self.assertIsNone(self.monitor.check())
        self.assertEqual("unknown", self.monitor.sample()["status"])

    def test_disabled_monitor_reports_nothing(self):
        self.cfg["enabled"] = False
        self.monitor.check()

        self.running = False
        self.clock.advance(600)

        self.assertIsNone(self.monitor.check())
        self.assertEqual("disabled", self.monitor.sample()["status"])

    def test_reset_forgets_the_previous_run(self):
        self.monitor.check()
        self.running = False
        self.clock.advance(1)
        self.monitor.check()
        self.clock.advance(60)
        self.assertIsNotNone(self.monitor.check())

        self.monitor.reset()

        self.assertIsNone(self.monitor.check())          # 新一轮不计旧账
        self.assertEqual("idle", self.monitor.sample()["status"])


class LogStalenessTests(_MonitorFixture):
    def test_fresh_log_is_ok(self):
        self.age = 5.0

        self.assertIsNone(self.monitor.check(in_game=True))
        self.assertEqual("ok", self.monitor.sample(in_game=True)["status"])

    def test_warn_then_stop(self):
        self.age = 130.0
        warn = self.monitor.check(in_game=True)
        self.assertIsNotNone(warn)
        self.assertFalse(warn["fatal"])
        self.assertIn("Power.log", warn["message"])
        self.assertEqual("warning", self.monitor.sample(in_game=True)["status"])
        self.assertIsNone(self.monitor.check(in_game=True))   # 同类告警只报一次

        self.age = 420.0
        stop = self.monitor.check(in_game=True)
        self.assertIsNotNone(stop)
        self.assertTrue(stop["fatal"])
        self.assertEqual("log_stale_stop", stop["kind"])
        self.assertEqual("stale", self.monitor.sample(in_game=True)["status"])

    def test_stale_log_outside_a_game_is_ignored(self):
        """匹配对手/主菜单阶段日志本来就安静，不能判定无响应。"""
        self.age = 900.0

        self.assertIsNone(self.monitor.check(in_game=False))
        self.assertEqual("ok", self.monitor.sample(in_game=False)["status"])

    def test_no_power_log_is_not_treated_as_stale(self):
        self.age = None

        self.assertIsNone(self.monitor.check(in_game=True))
        self.assertEqual("ok", self.monitor.sample(in_game=True)["status"])

    def test_context_is_appended_to_the_message(self):
        self.age = 400.0

        event = self.monitor.check(in_game=True, detail="连续 12 次推荐读取失败")

        self.assertIn("连续 12 次推荐读取失败", event["message"])

    def test_thresholds_come_from_config(self):
        self.cfg["log_stale_warn_seconds"] = 30.0
        self.cfg["log_stale_stop_seconds"] = 45.0
        self.age = 31.0

        self.assertFalse(self.monitor.check(in_game=True)["fatal"])

        self.age = 46.0
        self.assertTrue(self.monitor.check(in_game=True)["fatal"])


class NativeProcessTests(unittest.TestCase):
    """不注入替身，直接验证 ctypes 进程枚举真的能用（Windows）。"""

    def test_can_enumerate_processes(self):
        names = list_process_names()
        if names is None:
            self.skipTest("当前环境不支持进程枚举")
        self.assertTrue(names)

    def test_finds_this_python_process(self):
        names = list_process_names()
        if names is None:
            self.skipTest("当前环境不支持进程枚举")
        exe = os.path.basename(sys.executable).lower()
        self.assertIn(exe, names)

    def test_returns_bool_for_unknown_process(self):
        result = hearthstone_process_running(
            process_lister=lambda: ["notepad.exe"])
        self.assertIs(False, result)

        result = hearthstone_process_running(
            process_lister=lambda: ["HEARTHSTONE.EXE"])
        self.assertIs(True, result)

    def test_unreadable_process_list_is_unknown(self):
        self.assertIsNone(hearthstone_process_running(process_lister=lambda: None))
        self.assertIsNone(hearthstone_process_running(
            process_lister=lambda: (_ for _ in ()).throw(OSError("boom"))))


if __name__ == "__main__":
    unittest.main()
