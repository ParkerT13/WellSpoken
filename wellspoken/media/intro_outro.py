from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from wellspoken.media.ffmpeg_runner import has_audio_track, normalize_clip, run

DEFAULT_DURATION_SECONDS = 3.0


def build_title_card_image(
    title: str,
    out_image_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    bg_color: tuple[int, int, int] = (18, 18, 24),
    text_color: tuple[int, int, int] = (240, 240, 245),
) -> Path:
    """Render a simple centered-title card as a still image (built-in intro/outro fallback)."""
    out_image_path = Path(out_image_path)
    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    font_size = 96
    try:
        font = ImageFont.truetype("segoeui.ttf", font_size)
    except OSError:
        font = ImageFont.load_default(size=font_size)

    bbox = draw.textbbox((0, 0), title, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((width - text_w) / 2 - bbox[0], (height - text_h) / 2 - bbox[1]),
        title,
        fill=text_color,
        font=font,
    )
    img.save(out_image_path)
    return out_image_path


def build_title_card_clip(
    title: str,
    out_video_path: str | Path,
    width: int,
    height: int,
    fps: float,
    duration_seconds: float = DEFAULT_DURATION_SECONDS,
    scratch_dir: str | Path = ".",
) -> Path:
    """Turn a rendered title-card image into a short silent video clip with a fade in/out."""
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    image_path = scratch_dir / "title_card.png"
    build_title_card_image(title, image_path, width=width, height=height)

    out_video_path = Path(out_video_path)
    fade_out_start = max(duration_seconds - 0.5, 0.0)
    run(
        [
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(duration_seconds),
            "-vf",
            f"fps={fps},fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start}:d=0.5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out_video_path),
        ]
    )
    return out_video_path


def normalize_user_clip(
    src_path: str | Path,
    out_path: str | Path,
    width: int,
    height: int,
    fps: float,
) -> Path:
    """Transcode a user-supplied intro/outro clip so it matches the main recording.

    Must explicitly detect whether src_path actually has an audio track and
    tell normalize_clip - its has_audio=True default blindly tries to encode
    audio that may not exist, silently producing an output with NO audio
    stream at all (not just a silent one) when the source clip is video-only.
    That's more than just a silent intro: concatenating a stream-count
    mismatched clip like that with the (audio-having) main narration
    collapses the ENTIRE final export to no audio whatsoever - ffmpeg's
    concat demuxer drops a stream from the whole output rather than keeping
    it for just the segments that have it (verified empirically - this was
    the root cause of a real report where narration audio worked right up
    until export, then was completely missing from the final file)."""
    return normalize_clip(src_path, out_path, width=width, height=height, fps=fps, has_audio=has_audio_track(src_path))
