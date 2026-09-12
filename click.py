import win32gui
import win32api
import win32con
import pywintypes
import time
from pynput.mouse import Button, Controller
import random
from contextlib import contextmanager
# import get_screen

from constants.constants import *
from print_info import *
from get_screen import *
from config import human_like_settings


#测试不同分辨率的点击效果，因为屏幕截取未支持不同分辨率，失败了
#炉石传说坐标
# hwnd_left,hwnd_top, hwnd_right, hwnd_bottom = 0,0,0,0
# hwnd_width  = 0
# hwnd_height = 0

#获取炉石传说的分辨率
#得到不同分辨率下的点击位置
# def hs_size():
#     hwnd = get_HS_hwnd()
#     global hwnd_left 
#     global hwnd_top 
#     global hwnd_right 
#     global hwnd_bottom 
#     hwnd_left,hwnd_top,hwnd_right,hwnd_bottom = win32gui.GetWindowRect(hwnd)
#     # print(left)
#     # print(top)
#     # print(right)
#     # print(bottom)
#     global hwnd_width 
#     global hwnd_height
#     hwnd_width = hwnd_right - hwnd_left -16
#     hwnd_height = hwnd_bottom - hwnd_top -39
#     print(hwnd_width)
#     print(hwnd_height)
#     # 1440 900 = 1456 939
#     # 16 39
#     # 1400 1050 = 1416 1089 
#     # 16 39

# def position_x(x):
#     global hwnd_width 
#     global hwnd_left 
#     if hwnd_width == 0:
#      return x
#     return x * (hwnd_width / 1920) + 16 + hwnd_left 

# def position_y(y):
#     global hwnd_height
#     global hwnd_top 
#     if hwnd_height == 0:
#      return y
#     return y * (hwnd_height / 1920) + 39 + hwnd_top 



def rand_sleep(interval):
    base_time = interval * 0.75
    rand_time = interval * 0.5 * random.random()  # avg = 0.25 * interval
    time.sleep(base_time + rand_time)


# 鼠标复位点（只移动、不点击的待命位）：屏幕左上角空白处。
# 历史：最初 (480, 540) 每局结束后会停在牌组选择界面第二行第一个卡组上，
# 复位后偶发误选中该卡组；用户最终指定 (70, 60) 作为待命点。
MOUSE_RESET_POS = (70, 60)

# ---------------------------------------------------------------- 活人感（可选）
# 只在【对局中识别盒子意见并执行完之后】生效：由 RecommendationFlow / MulliganFlow
# 在动作执行成功后经 FSM_action._human_like_post_action_pause() 调用本函数，
# 用 0.5~3s 的随机“思考”延时代替固定延时，期间把手牌区当普通人类一样随手悬停
# （每处约 1s），最后仍复位到 MOUSE_RESET_POS。
# 匹配对手、选卡组、错误弹窗取消这类非推荐动作不会触发。
# 开关与参数在 Web 控制台的「活人感」卡片里设置（ui_config.json 的 human_like 段）。
HAND_HOVER_Y = 1000          # 手牌卡面所在高度（与 choose_card 用的 y 一致）
HAND_HOVER_SIZES = (3, 8)    # 手牌张数未知，按常见张数取卡位


def center_mouse(mouse=None):
    """Move the pointer to the neutral reset point without clicking."""
    if mouse is None:
        mouse = Controller()
    mouse.position = MOUSE_RESET_POS


def _random_hand_hover_point(last=None):
    """在“看起来像手牌卡面”的位置里随机取一点（对齐 HAND_CARD_X 的卡位）。

    只移动不点击，所以即使落点处没有牌也无害；尽量避开刚停过的那个点。
    """
    for _ in range(8):
        size = random.randint(*HAND_HOVER_SIZES)
        point = (random.choice(HAND_CARD_X[size]), HAND_HOVER_Y)
        if point != last:
            return point
    return (960, HAND_HOVER_Y)


