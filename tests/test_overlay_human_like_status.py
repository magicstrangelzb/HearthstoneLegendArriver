"""浮窗显示活人感开关状态 + 活人感延时驱动底部进度条。"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args

    def start(self):
        return None       # 不真的开 Tk 窗口


class BrandHeaderTests(unittest.TestCase):
    """浮窗顶部品牌行：本项目大名 + 副标题（排在“自动化日志”之前）。"""

    def test_brand_name_and_subtitle(self):
        self.assertEqual("HSLegendArriver", log_overlay.BRAND_NAME)
        self.assertTrue(log_overlay.BRAND_SUB.strip())
        self.assertTrue(log_overlay.GOLD.startswith("#"))

    def test_marker_colors_are_green_red_gray(self):
        self.assertEqual(log_overlay.GREEN, log_overlay.MARKER_ON)
        self.assertEqual(log_overlay.DANGER, log_overlay.MARKER_OFF)
        self.assertEqual(log_overlay.DIM, log_overlay.MARKER_UNKNOWN)


class HumanLikeLabelTests(unittest.TestCase):
    def test_unknown_state_shows_dash(self):
        row = log_overlay.human_like_row(None)
        self.assertEqual("—", row["value"])
        self.assertEqual(log_overlay.MARKER_UNKNOWN, row["marker"])
        self.assertEqual("", row["detail"])

    def test_disabled_state(self):
        row = log_overlay.human_like_row({"enabled": False})
        self.assertEqual("关", row["value"])
        self.assertEqual(log_overlay.MARKER_OFF, row["marker"])   # 关 = 红点

    def test_enabled_state_shows_params(self):
        row = log_overlay.human_like_row({
            "enabled": True, "post_delay_min": 0.5, "post_delay_max": 3.0,
            "hover_min": 0.2, "hover_max": 1.0})
        self.assertEqual("开", row["value"])
        self.assertEqual(log_overlay.GREEN, row["value_color"])
        self.assertEqual(log_overlay.MARKER_ON, row["marker"])    # 开 = 绿点
        self.assertIn("0.5~3s", row["detail"])
        self.assertIn("悬停 0.2~1s", row["detail"])

    def test_enabled_without_params(self):
        row = log_overlay.human_like_row({"enabled": True})
        self.assertEqual("开", row["value"])
        self.assertEqual("", row["detail"])


class ConcedeDetectLabelTests(unittest.TestCase):
    def test_unknown_state(self):
        row = log_overlay.concede_detect_row(None)
        self.assertEqual("—", row["value"])
        self.assertEqual(log_overlay.MARKER_UNKNOWN, row["marker"])

    def test_disabled(self):
        row = log_overlay.concede_detect_row({"enabled": False})
        self.assertEqual("关", row["value"])
        self.assertEqual(log_overlay.MARKER_OFF, row["marker"])   # 关 = 红点

    def test_not_read_this_turn(self):
        row = log_overlay.concede_detect_row({
            "enabled": True, "threshold": 20.0, "rounds": 3, "rate": None,
            "streak": 0, "checked_turn": 4, "triggered": False})
        self.assertEqual("未读到", row["value"])
        self.assertIn("第 4 回合", row["detail"])
        self.assertIn("连续 0/3", row["detail"])
        self.assertEqual(log_overlay.MARKER_ON, row["marker"])    # 开着 = 绿点
        self.assertEqual(log_overlay.WARN, row["value_color"])

    def test_below_threshold(self):
        row = log_overlay.concede_detect_row({
            "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 15.4,
            "streak": 2, "checked_turn": 5, "triggered": False})
        self.assertEqual("15%", row["value"])
        self.assertIn("低于阈值 20%", row["detail"])
        self.assertIn("连续 2/3", row["detail"])
        self.assertEqual(log_overlay.WARN, row["value_color"])

    def test_above_threshold(self):
        row = log_overlay.concede_detect_row({
            "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 64.0,
            "streak": 0, "checked_turn": 5, "triggered": False})
        self.assertEqual("64%", row["value"])
        self.assertIn("阈值 20%", row["detail"])
        self.assertNotIn("低于阈值", row["detail"])
        self.assertEqual(log_overlay.TEXT, row["value_color"])
        self.assertEqual(log_overlay.MARKER_ON, row["marker"])

    def test_triggered(self):
        row = log_overlay.concede_detect_row({
            "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 9.0,
            "streak": 3, "checked_turn": 6, "triggered": True})
        self.assertEqual("已触发认输", row["value"])
        self.assertEqual(log_overlay.DANGER, row["value_color"])


class StartWiringTests(unittest.TestCase):
    def setUp(self):
        self._started = log_overlay._STARTED[0]
        self._human_like = log_overlay._HUMAN_LIKE
        self._concede = log_overlay._CONCEDE_DETECT

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._HUMAN_LIKE = self._human_like
        log_overlay._CONCEDE_DETECT = self._concede

    def test_start_accepts_human_like_callback(self):
        self.assertIn("human_like_callback",
                      inspect.signature(log_overlay.start).parameters)
        callback = lambda: {"enabled": True}      # noqa: E731

        with (
            patch.object(log_overlay, "_run"),
            patch.object(log_overlay.threading, "Thread", _FakeThread),
        ):
            log_overlay._STARTED[0] = False
            log_overlay.start(human_like_callback=callback)

        self.assertIs(callback, log_overlay._HUMAN_LIKE)

    def test_start_accepts_concede_callback(self):
        self.assertIn("concede_callback",
                      inspect.signature(log_overlay.start).parameters)
        callback = lambda: {"enabled": False}     # noqa: E731

        with (
            patch.object(log_overlay, "_run"),
            patch.object(log_overlay.threading, "Thread", _FakeThread),
        ):
            log_overlay._STARTED[0] = False
            log_overlay.start(concede_callback=callback)

        self.assertIs(callback, log_overlay._CONCEDE_DETECT)


class DelayBarTests(unittest.TestCase):
    """活人感延时行必须能驱动浮窗底部进度条，并在结束时清空。"""

    def setUp(self):
        self._started = log_overlay._STARTED[0]
        self._delay = log_overlay._DELAY
        self._lines = list(log_overlay._LINES)
        log_overlay._STARTED[0] = True
        log_overlay._LINES.clear()

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._DELAY = self._delay
        log_overlay._LINES.clear()
        log_overlay._LINES.extend(self._lines)

    def test_human_like_delay_line_sets_progress_bar(self):
        line = "[SYS] 活人感 延时 2.4s 后（手牌区随机悬停等待）"
        self.assertIsNotNone(log_overlay._delay_start_re.search(line))

        log_overlay.push(line, "SYS")

        self.assertIsNotNone(log_overlay._DELAY)
        self.assertAlmostEqual(2.4, log_overlay._DELAY["total"], places=3)
        self.assertIn("活人感", log_overlay._DELAY["desc"])
        self.assertIn("悬停", log_overlay._DELAY["desc"])

    def test_delay_end_marker_clears_progress_bar(self):
        log_overlay.push("[SYS] 活人感 延时 3.0s 后（手牌区随机悬停等待）", "SYS")
        self.assertIsNotNone(log_overlay._DELAY)

        log_overlay.push("[SYS] 延时结束", "SYS")

        self.assertIsNone(log_overlay._DELAY)

    def test_long_delay_line_also_stays_in_text_log(self):
        """≥1s 的延时行除了驱动进度条，仍会出现在浮窗正文里（上游既有行为）。"""
        log_overlay.push("[SYS] 活人感 延时 3.0s 后（手牌区随机悬停等待）", "SYS")

        self.assertIsNotNone(log_overlay._DELAY)
        self.assertIn("[SYS] 活人感 延时 3.0s 后（手牌区随机悬停等待）",
                      [line for line, _turn in log_overlay._LINES])

    def test_short_delay_line_only_drives_the_bar(self):
        """<1s 的延时行只驱动进度条，不占浮窗正文（上游既有行为）。"""
        line = "[SYS] 活人感 延时 0.5s 后（手牌区随机悬停等待）"
        log_overlay.push(line, "SYS")

        self.assertIsNotNone(log_overlay._DELAY)
        self.assertNotIn(line, [text for text, _turn in log_overlay._LINES])


class WebCallbackTests(unittest.TestCase):
    def test_overlay_callback_reports_params(self):
        import web_ui

        with patch.object(web_ui, "_current_human_like", return_value={
                "enabled": True, "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0}):
            info = web_ui._overlay_human_like()

        row = log_overlay.human_like_row(info)
        self.assertEqual("开", row["value"])
        self.assertIn("0.5~3s", row["detail"])
        self.assertIn("悬停 0.2~1s", row["detail"])

    def test_overlay_callback_reports_disabled(self):
        import web_ui

        with patch.object(web_ui, "_current_human_like", return_value={
                "enabled": False, "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0}):
            info = web_ui._overlay_human_like()

        row = log_overlay.human_like_row(info)
        self.assertEqual("关", row["value"])

    def test_concede_state_prefers_live_fsm_values(self):
        import web_ui

        live = {"enabled": True, "threshold": 20.0, "rounds": 3, "rate": 15.0,
                "streak": 2, "checked_turn": 5, "triggered": False}
        fsm = SimpleNamespace(concede_detection_state=lambda: dict(live))

        with patch.object(web_ui.CTRL, "fsm", fsm):
            state = web_ui._concede_detect_state()

        self.assertEqual(live, state)

    def test_concede_state_falls_back_to_config(self):
        import web_ui

        with (
            patch.object(web_ui.CTRL, "fsm", None),
            patch.object(web_ui, "load_config", return_value={
                "auto_concede": {"enabled": True, "threshold": 25.0,
                                 "rounds": 4}}),
        ):
            state = web_ui._concede_detect_state()

        self.assertTrue(state["enabled"])
        self.assertEqual(25.0, state["threshold"])
        self.assertEqual(4, state["rounds"])
        self.assertIsNone(state["rate"])

    def test_status_snapshot_exposes_detection_state(self):
        import web_ui

        live = {"enabled": True, "threshold": 20.0, "rounds": 3, "rate": 33.0,
                "streak": 0, "checked_turn": 7, "triggered": False}
        fsm = SimpleNamespace(concede_detection_state=lambda: dict(live),
                              stop_after_current_game=False,
                              FSM_state="Battling", game_count=1, win_count=1,
                              concede_count=0)

        with patch.object(web_ui.CTRL, "fsm", fsm):
            snapshot = web_ui.status_snapshot()

        self.assertEqual(live, snapshot["concede_detect"])

    def test_overlay_concede_callback_returns_state(self):
        import web_ui

        with patch.object(web_ui, "_concede_detect_state",
                          return_value={"enabled": True, "threshold": 20.0,
                                        "rounds": 3, "rate": 10.0, "streak": 1,
                                        "checked_turn": 2, "triggered": False}):
            row = log_overlay.concede_detect_row(
                web_ui._overlay_concede_detect())

        self.assertEqual("10%", row["value"])
        self.assertIn("连续 1/3", row["detail"])


if __name__ == "__main__":
    unittest.main()
