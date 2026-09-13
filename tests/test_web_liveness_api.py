"""Web 控制台侧的存活检测 / 昵称校验：保存配置、状态暴露、浮窗回调、页面元素。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import web_ui

INDEX_HTML = (Path(__file__).resolve().parent.parent / "web" / "index.html")


class SaveLivenessTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}

        def _save(cfg):
            self.saved = cfg

        self._patches = [
            patch.object(web_ui, "load_config",
                         side_effect=lambda: dict(self.saved)),
            patch.object(web_ui, "save_config", side_effect=_save),
            patch.object(web_ui, "_log"),
        ]
        for p in self._patches:
            p.start()
        self._saved_thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved_thread
        for p in self._patches:
            p.stop()

    def test_defaults_are_enabled(self):
        cfg = web_ui._current_liveness()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(300.0, cfg["log_stale_stop_seconds"])

    def test_save_writes_thresholds(self):
        result = web_ui.api_save_liveness(
            {"enabled": True, "log_stale_warn_seconds": 90,
             "log_stale_stop_seconds": 240})

        self.assertTrue(result["ok"])
        self.assertEqual(
            {"enabled": True, "process_grace_seconds": 6.0,
             "log_stale_warn_seconds": 90.0, "log_stale_stop_seconds": 240.0},
            self.saved["liveness"])

    def test_stop_threshold_cannot_be_below_warn_threshold(self):
        web_ui.api_save_liveness({"enabled": True,
                                  "log_stale_warn_seconds": 200,
                                  "log_stale_stop_seconds": 100})

        self.assertEqual(200.0, self.saved["liveness"]["log_stale_stop_seconds"])

    def test_invalid_numbers_are_refused(self):
        result = web_ui.api_save_liveness(
            {"enabled": True, "log_stale_warn_seconds": "abc"})

        self.assertFalse(result["ok"])
        self.assertEqual({}, self.saved)

    def test_refused_while_automation_runs(self):
        web_ui.CTRL.automation_thread = object()

        result = web_ui.api_save_liveness({"enabled": False})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])


class LivenessStateTests(unittest.TestCase):
    def tearDown(self):
        pass

    def test_prefers_live_state_from_the_state_machine(self):
        live = {"enabled": True, "status": "gone", "process": "stopped",
                "log_age": None, "alert": "炉石已退出", "in_game": True}
        fsm = SimpleNamespace(hearthstone_liveness_state=lambda: dict(live))

        with patch.object(web_ui.CTRL, "fsm", fsm):
            state = web_ui._liveness_state()

        self.assertEqual("gone", state["status"])
        self.assertEqual("炉石已退出", state["alert"])

    def test_samples_the_monitor_without_automation(self):
        monitor = SimpleNamespace(sample=lambda in_game=False: {
            "enabled": True, "status": "ok", "process": "running",
            "log_age": 3.0, "alert": None, "in_game": in_game})
        module = SimpleNamespace(default_monitor=lambda: monitor)

        with (
            patch.object(web_ui.CTRL, "fsm", None),
            patch.dict("sys.modules",
                       {"src.safety.hearthstone_liveness": module}),
        ):
            state = web_ui._liveness_state()

        self.assertEqual("ok", state["status"])
        self.assertFalse(state["in_game"])

    def test_status_snapshot_exposes_liveness_and_alert(self):
        live = {"enabled": True, "status": "stale", "process": "running",
                "log_age": 400.0, "alert": "炉石疑似无响应", "in_game": True}
        fsm = SimpleNamespace(hearthstone_liveness_state=lambda: dict(live),
                              stop_after_current_game=False,
                              FSM_state="Battling", game_count=0, win_count=0,
                              concede_count=0)

        with patch.object(web_ui.CTRL, "fsm", fsm):
            snapshot = web_ui.status_snapshot()

        self.assertEqual("stale", snapshot["liveness"]["status"])
        self.assertEqual("炉石疑似无响应", snapshot["liveness_alert"])
        self.assertTrue(snapshot["liveness"]["enabled"])

    def test_name_match_state_falls_back_to_config(self):
        with (
            patch.object(web_ui.CTRL, "fsm", None),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345"}),
        ):
            state = web_ui._name_match_state()

        self.assertIsNone(state["matched"])
        self.assertEqual("TestUser#12345", state["config"])

    def test_name_match_state_prefers_state_machine(self):
        live = {"config": "Old#1", "players": {"1": "New#2"}, "matched": False}
        fsm = SimpleNamespace(player_name_state=lambda: dict(live))

        with patch.object(web_ui.CTRL, "fsm", fsm):
            state = web_ui._name_match_state()

        self.assertFalse(state["matched"])


class OverlayCallbackTests(unittest.TestCase):
    def test_overlay_liveness_callback_renders_a_row(self):
        live = {"enabled": True, "status": "ok", "log_age": 5.0,
                "in_game": True, "alert": None}

        with patch.object(web_ui, "_liveness_state", return_value=dict(live)):
            row = log_overlay.hearthstone_row(web_ui._overlay_liveness())

        self.assertEqual("运行中", row["value"])
        self.assertEqual(log_overlay.GREEN, row["value_color"])

    def test_overlay_account_callback_renders_a_row(self):
        live = {"config": "Old#1", "players": {"1": "New#2"}, "matched": False}

        with patch.object(web_ui, "_name_match_state", return_value=dict(live)):
            row = log_overlay.account_row(web_ui._overlay_account())

        self.assertEqual("不匹配", row["value"])
        self.assertEqual(log_overlay.DANGER, row["value_color"])

    def test_callbacks_are_wired_into_the_overlay(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui.threading, "Thread"),
        ):
            web_ui._bind_overlay()

        self.assertIn("liveness_callback", bound)
        self.assertIn("account_callback", bound)
        self.assertIs(bound["liveness_callback"], web_ui._overlay_liveness)
        self.assertIs(bound["account_callback"], web_ui._overlay_account)

    def test_alerts_reach_the_overlay_log(self):
        alert = ("[12:00:00 ERROR] ⚠️ 炉石已退出：Hearthstone.exe 进程消失；"
                 "自动化已自动停止。")
        mismatch = ("[12:00:00 WARN] ⚠️ 用户 ID 与日志玩家名不匹配：日志中玩家为 "
                    "NewAccount#2222，当前配置为 Old#1111。")

        self.assertTrue(web_ui._overlay_key(alert))
        self.assertTrue(web_ui._overlay_key(mismatch))


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_liveness_card_exists(self):
        for token in ('id="chkLiveness"', 'id="inpLvWarn"', 'id="inpLvStop"',
                      'id="btnSaveLiveness"', 'id="livenessStatus"',
                      'id="nameMatch"'):
            self.assertIn(token, self.html)

    def test_page_posts_to_the_liveness_api(self):
        self.assertIn('post("/api/liveness"', self.html)

    def test_page_warns_about_fullscreen_and_full_nickname(self):
        self.assertIn("全屏", self.html)
        self.assertIn("最大化", self.html)
        self.assertIn("#编号", self.html)


if __name__ == "__main__":
    unittest.main()
