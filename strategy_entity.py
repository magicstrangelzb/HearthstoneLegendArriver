"""Typed game entities used by the manual controller."""

from abc import ABC, abstractmethod

from constants.constants import (
    CARD_HERO, CARD_HERO_POWER, CARD_LOCATION, CARD_MINION, CARD_SPELL,
    CARD_WEAPON,
)
from json_op import query_json_dict


class StrategyEntity(ABC):
    def __init__(self, entity_id, card_id, zone, zone_pos, current_cost, overload,
                 is_mine):
        self.entity_id = entity_id
        self.card_id = card_id
        self.zone = zone
        self.zone_pos = zone_pos
        self.current_cost = current_cost
        self.overload = overload
        self.is_mine = is_mine

    @property
    def name(self):
        return query_json_dict(self.card_id)

    @property
    def is_coin(self):
        return self.name == "幸运币"

    @property
    @abstractmethod
    def cardtype(self):
        raise NotImplementedError


class StrategyMinion(StrategyEntity):
    def __init__(
        self, entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine,
        attack, max_health, damage=0, taunt=0, divine_shield=0, stealth=0,
        windfury=0, poisonous=0, life_steal=0, spell_power=0, freeze=0,
        battlecry=0, not_targeted_by_spell=0, not_targeted_by_power=0,
        charge=0, rush=0, attackable_by_rush=0, frozen=0, dormant=0,
        untouchable=0, immune=0, cant_attack=0, exhausted=1,
        num_turns_in_play=1,
    ):
        super().__init__(
            entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine)
        self.attack = attack
        self.max_health = max_health
        self.damage = damage
        self.taunt = taunt
        self.divine_shield = divine_shield
        self.stealth = stealth
        self.windfury = windfury
        self.poisonous = poisonous
        self.life_steal = life_steal
        self.spell_power = spell_power
        self.freeze = freeze
        self.battlecry = battlecry
        self.not_targeted_by_spell = not_targeted_by_spell
        self.not_targeted_by_power = not_targeted_by_power
        self.charge = charge
        self.rush = rush
        self.attackable_by_rush = attackable_by_rush
        self.frozen = frozen
        self.dormant = dormant
        self.untouchable = untouchable
        self.immune = immune
        self.cant_attack = cant_attack
        self.exhausted = exhausted
        self.num_turns_in_play = num_turns_in_play

    @property
    def cardtype(self):
        return CARD_MINION

    @property
    def health(self):
        return self.max_health - self.damage

    @property
    def can_beat_face(self):
        return (self.attack > 0 and not self.dormant and not self.frozen
                and not self.cant_attack and self.exhausted == 0)

    @property
    def can_attack_minion(self):
        return (self.attack > 0 and not self.dormant and not self.frozen
                and not self.cant_attack
                and (self.exhausted == 0 or self.attackable_by_rush))

    @property
    def can_be_attacked(self):
        return not self.stealth and not self.immune and not self.dormant

    def __str__(self):
        flags = []
        for enabled, label in (
            (self.taunt, "嘲讽"), (self.divine_shield, "圣盾"),
            (self.stealth, "潜行"), (self.frozen, "冻结"),
            (self.dormant, "休眠"), (self.immune, "免疫"),
        ):
            if enabled:
                flags.append(label)
        suffix = " " + "/".join(flags) if flags else ""
        return f"{self.name} {self.attack}/{self.health}{suffix}"


class StrategyWeapon(StrategyEntity):
    def __init__(self, entity_id, card_id, zone, zone_pos, current_cost, overload,
                 is_mine, attack, durability, damage=0, windfury=0):
        super().__init__(
            entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine)
        self.attack = attack
        self.durability = durability
        self.damage = damage
        self.windfury = windfury

    @property
    def cardtype(self):
        return CARD_WEAPON

    @property
    def health(self):
        return self.durability - self.damage

    def __str__(self):
        return f"{self.name} {self.attack}/{self.health}"


class StrategyHero(StrategyEntity):
    def __init__(
        self, entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine,
        max_health, damage=0, stealth=0, immune=0,
        not_targeted_by_spell=0, not_targeted_by_power=0, armor=0,
        attack=0, exhausted=1, frozen=0, cant_attack=0,
    ):
        super().__init__(
            entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine)
        self.max_health = max_health
        self.damage = damage
        self.stealth = stealth
        self.immune = immune
        self.not_targeted_by_spell = not_targeted_by_spell
        self.not_targeted_by_power = not_targeted_by_power
        self.armor = armor
        self.attack = attack
        self.exhausted = exhausted
        self.frozen = frozen
        self.cant_attack = cant_attack

    @property
    def cardtype(self):
        return CARD_HERO

    @property
    def health(self):
        return self.max_health + self.armor - self.damage

    @property
    def can_attack(self):
        return (self.attack > 0 and not self.exhausted
                and not self.frozen and not self.cant_attack)

    @property
    def can_be_attacked(self):
        return not self.stealth and not self.immune

    def __str__(self):
        return f"{self.name} {self.attack}/{self.health}"


class StrategySpell(StrategyEntity):
    @property
    def cardtype(self):
        return CARD_SPELL


class StrategyLocation(StrategyEntity):
    def __init__(
        self, entity_id, card_id, zone, zone_pos, current_cost, overload,
        is_mine, max_health=0, damage=0, action_cooldown=0, exhausted=0,
        just_played=0,
    ):
        super().__init__(
            entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine)
        self.max_health = max_health
        self.damage = damage
        self.action_cooldown = action_cooldown
        self.exhausted = exhausted
        self.just_played = just_played

    @property
    def cardtype(self):
        return CARD_LOCATION

    @property
    def durability(self):
        return max(0, self.max_health - self.damage)

    @property
    def can_activate(self):
        return (self.zone == "PLAY" and self.durability > 0
                and not self.action_cooldown and not self.exhausted)

    def __str__(self):
        status = "可使用" if self.can_activate else "冷却中"
        return f"{self.name} 耐久: {self.durability}/{self.max_health} {status}"


class StrategyHeroPower(StrategyEntity):
    def __init__(self, entity_id, card_id, zone, zone_pos, current_cost, overload,
                 is_mine, exhausted):
        super().__init__(
            entity_id, card_id, zone, zone_pos, current_cost, overload, is_mine)
        self.exhausted = exhausted

    @property
    def cardtype(self):
        return CARD_HERO_POWER
