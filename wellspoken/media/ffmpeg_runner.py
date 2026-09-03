from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

# Without this, every ffmpeg subprocess call flashes a console window (most
# visible during screen recording, where it happens continuously) - Windows
# creates one by default for any console executable launched via subprocess
# unless explicitly suppressed. This app is Windows-only throughout (WGC,
# dshow, ctypes.windll), so applying it unconditionally is safe.
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW


class FfmpegError(RuntimeError):
    pass


def run(args: list[str]) -> str:
    """Run ffmpeg with `args` (excluding the binary itself). Returns combined stderr/stdout."""
    cmd = [FFMPEG_EXE, "-y", "-hide_banner", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr}")
    return proc.stderr


_probe_cache: dict[tuple[str, float], dict] = {}


def probe(video_path: str | Path) -> dict:
    """Lightweight probe (fps, size, duration) via imageio_ffmpeg's reader metadata.

    Each call spins up an ffmpeg subprocess just to read the header, so results
    are cached per (path, mtime) - the same video gets probed repeatedly across
    project selection, intro/outro previews, and render().
    """
    video_path = str(video_path)
    mtime = Path(video_path).stat().st_mtime
    key = (video_path, mtime)
    if key not in _probe_cache:
        gen = imageio_ffmpeg.read_frames(video_path)
        meta = next(gen)
        gen.close()
        _probe_cache[key] = meta
    return _probe_cache[key]


def media_duration(path: str | Path) -> float:
    """Duration in seconds for ANY media file, including audio-only files that
    probe() can't handle (it uses imageio_ffmpeg's video-frame reader, which
    requires a video stream). Parses ffmpeg's own "Duration: HH:MM:SS.ss" log
    line rather than reading any frames."""
    proc = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    m = _DURATION_RE.search(proc.stderr)
    if not m:
        raise FfmpegError(f"Could not determine duration of {path}:\n{proc.stderr}")
    hours, minutes, seconds = m.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*:\s*Audio:")


def has_audio_track(path: str | Path) -> bool:
    """Whether `path` has an audio stream at all - works for video-only (no
    audio), audio-only, and video+audio files alike, unlike probe() (which
    needs a video stream to read frames from). Used to give a clear error
    before handing a silent file to faster-whisper, which otherwise fails
    deep inside PyAV with an opaque `IndexError: tuple index out of range`
    rather than a message that says what's actually wrong."""
    proc = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    return bool(_AUDIO_STREAM_RE.search(proc.stderr))


