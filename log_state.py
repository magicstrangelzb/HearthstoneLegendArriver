import sys

from log_op import *
from json_op import *
from strategy_entity import *
from print_info import *
import constants.constants

MY_NAME = constants.constants.YOUR_NAME
# 进程级：my_player_id 自愈交换的警告只打一次，避免读取历史日志刷屏。
_MY_PLAYER_SWAP_WARNED = False


def check_name():
    global MY_NAME
    if MY_NAME == "ChangeThis#54321":
        MY_NAME = input("请输入你的炉石用户名, 例子: \"为所欲为、异灵术#54321\" (不用输入引号!)\n").strip()


# 日志里用来表示“名字不可见”的占位名：不能拿它当玩家真名来比对。
_UNKNOWN_PLAYER_MARKERS = ("UNKNOWN",)


def player_name_check(state, my_name=None):
    """比对日志里的玩家名与配置的用户 ID，返回 None（暂无法判断）或判定结果。

    返回形如 ``{"config": 配置值, "players": {PlayerID: 名字}, "matched": bool}``。

    为什么要做这个校验：脚本完全靠昵称区分敌我（`MY_NAME in entity_string`），
    换战网账号后若忘记改「用户 ID」，整局都会被判成对手回合而不出牌，且日志里
    看不出任何异常。这里在拿到双方玩家名后比对一次，不匹配就交给上层提示。

    判定规则（保守，宁可不提示也不误报）：
      * 必须已经读到两个 PlayerID 的玩家名（日志成对给出，只读到一条时无法判断）；
      * 至少有一个名字是真实昵称（"UNKNOWN HUMAN PLAYER" 这类占位名不算）；
      * 配置昵称出现在任一真实昵称里即视为匹配（与解析层 `in` 的语义一致）。
    """
    name = (MY_NAME if my_name is None else my_name) or ""
    name = str(name).strip()
    players = {str(pid): str(n).strip()
               for pid, n in getattr(state, "player_names", {}).items()
               if str(n).strip()}
    if len(players) < 2:
        return None
    real_names = [n for n in players.values()
                  if not any(marker in n.upper()
                             for marker in _UNKNOWN_PLAYER_MARKERS)]
    if not real_names:
        return None
    target = name.lower()
    matched = bool(target) and any(target in n.lower() for n in real_names)
    return {"config": name, "players": players, "matched": matched}


