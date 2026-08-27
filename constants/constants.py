# 用户身份与炉石日志目录已迁移到 config.py（分层：内置默认 < ui_config.json < 环境变量）。
# 这里是唯一的转发点，避免在多处硬编码机器路径/用户身份。
from config import (
    HEARTHSTONE_LOG_ROOT, USER_NAME,
    OPERATE_INTERVAL, STATE_CHECK_INTERVAL, TINY_OPERATE_INTERVAL,
)

# 你的炉石用户名, 注意英文标点符号'#', 把后面的数字也带上
# 可以输入中文
YOUR_NAME = USER_NAME

# 关于控制台信息打印的设置
DEBUG_PRINT = True
WARN_PRINT = True
SYS_PRINT = True
INFO_PRINT = True
ERROR_PRINT = True

# 关于文件信息输出的设置
DEBUG_FILE_WRITE = True
WARN_FILE_WRITE = True
SYS_FILE_WRITE = True
INFO_FILE_WRITE = True
ERROR_FILE_WRITE = True

# 我觉得这行注释之后的内容应该不需要修改……
FSM_LEAVE_HS = "Leave Hearth Stone"
FSM_MAIN_MENU = "Main Menu"
FSM_CHOOSING_HERO = "Choosing Hero"
FSM_MATCHING = "Match Opponent"
FSM_CHOOSING_CARD = "Choosing Card"
# FSM_NOT_MY_TURN = "Not My Turn"
# FSM_MY_TURN = "My Turn"
FSM_BATTLING = "Battling"
FSM_ERROR = "ERROR"
FSM_QUITTING_BATTLE = "Quitting Battle"
FSM_WAIT_MAIN_MENU = "Wait main menu"

LOG_CONTAINER_ERROR = 0
LOG_CONTAINER_INFO = 1

LOG_LINE_CREATE_GAME = "Create Game"
LOG_LINE_GAME_ENTITY = "Create Game Entity"
LOG_LINE_PLAYER_ENTITY = "Create Player Entity"
LOG_LINE_FULL_ENTITY = "Full Entity"
LOG_LINE_SHOW_ENTITY = "Show Entity"
LOG_LINE_CHANGE_ENTITY = "Change Entity"
LOG_LINE_BLOCK_START = "Block Start"
LOG_LINE_BLOCK_END = "Block End"
LOG_LINE_PLAYER_ID = "Player ID"
LOG_LINE_TAG_CHANGE = "Tag Change"
LOG_LINE_TAG = "Tag"
LOG_LINE_GENERAL_CHOICE_START = "General Choice Start"
LOG_LINE_GENERAL_CHOICE_ENTITY = "General Choice Entity"
LOG_LINE_GENERAL_CHOICE_READY = "General Choice Ready"
LOG_LINE_GENERAL_CHOICE_RESOLVED = "General Choice Resolved"
LOG_LINE_BLOCK_START_TRIGGER = "Block Start Trigger"

CARD_BASE = "BASE"
CARD_SPELL = "SPELL"
CARD_MINION = "MINION"
CARD_WEAPON = "WEAPON"
CARD_LOCATION = "LOCATION"
CARD_HERO = "HERO"
CARD_HERO_POWER = "HERO_POWER"
CARD_ENCHANTMENT = "ENCHANTMENT"
