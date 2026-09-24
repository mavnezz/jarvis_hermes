"""Turn an LLM token stream into speakable chunks.

Time to first audio dominates how responsive the assistant feels, so a chunk is
released as soon as a sentence boundary allows and the rest is synthesised
while the speaker is already busy.

Two things make naive splitting on `.` wrong for German: decimals and ordinals
("21.5", "1. Januar") and the language's fondness for abbreviations ("z. B.",
"bzw.", "ca."). A boundary is therefore only accepted when the punctuation is
followed by real whitespace — never merely by end-of-buffer, which during
streaming just means "the next token has not arrived yet" — and when the token
in front of it does not look like an abbreviation or a number.
"""

from __future__ import annotations

import re

# During streaming a boundary needs trailing whitespace. End-of-buffer is not a
# boundary: the stream is simply mid-token.
_BOUNDARY = re.compile(r"[.!?…]+[\"')\]]*\s")
_SOFT_BOUNDARY = re.compile(r"[,;:]\s")

# A period after a single letter or a digit run is almost never a sentence end.
_INITIAL_OR_ORDINAL = re.compile(r"(?:^|\s)(?:\w|\d+)\.$")

_ABBREVIATIONS = {
    "abb.", "abs.", "bspw.", "bzgl.", "bzw.", "ca.", "d.h.", "dr.", "etc.",
    "evtl.", "frl.", "fr.", "ggf.", "hr.", "inkl.", "insb.", "jhd.", "kap.",
    "max.", "min.", "mio.", "mrd.", "nr.", "o.ä.", "od.", "prof.", "s.o.",
    "s.u.", "sog.", "st.", "str.", "tel.", "u.a.", "u.ä.", "usw.", "v.a.",
    "vgl.", "vs.", "z.b.", "zzgl.",
}

FIRST_CHUNK_MIN_CHARS = 4
LATER_CHUNK_MIN_CHARS = 40
HARD_LIMIT_CHARS = 260


def _is_false_boundary(candidate: str) -> bool:
    """True when the period belongs to an abbreviation, initial or number."""
    if not candidate.endswith((".", ".\"", ".'", ".)", ".]")):
        return False
    stripped = candidate.rstrip("\"')]")
    if _INITIAL_OR_ORDINAL.search(stripped):
        return True
    last_word = stripped.split()[-1].lower() if stripped.split() else ""
    return last_word in _ABBREVIATIONS


class SentenceChunker:
    """Accumulates tokens and yields chunks ready for synthesis."""

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted = 0

    def push(self, token: str) -> list[str]:
        self._buffer += token
        chunks: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            chunks.append(chunk)
        return chunks

    def flush(self) -> str | None:
        """Drain whatever is left; at end of stream there is no ambiguity."""
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return None
        self._emitted += 1
        return text

    # -- internals ---------------------------------------------------------

    def _minimum(self) -> int:
        return FIRST_CHUNK_MIN_CHARS if self._emitted == 0 else LATER_CHUNK_MIN_CHARS

    def _take(self) -> str | None:
        minimum = self._minimum()

        search_from = 0
        while True:
            match = _BOUNDARY.search(self._buffer, search_from)
            if match is None:
                break
            end = match.end()
            candidate = self._buffer[:end].strip()
            if len(candidate) >= minimum and not _is_false_boundary(candidate):
                self._buffer = self._buffer[end:]
                self._emitted += 1
                return candidate
            search_from = end

        if len(self._buffer) >= HARD_LIMIT_CHARS:
            soft = None
            for found in _SOFT_BOUNDARY.finditer(self._buffer[:HARD_LIMIT_CHARS]):
                soft = found
            cut = soft.end() if soft else self._buffer.rfind(" ", 0, HARD_LIMIT_CHARS)
            if cut > minimum:
                text = self._buffer[:cut].strip()
                self._buffer = self._buffer[cut:]
                self._emitted += 1
                return text

        return None
