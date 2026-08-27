import _thread
import random
import re
import sys
import threading
import time

import keyboard

import click
import get_screen
from config import DEFAULT_AUTO_CONCEDE, SNAPSHOT_WRITE_INTERVAL
from manual_controller import (
    ClickExecutor, GlobalHotkeyInput, ManualController,
)
from strategy import StrategyState
from log_state import *
from src.capture.desktop_capture import DesktopCapture
from src.flow.mulligan_flow import MulliganFlow, MulliganStatus
from src.flow.recommendation_flow import (
    FlowStepStatus, RecommendationFlow,
)
from src.game_state.recommendation_adapter import adapt_action
from src.ocr.paddle_adapter import PaddleOcrAdapter
from src.ocr.stable_reader import StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_config import RecommendationConfig
from src.recommendation_models import ActionKind
from src.safety.recommendation_validator import RecommendationValidator


FSM_state = ""
time_begin = 0.0
game_count = 0
win_count = 0
concede_count = 0
quitting_flag = False
# 定时计划 / Web 控制台：置 True 表示“本局对战结束后停止自动化”。
# 对局进行中（换牌/对战/结算）不会立即退出，只有回到非对局状态才停止。
stop_after_current_game = False
shutdown_event = threading.Event()
log_state = LogState()
log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
choose_hero_count = 0
manual_controller = ManualController(
    input_func=GlobalHotkeyInput(
        keyboard, shutdown_event=shutdown_event),
    executor=ClickExecutor(click),
)
auto_mulligan_flow = None
recommendation_flow = None
recommendation_config = None
recommendation_capture = None
recommendation_parser = None
recommendation_reader = None
mulligan_reader = None
recommendation_validator = None
active_game_generation = -1
mulligan_delay_generation = None
player_turn_delay_key = None
last_automation_diagnostic = None
_snapshot_cache_key = None
_snapshot_cache = None
_mulligan_diagnostic_key = None
# 自动投降状态：连续低胜率检测 + 触发标记（每局重置）。
_concede_streak = 0
_concede_last_turn = None
_concede_triggered = False
# 调试快照写盘节流：日志每次变化都全量序列化整个 log_state 会拖慢主循环，
# 只在间隔 SNAPSHOT_WRITE_INTERVAL 秒后重新写盘。（定义于 config.py）
_last_snapshot_write = 0.0


def _automation_state():
    snapshot = refresh_snapshot()
    if snapshot is None:
        raise RuntimeError("power_log_snapshot_unavailable")
    snapshot.log_revision = log_state.revision
    return snapshot


def _automation_state_with_revision():
    snapshot = _automation_state()
    return snapshot, log_state.revision


