from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from wellspoken.media.ffmpeg_runner import CREATE_NO_WINDOW, FFMPEG_EXE


def extract_pcm(audio_path: str | Path, sample_rate: int = 8000) -> np.ndarray:
    """Decode `audio_path` to mono int16 PCM via an ffmpeg pipe, for waveform
    rendering. A low sample rate is plenty for visualization and keeps the
    array small/fast - this is not used for playback, just the picture."""
    args = [
        FFMPEG_EXE, "-y", "-hide_banner", "-i", str(audio_path),
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "-acodec", "pcm_s16le", "pipe:1",
    ]
    proc = subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to decode audio for waveform:\n{proc.stderr.decode(errors='replace')}")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def peak_pairs(samples: np.ndarray, target_width: int) -> tuple[np.ndarray, np.ndarray]:
    """Downsample `samples` into `target_width` (min, max) pairs, one per
    pixel column - the standard technique for fast, non-aliased waveform
    rendering regardless of zoom level (recomputed on zoom change rather than
    pre-cached per level, since even a full narration track is small at 8kHz
    mono)."""
    if len(samples) == 0 or target_width <= 0:
        return np.zeros(0), np.zeros(0)
    target_width = min(target_width, len(samples)) or 1
    chunks = np.array_split(samples, target_width)
    mins = np.array([c.min() for c in chunks], dtype=np.float32)
    maxes = np.array([c.max() for c in chunks], dtype=np.float32)
    return mins, maxes
