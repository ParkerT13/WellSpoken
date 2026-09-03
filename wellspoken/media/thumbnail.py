from __future__ import annotations

from pathlib import Path

from wellspoken.media.ffmpeg_runner import grab_frame


def make_thumbnail(video_path: str | Path, out_path: str | Path, at_seconds: float = 1.0) -> Path:
    return grab_frame(video_path, out_path, at_seconds=at_seconds)
