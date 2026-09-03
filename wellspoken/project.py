from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

from wellspoken.captions.style import CaptionStyle
from wellspoken.models import CaptionSegment


@dataclass
class Project:
    name: str = "Untitled"
    source_video: Optional[str] = None
    workflow: Literal["script", "transcribe"] = "script"
    script_text: str = ""
    narration_audio: Optional[str] = None  # user-supplied narration, or synthesized voice output
    segments: list[CaptionSegment] = field(default_factory=list)
    intro_kind: Literal["none", "template", "clip"] = "none"
    intro_title: str = ""
    intro_clip_path: Optional[str] = None
    outro_kind: Literal["none", "template", "clip"] = "none"
    outro_title: str = ""
    outro_clip_path: Optional[str] = None
    caption_mode: Literal["burned_in", "sidecar", "both"] = "both"
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    background_music_path: Optional[str] = None
    background_music_volume: float = 0.15
    logo_path: Optional[str] = None  # None = use the bundled default (see gui.tab_intro_outro.DEFAULT_LOGO_PATH)
    watermark_enabled: bool = False
    watermark_position: Literal["bottom-left", "bottom-right"] = "bottom-right"
    voice_name: str = "af_heart"
    output_dir: Optional[str] = None
    aspect_ratio: Literal["original", "16:9", "9:16", "1:1"] = "original"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["segments"] = [s.to_dict() for s in self.segments]
        return d

    @staticmethod
    def from_dict(d: dict) -> "Project":
        d = dict(d)
        segments = [CaptionSegment.from_dict(s) for s in d.get("segments", [])]
        d["segments"] = segments
        style = d.get("caption_style")
        d["caption_style"] = CaptionStyle(**style) if isinstance(style, dict) else CaptionStyle()
        return Project(**d)

    def resolve_narration_for_render(self) -> Optional[str]:
        """The standalone narration track (if any) that render() must mux into
        the video. None means the source video's own audio should be kept as-is
        (the transcribe workflow when narration_audio == source_video)."""
        if self.narration_audio and self.narration_audio != self.source_video:
            return self.narration_audio
        return None

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "Project":
        path = Path(path)
        return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
