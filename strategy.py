"""Build a user-facing game snapshot from parsed Power.log state."""

from constants.constants import DEBUG_PRINT
from print_info import debug_print


class StrategyState:
    def __init__(self, log_state):
        self.is_end = log_state.is_end
        self.is_my_turn = log_state.is_my_turn
        self.game_num_turns_in_play = log_state.game_num_turns_in_play
        self.my_total_mana = int(log_state.my_entity.query_tag("RESOURCES"))
        self.my_used_mana = int(log_state.my_entity.query_tag("RESOURCES_USED"))
        self.my_temp_mana = int(log_state.my_entity.query_tag("TEMP_RESOURCES"))

        self.oppo_minions = []
        self.oppo_graveyard = []
        self.my_minions = []
        self.my_locations = []
        self.oppo_locations = []
        self.my_hand_cards = []
        self.my_graveyard = []
        self.my_hero = None
        self.my_hero_power = None
        self.my_weapon = None
        self.oppo_hero = None
        self.oppo_hero_power = None
        self.oppo_weapon = None
        self.oppo_hand_card_num = 0
        self.discover_choice_count = log_state.discover_choice_count
        self.hand_entry_count = log_state.hand_entry_count

        for entity_id, entity in log_state.entity_dict.items():
            if not hasattr(entity, "generate_strategy_entity"):
                continue
            if entity.query_tag("ZONE") == "HAND":
                if log_state.is_my_entity(entity):
                    converted = entity.generate_strategy_entity(
                        log_state, entity_id)
                    if converted is not None:
                        self.my_hand_cards.append(converted)
                else:
                    self.oppo_hand_card_num += 1
            elif entity.query_tag("ZONE") == "PLAY":
                converted = entity.generate_strategy_entity(
                    log_state, entity_id)
                if converted is None:
                    continue
                mine = log_state.is_my_entity(entity)
                if entity.cardtype == "MINION":
                    (self.my_minions if mine else self.oppo_minions).append(converted)
                elif entity.cardtype == "HERO":
                    if mine:
                        self.my_hero = converted
                    else:
                        self.oppo_hero = converted
                elif entity.cardtype == "HERO_POWER":
                    if mine:
                        self.my_hero_power = converted
                    else:
                        self.oppo_hero_power = converted
                elif entity.cardtype == "WEAPON":
                    if mine:
                        self.my_weapon = converted
                    else:
                        self.oppo_weapon = converted
                elif entity.cardtype == "LOCATION":
                    (self.my_locations if mine
                     else self.oppo_locations).append(converted)
            elif entity.query_tag("ZONE") == "GRAVEYARD":
                (self.my_graveyard if log_state.is_my_entity(entity)
                 else self.oppo_graveyard).append(entity)

        self.my_minions.sort(key=lambda item: item.zone_pos)
        self.oppo_minions.sort(key=lambda item: item.zone_pos)
        self.my_hand_cards.sort(key=lambda item: item.zone_pos)
        self.my_locations.sort(key=lambda item: item.zone_pos)
        self.oppo_locations.sort(key=lambda item: item.zone_pos)

    @property
    def my_last_mana(self):
        return self.my_total_mana - self.my_used_mana + self.my_temp_mana

    @property
    def my_minion_num(self):
        return len(self.my_minions)

    @property
    def oppo_minion_num(self):
        return len(self.oppo_minions)

    @property
    def my_board_slot_num(self):
        return len(self.my_minions) + len(self.my_locations)

    @property
    def oppo_board_slot_num(self):
        return len(self.oppo_minions) + len(self.oppo_locations)

    @property
    def my_hand_card_num(self):
        return len(self.my_hand_cards)

    @staticmethod
    def _hero_line(hero):
        if hero is None:
            return "未知"
        return f"{hero.name} 生命/护甲: {hero.health - hero.armor}+{hero.armor}"

    @staticmethod
    def _weapon_line(weapon):
        return "无" if weapon is None else str(weapon)

    def format_for_manual_control(self):
        lines = [
            "=== 当前战局 ===",
            f"回合: {self.game_num_turns_in_play}  法力: {self.my_last_mana}/{self.my_total_mana}",
            f"己方英雄: {self._hero_line(self.my_hero)}",
            f"己方武器: {self._weapon_line(self.my_weapon)}",
            "己方随从:",
        ]
        if self.my_minions:
            lines.extend(f"  [{i}] {minion}" for i, minion in enumerate(self.my_minions, 1))
        else:
            lines.append("  无")
        if self.my_locations:
            lines.append("己方地标:")
            lines.extend(
                f"  [{i}] {location}"
                for i, location in enumerate(self.my_locations, 1))
        lines.extend([
            f"敌方英雄: {self._hero_line(self.oppo_hero)}",
            f"敌方武器: {self._weapon_line(self.oppo_weapon)}",
            "敌方随从:",
        ])
        if self.oppo_minions:
            lines.extend(f"  [{i}] {minion}" for i, minion in enumerate(self.oppo_minions, 1))
        else:
            lines.append("  无")
        if self.oppo_locations:
            lines.append("敌方地标:")
            lines.extend(
                f"  [{i}] {location}"
                for i, location in enumerate(self.oppo_locations, 1))
        lines.append("手牌:")
        if self.my_hand_cards:
            lines.extend(
                f"  [{i}] {card.name} 类型: {card.cardtype} 费用: {card.current_cost}"
                for i, card in enumerate(self.my_hand_cards, 1)
            )
        else:
            lines.append("  无")
        return "\n".join(lines)

    def debug_print_out(self):
        if DEBUG_PRINT:
            for line in self.format_for_manual_control().splitlines():
                debug_print(line)
