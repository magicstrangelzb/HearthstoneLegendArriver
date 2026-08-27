import unittest
from unittest.mock import patch

import click as hearthstone_click


class RecordingMouse:
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


class ClickSafetyCheckRemovalTests(unittest.TestCase):
    def test_clicks_when_hearthstone_window_is_not_found(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=0),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertEqual([
            ("position", (960, 650)),
            ("press", hearthstone_click.Button.left),
            ("release", hearthstone_click.Button.left),
        ], mouse.events)

    def test_background_window_activates_and_clicks_without_hit_testing(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(
                hearthstone_click.win32gui,
                "GetForegroundWindow",
                return_value=202,
            ),
            patch.object(
                hearthstone_click,
                "point_targets_hearthstone",
                return_value=False,
            ),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertEqual([
            ("position", (1800, 500)),
            ("press", hearthstone_click.Button.right),
            ("release", hearthstone_click.Button.right),
            ("position", (960, 650)),
            ("press", hearthstone_click.Button.left),
            ("release", hearthstone_click.Button.left),
        ], mouse.events)


if __name__ == "__main__":
    unittest.main()