class LogState:
    def __init__(self):
        self.session_id = "unknown-session"
        self.game_entity_id = 0
        self.player_id_map_dict = {}
        self.my_name = ""
        self.oppo_name = ""
        # PlayerID -> 日志里给出的完整玩家名（"xxx#12345"）。用于校验配置的
        # 「用户 ID」是否与当前账号一致（换号后忘改昵称会整局不出牌）。
        self.player_names = {}
        self.my_player_id = "0"
        self.oppo_player_id = "0"
        self.entity_dict = {}
        self.current_update_id = 0
        self.revision = 0
        self.game_generation = 0
        self.general_choice_id = None
        self.general_choice_player = None
        self.general_choice_indexes = set()
        self.general_choice_ready = False
        self.power_options = {}
        self.current_power_option_id = None
        # 手牌入口计数（上游逻辑：用于手牌动画延迟判断）。
        self.hand_entry_count = 0
        # 开局生效的全局卡数量（BLOCK_START TRIGGER + START_OF_GAME_KEYWORD
        # + cardId 非空）。第一回合额外延时按此计数（每张 +2s，可配置）。
        self.start_of_game_card_count = 0
        # 本局是否已警告过 my_player_id 交换（flush 每局重置，避免刷屏）。
        self._swap_warned = False

    def __str__(self):
        res = \
            f"""GameState:
    game_entity_id: {self.game_entity_id}
    my_name: {self.my_name}
    oppo_name: {self.oppo_name}
    my_player_id: {self.my_player_id}
    oppo_player_id: {self.oppo_player_id}
    current_update_id: {self.current_update_id}
    entity_keys: {[list(self.entity_dict.keys())]}

"""
        key_list = list(self.entity_dict.keys())
        key_list.sort(key=int)

        for key in key_list:
            if key == self.game_entity_id:
                res += "GameState-"
            elif key == self.my_entity_id:
                res += "MyEntity-"
            elif key == self.oppo_entity_id:
                res += "OppoEntity-"
            res += f"[{str(key)}]\n"
            res += str(self.entity_dict[key])
            res += "\n"

        return res

    @property
    def is_end(self):
        return self.game_state == "COMPLETE"

    @property
    def current_update_entity(self):
        return self.entity_dict[self.current_update_id]

    @property
    def game_entity(self):
        return self.entity_dict[self.game_entity_id]

    @property
    # 这不是my_player_id, 而是指代表我这个玩家的那个entity的index
    def my_entity_id(self):
        return self.player_id_map_dict.get(self.my_player_id, 0)

    @property
    def my_entity(self):
        return self.entity_dict[self.my_entity_id]

    @property
    def oppo_entity_id(self):
        return self.player_id_map_dict.get(self.oppo_player_id, 0)

    @property
    def oppo_entity(self):
        return self.entity_dict[self.oppo_entity_id]

    @property
    def is_my_turn(self):
        return self.my_entity.query_tag("CURRENT_PLAYER") == "1"

    @property
    def my_last_mana(self):
        return self.my_entity.query_tag("RESOURCES") - \
               self.my_entity.query_tag("RESOURCES_USED")

    @property
    def game_step(self):
        return self.game_entity.query_tag("STEP")

    @property
    def game_state(self):
        return self.game_entity.query_tag("STATE")

    @property
    def game_num_turns_in_play(self):
        return int(self.game_entity.query_tag("NUM_TURNS_IN_PLAY"))

    @property
    def available(self):
        return self.game_entity_id != 0

    @property
    def discover_choice_count(self):
        if (not self.general_choice_ready
                or self.general_choice_player != self.my_player_id):
            return None
        count = len(self.general_choice_indexes)
        return count if 1 <= count <= 4 else None

    def clear_general_choice(self):
        self.general_choice_id = None
        self.general_choice_player = None
        self.general_choice_indexes = set()
        self.general_choice_ready = False

    def flush(self):
        revision = self.revision
        game_generation = self.game_generation + 1
        session_id = self.session_id
        self.__init__()
        self.revision = revision
        self.game_generation = game_generation
        self.session_id = session_id

    def add_entity(self, entity_id, entity):
        assert entity_id.isdigit()
        self.entity_dict[entity_id] = entity

    def set_game_entity(self, game_entity_id, game_entity):
        self.game_entity_id = game_entity_id
        self.add_entity(game_entity_id, game_entity)

    def fetch_game_entity(self):
        return self.entity_dict[self.game_entity_id]

    def add_player_entity(self, player_entity_id, player_id, player_entity):
        self.add_entity(player_entity_id, player_entity)
        self.player_id_map_dict[player_id] = player_entity_id

    def is_my_entity(self, entity):
        return entity.query_tag("CONTROLLER") == self.my_player_id


class Entity:
    def __init__(self):
        self.tag_dict = {}

    def __str__(self):
        res = ""
        for key, value in self.tag_dict.items():
            res += f"\t{key}: {value}\n"
        return res

    def set_tag(self, tag, val):
        self.tag_dict[tag] = val

    def query_tag(self, tag, default_val="0"):
        return self.tag_dict.get(tag, default_val)

    @property
    def cardtype(self):
        return self.query_tag("CARDTYPE")

    @property
    def zone(self):
        return self.query_tag("ZONE")


class GameEntity(Entity):
    pass


class PlayerEntity(Entity):
    pass