def human_like_pause(mouse=None, settings=None):
    """活人感延时：0.5~3s 随机等待，期间鼠标在手牌区随机悬停。

    每处悬停时长同样随机（默认 0.2~1s，见 config.DEFAULT_HUMAN_LIKE）。
    全程只移动、绝不点击；结束前复位到 MOUSE_RESET_POS。返回本次总延时（秒）。
    """
    cfg = settings or human_like_settings()
    if mouse is None:
        mouse = Controller()
    total = random.uniform(cfg["post_delay_min"], cfg["post_delay_max"])
    # 这句同时驱动浮窗底部的延时进度条（log_overlay 解析“延时 X s 后”取总时长），
    # 措辞保持“延时 X.Xs 后”，剩下的文字会作为进度条下方的说明显示。
    sys_print(f"[SYS] 活人感 延时 {total:.1f}s 后（手牌区随机悬停等待）")
    deadline = time.monotonic() + total
    last = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        point = _random_hand_hover_point(last)
        mouse.position = point
        last = point
        hover = random.uniform(cfg["hover_min"], cfg["hover_max"])
        time.sleep(min(hover, remaining))
    center_mouse(mouse)
    # 与固定延时一致的收尾标记：让浮窗进度条立刻清空、显示“延时：无”。
    sys_print("[SYS] 延时结束")
    return total


def park_mouse(mouse=None):
    """Finish an action: honor the minimum delay, then park without clicking.

    「活人感」的随机延时/手牌悬停**不在这里**：它只在对局中识别盒子意见并执行后
    由流程层调用（RecommendationFlow / MulliganFlow 的 post_action_pause），
    否则匹配对手、选卡组、错误弹窗取消这类非推荐动作也会被拖慢。
    """
    time.sleep(0.1)
    center_mouse(mouse)


@contextmanager
def hearthstone_action_session():
    """Park the pointer after every complete Hearthstone action."""
    try:
        yield
    finally:
        park_mouse()


def run_hearthstone_action(action):
    with hearthstone_action_session():
        return action()


def _send_physical_click(mouse, x, y, button):
    mouse.position = (x, y)
    rand_sleep(0.1)
    mouse.press(button)
    rand_sleep(0.1)
    mouse.release(button)


def click_button(x, y, button, require_hearthstone=True):
    # x = position_x(x)
    # y = position_y(y)
    hearthstone_hwnd = get_HS_hwnd() if require_hearthstone else 0
    mouse = Controller()
    rand_sleep(0.1)
    if (require_hearthstone
            and hearthstone_hwnd != 0
            and win32gui.GetForegroundWindow() != hearthstone_hwnd):
        # The first physical click on a background Unity window can be
        # consumed solely to activate it. Activate via the same harmless
        # right-side point used by cancel_click, then send the real action.
        activate_x, activate_y = 1800, 500
        _send_physical_click(
            mouse, activate_x, activate_y, Button.right)
        rand_sleep(0.25)
    _send_physical_click(mouse, x, y, button)


def left_click(x, y, require_hearthstone=True):
    x += random.randint(-2, 3)
    y += random.randint(-2, 3)
    click_button(x, y, Button.left, require_hearthstone)


def right_click(x, y, require_hearthstone=True):
    click_button(x, y, Button.right, require_hearthstone)


def choose_my_board_entity(entity_index, entity_num):
    rand_sleep(OPERATE_INTERVAL)
    x = 960 - (entity_num - 1) * 70 + entity_index * 140
    y = 600
    left_click(x, y)


def choose_my_minion(mine_index, mine_num):
    choose_my_board_entity(mine_index, mine_num)


def choose_my_hero():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 850)


def choose_opponent_minion(oppo_index, oppo_num):
    rand_sleep(OPERATE_INTERVAL)
    x = 960 - (oppo_num - 1) * 70 + oppo_index * 140
    y = 400
    left_click(x, y)


def choose_oppo_hero():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 200)


def cancel_click():
    rand_sleep(TINY_OPERATE_INTERVAL)
    right_click(1700, 400)


def test_click():
    rand_sleep(TINY_OPERATE_INTERVAL)
    left_click(1700, 400)


