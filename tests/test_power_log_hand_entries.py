import unittest
from types import SimpleNamespace

from constants.constants import (
    LOG_LINE_FULL_ENTITY,
    LOG_LINE_TAG,
    LOG_LINE_TAG_CHANGE,
)
from log_op import LineInfoContainer
from log_state import CardEntity, LogState, update_state
from strategy import StrategyState


class PowerLogHandEntryTests(unittest.TestCase):
    @staticmethod
    def _card(card_id, controller, zone):
        card = CardEntity(card_id)
        card.set_tag("CONTROLLER", controller)
        card.set_tag("ZONE", zone)
        return card

    @staticmethod
    def _zone_change(entity_id, value):
        return LineInfoContainer(
            LOG_LINE_TAG_CHANGE,
            entity=entity_id,
            tag="ZONE",
            value=value,
        )

    def test_counts_every_friendly_transition_into_hand(self):
        state = LogState()
        state.my_player_id = "1"
        state.add_entity("10", self._card("TEST_001", "1", "DECK"))

        update_state(state, self._zone_change("10", "HAND"))
        update_state(state, self._zone_change("10", "PLAY"))
        update_state(state, self._zone_change("10", "HAND"))
        update_state(state, self._zone_change("10", "HAND"))

        self.assertEqual(2, state.hand_entry_count)

    def test_does_not_count_enemy_hand_entries(self):
        state = LogState()
        state.my_player_id = "1"
        state.add_entity("20", self._card("TEST_002", "2", "DECK"))

        update_state(state, self._zone_change("20", "HAND"))

        self.assertEqual(0, state.hand_entry_count)

    def test_counts_when_controller_becomes_known_after_zone(self):
        state = LogState()
        state.my_player_id = "1"
        state.add_entity("30", self._card("TEST_003", "0", "HAND"))

        update_state(state, LineInfoContainer(
            LOG_LINE_TAG_CHANGE,
            entity="30",
            tag="CONTROLLER",
            value="1",
        ))

        self.assertEqual(1, state.hand_entry_count)

    def test_full_entity_tag_sequence_counts_entry(self):
        state = LogState()
        state.my_player_id = "1"
        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="40", card="TEST_004"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="HAND"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="1"))

        self.assertEqual(1, state.hand_entry_count)
        self.assertEqual("HAND", state.entity_dict["40"].zone)

    def test_entry_remains_counted_after_card_leaves_hand(self):
        state = LogState()
        state.my_player_id = "1"
        state.add_entity("50", self._card("TEST_005", "1", "DECK"))

        update_state(state, self._zone_change("50", "HAND"))
        update_state(state, self._zone_change("50", "PLAY"))

        self.assertEqual("PLAY", state.entity_dict["50"].zone)
        self.assertEqual(1, state.hand_entry_count)

    def test_strategy_state_exposes_cumulative_entry_count(self):
        my_entity = self._card("", "1", "PLAY")
        my_entity.set_tag("RESOURCES", "0")
        my_entity.set_tag("RESOURCES_USED", "0")
        my_entity.set_tag("TEMP_RESOURCES", "0")
        log_state = SimpleNamespace(
            hand_entry_count=7,
            is_end=False,
            is_my_turn=True,
            game_num_turns_in_play=3,
            my_entity=my_entity,
            discover_choice_count=None,
            entity_dict={},
        )

        strategy_state = StrategyState(log_state)

        self.assertEqual(7, strategy_state.hand_entry_count)


if __name__ == "__main__":
    unittest.main()
