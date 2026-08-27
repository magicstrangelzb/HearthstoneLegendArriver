"""Map display slots in HSAng instructions to identity-bound manual actions."""

from dataclasses import dataclass

from manual_controller import (
    AttackAction, DiscoverChoiceAction, EndTurnAction, HeroPowerAction,
    LaunchStarshipAction, PlayCardAction, Target, TradeCardAction,
    UseLocationAction,
)
from src.recommendation_models import ActionKind


class RecommendationStateError(ValueError):
    pass


@dataclass(frozen=True)
class BoardEntry:
    kind: str
    collection_index: int
    entity: object


@dataclass(frozen=True)
class AdaptedAction:
    manual_action: object
    source_entity_id: str | None
    target_entity_id: str | None
    postcondition: str


def ordered_board(state, side):
    minions = state.my_minions if side == "friendly" else state.oppo_minions
    locations = (getattr(state, "my_locations", []) if side == "friendly"
                 else getattr(state, "oppo_locations", []))
    entries = [BoardEntry("minion", i, entity)
               for i, entity in enumerate(minions)]
    entries.extend(BoardEntry("location", i, entity)
                   for i, entity in enumerate(locations))
    positions = [getattr(entry.entity, "zone_pos", 0) for entry in entries]
    if any(position <= 0 for position in positions):
        raise RecommendationStateError("board_position_unknown")
    if len(set(positions)) != len(positions):
        raise RecommendationStateError("duplicate_board_position")
    return tuple(sorted(entries, key=lambda entry: entry.entity.zone_pos))


def board_slot(state, side, one_based_index):
    board = ordered_board(state, side)
    if not 1 <= one_based_index <= len(board):
        raise RecommendationStateError("board_slot_out_of_range")
    return board[one_based_index - 1]


def adapt_action(proposed, state):
    if proposed.turn_number != state.game_num_turns_in_play:
        raise RecommendationStateError("turn_changed")
    if proposed.action == ActionKind.PLAY_CARD:
        return _adapt_play_card(proposed, state)
    if proposed.action == ActionKind.TRADE_CARD:
        return _adapt_trade_card(proposed, state)
    if proposed.action == ActionKind.USE_HERO_POWER:
        return _adapt_hero_power(proposed, state)
    if proposed.action == ActionKind.ATTACK:
        return _adapt_attack(proposed, state)
    if proposed.action == ActionKind.USE_LOCATION:
        entry = board_slot(state, "friendly", proposed.source.index)
        if entry.kind != "location":
            raise RecommendationStateError("source_not_location")
        location = entry.entity
        manual = UseLocationAction(
            entry.collection_index, location.card_id,
            getattr(location, "entity_id", None))
        return AdaptedAction(manual, getattr(location, "entity_id", None),
                             None, "location_changed")
    if proposed.action == ActionKind.CHOOSE_DISCOVER:
        choice_count = getattr(state, "discover_choice_count", None)
        if choice_count not in (1, 2, 3):
            raise RecommendationStateError("discover_choice_count_unavailable")
        if (proposed.source is None
                or proposed.source.kind != "discover_slot"
                or proposed.source.owner != "friendly"
                or not 1 <= proposed.source.index <= choice_count):
            raise RecommendationStateError("discover_slot_out_of_range")
        return AdaptedAction(
            DiscoverChoiceAction(
                proposed.source.index - 1, choice_count),
            None, None, "choice_resolved")
    if proposed.action == ActionKind.END_TURN:
        return AdaptedAction(EndTurnAction(), None, None, "turn_changed")
    raise RecommendationStateError("unsupported_action")


