"""Reading-order repair for extracted words.

MuPDF reports text in content-stream order. That order is usually the reading order,
but form fields and checkbox marks are often drawn after everything else, so a filled-in
"X" ends up at the end of the page instead of next to its label. Several identical marks
then can't be told apart and the diff pairs the wrong ones.

This module moves such short "satellite" rows into the row of longer text they sit on
(between its words, or right next to them), and orders the lines within a row from left
to right.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_left, bisect_right

# Raw word tuple: (x0, y0, x1, y1, text, block, line)
RawWord = tuple

# A satellite row has at most this many words, each at most this many characters
# (checkbox marks, option letters, bullets). Longer text is distinctive enough to be
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

    # Short rows (checkbox marks, option letters, short fill-ins) are placed on the row
    # of longer text they sit on, whichever block MuPDF put them in: the grouping of
    # nearby marks into blocks differs between otherwise identical versions.
    def short(row: list[_Line]) -> bool:
        ws = [w for ln in row for w in ln.words]
        return len(ws) <= SATELLITE_MAX_WORDS and all(len(w[4]) <= SATELLITE_MAX_CHARS for w in ws)

    hosts = sorted(
        ((b, row, ln) for b, brows in rows.items() for row in brows if not short(row) for ln in row),
        key=lambda h: h[2].y0,
    )
    host_y0 = [h[2].y0 for h in hosts]
    max_h = max((h[2].height for h in hosts), default=0.0)
    moves = []
    for s, brows in rows.items():
        for sat_row in brows:
            if not short(sat_row):
                continue
            probe = _Line(s, -1)
            for ln in sat_row:
                for w in ln.words:
                    probe.add(w)
            lo, hi = bisect_left(host_y0, probe.y0 - max_h), bisect_right(host_y0, probe.y1)
            same = [(b, row, ln) for b, row, ln in hosts[lo:hi] if _same_row(probe, ln)]
            if not same:
                continue
            between = any(ln.x1 <= probe.x0 + 1 for _, _, ln in same) and any(
                ln.x0 >= probe.x1 - 1 for _, _, ln in same
            )
            d, b, row = min(((_gap(probe, ln), b, row) for b, row, ln in same), key=lambda t: t[0])
            if between or d <= probe.height:
                moves.append((s, sat_row, b, row))
    for s, sat_row, b, row in moves:
        rows[s] = [r for r in rows[s] if r is not sat_row]
        for ln in sat_row:
            ln.words = [(*w[:5], b, SATELLITE_LINE_BASE + s * 100 + ln.line) for w in ln.words]
            row.append(ln)

    out: list[RawWord] = []
    for brows in rows.values():
        for row in brows:
            if len(row) > 1 and _sortable(row):
                row = sorted(row, key=lambda ln: ln.x0)
            for ln in row:
                out.extend(ln.words)
    return out