HAND_CARD_X = [
    [],  # 0
    [885],  # 1
    [820, 980],  # 2
    [750, 890, 1040],  # 3
    [690, 820, 970, 1130],  # 4
    [680, 780, 890, 1010, 1130],  # 5
    [660, 750, 840, 930, 1020, 1110],  # 6
    [660, 733, 810, 885, 965, 1040, 1120],  # 7
    [650, 720, 785, 855, 925, 995, 1060, 1130],  # 8
    [650, 710, 765, 825, 880, 950, 1010, 1070, 1140],  # 9
    [647, 700, 750, 800, 860, 910, 970, 1020, 1070, 1120]  # 10
]


def choose_card(card_index, card_num):
    rand_sleep(OPERATE_INTERVAL)

    assert 0 <= card_index < card_num <= 10
    # x = START[card_num] + 65 + STEP[card_num] * card_index
    x = HAND_CARD_X[card_num][card_index]

    y = 1000
    left_click(x, y)


DISCOVER_CARD_X = {
    1: (960,),
    2: (760, 1160),
    3: (560, 960, 1360),
    # Four-option menus (e.g. BG31_BOB and CATA_190h), at 1920x1080.
    4: (400, 775, 1150, 1525),
}


def choose_discover_card(choice_index, choice_count):
    """Click a centered 1920x1080 discover choice for its layout size."""
    assert choice_count in DISCOVER_CARD_X
    positions = DISCOVER_CARD_X[choice_count]
    assert 0 <= choice_index < len(positions)
    rand_sleep(OPERATE_INTERVAL)
    y = 540 if choice_count == 1 else 500
    left_click(positions[choice_index], y)


STARTING_CARD_X = {
    3: [600, 960, 1320],
    5: [600, 850, 1100, 1350],
}


def replace_starting_card(card_index, hand_card_num):
    assert hand_card_num in STARTING_CARD_X
    assert card_index < len(STARTING_CARD_X[hand_card_num])

    rand_sleep(OPERATE_INTERVAL)
    left_click(STARTING_CARD_X[hand_card_num][card_index], 500)


def click_middle():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 500)


def click_setting():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1895, 1060)


def click_concede():
    """点击游戏菜单中央的红色“认输”按钮（1920x1080 实测坐标）。"""
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 380)


def choose_and_use_spell(card_index, card_num):
    choose_card(card_index, card_num)
    click_middle()


def drag_card_to_board_entity(card_index, card_num, entity_index, entity_num):
    """Drag a hand card just left of an existing friendly board entity."""
    assert 0 <= card_index < card_num <= 10
    assert 0 <= entity_index < entity_num <= 7

    mouse = Controller()
    mouse.position = (HAND_CARD_X[card_num][card_index], 1000)
    rand_sleep(0.1)
    mouse.press(Button.left)
    try:
        board_x = 960 - (entity_num - 1) * 70 + entity_index * 140
        mouse.position = (board_x - 25, 600)
        rand_sleep(0.1)
    finally:
        mouse.release(Button.left)


# 第[i]个随从左边那个空隙记为第[i]个gap
def put_minion(gap_index, minion_num):
    rand_sleep(OPERATE_INTERVAL)

    if minion_num >= 7:
        warn_print(f"Try to put a minion but there has already been {minion_num} minions")

    x = 960 - (minion_num - 1) * 70 + 140 * gap_index - 70
    y = 600
    left_click(x, y)


def match_opponent():
    # 一些奇怪的错误提示
    commit_error_report()
    rand_sleep(OPERATE_INTERVAL)
    left_click(1400, 900)


def enter_battle_mode():
    # 一些奇怪的错误提示
    commit_error_report()
    rand_sleep(OPERATE_INTERVAL)
    left_click(950, 320)


def commit_choose_card():
    rand_sleep(OPERATE_INTERVAL)
    left_click(960, 850)


def end_turn():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1550, 500)


# HSAng 左下「时间线」提示按钮中心（1920x1080 实测，见用户截图）：
#   回溯(撤销) ≈ (351, 805)   维持(保留) ≈ (582, 805)
TIMELINE_UNDO_POS = (351, 805)
TIMELINE_KEEP_POS = (582, 805)


