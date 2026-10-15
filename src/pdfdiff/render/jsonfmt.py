"""JSON change list. Page numbers are 1-based and bboxes are in PDF points
(x0, y0, x1, y1), with the origin at the top-left of the page."""

from __future__ import annotations

import json
from dataclasses import asdict

from .. import __version__
from ..model import Change, DiffResult, Document, PageChange, Word, line_rects

SCHEMA_VERSION = 1


def to_dict(result: DiffResult) -> dict:
    counts = result.counts()
    return {
        "schema": SCHEMA_VERSION,
        "tool": {"name": "pdfdiff", "version": __version__},
        "old": _doc(result.old),
        "new": _doc(result.new),
        "options": asdict(result.options),
        "summary": {
            "identical": result.identical,
            "changes": len(result.changes) + len(result.page_changes),
            "insertions": counts["insert"],
            "deletions": counts["delete"],
            "replacements": counts["replace"],
            "pages_inserted": counts["page_inserted"],
            "pages_deleted": counts["page_deleted"],
        },
        "page_changes": [_page_change(pc) for pc in result.page_changes],
        "page_map": [[_p1(o), _p1(n)] for o, n in result.page_map],
        "changes": [_change(c) for c in result.changes],
        "warnings": list(result.warnings),
    }


def dumps(result: DiffResult, indent: int | None = 2) -> str:
    return json.dumps(to_dict(result), indent=indent, ensure_ascii=False)


def write(result: DiffResult, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps(result))
        f.write("\n")


def _doc(doc: Document) -> dict:
    return {
        "path": doc.path,
        "pages": doc.page_count,
        "page_sizes": [[round(p.width, 2), round(p.height, 2)] for p in doc.pages],
    }


def _side(text: str, words: list[Word], pages: list[int], anchor: Word | None, caret_x: float | None) -> dict:
    side: dict = {
        "text": text,
        "pages": [p + 1 for p in pages],
        "rects": [
            {"page": page + 1, "bbox": [round(v, 2) for v in bbox]}
            for page, bbox in line_rects(words)
        ],
    }
    if anchor is not None:
        side["anchor"] = {"page": anchor.page + 1, "bbox": [round(v, 2) for v in anchor.bbox]}
        if caret_x is not None:
            side["anchor"]["caret_x"] = round(caret_x, 2)
    return side


def _change(c: Change) -> dict:
    return {
        "id": c.id,
        "kind": c.kind,
        "old": _side(c.old_text, c.old_words, c.old_pages, c.old_anchor, c.old_caret_x),
        "new": _side(c.new_text, c.new_words, c.new_pages, c.new_anchor, c.new_caret_x),
        "context": {"before": c.context_before, "after": c.context_after},
    }


def _p1(page: int | None) -> int | None:
    return None if page is None else page + 1


def _page_change(pc: PageChange) -> dict:
    if pc.kind == "page_inserted":
        loc = {"new_page": pc.page + 1, "after_old_page": pc.after + 1}
    else:
        loc = {"old_page": pc.page + 1, "after_new_page": pc.after + 1}
    return {
        "id": pc.id,
        "kind": pc.kind,
        **loc,
        "word_count": pc.word_count,
        "preview": pc.preview,
    }
