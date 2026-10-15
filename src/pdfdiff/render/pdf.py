"""Annotated PDF output.

* ``write_annotated`` saves copies of the old and new PDFs with highlight
  annotations (each with a popup note), carets at insertion/deletion points,
  and banners on inserted/deleted pages.
* ``write_combined`` writes one PDF: a linked summary cover, then old and new pages
  side by side, aligned with the page map. The same marks are drawn on it.

Word boxes come from text extraction in unrotated page space, the same space
annotations use. So the annotated copies need no transformation. The combined
view maps them through each source page's rotation and scaling.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass

import pymupdf

from ..model import BBox, Change, DiffResult, Document, PageChange, line_rects

COLORS = {
    "insert": (0.0, 0.62, 0.25),
    "delete": (0.86, 0.15, 0.15),
    "replace": (0.15, 0.45, 0.95),
}
LABELS = {"insert": "Inserted", "delete": "Deleted", "replace": "Replaced"}
HIGHLIGHT_OPACITY = 0.4
AUTHOR = "pdfdiff"
MAX_NOTE_TEXT = 1000
_GREY = (0.45, 0.45, 0.45)


@dataclass
class Mark:
    change_id: int
    kind: str
    rects: list[BBox]  # highlighted text on this page; empty for a caret
    caret: tuple[float, float, float] | None  # (x, line top, line bottom)
    note: str


# --------------------------------------------------------------------------- marks


def collect_marks(result: DiffResult, side: str) -> dict[int, list[Mark]]:
    """Per-page marks for one side ("old" or "new")."""
    marks: dict[int, list[Mark]] = defaultdict(list)
    for ch in result.changes:
        words = ch.old_words if side == "old" else ch.new_words
        note = change_note(ch)
        if words:
            by_page: dict[int, list[BBox]] = defaultdict(list)
            for page, bbox in line_rects(words):
                by_page[page].append(bbox)
            for page, rects in by_page.items():
                marks[page].append(Mark(ch.id, ch.kind, rects, None, note))
            continue
        anchor = ch.old_anchor if side == "old" else ch.new_anchor
        after = ch.old_anchor_after if side == "old" else ch.new_anchor_after
        if anchor is None:
            continue
        x0, y0, x1, y1 = anchor.bbox
        marks[anchor.page].append(Mark(ch.id, ch.kind, [], (x1 if after else x0, y0, y1), note))
    return marks


def change_note(ch: Change) -> str:
    lines = [f"#{ch.id} {LABELS[ch.kind]}"]
    if ch.old_tokens:
        lines.append(f"Old (p.{_pages(ch.old_pages)}): {_clip(ch.old_text)}")
    if ch.new_tokens:
        lines.append(f"New (p.{_pages(ch.new_pages)}): {_clip(ch.new_text)}")
    return "\n".join(lines)


def page_change_note(pc: PageChange) -> str:
    if pc.kind == "page_inserted":
        return f"Page inserted: new page {pc.page + 1} is not in the old document."
    return f"Page deleted: old page {pc.page + 1} is not in the new document."


def apply_marks(page: pymupdf.Page, marks: list[Mark], m: pymupdf.Matrix | None = None) -> None:
    m = m or pymupdf.Identity
    for mk in marks:
        color = COLORS[mk.kind]
        if mk.rects:
            quads = [(pymupdf.Rect(r) * m).quad for r in mk.rects]
            annot = page.add_highlight_annot(quads)
            annot.set_opacity(HIGHLIGHT_OPACITY)
        else:
            x, top, bottom = mk.caret
            center = pymupdf.Point(x, bottom - 0.25 * (bottom - top)) * m
            size = 0.6 * (bottom - top) * abs((pymupdf.Point(0, 1) * m) - (pymupdf.Point(0, 0) * m))
            annot = page.add_caret_annot(center)
            annot.set_rect(pymupdf.Rect(center, center) + (-size / 2, -size / 2, size / 2, size / 2))
        annot.set_colors(stroke=color)
        annot.set_info(title=AUTHOR, subject=f"{LABELS[mk.kind]} #{mk.change_id}", content=mk.note)
        annot.update()


# ----------------------------------------------------------------- annotated copies


def write_annotated(result: DiffResult, old_out: str, new_out: str) -> None:
    for side, doc_model, out in (("old", result.old, old_out), ("new", result.new, new_out)):
        marks = collect_marks(result, side)
        with _open(doc_model) as doc:
            for pno, page_marks in marks.items():
                apply_marks(doc[pno], page_marks)
            for pc in result.page_changes:
                own = (pc.kind == "page_deleted") == (side == "old")
                if own:
                    _page_banner(doc[pc.page], pc)
                elif doc.page_count:
                    _page_pointer(doc[max(pc.after, 0)], pc, at_start=pc.after < 0)
            _save(doc, out, keep_encryption=True)


def _page_banner(page: pymupdf.Page, pc: PageChange) -> None:
    color = COLORS["insert" if pc.kind == "page_inserted" else "delete"]
    frame = page.add_rect_annot(page.rect * page.derotation_matrix + (3, 3, -3, -3))
    frame.set_border(width=4)
    frame.set_colors(stroke=color)
    frame.set_info(title=AUTHOR, subject="Page change", content=page_change_note(pc))
    frame.update()

    label = "INSERTED PAGE (not in old document)" if pc.kind == "page_inserted" else (
        "DELETED PAGE (not in new document)"
    )
    shown = pymupdf.Rect(10, 8, min(page.rect.width - 10, 300), 26)
    annot = page.add_freetext_annot(
        shown * page.derotation_matrix,
        label,
        fontsize=10,
        fontname="helv",
        text_color=(1, 1, 1),
        fill_color=color,
        rotate=page.rotation,
    )
    annot.set_info(title=AUTHOR, subject="Page change")
    annot.update()


def _page_pointer(page: pymupdf.Page, pc: PageChange, at_start: bool) -> None:
    """Sticky note on the neighbouring page in the other document."""
    if pc.kind == "page_inserted":
        text = f"New page {pc.page + 1} was inserted {'before' if at_start else 'after'} this page."
        color = COLORS["insert"]
    else:
        text = f"Old page {pc.page + 1} was deleted {'before' if at_start else 'after'} this page."
        color = COLORS["delete"]
    shown = pymupdf.Point(page.rect.width - 24, 6 if at_start else page.rect.height - 24)
    annot = page.add_text_annot(shown * page.derotation_matrix, text, icon="Insert")
    annot.set_colors(stroke=color)
    annot.set_info(title=AUTHOR, subject="Page change", content=text)
    annot.update()


# -------------------------------------------------------------------- combined view

_MARGIN = 18.0
_HEADER = 26.0
_COVER_W, _COVER_H = 612.0, 792.0
_COVER_LINE = 13.0
_MAX_COVER_ITEMS = 1000


def write_combined(result: DiffResult, out: str) -> None:
    old_marks = collect_marks(result, "old")
    new_marks = collect_marks(result, "new")
    deleted = {pc.page: pc for pc in result.page_changes if pc.kind == "page_deleted"}
    inserted = {pc.page: pc for pc in result.page_changes if pc.kind == "page_inserted"}
    row_of_old = {o: i for i, (o, _) in enumerate(result.page_map) if o is not None}
    row_of_new = {n: i for i, (_, n) in enumerate(result.page_map) if n is not None}

    items = _cover_items(result, row_of_old, row_of_new)
    n_cover = _cover_page_count(len(items))

    with _open(result.old) as old_doc, _open(result.new) as new_doc, pymupdf.open() as out_doc:
        for _ in range(n_cover):
            out_doc.new_page(width=_COVER_W, height=_COVER_H)

        for o, n in result.page_map:
            src_pages = [old_doc[o] if o is not None else None, new_doc[n] if n is not None else None]
            cw = max(p.rect.width for p in src_pages if p is not None)
            ch = max(p.rect.height for p in src_pages if p is not None)
            page = out_doc.new_page(width=3 * _MARGIN + 2 * cw, height=_HEADER + ch + _MARGIN)
            cells = [
                pymupdf.Rect(_MARGIN, _HEADER, _MARGIN + cw, _HEADER + ch),
                pymupdf.Rect(2 * _MARGIN + cw, _HEADER, 2 * _MARGIN + 2 * cw, _HEADER + ch),
            ]
            for idx, (src_doc, pno, src, cell, marks, gone, label, path) in enumerate(
                (
                    (old_doc, o, src_pages[0], cells[0], old_marks, deleted, "OLD", result.old.path),
                    (new_doc, n, src_pages[1], cells[1], new_marks, inserted, "NEW", result.new.path),
                )
            ):
                name = _latin(os.path.basename(path))
                if src is None:
                    other_gone = (deleted if idx == 1 else inserted).get(o if idx == 1 else n)
                    msg = (
                        ("No page: deleted from old" if idx == 1 else "No page: inserted in new")
                        if other_gone
                        else "No matching page (content reflowed)"
                    )
                    page.draw_rect(cell, color=_GREY, dashes="[3] 0", width=0.5)
                    page.insert_text((cell.x0 + 8, _HEADER - 8), f"{label}  {name}  -", fontsize=9, fontname="helv", color=_GREY)
                    page.insert_text((cell.x0 + 20, cell.y0 + 40), msg, fontsize=14, fontname="helv", color=_GREY)
                    continue
                m = _fit_matrix(src, cell)
                shown = src.rect * src.derotation_matrix * m
                _show_page(page, cell, src_doc, src)
                page.draw_rect(cell, color=_GREY, width=0.5)
                apply_marks(page, marks.get(pno, []), m)
                header = f"{label}  {name}  page {pno + 1}"
                color = (0, 0, 0)
                if pno in gone:
                    header += "  -  " + ("DELETED PAGE" if idx == 0 else "INSERTED PAGE")
                    color = COLORS["delete" if idx == 0 else "insert"]
                    page.draw_rect(shown + (-2, -2, 2, 2), color=color, width=3)
                page.insert_text((cell.x0, _HEADER - 8), header, fontsize=9, fontname="helv", color=color)

        _draw_cover(out_doc, result, items, n_cover)
        _save(out_doc, out, keep_encryption=False, password=result.old.password or result.new.password)


def _show_page(page: pymupdf.Page, cell: pymupdf.Rect, src_doc, src: pymupdf.Page) -> None:
    """Draw ``src`` into ``cell`` the way a viewer shows it (including /Rotate).

    show_pdf_page ignores the source page's rotation, and its ``rotate`` argument
    pivots the unrotated content around the *rotated* rect's center. That
    misplaces 90/270-degree pages. Clearing the rotation on our in-memory copy
    (never saved) and passing it explicitly gives the geometry ``_fit_matrix``
    expects.
    """
    rot = src.rotation
    if rot:
        src.set_rotation(0)
    try:
        page.show_pdf_page(cell, src_doc, src.number, rotate=-rot)
    except ValueError:
        pass  # source page has no content (blank)


def _fit_matrix(src: pymupdf.Page, cell: pymupdf.Rect) -> pymupdf.Matrix:
    """Unrotated source-page coordinates -> position inside ``cell`` (as show_pdf_page draws it)."""
    w, h = src.rect.width, src.rect.height
    s = min(cell.width / w, cell.height / h)
    tx = cell.x0 + (cell.width - w * s) / 2
    ty = cell.y0 + (cell.height - h * s) / 2
    return src.rotation_matrix * pymupdf.Matrix(s, 0, 0, s, tx, ty)


def _cover_items(result: DiffResult, row_of_old, row_of_new) -> list[tuple[str, tuple, int]]:
    """(text, color, target row) for each change, in document order."""
    entries: list[tuple[tuple[int, int], str, tuple, int]] = []
    for pc in result.page_changes:
        if pc.kind == "page_inserted":
            text = f"P{pc.id}   Page inserted   new p.{pc.page + 1}  ({pc.word_count} words)"
            row, color = row_of_new[pc.page], COLORS["insert"]
        else:
            text = f"P{pc.id}   Page deleted   old p.{pc.page + 1}  ({pc.word_count} words)"
            row, color = row_of_old[pc.page], COLORS["delete"]
        entries.append(((row, 0), text, color, row))
    for ch in result.changes:
        if ch.new_pages and ch.new_pages[0] in row_of_new:
            row = row_of_new[ch.new_pages[0]]
        else:
            row = row_of_old.get(ch.old_pages[0], 0) if ch.old_pages else 0
        snippet = {
            "replace": f"{ch.old_text}  ->  {ch.new_text}",
            "insert": f"+ {ch.new_text}",
            "delete": f"- {ch.old_text}",
        }[ch.kind]
        text = (
            f"#{ch.id}   {LABELS[ch.kind]}   old p.{_pages(ch.old_pages)} / new p.{_pages(ch.new_pages)}"
            f"   {snippet}"
        )
        entries.append(((row, 1), text, COLORS[ch.kind], row))
    entries.sort(key=lambda e: e[0])
    return [(t, c, r) for _, t, c, r in entries]


def _cover_lines_per_page(first: bool) -> int:
    top = 150.0 if first else 50.0
    return int((_COVER_H - top - 40) // _COVER_LINE)


def _cover_page_count(n_items: int) -> int:
    n = min(n_items, _MAX_COVER_ITEMS) + (1 if n_items > _MAX_COVER_ITEMS else 0)
    first = _cover_lines_per_page(True)
    if n <= first:
        return 1
    return 1 + math.ceil((n - first) / _cover_lines_per_page(False))


def _draw_cover(out_doc, result: DiffResult, items, n_cover: int) -> None:
    from .term import summary_line

    page = out_doc[0]
    page.insert_text((50, 60), "PDF comparison", fontsize=18, fontname="hebo")
    page.insert_text((50, 84), _fit(f"Old: {result.old.path}  ({result.old.page_count} pages)", 95), fontsize=9, fontname="helv")
    page.insert_text((50, 98), _fit(f"New: {result.new.path}  ({result.new.page_count} pages)", 95), fontsize=9, fontname="helv")
    page.insert_text((50, 120), summary_line(result), fontsize=11, fontname="hebo")
    for w in result.warnings[:1]:
        page.insert_text((50, 136), _fit(f"Warning: {w}", 95), fontsize=8, fontname="helv", color=COLORS["delete"])

    shown = items[:_MAX_COVER_ITEMS]
    p, y = 0, 150.0
    remaining = _cover_lines_per_page(True)
    for text, color, row in shown:
        if remaining == 0:
            p += 1
            page, y, remaining = out_doc[p], 50.0, _cover_lines_per_page(False)
        page.insert_text((50, y + 9), _fit(text, 110), fontsize=8.5, fontname="helv", color=color)
        page.insert_link(
            {"kind": pymupdf.LINK_GOTO, "page": n_cover + row, "from": pymupdf.Rect(48, y, 564, y + _COVER_LINE)}
        )
        y += _COVER_LINE
        remaining -= 1
    if len(items) > len(shown):
        if remaining == 0:
            p += 1
            page, y = out_doc[p], 50.0
        page.insert_text((50, y + 9), f"... and {len(items) - len(shown)} more (see JSON output)", fontsize=8.5, fontname="helv")


# ------------------------------------------------------------------------- helpers


def _open(doc_model: Document) -> pymupdf.Document:
    doc = pymupdf.open(doc_model.path, filetype="pdf")
    if doc.needs_pass:
        doc.authenticate(doc_model.password or "")
    return doc


def _save(doc: pymupdf.Document, out: str, keep_encryption: bool, password: str | None = None) -> None:
    if keep_encryption:
        doc.save(out, garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    elif password:
        # Don't write an unprotected copy of protected input.
        doc.save(
            out,
            garbage=1,
            deflate=True,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password,
        )
    else:
        doc.save(out, garbage=1, deflate=True)


def _pages(pages: list[int]) -> str:
    if not pages:
        return "-"
    return str(pages[0] + 1) if len(pages) == 1 else f"{pages[0] + 1}-{pages[-1] + 1}"


def _clip(s: str, n: int = MAX_NOTE_TEXT) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _latin(s: str) -> str:
    """Base-14 fonts only cover WinAnsi; replace anything else."""
    return s.encode("cp1252", "replace").decode("cp1252")


def _fit(s: str, n: int) -> str:
    s = _latin(re.sub(r"[\r\n\t]+", " ", s))
    return s if len(s) <= n else s[: n - 3] + "..."
