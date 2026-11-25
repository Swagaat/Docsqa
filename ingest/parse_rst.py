"""Parse scikit-learn / pandas User Guide .rst files into cited sections.

Each page is split at its headings. Every section keeps:
- which library and file it came from,
- its heading path (page title > section > subsection),
- a URL to the matching part of the published docs (for citations),
- its body text with reStructuredText markup stripped.

Run from the repo root:
    python -m ingest.parse_rst            # writes data/sections.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

RAW_DIR = Path("data/raw")

# Where each library's User Guide lives locally, and where it is published online.
SOURCES = {
    "scikit-learn": {
        "dir": RAW_DIR / "scikit-learn/doc/modules",
        "base_url": "https://scikit-learn.org/stable/modules/",
    },
    "pandas": {
        "dir": RAW_DIR / "pandas/doc/source/user_guide",
        "base_url": "https://pandas.pydata.org/docs/user_guide/",
    },
}

# Directives whose whole block is dropped (images, navigation, build-only content).
DROP_DIRECTIVES = {
    "autosummary",
    "currentmodule",
    "figure",
    "image",
    "include",
    "only",
    "plot",
    "raw",
    "toctree",
}

# A heading underline/overline: one punctuation character repeated (e.g. "=====").
ADORNMENT_RE = re.compile(r"""^([=\-~^"'`#*+<>:._])\1{2,}\s*$""")
DIRECTIVE_RE = re.compile(r"^(\s*)\.\.\s+([\w:-]+)::\s*(.*)$")
EXPLICIT_MARKUP_RE = re.compile(r"^(\s*)\.\.(\s|$)")
FOOTNOTE_RE = re.compile(r"^(\s*)\.\.\s+(\[[^\]]+\])\s+(.*)$")


@dataclass
class Section:
    library: str
    doc_path: str  # path relative to data/raw
    page_title: str
    headings: list[str]  # page title first, current section last
    url: str
    text: str


# --------------------------------------------------------------------------
# Inline markup
# --------------------------------------------------------------------------


def _role_text(match: re.Match) -> str:
    content = match.group(2)
    # :ref:`shown text <target>` -> shown text
    labelled = re.match(r"^(.*?)\s*<[^>]+>$", content, flags=re.DOTALL)
    if labelled and labelled.group(1):
        return labelled.group(1)
    # Example-gallery links have no shown text, only an internal name:
    # sphx_glr_auto_examples_model_selection_plot_grid_search.py -> example: grid search
    if content.startswith("sphx_glr_"):
        name = content.removesuffix(".py").split("_plot_")[-1]
        name = name.removeprefix("sphx_glr_auto_examples_")
        return "example: " + name.replace("_", " ").strip()
    # :class:`~sklearn.impute.SimpleImputer` -> SimpleImputer
    if content.startswith("~"):
        return content[1:].split(".")[-1]
    return content.lstrip("!")


def clean_inline(text: str) -> str:
    """Strip inline reST markup but keep the readable words."""
    text = re.sub(r":([\w:.-]+):`([^`]+)`", _role_text, text)  # roles
    text = re.sub(r"`([^`<]+?)\s*<[^>]+>`__?", r"\1", text)  # `text <url>`_
    text = re.sub(r"`([^`]+)`__?", r"\1", text)  # `text`_
    text = re.sub(r"``([^`]+)``", r"`\1`", text)  # ``code`` -> `code`
    text = re.sub(r"(\[[^\]\s]+\])_", r"\1", text)  # [3]_ -> [3]
    text = re.sub(r"\|(details-start|details-split|details-end)\|", "", text)
    return text


# --------------------------------------------------------------------------
# Block-level cleanup
# --------------------------------------------------------------------------


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _skip_block(lines: list[str], i: int, base_indent: int) -> int:
    """Return the index just past the indented block that follows line i."""
    j = i + 1
    while j < len(lines):
        line = lines[j]
        if line.strip() and _indent(line) <= base_indent:
            break
        j += 1
    return j


def clean_body(raw: str, substitutions: set[str] = frozenset()) -> str:
    """Turn a section's raw reST into plain text (code and math are kept).

    ``substitutions`` are names defined with ``.. |name| image::`` elsewhere in
    the file; references to them (``|name|``) are removed.
    """
    lines = raw.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        directive = DIRECTIVE_RE.match(line)
        if directive:
            indent, name, arg = len(directive.group(1)), directive.group(2), directive.group(3)
            if name in DROP_DIRECTIVES:
                i = _skip_block(lines, i, indent)
                continue
            if arg:
                out.append(" " * indent + arg)
            # Skip the directive's ":option:" lines.
            i += 1
            while i < len(lines) and re.match(r"^\s+:[\w-]+:", lines[i]):
                i += 1
            continue

        footnote = FOOTNOTE_RE.match(line)
        if footnote:
            out.append(footnote.group(1) + footnote.group(2) + " " + footnote.group(3))
            i += 1
            continue

        # Labels (".. _name:"), substitutions and comments: drop with their block.
        if EXPLICIT_MARKUP_RE.match(line):
            i = _skip_block(lines, i, _indent(line))
            continue

        out.append(line.rstrip())
        i += 1

    text = clean_inline("\n".join(out))
    for name in substitutions:
        text = text.replace(f"|{name}|", "")
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"::[ \t]*$", ":", text, flags=re.MULTILINE)  # "Example::" -> "Example:"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Heading detection and splitting
# --------------------------------------------------------------------------


