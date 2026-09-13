"""状态机存活检测接线：判定炉石退出/无响应后要醒目告警并自动停止。

回归背景（issue 反馈）：炉石闪退后脚本察觉不到，对失效画面空转近 3 小时。
所以除了检测本身（见 test_hearthstone_liveness.py），这里验证状态机这一侧的
行为：什么时候检查、判定后是否真的停下来、告警是否够醒目。
"""

import unittest
from unittest.mock import patch

import FSM_action


class _FakeMonitor:
    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def check(self, in_game=False, detail=""):
        self.calls.append({"in_game": in_game, "detail": detail})
        return self.events.pop(0) if self.events else None


class LivenessStopTests(unittest.TestCase):
    def setUp(self):
        self._saved = (FSM_action.quitting_flag, FSM_action._liveness_alert,
                       FSM_action.FSM_state, FSM_action._ocr_fail_streak,
                       FSM_action.last_automation_diagnostic)
        FSM_action.quitting_flag = False
        FSM_action._liveness_alert = None
        FSM_action._ocr_fail_streak = 0
        FSM_action.last_automation_diagnostic = None
        FSM_action.shutdown_event.clear()

    def tearDown(self):
        (FSM_action.quitting_flag, FSM_action._liveness_alert,
         FSM_action.FSM_state, FSM_action._ocr_fail_streak,
         FSM_action.last_automation_diagnostic) = self._saved
        FSM_action.shutdown_event.clear()

    def _run(self, events, state="Battling"):
        monitor = _FakeMonitor(events)
        logs = []
        FSM_action.FSM_state = state
        with (
            patch.object(FSM_action, "hearthstone_liveness", monitor),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=logs.append),
            patch.object(FSM_action, "error_print", side_effect=logs.append),
        ):
            event = FSM_action.check_hearthstone_liveness()
        return event, monitor, logs

    def test_fatal_event_marks_quitting_and_alerts(self):
        event, _monitor, logs = self._run([
            {"kind": "process_gone", "fatal": True,
             "message": "炉石已退出：Hearthstone.exe 进程消失（已确认 7s 未回来）"}])

        self.assertTrue(FSM_action.quitting_flag)
        self.assertTrue(FSM_action.shutdown_event.is_set())
        self.assertIsNotNone(event)
        self.assertTrue(any("⚠️" in m for m in logs))
        self.assertTrue(any("自动化已自动停止" in m for m in logs))
        # 告警要能被网页横幅读到（停止之后用户回来第一眼就能看见）
        self.assertIn("炉石已退出", FSM_action._liveness_alert)

    def test_warning_event_does_not_stop(self):
        _event, _monitor, logs = self._run([
            {"kind": "log_stale_warn", "fatal": False,
             "message": "炉石疑似无响应：对局中 Power.log 已 130s 没有新内容"}])

        self.assertFalse(FSM_action.quitting_flag)
        self.assertFalse(FSM_action.shutdown_event.is_set())
        self.assertTrue(any("Power.log" in m for m in logs))
        self.assertIsNone(FSM_action._liveness_alert)

    def test_in_game_state_is_passed_to_the_monitor(self):
        for state, expected in (("Battling", True), ("Choosing Card", True),
                                ("Quitting Battle", True), ("Main Menu", False),
                                ("Match Opponent", False), ("", False)):
            _event, monitor, _logs = self._run([], state=state)
            self.assertEqual(expected, monitor.calls[-1]["in_game"], state)

    def test_detail_carries_the_ocr_failure_context(self):
        """issue 建议“辅以 OCR 连续失败计数防误判”，这里把它作为告警旁证。"""
        FSM_action.FSM_state = "Battling"
        FSM_action._ocr_fail_streak = 12
        FSM_action.last_automation_diagnostic = "retry:recommendation_not_stable"

        _event, monitor, _logs = self._run([])

        detail = monitor.calls[-1]["detail"]
        self.assertIn("连续 12 次推荐读取失败", detail)
        self.assertIn("recommendation_not_stable", detail)

    def test_monitor_exception_never_breaks_the_loop(self):
        class _Boom:
            def check(self, in_game=False, detail=""):
                raise RuntimeError("boom")

        FSM_action.FSM_state = "Battling"
        with patch.object(FSM_action, "hearthstone_liveness", _Boom()):
            self.assertIsNone(FSM_action.check_hearthstone_liveness())
        self.assertFalse(FSM_action.quitting_flag)

    def test_state_snapshot_exposes_alert_and_in_game(self):
        class _Monitor:
            def sample(self, in_game=False):
                return {"enabled": True, "status": "gone", "log_age": None,
                        "alert": "炉石已退出", "in_game": in_game}

        FSM_action.FSM_state = "Battling"
        with patch.object(FSM_action, "hearthstone_liveness", _Monitor()):
            state = FSM_action.hearthstone_liveness_state()

        self.assertEqual("gone", state["status"])
        self.assertTrue(state["in_game"])
        self.assertEqual("炉石已退出", state["alert"])


class LoopWiringTests(unittest.TestCase):
    """主循环与对局循环都要调用存活检测（否则闪退还是会空转）。"""

    def test_main_loop_and_battling_call_the_check(self):
        import inspect

        source = inspect.getsource(FSM_action.AutoHS_automata)
        self.assertIn("check_hearthstone_liveness()", source)
        battling = inspect.getsource(FSM_action.Battling)
        self.assertIn("check_hearthstone_liveness()", battling)

    def test_init_resets_the_monitor(self):
        calls = []

        class _Monitor:
            def reset(self):
                calls.append(1)

        with (
            patch.object(FSM_action, "hearthstone_liveness", _Monitor()),
            patch.object(FSM_action, "initialize_recommendation_automation"),
            patch.object(FSM_action.click, "center_mouse"),
            patch.object(FSM_action, "log_iter_func", lambda *_a, **_k: iter(())),
        ):
            FSM_action.init()

        self.assertEqual([1], calls)


if __name__ == "__main__":
    unittest.main()
