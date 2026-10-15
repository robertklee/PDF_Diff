from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

LOREM = [
    "The quick brown fox jumps over the lazy dog near the river bank.",
    "Pack my box with five dozen liquor jugs before the morning shipment arrives.",
    "Sphinx of black quartz, judge my vow, said the weary traveler at dusk.",
    "How vexingly quick daft zebras jump when the meadow is full of clover.",
]


def make_pdf(path: Path, pages: list[list[str]], *, password: str | None = None) -> Path:
    """Write a PDF where each page is a list of paragraphs (one text block each)."""
    doc = pymupdf.open()
    for paragraphs in pages:
        page = doc.new_page(width=612, height=792)
        y = 72.0
        for para in paragraphs:
            rect = pymupdf.Rect(72, y, 540, 760)
            unused = page.insert_textbox(rect, para, fontsize=11, fontname="helv")
            assert unused >= 0, "paragraph does not fit on page"
            y += rect.height - unused + 14
    kwargs = {}
    if password:
        kwargs = dict(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password
        )
    doc.save(path, **kwargs)
    doc.close()
    return path


@pytest.fixture
def pdf(tmp_path):
    counter = {"n": 0}

    def _make(pages: list[list[str]], **kwargs) -> Path:
        counter["n"] += 1
        return make_pdf(tmp_path / f"doc{counter['n']}.pdf", pages, **kwargs)

    return _make
