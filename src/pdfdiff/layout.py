"""Reading-order repair for extracted words.

MuPDF reports text in content-stream order. That order is usually the reading order,
but form fields and checkbox marks are often drawn after everything else, so a filled-in
"X" ends up at the end of the page instead of next to its label. Several identical marks
then can't be told apart and the diff pairs the wrong ones.

This module moves such small "satellite" blocks into the row of text they sit on, and
orders the lines within a row from left to right.
"""

from __future__ import annotations

import unicodedata

# Raw word tuple: (x0, y0, x1, y1, text, block, line)
RawWord = tuple

# A satellite is a block of at most this many words, each at most this many characters
# (checkbox marks, bullets, short table cells). Longer text is distinctive enough to be
# matched correctly wherever it appears.
SATELLITE_MAX_WORDS = 3
SATELLITE_MAX_CHARS = 3
# Satellite line numbers start here so they never merge with the host's lines.
SATELLITE_LINE_BASE = 100_000
_MIN_OVERLAP = 0.5  # vertical overlap, as a fraction of the smaller line height
_MIN_HEIGHT_RATIO = 0.4


class _Line:
    __slots__ = ("block", "line", "words", "x0", "y0", "x1", "y1")

    def __init__(self, block: int, line: int) -> None:
        self.block, self.line = block, line
        self.words: list[RawWord] = []
        self.x0 = self.y0 = float("inf")
        self.x1 = self.y1 = float("-inf")

    def add(self, w: RawWord) -> None:
        self.words.append(w)
        self.x0, self.y0 = min(self.x0, w[0]), min(self.y0, w[1])
        self.x1, self.y1 = max(self.x1, w[2]), max(self.y1, w[3])

    @property
    def height(self) -> float:
        return self.y1 - self.y0


def _same_row(a: _Line, b: _Line) -> bool:
    ha, hb = a.height, b.height
    if ha <= 0 or hb <= 0 or min(ha, hb) / max(ha, hb) < _MIN_HEIGHT_RATIO:
        return False
    return min(a.y1, b.y1) - max(a.y0, b.y0) >= _MIN_OVERLAP * min(ha, hb)


def _gap(a: _Line, b: _Line) -> float:
    return max(0.0, a.x0 - b.x1, b.x0 - a.x1)


def _sortable(row: list[_Line]) -> bool:
    """Lines in a row can be sorted left to right unless the text is right-to-left or
    vertical."""
    for ln in row:
        if sum(len(w[4]) for w in ln.words) >= 3 and ln.height > ln.x1 - ln.x0:
            return False  # taller than wide: vertical text
        for w in ln.words:
            if any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in w[4]):
                return False
    return True


def reading_order(words: list[RawWord]) -> list[RawWord]:
    """Return the page's words in reading order (see module docstring)."""
    if not words:
        return words

    lines: dict[tuple[int, int], _Line] = {}
    blocks: dict[int, list[_Line]] = {}
    for w in words:
        key = (w[5], w[6])
        ln = lines.get(key)
        if ln is None:
            ln = lines[key] = _Line(*key)
            blocks.setdefault(key[0], []).append(ln)
        ln.add(w)

    # Rows: consecutive lines of a block that share a baseline band.
    rows: dict[int, list[list[_Line]]] = {}
    for b, blines in blocks.items():
        brows: list[list[_Line]] = []
        for ln in blines:
            if brows and _same_row(brows[-1][0], ln):
                brows[-1].append(ln)
            else:
                brows.append([ln])
        rows[b] = brows

    satellites = [
        b for b, brows in rows.items()
        if len(brows) == 1
        and sum(len(ln.words) for ln in brows[0]) <= SATELLITE_MAX_WORDS
        and all(len(w[4]) <= SATELLITE_MAX_CHARS for ln in brows[0] for w in ln.words)
    ]
    sat_set = set(satellites)
    hosts = [(b, row, ln) for b, brows in rows.items() if b not in sat_set for row in brows for ln in row]

    attached: set[int] = set()
    for s in satellites:
        sat_row = rows[s][0]
        probe = sat_row[0]
        best = None
        for b, row, ln in hosts:
            if _same_row(probe, ln):
                d = _gap(probe, ln)
                if best is None or d < best[0]:
                    best = (d, b, row)
        if best is None:
            continue
        _, b, row = best
        for ln in sat_row:
            ln.words = [(*w[:5], b, SATELLITE_LINE_BASE + s * 100 + ln.line) for w in ln.words]
            row.append(ln)
        attached.add(s)

    out: list[RawWord] = []
    for b, brows in rows.items():
        if b in attached:
            continue
        for row in brows:
            if len(row) > 1 and _sortable(row):
                row = sorted(row, key=lambda ln: ln.x0)
            for ln in row:
                out.extend(ln.words)
    return out