def make_anchor(title: str) -> str:
    """Approximate the HTML id Sphinx gives a section title."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return re.sub(r"^[^a-z]+", "", text).strip("-")


def find_headings(lines: list[str]) -> list[tuple[int, int, str, tuple[str, bool]]]:
    """Find headings as (first_line, line_after, title, style).

    A heading is a non-indented text line followed by an adornment line,
    optionally with a matching adornment line above it. ``style`` is
    (adornment char, has overline); the order in which styles first appear
    in a file defines heading levels.
    """
    headings = []
    i = 0
    while i < len(lines) - 1:
        line, below = lines[i], lines[i + 1]
        is_title_text = line.strip() and not ADORNMENT_RE.match(line)
        under = ADORNMENT_RE.match(below)
        if is_title_text and under:
            above = lines[i - 1] if i > 0 else ""
            has_over = bool(ADORNMENT_RE.match(above)) and above.strip() == below.strip()
            title = line.strip()
            # Without an overline the title must start at column 0 and the
            # underline must be roughly as long as the title; this avoids
            # mistaking indented code for headings.
            if has_over or (_indent(line) == 0 and len(below.strip()) >= min(len(title), 3)):
                start = i - 1 if has_over else i
                headings.append((start, i + 2, title, (under.group(1), has_over)))
                i += 2
                continue
        i += 1
    return headings


def parse_file(path: Path, library: str, base_url: str) -> list[Section]:
    lines = path.read_text(encoding="utf-8").splitlines()
    headings = find_headings(lines)
    if not headings:
        return []

    substitutions = set(re.findall(r"^\s*\.\.\s+\|([^|]+)\|", "\n".join(lines), flags=re.M))
    style_levels: dict[tuple[str, bool], int] = {}
    stack: list[str] = []
    page_url = base_url + path.stem + ".html"
    page_title = clean_inline(headings[0][2])
    sections = []

    for n, (_start, body_start, raw_title, style) in enumerate(headings):
        level = style_levels.setdefault(style, len(style_levels))
        title = clean_inline(raw_title)
        stack = stack[:level] + [title]

        body_end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        text = clean_body("\n".join(lines[body_start:body_end]), substitutions)
        if not text:
            continue  # heading immediately followed by a sub-heading

        url = page_url if n == 0 else f"{page_url}#{make_anchor(title)}"
        sections.append(
            Section(
                library=library,
                doc_path=str(path.relative_to(RAW_DIR)),
                page_title=page_title,
                headings=list(stack),
                url=url,
                text=text,
            )
        )
    return sections


def parse_all() -> list[Section]:
    sections = []
    for library, cfg in SOURCES.items():
        for path in sorted(cfg["dir"].glob("*.rst")):
            if path.stem == "index":
                continue  # table-of-contents page, no content
            sections.extend(parse_file(path, library, cfg["base_url"]))
    return sections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("data/sections.jsonl"))
    args = parser.parse_args()

    sections = parse_all()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for section in sections:
            f.write(json.dumps(asdict(section), ensure_ascii=False) + "\n")

    for library in SOURCES:
        subset = [s for s in sections if s.library == library]
        pages = len({s.doc_path for s in subset})
        words = sum(len(s.text.split()) for s in subset)
        print(f"{library}: {pages} pages, {len(subset)} sections, {words:,} words")
    print(f"Wrote {len(sections)} sections to {args.out}")


if __name__ == "__main__":
    main()
