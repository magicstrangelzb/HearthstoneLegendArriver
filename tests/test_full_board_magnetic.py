import unittest
from contextlib import nullcontext
from unittest.mock import patch

import click as hearthstone_click
from manual_controller import ClickExecutor


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_card(self, hand_index, hand_count):
        self.events.append(("choose_card", hand_index, hand_count))

    def put_minion(self, gap_index, board_count):
        self.events.append(("put_minion", gap_index, board_count))

    def drag_card_to_board_entity(
            self, hand_index, hand_count, board_index, board_count):
        self.events.append((
            "drag_card_to_board_entity",
            hand_index,
            hand_count,
            board_index,
            board_count,
        ))

    def cancel_click(self):
        self.events.append(("cancel_click",))


class FullBoardMagneticTests(unittest.TestCase):
    def _executor(self, clicks):
        return ClickExecutor(
            click_module=clicks,
            sleep_func=lambda _seconds: None,
            action_context=nullcontext,
        )

    def test_full_board_drags_card_to_recommended_original_position(self):
        clicks = RecordingClickModule()

        self._executor(clicks).play_minion(
            hand_index=2,
            hand_count=5,
            gap_index=3,
            minion_count=7,
            oppo_minion_count=0,
            target=None,
        )

        self.assertEqual([
            ("drag_card_to_board_entity", 2, 5, 3, 7),
            ("cancel_click",),
        ], clicks.events)

    def test_non_full_board_keeps_original_click_placement(self):
        clicks = RecordingClickModule()

        self._executor(clicks).play_minion(
            hand_index=2,
            hand_count=5,
            gap_index=3,
            minion_count=6,
            oppo_minion_count=0,
            target=None,
        )

        self.assertEqual([
            ("choose_card", 2, 5),
            ("put_minion", 3, 6),
            ("cancel_click",),
        ], clicks.events)

    def test_drag_ends_25_pixels_left_of_original_board_position(self):
        class RecordingMouse:
            def __init__(self):
                self.events = []

            @property
            def position(self):
                return None

            @position.setter
            def position(self, value):
                self.events.append(("position", value))

            def press(self, _button):
                self.events.append(("press",))

            def release(self, _button):
                self.events.append(("release",))

        mouse = RecordingMouse()
        try:
            drag = hearthstone_click.drag_card_to_board_entity
        except AttributeError:
            self.fail("full-board magnetic drag primitive is missing")

        with (
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            drag(
                card_index=2,
                card_num=5,
                entity_index=3,
                entity_num=7,
            )

        self.assertEqual([
            ("position", (890, 1000)),
            ("press",),
            ("position", (935, 600)),
            ("release",),
        ], mouse.events)


if __name__ == "__main__":
    unittest.main()