class CardEntity(Entity):
    def __init__(self, card_id):
        super().__init__()
        self.card_id = card_id

    def __str__(self):
        return "cardID: " + self.card_id + "\n" + \
               "name: " + self.name + "\n" + \
               super().__str__()

    def generate_strategy_entity(self, log_state, entity_id=None):
        if self.cardtype == "MINION":
            return StrategyMinion(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POSITION")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
                attack=int(self.query_tag("ATK")),
                max_health=int(self.query_tag("HEALTH")),
                damage=int(self.query_tag("DAMAGE")),
                taunt=int(self.query_tag("TAUNT")),
                divine_shield=int(self.query_tag("DIVINE_SHIELD")),
                stealth=int(self.query_tag("STEALTH")),
                windfury=int(self.query_tag("WINDFURY")),
                poisonous=int(self.query_tag("POISONOUS")),
                freeze=int(self.query_tag("FREEZE")),
                battlecry=int(self.query_tag("BATTLECRY")),
                spell_power=int(self.query_tag("SPELLPOWER")),
                not_targeted_by_spell=int(self.query_tag("CANT_BE_TARGETED_BY_SPELLS")),
                not_targeted_by_power=int(self.query_tag("CANT_BE_TARGETED_BY_HERO_POWERS")),
                charge=int(self.query_tag("CHARGE")),
                rush=int(self.query_tag("RUSH")),
                attackable_by_rush=int(self.query_tag("ATTACKABLE_BY_RUSH")),
                frozen=int(self.query_tag("FROZEN")),
                dormant=int(self.query_tag("DORMANT")),
                untouchable=int(self.query_tag("UNTOUCHABLE")),
                immune=int(self.query_tag("IMMUNE")),
                # -1代表标签缺失, 有两种情况会产生-1: 断线重连; 卡刚从手牌中被打出来
                exhausted=int(self.query_tag("EXHAUSTED")),
                cant_attack=int(self.query_tag("CANT_ATTACK")),
                num_turns_in_play=int(self.query_tag("NUM_TURNS_IN_PLAY")),
            )
        elif self.cardtype == "SPELL":
            return StrategySpell(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POSITION")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
            )
        elif self.cardtype == "WEAPON":
            return StrategyWeapon(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POSITION")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
                attack=int(self.query_tag("ATK")),
                durability=int(self.query_tag("DURABILITY")),
                damage=int(self.query_tag("DAMAGE")),
                windfury=int(self.query_tag("WINDFURY")),
            )
        elif self.cardtype == "LOCATION":
            return StrategyLocation(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POSITION")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
                max_health=int(self.query_tag("HEALTH")),
                damage=int(self.query_tag("DAMAGE")),
                action_cooldown=int(
                    self.query_tag("LOCATION_ACTION_COOLDOWN")),
                exhausted=int(self.query_tag("EXHAUSTED")),
                just_played=int(self.query_tag("JUST_PLAYED")),
            )
        elif self.cardtype == "HERO":
            return StrategyHero(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POS")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
                max_health=int(self.query_tag("HEALTH")),
                damage=int(self.query_tag("DAMAGE")),
                stealth=int(self.query_tag("STEALTH")),
                immune=int(self.query_tag("IMMUNE")),
                not_targeted_by_spell=int(self.query_tag("CANT_BE_TARGETED_BY_SPELLS")),
                not_targeted_by_power=int(self.query_tag("CANT_BE_TARGETED_BY_HERO_POWERS")),
                armor=int(self.query_tag("ARMOR")),
                attack=int(self.query_tag("ATK")),
                exhausted=int(self.query_tag("EXHAUSTED")),
                frozen=int(self.query_tag("FROZEN")),
                cant_attack=int(self.query_tag("CANT_ATTACK")),
            )
        elif self.cardtype == "HERO_POWER":
            return StrategyHeroPower(
                entity_id=entity_id,
                card_id=self.card_id,
                zone=self.query_tag("ZONE"),
                zone_pos=int(self.query_tag("ZONE_POS")),
                current_cost=int(self.query_tag("TAG_LAST_KNOWN_COST_IN_HAND")),
                overload=int(self.query_tag("OVERLOAD")),
                is_mine=log_state.is_my_entity(self),
                exhausted=int(self.query_tag("EXHAUSTED")),
            )
        else:
            return None

    @property
    def name(self):
        return query_json_dict(self.card_id)

    def update_card_id(self, card_id):
        self.card_id = card_id


