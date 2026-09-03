from __future__ import annotations

from pathlib import Path

from wellspoken.captions.style import CaptionStyle, ass_color
from wellspoken.models import CaptionSegment


def _srt_timestamp(seconds: float) -> str:
    ms_total = round(seconds * 1000)
    hours, rem = divmod(ms_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def _vtt_timestamp(seconds: float) -> str:
    return _srt_timestamp(seconds).replace(",", ".")


def write_srt(segments: list[CaptionSegment], path: str | Path) -> Path:
    path = Path(path)
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(seg.start)} --> {_srt_timestamp(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_vtt(segments: list[CaptionSegment], path: str | Path) -> Path:
    path = Path(path)
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_vtt_timestamp(seg.start)} --> {_vtt_timestamp(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ass_timestamp(seconds: float) -> str:
    cs_total = round(seconds * 100)  # ASS timestamps are centisecond-precision
    hours, rem = divmod(cs_total, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{cs:02}"


def write_ass(
    segments: list[CaptionSegment], style: CaptionStyle, video_width: int, video_height: int, path: str | Path
) -> Path:
    """Burned-in caption styling only renders at the intended pixel size when
    PlayResX/PlayResY match the ACTUAL video resolution - ffmpeg's `subtitles`
    filter auto-converts a plain .srt to ASS using its own small internal
    default script resolution, which silently blows FontSize up several times
    too large (verified empirically: FontSize=42 rendered ~150px tall on a
    1080-tall frame). Writing a real .ass file with explicit PlayRes fixes
    this - FontSize then maps directly to real on-screen pixels."""
    path = Path(path)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {video_width}\n"
        f"PlayResY: {video_height}\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,{font},{size},{primary},&H000000FF,{outline},&H00000000,{bold},0,0,0,"
        "100,100,0,0,1,{outline_w},0,{alignment},10,10,{margin_v},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).format(
        font=style.font_family,
        size=style.font_size,
        primary=ass_color(style.primary_color),
        outline=ass_color(style.outline_color),
        bold=1 if style.bold else 0,
        outline_w=style.outline_width,
        alignment=2 if style.position == "bottom" else 8,
        margin_v=style.margin_v,
    )
    lines = [header]
    for seg in segments:
        text = seg.text.replace("\n", "\\N").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(seg.start)},{_ass_timestamp(seg.end)},Default,,0,0,0,,{text}\n"
        )
    path.write_text("".join(lines), encoding="utf-8")
    return path
