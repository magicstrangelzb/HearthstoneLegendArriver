"""鼠标复位点：复位必须落在安全位置，且只移动、不点击。

回归背景：旧的复位点 (480, 540) 每局结束后停在牌组选择界面第二行第一个卡组上，
复位后偶发误选中卡组；用户最终指定屏幕左上角 (70, 60) 作为待命点。
"""

import unittest
from unittest.mock import patch

import click as hearthstone_click

RESET = (70, 60)


class RecordingMouse:
    """只记录事件、不真的移动系统鼠标。"""

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


class MouseResetPositionTests(unittest.TestCase):
    def test_reset_position_is_the_safe_spot(self):
        self.assertEqual(RESET, hearthstone_click.MOUSE_RESET_POS)

    def test_center_mouse_only_moves_the_pointer(self):
        mouse = RecordingMouse()

        with patch.object(hearthstone_click, "Controller", return_value=mouse):
            hearthstone_click.center_mouse()

        self.assertEqual([("position", RESET)], mouse.events)

    def test_park_mouse_moves_without_clicking(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click.time, "sleep"),
        ):
            hearthstone_click.park_mouse()

        self.assertEqual([("position", RESET)], mouse.events)

    def test_action_session_parks_at_the_safe_spot(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click.time, "sleep"),
        ):
            with hearthstone_click.hearthstone_action_session():
                pass

        self.assertEqual([("position", RESET)], mouse.events)


if __name__ == "__main__":
    unittest.main()
