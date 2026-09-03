from __future__ import annotations

import copy

from wellspoken.models import CaptionSegment, WordTiming

MAX_LINE_CHARS = 84
MAX_LINE_SECONDS = 6.0


def group_words_into_segments(
    words: list[WordTiming], source: str
) -> list[CaptionSegment]:
    """Group a flat word-timestamp stream into readable caption lines.

    Greedy grouping: start a new line when appending the next word would push
    the line over MAX_LINE_CHARS or MAX_LINE_SECONDS, or after sentence-ending
    punctuation.
    """
    segments: list[CaptionSegment] = []
    current: list[WordTiming] = []

    def flush():
        if not current:
            return
        text = " ".join(w.word for w in current).strip()
        confidences = [w.confidence for w in current if w.confidence is not None]
        avg_conf = sum(confidences) / len(confidences) if confidences else None
        segments.append(
            CaptionSegment(
                index=len(segments),
                start=current[0].start,
                end=current[-1].end,
                text=text,
                source=source,
                words=list(current),
                confidence=avg_conf,
            )
        )
        current.clear()

    for w in words:
        candidate_text = " ".join(x.word for x in current + [w])
        candidate_duration = w.end - current[0].start if current else 0.0
        if current and (
            len(candidate_text) > MAX_LINE_CHARS or candidate_duration > MAX_LINE_SECONDS
        ):
            flush()
        current.append(w)
        if w.word.strip().endswith((".", "?", "!")):
            flush()

    flush()
    return segments


def shift_segments(segments: list[CaptionSegment], offset_seconds: float) -> list[CaptionSegment]:
    """Return copies of `segments` with all timestamps shifted by `offset_seconds`.

    Used when captions were timed against a narration/main clip that is later
    concatenated after an intro of nonzero duration - the burned-in/sidecar
    captions must line up with the assembled video's timeline, not the
    original clip's.
    """
    if not offset_seconds:
        return list(segments)
    shifted = []
    for seg in segments:
        new_seg = copy.deepcopy(seg)
        new_seg.start += offset_seconds
        new_seg.end += offset_seconds
        for w in new_seg.words:
            w.start += offset_seconds
            w.end += offset_seconds
        shifted.append(new_seg)
    return shifted


def shift_segments_for_cuts(
    segments: list[CaptionSegment], cut_ranges: list[tuple[float, float]]
) -> list[CaptionSegment]:
    """Remap segment/word timestamps after ripple-deleting `cut_ranges` ([start,
    end) spans to remove) from the timeline - the timeline editor's core
    remapping step. Words whose midpoint falls inside a cut are dropped;
    surviving words are shifted earlier by the cumulative duration of every
    cut before them. Segments are rebuilt from the surviving words via
    group_words_into_segments, so line-breaking stays consistent with how
    segments are normally produced.
    """
    if not cut_ranges:
        return list(segments)
    cuts = sorted(cut_ranges)

    def in_a_cut(t: float) -> bool:
        return any(start <= t < end for start, end in cuts)

    def offset_at(t: float) -> float:
        return sum(end - start for start, end in cuts if end <= t)

    all_words = [w for seg in segments for w in seg.words]
    source = segments[0].source if segments else "transcribed"

    kept_words: list[WordTiming] = []
    for w in all_words:
        midpoint = (w.start + w.end) / 2
        if in_a_cut(midpoint):
            continue
        offset = offset_at(midpoint)
        new_word = copy.deepcopy(w)
        new_word.start = w.start - offset
        new_word.end = w.end - offset
        kept_words.append(new_word)

    if not kept_words:
        return []
    return group_words_into_segments(kept_words, source=source)
