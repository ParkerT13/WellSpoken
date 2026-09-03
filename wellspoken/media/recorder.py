from __future__ import annotations

import ctypes
import re
import subprocess
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from wellspoken.media.ffmpeg_runner import CREATE_NO_WINDOW, FFMPEG_EXE

_DEVICE_LINE_RE = re.compile(r'"(.+)"\s+\((audio|video)\)')


def list_audio_devices() -> list[str]:
    """Microphone names available via ffmpeg's dshow input, parsed from its
    device-listing stderr output (this call always exits non-zero, by design
    of `-list_devices` - not a real failure, so we don't use ffmpeg_runner.run())."""
    proc = subprocess.run(
        [FFMPEG_EXE, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        creationflags=CREATE_NO_WINDOW,
    )
    devices = []
    for line in proc.stderr.splitlines():
        m = _DEVICE_LINE_RE.search(line)
        if m and m.group(2) == "audio":
            devices.append(m.group(1))
    return devices


def list_open_windows() -> list[tuple[int, str]]:
    """(hwnd, title) pairs for currently visible top-level windows. hwnd is
    used for capture targeting (window_hwnd is far more reliable than
    title-matching - titles can repeat or change, e.g. multiple "Untitled -
    Notepad" windows)."""
    user32 = ctypes.windll.user32
    windows: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title and title != "WellSpoken":
                    windows.append((hwnd, title))
        return True

    user32.EnumWindows(_enum_proc, 0)
    return windows


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def list_monitors() -> list[tuple[int, int, int, int]]:
    """(x, y, width, height) per monitor, in the SAME order Windows.Graphics.Capture's
    monitor_index uses (list index 0 -> monitor_index 1, etc. - verified empirically,
    since WGC's own monitor ordering does not reliably match Qt's QApplication.screens()
    order). Coordinates are in the same non-DPI-aware logical space Qt's own
    QScreen.geometry() reports, so they can be directly compared/matched against Qt
    screens for devicePixelRatio lookups."""
    user32 = ctypes.windll.user32
    monitors: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double)
    def _enum_proc(_hmonitor, _hdc, lprc, _data):
        r = lprc.contents
        monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    user32.EnumDisplayMonitors(0, 0, _enum_proc, 0)
    return monitors


@dataclass
class CaptureSpec:
    kind: str  # "fullscreen" | "window" | "region"
    monitor_index: int = 0  # 0-based, matches QApplication.screens() order
    x: int = 0  # region only, relative to the monitor's own top-left
    y: int = 0
    width: int = 0
    height: int = 0
    window_hwnd: Optional[int] = None


