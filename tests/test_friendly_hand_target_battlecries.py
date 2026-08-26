import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from manual_controller import (
    ClickExecutor,
    ManualController,
    PlayCardAction,
    Target,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_card(self, hand_index, hand_count):
        self.events.append(("choose_card", hand_index, hand_count))

    def put_minion(self, gap_index, board_count):
        self.events.append(("put_minion", gap_index, board_count))

    def cancel_click(self):
        self.events.append(("cancel_click",))


class FriendlyHandTargetBattlecryTests(unittest.TestCase):
    @staticmethod
    def _proposed(source_slot, target_slot):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text=(
                "打法参考A\n"
                f"打出{source_slot}号位随从\n"
                f"目标是我方{target_slot}号位"
            ),
            confidence=0.99,
        )
        return RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

    @staticmethod
    def _controller(clicks, sleeps):
        return ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(
                click_module=clicks,
                sleep_func=sleeps.append,
                action_context=nullcontext,
            ),
        )

    def test_supported_minions_use_friendly_hand_target_flow(self):
        for card_id, card_name in (
                ("CATA_490", "魔眼秘术师"),
                ("CATA_563", "雷鸣流云")):
            with self.subTest(card_id=card_id):
                cards = [
                    SimpleNamespace(
                        card_id="SPELL_1", cardtype="SPELL",
                        entity_id="entity-1", name="法术一"),
                    SimpleNamespace(
                        card_id=card_id, cardtype="MINION",
                        entity_id="entity-2", name=card_name),
                    SimpleNamespace(
                        card_id="SPELL_2", cardtype="SPELL",
                        entity_id="entity-3", name="法术二"),
                    SimpleNamespace(
                        card_id="SPELL_3", cardtype="SPELL",
                        entity_id="entity-4", name="法术三"),
                ]
                state = SimpleNamespace(
                    game_num_turns_in_play=3,
                    is_my_turn=True,
                    my_hand_cards=cards,
                    my_minions=[],
                    my_locations=[],
                    my_board_slot_num=0,
                    oppo_minions=[],
                    oppo_board_slot_num=0,
                )
                proposed = self._proposed(source_slot=2, target_slot=4)
                try:
                    adapted = adapt_action(proposed, state)
                except RecommendationStateError as exc:
                    self.fail(
                        f"{card_id} should support a friendly hand target: "
                        f"{exc}")
                clicks = RecordingClickModule()
                sleeps = []

                result = self._controller(clicks, sleeps).execute(
                    adapted.manual_action, state)

                self.assertTrue(result.executed, result.message)
                self.assertEqual([
                    ("choose_card", 1, 4),
                    ("put_minion", 0, 0),
                    ("choose_card", 2, 3),
                    ("cancel_click",),
                ], clicks.events)
                self.assertEqual([0.8], sleeps)

    def test_cata_563_rejects_out_of_range_hand_target(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-1"),
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-2"),
            ],
            my_minions=[],
            my_locations=[],
        )

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_target_out_of_range"):
            adapt_action(
                self._proposed(source_slot=1, target_slot=99), state)

    def test_cata_563_rejects_its_own_hand_slot_as_target(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-1"),
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-2"),
            ],
            my_minions=[],
            my_locations=[],
        )

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_target_is_source"):
            adapt_action(
                self._proposed(source_slot=2, target_slot=2), state)

    def test_cata_563_rejects_replaced_hand_target_before_clicking(self):
        cards = [
            SimpleNamespace(
                card_id="CATA_563", cardtype="MINION",
                entity_id="entity-1", name="雷鸣流云"),
            SimpleNamespace(
                card_id="SPELL_1", cardtype="SPELL",
                entity_id="entity-2", name="法术一"),
        ]
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=cards,
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        adapted = adapt_action(
            self._proposed(source_slot=1, target_slot=2), state)
        state.my_hand_cards[1] = SimpleNamespace(
            card_id="SPELL_2", cardtype="SPELL",
            entity_id="replacement", name="替换法术")
        clicks = RecordingClickModule()
        sleeps = []
        controller = self._controller(clicks, sleeps)

        result = controller.execute(adapted.manual_action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)

    def test_cata_563_executor_rejects_source_card_as_hand_target(self):
        source = SimpleNamespace(
            card_id="CATA_563", cardtype="MINION",
            entity_id="entity-1", name="雷鸣流云")
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[source],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        action = PlayCardAction(
            hand_index=0,
            card_id="CATA_563",
            cardtype="MINION",
            gap_index=0,
            target=Target("friendly", "hand", 0, "entity-1"),
            hand_entity_id="entity-1",
        )
        clicks = RecordingClickModule()
        sleeps = []
        controller = self._controller(clicks, sleeps)

        result = controller.execute(action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)

    def test_target_before_source_keeps_its_hand_index(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-1", name="法术一"),
                SimpleNamespace(
                    card_id="SPELL_2", cardtype="SPELL",
                    entity_id="entity-2", name="法术二"),
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-3", name="雷鸣流云"),
            ],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        adapted = adapt_action(
            self._proposed(source_slot=3, target_slot=1), state)
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([
            ("choose_card", 2, 3),
            ("put_minion", 0, 0),
            ("choose_card", 0, 2),
            ("cancel_click",),
        ], clicks.events)

    def test_other_minions_cannot_use_friendly_hand_targets(self):
        source = SimpleNamespace(
            card_id="OTHER_MINION", cardtype="MINION",
            entity_id="entity-1", name="普通随从")
        target = SimpleNamespace(
            card_id="SPELL_1", cardtype="SPELL",
            entity_id="entity-2", name="法术一")
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[source, target],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        action = PlayCardAction(
            hand_index=0,
            card_id="OTHER_MINION",
            cardtype="MINION",
            gap_index=0,
            target=Target("friendly", "hand", 1, "entity-2"),
            hand_entity_id="entity-1",
        )
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)


if __name__ == "__main__":
    unittest.main()
