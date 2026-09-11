"""浮窗「恢复」续跑不得清零胜负战绩（中止 → 恢复 → 战绩继续累计）。

回归背景：浮窗的「中止/恢复」是同一个按钮的开关；点「恢复」会走
api_start → _start_automation，而它原先无条件把 game_count / win_count /
concede_count 清零，导致一恢复战绩就归零。现在只有「开始对战」（重新开始）
才清零，「恢复」保留已累计战绩。
"""

import sys
import types
import unittest
from unittest.mock import patch

import web_ui


class _SyncThread:
    """把 _start_automation 起的后台线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def _fake_fsm(game_count, win_count, concede_count):
    """最小可用的 FSM_action 替身（不碰炉石、不点鼠标）。"""
    module = types.ModuleType("FSM_action")
    module.game_count = game_count
    module.win_count = win_count
    module.concede_count = concede_count
    module.quitting_flag = False
    module.stop_after_current_game = False
    module.FSM_state = ""
    module.time_begin = 0.0
    module.print_info_init = lambda: None
    module.init = lambda: None
    module.AutoHS_automata = lambda: None
    module.print_info_close = lambda: None
    return module


class OverlayHaltToggleTests(unittest.TestCase):
    """浮窗「中止/恢复」按钮的分支。"""

    def setUp(self):
        self.calls = []
        self._saved_thread = web_ui.CTRL.automation_thread

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved_thread

    def test_resume_asks_for_keep_score(self):
        def fake_start(body):
            self.calls.append(("start", body))
            return {"ok": True}

        def fake_stop(body):
            self.calls.append(("stop", body))
            return {"ok": True}

        with (
            patch.object(web_ui, "api_start", side_effect=fake_start),
            patch.object(web_ui, "api_stop", side_effect=fake_stop),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui._overlay_halt()

        self.assertEqual([("start", {"keep_score": True})], self.calls)

    def test_halt_still_stops_immediately(self):
        def fake_start(body):
            self.calls.append(("start", body))
            return {"ok": True}

        def fake_stop(body):
            self.calls.append(("stop", body))
            return {"ok": True}

        with (
            patch.object(web_ui, "api_start", side_effect=fake_start),
            patch.object(web_ui, "api_stop", side_effect=fake_stop),
        ):
            web_ui.CTRL.automation_thread = object()
            web_ui._overlay_halt()

        self.assertEqual([("stop", {"mode": "now"})], self.calls)


class StartAutomationScoreTests(unittest.TestCase):
    """_start_automation 的清零开关。"""

    def setUp(self):
        self._saved = (web_ui.CTRL.fsm, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.phase)

    def tearDown(self):
        (web_ui.CTRL.fsm, web_ui.CTRL.automation_thread,
         web_ui.CTRL.phase) = self._saved

    def _start(self, fake, reset_stats):
        with (
            patch.dict(sys.modules, {"FSM_action": fake}),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345",
                                       "log_root": "."}),
            patch.object(web_ui, "_apply_constants"),
            patch.object(web_ui, "_bind_overlay"),
            patch.object(web_ui, "_stdout_capture_start"),
            patch.object(web_ui, "_stdout_capture_stop"),
            patch.object(web_ui, "_register_hotkey"),
            patch.object(web_ui, "_remove_hotkey"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            return web_ui._start_automation(reset_stats=reset_stats)

    def test_resume_keeps_win_loss_counters(self):
        fake = _fake_fsm(5, 3, 1)

        ok, _msg = self._start(fake, reset_stats=False)

        self.assertTrue(ok)
        self.assertEqual((5, 3, 1), (fake.game_count, fake.win_count,
                                     fake.concede_count))

    def test_fresh_start_still_resets_counters(self):
        fake = _fake_fsm(5, 3, 1)

        ok, _msg = self._start(fake, reset_stats=True)

        self.assertTrue(ok)
        self.assertEqual((0, 0, 0), (fake.game_count, fake.win_count,
                                     fake.concede_count))

    def test_reset_score_zeroes_all_three_counters(self):
        fake = _fake_fsm(9, 4, 2)

        web_ui._reset_score(fake)

        self.assertEqual((0, 0, 0), (fake.game_count, fake.win_count,
                                     fake.concede_count))


if __name__ == "__main__":
    unittest.main()
