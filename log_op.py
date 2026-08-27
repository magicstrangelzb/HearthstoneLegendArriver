import re
import time
import copy
from pathlib import Path
from config import LOG_TAIL_WAIT_INTERVAL
from constants.constants import *
from power_log import find_latest_power_log

# 读到 Power.log 尾部（EOF）后等待新行的时间。原来每次 0.2 秒、连续两次
# EOF 才返回，导致 update_log_state 静止时每轮阻塞 0.4 秒；缩短后主循环
# 对日志的响应延迟大幅下降，空轮询的 CPU 开销仍然很低。
# （定义于 config.py，可经 HS_LOG_TAIL_WAIT_INTERVAL 覆盖。）

# "D 04:23:18.0000001 GameState.DebugPrintPower() -     GameEntity EntityID=1"
# 只解析 GameState 前缀。Power.log 里还有 PowerTaskList 前缀的"复述"行，
# 那是任务执行的重复视图，若也作为状态源会把实体的 CONTROLLER/ZONE 等
# 改错（实测把友方手牌 CONTROLLER 从 1 改成 2，导致 my_hand_cards 少算）。
GAME_STATE_PATTERN = re.compile(
    r"D [\d]{2}:[\d]{2}:[\d]{2}.[\d]{7} "
    r"GameState\.DebugPrint(Game|Power)\(\) - (.+)")

# "GameEntity EntityID=1"
GAME_ENTITY_PATTERN = re.compile(r" *GameEntity EntityID=(\d+)")

# "Player EntityID=2 PlayerID=1 GameAccountId=[hi=112233445566778899 lo=223344556]"
PLAYER_PATTERN = re.compile(r" *Player EntityID=(\d+) PlayerID=(\d+).*")

# "FULL_ENTITY - Creting ID=89 CardID=EX1_538t"
# "FULL_ENTITY - Creating ID=90 CardID="
FULL_ENTITY_PATTERN = re.compile(r" *FULL_ENTITY - Creating ID=(\d+) CardID=(.*)")

# "SHOW_ENTITY - Updating Entity=90 CardID=NEW1_033o"
# "SHOW_ENTITY - Updating Entity=[entityName=UNKNOWN ENTITY [cardType=INVALID] id=32 zone=DECK zonePos=0 cardId= player=1] CardID=VAN_EX1_539"
SHOW_ENTITY_PATTERN = re.compile(r" *SHOW_ENTITY - Updating Entity=(.*) CardID=(.*) *")

# CHANGE_ENTITY 比较罕见，主要对应“呱”等变形行为
# "CHANGE_ENTITY - Updating Entity=[entityName=凯恩·血蹄 id=37 zone=PLAY zonePos=3 cardId=VAN_EX1_110 player=2] CardID=hexfrog"
CHANGE_ENTITY_PATTERN = re.compile(r" *CHANGE_ENTITY - Updating Entity=(.*) CardID=(.*) *")

# "BLOCK_START BlockType=DEATHS Entity=GameEntity EffectCardId=System.Collections.Generic.List`1[System.String] EffectIndex=0 Target=0 SubOption=-1 "
BLOCK_START_PATTERN = re.compile(r" *BLOCK_START BlockType=([A-Z]+) Entity=(.*) EffectCardId=.*")

# "BLOCK_START BlockType=TRIGGER Entity=[entityName=黑暗主教本尼迪塔斯 id=5 zone=DECK zonePos=0 cardId=SW_448 player=1] EffectCardId=System.Collections.Generic.List`1[System.String] EffectIndex=0 Target=0 SubOption=-1 TriggerKeyword=START_OF_GAME_KEYWORD"
# 用于识别"开局生效的全局卡"事件：BlockType=TRIGGER + TriggerKeyword=START_OF_GAME_KEYWORD。
# 这里的 Entity 通常是 "Entity=[... id=N ...]"，卡牌 cardId 可能当时为空格，
# 但实体 id 已由 SHOW_ENTITY 注册过 card_id，所以这里捕获实体 id，由 log_state
# 用 state.entity_dict 解析出真实 card_id（非空才计为一张生效卡）。
BLOCK_START_TRIGGER_PATTERN = re.compile(
    r" *BLOCK_START BlockType=TRIGGER Entity=\[.* id=(\d+) .*"
    r" TriggerKeyword=START_OF_GAME_KEYWORD")

