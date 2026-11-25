from pathlib import Path

from ingest.parse_rst import RAW_DIR, clean_body, clean_inline, make_anchor, parse_file

SAMPLE = """\
.. _impute:

============================
Imputation of missing values
============================

.. currentmodule:: sklearn.impute

Use :class:`SimpleImputer` or :class:`~sklearn.impute.KNNImputer`.
See :ref:`the guide <iterative_imputer>` and `Wikipedia <https://example.org>`_.

.. figure:: ../images/plot.png
   :scale: 50

   A caption that should be dropped.

Univariate vs. Multivariate Imputation
======================================

Univariate imputers use one feature [1]_.

Example::

    >>> imp = SimpleImputer()

Empty parent
============

Child section
-------------

.. note::

   The ``strategy`` parameter matters.

.. [1] A footnote.
"""


def _parse(tmp_path: Path, text: str):
    # parse_file expects files under RAW_DIR, so mirror that layout in tmp_path.
    path = tmp_path / RAW_DIR / "scikit-learn/doc/modules/impute.rst"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return parse_file(path.relative_to(tmp_path), "scikit-learn", "https://ex.org/modules/")


def test_clean_inline_roles_and_links():
    assert clean_inline(":class:`SimpleImputer`") == "SimpleImputer"
    assert clean_inline(":func:`~sklearn.utils.resample`") == "resample"
    assert clean_inline(":ref:`user guide <ug>`") == "user guide"
    assert clean_inline("`docs <https://x.org>`_") == "docs"
    assert clean_inline("``fit``") == "`fit`"
    assert clean_inline("see [3]_") == "see [3]"


def test_clean_body_drops_figures_and_keeps_code():
    body = "Intro::\n\n    x = 1\n\n.. image:: a.png\n   :width: 10\n\nEnd."
    assert clean_body(body) == "Intro:\n\n    x = 1\n\nEnd."


def test_clean_body_removes_image_substitutions_but_not_math():
    body = "|plot_a| |plot_b|\n\nThe norm |A| stays."
    assert clean_body(body, {"plot_a", "plot_b"}) == "The norm |A| stays."


def test_make_anchor():
    assert make_anchor("Univariate vs. Multivariate Imputation") == (
        "univariate-vs-multivariate-imputation"
    )


def test_parse_file_sections(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sections = _parse(tmp_path, SAMPLE)

    # "Empty parent" has no body text of its own, so it is skipped.
    assert [s.headings for s in sections] == [
        ["Imputation of missing values"],
        ["Imputation of missing values", "Univariate vs. Multivariate Imputation"],
        ["Imputation of missing values", "Empty parent", "Child section"],
    ]

    intro, univariate, child = sections
    assert intro.url == "https://ex.org/modules/impute.html"
    assert univariate.url.endswith("impute.html#univariate-vs-multivariate-imputation")
    assert intro.doc_path == "scikit-learn/doc/modules/impute.rst"

    assert "SimpleImputer or KNNImputer" in intro.text
    assert "the guide and Wikipedia." in intro.text
    assert "caption" not in intro.text and "currentmodule" not in intro.text
    assert ">>> imp = SimpleImputer()" in univariate.text
    assert "one feature [1]" in univariate.text
    assert "The `strategy` parameter matters." in child.text
    assert "[1] A footnote." in child.text
