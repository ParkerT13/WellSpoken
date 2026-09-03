from __future__ import annotations

import re
import sys
import types
import wave
from pathlib import Path

import numpy as np

from wellspoken.tts.lexicon import Lexicon

SAMPLE_RATE = 24000

# Matches hyphenated letter-acronym respellings the lexicon produces, e.g.
# "R-O-P", "E-U-R" (two or more single letters joined by hyphens).
_ACRONYM_RE = re.compile(r"\b[A-Za-z](?:-[A-Za-z]){1,}\b")
# Verified empirically: Kokoro's default cadence (speed=1) reads a spelled-out
# acronym like "R-O-P" noticeably slower/more deliberate than a person
# actually rattling off letters - this speeds up only those tokens, leaving
# the rest of the narration's natural pace untouched.
ACRONYM_SPEED = 1.35

# KPipeline construction loads the underlying model - expensive, so pipelines
# are cached per language code and shared across engine instances/voice switches.
_pipelines: dict = {}


def _block_espeak_backend() -> None:
    """kokoro.pipeline does `from misaki import en, espeak` unconditionally at
    import time, and the real misaki/espeak.py imports `phonemizer` and loads
    the espeak-ng shared library - both GPL-3.0 - as its out-of-dictionary
    word fallback for English. WellSpoken can never link GPL code into a
    closed-source commercial build (same constraint that ruled out OBS/MLT/
    libopenshot/LosslessCut elsewhere in this project), so this installs a
    stub `misaki.espeak` module in sys.modules before kokoro is ever
    imported, keeping the real phonemizer package from loading into the
    process at all. kokoro's own KPipeline already wraps EspeakFallback
    construction in try/except and degrades gracefully to fallback=None
    (out-of-dictionary words are skipped rather than mispronounced) if it
    raises - this stub's constructor raises immediately, so that documented
    degradation path is what actually runs. This is why the pronunciation
    lexicon (assets/lexicon_default.json) matters: any domain word not in
    misaki's built-in dictionary needs a lexicon respelling using ordinary
    English words, or it won't be pronounced at all.
    """
    if "misaki.espeak" in sys.modules:
        return
    stub = types.ModuleType("misaki.espeak")

    class _EspeakDisabled:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("espeak-ng backend disabled (GPL-3.0 avoidance) - see _block_espeak_backend")

    stub.EspeakFallback = _EspeakDisabled
    stub.EspeakG2P = _EspeakDisabled
    sys.modules["misaki.espeak"] = stub


def _get_pipeline(lang_code: str):
    _block_espeak_backend()
    from kokoro import KPipeline

    if lang_code not in _pipelines:
        _pipelines[lang_code] = KPipeline(lang_code=lang_code)
    return _pipelines[lang_code]


def _trim_silence(audio, trim_start: bool = True, trim_end: bool = True, threshold: float = 0.02, pad_samples: int = 200):
    """Trim leading/trailing near-silence from one synthesized chunk.

    Kokoro pads each pipeline() call with ~0.4-0.7s of silence at both ends
    (verified via silencedetect) - fine for a single whole-text call, but
    when a sentence is split into several calls (see _split_for_speed) those
    paddings stack at every join and badly inflate total duration (a 5.4s
    sentence became 8.2s in testing). Trimming each internal join before
    concat removes that without re-encoding; the outermost edges of the
    whole narration keep trim_start/trim_end False so overall lead-in/
    trail-off silence is unchanged. pad_samples keeps a small buffer so the
    cut doesn't clip the actual attack/decay of the speech."""
    if not trim_start and not trim_end:
        return audio
    arr = audio.detach().cpu().numpy()
    mask = np.abs(arr) > threshold
    if not mask.any():
        return audio
    start = max(0, int(np.argmax(mask)) - pad_samples) if trim_start else 0
    end = min(len(arr), len(arr) - int(np.argmax(mask[::-1])) + pad_samples) if trim_end else len(arr)
    return audio[start:end]


def _split_for_speed(text: str) -> list[tuple[str, float]]:
    """Split text into (chunk, speed) pieces so hyphenated letter-acronyms
    ("R-O-P") synthesize at ACRONYM_SPEED while everything else stays at the
    normal speed=1 pace. Each piece becomes its own pipeline() call since
    Kokoro's speed param applies uniformly per call."""
    pieces: list[tuple[str, float]] = []
    last_end = 0
    for m in _ACRONYM_RE.finditer(text):
        if m.start() > last_end:
            pieces.append((text[last_end:m.start()], 1.0))
        pieces.append((m.group(0), ACRONYM_SPEED))
        last_end = m.end()
    if last_end < len(text):
        pieces.append((text[last_end:], 1.0))
    return pieces or [(text, 1.0)]


class KokoroEngine:
    def __init__(self, voice_id: str, lexicon: Lexicon | None = None, lang_code: str = "a"):
        self.voice_id = voice_id
        self.lexicon = lexicon or Lexicon()
        self.pipeline = _get_pipeline(lang_code)

    def synthesize_to_wav(self, text: str, wav_path: str | Path, on_progress=None) -> Path:
        # on_progress accepted for interface parity with ChatterboxEngine
        # (multi-chunk, slow enough to need progress) - Kokoro is fast enough
        # end-to-end that per-chunk progress isn't needed here.
        import torch

        wav_path = Path(wav_path)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        spoken_text = self.lexicon.apply(text)

        pieces = [(p, s) for p, s in _split_for_speed(spoken_text) if p.strip()]
        piece_chunks = [
            [r.audio for r in self.pipeline(piece, voice=self.voice_id, speed=speed) if r.audio is not None]
            for piece, speed in pieces
        ]
        multi_call = len(pieces) > 1 or any(len(pc) > 1 for pc in piece_chunks)
        chunks = []
        total = sum(len(pc) for pc in piece_chunks)
        idx = 0
        for pc in piece_chunks:
            for chunk in pc:
                is_first = idx == 0
                is_last = idx == total - 1
                idx += 1
                if multi_call:
                    chunk = _trim_silence(chunk, trim_start=not is_first, trim_end=not is_last)
                chunks.append(chunk)
        if not chunks:
            raise RuntimeError("Kokoro produced no audio for the given text.")
        audio = chunks[0] if len(chunks) == 1 else torch.cat(chunks)
        audio_np = np.clip(audio.detach().cpu().numpy(), -1.0, 1.0)
        pcm16 = (audio_np * 32767).astype(np.int16)

        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm16.tobytes())
        return wav_path