def initialize_recommendation_automation():
    """Rebuild per-game flows while reusing expensive OCR components.

    每次调用都会重新读取 ui_config.json 的 recommendation_roi（重建轻量的
    RecommendationConfig + DesktopCapture），因此用校准工具画完框后，直接重开
    对局/重启自动化即可生效，无需重启 web_ui。昂贵的 OCR 引擎（reader 与
    paddle backend）仍只创建一次、跨对局复用。
    """
    global auto_mulligan_flow, recommendation_flow
    global recommendation_config, recommendation_capture
    global recommendation_parser, recommendation_reader
    global mulligan_reader, recommendation_validator

    # 轻量：每次都重建，以便拾取校准后的最新 ROI / 尺寸配置。
    recommendation_config = RecommendationConfig()
    recommendation_capture = DesktopCapture(recommendation_config)

    if recommendation_parser is None:
        recommendation_parser = RecommendationParser()

    if recommendation_reader is None:
        recommendation_reader = StableRecommendationReader(
            recommendation_config, PaddleOcrAdapter(),
            text_normalizer=recommendation_parser.normalize_action_text)
        # 不设"打法参考A"信标：对战时该标题不在截图区域内（实测
        # 面板直出「打出N号位…」指令文本），设信标会把正确指令清空为
        # recommendation_not_stable 死循环。面板是否在场由 parser
        # 严格句式（打出N号位随从/放置于我方N号位 等）唯一把关，
        # 与换牌 reader 同一设计。
    if mulligan_reader is None:
        # 换牌面板只有留牌建议（无"打法参考A"标题）：不设信标，
        # 面板是否在场由 `替换N号位卡牌` 换牌句式唯一把关。
        # 独立 if 保证每次 initialize（含 config 已存在的后续对局）
        # 都会为闭包绑定该变量。
        mulligan_reader = StableRecommendationReader(
            recommendation_config, recommendation_reader.backend,
            text_normalizer=recommendation_parser.normalize_action_text)
        recommendation_validator = RecommendationValidator(
            recommendation_config)

    def read_mulligan_action():
        # 换牌面板是否在场，由 OCR 证据裁定：识别出的文本必须能解析出
        # `替换N号位卡牌` 换牌句式（无"打法参考A"信标的专用 reader）。
        evidence = mulligan_reader.read(
            lambda: recommendation_capture.capture(ocr_panel_ok=True),
            recommendation_capture.crop_recommendation)
        action = recommendation_parser.parse(
            evidence, log_state.game_num_turns_in_play, log_state.revision)
        if action.action != ActionKind.MULLIGAN:
            raise RuntimeError("recommendation_is_not_mulligan")
        return action

    auto_mulligan_flow = MulliganFlow(
        click, read_mulligan_action, _automation_state,
        action_context=click.hearthstone_action_session,
        stopped=shutdown_event.is_set,
        # 上游时序：OCR 前的每局等待由 ChoosingCardAction 的 ready 延时负责；
        # OCR 成功后立即点击，不再叠加缓冲（mulligan_post_ocr_delay=0）。
        first_delay=recommendation_config.mulligan_post_ocr_delay_seconds,
        retry_delay=recommendation_config.mulligan_post_ocr_delay_seconds)
    recommendation_flow = RecommendationFlow(
        capture=recommendation_capture,
        reader=recommendation_reader,
        parser=recommendation_parser,
        state_supplier=_automation_state_with_revision,
        adapter=adapt_action,
        validator=recommendation_validator,
        controller=manual_controller,
        result_timeout=recommendation_config.result_timeout_seconds,
        post_action_delay=recommendation_config.post_action_delay_seconds,
        stopped=shutdown_event.is_set,
    )


def reset_game_session():
    """Clear every match-scoped automation state for a newly created game."""
    global active_game_generation, choose_hero_count
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key
    global _concede_streak, _concede_last_turn, _concede_triggered
    initialize_recommendation_automation()
    active_game_generation = log_state.game_generation
    choose_hero_count = 0
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    _concede_streak = 0
    _concede_last_turn = None
    _concede_triggered = False
    click.center_mouse()


def init():
    global log_state, log_iter, choose_hero_count, active_game_generation
    global mulligan_delay_generation, player_turn_delay_key
    global last_automation_diagnostic
    global _snapshot_cache_key, _snapshot_cache, _mulligan_diagnostic_key
    global _concede_streak, _concede_last_turn, _concede_triggered

    log_state = LogState()
    log_iter = log_iter_func(HEARTHSTONE_LOG_ROOT)
    choose_hero_count = 0
    active_game_generation = -1
    mulligan_delay_generation = None
    player_turn_delay_key = None
    last_automation_diagnostic = None
    _snapshot_cache_key = None
    _snapshot_cache = None
    _mulligan_diagnostic_key = None
    _concede_streak = 0
    _concede_last_turn = None
    _concede_triggered = False
    shutdown_event.clear()
    initialize_recommendation_automation()
    click.center_mouse()


def update_log_state():
    global active_game_generation
    global _last_snapshot_write
    log_container = next(log_iter)
    if log_container.log_type == LOG_CONTAINER_ERROR:
        return False

    previous_revision = log_state.revision
    for log_line_container in log_container.message_list:
        ok = update_state(log_state, log_line_container)
        # if not ok:
        #     return False

    if log_state.game_generation != active_game_generation:
        reset_game_session()

    if (DEBUG_FILE_WRITE and log_state.revision != previous_revision
            and time.time() - _last_snapshot_write
            >= SNAPSHOT_WRITE_INTERVAL):
        _last_snapshot_write = time.time()
        with open("./log/game_state_snapshot.txt", "w", encoding="utf8") as f:
            f.write(str(log_state))

    # 注意如果Power.log没有更新, 这个函数依然会返回. 应该考虑到game_state只是被初始化
    # 过而没有进一步更新的可能
    if log_state.game_entity_id == 0:
        return False

    return True


