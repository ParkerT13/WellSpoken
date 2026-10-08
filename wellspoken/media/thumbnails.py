from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from wellspoken.media import ffmpeg_runner

FRAME_COUNT = 40
STRIP_HEIGHT = 90


def generate_filmstrip(video_path: str | Path, scratch_dir: str | Path, strip_width: int = 2000) -> np.ndarray:
    """A single horizontal filmstrip image - FRAME_COUNT evenly-spaced frames
    from `video_path`, stretched edge-to-edge across `strip_width` - for the
    Timeline tab's thumbnail row. Rendered once per load (not re-rendered on
    zoom/pan) and displayed as a pg.ImageItem sharing the waveform plot's own
    time axis, so panning/zooming the waveform moves the filmstrip with it
    for free instead of needing separate sync logic.

    Returns a (STRIP_HEIGHT, strip_width, 3) uint8 array ready to hand
    directly to pg.ImageItem under imageAxisOrder='row-major' - pre-flipped
    vertically, since pyqtgraph's row-major images otherwise render upside
    down against a y-increases-upward plot (verified empirically, not
    documented behavior).
    """
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    duration = ffmpeg_runner.media_duration(video_path)
    col_width = max(1, strip_width // FRAME_COUNT)
    strip = Image.new("RGB", (col_width * FRAME_COUNT, STRIP_HEIGHT))
    for i in range(FRAME_COUNT):
        t = min(duration, max(0.0, (i + 0.5) * duration / FRAME_COUNT))
        frame_path = scratch_dir / f"filmstrip_{i}.jpg"
        ffmpeg_runner.grab_frame(video_path, frame_path, at_seconds=t)
        frame = Image.open(frame_path).convert("RGB").resize((col_width, STRIP_HEIGHT))
        strip.paste(frame, (i * col_width, 0))
        frame_path.unlink(missing_ok=True)
    return np.flipud(np.array(strip))