class _SegmentSession:
    """One continuous Windows.Graphics.Capture recording, start to finish -
    what a "recording" was before pause/resume existed. A paused-and-resumed
    RecordingSession (below) is a sequence of these, concatenated at the end.

    WGC only fires on_frame_arrived when the screen content actually changes
    (a DXGI dirty-region optimization) - it is NOT a steady N-fps stream. If
    each arrived frame were written straight to a `-r fps` rawvideo pipe,
    static content (nothing moving on screen) would starve the pipe and the
    resulting video's real duration would be far shorter than the actual
    wall-clock recording time (verified empirically: a 3s recording of a
    mostly-static desktop produced a 0.2s video).

    So on_frame_arrived only updates `latest_frame` (the most recent captured
    bytes); a separate timer thread (`_pump_loop`) writes whatever the current
    `latest_frame` is to ffmpeg's stdin on a steady `1/fps` cadence, duplicating
    frames when nothing changed. This decouples WGC's irregular event timing
    from ffmpeg's fixed-rate frame consumption, producing correctly-timed video.
    """

    def __init__(self, output_path: Path, audio_device: Optional[str], fps: int, crop_box):
        self.output_path = output_path
        self.audio_device = audio_device
        self.fps = fps
        self.crop_box = crop_box  # (x0, y0, x1, y1) in captured-frame pixel coords, or None
        self.capture = None
        self.control = None
        self.ffmpeg_proc: Optional[subprocess.Popen] = None  # video-only (rawvideo pipe)
        self.audio_proc: Optional[subprocess.Popen] = None  # audio-only (live dshow device)
        self.video_only_path: Optional[Path] = None
        self.audio_only_path: Optional[Path] = None
        self.out_w = 0
        self.out_h = 0
        self.stopped = False
        self.lock = threading.Lock()
        self.started_event = threading.Event()
        self.start_error: Optional[str] = None
        self.latest_frame: Optional[bytes] = None
        self.pump_thread: Optional[threading.Thread] = None
        self._pump_stop = threading.Event()

    def _start_ffmpeg(self, width: int, height: int) -> None:
        # Video (rawvideo pipe) and audio (live dshow device) are recorded as
        # two SEPARATE ffmpeg processes, remuxed together in stop_recording().
        # A single combined process doesn't work reliably: the rawvideo pipe
        # needs stdin free for frame data, and a live dshow input has no
        # natural EOF, so there's no single clean way to signal one combined
        # process to finalize (verified empirically - both stdin-close-only
        # and CTRL_BREAK_EVENT left the combined process hung/corrupt with a
        # live mic attached). Split, each stops via a technique proven to work
        # in isolation: rawvideo via stdin-close, dshow audio via 'q' keypress.
        self.out_w, self.out_h = width, height
        self.video_only_path = self.output_path.with_name(self.output_path.stem + "_video_only.mp4")
        video_args = [
            FFMPEG_EXE, "-y", "-hide_banner",
            "-f", "rawvideo", "-pix_fmt", "bgra",
            "-s", f"{width}x{height}", "-r", str(self.fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(self.video_only_path),
        ]
        self.ffmpeg_proc = subprocess.Popen(
            video_args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )

        if self.audio_device:
            self.audio_only_path = self.output_path.with_name(self.output_path.stem + "_audio_only.m4a")
            audio_args = [
                FFMPEG_EXE, "-y", "-hide_banner",
                "-f", "dshow", "-i", f"audio={self.audio_device}",
                "-c:a", "aac", str(self.audio_only_path),
            ]
            self.audio_proc = subprocess.Popen(
                audio_args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )

        self.pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
        self.pump_thread.start()

    def _pump_loop(self) -> None:
        interval = 1.0 / self.fps
        next_tick = time.monotonic()
        blank = bytes(self.out_w * self.out_h * 4)
        while not self._pump_stop.is_set():
            next_tick += interval
            with self.lock:
                buf = self.latest_frame
                proc = self.ffmpeg_proc
            if proc and proc.stdin:
                try:
                    proc.stdin.write(buf if buf is not None else blank)
                except Exception:
                    break
            sleep_time = next_tick - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)


def _start_segment(
    output_path: Path,
    spec: CaptureSpec,
    audio_device: Optional[str],
    fps: int,
) -> _SegmentSession:
    from windows_capture import WindowsCapture

    if spec.kind == "window":
        if not spec.window_hwnd:
            raise ValueError("window capture requires window_hwnd")
        capture = WindowsCapture(cursor_capture=True, window_hwnd=spec.window_hwnd)
        crop_box = None
    else:
        capture = WindowsCapture(cursor_capture=True, monitor_index=spec.monitor_index + 1)
        crop_box = (spec.x, spec.y, spec.x + spec.width, spec.y + spec.height) if spec.kind == "region" else None

    segment = _SegmentSession(output_path, audio_device, fps, crop_box)
    segment.capture = capture

    @capture.event
    def on_frame_arrived(frame, capture_control):
        if segment.stopped:
            capture_control.stop()
            return
        try:
            if segment.crop_box:
                x0, y0, x1, y1 = segment.crop_box
                frame = frame.crop(x0, y0, x1, y1)
            buf = frame.frame_buffer
            with segment.lock:
                if segment.ffmpeg_proc is None:
                    h, w = buf.shape[0], buf.shape[1]
                    segment._start_ffmpeg(w - w % 2, h - h % 2)
                    segment.started_event.set()
                if buf.shape[0] != segment.out_h or buf.shape[1] != segment.out_w:
                    buf = buf[: segment.out_h, : segment.out_w]
                segment.latest_frame = buf.tobytes()
        except Exception as exc:
            segment.start_error = str(exc)

    @capture.event
    def on_closed():
        pass

    segment.control = capture.start_free_threaded()

    if not segment.started_event.wait(timeout=5):
        segment.stopped = True
        try:
            segment.control.stop()
        except Exception:
            pass
        raise RuntimeError(segment.start_error or "No frames were captured - is the window/monitor still available?")

    return segment


