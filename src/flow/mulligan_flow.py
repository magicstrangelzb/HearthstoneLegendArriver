"""Execute all validated mulligan slots and confirm immediately."""

from dataclasses import dataclass
from enum import Enum
import time


class MulliganStatus(str, Enum):
    CONFIRMED = "confirmed"
    CONCEDE = "concede"


@dataclass(frozen=True)
class MulliganResult:
    status: MulliganStatus
    selected_zero_based_slots: tuple[int, ...] = ()
    diagnostics: str = ""


class MulliganFlow:
    def __init__(self, executor, action_supplier, state_supplier,
                 action_context=None, stopped=lambda: False,
                 sleep=time.sleep, pre_action_delay=0.0,
                 first_delay=None, retry_delay=None, confirm_ready=None):
        self.executor = executor
        self.action_supplier = action_supplier
        self.state_supplier = state_supplier
        self.stopped = stopped
        self.sleep = sleep
        self.first_delay = first_delay if first_delay is not None else pre_action_delay
        self.retry_delay = retry_delay if retry_delay is not None else pre_action_delay
        # 换牌面板“确认”按钮是否就绪的检测回调（None = 不启用前置检测）。
        # 用于“先确认面板在再执行换牌”：按钮不在场就不点击，避免面板未
        # 就绪时盲点。典型实现见 FSM_action.confirm_button_present()。
        self.confirm_ready = confirm_ready
        self._delay_done = False
        if action_context is None:
            from contextlib import nullcontext
            action_context = nullcontext
        self.action_context = action_context

    def run(self):
        try:
            initial = self.state_supplier()
            action = self.action_supplier()
            current_action = self.action_supplier()
            fresh = self.state_supplier()
            if self.stopped():
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="stopped")
            if self._identity(initial) != self._identity(fresh):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="hand_changed")
            # 换牌阶段 Power.log 会持续写入新行，log_revision 必然漂移；
            # 若要求 initial==fresh 的 revision 完全一致，会在“识别到点击”
            # 之前就频繁返回 revision_changed，导致换牌卡住。这里只要求
            # 手牌指纹与对局阶段稳定，不再要求日志版本号一致。
            if (getattr(fresh, "is_end", False)
                    or getattr(fresh, "game_num_turns_in_play", 0) != 0):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_stage_changed")
            if (current_action.normalized_instruction
                    != action.normalized_instruction
                    or current_action.mulligan_slots != action.mulligan_slots):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="recommendation_changed")
            count = len(fresh.my_hand_cards)
            selected = tuple(sorted({slot - 1 for slot in action.mulligan_slots}))
            clickable_count = {3: 3, 5: 4}.get(count, 0)
            if count not in (3, 5) or any(
                    index < 0 or index >= clickable_count
                    for index in selected):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_slot_invalid")
            # 前置门：确认按钮在场才执行换牌。按钮不在 → 说明面板未就绪
            # 或已提交，交给外层继续轮询，不盲目点击。
            if self.confirm_ready is not None and not self.confirm_ready():
                return MulliganResult(
                    MulliganStatus.CONCEDE,
                    diagnostics="confirm_button_absent")
            delay = self.retry_delay if self._delay_done else self.first_delay
            if delay > 0:
                print(f"已识别换牌建议，等待 {delay:.0f}s 后执行……")
                self.sleep(delay)
            self._delay_done = True
            with self.action_context():
                for index in selected:
                    self.executor.replace_starting_card(index, count)
                self.executor.commit_choose_card()
            return MulliganResult(MulliganStatus.CONFIRMED, selected)
        except Exception as exc:
            try:
                self.executor.cancel_click()
            except Exception:
                pass
            return MulliganResult(
                MulliganStatus.CONCEDE,
                diagnostics=f"{type(exc).__name__}:{exc}")

    def reset_delay(self):
        """每局换牌开始时调用：首次用 ready_delay，重试用 retry_delay。"""
        self._delay_done = False

    @staticmethod
    def _identity(state):
        return tuple((card.card_id, getattr(card, "entity_id", None))
                     for card in state.my_hand_cards)
