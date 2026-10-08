from __future__ import annotations

import csv
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

    @staticmethod
    def _read_pairs(path: str | Path) -> dict[str, str]:
        """Read word->respelling pairs from a .json or .csv file.

        CSV supports an optional header row (a first row that reads
        literally "word, respelling", case-insensitive, is skipped) so a
        teammate can build a list in Excel without knowing the internal
        format - the two-column shape is the only requirement.
        """
        path = Path(path)
        if path.suffix.lower() == ".csv":
            pairs: dict[str, str] = {}
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                for i, row in enumerate(csv.reader(f)):
                    if not row or not row[0].strip():
                        continue
                    if i == 0 and [c.strip().lower() for c in row[:2]] == ["word", "respelling"]:
                        continue
                    if len(row) < 2 or not row[1].strip():
                        continue
                    pairs[row[0].strip()] = row[1].strip()
            return pairs
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def import_file(self, path: str | Path) -> int:
        """Merge word->respelling pairs from a .json or .csv file into this
        lexicon (existing keys are overwritten by the imported value).
        Returns the number of entries read from the file, for a confirmation
        message - the point of this is letting a teammate hand over a file of
        pronunciations built independently (e.g. in Excel) instead of everyone
        re-typing entries one at a time in the GUI."""
        pairs = self._read_pairs(path)
        self.overrides.update(pairs)
        return len(pairs)

    def export_csv(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["word", "respelling"])
            for word, respelling in sorted(self.overrides.items()):
                writer.writerow([word, respelling])

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

        SeisWare always goes first regardless of where it falls alphabetically:
        this product exists specifically to work with the SeisWare SDK, so
        mishearing it is the one mistake this app can't afford - and it had
        silently regressed out of the prompt entirely once enough formation
        names got added before it alphabetically to push it past
        MAX_PROMPT_TERMS (verified: at 50 candidate terms, "SeisWare" sat at
        position 23, one past the old cutoff of 20).
        """
        candidates = [w for w in self.overrides if not (w.isupper() and len(w) <= 5)]
        if "SeisWare" in candidates:
            candidates = ["SeisWare"] + [w for w in candidates if w != "SeisWare"]
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