def _stop_segment(segment: _SegmentSession, timeout: float = 10.0) -> None:
    """Finalize one segment's own file (segment.output_path). Does NOT touch
    a RecordingSession's overall output_path - the caller (pause_recording or
    stop_recording) decides what happens to the finished segment file."""
    segment.stopped = True
    try:
        segment.control.stop()
    except Exception:
        pass
    segment._pump_stop.set()
    if segment.pump_thread:
        segment.pump_thread.join(timeout=5)

    if segment.ffmpeg_proc is None:
        return  # no frames ever arrived - nothing was recorded in this segment

    _stop_pipe_process(segment.ffmpeg_proc, segment.lock, timeout)
    if segment.audio_proc:
        _stop_device_process(segment.audio_proc, timeout)

    if segment.audio_proc and segment.video_only_path.exists() and segment.audio_only_path.exists():
        from wellspoken.media import ffmpeg_runner

        ffmpeg_runner.run([
            "-i", str(segment.video_only_path),
            "-i", str(segment.audio_only_path),
            "-c", "copy", "-shortest",
            str(segment.output_path),
        ])
        segment.video_only_path.unlink(missing_ok=True)
        segment.audio_only_path.unlink(missing_ok=True)
    elif segment.video_only_path.exists():
        segment.video_only_path.replace(segment.output_path)


class RecordingSession:
    """A full recording, possibly paused and resumed one or more times.

    Each Record/Pause/Resume/Stop cycle is backed by a _SegmentSession - a
    completely independent, cleanly-finalized WGC capture + ffmpeg encode.
    Pausing mid-recording finalizes the current segment exactly like a normal
    Stop would (proven-correct code path, reused rather than reinvented);
    Resume starts a brand new segment targeting the same CaptureSpec. This
    sidesteps a much harder problem - freezing a live rawvideo pipe AND a
    live dshow mic capture in lockstep without them drifting out of sync -
    in favor of segments that are independently guaranteed correct, glued
    together at the end with the same concat-demuxer stream-copy already
    used for intro/outro (see ffmpeg_runner.concat()).
    """

    def __init__(self, output_path: Path, spec: CaptureSpec, audio_device: Optional[str], fps: int):
        self.output_path = output_path
        self.spec = spec
        self.audio_device = audio_device
        self.fps = fps
        self.segment_paths: list[Path] = []
        self._segment_index = 0
        self._current: Optional[_SegmentSession] = None
        self.paused = False

    def _next_segment_path(self) -> Path:
        self._segment_index += 1
        return self.output_path.with_name(f"{self.output_path.stem}_seg{self._segment_index}{self.output_path.suffix}")


def start_recording(
    output_path: str | Path,
    spec: CaptureSpec,
    audio_device: Optional[str],
    fps: int = 30,
) -> RecordingSession:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    session = RecordingSession(output_path, spec, audio_device, fps)
    segment_path = session._next_segment_path()
    session._current = _start_segment(segment_path, spec, audio_device, fps)
    session.segment_paths.append(segment_path)
    return session


def pause_recording(session: RecordingSession, timeout: float = 10.0) -> None:
    if session._current is None:
        return
    _stop_segment(session._current, timeout)
    session._current = None
    session.paused = True


def resume_recording(session: RecordingSession) -> None:
    if session._current is not None:
        return
    segment_path = session._next_segment_path()
    session._current = _start_segment(segment_path, session.spec, session.audio_device, session.fps)
    session.segment_paths.append(segment_path)
    session.paused = False


def _stop_pipe_process(proc: subprocess.Popen, lock: threading.Lock, timeout: float) -> None:
    """Stop a process reading rawvideo from stdin: closing stdin signals EOF,
    which is enough for it to finish encoding and finalize the file."""
    try:
        with lock:
            if proc.stdin:
                proc.stdin.close()
        proc.wait(timeout=timeout)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _stop_device_process(proc: subprocess.Popen, timeout: float) -> None:
    """Stop a process reading from a live device (no natural EOF): ffmpeg's
    interactive 'q' keypress via stdin triggers a clean finalize."""
    try:
        proc.stdin.write(b"q")
        proc.stdin.flush()
        proc.wait(timeout=timeout)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def stop_recording(session: RecordingSession, timeout: float = 10.0) -> None:
    if session._current is not None:
        _stop_segment(session._current, timeout)
        session._current = None

    valid_segments = [p for p in session.segment_paths if p.exists()]
    if not valid_segments:
        return  # no frames ever arrived in any segment - nothing was recorded

    if len(valid_segments) == 1:
        valid_segments[0].replace(session.output_path)
        return

    # Multiple pause/resume segments: same normalize-free concat-demuxer
    # stream-copy already used for intro/outro (ffmpeg_runner.concat()) -
    # safe here because every segment came from the same CaptureSpec/fps, so
    # codec/resolution/frame rate match across all of them.
    from wellspoken.media import ffmpeg_runner

    ffmpeg_runner.concat(valid_segments, session.output_path)
    for p in valid_segments:
        p.unlink(missing_ok=True)
