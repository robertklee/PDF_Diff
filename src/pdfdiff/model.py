"""Core data structures shared by extraction, diffing and renderers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Word:
    """A word as extracted from the PDF. ``page`` is 0-based."""

    text: str
    page: int
    bbox: BBox
    block: int
    line: int


@dataclass(frozen=True)
class Token:
    """A comparison unit: normalized key plus the source word(s) it came from.

    Usually one word; two when a word hyphenated across a line break is joined.
    """

    norm: str
    text: str
    words: tuple[Word, ...]

    @property
    def page(self) -> int:
        return self.words[0].page

    @property
    def block_key(self) -> tuple[int, int]:
        return (self.words[0].page, self.words[0].block)


Matrix = tuple[float, float, float, float, float, float]


@dataclass
class Page:
    index: int
    width: float  # displayed size (after /Rotate)
    height: float
    words: list[Word]
    # Maps word coordinates (unrotated page space) to displayed page space.
    matrix: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def displayed(self, bbox: BBox) -> BBox:
        a, b, c, d, e, f = self.matrix
        x0, y0, x1, y1 = bbox
        xs = (a * x0 + c * y0 + e, a * x1 + c * y1 + e)
        ys = (b * x0 + d * y0 + f, b * x1 + d * y1 + f)
        return (min(xs), min(ys), max(xs), max(ys))

    @property
    def has_text(self) -> bool:
        return bool(self.words)


@dataclass
class Document:
    path: str
    pages: list[Page]
    # Kept so renderers can reopen encrypted files.
    password: str | None = field(default=None, repr=False)
    # Problems found while reading, e.g. "page 3: text extraction failed (...)".
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True)
class CompareOptions:
    ignore_case: bool = False
    ignore_punctuation: bool = False
    dehyphenate: bool = True
    # A page is reported as inserted/deleted when at most this fraction of its
    # words match the other document.
    page_threshold: float = 0.2
    # Ignore running headers and footers: None (off), "auto" (detect repeated
    # lines near the top and bottom of pages) or (top, bottom) margins in points.
    header_footer: str | tuple[float, float] | None = None


ChangeKind = Literal["insert", "delete", "replace"]


@dataclass
class Change:
    """A text change.

    ``old_tokens`` is empty for inserts and ``new_tokens`` is empty for deletes.
    The empty side gets an anchor word marking where the change happens: the
    change is right after the anchor when ``*_anchor_after`` is true, else right
    before it. ``*_caret_x`` overrides the anchor's x position when the change point
    sits in a gap between two words on the same row, such as an empty form field.
    """

    id: int
    kind: ChangeKind
    old_tokens: list[Token]
    new_tokens: list[Token]
    old_anchor: Word | None = None
    new_anchor: Word | None = None
    old_anchor_after: bool = True
    new_anchor_after: bool = True
    old_caret_x: float | None = None
    new_caret_x: float | None = None
    context_before: str = ""
    context_after: str = ""

    @property
    def old_text(self) -> str:
        return " ".join(t.text for t in self.old_tokens)

    @property
    def new_text(self) -> str:
        return " ".join(t.text for t in self.new_tokens)

    @property
    def old_words(self) -> list[Word]:
        return [w for t in self.old_tokens for w in t.words]

    @property
    def new_words(self) -> list[Word]:
        return [w for t in self.new_tokens for w in t.words]

    @property
    def old_pages(self) -> list[int]:
        return _pages(self.old_words, self.old_anchor)

    @property
    def new_pages(self) -> list[int]:
        return _pages(self.new_words, self.new_anchor)


PageChangeKind = Literal["page_inserted", "page_deleted"]


@dataclass
class PageChange:
    """A whole page present in only one document. Page numbers are 0-based.

    ``page`` is in the new document for insertions and in the old document for
    deletions. ``after`` is the page in the *other* document it follows (-1 = before
    the first page).
    """

    id: int
    kind: PageChangeKind
    page: int
    after: int
    word_count: int
    preview: str


# Alignment of the two page sequences, in reading order: (old, new) with None for
# a page that has no counterpart.
PageMap = list[tuple[int | None, int | None]]


@dataclass
class DiffResult:
    old: Document
    new: Document
    options: CompareOptions
    changes: list[Change] = field(default_factory=list)
    page_changes: list[PageChange] = field(default_factory=list)
    page_map: PageMap = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.changes and not self.page_changes

    def counts(self) -> dict[str, int]:
        c = {"insert": 0, "delete": 0, "replace": 0, "page_inserted": 0, "page_deleted": 0}
        for ch in self.changes:
            c[ch.kind] += 1
        for pc in self.page_changes:
            c[pc.kind] += 1
        return c


def _pages(words: list[Word], anchor: Word | None) -> list[int]:
    if words:
        return sorted({w.page for w in words})
    return [anchor.page] if anchor else []


def line_rects(words: list[Word]) -> list[tuple[int, BBox]]:
    """Merge consecutive words on the same line into one rectangle per line."""
    rects: list[tuple[int, BBox]] = []
    key = None
    for w in words:
        k = (w.page, w.block, w.line)
        if k == key:
            page, (x0, y0, x1, y1) = rects[-1]
            bx0, by0, bx1, by1 = w.bbox
            rects[-1] = (page, (min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)))
        else:
            rects.append((w.page, w.bbox))
            key = k
    return rects
