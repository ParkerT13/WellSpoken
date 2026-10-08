from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from wellspoken.media.waveform import extract_pcm

SAMPLE_RATE = 16000

_model = None
_get_speech_timestamps = None


def _load_model():
    global _model, _get_speech_timestamps
    if _model is None:
        _model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)
        _get_speech_timestamps = utils[0]
    return _model, _get_speech_timestamps


def detect_dead_air(audio_path: str | Path, min_duration: float = 0.5) -> list[tuple[float, float]]:
    """Ranges of non-speech (silence, breathing, room hum, keyboard clicks) in
    `audio_path`, via Silero VAD rather than a flat dB threshold - raw mic
    recordings have exactly the kind of background noise and quiet speech
    that make ffmpeg's silencedetect (see media/silence.py) unreliable. Used
    for the pre-transcribe "clean up my dialog" path on the Timeline tab; the
    clean TTS/narration path keeps using silencedetect, which already works
    well there and isn't worth touching.

    Same (start, end) list contract as silence.detect_silence, so callers can
    treat the two interchangeably.
    """
    model, get_speech_timestamps = _load_model()
    samples = extract_pcm(audio_path, sample_rate=SAMPLE_RATE)
    if len(samples) == 0:
        return []
    audio = torch.from_numpy(samples.astype(np.float32) / 32768.0)
    speech = get_speech_timestamps(audio, model, sampling_rate=SAMPLE_RATE, return_seconds=True)
    duration = len(samples) / SAMPLE_RATE

    ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for seg in speech:
        start = seg["start"]
        if start - cursor >= min_duration:
            ranges.append((cursor, start))
        cursor = max(cursor, seg["end"])
    if duration - cursor >= min_duration:
        ranges.append((cursor, duration))
    return ranges
