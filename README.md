# WellSpoken

Record your screen, narrate it (AI voice or your own reworded transcript),
trim the dead air, add an intro/outro, and export — ready for YouTube or
vertical social formats. One tool, start to finish.

![WellSpoken](assets/branding/logo_1024.png)

## Setup

Requires Python 3.12 (a different version may not have wheels for every
dependency below - stick to 3.12 to avoid install issues).

```
git clone <this repo>
cd WellSpoken
py -3.12 -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

No system-wide `ffmpeg` install is needed — it's bundled via `imageio-ffmpeg`.
The AI voice models (Kokoro, Chatterbox) and the transcription model
(faster-whisper, `medium.en` — a larger, more accurate model than the
`small.en` default, roughly 1.5GB) download automatically the first time you
use each feature; you'll need internet access for that first run. Screen
recording uses Windows' native Windows.Graphics.Capture API (via the
`windows-capture` package) — no OBS or other external recorder required.

### Desktop shortcut (optional)

A Windows shortcut with the WellSpoken icon can be created by pointing it at
`env\Scripts\pythonw.exe` with `main.py` as the argument and the project
folder as the working directory — `pythonw.exe` (not `python.exe`) avoids a
console window popping up alongside the app.

## Workflow

1. **Record** — capture the full screen, a specific window, or a region you
   draw, with optional microphone audio. Pause and resume freely - paused
   time is never recorded (video/audio stay in sync across any number of
   pauses). Or skip this and pick an existing recording on the Project tab
   instead.
2. **Project** — the screen recording you're working from; shows its
   thumbnail, resolution, and duration. "Append Another Recording..." lets
   you combine two separately-recorded videos (e.g. recorded on different
   days) into one main video before narrating/captioning it.
3. **Transcribe** — if you already recorded your own narration (or the
   recording has your voice on it), transcribe it, fix any misheard lines,
   and optionally send the corrected transcript over to AI Voice to
   re-record it in a synthetic voice.
4. **AI Voice** — write (or paste) a script, pick a voice, and generate AI
   narration with time-synced captions. Four voices across two tiers: Kokoro
   ("Fast" — quick, clean, the default) and Chatterbox ("Expressive" — more
   natural-sounding, noticeably slower on CPU since it's a much larger model).
5. **Timeline** — trim dead air or mistakes from the narration on a waveform
   timeline (with a scrubbable video preview). Auto-Detect finds pauses for
   you; cuts ripple-delete from both the video and the narration together, so
   they stay in sync.
6. **Intro / Outro** — add a built-in title card or your own clip at the
   start and/or end.
7. **Export** — burned-in captions, a separate .srt/.vtt file, or both, with
   an editable caption style (font, size, bold, text/outline color,
   top/bottom position) that defaults to bold white-on-black for legibility
   over any background; a live preview updates as you adjust it, and
   "Preview Full Clip" burns a real short clip to check it in motion before
   rendering. Also: an aspect ratio preset (original, 16:9, 9:16 for
   Reels/TikTok/Stories, or 1:1 for feed posts — always letterboxed to fit,
   never cropped) with an "Also Export As" option to render extra formats
   in the same pass, for posting the same video to multiple platforms
   without re-rendering from scratch; optional background music, mixed
   under the narration at an adjustable volume and looped or trimmed to
   match the video's length automatically; and narration loudness is
   automatically normalized to a
   standard streaming level so volume stays consistent across videos.
   Renders the final combined video.

### Pronunciation

Words the AI voice mispronounces can be added to the pronunciation lexicon on
the AI Voice tab (a plain word → respelling map, also editable directly at
`assets/lexicon_default.json`). A QC pass automatically flags any caption
line where the synthesized audio didn't match the script, so mispronunciations
don't slip through silently.

## Sharing this with colleagues

- **Via git/GitHub:** the repo excludes `env/` (each person creates their own
  virtualenv locally), `scratch/` (personal test artifacts), and generated
  media files — so it stays small and clean to clone.
- **Via a zip/cloud drive:** copy the project folder but skip `env/` and
  `scratch/` for the same reason; the recipient just runs the Setup steps
  above once they have the folder.
