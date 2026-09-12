"""自动投降检测：每回合都要读到胜率，读不到要在同一回合内重试。

回归背景：原先每回合只读一次，读不到就把整回合标记为“已检测”并清零连续计数，
盒子浮动条还没画好时就表现为“有时候根本没在检测”。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import FSM_action

CONCEDE_CFG = {"enabled": True, "threshold": 20.0, "rounds": 3}


class MaybeConcedeTests(unittest.TestCase):
    def setUp(self):
        self._saved = (FSM_action._concede_streak, FSM_action._concede_last_turn,
                       FSM_action._concede_triggered)
        FSM_action._concede_streak = 0
        FSM_action._concede_last_turn = None
        FSM_action._concede_triggered = False
        self.logs = []

    def tearDown(self):
        (FSM_action._concede_streak, FSM_action._concede_last_turn,
         FSM_action._concede_triggered) = self._saved

    def _snapshot(self, turn):
        return SimpleNamespace(game_num_turns_in_play=turn)

    def _run(self, turn, rates, cfg=None, output=None):
        """rates 为 read_ai_win_rate 依次返回的值（可含 None）。"""
        seq = list(rates)
        logs = self.logs

        def fake_read():
            return seq.pop(0) if seq else None

        with (
            patch.object(FSM_action, "_load_concede_config",
                         return_value=dict(cfg or CONCEDE_CFG)),
            patch.object(FSM_action, "read_ai_win_rate", side_effect=fake_read),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=(output or (lambda msg: logs.append(msg)))),
            patch.object(FSM_action.time, "sleep"),
        ):
            result = FSM_action._maybe_concede(self._snapshot(turn))
        return result

    def test_retries_within_the_same_turn_until_a_value_is_read(self):
        result = self._run(turn=3, rates=[None, None, 12.0])

        self.assertFalse(result)
        self.assertEqual(1, FSM_action._concede_streak)
        self.assertEqual(3, FSM_action._concede_last_turn)
        # 前两次失败各打一条重试日志，说明“确实在检测、只是没读到”
        self.assertEqual(2, len([m for m in self.logs if "没读到" in m]))

    def test_all_attempts_failing_skips_the_turn_and_logs_it(self):
        result = self._run(turn=4, rates=[None, None, None])

        self.assertFalse(result)
        self.assertEqual(0, FSM_action._concede_streak)
        self.assertEqual(4, FSM_action._concede_last_turn)
        self.assertTrue(any("读取失败" in m for m in self.logs))
        self.assertTrue(any("跳过本次检测" in m for m in self.logs))

    def test_same_turn_is_only_checked_once(self):
        calls = []

        with (
            patch.object(FSM_action, "_load_concede_config",
                         return_value=dict(CONCEDE_CFG)),
            patch.object(FSM_action, "read_ai_win_rate",
                         side_effect=lambda: (calls.append(1), 30.0)[1]),
            patch.object(FSM_action.manual_controller, "output"),
        ):
            FSM_action._maybe_concede(self._snapshot(5))
            FSM_action._maybe_concede(self._snapshot(5))

        self.assertEqual(1, len(calls))

    def test_three_consecutive_low_turns_trigger_concede(self):
        results = [self._run(turn=t, rates=[10.0]) for t in (1, 2, 3)]

        self.assertEqual([False, False, True], results)
        self.assertTrue(FSM_action._concede_triggered)
        self.assertEqual(3, FSM_action._concede_streak)

    def test_high_rate_resets_the_streak(self):
        self._run(turn=1, rates=[10.0])
        self.assertEqual(1, FSM_action._concede_streak)

        self._run(turn=2, rates=[55.0])

        self.assertEqual(0, FSM_action._concede_streak)
        self.assertTrue(any("未低于阈值" in m for m in self.logs))

    def test_failed_read_resets_the_streak_and_says_so(self):
        self._run(turn=1, rates=[10.0])

        self._run(turn=2, rates=[None, None, None])

        self.assertEqual(0, FSM_action._concede_streak)
        self.assertTrue(any("连续计数清零" in m for m in self.logs))

    def test_disabled_config_never_reads(self):
        calls = []

        with (
            patch.object(FSM_action, "_load_concede_config",
                         return_value={"enabled": False, "threshold": 20.0,
                                       "rounds": 3}),
            patch.object(FSM_action, "read_ai_win_rate",
                         side_effect=lambda: calls.append(1)),
        ):
            result = FSM_action._maybe_concede(self._snapshot(1))

        self.assertFalse(result)
        self.assertEqual([], calls)
        self.assertIsNone(FSM_action._concede_last_turn)

    def test_already_triggered_stops_checking(self):
        FSM_action._concede_triggered = True
        calls = []

        with patch.object(FSM_action, "read_ai_win_rate",
                          side_effect=lambda: calls.append(1)):
            result = FSM_action._maybe_concede(self._snapshot(9))

        self.assertFalse(result)
        self.assertEqual([], calls)


class ConcedeDetectionStateTests(unittest.TestCase):
    """供界面显示的检测状态：最近胜率 / 连续计数 / 最近检测回合。"""

    def setUp(self):
        self._saved = (FSM_action._concede_streak, FSM_action._concede_last_turn,
                       FSM_action._concede_triggered, FSM_action._concede_last_rate,
                       FSM_action._concede_last_check)
        FSM_action._concede_streak = 0
        FSM_action._concede_last_turn = None
        FSM_action._concede_triggered = False
        FSM_action._concede_last_rate = None
        FSM_action._concede_last_check = None

    def tearDown(self):
        (FSM_action._concede_streak, FSM_action._concede_last_turn,
         FSM_action._concede_triggered, FSM_action._concede_last_rate,
         FSM_action._concede_last_check) = self._saved

    def test_state_reports_config_and_live_values(self):
        FSM_action._concede_last_rate = 15.0
        FSM_action._concede_last_check = 5
        FSM_action._concede_streak = 2
        FSM_action._concede_triggered = True

        with patch.object(FSM_action, "_load_concede_config",
                          return_value=dict(CONCEDE_CFG)):
            state = FSM_action.concede_detection_state()

        self.assertEqual({"enabled": True, "threshold": 20.0, "rounds": 3,
                          "rate": 15.0, "streak": 2, "checked_turn": 5,
                          "triggered": True}, state)

    def test_successful_check_records_rate_and_turn(self):
        with (
            patch.object(FSM_action, "_load_concede_config",
                         return_value=dict(CONCEDE_CFG)),
            patch.object(FSM_action, "read_ai_win_rate", return_value=12.0),
            patch.object(FSM_action.manual_controller, "output"),
        ):
            FSM_action._maybe_concede(SimpleNamespace(game_num_turns_in_play=3))
            state = FSM_action.concede_detection_state()

        self.assertEqual(12.0, state["rate"])
        self.assertEqual(3, state["checked_turn"])

    def test_failed_check_keeps_turn_but_no_rate(self):
        with (
            patch.object(FSM_action, "_load_concede_config",
                         return_value=dict(CONCEDE_CFG)),
            patch.object(FSM_action, "read_ai_win_rate", return_value=None),
            patch.object(FSM_action.manual_controller, "output"),
            patch.object(FSM_action.time, "sleep"),
        ):
            FSM_action._maybe_concede(SimpleNamespace(game_num_turns_in_play=4))
            state = FSM_action.concede_detection_state()

        self.assertIsNone(state["rate"])
        self.assertEqual(4, state["checked_turn"])   # 检测过，只是没读到


class WinRateReadTests(unittest.TestCase):
    """OCR 读取：小字号放大 + 主区域读不到时用兜底区域。"""

    def test_returns_none_when_dependencies_missing(self):
        with patch.dict("sys.modules", {"cv2": None}):
            self.assertIsNone(FSM_action.read_ai_win_rate())

    def test_uses_the_fallback_region(self):
        attempts = []

        class FakeBackend:
            def recognize(self, img, tag, kind):
                attempts.append(tag)
                if tag.startswith("winrate-0"):
                    return SimpleNamespace(lines=())
                return SimpleNamespace(lines=(SimpleNamespace(
                    text="AI胜率 15%", confidence=0.9),))

        class FakeCv2:
            COLOR_RGB2BGR = 0
            INTER_CUBIC = 2

            @staticmethod
            def cvtColor(img, code):
                return img

            @staticmethod
            def resize(img, size, fx=None, fy=None, interpolation=None):
                return img

        fake_np = SimpleNamespace(asarray=lambda img: img)
        fake_grab = SimpleNamespace(grab=lambda bbox, all_screens: "img")

        with (
            patch.dict("sys.modules", {
                "cv2": FakeCv2,
                "numpy": fake_np,
                "PIL": SimpleNamespace(ImageGrab=fake_grab),
                "PIL.ImageGrab": fake_grab,
            }),
            patch.object(FSM_action, "mulligan_reader",
                         SimpleNamespace(backend=SimpleNamespace(
                             recognize=FakeBackend().recognize))),
        ):
            value = FSM_action.read_ai_win_rate()

        self.assertEqual(15.0, value)
        self.assertEqual(2, len(attempts))       # 主区域失败后用了兜底区域


if __name__ == "__main__":
    unittest.main()
