from __future__ import annotations

from pathlib import Path

from moviepy import AudioFileClip, VideoFileClip


def replace_narration(
    video_path: str | Path, narration_wav_path: str | Path, out_path: str | Path
) -> Path:
    """Replace the main recording's audio track with the narration track."""
    out_path = Path(out_path)
    video = VideoFileClip(str(video_path))
    narration = AudioFileClip(str(narration_wav_path))
    try:
        final = video.with_audio(narration)
        final.write_videofile(
            str(out_path), codec="libx264", audio_codec="aac", logger=None
        )
    finally:
        video.close()
        narration.close()
    return out_path
