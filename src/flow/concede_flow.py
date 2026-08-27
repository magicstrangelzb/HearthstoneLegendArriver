"""Fail closed when a verified concede UI sequence is unavailable."""

from dataclasses import dataclass
from enum import Enum


class ConcedeStatus(str, Enum):
    CONCEDED = "conceded"
    STOPPED_WITHOUT_INPUT = "stopped_without_input"
    FAILED_SAFE = "failed_safe"


@dataclass(frozen=True)
class LocatedButton:
    name: str
    frame_id: str
    confidence: float
    center: tuple[int, int]


@dataclass(frozen=True)
class ConcedeResult:
    status: ConcedeStatus
    diagnostics: str = ""


class ConcedeFlow:
    def __init__(self, locator, executor, state_detector,
                 stopped=lambda: False, min_confidence=0.90):
        self.locator = locator
        self.executor = executor
        self.state_detector = state_detector
        self.stopped = stopped
        self.min_confidence = min_confidence

    def run(self):
        try:
            for name in ("settings", "concede", "confirm"):
                if self.stopped():
                    return ConcedeResult(
                        ConcedeStatus.STOPPED_WITHOUT_INPUT, "stopped")
                element = self.locator.locate(name)
                if (element is None or element.name != name
                        or element.confidence < self.min_confidence
                        or not element.frame_id):
                    return ConcedeResult(
                        ConcedeStatus.STOPPED_WITHOUT_INPUT,
                        f"{name}_not_verified")
                self.executor.click_located(element)
            if self.state_detector() in {"game_end", "menu", "deck_select"}:
                return ConcedeResult(ConcedeStatus.CONCEDED)
            return ConcedeResult(ConcedeStatus.FAILED_SAFE,
                                 "concede_not_confirmed")
        except Exception as exc:
            return ConcedeResult(
                ConcedeStatus.FAILED_SAFE,
                f"{type(exc).__name__}:{exc}")
