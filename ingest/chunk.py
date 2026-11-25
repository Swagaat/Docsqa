"""Split parsed sections into retrieval-sized chunks.

Reads data/sections.jsonl (from ingest.parse_rst) and writes data/chunks.jsonl.

- A section that fits in ``max_words`` becomes one chunk.
- A longer section is split at paragraph breaks (blank lines), so code
  examples and lists stay whole. Consecutive pieces share up to
  ``overlap_words`` of trailing paragraphs, so a sentence near a boundary is
  still findable with its context.
- A single paragraph that is longer than ``max_words`` is split by lines, and a
  single line that is still too long is split by words.
- Every chunk starts with its heading path ("pandas > Group by > Aggregation"),
  so a chunk read on its own still says what it is about. It keeps its
  section's citation URL. ``max_words`` limits the body only; the heading
  line comes on top.

Word counts are a stand-in for model tokens (roughly 1 word ~ 1.3 tokens for
English prose; code uses more). That keeps this step free of any tokenizer
dependency.

Run from the repo root:
    python -m ingest.chunk
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_WORDS = 400
OVERLAP_WORDS = 60


@dataclass
class Chunk:
    id: str  # e.g. "scikit-learn/clustering/0007"
    library: str
    doc_path: str
    headings: list[str]
    url: str
    text: str  # heading path line + body


def _words(text: str) -> int:
    return len(text.split())


def _split_long_unit(unit: str, max_words: int) -> list[str]:
    """Split one oversized paragraph by lines, then by words if needed."""
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for line in unit.splitlines():
        n = _words(line)
        if n > max_words:
            if current:
                pieces.append("\n".join(current))
                current, count = [], 0
            words = line.split()
            pieces.extend(
                " ".join(words[i : i + max_words]) for i in range(0, len(words), max_words)
            )
            continue
        if count + n > max_words and current:
            pieces.append("\n".join(current))
            current, count = [], 0
        current.append(line)
        count += n
    if current:
        pieces.append("\n".join(current))
    return pieces


def split_text(text: str, max_words: int = MAX_WORDS, overlap_words: int = OVERLAP_WORDS):
    """Split ``text`` into pieces of at most ``max_words`` words."""
    if _words(text) <= max_words:
        return [text]

    units: list[str] = []
    for paragraph in text.split("\n\n"):
        if _words(paragraph) > max_words:
            units.extend(_split_long_unit(paragraph, max_words))
        elif paragraph.strip():
            units.append(paragraph)

    pieces: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and _words("\n\n".join(current + [unit])) > max_words:
            pieces.append("\n\n".join(current))
            # Carry trailing paragraphs into the next piece as overlap,
            # without letting the overlap plus the new unit overflow.
            carry: list[str] = []
            for prev in reversed(current):
                total = _words("\n\n".join([prev, *carry, unit]))
                if _words("\n\n".join([prev, *carry])) > overlap_words or total > max_words:
                    break
                carry.insert(0, prev)
            current = carry
        current.append(unit)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def chunk_sections(
    sections: Iterable[dict],
    max_words: int = MAX_WORDS,
    overlap_words: int = OVERLAP_WORDS,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    counters: dict[str, int] = {}
    for section in sections:
        page = f"{section['library']}/{Path(section['doc_path']).stem}"
        header = " > ".join([section["library"], *section["headings"]])
        for piece in split_text(section["text"], max_words, overlap_words):
            n = counters.get(page, 0)
            counters[page] = n + 1
            chunks.append(
                Chunk(
                    id=f"{page}/{n:04d}",
                    library=section["library"],
                    doc_path=section["doc_path"],
                    headings=section["headings"],
                    url=section["url"],
                    text=f"{header}\n\n{piece}",
                )
            )
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sections", type=Path, default=Path("data/sections.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("data/chunks.jsonl"))
    parser.add_argument("--max-words", type=int, default=MAX_WORDS)
    parser.add_argument("--overlap-words", type=int, default=OVERLAP_WORDS)
    args = parser.parse_args()

    if not args.sections.exists():
        parser.error(f"{args.sections} not found; run `python -m ingest.parse_rst` first")

    with args.sections.open(encoding="utf-8") as f:
        sections = [json.loads(line) for line in f]
    chunks = chunk_sections(sections, args.max_words, args.overlap_words)

    with args.out.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")

    sizes = sorted(_words(c.text) for c in chunks)
    print(f"{len(sections)} sections -> {len(chunks)} chunks")
    print(f"words per chunk: median {sizes[len(sizes) // 2]}, max {sizes[-1]}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
