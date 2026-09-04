from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from wellspoken.media.ffmpeg_runner import concat_with_delays, media_duration

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def split_script_into_paragraphs(script: str) -> list[str]:
    """Blank-line-separated paragraphs - the unit a sync marker attaches to.
    Paragraph N syncs to marker N (in video-timestamp order), so the script
    must be broken into one paragraph per marked moment."""
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(script.strip()) if p.strip()]


@dataclass
class SyncSegmentReport:
    index: int
    text: str
    target_start: float | None
    actual_start: float
    overrun: float  # seconds this segment started late because an earlier one ran long; 0 if on time or no marker


def synthesize_with_markers(
    engine, script: str, markers: list[float], wav_path: str | Path, scratch_dir: str | Path, on_progress=None
) -> tuple[Path, list[SyncSegmentReport]]:
    """Synthesize `script` as one narration track where paragraph i's audio
    starts as close as possible to markers[i] (video timestamps in seconds),
    padding with silence to hit each target exactly.

    Deliberately never speeds up or time-stretches a segment to compress it
    into its slot - that would audibly distort the voice. So if a paragraph's
    natural narration runs past its marker, every following segment is
    delayed by the same amount and the overrun is reported back rather than
    silently absorbed - shortening the script is the only fix that doesn't
    degrade audio quality, and that's a call only the user can make.
    """
    paragraphs = split_script_into_paragraphs(script)
    if not paragraphs:
        raise ValueError("Script is empty.")

    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    sorted_markers = sorted(markers)
    pieces: list[tuple[Path, float]] = []
    reports: list[SyncSegmentReport] = []
    cursor = 0.0

    for i, para in enumerate(paragraphs):
        if on_progress:
            on_progress(f"Synthesizing line {i + 1} of {len(paragraphs)}...")
        piece_path = scratch_dir / f"sync_piece_{i:03d}.wav"
        engine.synthesize_to_wav(para, piece_path)
        piece_dur = media_duration(piece_path)

        target = sorted_markers[i] if i < len(sorted_markers) else None
        overrun = max(0.0, cursor - target) if target is not None else 0.0
        actual_start = max(cursor, target) if target is not None else cursor
        reports.append(
            SyncSegmentReport(index=i, text=para, target_start=target, actual_start=actual_start, overrun=overrun)
        )

        pieces.append((piece_path, actual_start))
        cursor = actual_start + piece_dur

    concat_with_delays(pieces, wav_path)
    return Path(wav_path), reports
