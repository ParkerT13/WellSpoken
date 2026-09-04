from __future__ import annotations

import re
from pathlib import Path

from wellspoken.tts.lexicon import Lexicon

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
VOICE_REFS_DIR = ROOT_DIR / "assets" / "voice_refs"

# Chatterbox-Turbo is voice-cloning based (no built-in preset voices), so
# each curated voice is a short public-domain reference clip to clone from -
# see assets/voice_refs/NOTICE.md for where these come from.
REFERENCE_CLIPS = {
    "chatterbox_female": VOICE_REFS_DIR / "chatterbox_female_ref.wav",
    "chatterbox_male": VOICE_REFS_DIR / "chatterbox_male_ref.wav",
}

# Turbo has a fixed internal generation-step budget (visible as a hard-capped
# progress bar during inference) and does NOT error when text runs past it -
# it silently degrades into incoherent, unrelated-sounding audio instead
# (verified empirically: a ~180-word block produced complete gibberish, while
# the same text split into <=3-sentence/~330-char chunks came out clean; a
# single 553-char/5-sentence block already showed real degradation). This
# mirrors why Kokoro's own KPipeline auto-chunks internally - text is grouped
# into sentence-based chunks under this budget, synthesized separately, and
# concatenated, with a safety margin below the point degradation was observed.
MAX_CHUNK_CHARS = 300

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_model = None


def _get_model():
    global _model
    if _model is None:
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        from wellspoken.device import get_device

        _model = ChatterboxTurboTTS.from_pretrained(device=get_device())
    return _model


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text.strip()]


class ChatterboxEngine:
    def __init__(self, voice_id: str, lexicon: Lexicon | None = None):
        self.voice_id = voice_id
        self.lexicon = lexicon or Lexicon()
        if voice_id in REFERENCE_CLIPS:
            self.ref_clip = REFERENCE_CLIPS[voice_id]
        else:
            # Not a curated preset - check user-imported/cloned voices
            # (see custom_voices.py), which use this same zero-shot cloning
            # engine with a different reference clip.
            from wellspoken.tts.custom_voices import get_custom_voice

            custom = get_custom_voice(voice_id)
            if custom is None:
                raise ValueError(f"Unknown Chatterbox voice_id: {voice_id!r}")
            self.ref_clip = custom.ref_clip_path

    def synthesize_to_wav(self, text: str, wav_path: str | Path, on_progress=None) -> Path:
        import torch
        import torchaudio as ta

        wav_path = Path(wav_path)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        spoken_text = self.lexicon.apply(text)

        model = _get_model()
        chunks = _chunk_text(spoken_text)
        # Turbo ignores exaggeration/cfg_weight (it warns and no-ops if passed) -
        # inflection control on this model comes entirely from the reference
        # clip's own delivery, not a runtime knob.
        waveforms = []
        for i, chunk in enumerate(chunks, start=1):
            if on_progress and len(chunks) > 1:
                # Chatterbox runs several times slower than realtime on CPU, so
                # a multi-chunk script can take minutes - without per-chunk
                # progress the UI just shows one frozen message the whole time.
                on_progress(f"Synthesizing narration (part {i} of {len(chunks)})...")
            waveforms.append(model.generate(chunk, audio_prompt_path=str(self.ref_clip)))
        wav = waveforms[0] if len(waveforms) == 1 else torch.cat(waveforms, dim=-1)
        ta.save(str(wav_path), wav.cpu(), model.sr)
        return wav_path
