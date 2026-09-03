from __future__ import annotations

# voice_id, display label, engine ("kokoro" or "chatterbox"). Kokoro-82M
# (Apache-2.0) is the fast default tier. Chatterbox-Turbo (MIT) is a slower,
# more expressive second tier with emotion/inflection control, cloned from
# public-domain reference clips (see assets/voice_refs/NOTICE.md) since it
# has no built-in preset voices of its own.
CURATED_VOICES: list[tuple[str, str, str]] = [
    ("af_heart", "Woman (Fast)", "kokoro"),
    ("am_michael", "Man (Fast)", "kokoro"),
    ("chatterbox_female", "Woman (Expressive)", "chatterbox"),
    ("chatterbox_male", "Man (Expressive)", "chatterbox"),
]

VOICE_ENGINES: dict[str, str] = {voice_id: engine for voice_id, _label, engine in CURATED_VOICES}

DEFAULT_VOICE_ID = CURATED_VOICES[0][0]