def refresh_snapshot():
    """Read pending Power.log events and build a fresh manual snapshot."""
    global _snapshot_cache_key, _snapshot_cache
    if not update_log_state():
        return None
    cache_key = (log_state.game_generation, log_state.revision)
    if cache_key != _snapshot_cache_key:
        _snapshot_cache = StrategyState(log_state)
        _snapshot_cache_key = cache_key
    return _snapshot_cache


def wait_for_log_update(start_revision=None, timeout=2.0):
    """Wait briefly for evidence that an input changed game state."""
    if start_revision is None:
        start_revision = log_state.revision
    deadline = time.time() + timeout
    while time.time() < deadline:
        if update_log_state() and log_state.revision > start_revision:
            return True
    manual_controller.output("尚未检测到状态变化，请查看游戏后刷新或重试。")
    return False


def wait_until_battle_starts():
    loop_count = 0
    while True:
        if not update_log_state():
            return FSM_ERROR
        if log_state.is_end:
            return FSM_QUITTING_BATTLE
        if log_state.game_num_turns_in_play > 0:
            return FSM_BATTLING
        loop_count += 1
        if loop_count >= 60:
            warn_print("Time out in Choosing Card")
            return FSM_ERROR
        time.sleep(STATE_CHECK_INTERVAL)


def system_exit():
    global quitting_flag

    sys_print(f"一共完成了{game_count}场对战, 赢了{win_count}场")
    print_info_close()

    quitting_flag = True
    shutdown_event.set()
    if threading.current_thread() is threading.main_thread():
        raise SystemExit(0)
    _thread.interrupt_main()


def request_stop_after_game():
    """请求“本局对战结束后停止”。

    对局进行中时不会中断当前操作；当状态机回到非对局状态
    （主菜单/选职业/匹配/炉石未运行等）后自动化线程自动退出。
    再次调用 request_cancel_stop_after_game() 可在本局结束前撤销。
    """
    global stop_after_current_game
    stop_after_current_game = True
    info_print("已请求：本局对战结束后停止自动化。")
    return True


def request_cancel_stop_after_game():
    """撤销“本局结束后停止”，让自动化继续打下去。

    在线程退出前调用即可，无需重启脚本；本局结束前都可自由更改。
    """
    global stop_after_current_game
    stop_after_current_game = False
    info_print("已取消「本局结束后停止」，自动化继续运行。")
    return True


def request_immediate_stop():
    """Web 模式下的立即停止：只终止自动化线程，不影响服务器主线程。"""
    global quitting_flag
    info_print("收到立即停止指令，正在终止自动化……")
    quitting_flag = True
    shutdown_event.set()
    return True