def click_timeline_undo():
    """点 HSAng 左下「回溯」：撤销时间线里上一步操作。"""
    rand_sleep(OPERATE_INTERVAL)
    x, y = TIMELINE_UNDO_POS
    left_click(x, y)


def click_timeline_keep():
    """点 HSAng 左下「维持」：保留当前操作、关掉时间线提示。"""
    rand_sleep(OPERATE_INTERVAL)
    x, y = TIMELINE_KEEP_POS
    left_click(x, y)


def click_launch_starship():
    """Click the starship launch button at 1920x1080."""
    rand_sleep(OPERATE_INTERVAL)
    left_click(1080, 920)


def drag_card_to_deck():
    """Drag the selected hand card to the friendly deck.

    到达牌库后必须按够 DECK_DROP_HOLD_INTERVAL 再松手：交易/锻造/预备都靠
    “牌库悬停高亮就绪→松开”触发，松太快会概率性失败(卡牌又弹回手牌)。
    """
    mouse = Controller()
    mouse.press(Button.left)
    try:
        rand_sleep(0.1)
        # 我方牌库中心点(1920x1080 实测，用户量得 1635,640)。
        mouse.position = (1635, 640)
        rand_sleep(DECK_DROP_HOLD_INTERVAL)
    finally:
        mouse.release(Button.left)


def commit_error_report():
    # 一些奇怪的错误提示
    left_click(1100, 820)
    # 如果已断线, 点这里时取消
    left_click(960, 650)


def emoj(target=None):
    emoj_list = [(800, 880), (800, 780), (800, 680), (1150, 680), (1150, 780)]
    right_click(960, 830)
    rand_sleep(OPERATE_INTERVAL)

    if target is None:
        x, y = emoj_list[random.randint(1, 4)]
    else:
        x, y = emoj_list[target]
    left_click(x, y)
    rand_sleep(OPERATE_INTERVAL)


def click_skill():
    rand_sleep(OPERATE_INTERVAL)
    left_click(1150, 850)


def use_skill_no_point():
    click_skill()
    cancel_click()


def use_skill_point_mine(my_index, my_num):
    click_skill()

    if my_index < 0:
        choose_my_hero()
    else:
        choose_my_minion(my_index, my_num)

    cancel_click()


def minion_beat_minion(mine_index, mine_number, oppo_index, oppo_num):
    _choose_my_attacker(mine_index, mine_number)
    _choose_attack_target(oppo_index, oppo_num)
    _finish_attack()


def minion_beat_hero(mine_index, mine_number):
    _choose_my_attacker(mine_index, mine_number)
    _choose_attack_target_hero()
    _finish_attack()


def hero_beat_minion(oppo_index, oppo_num):
    _choose_my_hero_attacker()
    _choose_attack_target(oppo_index, oppo_num)
    _finish_attack()


def hero_beat_hero():
    _choose_my_hero_attacker()
    _choose_attack_target_hero()
    _finish_attack()


def _choose_my_attacker(mine_index, mine_number):
    time.sleep(0.05)
    x = 960 - (mine_number - 1) * 70 + mine_index * 140
    left_click(x, 600)


def _choose_my_hero_attacker():
    time.sleep(0.05)
    left_click(960, 850)


def _choose_attack_target(oppo_index, oppo_num):
    time.sleep(0.05)
    x = 960 - (oppo_num - 1) * 70 + oppo_index * 140
    left_click(x, 400)


def _choose_attack_target_hero():
    time.sleep(0.05)
    left_click(960, 200)


def _finish_attack():
    time.sleep(0.05)
    right_click(1700, 400)


def enter_HS():
    rand_sleep(1)

    if test_hs_available():
        move_window_foreground(get_HS_hwnd(), "炉石传说")
        return

    battlenet_hwnd = get_battlenet_hwnd()

    if battlenet_hwnd == 0:
        error_print("未找到应用战网")
        return False

    move_window_foreground(battlenet_hwnd, "战网")

    rand_sleep(1)

    left, top, right, bottom = win32gui.GetWindowRect(battlenet_hwnd)
    left_click(left + 180, bottom - 110, require_hearthstone=False)
    return True