def normalize_loudness(audio_path: str | Path, out_path: str | Path) -> Path:
    """Level narration to a standard streaming loudness target (-16 LUFS
    integrated, -1.5dBTP true peak, 11 LU range - the common "web video"
    target used by YouTube/Spotify-style normalization) via ffmpeg's built-in
    `loudnorm` filter. Keeps narration volume consistent take-to-take and
    project-to-project instead of whatever Kokoro/the mic happened to output."""
    out_path = Path(out_path)
    run(["-i", str(audio_path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(out_path)])
    return out_path


def reformat_aspect(video_path: str | Path, out_path: str | Path, width: int, height: int) -> Path:
    """Fit `video_path` into a width x height frame, letterboxing/pillarboxing
    (never cropping) so on-screen UI/content is never cut off - used for
    social media aspect ratio presets (9:16, 1:1) on the Export tab. Same
    scale+pad technique as normalize_clip, generalized to arbitrary targets."""
    out_path = Path(out_path)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    run(["-i", str(video_path), "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out_path)])
    return out_path


def normalize_clip(
    src_path: str | Path,
    out_path: str | Path,
    width: int,
    height: int,
    fps: float,
    has_audio: bool = True,
) -> Path:
    """Transcode `src_path` to match the given resolution/fps/codec so it can be
    concatenated with the main recording via the concat demuxer."""
    out_path = Path(out_path)
    # All -i inputs must come before any output/filter options - ffmpeg parses
    # arguments positionally, so appending a second -i (the anullsrc silent
    # track, for has_audio=False) AFTER -vf misattributes -vf to the wrong
    # input and fails outright (verified empirically: this exact ordering
    # bug had sat dormant since has_audio's only caller always passed the
    # True default, until a real fix elsewhere started passing False).
    args = ["-i", str(src_path)]
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    vf = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    args += ["-vf", vf, "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if has_audio:
        args += ["-c:a", "aac", "-ar", "44100"]
    else:
        args += ["-shortest", "-c:a", "aac"]
    args += [str(out_path)]
    run(args)
    return out_path


def concat(clip_paths: list[str | Path], out_path: str | Path) -> Path:
    """Concatenate pre-normalized clips (same codec/res/fps) via the concat demuxer."""
    out_path = Path(out_path)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for p in clip_paths:
            escaped = str(Path(p).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
        list_file = f.name
    try:
        run(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", str(out_path)])
    finally:
        Path(list_file).unlink(missing_ok=True)
    return out_path


def _subtitles_filter(subtitle_path: str | Path) -> str:
    escaped = str(Path(subtitle_path).resolve()).replace("\\", "/").replace(":", "\\:")
    return f"subtitles='{escaped}'"


def burn_subtitles(video_path: str | Path, subtitle_path: str | Path, out_path: str | Path) -> Path:
    """Burn `subtitle_path` (.srt or .ass) into the video. For styled burns,
    pass a .ass file from captions.export.write_ass() - it bakes the style
    directly into the file, so no extra styling options are needed here."""
    out_path = Path(out_path)
    run(["-i", str(video_path), "-vf", _subtitles_filter(subtitle_path), "-c:a", "copy", str(out_path)])
    return out_path


def render_caption_style_preview(image_path: str | Path, ass_path: str | Path, out_image_path: str | Path) -> Path:
    """Burn a styled .ass onto a single still frame - same filter/code path as
    the real export, so the preview can never drift from what a render will
    actually produce."""
    out_image_path = Path(out_image_path)
    run(["-i", str(image_path), "-vf", _subtitles_filter(ass_path), str(out_image_path)])
    return out_image_path


def extract_range(src_path: str | Path, start: float, end: float, out_path: str | Path) -> Path:
    """Stream-copy [start, end) out of src_path - fast, no re-encoding. Used by
    the timeline editor to keep the un-cut portions of a video/audio file.
    Note: with -c copy the actual start snaps to the nearest keyframe at or
    before `start`, so cuts can be off by a fraction of a second - acceptable
    for trimming dead air/mistakes, not frame-exact."""
    out_path = Path(out_path)
    run(["-ss", str(start), "-i", str(src_path), "-t", str(end - start), "-c", "copy", str(out_path)])
    return out_path


def mix_background_music(
    video_path: str | Path, music_path: str | Path, out_path: str | Path, music_volume: float = 0.15
) -> Path:
    """Layer `music_path` under the video's existing audio (narration/mic),
    looped to cover the full video if shorter, trimmed to the video's length
    if longer - never changes the video's duration. `music_volume` is a
    linear multiplier (0.15 = music at 15% of its own level, subdued enough
    to sit under narration without masking it - the standard "music bed"
    level for talk-over-music marketing content)."""
    out_path = Path(out_path)
    filter_complex = (
        f"[1:a]volume={music_volume},aloop=loop=-1:size=2e9[music];"
        "[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    run([
        "-i", str(video_path), "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac",
        str(out_path),
    ])
    return out_path


def apply_watermark(
    video_path: str | Path,
    logo_path: str | Path,
    out_path: str | Path,
    video_width: int,
    video_height: int,
    position: str = "bottom-right",
    scale: float = 0.08,
    opacity: float = 0.7,
    margin: float = 0.03,
) -> Path:
    """Overlay `logo_path` (needs an alpha channel - a plain JPG would paint
    an opaque box over the video) in a corner, sized and positioned as a
    fraction of the video's own dimensions so it scales sensibly across
    aspect ratios. Defaults (8% width, 70% opacity, 3% margin, bottom-right)
    follow common watermark conventions: small enough to stay unobtrusive,
    corner placement viewers already expect branding in."""
    out_path = Path(out_path)
    logo_width = max(1, round(video_width * scale))
    margin_px = round(min(video_width, video_height) * margin)
    xy = f"W-w-{margin_px}:H-h-{margin_px}" if position == "bottom-right" else f"{margin_px}:H-h-{margin_px}"
    filter_complex = (
        f"[1:v]scale={logo_width}:-1,format=rgba,colorchannelmixer=aa={opacity}[wm];"
        f"[0:v][wm]overlay={xy}:format=auto[vout]"
    )
    run([
        "-i", str(video_path), "-i", str(logo_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(out_path),
    ])
    return out_path


def grab_frame(video_path: str | Path, out_image_path: str | Path, at_seconds: float = 1.0) -> Path:
    out_image_path = Path(out_image_path)
    run(["-ss", str(at_seconds), "-i", str(video_path), "-frames:v", "1", str(out_image_path)])
    return out_image_path
