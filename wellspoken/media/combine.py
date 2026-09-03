from __future__ import annotations

from pathlib import Path

from wellspoken.media import ffmpeg_runner


def append_clip(base_path: str | Path, extra_path: str | Path, out_path: str | Path, scratch_dir: str | Path) -> Path:
    """Concatenate `extra_path` onto the end of `base_path` - lets a user
    combine two separately-recorded videos into one main recording. Both
    clips are normalized to `base_path`'s resolution/fps AND forced to have
    an audio track (silent if either lacked one) via normalize_clip's
    has_audio=True default - the concat demuxer's stream-copy requires every
    segment's codec parameters to match exactly, so a resolution mismatch or
    one clip having no audio track would otherwise fail (same reasoning
    intro/outro's normalize_user_clip already relies on)."""
    scratch = Path(scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    meta = ffmpeg_runner.probe(base_path)
    width, height = meta["size"]
    fps = meta["fps"]

    base_norm = ffmpeg_runner.normalize_clip(base_path, scratch / "append_base_norm.mp4", width, height, fps)
    extra_norm = ffmpeg_runner.normalize_clip(extra_path, scratch / "append_extra_norm.mp4", width, height, fps)
    result = ffmpeg_runner.concat([base_norm, extra_norm], out_path)
    base_norm.unlink(missing_ok=True)
    extra_norm.unlink(missing_ok=True)
    return result
