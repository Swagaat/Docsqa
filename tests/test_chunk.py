from ingest.chunk import chunk_sections, split_text


def _para(word: str, n: int) -> str:
    return " ".join([word] * n)


def _section(text: str, **overrides) -> dict:
    section = {
        "library": "pandas",
        "doc_path": "pandas/doc/source/user_guide/groupby.rst",
        "page_title": "Group by",
        "headings": ["Group by", "Aggregation"],
        "url": "https://ex.org/groupby.html#aggregation",
        "text": text,
    }
    section.update(overrides)
    return section


def test_short_text_is_one_piece():
    assert split_text("just a few words", max_words=10) == ["just a few words"]


def test_splits_at_paragraphs_with_overlap():
    text = "\n\n".join([_para("a", 6), _para("b", 6), _para("c", 3), _para("d", 6)])
    pieces = split_text(text, max_words=15, overlap_words=5)

    assert all(len(p.split()) <= 15 for p in pieces)
    # Paragraphs are never cut in half.
    for piece in pieces:
        for paragraph in piece.split("\n\n"):
            assert len(set(paragraph.split())) == 1
    # The short "c" paragraph is carried over as overlap.
    assert pieces[1].startswith(_para("c", 3))
    # Nothing is lost.
    assert set(" ".join(pieces).split()) == {"a", "b", "c", "d"}


def test_oversized_paragraph_is_split_by_lines_then_words():
    code = "\n".join(["x = 1 2 3"] * 10)  # 10 lines x 5 words = 50 words
    long_line = _para("w", 25)
    pieces = split_text(code + "\n\n" + long_line, max_words=12, overlap_words=0)

    assert all(len(p.split()) <= 12 for p in pieces)
    assert pieces[0] == "\n".join(["x = 1 2 3"] * 2)  # whole lines kept
    assert sum(p.count("w") for p in pieces) == 25


def test_chunk_ids_headers_and_urls():
    sections = [
        _section(_para("a", 5)),
        _section("\n\n".join([_para("b", 8), _para("c", 8)])),
    ]
    chunks = chunk_sections(sections, max_words=10, overlap_words=0)

    assert [c.id for c in chunks] == [
        "pandas/groupby/0000",
        "pandas/groupby/0001",
        "pandas/groupby/0002",
    ]
    assert all(c.url == "https://ex.org/groupby.html#aggregation" for c in chunks)
    assert chunks[0].text == "pandas > Group by > Aggregation\n\n" + _para("a", 5)
