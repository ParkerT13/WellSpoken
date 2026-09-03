from __future__ import annotations

import re
import subprocess
from pathlib import Path

from wellspoken.media.ffmpeg_runner import CREATE_NO_WINDOW, FFMPEG_EXE

_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(
    audio_path: str | Path, noise_db: float = -30, min_duration: float = 0.5
) -> list[tuple[float, float]]:
    """Ranges of near-silence in `audio_path`, via ffmpeg's own `silencedetect`
    filter (parsed from stderr - the filter has no other output mode). Used to
    suggest dead-air cuts on the Timeline tab; never applied automatically."""
    args = [
        FFMPEG_EXE, "-hide_banner", "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(args, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)

    ranges: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in proc.stderr.splitlines():
        m = _START_RE.search(line)
        if m:
            pending_start = float(m.group(1))
            continue
        m = _END_RE.search(line)
        if m and pending_start is not None:
            ranges.append((pending_start, float(m.group(1))))
            pending_start = None
    return ranges