# "BLOCK_END"
BlOCK_END_PATTERN = re.compile(r" *BLOCK_END *")

# "PlayerID=1, PlayerName=UNKNOWN HUMAN PLAYER"
# "PlayerID=2, PlayerName=Example#51234"
PLAYER_ID_PATTERN = re.compile(r"PlayerID=(\d+), PlayerName=(.*)")

# "TAG_CHANGE Entity=GameEntity tag=NEXT_STEP value=FINAL_WRAPUP "
# "TAG_CHANGE Entity=Example#51234 tag=467 value=4 "
# "TAG_CHANGE Entity=[entityName=UNKNOWN ENTITY [cardType=INVALID] id=14 zone=DECK zonePos=0 cardId= player=1] tag=ZONE value=HAND "
TAG_CHANGE_PATTERN = re.compile(r" *TAG_CHANGE Entity=(.*) tag=(.*) value=(.*) ")

# "tag=ZONE value=DECK"
TAG_PATTERN = re.compile(r" *tag=(.*) value=(.*)")

GENERAL_CHOICE_START_PATTERN = re.compile(
    r"D [\d]{2}:[\d]{2}:[\d]{2}\.[\d]{7} "
    r"GameState\.DebugPrintEntityChoices\(\) - id=(\d+) .*"
    r"ChoiceType=GENERAL(?: .*)?$")
GENERAL_CHOICE_ENTITY_PATTERN = re.compile(
    r"D [\d]{2}:[\d]{2}:[\d]{2}\.[\d]{7} "
    r"GameState\.DebugPrintEntityChoices\(\) - +Entities\[(\d+)\]="
    r".* player=(\d+)\]$")
GENERAL_CHOICE_READY_PATTERN = re.compile(
    r"D [\d]{2}:[\d]{2}:[\d]{2}\.[\d]{7} "
    r"ChoiceCardMgr\.WaitThenShowChoices\(\) - id=(\d+) "
    r"(?:WAIT(?: .*)?|BEGIN)$")
GENERAL_CHOICE_RESOLVED_PATTERN = re.compile(
    r"D [\d]{2}:[\d]{2}:[\d]{2}\.[\d]{7} "
    r"GameState\.SendChoices\(\) - id=(\d+) ChoiceType=GENERAL$")


class LineInfoContainer:
    def __init__(self, line_type, **kwargs):
        self.line_type = line_type
        self.info_dict = copy.copy(kwargs)

    def __str__(self):
        res = "line_type: " + str(self.line_type) + "\n"
        if len(self.info_dict) > 0:
            res += "info_dict\n"
            for key, value in self.info_dict.items():
                res += "\t" + str(key) + ": " + str(value) + "\n"
        return res


class LogInfoContainer:
    def __init__(self, log_type, source_path: Path | None = None):
        self.log_type = log_type
        self.source_path = Path(source_path) if source_path is not None else None
        self.message_list = []

    def append_info(self, line_info):
        self.message_list.append(line_info)

    @property
    def length(self):
        return len(self.message_list)

    @property
    def session_id(self):
        if self.source_path is None:
            return "unknown-session"
        return self.source_path.parent.name


def fetch_entity_id(input_string):
    if input_string[0] != "[":
        return input_string

    # 去除前后的 "[", "]"
    kv_list = input_string[1:-1]

    # 提取成形如 [... , "id=233" , ...]的格式
    kv_list = kv_list.split(" ")

    for item in kv_list:
        if item[:3] == "id=":
            return item[3:]


