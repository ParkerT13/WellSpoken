from __future__ import annotations

import re
from pathlib import Path

from faster_whisper import WhisperModel

from wellspoken.captions.align import group_words_into_segments
from wellspoken.models import CaptionSegment, WordTiming
from wellspoken.tts.lexicon import Lexicon

# medium.en has a meaningfully lower word-error-rate than small.en (~12% vs
# ~15%, per Distil-Whisper's published benchmarks) - verified concretely on a
# real recording too: medium.en correctly heard "wells run into trouble
# zones" where small.en heard "balls run into trouble zones". Slower on CPU,
# but accuracy was the explicit complaint this change is fixing.
DEFAULT_MODEL_SIZE = "medium.en"

_model_cache: dict[str, WhisperModel] = {}

# Whisper's most common hallucination pattern on silence/near-silence is
# repeating a short phrase several times in a row (e.g. "thank you thank you
# thank you...") - a documented failure mode, not something that happens in
# real narration. Segments matching this are dropped rather than trusted.
_REPEAT_PHRASE_RE = re.compile(r"\b(\w+(?:\s+\w+){0,4})\b(?:[\s.,!?]+\1\b){2,}", re.IGNORECASE)


def get_model(model_size: str = DEFAULT_MODEL_SIZE) -> WhisperModel:
    if model_size not in _model_cache:
        _model_cache[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _model_cache[model_size]


def _is_hallucinated_repeat(text: str) -> bool:
    return bool(_REPEAT_PHRASE_RE.search(text))


def transcribe(
    audio_path: str | Path,
    source: str = "transcribed",
    model_size: str = DEFAULT_MODEL_SIZE,
    initial_prompt: str | None = None,
    lexicon: Lexicon | None = None,
) -> list[CaptionSegment]:
    """Transcribe audio into word-level-timestamped CaptionSegments.

    Used both for real narration (source="transcribed") and to re-transcribe
    the TTS engine's own synthesized audio (source="tts") for timing + pronunciation QC.

    vad_filter strips non-speech before decoding - this both speeds things up
    and, more importantly, stops Whisper from inventing sentences over
    silence (verified empirically: without it, a screen-recording demo that
    just ends in silence got a fabricated "please visit www.ROP.com" outro
    line that was never actually said).

    initial_prompt (typically the project's pronunciation lexicon's known
    terms - see Lexicon.prompt_text()) biases decoding toward that
    vocabulary, which fixes proper-noun misrecognition that model size alone
    doesn't (verified: "SeisWare" decoded as "Isos"/"Heisler's" without a
    prompt, correctly as "SeisWare" with one).

    lexicon, if given, also fixes the CAPITALIZATION of recognized terms
    afterward via Lexicon.canonicalize() - even once Whisper correctly hears
    "SeisWare" as a word, it has no way to know the brand name has a capital
    W in the middle, and writes ordinary-English-rules "Seisware" instead.
    """
    model = get_model(model_size)
    segments, _info = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt=initial_prompt,
    )

    words: list[WordTiming] = []
    for seg in segments:
        if _is_hallucinated_repeat(seg.text):
            continue
        for w in seg.words or []:
            words.append(
                WordTiming(
                    word=w.word.strip(),
                    start=w.start,
                    end=w.end,
                    confidence=float(w.probability) if w.probability is not None else None,
                )
            )

    result = group_words_into_segments(words, source=source)
    if lexicon:
        for seg in result:
            seg.text = lexicon.canonicalize(seg.text)
            for w in seg.words:
                w.word = lexicon.canonicalize(w.word)
    return result
