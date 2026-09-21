"""Pure unit tests -- no DB, no Hermes, no embedding model."""

from app.materials.ingest.chunker import (
    MAX_CHARS,
    MIN_CHARS,
    OVERLAP_CHARS,
    chunk,
)


def _para(word: str, length: int) -> str:
    """A paragraph of roughly `length` chars built from a repeated word."""
    unit = word + " "
    return (unit * (length // len(unit) + 1))[:length].strip()


def test_empty_and_whitespace_only():
    assert chunk("") == []
    assert chunk("   \n\n  \t ") == []


def test_short_document_is_one_chunk():
    text = "Entropy measures disorder in a thermodynamic system, and it only increases."
    result = chunk(text)
    assert len(result) == 1
    assert result[0].text == text
    assert (result[0].char_start, result[0].char_end) == (0, len(text))


def test_offsets_are_an_exact_slice_of_the_source():
    """The invariant the whole module rests on: char_start/char_end must locate
    the chunk in the original text, so a UI can highlight it later."""
    text = "\n\n".join(_para("thermodynamics", 700) for _ in range(6))
    for c in chunk(text):
        assert c.text == text[c.char_start : c.char_end]


def test_respects_size_ceiling_allowing_for_overlap():
    text = "\n\n".join(_para("entropy", 600) for _ in range(8))
    for c in chunk(text):
        assert len(c.text) <= MAX_CHARS + OVERLAP_CHARS


def test_adjacent_chunks_overlap():
    text = "\n\n".join(_para("enthalpy", 800) for _ in range(4))
    result = chunk(text)
    assert len(result) > 1
    for prev, nxt in zip(result, result[1:]):
        # The next chunk starts before the previous one ends.
        assert nxt.char_start < prev.char_end
        # ...but never swallows it whole.
        assert nxt.char_start > prev.char_start


def test_oversized_paragraph_splits_on_sentences():
    sentence = _para("reversible", 300) + "."
    text = " ".join([sentence] * 8)  # one paragraph, ~2400 chars
    result = chunk(text)
    assert len(result) > 1
    # Sentence-boundary splitting means chunks end at punctuation, not mid-word.
    assert any(c.text.rstrip().endswith(".") for c in result)


def test_sentence_with_no_punctuation_falls_back_to_hard_split():
    text = _para("x", MAX_CHARS * 3)  # no '.', '!' or '?' anywhere
    result = chunk(text)
    assert len(result) >= 3
    for c in result:
        assert c.text == text[c.char_start : c.char_end]


def test_isolated_short_trailing_junk_is_dropped():
    body = _para("equilibrium", 500)
    text = f"{body}\n\n7"  # a page number stranded at the end
    result = chunk(text)
    assert all(len(c.text.strip()) >= MIN_CHARS for c in result)
    assert not any(c.text.strip() == "7" for c in result)


def test_short_paragraphs_are_merged_not_deleted():
    """Bullet lists are short paragraphs; dropping them would lose real
    content, so they must survive by merging into a neighbour."""
    text = "\n\n".join(["Heading", "- first bullet", "- second bullet", _para("gibbs", 300)])
    result = chunk(text)
    assert len(result) == 1
    assert "first bullet" in result[0].text
    assert "second bullet" in result[0].text


def test_covers_the_whole_document():
    text = "\n\n".join(_para("carnot", 650) for _ in range(10))
    result = chunk(text)
    assert result[0].char_start == 0
    assert result[-1].char_end == len(text)
    # No gaps: each chunk starts at or before the previous one's end.
    for prev, nxt in zip(result, result[1:]):
        assert nxt.char_start <= prev.char_end
