from __future__ import annotations

import sys
import types
import wave
from pathlib import Path

import numpy as np

from wellspoken.tts.lexicon import Lexicon

SAMPLE_RATE = 24000

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

        chunks = [r.audio for r in self.pipeline(spoken_text, voice=self.voice_id) if r.audio is not None]
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
