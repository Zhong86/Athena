"""Split extracted text into embeddable chunks.

Paragraph-first with a character ceiling rather than a token-aware splitter: a
tokenizer dependency buys accuracy we do not need, since the ceiling below sits
well inside bge-small's 512-token window even for token-dense text.

Pure and deterministic -- no I/O, no model, no database. That is deliberate, so
this module is testable without fixtures and can be tuned by running it over a
real PDF's text in isolation.
"""

import re
from dataclasses import dataclass

# ~250 tokens of English at 4 chars/token, leaving headroom under bge-small's
# 512-token limit for token-dense material (formulae, code, CJK).
MAX_CHARS = 1000
# Carried from the tail of the previous chunk so a definition straddling a
# boundary is still retrievable from both sides.
OVERLAP_CHARS = 150
# Below this a chunk is almost always a page number, a running header, or an
# OCR artefact -- embedding it just adds noise to search results.
MIN_CHARS = 40

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    char_start: int
    char_end: int


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for sep in _PARAGRAPH_BREAK.finditer(text):
        spans.append((pos, sep.start()))
        pos = sep.end()
    spans.append((pos, len(text)))

    trimmed = [_trim(text, s, e) for s, e in spans]
    return [(s, e) for s, e in trimmed if e > s]


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = start
    for sep in _SENTENCE_BREAK.finditer(text, start, end):
        spans.append((pos, sep.start()))
        pos = sep.end()
    spans.append((pos, end))
    return [(s, e) for s, e in spans if e > s]


def _hard_split(start: int, end: int) -> list[tuple[int, int]]:
    """Last resort for a single sentence longer than the ceiling -- a wall of
    text with no punctuation, which OCR output produces regularly."""
    return [(i, min(i + MAX_CHARS, end)) for i in range(start, end, MAX_CHARS)]


def _units(text: str) -> list[tuple[int, int]]:
    """Paragraphs, subdivided until every unit fits under the ceiling."""
    units: list[tuple[int, int]] = []
    for start, end in _paragraph_spans(text):
        if end - start <= MAX_CHARS:
            units.append((start, end))
            continue
        for s, e in _sentence_spans(text, start, end):
            units.extend([(s, e)] if e - s <= MAX_CHARS else _hard_split(s, e))
    return units


def chunk(text: str) -> list[Chunk]:
    """Group units greedily under the ceiling, then overlap adjacent chunks.

    Short units (page numbers, headers) are *not* dropped here -- they merge
    into a neighbouring chunk instead. Filtering them at the unit level would
    silently delete real content from bullet lists, whereas the MIN_CHARS check
    on the finished chunks removes only genuinely isolated junk.
    """
    units = _units(text)
    if not units:
        return []

    grouped: list[tuple[int, int]] = []
    cur_start, cur_end = units[0]
    for start, end in units[1:]:
        # Measured from the running start, so the separators swallowed between
        # units count toward the ceiling too.
        if end - cur_start <= MAX_CHARS:
            cur_end = end
        else:
            grouped.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    grouped.append((cur_start, cur_end))

    chunks: list[Chunk] = []
    for i, (start, end) in enumerate(grouped):
        if i > 0:
            # Bounded by the previous chunk's start so overlap can never make
            # one chunk a superset of another.
            start = max(grouped[i - 1][0], start - OVERLAP_CHARS)
        # Kept as an exact slice of the source: char_start/char_end stay usable
        # for locating the chunk in the original text.
        chunks.append(Chunk(text=text[start:end], char_start=start, char_end=end))

    return [c for c in chunks if len(c.text.strip()) >= MIN_CHARS]
