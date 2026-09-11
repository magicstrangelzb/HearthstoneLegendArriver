"""活人感的生效范围：只在“对局中识别盒子意见并执行完”之后。

- RecommendationFlow：出牌/攻击/技能等推荐动作执行成功后调用 post_action_pause；
  返回 True 表示已接管延时，不再叠加固定「操作后延时」。
- MulliganFlow：盒子留牌意见提交后同样调用一次。
- 其他点击（匹配对手、选卡组、错误弹窗取消…）不经过这两个流程，因此不受影响
  —— 见 tests/test_human_like.py 里的 park_mouse 测试。
"""

import unittest
from types import SimpleNamespace

from manual_controller import ActionExecutionResult, EndTurnAction
from src.flow.mulligan_flow import MulliganFlow, MulliganStatus
from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import (
    ActionKind, FrameEvidence, OcrEvidence,
)
from src.safety.recommendation_validator import RecommendationValidator


def _frame(frame_id, exact_hash):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080), dpi=96,
        window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=exact_hash,
        perceptual_hash="0" * 64, panel_visible=True)


def _evidence(frame_id, text):
    return OcrEvidence(frame_id=frame_id, created_at=0.0, lines=(),
                       normalized_text=text, confidence=0.90,
                       backend="test", preprocessing="test")


class SequencedCapture:
    def __init__(self):
        self.frames = iter((_frame("stable", "before"),
                            _frame("current", "before"),
                            _frame("after", "after")))

    def capture(self, ocr_panel_ok=False):
        return next(self.frames)

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class SequencedReader:
    def __init__(self):
        self.read_count = 0

    def read(self, frame_supplier, _roi_supplier):
        current_frame = frame_supplier()
        self.read_count += 1
        return _evidence(current_frame.frame_id,
                         "结束回合" if self.read_count == 1 else "等待对手操作")

    @staticmethod
    def read_frame(current_frame, _roi_supplier):
        return _evidence(current_frame.frame_id, "结束回合")


class RecordingController:
    def __init__(self):
        self.actions = []

    def execute(self, action, _state):
        self.actions.append(action)
        return ActionExecutionResult(True, "executed")


class RecommendationPauseTests(unittest.TestCase):
    def _run(self, hook):
        active = SimpleNamespace(is_my_turn=True, game_num_turns_in_play=3)
        ended = SimpleNamespace(is_my_turn=False, game_num_turns_in_play=4)
        states = iter(((active, 10), (active, 10), (ended, 11)))
        sleeps = []
        flow = RecommendationFlow(
            capture=SequencedCapture(),
            reader=SequencedReader(),
            parser=RecommendationParser(),
            state_supplier=lambda: next(states),
            adapter=adapt_action,
            validator=RecommendationValidator(),
            controller=RecordingController(),
            sleep=sleeps.append,
            post_action_delay=0.2,
            post_action_pause=hook,
        )
        flow.waiting_instruction = "结束回合"
        flow.waiting_panel_hash = "0" * 64
        flow.waiting_turn_number = 3
        flow.waiting_action_kind = ActionKind.END_TURN
        result = flow.run_player_turn_step()
        return result, sleeps

    def test_hook_replaces_the_fixed_post_action_delay(self):
        calls = []

        result, sleeps = self._run(lambda: (calls.append(1), True)[1])

        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertEqual(1, len(calls))          # 推荐动作执行后调用了一次
        self.assertNotIn(0.2, sleeps)            # 不再叠加固定延时

    def test_disabled_hook_keeps_the_fixed_delay(self):
        result, sleeps = self._run(lambda: False)

        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertIn(0.2, sleeps)

    def test_hook_exception_does_not_break_the_step(self):
        def boom():
            raise RuntimeError("鼠标异常")

        result, sleeps = self._run(boom)

        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertIn(0.2, sleeps)               # 异常时回退固定延时


class _MulliganExecutor:
    def __init__(self):
        self.calls = []

    def replace_starting_card(self, index, count):
        self.calls.append(("replace", index, count))

    def commit_choose_card(self):
        self.calls.append(("commit",))

    def cancel_click(self):
        self.calls.append(("cancel",))


def _card(card_id, entity_id):
    return SimpleNamespace(card_id=card_id, entity_id=entity_id)


class MulliganPauseTests(unittest.TestCase):
    def _flow(self, hook):
        cards = tuple(_card(f"CARD_{i}", i) for i in range(3))
        state = SimpleNamespace(my_hand_cards=cards, is_end=False,
                                game_num_turns_in_play=0)
        action = SimpleNamespace(normalized_instruction="留牌1",
                                 mulligan_slots=(1,))
        executor = _MulliganExecutor()
        flow = MulliganFlow(
            executor=executor,
            action_supplier=lambda: action,
            state_supplier=lambda: state,
            stopped=lambda: False,
            first_delay=0.0,
            retry_delay=0.0,
            post_action_pause=hook,
        )
        return flow, executor

    def test_pause_runs_after_mulligan_is_executed(self):
        order = []

        def hook():
            order.append("pause")
            return True

        flow, executor = self._flow(hook)
        original_commit = executor.commit_choose_card

        def commit():
            original_commit()
            order.append("commit")

        executor.commit_choose_card = commit

        result = flow.run()

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual(["commit", "pause"], order)

    def test_pause_exception_does_not_break_mulligan(self):
        def boom():
            raise RuntimeError("鼠标异常")

        flow, executor = self._flow(boom)

        result = flow.run()

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertIn(("commit",), executor.calls)


if __name__ == "__main__":
    unittest.main()
