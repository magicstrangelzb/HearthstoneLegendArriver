import unittest
from types import SimpleNamespace

from src.parser.recommendation_parser import (
    RecommendationParseError,
    RecommendationParser,
)
from src.recommendation_models import ActionKind, SlotRef


class HandDeckActionParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = RecommendationParser()

    def _parse(self, instruction):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text=instruction,
            confidence=0.99,
        )
        try:
            return self.parser.parse(ocr, turn_number=3, log_revision=7)
        except RecommendationParseError as exc:
            self.fail(f"{instruction!r} should be accepted: {exc}")

    def test_forge_card_uses_existing_trade_action_for_requested_slot(self):
        proposed = self._parse("锻造3号位卡牌")

        self.assertEqual(ActionKind.TRADE_CARD, proposed.action)
        self.assertEqual(SlotRef("hand_slot", "friendly", 3), proposed.source)
        self.assertEqual("锻造3号位卡牌", proposed.normalized_instruction)

    def test_prepare_card_uses_existing_trade_action_for_requested_slot(self):
        proposed = self._parse("预备2号位卡牌")

        self.assertEqual(ActionKind.TRADE_CARD, proposed.action)
        self.assertEqual(SlotRef("hand_slot", "friendly", 2), proposed.source)
        self.assertEqual("预备2号位卡牌", proposed.normalized_instruction)


if __name__ == "__main__":
    unittest.main()