def update_state(state, line_info_container):
    if line_info_container.line_type == LOG_LINE_CREATE_GAME:
        # sys_print("Read in new game and flush state")
        state.flush()

    if line_info_container.line_type == LOG_LINE_BLOCK_START_TRIGGER:
        # 一张开局生效的全局卡事件。Entity 括号里 cardId 可能当时为空，
        # 但实体 id 已在 SHOW_ENTITY 时注册过 card_id。据此解析真实卡牌，
        # 只有 card_id 非空才计为一张生效卡（排除无卡牌的通用机制触发）。
        entity = state.entity_dict.get(
            line_info_container.info_dict["entity_id"])
        card_id = getattr(entity, "card_id", "") if entity is not None else ""
        if card_id:
            state.start_of_game_card_count += 1

    if line_info_container.line_type == LOG_LINE_POWER_OPTIONS_START:
        state.power_options = {}
        state.current_power_option_id = None

    if line_info_container.line_type == LOG_LINE_POWER_OPTION:
        info = line_info_container.info_dict
        state.current_power_option_id = info.get("entity_id")
        if state.current_power_option_id is not None:
            state.power_options[state.current_power_option_id] = {
                **info, "choices": {}}

    if line_info_container.line_type == LOG_LINE_POWER_SUBOPTION:
        parent = state.power_options.get(state.current_power_option_id)
        if parent is not None:
            info = line_info_container.info_dict
            parent["choices"][info["index"]] = dict(info)

    if line_info_container.line_type == LOG_LINE_GENERAL_CHOICE_START:
        state.clear_general_choice()
        state.general_choice_id = line_info_container.info_dict["choice_id"]

    if line_info_container.line_type == LOG_LINE_GENERAL_CHOICE_ENTITY:
        if state.general_choice_id is not None:
            player = line_info_container.info_dict["player"]
            if state.general_choice_player in (None, player):
                state.general_choice_player = player
                state.general_choice_indexes.add(
                    line_info_container.info_dict["index"])
            else:
                state.clear_general_choice()

    if line_info_container.line_type == LOG_LINE_GENERAL_CHOICE_READY:
        if (state.general_choice_id
                == line_info_container.info_dict["choice_id"]):
            state.general_choice_ready = True

    if line_info_container.line_type == LOG_LINE_GENERAL_CHOICE_RESOLVED:
        if (state.general_choice_id
                == line_info_container.info_dict["choice_id"]):
            state.clear_general_choice()

    if line_info_container.line_type == LOG_LINE_GAME_ENTITY:
        game_entity = GameEntity()
        game_entity_id = line_info_container.info_dict["entity"]

        state.current_update_id = game_entity_id
        state.add_entity(game_entity_id, game_entity)
        state.game_entity_id = game_entity_id

    if line_info_container.line_type == LOG_LINE_PLAYER_ENTITY:
        player_entity = PlayerEntity()
        player_entity_id = line_info_container.info_dict["entity"]
        player_id = line_info_container.info_dict["player"]

        state.current_update_id = player_entity_id
        state.add_player_entity(player_entity_id, player_id, player_entity)

    if line_info_container.line_type == LOG_LINE_FULL_ENTITY:
        card_id = line_info_container.info_dict["card"]
        card_entity_id = line_info_container.info_dict["entity"]
        card_entity = CardEntity(card_id)

        state.current_update_id = card_entity_id
        state.add_entity(card_entity_id, card_entity)

    if line_info_container.line_type == LOG_LINE_FULL_ENTITY_PLAYER:
        # GAME_RESET(回溯)里玩家实体也是 FULL_ENTITY - Updating <裸名> CardID=,
        # 且不带括号 -> parse_line 解析不到实体 id。玩家实体本身不用重建(开局已由
        # PLAYER 行注册), 只需把 current_update_id 指向该玩家, 让它紧随的 tag= 块
        # (RESOURCES/RESOURCES_USED/CURRENT_PLAYER 等)能写到正确的玩家实体上。
        player_name = line_info_container.info_dict["name"]
        bare = player_name.split('#')[0].strip()
        entity_id = None
        if bare:
            for cand in (MY_NAME, state.my_name):
                if cand and cand.split('#')[0].strip() == bare:
                    entity_id = state.my_entity_id
                    break
            if entity_id is None:
                entity_id = state.oppo_entity_id
        if entity_id in state.entity_dict:
            state.current_update_id = entity_id

    if line_info_container.line_type == LOG_LINE_SHOW_ENTITY:
        card_id = line_info_container.info_dict["card"]
        card_entity_id = line_info_container.info_dict["entity"]

        card_entity = state.entity_dict[card_entity_id]
        card_entity.update_card_id(card_id)
        state.current_update_id = card_entity_id

    if line_info_container.line_type == LOG_LINE_CHANGE_ENTITY:
        card_id = line_info_container.info_dict["card"]
        card_entity_id = line_info_container.info_dict["entity"]

        card_entity = state.entity_dict[card_entity_id]
        card_entity.update_card_id(card_id)
        state.current_update_id = card_entity_id

    if line_info_container.line_type == LOG_LINE_TAG_CHANGE:
        entity_string = line_info_container.info_dict["entity"]

        # 情形一 "TAG_CHANGE Entity=GameEntity"
        if entity_string == "GameEntity":
            entity_id = state.game_entity_id

        # 情形二 "TAG_CHANGE Entity=Example#51234"
        elif not entity_string.isdigit():
            # 关于为什么用 "in" 而非 "==", 因为我总是懒得输入后面的数字
            if MY_NAME in entity_string:
                entity_id = state.my_entity_id
                if entity_string != state.my_name:
                    state.my_name = entity_string
            else:
                entity_id = state.oppo_entity_id
                if entity_string != state.oppo_name:
                    state.oppo_name = entity_string

            assert int(entity_id) <= 3

        # 情形三 "TAG_CHANGE Entity=[entityName=UNKNOWN ENTITY [cardType=INVALID] id=14 ...]"
        # 此时的EntityId已经被提取出来了
        else:
            entity_id = entity_string

        if entity_id not in state.entity_dict:
            warn_print(f"Invalid entity_id: {entity_id}")
            warn_print(f"Current line container: {line_info_container}")
            return False

        tag = line_info_container.info_dict["tag"]
        value = line_info_container.info_dict["value"]

        entity = state.entity_dict[entity_id]
        _record_friendly_hand_entry(state, entity, tag, value)
        entity.set_tag(tag, value)

    if line_info_container.line_type == LOG_LINE_TAG:
        tag = line_info_container.info_dict["tag"]
        value = line_info_container.info_dict["value"]

        # 在对战的一开始的时候, 对手的任何牌对你都是不可见的.
        # 故而在日志中发现的第一个不是英雄, 不是英雄技能, 而且
        # 你知道它的 CardID 的 Entity 一定是你的牌. 利用这
        # 张牌确定双方的 PlayerID
        if state.my_player_id == "0":
            if tag == "CARDTYPE" \
               and value not in ["HERO", "HERO_POWER", "PLAYER", "GAME"] \
               and "CONTROLLER" in state.current_update_entity.tag_dict:
                state.my_player_id = state.current_update_entity.query_tag("CONTROLLER")
                # 双方PlayerID, 一个是1, 一个是2
                state.oppo_player_id = str(3 - int(state.my_player_id))
                # debug_print(f"my_player_id: {state.my_player_id}")

        entity = state.current_update_entity
        _record_friendly_hand_entry(state, entity, tag, value)
        entity.set_tag(tag, value)

    if line_info_container.line_type == LOG_LINE_PLAYER_ID:
        player_id = line_info_container.info_dict["player"]
        player_name = line_info_container.info_dict["name"]
        if player_name:
            state.player_names[str(player_id)] = player_name.strip()

        # 我发现用这里的信息很不靠谱, 正常情况下的两个player_name
        # 应该对手的是"UNKNOWN HUMAN PLAYER", 你的是自己的用户名,
        # 但有时两个都是"UNKNOWN HUMAN PLAYER", 有时又都是已知.
        # 所以只拿来做校验

        # 下面这种情况明显是发生了错误. 一般会出现在在对战过程中关闭炉石
        # 再重新启动炉石. 此时在构建过程中看到的第一个确切的卡可能是对手
        # 场上的怪而非我自己的手牌, 进而误判 my_player_id
        if MY_NAME and MY_NAME in player_name:
            # 日志明确给出“我”的名字：直接确立己方 PlayerID，
            # 不再依赖“第一张卡”的 CONTROLLER 推断（那会因先读到对手怪而反）。
            try:
                my_pid = str(player_id)
                oppo_pid = str(3 - int(my_pid))
            except (TypeError, ValueError):
                my_pid = oppo_pid = None
            if my_pid and (state.my_player_id != my_pid
                           or state.oppo_player_id != oppo_pid):
                state.my_player_id = my_pid
                state.oppo_player_id = oppo_pid
        elif state.my_player_id != "0" and \
                player_id == state.oppo_player_id and MY_NAME in player_name:
            global _MY_PLAYER_SWAP_WARNED
            # 兜底：名字没能用于确立（如名字不含我），但发现“对手位是我名字”时交换。
            if not _MY_PLAYER_SWAP_WARNED:
                warn_print("my_player_id may be wrong")
                _MY_PLAYER_SWAP_WARNED = True
            state.my_player_id, state.oppo_player_id = \
                state.oppo_player_id, state.my_player_id

    state.revision += 1
    return True


def _record_friendly_hand_entry(state, entity, tag, value):
    """Count transitions into the known-friendly HAND state."""
    if state.my_player_id == "0":
        return
    old_zone = entity.query_tag("ZONE")
    old_controller = entity.query_tag("CONTROLLER")
    new_zone = value if tag == "ZONE" else old_zone
    new_controller = value if tag == "CONTROLLER" else old_controller
    was_friendly_hand = (
        old_zone == "HAND" and old_controller == state.my_player_id)
    is_friendly_hand = (
        new_zone == "HAND" and new_controller == state.my_player_id)
    if is_friendly_hand and not was_friendly_hand:
        state.hand_entry_count += 1


if __name__ == "__main__":
    log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
    log_container = next(log_iter)
    temp_state = LogState()

    for x in log_container.message_list:
        # print(x)
        update_state(temp_state, x)
    print(temp_state)
