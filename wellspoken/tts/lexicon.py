from __future__ import annotations

import json
import re
from pathlib import Path


class Lexicon:
    """Word -> respelling map applied to script text before TTS synthesis.

    The TTS engine infers pronunciation from plain text, so the only lever we
    have for "always pronounce correctly" on a free/local engine is to rewrite
    risky words (proper nouns, acronyms, jargon) into a spelling it will read
    correctly, before the text ever reaches the synthesizer.
    """

    def __init__(self, overrides: dict[str, str] | None = None):
        self.overrides = dict(overrides or {})

    @staticmethod
    def load(path: str | Path) -> "Lexicon":
        path = Path(path)
        if not path.exists():
            return Lexicon()
        with open(path, "r", encoding="utf-8") as f:
            return Lexicon(json.load(f))

    def save(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.overrides, f, indent=2, ensure_ascii=False, sort_keys=True)

    def set(self, word: str, respelling: str) -> None:
        self.overrides[word] = respelling

    def remove(self, word: str) -> None:
        self.overrides.pop(word, None)

    MAX_PROMPT_TERMS = 20

    def prompt_text(self) -> str:
        """Comma-separated list of known domain terms, for Whisper's
        initial_prompt - biases transcription toward recognizing these words
        instead of guessing a similar-sounding common word (verified this
        matters: without a prompt, real narration audio saying "SeisWare"
        was transcribed as "Isos"/"Heisler's" - with the term listed here, it
        was correctly recognized).

        Deliberately short and curated, NOT the whole lexicon: verified
        empirically that once the lexicon grew past ~80 entries, cramming
        all of them into initial_prompt performed identically to passing no
        prompt at all (still misheard "SeisWare" as "Heisler's") - the
        signal for any one term gets diluted into noise. A short prompt with
        just the term fixed it immediately. Short acronym-like keys (<=5
        chars, all-caps) are skipped - Whisper already handles common short
        acronyms reasonably from context, so they're not worth the prompt
        budget; this keeps the list focused on the proper nouns/formation
        names Whisper has never seen in training, which is where prompting
        actually earns its keep.
        """
        candidates = [w for w in self.overrides if not (w.isupper() and len(w) <= 5)]
        return ", ".join(candidates[: self.MAX_PROMPT_TERMS])

    def _pattern(self) -> re.Pattern:
        # Trailing ('s|s)? catches plurals/possessives with no apostrophe
        # ("SeisWares") as well as with one ("SeisWare's") - a bare \b right
        # after the term only matches when the NEXT character is already a
        # non-word boundary, so "SeisWares" (no break between "SeisWare" and
        # the trailing s) silently failed to match at all and passed through
        # unrespelled/uncorrected (verified empirically - a real regression
        # report). The captured suffix is re-appended after substitution so
        # "SeisWares" -> "Size-wheres" and "SeisWare's" -> "Size-where's",
        # not just bare "SeisWare" ones.
        return re.compile(
            r"\b(" + "|".join(re.escape(w) for w in self.overrides) + r")('s|s)?\b",
            flags=re.IGNORECASE,
        )

    def canonicalize(self, text: str) -> str:
        """Fix the capitalization of any lexicon key found (case-insensitively)
        in `text` to its exact canonical spelling - e.g. "Seisware" ->
        "SeisWare". Whisper transcribes real narration using ordinary English
        capitalization rules (capitalize the first letter, nothing else), so
        it has no way to know a brand name has non-standard internal caps
        like SeisWare's capital W - this corrects that after the fact,
        independent of apply()'s opposite job (respelling for pronunciation
        before TTS, not correcting spelling after ASR)."""
        if not self.overrides:
            return text
        canonical = {w.lower(): w for w in self.overrides}

        def _sub(match: re.Match) -> str:
            suffix = match.group(2) or ""
            return canonical[match.group(1).lower()] + suffix

        return self._pattern().sub(_sub, text)

    def apply(self, text: str) -> str:
        """Case-preserving whole-word substitution of every override in text."""
        if not self.overrides:
            return text
        lookup = {w.lower(): r for w, r in self.overrides.items()}

        def _sub(match: re.Match) -> str:
            suffix = match.group(2) or ""
            return lookup[match.group(1).lower()] + suffix

        return self._pattern().sub(_sub, text)
