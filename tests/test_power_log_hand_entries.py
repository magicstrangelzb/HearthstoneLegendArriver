import unittest
from types import SimpleNamespace

from constants.constants import (
    LOG_LINE_FULL_ENTITY,
    LOG_LINE_FULL_ENTITY_PLAYER,
    LOG_LINE_TAG,
    LOG_LINE_TAG_CHANGE,
)
from log_op import LineInfoContainer, parse_line
from log_state import CardEntity, LogState, PlayerEntity, update_state
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
            my_player_id="1",
            power_options={},
            entity_dict={},
        )

        strategy_state = StrategyState(log_state)

        self.assertEqual(7, strategy_state.hand_entry_count)

    # ---- 回溯/回合重置(GAME_RESET): FULL_ENTITY - Updating 整实体重同步 ----

    @staticmethod
    def _gs(body):
        # 还原完整日志行前缀(带毫秒与 GameState 前缀), 让 parse_line 能解析。
        return (f"D 00:30:08.1870240 GameState.DebugPrintPower() - "
                f"    {body}")

    def test_parse_line_reads_bracket_updating_as_full_entity(self):
        # 服务器在回溯重置里用 Updating 括号形态重定义已有卡牌实体, 旧解析器
        # 只认 "Creating" 而把它当 PARSE:NONE 丢弃 —— 这是手牌幻影的根因。
        c = parse_line(self._gs(
            "FULL_ENTITY - Updating [entityName=远古迅猛龙 id=121 "
            "zone=HAND zonePos=6 cardId=TLC_245 player=2] CardID=TIME_046"))

        self.assertEqual(LOG_LINE_FULL_ENTITY, c.line_type)
        self.assertEqual("121", c.info_dict["entity"])
        self.assertEqual("TIME_046", c.info_dict["card"])

    def test_parse_line_reads_player_updating(self):
        c = parse_line(self._gs("FULL_ENTITY - Updating YOURNAME CardID="))

        self.assertEqual(LOG_LINE_FULL_ENTITY_PLAYER, c.line_type)
        self.assertEqual("YOURNAME", c.info_dict["name"])

    def test_rewind_updating_recreates_card_and_drops_phantom_hand_entry(self):
        # 回溯把经战吼入手的手牌卡(id=121 远古迅猛龙)重定义为 SETASIDE 里的
        # 另一张候选(TIME_046): 整实体重建 -> 旧 ZONE=HAND 被清掉, 手牌不再
        # 多算这张幻影。
        state = LogState()
        state.my_player_id = "2"
        state.add_entity("121", self._card("TLC_245", "2", "HAND"))
        self.assertEqual(["121"], [e for e in state.entity_dict
                                   if state.entity_dict[e].zone == "HAND"])

        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="121", card="TIME_046"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="2"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="SETASIDE"))

        ent = state.entity_dict["121"]
        self.assertEqual("TIME_046", ent.card_id)
        self.assertEqual("SETASIDE", ent.zone)
        self.assertEqual([], [e for e in state.entity_dict
                              if state.entity_dict[e].zone == "HAND"])

    def test_rewind_updating_then_hand_reaffirm_readds_card(self):
        # 若重置后的 tag 块把 ZONE=HAND 重新声明(未被战吼顶掉的正常手牌),
        # 实体应重新进入手牌模型 —— 手牌成员由日志权威决定。
        state = LogState()
        state.my_player_id = "2"
        state.add_entity("54", self._card("JAIL_457", "2", "HAND"))

        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY, entity="54", card="JAIL_457"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="CONTROLLER", value="2"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE", value="HAND"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="ZONE_POSITION", value="1"))

        self.assertEqual("HAND", state.entity_dict["54"].zone)
        self.assertEqual("JAIL_457", state.entity_dict["54"].card_id)

    def test_player_updating_restores_mana_onto_player_entity(self):
        # GAME_RESET 里玩家实体用裸名 FULL_ENTITY - Updating 重新 dump
        # (RESOURCES/RESOURCES_USED/CURRENT_PLAYER 等): 需把 current_update_id
        # 指到正确的玩家实体, 否则法力恢复会写到别的实体上。
        state = LogState()
        state.my_player_id = "2"
        state.oppo_player_id = "1"
        my_entity = PlayerEntity()
        my_entity.set_tag("CARDTYPE", "PLAYER")
        my_entity.set_tag("CONTROLLER", "2")
        oppo_entity = PlayerEntity()
        oppo_entity.set_tag("CARDTYPE", "PLAYER")
        oppo_entity.set_tag("CONTROLLER", "1")
        state.add_player_entity("3", "2", my_entity)
        state.add_player_entity("2", "1", oppo_entity)
        state.my_name = "YOURNAME#1234"

        update_state(state, LineInfoContainer(
            LOG_LINE_FULL_ENTITY_PLAYER, name="YOURNAME"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="RESOURCES", value="5"))
        update_state(state, LineInfoContainer(
            LOG_LINE_TAG, tag="RESOURCES_USED", value="2"))

        self.assertEqual("5", state.entity_dict["3"].query_tag("RESOURCES"))
        self.assertEqual("2", state.entity_dict["3"].query_tag("RESOURCES_USED"))
        self.assertEqual("0", state.entity_dict["2"].query_tag("RESOURCES"))


if __name__ == "__main__":
    unittest.main()
