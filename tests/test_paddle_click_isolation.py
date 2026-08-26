import importlib
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from src.ocr.paddle_adapter import PaddleOcrAdapter


class PaddleClickIsolationTests(unittest.TestCase):
    def test_load_uses_external_click_and_restores_project_click(self):
        project_click = importlib.import_module("click")
        previous_paddleocr = sys.modules.pop("paddleocr", None)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            (temp_root / "paddleocr.py").write_text(textwrap.dedent("""
                import click

                if not hasattr(click, "command"):
                    raise AttributeError("module 'click' has no attribute 'command'")

                class PaddleOCR:
                    def __init__(self, **kwargs):
                        self.kwargs = kwargs
            """), encoding="utf-8")
            sys.path.insert(0, temp_dir)
            try:
                adapter = PaddleOcrAdapter(
                    model_root=temp_root / "models",
                ).load()
            finally:
                sys.path.remove(temp_dir)
                sys.modules.pop("paddleocr", None)
                if previous_paddleocr is not None:
                    sys.modules["paddleocr"] = previous_paddleocr

        self.assertIsNotNone(adapter.engine)
        self.assertIs(sys.modules.get("click"), project_click)


if __name__ == "__main__":
    unittest.main()
