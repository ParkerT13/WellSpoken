from __future__ import annotations

from pathlib import Path

from wellspoken.captions.align import shift_segments_for_cuts
from wellspoken.media import ffmpeg_runner
from wellspoken.models import CaptionSegment


def _keep_ranges(total_duration: float, cut_ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The complement of `cut_ranges` within [0, total_duration]."""
    cuts = sorted(cut_ranges)
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in cuts:
        if start > cursor:
            keep.append((cursor, min(start, total_duration)))
        cursor = max(cursor, end)
    if cursor < total_duration:
        keep.append((cursor, total_duration))
    return [r for r in keep if r[1] - r[0] > 0.01]


def _cut_file(src_path: str | Path, keep_ranges: list[tuple[float, float]], scratch_dir: Path, prefix: str, ext: str) -> Path:
    if not keep_ranges:
        raise ValueError("cutting would remove the entire file")
    parts = []
    for i, (s, e) in enumerate(keep_ranges):
        part = scratch_dir / f"{prefix}_{i}{ext}"
        ffmpeg_runner.extract_range(src_path, s, e, part)
        parts.append(part)
    if len(parts) == 1:
        return parts[0]
    out_path = scratch_dir / f"{prefix}_joined{ext}"
    ffmpeg_runner.concat(parts, out_path)
    return out_path


def apply_cuts(
    video_path: str | Path,
    narration_path: str | Path,
    segments: list[CaptionSegment],
    cut_ranges: list[tuple[float, float]],
    scratch_dir: str | Path,
) -> tuple[Path, Path, list[CaptionSegment]]:
    """Ripple-delete `cut_ranges` from both the video and the narration audio
    (keeping them in sync), and remap caption timing to match. Cuts are
    stream-copy extractions concatenated back together (see
    ffmpeg_runner.extract_range/concat) - fast, no re-encoding, at the cost of
    cut points snapping to the nearest keyframe rather than being frame-exact.
    """
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # When narration_audio IS the source video (the "use project video's own
    # audio" transcribe path - see Project.resolve_narration_for_render()),
    # cutting it a second time as a separate audio-only extraction would try
    # to stream-copy a video track into a .wav container and fail. Cut once
    # and point both results at it, preserving that same-file relationship.
    same_source = Path(video_path).resolve() == Path(narration_path).resolve()

    video_duration = ffmpeg_runner.media_duration(video_path)
    new_video = _cut_file(video_path, _keep_ranges(video_duration, cut_ranges), scratch_dir, "video_cut", ".mp4")

    if same_source:
        new_narration = new_video
    else:
        narration_duration = ffmpeg_runner.media_duration(narration_path)
        new_narration = _cut_file(narration_path, _keep_ranges(narration_duration, cut_ranges), scratch_dir, "narration_cut", ".wav")

    new_segments = shift_segments_for_cuts(segments, cut_ranges)
    return new_video, new_narration, new_segments