def print_out():
    global FSM_state
    global time_begin
    global game_count

    # sys_print("Enter State " + str(FSM_state))

    if FSM_state == FSM_LEAVE_HS:
        warn_print("HearthStone not found! Try to go back to HS")

    if FSM_state == FSM_CHOOSING_CARD:
        # 只在“真正打完一局”时计数（见 Battling），开局只记录开始时间。
        # sys_print("The " + str(game_count) + " game begins")
        time_begin = time.time()

    if FSM_state == FSM_QUITTING_BATTLE:
        # sys_print("The " + str(game_count) + " game ends")
        time_now = time.time()
        if time_begin > 0:
            info_print("The last game last for : {} mins {} secs"
                       .format(int((time_now - time_begin) // 60),
                               int(time_now - time_begin) % 60))

    return


def ChoosingHeroAction():
    global choose_hero_count

    if quitting_flag or stop_after_current_game:
        sys.exit(0)

    print_out()

    # 有时脚本会卡在某个地方, 从而在FSM_Matching
    # 和FSM_CHOOSING_HERO之间反复横跳. 这时候要
    # 重启炉石
    # choose_hero_count会在每一次开始留牌时重置
    choose_hero_count += 1
    if choose_hero_count >= 20:
        return FSM_ERROR

    time.sleep(2)
    click.run_hearthstone_action(click.match_opponent)
    time.sleep(1)
    return FSM_MATCHING


def MatchingAction():
    print_out()
    loop_count = 0

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        time.sleep(STATE_CHECK_INTERVAL+random.random()+random.random()+random.random())

        click.run_hearthstone_action(click.commit_error_report)

        ok = update_log_state()
        if ok:
            if not log_state.is_end:
                return FSM_CHOOSING_CARD

        curr_state = get_screen.get_state()
        if curr_state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO

        loop_count += 1
        # print("寻找对手计时器")
        # print(loop_count)
        if loop_count >= 60:
            warn_print("Time out in Matching Opponent")
            return FSM_ERROR


def ChoosingCardAction():
    global choose_hero_count, mulligan_delay_generation
    global quitting_flag, stop_after_current_game, shutdown_event
    choose_hero_count = 0

    print_out()
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if snapshot.game_num_turns_in_play > 0:
        return FSM_BATTLING

    # CREATE_GAME increments game_generation.  Bind the ready delay to that
    # generation so every match waits once, including matches after the first.
    while mulligan_delay_generation != log_state.game_generation:
        waiting_generation = log_state.game_generation
        delay = recommendation_config.mulligan_ready_delay_seconds
        # 用统一的 _sleep_with_delay：推送"延时 Ns 后"启动浮窗进度条，
        # sleep 后再推"延时结束"清除，与换牌重试的进度表行为一致。
        _sleep_with_delay(delay, "换牌前识别")
        mulligan_delay_generation = waiting_generation
        snapshot = refresh_snapshot()
        if snapshot is None:
            return FSM_ERROR
        if snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING

    if auto_mulligan_flow is not None:
        auto_mulligan_flow.reset_delay()  # 每局首次用 ready(7)，重试用 post_ocr(5)
        # 换牌自动流（理想流程）：
        #   每局 ready(20s) 等待后进入循环 → 每隔 mulligan_retry(5s) 一次：
        #     ① 先检测屏幕中间“确认”按钮是否在场（面板就绪的物理信号）
        #     ② 在    → OCR 左侧留牌建议并执行换牌（替换+确认）
        #        不在 → 等 mulligan_retry_delay 再试
        #   点击后转为“确认按钮是否消失”校验：消失=已提交，仍在=未提交重试。
        confirmed_waiting = False
        verified = False
        # 重试间隔（面板未就绪/推荐暂不可执行/确认未消失时每轮等待）。
        # 不复用 post_ocr（那是“识别→点击”缓冲）：上游 post_ocr=0 时，
        # 若重试也取 0 会变成 0 秒忙等（CPU 空转 + 疯狂截图）。独立默认 5s。
        retry_delay = recommendation_config.mulligan_retry_delay_seconds
        while True:
            # 循环内必须检查停止标志：主循环只在状态分发处检查，
            # 本循环若能无限运行，立即停止后鼠标会继续点击。
            # 「本局结束后停止」不在此处生效（那是打完本局才停），
            # 本局内随时可通过 request_cancel_stop_after_game 反悔。
            if quitting_flag:
                sys.exit(0)
            fresh = refresh_snapshot()
            if fresh is None:
                return FSM_ERROR
            if fresh.is_end:
                return FSM_QUITTING_BATTLE
            if fresh.game_num_turns_in_play > 0:
                return FSM_BATTLING
            if confirmed_waiting:
                if not verified:
                    verified = True
                    # 点击确认后等界面切换，再检测“确认”按钮是否还在：
                    # 还在 → 换牌未提交成功，重新执行；消失 → 已提交。
                    time.sleep(0.5)
                    if confirm_button_present():
                        confirmed_waiting = False
                        verified = False
                        _report_mulligan_diagnostic(
                            "confirm_still_there",
                            "换牌确认仍在（未提交成功），重新执行……")
                        continue
                time.sleep(0.3)
                continue
            # 每轮开头先检测“确认”按钮：在 → 执行换牌；不在 → 等 retry 再试。
            if not confirm_button_present():
                _report_mulligan_diagnostic(
                    "confirm_absent",
                    "确认按钮未检测到（面板尚未就绪或已提交），等待重试……")
                _sleep_with_delay(retry_delay, "换牌重试")
                continue
            result = auto_mulligan_flow.run()
            if result.status == MulliganStatus.CONFIRMED:
                confirmed_waiting = True
                verified = False
                _report_mulligan_diagnostic(
                    "confirmed", "已执行换牌，检测确认按钮……")
                time.sleep(0.3)
                continue
            message = f"换牌推荐暂不可执行，继续重试：{result.diagnostics}"
            # 换牌面板已不在/阶段已变更时给出更明确提示，避免误以为卡死。
            diag = result.diagnostics
            if (diag == "recommendation_is_not_mulligan"
                    or diag.endswith(":recommendation_is_not_mulligan")
                    or diag == "mulligan_stage_changed"
                    or diag == "hand_changed"
                    or diag == "confirm_button_absent"
                    or diag.endswith(":confirm_button_absent")):
                message = ("换牌阶段未检测到可执行的留牌面板（可能已提交或"
                           "面板未就绪），等待对局开始……")
            _report_mulligan_diagnostic(result.diagnostics, message)
            _sleep_with_delay(retry_delay, "换牌重试")

    selected = manual_controller.choose_mulligan(snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    if not manual_controller.mulligan_is_current(snapshot, fresh_snapshot):
        manual_controller.output("留牌状态已经变化，本次选择未点击，请重新确认。")
        if fresh_snapshot.is_end:
            return FSM_QUITTING_BATTLE
        if fresh_snapshot.game_num_turns_in_play > 0:
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    try:
        with click.hearthstone_action_session():
            try:
                for hand_index in selected:
                    click.replace_starting_card(
                        hand_index, fresh_snapshot.my_hand_card_num)
                click.commit_choose_card()
            except Exception:
                try:
                    click.cancel_click()
                except Exception:
                    pass
                raise
    except Exception as exc:
        manual_controller.output(f"留牌鼠标操作失败：{exc}")
        return FSM_ERROR
    return wait_until_battle_starts()


def run_manual_battle_step():
    snapshot = refresh_snapshot()
    if snapshot is None:
        return FSM_ERROR
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if not snapshot.is_my_turn:
        return None

    manual_controller.output(snapshot.format_for_manual_control())
    action = manual_controller.prompt_turn_action(snapshot)
    action = manual_controller.bind_to_turn(action, snapshot)
    fresh_snapshot = refresh_snapshot()
    if fresh_snapshot is None:
        return FSM_ERROR
    revision_before = log_state.revision
    result = manual_controller.execute(action, fresh_snapshot)
    manual_controller.output(result.message)
    if result.recovery_needed:
        return FSM_ERROR
    if result.executed:
        wait_for_log_update(revision_before)
    return None


def _report_automation_diagnostic(code, message):
    """Report a stable automation state once instead of every loop."""
    global last_automation_diagnostic
    if last_automation_diagnostic == code:
        return
    try:
        manual_controller.output(message)
    except Exception:
        pass
    last_automation_diagnostic = code


def _report_mulligan_diagnostic(code, message):
    """Report a stable mulligan retry state once instead of every 0.3s loop.

    换牌阶段如果 OCR 暂时读不出/面板已变更，原逻辑会每 0.3s 打印一次
    「换牌推荐暂不可执行」，在浮窗里刷屏。这里按诊断码去重，只在原因
    变化时输出一次，浮窗能稳定看见卡在哪一步。
    """
    global _mulligan_diagnostic_key
    if _mulligan_diagnostic_key == code:
        return
    try:
        manual_controller.output(message)
    except Exception:
        pass
    _mulligan_diagnostic_key = code


def _sleep_with_delay(seconds: float, desc: str) -> None:
    """等待并驱动浮窗底部延时进度条（进度条识别"延时 Ns 后"字样）。

    换牌重试/等待类延时若直接用 time.sleep，浮窗倒计时表不会启动（它只
    解析含"延时/等待 Ns 后"的日志行）。这里在 sleep 前推送一条带数字的
    延时行让进度条显示当前等待，sleep 后再推"延时结束"清除进度条。
    """
    seconds = max(0.0, float(seconds))
    try:
        manual_controller.output(f"[SYS] {desc}：延时 {seconds:.0f}s 后……")
    except Exception:
        pass
    if seconds > 0:
        time.sleep(seconds)
    try:
        manual_controller.output("[SYS] 延时结束")
    except Exception:
        pass


def confirm_button_present() -> bool:
    """换牌“确认”按钮是否仍在屏幕中间（提交后应消失）。

    用于换牌点击后的二次校验：确认按钮还在 → 换牌未提交成功，需重试；
    确认按钮消失 → 已提交，等待对局开始。用 OCR 读按钮区域的“确认”二字。
    """
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
    except Exception:
        return False
    try:
        left, top, right, bottom = recommendation_config.mulligan_confirm_roi
        rgb = np.asarray(ImageGrab.grab(
            bbox=(left, top, right, bottom), all_screens=False))
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        evidence = mulligan_reader.backend.recognize(
            img, f"confirm-{time.time():.3f}", "confirm")
    except Exception:
        return False
    return any(
        "确认" in line.text and line.confidence >= 0.5
        for line in evidence.lines)


def _load_concede_config():
    """读取自动投降配置（ui_config.json 的 auto_concede 段）。"""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).resolve().parent / "ui_config.json"
        ac = json.loads(p.read_text(encoding="utf-8")).get("auto_concede") or {}
        return {
            "enabled": bool(ac.get(
                "enabled", DEFAULT_AUTO_CONCEDE["enabled"])),
            "threshold": float(ac.get(
                "threshold", DEFAULT_AUTO_CONCEDE["threshold"])),
            "rounds": max(1, int(ac.get(
                "rounds", DEFAULT_AUTO_CONCEDE["rounds"]))),
        }
    except Exception:
        return dict(DEFAULT_AUTO_CONCEDE)


def read_ai_win_rate():
    """OCR 左上角盒子“AI胜率 X%”，返回百分数值；读不到返回 None。"""
    try:
        import cv2
        import numpy as np
        from PIL import ImageGrab
        # 左上角盒子浮动条“AI胜率 49%”（1920x1080 实测区域）。
        left, top, right, bottom = 110, 8, 270, 48
        rgb = np.asarray(ImageGrab.grab(
            bbox=(left, top, right, bottom), all_screens=False))
        img = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        evidence = mulligan_reader.backend.recognize(
            img, f"winrate-{time.time():.3f}", "winrate")
    except Exception:
        return None
    for line in evidence.lines:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", line.text)
        if m:
            value = float(m.group(1))
            if 0.0 <= value <= 100.0:
                return value
    return None


def _maybe_concede(snapshot):
    """每回合检测一次左上角 AI 胜率；连续低于阈值达到设定回合数则返回 True。"""
    global _concede_streak, _concede_last_turn, _concede_triggered
    if _concede_triggered:
        return False
    cfg = _load_concede_config()
    if not cfg["enabled"]:
        return False
    turn = getattr(snapshot, "game_num_turns_in_play", 0)
    if turn == _concede_last_turn:
        return False  # 本回合已检测过
    _concede_last_turn = turn
    rate = read_ai_win_rate()
    if rate is None:
        # 读不到胜率（面板未就绪/OCR失败）：不激进投降，重置连续计数。
        if _concede_streak:
            manual_controller.output(
                "[SYS] 自动投降检测：AI胜率读取失败，连续计数清零。")
        _concede_streak = 0
        return False
    if rate < cfg["threshold"]:
        # 先 +1 再提示，第一次低于阈值就显示“连续低于 1 回合”。
        _concede_streak += 1
        manual_controller.output(
            f"[SYS] 自动投降检测：AI胜率 {rate:.1f}%（阈值 "
            f"{cfg['threshold']:.0f}%，连续低于 {_concede_streak} 回合）")
        if _concede_streak >= cfg["rounds"]:
            _concede_triggered = True
            return True
    else:
        _concede_streak = 0
        manual_controller.output(
            f"[SYS] 自动投降检测：AI胜率 {rate:.1f}%（阈值 "
            f"{cfg['threshold']:.0f}%，未低于阈值，连续计数清零）")
    return False


def _do_concede():
    """点击右下角齿轮 → 等菜单弹出 → 点中间红色“认输”。"""
    global concede_count
    concede_count += 1
    manual_controller.output("[SYS] 持续低胜率，开始自动认输……")
    try:
        with click.hearthstone_action_session():
            click.click_setting()      # 齿轮 (1895, 1060)
            time.sleep(1.0)            # 等游戏菜单弹出
            click.click_concede()      # 认输 (960, 380)
        time.sleep(1.0)
    except Exception as exc:
        manual_controller.output(f"[SYS] 自动认输点击失败：{exc}")


def run_automatic_battle_step():
    """Observe opponent turns; execute one newly validated player action."""
    global player_turn_delay_key, last_automation_diagnostic

    snapshot = refresh_snapshot()
    if snapshot is None:
        _report_automation_diagnostic(
            "power_log_unavailable", "Power.log 暂不可用，继续重试。")
        return None
    if snapshot.is_end:
        return FSM_QUITTING_BATTLE
    if _concede_triggered:
        # 已触发自动认输：等待日志确认对局真正结束（COMPLETE）后再走结算计数，
        # 避免点完认输立刻计数导致“完成对局”时机不准。
        return None
    if not snapshot.is_my_turn:
        _report_automation_diagnostic("opponent_turn", "等待对手操作。")
        # 对方回合清空延迟标记：每次切回我方回合必延时一次，
        # 同回合内多次出牌不再重复延时（不依赖可能失真的回合号）。
        player_turn_delay_key = None
        return None
    # 自动投降：只在我方回合检测（对手回合不计入“连续回合”），
    # 连续低于阈值达到设定回合数则主动认输。
    if _maybe_concede(snapshot):
        _do_concede()
        # 认输后不立即返回结算，等上面 is_end 分支（日志 COMPLETE）再计数。
        return None
    _report_automation_diagnostic("my_turn", "轮到己方操作：开始读取推荐……")
    turn = snapshot.game_num_turns_in_play
    if player_turn_delay_key != turn:
        # 每个新回合开始只延时一次（给盒子更新推荐留时间），
        # 同回合内的多次出牌操作之间不重复延时。
        player_turn_delay_key = turn
        delay = recommendation_config.pre_action_delay_seconds
        label = f"回合 {turn} 延时"
        # 第一回合额外延时：开局生效的全局卡（如黑暗主教本尼迪塔斯）要跑
        # 效果动画，盒子推荐更新更晚。这里在 pre_action 基础上按张数追加：
        #   每张生效卡 × per_card_delay（无固定基础额外）。
        if turn == 1:
            card_count = getattr(snapshot, "start_of_game_card_count", 0) or 0
            extra = (card_count
                     * recommendation_config.first_turn_per_card_delay_seconds)
            if extra > 0:
                delay += extra
                label = f"第一回合延时（{card_count} 张开局生效卡）"
        _sleep_with_delay(delay, label)
        manual_controller.output(
            f"[SYS] 回合 {turn} 延时结束，开始本轮推荐读取。")
    if recommendation_flow is None:
        return run_manual_battle_step()

    result = recommendation_flow.run_player_turn_step()
    if result.status == FlowStepStatus.RETRY:
        if result.diagnostics == "discover_choice_still_open":
            message = "发现选择仍在，准备重新点击。"
        else:
            message = (
                "当前推荐暂不可执行，继续重试："
                f"{result.diagnostics}")
        _report_automation_diagnostic(
            f"retry:{result.diagnostics}", message)
    elif result.status == FlowStepStatus.OBSERVE:
        observe_messages = {
            "opponent_turn": "等待对手操作。",
            "waiting_recommendation_update": "等待盒子更新推荐。",
            "stale_mulligan_recommendation": "等待盒子刷新对局推荐。",
        }
        message = observe_messages.get(
            result.diagnostics,
            f"自动流程观察中：{result.diagnostics}")
        _report_automation_diagnostic(
            f"observe:{result.diagnostics}", message)
    else:
        last_automation_diagnostic = None
    return None


def Battling():
    global win_count, game_count

    print_out()
    while True:
        if quitting_flag:
            sys.exit(0)
        next_state = run_automatic_battle_step()
        if next_state == FSM_QUITTING_BATTLE:
            # 对局真正结束才计数：game_count=已完成场数，win_count=胜场。
            game_count += 1
            if log_state.my_entity.query_tag("PLAYSTATE") == "WON":
                win_count += 1
                info_print("你赢得了这场对战")
            else:
                info_print("你输了")
            return next_state
        if next_state == FSM_ERROR:
            return next_state
        time.sleep(0.2)


def QuittingBattle():
    print_out()

    time.sleep(5)

    loop_count = 0
    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        state = get_screen.get_state()
        if state in [FSM_CHOOSING_HERO, FSM_LEAVE_HS]:
            return state
        click.run_hearthstone_action(lambda: (
            click.cancel_click(),
            click.test_click(),
            click.commit_error_report(),
        ))

        loop_count += 1
        if loop_count >= 15:
            return FSM_ERROR

        time.sleep(STATE_CHECK_INTERVAL+random.random()+random.random()+random.random())


def GoBackHSAction():
    global FSM_state

    print_out()
    time.sleep(3)

    while not get_screen.test_hs_available():
        if quitting_flag or stop_after_current_game:
            sys.exit(0)
        click.enter_HS()
        time.sleep(10)

    # 有时候炉石进程会直接重写Power.log, 这时应该重新创建文件操作句柄
    init()

    return FSM_WAIT_MAIN_MENU


def MainMenuAction():
    print_out()

    time.sleep(3)

    while True:
        if quitting_flag or stop_after_current_game:
            sys.exit(0)

        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)

        state = get_screen.get_state()

        # 重新连接对战之类的
        if state == FSM_BATTLING:
            ok = update_log_state()
            if ok and log_state.available:
                return FSM_BATTLING
        if state == FSM_CHOOSING_HERO:
            return FSM_CHOOSING_HERO