def parse_line(line_str):
    line_str = line_str.rstrip("\r\n")

    match_obj = GENERAL_CHOICE_START_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_GENERAL_CHOICE_START,
            choice_id=match_obj.group(1),
        )

    match_obj = GENERAL_CHOICE_ENTITY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_GENERAL_CHOICE_ENTITY,
            choice_id=None,
            index=int(match_obj.group(1)),
            player=match_obj.group(2),
        )

    match_obj = GENERAL_CHOICE_READY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_GENERAL_CHOICE_READY,
            choice_id=match_obj.group(1),
        )

    match_obj = GENERAL_CHOICE_RESOLVED_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_GENERAL_CHOICE_RESOLVED,
            choice_id=match_obj.group(1),
        )

    match_obj = GAME_STATE_PATTERN.match(line_str)
    if match_obj is None:
        return

    line_str = match_obj.group(2)

    if line_str == "CREATE_GAME":
        return LineInfoContainer(LOG_LINE_CREATE_GAME)

    # 开局生效的全局卡：BLOCK_START BlockType=TRIGGER 且
    # TriggerKeyword=START_OF_GAME_KEYWORD 且 cardId 非空。只有这种才代表
    # 一张卡真的在开局触发了效果（排除 cardId 为空、TriggerKeyword=TAG_NOT_SET
    # 的通用机制触发）。
    match_obj = BLOCK_START_TRIGGER_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_BLOCK_START_TRIGGER,
            entity_id=match_obj.group(1),
        )

    match_obj = TAG_CHANGE_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_TAG_CHANGE,
            entity=fetch_entity_id(match_obj.group(1)),
            tag=match_obj.group(2),
            value=match_obj.group(3),
        )

    match_obj = TAG_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_TAG,
            tag=match_obj.group(1),
            value=match_obj.group(2)
        )

    match_obj = GAME_ENTITY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_GAME_ENTITY,
            entity=match_obj.group(1)
        )

    match_obj = PLAYER_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_PLAYER_ENTITY,
            entity=match_obj.group(1),
            player=match_obj.group(2)
        )

    match_obj = FULL_ENTITY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_FULL_ENTITY,
            entity=match_obj.group(1),
            card=match_obj.group(2)
        )

    match_obj = SHOW_ENTITY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_SHOW_ENTITY,
            entity=fetch_entity_id(match_obj.group(1)),
            card=match_obj.group(2)
        )

    match_obj = CHANGE_ENTITY_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_CHANGE_ENTITY,
            entity=fetch_entity_id(match_obj.group(1)),
            card=match_obj.group(2)
        )

    match_obj = BLOCK_START_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_BLOCK_START,
            type=match_obj.group(1),
            card=match_obj.group(2)
        )

    match_obj = BlOCK_END_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_BLOCK_END
        )

    match_obj = PLAYER_ID_PATTERN.match(line_str)
    if match_obj is not None:
        return LineInfoContainer(
            LOG_LINE_PLAYER_ID,
            player=match_obj.group(1),
            name=match_obj.group(2)
        )

    return None


def log_iter_func(log_root=HEARTHSTONE_LOG_ROOT):
    while True:
        path = find_latest_power_log(log_root)
        if path is None:
            time.sleep(0.2)
            yield LogInfoContainer(LOG_CONTAINER_ERROR)
            continue

        try:
            file_handle = open(path, "r", encoding="utf8")
        except OSError:
            time.sleep(0.2)
            yield LogInfoContainer(LOG_CONTAINER_ERROR, path)
            continue

        with file_handle as f:
            switch_log = False
            while not switch_log:

                log_container = LogInfoContainer(LOG_CONTAINER_INFO, path)

                while True:
                    line = f.readline()

                    if line == "":
                        # 文件暂时写完：短等后重试一次，仍无新行就返回
                        # 已读到的增量，避免长时间阻塞主循环。
                        time.sleep(LOG_TAIL_WAIT_INTERVAL)
                        line = f.readline()
                        if line == "":
                            break
                    line_container = parse_line(line)
                    if line_container is not None:
                        log_container.append_info(line_container)

                latest_path = find_latest_power_log(log_root)
                if latest_path != path:
                    switch_log = True

                yield log_container


if __name__ == "__main__":
    line_str = input()
    print(parse_line(line_str))
