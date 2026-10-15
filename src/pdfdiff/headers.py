"""Removal of running headers and footers (and page numbers) before comparing.

Two modes:

* explicit margins ``(top, bottom)`` in points: drop every word whose center lies
  within that distance of the top or bottom edge of the displayed page;
* ``"auto"``: drop lines in the top or bottom band of the page that repeat at the
  same height on several pages of the same document. Digits are ignored when
  comparing lines, so "Page 3 of 10" matches "Page 4 of 10". Single-word Roman
  numerals count as numbers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from .model import Document, Page, Word

BAND = 0.12  # fraction of the page height searched for headers/footers
Y_TOLERANCE = 6.0  # points
_DIGITS = re.compile(r"\d+")
_ROMAN = re.compile(r"^[ivxlcdm]+\.?$", re.I)


def strip_headers_footers(doc: Document, spec: str | tuple[float, float]) -> tuple[Document, int]:
    """Return a copy of ``doc`` without header/footer words, and the number of lines removed."""
    if spec == "auto":
        drop = _detect(doc)
    else:
        top, bottom = spec
        drop = {
            p.index: {(w.block, w.line) for w in p.words if _in_margin(p, w, top, bottom)}
            for p in doc.pages
        }
    pages = []
    removed = 0
    for p in doc.pages:
        lines = drop.get(p.index)
        if lines:
            removed += len(lines)
            p = replace(p, words=[w for w in p.words if (w.block, w.line) not in lines])
        pages.append(p)
    return replace(doc, pages=pages), removed


def _in_margin(page: Page, w: Word, top: float, bottom: float) -> bool:
    _, y0, _, y1 = page.displayed(w.bbox)
    y = (y0 + y1) / 2
    return y < top or y > page.height - bottom


def _detect(doc: Document) -> dict[int, set[tuple[int, int]]]:
    text_pages = sum(1 for p in doc.pages if p.words)
    min_pages = 2 if text_pages <= 3 else 3

    # (band, signature) -> [(page, line key, y)]
    seen: dict[tuple[str, str], list[tuple[int, tuple[int, int], float]]] = defaultdict(list)
    for p in doc.pages:
        if not p.height:
            continue
        lines: dict[tuple[int, int], list[Word]] = defaultdict(list)
        for w in p.words:
            lines[(w.block, w.line)].append(w)
        for key, words in lines.items():
            boxes = [p.displayed(w.bbox) for w in words]
            y = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2
            if y < p.height * BAND:
                band, offset = "top", y
            elif y > p.height * (1 - BAND):
                band, offset = "bottom", p.height - y
            else:
                continue
            seen[(band, _signature(words))].append((p.index, key, offset))

    drop: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for hits in seen.values():
        if len({page for page, _, _ in hits}) < min_pages:
            continue
        # Keep the hits at the most common height (within the tolerance).
        lo, hi = _densest_window(sorted(o for _, _, o in hits), 2 * Y_TOLERANCE)
        same = [(page, key) for page, key, o in hits if lo <= o <= hi]
        if len({page for page, _ in same}) >= min_pages:
            for page, key in same:
                drop[page].add(key)
    return drop


def _densest_window(values: list[float], width: float) -> tuple[float, float]:
    """The [lo, hi] range of at most ``width`` containing the most of the sorted ``values``."""
    best = (values[0], values[0])
    count = 0
    start = 0
    for end, v in enumerate(values):
        while v - values[start] > width:
            start += 1
        if end - start + 1 > count:
            count = end - start + 1
            best = (values[start], v)
    return best


def _signature(words: list[Word]) -> str:
    if len(words) == 1 and _ROMAN.match(words[0].text):
        return "#"
    return _DIGITS.sub("#", " ".join(w.text for w in words).lower())