def WaitMainMenu():
    print_out()
    wait_main_menu_count = 0
    while get_screen.get_state() != FSM_MAIN_MENU:
        click.run_hearthstone_action(click.enter_battle_mode)
        time.sleep(5)
        wait_main_menu_count += 1
        if wait_main_menu_count >= 5:
            break
    return FSM_MAIN_MENU


def HandleErrorAction():
    print_out()

    if not get_screen.test_hs_available():
        return FSM_LEAVE_HS
    manual_controller.output("状态暂不可确认，等待后重新检测。")
    time.sleep(STATE_CHECK_INTERVAL)
    state = get_screen.get_state()
    known_states = {
        FSM_LEAVE_HS, FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_CHOOSING_CARD, FSM_BATTLING, FSM_QUITTING_BATTLE,
        FSM_WAIT_MAIN_MENU,
    }
    return state if state in known_states else FSM_ERROR


def FSM_dispatch(next_state):
    dispatch_dict = {
        FSM_LEAVE_HS: GoBackHSAction,
        FSM_MAIN_MENU: MainMenuAction,
        FSM_CHOOSING_HERO: ChoosingHeroAction,
        FSM_MATCHING: MatchingAction,
        FSM_CHOOSING_CARD: ChoosingCardAction,
        FSM_BATTLING: Battling,
        FSM_ERROR: HandleErrorAction,
        FSM_QUITTING_BATTLE: QuittingBattle,
        FSM_WAIT_MAIN_MENU: WaitMainMenu,
    }

    debug_print(f"当前状态为：+{next_state}")
    if next_state not in dispatch_dict:
        error_print("Unknown state!")
        return FSM_ERROR
    else:
        return dispatch_dict[next_state]()


