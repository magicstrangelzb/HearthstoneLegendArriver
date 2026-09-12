"""点「开始运行」只做准备（开浮窗 + 切炉石前台），绝不自动开始对战。"""

import types
import unittest
from unittest.mock import patch

import web_ui


class _SyncThread:
    """把 api_prepare 起的前台切换线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self._saved = (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.starting)

    def tearDown(self):
        (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
         web_ui.CTRL.starting) = self._saved

    def test_prepare_never_starts_automation(self):
        started, bound, foreground = [], [], []
        overlay = types.SimpleNamespace(is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)),
            patch.object(web_ui, "_bind_overlay", side_effect=lambda: bound.append(1)),
            patch.object(web_ui, "_bring_hearthstone_foreground",
                         side_effect=lambda: foreground.append(1)),
            patch.object(web_ui.threading, "Thread", _SyncThread),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.CTRL.prepared = False
            result = web_ui.api_prepare({})

        self.assertTrue(result["ok"])
        self.assertTrue(result["prepared"])
        self.assertEqual([], started)          # 关键：没有自动开始对战
        self.assertEqual(1, len(bound))        # 浮窗已开启
        self.assertEqual(1, len(foreground))   # 已切炉石前台
        self.assertTrue(web_ui.CTRL.prepared)

    def test_prepare_skips_overlay_when_already_open(self):
        bound = []
        overlay = types.SimpleNamespace(is_running=lambda: True)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "_bind_overlay", side_effect=lambda: bound.append(1)),
            patch.object(web_ui, "_bring_hearthstone_foreground"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.api_prepare({})

        self.assertEqual([], bound)

    def test_prepare_refused_while_running(self):
        web_ui.CTRL.automation_thread = object()

        result = web_ui.api_prepare({})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_prepare_refused_while_starting(self):
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = True

        result = web_ui.api_prepare({})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_status_exposes_prepared_flag(self):
        web_ui.CTRL.prepared = False
        self.assertFalse(web_ui.status_snapshot()["prepared"])
        web_ui.CTRL.prepared = True
        self.assertTrue(web_ui.status_snapshot()["prepared"])


class StartAfterPrepareTests(unittest.TestCase):
    """就绪之后再点「开始对战」仍然走原本的启动逻辑（不清零开关不变）。"""

    def setUp(self):
        self._saved = (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.starting)

    def tearDown(self):
        (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
         web_ui.CTRL.starting) = self._saved

    def test_start_after_prepare_moves_to_playing(self):
        fake = types.ModuleType("FSM_action")
        fake.game_count, fake.win_count, fake.concede_count = 5, 3, 1
        fake.quitting_flag = False
        fake.stop_after_current_game = False
        fake.FSM_state = ""
        fake.time_begin = 0.0
        fake.print_info_init = lambda: None
        fake.init = lambda: None
        fake.AutoHS_automata = lambda: None
        fake.print_info_close = lambda: None

        with (
            patch.dict("sys.modules", {"FSM_action": fake}),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345", "log_root": "."}),
            patch.object(web_ui, "_apply_constants"),
            patch.object(web_ui, "_bind_overlay"),
            patch.object(web_ui, "_stdout_capture_start"),
            patch.object(web_ui, "_stdout_capture_stop"),
            patch.object(web_ui, "_register_hotkey"),
            patch.object(web_ui, "_remove_hotkey"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.CTRL.prepared = True
            ok, _msg = web_ui._start_automation(reset_stats=True)

        self.assertTrue(ok)
        self.assertTrue(web_ui.CTRL.prepared)
        # 就绪后真正开打走的是原启动逻辑（这里 fake 线程瞬间跑完，
        # 所以最终回到 idle；关键是启动确实执行了且战绩按“重新开始”清零）
        self.assertEqual((0, 0, 0), (fake.game_count, fake.win_count,
                                     fake.concede_count))
        self.assertIn(web_ui.CTRL.phase, ("playing", "idle"))


if __name__ == "__main__":
    unittest.main()
