from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from wellspoken.media.ffmpeg_runner import media_duration, run

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CUSTOM_VOICES_DIR = ROOT_DIR / "assets" / "voice_refs" / "custom"
MANIFEST_PATH = CUSTOM_VOICES_DIR / "manifest.json"

# Chatterbox clones convincingly from ~8-20s of clean solo speech (matches
# the two bundled reference clips, both 12s) - much shorter gives it too
# little to go on, much longer doesn't improve cloning quality and just
# slows down every synthesis call, so imports are capped rather than kept
# at whatever length the source clip happens to be.
MIN_RECOMMENDED_SECONDS = 5.0
MAX_REFERENCE_SECONDS = 20.0

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class CustomVoice:
    voice_id: str
    label: str
    ref_clip: str  # filename within CUSTOM_VOICES_DIR
    # Playback speed multiplier applied after synthesis (ffmpeg atempo) -
    # Chatterbox-Turbo has no pacing/tempo parameter of its own (verified:
    # generate()'s only knobs are repetition_penalty/min_p/top_p/exaggeration/
    # cfg_weight/temperature/top_k, none control rate), so a reference clip
    # that was itself read slowly clones as slow speech with no way to ask
    # the model for a faster delivery - this is the only lever available.
    speed: float = 1.0

    @property
    def ref_clip_path(self) -> Path:
        return CUSTOM_VOICES_DIR / self.ref_clip


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(entries: list[dict]) -> None:
    CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def load_custom_voices() -> list[CustomVoice]:
    return [CustomVoice(**e) for e in _load_manifest()]


def get_custom_voice(voice_id: str) -> CustomVoice | None:
    for cv in load_custom_voices():
        if cv.voice_id == voice_id:
            return cv
    return None


def _slugify(label: str) -> str:
    slug = _SLUG_RE.sub("_", label.strip().lower()).strip("_")
    return slug or "voice"


def add_custom_voice(label: str, source_path: str | Path) -> tuple[CustomVoice, str | None]:
    """Import `source_path` (any audio or video file ffmpeg can read) as a new
    cloneable voice. Converts to a clean mono 24kHz WAV matching the format
    of the bundled reference clips, trimmed to MAX_REFERENCE_SECONDS.

    Returns (the new CustomVoice, a warning string or None) - the warning is
    non-fatal (e.g. the source clip was shorter than recommended), the import
    still succeeds so the caller can decide whether to surface it.
    """
    CUSTOM_VOICES_DIR.mkdir(parents=True, exist_ok=True)
    source_path = Path(source_path)
    duration = media_duration(source_path)

    warning = None
    if duration < MIN_RECOMMENDED_SECONDS:
        warning = (
            f"This clip is only {duration:.1f}s long - Chatterbox clones best from "
            f"{MIN_RECOMMENDED_SECONDS:.0f}s or more of clean solo speech. The voice may "
            "not sound very close to the original."
        )

    voice_id = f"custom_{_slugify(label)}_{uuid.uuid4().hex[:6]}"
    ref_filename = f"{voice_id}.wav"
    ref_path = CUSTOM_VOICES_DIR / ref_filename

    args = ["-i", str(source_path)]
    if duration > MAX_REFERENCE_SECONDS:
        args += ["-t", str(MAX_REFERENCE_SECONDS)]
    args += ["-ac", "1", "-ar", "24000", "-vn", str(ref_path)]
    run(args)

    cv = CustomVoice(voice_id=voice_id, label=label.strip(), ref_clip=ref_filename)
    entries = _load_manifest()
    entries.append(asdict(cv))
    _save_manifest(entries)
    return cv, warning


def remove_custom_voice(voice_id: str) -> None:
    cv = get_custom_voice(voice_id)
    if cv is None:
        return
    entries = [e for e in _load_manifest() if e["voice_id"] != voice_id]
    _save_manifest(entries)
    cv.ref_clip_path.unlink(missing_ok=True)


def set_voice_speed(voice_id: str, speed: float) -> None:
    entries = _load_manifest()
    for e in entries:
        if e["voice_id"] == voice_id:
            e["speed"] = speed
    _save_manifest(entries)


def reorder_custom_voices(voice_ids_in_order: list[str]) -> None:
    """Reorder the manifest to match voice_ids_in_order - controls the
    order custom voices appear after the curated ones in all_voices()."""
    entries = {e["voice_id"]: e for e in _load_manifest()}
    _save_manifest([entries[vid] for vid in voice_ids_in_order if vid in entries])
