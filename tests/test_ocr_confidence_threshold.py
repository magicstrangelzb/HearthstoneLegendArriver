import unittest
from types import SimpleNamespace

import numpy as np

from src.ocr.stable_reader import StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_config import RecommendationConfig
from src.recommendation_models import OcrEvidence, OcrLine


class FixedConfidenceBackend:
    def recognize(self, _image, frame_id, preprocessing):
        lines = (
            OcrLine("打法参考A", 0.70),
            OcrLine("保留全部卡牌", 0.70),
        )
        return OcrEvidence(
            frame_id=frame_id,
            created_at=0.0,
            lines=lines,
            normalized_text="打法参考A\n保留全部卡牌",
            confidence=0.70,
            backend="fixed-confidence",
            preprocessing=preprocessing,
        )


class OcrConfidenceThresholdTests(unittest.TestCase):
    def test_two_matching_recommendations_at_point_seven_are_accepted(self):
        frame_ids = iter(("frame-1", "frame-2", "frame-3"))
        reader = StableRecommendationReader(
            RecommendationConfig(),
            FixedConfidenceBackend(),
            sleep=lambda _seconds: None,
            text_normalizer=RecommendationParser.normalize_action_text,
            required_headers=("打法参考A", "打法参考Ａ"),
        )

        evidence = reader.read(
            lambda: SimpleNamespace(frame_id=next(frame_ids)),
            lambda _frame: np.zeros((8, 8, 3), dtype=np.uint8),
        )

        self.assertEqual("保留全部卡牌", evidence.normalized_text)
        self.assertEqual(0.70, evidence.confidence)


if __name__ == "__main__":
    unittest.main()
