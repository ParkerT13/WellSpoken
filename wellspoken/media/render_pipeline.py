from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from wellspoken.captions.align import shift_segments
from wellspoken.captions.export import write_ass, write_srt, write_vtt
from wellspoken.captions.style import CaptionStyle
from wellspoken.media import ffmpeg_runner
from wellspoken.media.audio_mix import replace_narration
from wellspoken.media.intro_outro import build_title_card_clip, normalize_user_clip
from wellspoken.models import CaptionSegment

ProgressFn = Callable[[str], None]

# Standard target resolutions per platform aspect ratio. Fit-and-pad (never
# crop) so on-screen UI/content from the recording is never cut off.
SOCIAL_ASPECT_PRESETS: dict[str, Optional[tuple[int, int]]] = {
    "original": None,
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}


@dataclass
class IntroOutroSpec:
    kind: str  # "none" | "template" | "clip"
    title: Optional[str] = None
    clip_path: Optional[str] = None


@dataclass
class RenderOptions:
    source_video: str
    narration_wav: Optional[str]  # None if the recording's own audio is the narration
    segments: list[CaptionSegment]
    intro: IntroOutroSpec
    outro: IntroOutroSpec
    caption_mode: str  # "burned_in" | "sidecar" | "both"
    output_dir: str
    scratch_dir: str
    aspect_ratio: str = "original"  # key into SOCIAL_ASPECT_PRESETS
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    background_music: Optional[str] = None
    background_music_volume: float = 0.15  # linear multiplier - subdued "music bed" level under narration
    extra_aspect_ratios: list[str] = field(default_factory=list)  # additional formats, exported alongside aspect_ratio
    watermark_path: Optional[str] = None
    watermark_position: str = "bottom-right"  # "bottom-right" | "bottom-left"


def _noop(_msg: str) -> None:
    pass


def render(opts: RenderOptions, on_progress: ProgressFn = _noop) -> dict:
    """Full pipeline: narration -> intro/outro normalize -> concat -> captions -> export.

    Returns dict of output paths produced.
    """
    scratch = Path(opts.scratch_dir)
    scratch.mkdir(parents=True, exist_ok=True)
    out_dir = Path(opts.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    on_progress("Probing source video...")
    meta = ffmpeg_runner.probe(opts.source_video)
    width, height = meta["size"]
    fps = meta["fps"]

    main_path = Path(opts.source_video)
    if opts.narration_wav:
        on_progress("Normalizing narration loudness...")
        leveled_narration = ffmpeg_runner.normalize_loudness(
            opts.narration_wav, scratch / "narration_normalized.wav"
        )
        on_progress("Replacing audio with narration...")
        main_path = replace_narration(
            opts.source_video, leveled_narration, scratch / "main_narrated.mp4"
        )

    def _prepare(label: str, spec: IntroOutroSpec) -> Optional[Path]:
        if spec.kind == "none":
            return None
        on_progress(f"Preparing {label}...")
        if spec.kind == "template":
            return build_title_card_clip(
                spec.title or "",
                scratch / f"{label}.mp4",
                width=width,
                height=height,
                fps=fps,
                scratch_dir=scratch,
            )
        if spec.kind == "clip":
            if not spec.clip_path:
                raise ValueError(f"{label} is set to 'My own clip' but no clip file was selected.")
            return normalize_user_clip(
                spec.clip_path, scratch / f"{label}_norm.mp4", width=width, height=height, fps=fps
            )
        return None

    intro_clip = _prepare("intro", opts.intro)
    outro_clip = _prepare("outro", opts.outro)

    clips_to_concat: list[Path] = [c for c in (intro_clip, main_path, outro_clip) if c]

    on_progress("Concatenating intro/main/outro...")
    if len(clips_to_concat) > 1:
        assembled = ffmpeg_runner.concat(clips_to_concat, scratch / "assembled.mp4")
    else:
        assembled = main_path

    if opts.background_music:
        on_progress("Mixing in background music...")
        assembled = ffmpeg_runner.mix_background_music(
            assembled, opts.background_music, scratch / "assembled_music.mp4",
            music_volume=opts.background_music_volume,
        )

    outputs: dict[str, str] = {}

    intro_offset = ffmpeg_runner.probe(intro_clip)["duration"] if intro_clip else 0.0
    timeline_segments = shift_segments(opts.segments, intro_offset)

    if opts.caption_mode in ("sidecar", "both") and timeline_segments:
        on_progress("Writing caption files...")
        srt_path = write_srt(timeline_segments, out_dir / "captions.srt")
        vtt_path = write_vtt(timeline_segments, out_dir / "captions.vtt")
        outputs["srt"] = str(srt_path)
        outputs["vtt"] = str(vtt_path)

    if opts.caption_mode in ("burned_in", "both") and timeline_segments:
        on_progress("Burning in captions...")
        ass_for_burn = write_ass(timeline_segments, opts.caption_style, width, height, scratch / "burn.ass")
        final_path = ffmpeg_runner.burn_subtitles(assembled, ass_for_burn, out_dir / "final.mp4")
    else:
        final_path = out_dir / "final.mp4"
        shutil.copyfile(assembled, final_path)

    # Preserved before the primary aspect ratio's in-place reformat below
    # (which deletes/replaces final_path) so extra_aspect_ratios has an
    # unmodified, caption-burned source to reformat from too - without this,
    # every extra format after the first would be reformatting an
    # already-reformatted (wrong aspect) video instead of the original.
    pre_reformat_path = scratch / "pre_reformat.mp4"
    shutil.copyfile(final_path, pre_reformat_path)

    def _watermark_if_needed(path: Path, w: int, h: int) -> None:
        # Position/size are relative to THIS output's own final resolution,
        # not the pre-reformat one - applied after each format's reformat
        # step (not once up front) so it's correctly placed and sized on
        # every exported aspect ratio, not just the primary one.
        if not opts.watermark_path:
            return
        on_progress("Applying watermark...")
        watermarked = scratch / f"{path.stem}_wm.mp4"
        ffmpeg_runner.apply_watermark(
            path, opts.watermark_path, watermarked, w, h, position=opts.watermark_position
        )
        path.unlink(missing_ok=True)
        watermarked.replace(path)

    target_size = SOCIAL_ASPECT_PRESETS.get(opts.aspect_ratio)
    if target_size:
        on_progress(f"Fitting to {opts.aspect_ratio} for social media...")
        reformatted = ffmpeg_runner.reformat_aspect(
            final_path, scratch / "final_reformatted.mp4", target_size[0], target_size[1]
        )
        final_path.unlink(missing_ok=True)
        reformatted.replace(final_path)
        _watermark_if_needed(final_path, target_size[0], target_size[1])
    else:
        _watermark_if_needed(final_path, width, height)

    outputs["video"] = str(final_path)

    for ratio in opts.extra_aspect_ratios:
        if ratio == opts.aspect_ratio:
            continue
        extra_out = out_dir / f"final_{ratio.replace(':', 'x')}.mp4"
        on_progress(f"Also exporting {ratio}...")
        extra_size = SOCIAL_ASPECT_PRESETS.get(ratio)
        if extra_size:
            ffmpeg_runner.reformat_aspect(pre_reformat_path, extra_out, extra_size[0], extra_size[1])
            _watermark_if_needed(extra_out, extra_size[0], extra_size[1])
        else:
            shutil.copyfile(pre_reformat_path, extra_out)
            _watermark_if_needed(extra_out, width, height)
        outputs[f"video_{ratio}"] = str(extra_out)

    pre_reformat_path.unlink(missing_ok=True)

    on_progress("Done.")
    return outputs
