from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal, Optional


@dataclass
class WordTiming:
    word: str
    start: float
    end: float
    confidence: Optional[float] = None


@dataclass
class CaptionSegment:
    index: int
    start: float
    end: float
    text: str
    source: Literal["tts", "transcribed"]
    words: list[WordTiming] = field(default_factory=list)
    confidence: Optional[float] = None
    flagged: bool = False
    original_script_text: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict) -> "CaptionSegment":
        words = [WordTiming(**w) for w in d.get("words", [])]
        d = dict(d)
        d["words"] = words
        return CaptionSegment(**d)
