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


@dataclass
class Page:
    index: int
    width: float
    height: float
    words: list[Word]

    @property
    def has_text(self) -> bool:
        return bool(self.words)


@dataclass
class Document:
    path: str
    pages: list[Page]

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True)
class CompareOptions:
    ignore_case: bool = False
    ignore_punctuation: bool = False
    dehyphenate: bool = True


ChangeKind = Literal["insert", "delete", "replace"]


@dataclass
class Change:
    """A text change.

    ``old_tokens`` is empty for inserts and ``new_tokens`` is empty for deletes.
    The empty side gets an anchor word marking where the change happens.
    """

    id: int
    kind: ChangeKind
    old_tokens: list[Token]
    new_tokens: list[Token]
    old_anchor: Word | None = None
    new_anchor: Word | None = None
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


@dataclass
class DiffResult:
    old: Document
    new: Document
    options: CompareOptions
    changes: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.changes

    def counts(self) -> dict[str, int]:
        c = {"insert": 0, "delete": 0, "replace": 0}
        for ch in self.changes:
            c[ch.kind] += 1
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
