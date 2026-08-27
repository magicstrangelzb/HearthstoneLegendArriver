import unittest
from types import SimpleNamespace

from manual_controller import ActionExecutionResult, EndTurnAction
from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import ActionKind, FrameEvidence, OcrEvidence
from src.safety.recommendation_validator import RecommendationValidator


def frame(frame_id, exact_hash):
    return FrameEvidence(
        frame_id=frame_id,
        captured_at=0.0,
        desktop_size=(1920, 1080),
        dpi=96,
        window_handle=1,
        foreground=True,
        recommendation_roi=(7, 32, 278, 970),
        exact_hash=exact_hash,
        perceptual_hash="0" * 64,
        panel_visible=True,
    )


def evidence(frame_id, text):
    return OcrEvidence(
        frame_id=frame_id,
        created_at=0.0,
        lines=(),
        normalized_text=text,
        confidence=0.90,
        backend="test",
        preprocessing="test",
    )


class SequencedCapture:
    def __init__(self):
        self.frames = iter((
            frame("stable", "before"),
            frame("current", "before"),
            frame("after", "after"),
        ))

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
        text = "结束回合" if self.read_count == 1 else "等待对手操作"
        return evidence(current_frame.frame_id, text)

    @staticmethod
    def read_frame(current_frame, _roi_supplier):
        return evidence(current_frame.frame_id, "结束回合")


class RecordingController:
    def __init__(self):
        self.actions = []

    def execute(self, action, _state):
        self.actions.append(action)
        return ActionExecutionResult(True, "executed")


class RepeatedRecommendationTests(unittest.TestCase):
    def test_same_recommendation_continues_to_execute(self):
        active = SimpleNamespace(is_my_turn=True, game_num_turns_in_play=3)
        ended = SimpleNamespace(is_my_turn=False, game_num_turns_in_play=4)
        states = iter(((active, 10), (active, 10), (ended, 11)))
        controller = RecordingController()
        flow = RecommendationFlow(
            capture=SequencedCapture(),
            reader=SequencedReader(),
            parser=RecommendationParser(),
            state_supplier=lambda: next(states),
            adapter=adapt_action,
            validator=RecommendationValidator(),
            controller=controller,
        )
        flow.waiting_instruction = "结束回合"
        flow.waiting_panel_hash = "0" * 64
        flow.waiting_turn_number = 3
        flow.waiting_action_kind = ActionKind.END_TURN

        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertEqual(1, len(controller.actions))
        self.assertIsInstance(controller.actions[0], EndTurnAction)


if __name__ == "__main__":
    unittest.main()
