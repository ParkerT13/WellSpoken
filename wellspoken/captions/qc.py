from __future__ import annotations

import difflib
import re
from typing import TYPE_CHECKING, Optional

from wellspoken.captions.align import group_words_into_segments
from wellspoken.models import CaptionSegment, WordTiming

if TYPE_CHECKING:
    from wellspoken.tts.lexicon import Lexicon

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _norm(token: str) -> str:
    return "".join(_WORD_RE.findall(token)).lower()


def reconcile_tts_captions(
    heard_segments: list[CaptionSegment], intended_script: str, lexicon: Optional["Lexicon"] = None
) -> list[CaptionSegment]:
    """Rebuild caption segments using the intended script's own wording (correct
    spelling/punctuation) with timestamps taken from the synthesized narration
    audio, via its whisper re-transcription in `heard_segments`.

    Whisper's re-transcription is noisy even when the TTS pronunciation is
    perfectly fine (e.g. "Wolfcamp" heard back as "wolf camp", "lithology" as
    "lethology") - using it verbatim as caption text would introduce spelling
    drift the script never had. So captions are always built from the script's
    own words; only the *timing* comes from what was actually heard. Where the
    heard audio doesn't line up with the script at all (a real dropped or
    garbled word), the segment is flagged so the user can add a pronunciation
    lexicon override and re-synthesize.

    Because captions always come from intended_script (never from what
    Whisper heard), a whisper_engine.transcribe(lexicon=...) fix on the ASR
    side never reaches these captions at all - if the user typed "Seisware"
    with a lowercase w, that's exactly what would show up here regardless.
    lexicon.canonicalize() run on the script up front is what actually fixes
    known terms' capitalization for this workflow (verified: this was a real
    reported gap - the ASR-side fix alone did nothing for AI Voice captions).
    """
    if lexicon:
        intended_script = lexicon.canonicalize(intended_script)
    heard_words: list[WordTiming] = [w for seg in heard_segments for w in seg.words]
    intended_tokens = intended_script.split()

    intended_norm = [_norm(t) for t in intended_tokens]
    heard_norm = [_norm(w.word) for w in heard_words]

    matcher = difflib.SequenceMatcher(a=intended_norm, b=heard_norm, autojunk=False)
    reconciled: list[WordTiming] = []
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if a1 == a2:
            continue  # heard audio had extra words with nothing intended - drop them
        if tag == "equal":
            for ai, bi in zip(range(a1, a2), range(b1, b2)):
                h = heard_words[bi]
                reconciled.append(
                    WordTiming(word=intended_tokens[ai], start=h.start, end=h.end, confidence=h.confidence)
                )
            continue

        if b1 < b2:
            span_start, span_end = heard_words[b1].start, heard_words[b2 - 1].end
        elif heard_words:
            # Pure deletion: no heard words at all for this intended span -
            # anchor on the nearest heard word so the gap still lands roughly
            # in the right place in the timeline.
            anchor = heard_words[min(b1, len(heard_words) - 1)]
            span_start = span_end = anchor.start
        else:
            span_start = span_end = 0.0

        n = a2 - a1
        step = (span_end - span_start) / n if span_end > span_start else 0.0
        for i, ai in enumerate(range(a1, a2)):
            ws = span_start + step * i
            we = ws + step if step else span_end
            reconciled.append(WordTiming(word=intended_tokens[ai], start=ws, end=we, confidence=None))

    segments = group_words_into_segments(reconciled, source="tts")
    for seg in segments:
        seg.flagged = any(w.confidence is None for w in seg.words)
        if seg.flagged:
            seg.original_script_text = intended_script
    return segments