def _initial_fsm_state():
    """启动/恢复时判断当前所处阶段，用于“立即接管”。

    屏幕像素(get_screen.get_state)在 BATTLING 时常不可靠，可能把对局误判成
    主菜单，导致恢复后要等下一回合才进入战斗。改用 Power.log 兜底：
    对局中(含对方回合)直接进入 Battling，换牌期进入 ChoosingCard；
    只有未对局时才退回屏幕检测。
    log_iter_func 每次开新 Power.log 会从头读到 EOF 一次性产出，
    因此一次 update_log_state() 即可把 log_state 快进到当前最新。
    """
    try:
        update_log_state()
    except Exception:
        pass
    if log_state.game_entity_id != 0 and not log_state.is_end:
        if log_state.game_num_turns_in_play > 0:
            return FSM_BATTLING
        return FSM_CHOOSING_CARD
    state = get_screen.get_state()
    return state if state else FSM_MAIN_MENU


def AutoHS_automata():
    global FSM_state, quitting_flag

    if get_screen.test_hs_available():
        hs_hwnd = get_screen.get_HS_hwnd()
        get_screen.move_window_foreground(hs_hwnd)
        time.sleep(0.5+random.random())

    # 出现这些状态时对局一定不在进行中，满足“打完本局再停止”的条件
    between_game_states = (
        FSM_MAIN_MENU, FSM_CHOOSING_HERO, FSM_MATCHING,
        FSM_WAIT_MAIN_MENU, FSM_LEAVE_HS, "",
    )

    while 1:
        if quitting_flag:
            sys.exit(0)
        if stop_after_current_game and FSM_state in between_game_states:
            info_print("已到计划停止时间，本局对战已经结束，自动化停止。")
            quitting_flag = True
            shutdown_event.set()
            sys.exit(0)
        if FSM_state == "":
            FSM_state = _initial_fsm_state()
        FSM_state = FSM_dispatch(FSM_state)





if __name__ == "__main__":
    keyboard.add_hotkey("ctrl+q", system_exit)

    init()
