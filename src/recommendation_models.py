"""Immutable evidence and action protocol shared across pipeline layers."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class ActionKind(str, Enum):
    MULLIGAN = "mulligan"
    PLAY_CARD = "play_card"
    TRADE_CARD = "trade_card"
    USE_HERO_POWER = "use_hero_power"
    ATTACK = "attack"
    USE_LOCATION = "use_location"
    CHOOSE_DISCOVER = "choose_discover"
    END_TURN = "end_turn"


@dataclass(frozen=True)
class SlotRef:
    kind: str
    owner: Optional[str] = None
    index: Optional[int] = None


@dataclass(frozen=True)
class FrameEvidence:
    frame_id: str
    captured_at: float
    desktop_size: tuple[int, int]
    dpi: int
    window_handle: int
    foreground: bool
    recommendation_roi: tuple[int, int, int, int]
    exact_hash: str
    perceptual_hash: str
    panel_visible: bool
    pixels: Any = None


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float
    box: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class OcrEvidence:
    frame_id: str
    created_at: float
    lines: tuple[OcrLine, ...]
    normalized_text: str
    confidence: float
    backend: str
    preprocessing: str


@dataclass(frozen=True)
class ProposedAction:
    action_id: str
    frame_id: str
    created_at: float
    turn_number: int
    log_revision: int
    raw_instruction: str
    normalized_instruction: str
    action: ActionKind
    source: Optional[SlotRef] = None
    destination: Optional[SlotRef] = None
    target: Optional[SlotRef] = None
    mulligan_slots: tuple[int, ...] = ()
    card_type: Optional[str] = None
    ocr_confidence: float = 0.0
    semantic_confidence: float = 0.0


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    code: str
    diagnostics: tuple[str, ...] = ()
    value: Any = None

    @classmethod
    def allow(cls, value: Any) -> "ValidationResult":
        return cls(True, "accepted", value=value)

    @classmethod
    def reject(cls, code: str, *diagnostics: str) -> "ValidationResult":
        return cls(False, code, diagnostics)
