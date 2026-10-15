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


_VOCAB = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee "
    "zulu amber basil cedar dune ember fjord grove heron iris jade kelp lotus maple "
    "nectar onyx pearl quartz raven sage thistle umber violet willow yarrow zephyr"
).split()


def para(seed: int, n: int = 60) -> str:
    """Deterministic pseudo-random paragraph; different seeds share almost no 2-grams."""
    import random

    rng = random.Random(seed)
    return " ".join(rng.choice(_VOCAB) + str(seed) * (i % 3 == 0) for i in range(n)) + "."


def make_pdf(
    path: Path,
    pages: list[list[str]],
    *,
    password: str | None = None,
    header: str | None = None,
    footer: str | None = None,
) -> Path:
    """Write a PDF where each page is a list of paragraphs (one text block each).

    ``header``/``footer`` are drawn on every page; ``{n}`` and ``{total}`` are replaced
    by the page number and count.
    """
    doc = pymupdf.open()
    for n, paragraphs in enumerate(pages, 1):
        page = doc.new_page(width=612, height=792)
        for text, y in ((header, 40), (footer, 770)):
            if text:
                page.insert_text((72, y), text.format(n=n, total=len(pages)), fontsize=9, fontname="helv")
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