def _adapt_play_card(proposed, state):
    index = proposed.source.index - 1
    if not 0 <= index < len(state.my_hand_cards):
        raise RecommendationStateError("hand_slot_out_of_range")
    card = state.my_hand_cards[index]
    gap = None
    manual_target = None
    target_id = None
    if card.cardtype in {"MINION", "LOCATION"}:
        board_count = len(ordered_board(state, "friendly"))
        gap = (board_count if proposed.destination is None
               else proposed.destination.index - 1)
        if not 0 <= gap <= board_count:
            raise RecommendationStateError("minion_destination_out_of_range")
    if proposed.target is not None:
        if (card.card_id == "CATA_490"
                and proposed.target.owner == "friendly"
                and proposed.target.kind == "hand_slot"):
            target_index = proposed.target.index - 1
            manual_target = Target(
                "friendly", "hand", target_index)
        elif proposed.card_type == "SPELL":
            if proposed.target.owner not in {"friendly", "enemy"}:
                raise RecommendationStateError("spell_target_unsupported")
            if proposed.target.kind == "hero":
                hero = (getattr(state, "my_hero", None)
                        if proposed.target.owner == "friendly"
                        else getattr(state, "oppo_hero", None))
                if hero is None:
                    raise RecommendationStateError("spell_target_missing")
                target_id = getattr(hero, "entity_id", None)
                manual_target = Target(
                    proposed.target.owner, "hero", None, target_id)
            else:
                target = board_slot(
                    state, proposed.target.owner, proposed.target.index)
                if target.kind != "minion":
                    raise RecommendationStateError("target_not_minion")
                target_id = getattr(target.entity, "entity_id", None)
                manual_target = Target(
                    proposed.target.owner, "minion",
                    target.collection_index, target_id)
        else:
            raise RecommendationStateError("targeted_action_unsupported")
    manual = PlayCardAction(
        index, card.card_id, card.cardtype, gap_index=gap,
        target=manual_target,
        hand_entity_id=getattr(card, "entity_id", None))
    return AdaptedAction(
        manual, getattr(card, "entity_id", None), target_id,
                         "hand_card_left")


def _adapt_trade_card(proposed, state):
    index = proposed.source.index - 1
    if not 0 <= index < len(state.my_hand_cards):
        raise RecommendationStateError("hand_slot_out_of_range")
    card = state.my_hand_cards[index]
    entity_id = getattr(card, "entity_id", None)
    manual = TradeCardAction(
        index, card.card_id, card.cardtype, hand_entity_id=entity_id)
    return AdaptedAction(
        manual, entity_id, None, "hand_card_left")


def _adapt_hero_power(proposed, state):
    power = getattr(state, "my_hero_power", None)
    target = None
    target_id = None
    if proposed.target is not None:
        side = proposed.target.owner
        if side not in {"friendly", "enemy"}:
            raise RecommendationStateError("hero_power_target_unsupported")
        if proposed.target.kind == "hero":
            hero = (getattr(state, "my_hero", None)
                    if side == "friendly"
                    else getattr(state, "oppo_hero", None))
            if hero is None:
                raise RecommendationStateError("hero_power_target_missing")
            target_id = getattr(hero, "entity_id", None)
            target = Target(side, "hero", None, target_id)
        elif proposed.target.kind == "board_slot":
            entry = board_slot(state, side, proposed.target.index)
            if entry.kind != "minion":
                raise RecommendationStateError("target_not_minion")
            target_id = getattr(entry.entity, "entity_id", None)
            target = Target(
                side, "minion", entry.collection_index, target_id)
        else:
            raise RecommendationStateError("hero_power_target_unsupported")
    return AdaptedAction(
        HeroPowerAction(target=target),
        getattr(power, "entity_id", None), target_id,
        "hero_power_changed")


def _adapt_attack(proposed, state):
    if proposed.source.kind == "hero":
        hero = getattr(state, "my_hero", None)
        if hero is None:
            raise RecommendationStateError("friendly_hero_missing")
        source_target = Target(
            "friendly", "hero", None, getattr(hero, "entity_id", None))
        source_id = getattr(hero, "entity_id", None)
    else:
        source = board_slot(state, "friendly", proposed.source.index)
        if source.kind != "minion":
            raise RecommendationStateError("source_not_minion")
        source_id = getattr(source.entity, "entity_id", None)
        if source.entity.card_id == "SC_999t":
            manual = LaunchStarshipAction(
                source.collection_index,
                source.entity.card_id,
                source_id,
            )
            return AdaptedAction(
                manual, source_id, None, "starship_launched")
        source_target = Target(
            "friendly", "minion", source.collection_index, source_id)
    if proposed.target is None:
        raise RecommendationStateError("attack_target_required")
    if proposed.target.kind == "hero":
        hero = getattr(state, "oppo_hero", None)
        if hero is None:
            raise RecommendationStateError("enemy_hero_missing")
        target_id = getattr(hero, "entity_id", None)
        manual_target = Target("enemy", "hero", None, target_id)
    else:
        target = board_slot(state, "enemy", proposed.target.index)
        if target.kind != "minion":
            raise RecommendationStateError("target_not_minion")
        target_id = getattr(target.entity, "entity_id", None)
        manual_target = Target("enemy", "minion", target.collection_index,
                               target_id)
    postcondition = ("hero_combat_state_changed"
                     if proposed.source.kind == "hero"
                     else "combat_state_changed")
    return AdaptedAction(AttackAction(source_target, manual_target),
                         source_id, target_id, postcondition)
