# -*- coding: utf-8 -*-
"""浮窗三项体验改动：

1. 「账号」行加一个眼睛按钮（👁 / 👁✖）切换是否显示战网昵称，选择会被记住；
2. 按钮缩小、一行两个，「本局结束后停止」单独一行；
3. 正文日志按标签着色，颜色只用浮窗现有调色板里的。
"""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import log_overlay
import web_ui


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target

    def start(self):
        return None       # 不真的开 Tk 窗口


class AccountEyeToggleTests(unittest.TestCase):
    def setUp(self):
        self._visible = log_overlay._ACCOUNT_VISIBLE[0]
        self._on_toggle = log_overlay._ON_TOGGLE_ACCOUNT
        log_overlay._ACCOUNT_VISIBLE[0] = True
        log_overlay._ON_TOGGLE_ACCOUNT = None

    def tearDown(self):
        log_overlay._ACCOUNT_VISIBLE[0] = self._visible
        log_overlay._ON_TOGGLE_ACCOUNT = self._on_toggle

    def test_default_is_visible(self):
        self.assertTrue(log_overlay.account_visible())
        self.assertNotEqual(log_overlay.EYE_SHOW, log_overlay.EYE_HIDE)

    def test_toggle_flips_and_notifies_the_saver(self):
        saved = []
        log_overlay._ON_TOGGLE_ACCOUNT = saved.append

        self.assertFalse(log_overlay.toggle_account_visibility())
        self.assertFalse(log_overlay.account_visible())

        self.assertTrue(log_overlay.toggle_account_visibility())
        self.assertTrue(log_overlay.account_visible())
        self.assertEqual([False, True], saved)

    def test_save_failure_still_toggles(self):
        def _boom(_value):
            raise RuntimeError("磁盘只读")

        log_overlay._ON_TOGGLE_ACCOUNT = _boom

        self.assertFalse(log_overlay.toggle_account_visibility())
        self.assertFalse(log_overlay.account_visible())

    def test_start_seeds_the_saved_state(self):
        with (
            patch.object(log_overlay, "_run"),
            patch.object(log_overlay.threading, "Thread", _FakeThread),
        ):
            log_overlay._STARTED[0] = False
            log_overlay.start(account_visible_setting=False,
                              on_toggle_account=lambda _v: None)

        self.assertFalse(log_overlay.account_visible())

    def test_hidden_row_does_not_leak_the_nickname(self):
        info = {"config": "TestUser#12345",
                "players": {"1": "TestUser#12345", "2": "Other#2"},
                "matched": True}

        row = log_overlay.account_row(info, show_account=False)

        self.assertEqual("匹配", row["value"])
        self.assertEqual(log_overlay.MARKER_ON, row["marker"])
        self.assertEqual(log_overlay._ACCOUNT_HIDDEN_TEXT, row["detail"])
        self.assertNotIn("TestUser", row["detail"])

    def test_hidden_row_keeps_the_mismatch_warning(self):
        info = {"config": "Old#1", "players": {"1": "New#2"}, "matched": False}

        row = log_overlay.account_row(info, show_account=False)

        self.assertEqual("不匹配", row["value"])
        self.assertEqual(log_overlay.DANGER, row["value_color"])
        self.assertEqual(log_overlay.MARKER_OFF, row["marker"])
        self.assertNotIn("New#2", row["detail"])

    def test_hiding_keeps_the_default_behaviour_for_unknown_state(self):
        row = log_overlay.account_row(
            {"config": "", "players": {}, "matched": None}, show_account=False)

        self.assertEqual("—", row["value"])

    def test_visible_row_is_unchanged(self):
        info = {"config": "TestUser#12345",
                "players": {"1": "TestUser#12345"}, "matched": True}

        row = log_overlay.account_row(info, show_account=True)

        self.assertEqual("TestUser#12345", row["detail"])

    def test_eye_is_rendered_and_clickable(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("toggle_account_visibility()", source)
        self.assertIn('bind("<Button-1>"', source)
        self.assertIn("column=4", source)      # 眼睛放在「账号」行最右侧


class AccountPreferencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ui_config.json"
        self._saved_path = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        self.path.write_text(json.dumps(
            {"name": "TestUser#12345", "log_root": "D:\\Logs",
             "auto_concede": {"enabled": True, "threshold": 20.0, "rounds": 3}},
            ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        config.CONFIG_PATH = self._saved_path
        self._tmp.cleanup()

    def test_default_shows_the_account(self):
        self.assertTrue(config.overlay_settings()["show_account"])

    def test_save_and_read_back(self):
        config.save_overlay_setting("show_account", False)

        self.assertFalse(config.overlay_settings()["show_account"])
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(written["overlay"]["show_account"])

    def test_other_settings_are_preserved(self):
        config.save_overlay_setting("show_account", False)

        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual("TestUser#12345", written["name"])
        self.assertEqual("D:\\Logs", written["log_root"])
        self.assertTrue(written["auto_concede"]["enabled"])

    def test_unknown_key_is_refused(self):
        with self.assertRaises(KeyError):
            config.save_overlay_setting("show_name", True)

    def test_broken_config_file_is_rebuilt(self):
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")

        config.save_overlay_setting("show_account", False)

        self.assertFalse(config.overlay_settings()["show_account"])

    def test_web_bridge_reads_and_writes_the_preference(self):
        self.assertFalse(web_ui._overlay_account_visible() is None)

        with patch.object(web_ui, "_log"):
            web_ui._overlay_save_account_visible(False)

        self.assertFalse(config.overlay_settings()["show_account"])
        self.assertFalse(web_ui._overlay_account_visible())

    def test_overlay_binding_passes_the_preference(self):
        bound = {}
        overlay = type("O", (), {
            "start": lambda self, **kwargs: bound.update(kwargs),
            "is_running": lambda self: False})()

        with patch.object(web_ui, "log_overlay", overlay):
            web_ui._bind_overlay()

        self.assertIn("account_visible_setting", bound)
        self.assertIn("on_toggle_account", bound)
        self.assertIs(bound["on_toggle_account"],
                      web_ui._overlay_save_account_visible)


class LogColorTests(unittest.TestCase):
    def test_colors_come_from_the_existing_palette(self):
        palette = {log_overlay.GREEN, log_overlay.ACCENT, log_overlay.GOLD,
                   log_overlay.WARN, log_overlay.DANGER, log_overlay.TEXT,
                   log_overlay.DIM, log_overlay.OK}
        for tag, color in log_overlay.LOG_TAG_COLORS.items():
            self.assertIn(color, palette, tag)

    def test_distinct_tags_get_distinct_colors(self):
        # 每类标签颜色都不同，才看得出区别（告警/报错共用红色是刻意的）。
        colors = list(log_overlay.LOG_TAG_COLORS.values())
        self.assertEqual(len(set(colors)), len(colors))

    def test_tag_mapping(self):
        cases = {
            "[推荐] 打出1号位随从": "reco",
            "[执行] 开始执行操作": "exec",
            "[12:04:45 SYS] 自动投降检测：AI胜率 15.4%": "sys",
            "[12:04:31 SYS] 回合 5 延时结束，开始本轮推荐读取。": "turn",
            "[12:04:33 INFO] 你赢得了这场对战": "dim",
            "[12:04:52 WARN] 盒子面板暂不可读，继续重试。": "warn",
            "等待对手操作。": "dim",
        }
        for line, expected in cases.items():
            self.assertEqual(expected, log_overlay.log_line_tag(line), line)

    def test_error_and_alerts_are_the_highlighted_tag(self):
        """ERROR 与 ⚠️ 都算“必须马上看见”，用红色加粗。"""
        for line in ("[12:04:57 ERROR] 自动化线程异常退出",
                     "[12:04:57 ERROR] ⚠️ 炉石已退出：Hearthstone.exe 进程消失",
                     "[12:04:58 WARN] ⚠️ 用户 ID 与日志玩家名不匹配……"):
            self.assertEqual("alert", log_overlay.log_line_tag(line), line)

    def test_renderer_uses_the_tag_function_and_configures_every_tag(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("log_line_tag(ln)", source)
        self.assertIn("for tag_name, color in LOG_TAG_COLORS.items()", source)
        for tag in log_overlay.LOG_TAG_COLORS:
            self.assertIn(tag, source.replace("tag_name", tag))
        self.assertNotIn('"act"', source)


class ButtonLayoutTests(unittest.TestCase):
    def test_two_buttons_per_row_and_stop_after_alone(self):
        rows = {}
        for key, (row, column) in log_overlay.BTN_LAYOUT.items():
            rows.setdefault(row, []).append(column)

        self.assertEqual({0: [0, 1], 1: [0], 2: [0, 1]}, rows)
        for columns in rows.values():
            self.assertLessEqual(len(columns), 2)

    def test_every_button_has_a_unique_slot(self):
        slots = list(log_overlay.BTN_LAYOUT.values())
        self.assertEqual(len(slots), len(set(slots)))

    def test_stop_after_spans_the_whole_row(self):
        self.assertEqual(2, log_overlay.BTN_SPAN["stop_after"])
        row, _column = log_overlay.BTN_LAYOUT["stop_after"]
        self.assertNotIn(row, [r for key, (r, _c) in log_overlay.BTN_LAYOUT.items()
                               if key != "stop_after"])

    def test_buttons_are_smaller_than_the_old_stacked_ones(self):
        # 旧版是 5 行、字号 10、pady 5；现在必须更小，否则省不出日志高度。
        self.assertLessEqual(log_overlay.BTN_FONT_SIZE, 9)
        self.assertLessEqual(log_overlay.BTN_PADY, 4)

    def test_buttons_are_placed_with_grid(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("def _place(btn, key)", source)
        for key in log_overlay.BTN_LAYOUT:
            self.assertIn(f'_place({key}_btn, "{key}")', source)


if __name__ == "__main__":
    unittest.main()
