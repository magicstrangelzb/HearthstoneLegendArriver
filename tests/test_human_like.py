"""活人感（可选）：操作后 0.5~3s 随机延时，期间鼠标在手牌区随机悬停（每处 1s）。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import click as hearthstone_click
import config


class RecordingMouse:
    """只记录事件，不真的动系统鼠标。"""

    def __init__(self):
        self.events = []

    @property
    def position(self):
        return None

    @position.setter
    def position(self, value):
        self.events.append(("position", value))

    def press(self, button):
        self.events.append(("press", button))

    def release(self, button):
        self.events.append(("release", button))

    @property
    def moves(self):
        return [v for kind, v in self.events if kind == "position"]


class FakeClock:
    """把 time.sleep / time.monotonic 换成可控时钟，测试不用真的等秒。"""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


class HumanLikeSettingsTests(unittest.TestCase):
    def _with_config(self, payload):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "ui_config.json"
        if payload is not None:
            path.write_text(json.dumps(payload), encoding="utf8")
        return tmp, path

    def test_defaults_when_section_missing(self):
        tmp, path = self._with_config({"name": "x"})
        try:
            with patch.object(config, "CONFIG_PATH", path):
                settings = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertEqual(config.DEFAULT_HUMAN_LIKE, settings)

    def test_reads_saved_values(self):
        tmp, path = self._with_config({"human_like": {
            "enabled": True, "post_delay_min": 0.8,
            "post_delay_max": 2.5, "hover_min": 0.3, "hover_max": 0.9}})
        try:
            with patch.object(config, "CONFIG_PATH", path):
                settings = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertTrue(settings["enabled"])
        self.assertEqual(0.8, settings["post_delay_min"])
        self.assertEqual(2.5, settings["post_delay_max"])
        self.assertEqual(0.3, settings["hover_min"])
        self.assertEqual(0.9, settings["hover_max"])

    def test_clamps_bad_numbers(self):
        tmp, path = self._with_config({"human_like": {
            "enabled": 1, "post_delay_min": -5,
            "post_delay_max": -1, "hover_min": -3, "hover_max": 0}})
        try:
            with patch.object(config, "CONFIG_PATH", path):
                settings = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertTrue(settings["enabled"])
        self.assertEqual(0.0, settings["post_delay_min"])
        self.assertEqual(0.0, settings["post_delay_max"])   # 上限不低于下限
        self.assertEqual(0.05, settings["hover_min"])   # 悬停下限不为 0
        self.assertEqual(0.05, settings["hover_max"])   # 上限不低于下限

    def test_bad_types_fall_back_to_defaults(self):
        tmp, path = self._with_config({"human_like": {
            "enabled": True, "post_delay_min": "很快"}})
        try:
            with patch.object(config, "CONFIG_PATH", path):
                settings = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertEqual(config.DEFAULT_HUMAN_LIKE, settings)


class HumanLikePauseTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mouse = RecordingMouse()
        self.printed = []
        self._patches = [
            patch.object(hearthstone_click, "Controller", return_value=self.mouse),
            patch.object(hearthstone_click.time, "sleep", self.clock.sleep),
            patch.object(hearthstone_click.time, "monotonic", self.clock.monotonic),
            patch.object(hearthstone_click, "sys_print",
                         side_effect=lambda line: self.printed.append(line)),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def _settings(self, lo=0.5, hi=3.0, h_lo=0.2, h_hi=1.0):
        return {"enabled": True, "post_delay_min": lo,
                "post_delay_max": hi, "hover_min": h_lo, "hover_max": h_hi}

    @staticmethod
    def _valid_hand_x():
        xs = set()
        for size in range(3, 9):
            xs.update(hearthstone_click.HAND_CARD_X[size])
        return xs

    def test_random_pause_and_hovers(self):
        # uniform 依次被调用：先取总延时，再为每处悬停取一次随机时长
        with patch.object(hearthstone_click.random, "uniform",
                          side_effect=[3.0, 1.0, 1.0, 1.0]):
            total = hearthstone_click.human_like_pause(self.mouse, self._settings())

        self.assertEqual(3.0, total)
        moves = self.mouse.moves
        # 3 处悬停 + 最后复位 1 次
        self.assertEqual(4, len(moves))
        self.assertEqual(hearthstone_click.MOUSE_RESET_POS, moves[-1])
        valid_x = self._valid_hand_x()
        for point in moves[:-1]:
            x, y = point
            self.assertEqual(hearthstone_click.HAND_HOVER_Y, y)
            self.assertIn(x, valid_x)
        # 每次悬停 1s（总时长刚好 3s）
        self.assertEqual([1.0, 1.0, 1.0], self.clock.slept)
        # 只移动，绝不点击
        self.assertEqual([], [e for e in self.mouse.events if e[0] != "position"])
        # 第一行是延时行（浮窗进度条靠它驱动），第二行是收尾标记（清空进度条）
        self.assertEqual(2, len(self.printed))
        self.assertIn("活人感 延时 3.0s 后", self.printed[0])
        self.assertIn("延时结束", self.printed[1])

    def test_short_pause_hovers_once(self):
        with patch.object(hearthstone_click.random, "uniform",
                          side_effect=[0.5, 1.0]):
            total = hearthstone_click.human_like_pause(self.mouse, self._settings())

        self.assertEqual(0.5, total)
        moves = self.mouse.moves
        self.assertEqual(2, len(moves))                       # 1 处悬停 + 复位
        self.assertEqual(hearthstone_click.MOUSE_RESET_POS, moves[-1])
        self.assertEqual([0.5], self.clock.slept)             # 只停到延时结束

    def test_hover_duration_is_random_per_hover(self):
        seq = [3.0, 0.2, 0.9, 0.5, 0.7, 0.3, 0.6, 0.4]
        with patch.object(hearthstone_click.random, "uniform", side_effect=seq):
            hearthstone_click.human_like_pause(self.mouse, self._settings())

        sleeps = self.clock.slept
        self.assertGreaterEqual(len(sleeps), 2)
        self.assertGreater(len(set(sleeps)), 1,
                           f"每处悬停时长应各自随机：{sleeps}")
        for value in sleeps:
            self.assertLessEqual(value, 1.0)      # 不超过设置的上限
        self.assertAlmostEqual(3.0, self.clock.now, places=3)

    def test_successive_hovers_prefer_different_slots(self):
        with patch.object(hearthstone_click.random, "uniform",
                          side_effect=[3.0, 1.0, 1.0, 1.0]):
            hearthstone_click.human_like_pause(self.mouse, self._settings())

        hovers = self.mouse.moves[:-1]
        for before, after in zip(hovers, hovers[1:]):
            self.assertNotEqual(before, after)


class ParkMouseTests(unittest.TestCase):
    """复位本身永远走“0.1s + 复位到左上角”，活人感不再挂在这里。

    否则匹配对手、选卡组、错误弹窗取消这类非推荐动作也会被拖慢，
    而活人感只应对局中识别盒子意见并执行完之后生效。
    """

    def test_park_is_plain_even_when_human_like_enabled(self):
        mouse = RecordingMouse()
        called = []

        with (
            patch.object(hearthstone_click, "human_like_settings",
                         return_value={"enabled": True, "post_delay_min": 0.5,
                                       "post_delay_max": 3.0,
                                       "hover_min": 0.2, "hover_max": 1.0}),
            patch.object(hearthstone_click, "human_like_pause",
                         side_effect=lambda m=None, s=None: called.append(1)),
            patch.object(hearthstone_click.time, "sleep") as sleep,
        ):
            hearthstone_click.park_mouse(mouse)

        self.assertEqual([], called)                 # 不再自动触发活人感
        sleep.assert_called_once_with(0.1)
        self.assertEqual([("position", hearthstone_click.MOUSE_RESET_POS)],
                         mouse.events)

    def test_action_session_parks_plainly(self):
        mouse = RecordingMouse()
        called = []

        with (
            patch.object(hearthstone_click, "human_like_settings",
                         return_value={"enabled": True}),
            patch.object(hearthstone_click, "human_like_pause",
                         side_effect=lambda m=None, s=None: called.append(1)),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click.time, "sleep"),
        ):
            with hearthstone_click.hearthstone_action_session():
                pass

        self.assertEqual([], called)
        self.assertEqual([("position", hearthstone_click.MOUSE_RESET_POS)],
                         mouse.events)


class PostActionPauseHookTests(unittest.TestCase):
    """FSM_action 的挂点：只在开启时接管，且只由流程层调用。"""

    def setUp(self):
        import FSM_action
        self.fsm = FSM_action

    def test_disabled_returns_false_and_does_not_pause(self):
        calls = []
        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value={"enabled": False}),
            patch.object(self.fsm.click, "human_like_pause",
                         side_effect=lambda: calls.append(1)),
        ):
            handled = self.fsm._human_like_post_action_pause()

        self.assertFalse(handled)
        self.assertEqual([], calls)

    def test_enabled_runs_pause_and_takes_over(self):
        calls = []
        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value={"enabled": True}),
            patch.object(self.fsm.click, "human_like_pause",
                         side_effect=lambda: calls.append(1)),
        ):
            handled = self.fsm._human_like_post_action_pause()

        self.assertTrue(handled)
        self.assertEqual([1], calls)

    def test_failure_falls_back_to_fixed_delay(self):
        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value={"enabled": True}),
            patch.object(self.fsm.click, "human_like_pause",
                         side_effect=RuntimeError("鼠标异常")),
        ):
            handled = self.fsm._human_like_post_action_pause()

        self.assertFalse(handled)


class WebHumanLikeTests(unittest.TestCase):
    def setUp(self):
        self._saved_thread = web_ui_thread()
        import web_ui
        self.web_ui = web_ui
        self._saved = web_ui.CTRL.automation_thread

    def tearDown(self):
        self.web_ui.CTRL.automation_thread = self._saved

    def test_status_exposes_defaults(self):
        # 不能依赖用户真实的 ui_config.json：显式让 load_config 返回“没有 human_like 段”
        with patch.object(self.web_ui, "load_config", return_value={}):
            snapshot = self.web_ui.status_snapshot()
        self.assertEqual(self.web_ui.DEFAULT_HUMAN_LIKE,
                         {k: snapshot["human_like"][k]
                          for k in self.web_ui.DEFAULT_HUMAN_LIKE})

    def test_status_reflects_saved_section(self):
        saved = {"human_like": {"enabled": True, "post_delay_min": 0.5,
                                "post_delay_max": 3.0, "hover_min": 0.2, "hover_max": 1.0}}
        with patch.object(self.web_ui, "load_config", return_value=saved):
            snapshot = self.web_ui.status_snapshot()
        self.assertTrue(snapshot["human_like"]["enabled"])

    def test_refuses_while_running(self):
        self.web_ui.CTRL.automation_thread = object()

        result = self.web_ui.api_save_human_like({"enabled": True})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_saves_and_clamps(self):
        saved = {}

        with (
            patch.object(self.web_ui, "load_config", return_value={}),
            patch.object(self.web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(self.web_ui, "_log"),
            patch.object(self.web_ui.CTRL, "automation_thread", None),
        ):
            result = self.web_ui.api_save_human_like({
                "enabled": True, "post_delay_min": 5, "post_delay_max": 1,
                "hover_min": 1.5, "hover_max": 0.3})

        self.assertTrue(result["ok"])
        hl = saved["human_like"]
        self.assertTrue(hl["enabled"])
        self.assertEqual(5.0, hl["post_delay_min"])
        self.assertEqual(5.0, hl["post_delay_max"])   # 上限被抬到不低于下限
        self.assertEqual(1.5, hl["hover_min"])
        self.assertEqual(1.5, hl["hover_max"])        # 悬停上限同样不低于下限
        self.assertNotIn("hover_seconds", hl)         # 旧的固定悬停字段被清掉

    def test_rejects_non_numeric(self):
        with (
            patch.object(self.web_ui, "load_config", return_value={}),
            patch.object(self.web_ui.CTRL, "automation_thread", None),
        ):
            result = self.web_ui.api_save_human_like({"post_delay_min": "慢"})

        self.assertFalse(result["ok"])
        self.assertIn("数字", result["error"])


def web_ui_thread():
    """占位：保证 setUp 里先导入 web_ui 再取旧值。"""
    import web_ui
    return web_ui.CTRL.automation_thread


if __name__ == "__main__":
    unittest.main()
