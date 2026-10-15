"""Text extraction from PDF files using PyMuPDF.

Text extraction runs in native code and dominates the run time, so large
documents are split into page ranges that are extracted in worker processes.
"""

from __future__ import annotations

import os

import pymupdf

from . import parallel
from .layout import reading_order
from .model import Document, Page, Word

# Below this many pages in total, process start-up costs more than it saves.
PARALLEL_MIN_PAGES = 100
_MIN_CHUNK = 20

# (page number, width, height, matrix, words as (x0, y0, x1, y1, text, block, line) or
#  None if extraction failed, problem reported by MuPDF)
_RawPage = tuple[int, float, float, tuple, list[tuple], str | None]


class PDFError(Exception):
    """Raised when a PDF cannot be opened or read."""


def load_document(path: str | os.PathLike, password: str | None = None, jobs: int | None = 1) -> Document:
    return load_documents([(path, password)], jobs)[0]


def load_documents(
    specs: list[tuple[str | os.PathLike, str | None]], jobs: int | None = None
) -> list[Document]:
    """Load several documents, extracting pages in parallel when worthwhile.

    ``jobs`` is the maximum number of worker processes: None picks one based on the
    CPU count, 1 disables parallelism.
    """
    opened = []
    for path, password in specs:
        path = os.fspath(path)
        with _open(path, password) as doc:
            opened.append((path, password, doc.page_count))

    total = sum(n for _, _, n in opened)
    workers = parallel.worker_count(jobs, total, _MIN_CHUNK)
    raw: list[list[_RawPage]] | None = None
    if workers > 1 and total >= PARALLEL_MIN_PAGES:
        raw = _extract_parallel(opened, workers)
    if raw is None:
        raw = [_extract_range(path, pw, 0, n) for path, pw, n in opened]

    return [_build(path, pw, pages) for (path, pw, _), pages in zip(opened, raw)]


def _open(path: str, password: str | None) -> pymupdf.Document:
    if not os.path.isfile(path):
        raise PDFError(f"{path}: file not found")
    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as e:
        raise PDFError(f"{path}: cannot open PDF ({e})") from e
    if doc.needs_pass:
        if not password:
            doc.close()
            raise PDFError(f"{path}: PDF is encrypted; a password is required")
        if not doc.authenticate(password):
            doc.close()
            raise PDFError(f"{path}: incorrect password")
    return doc


def quiet_mupdf() -> None:
    """Stop MuPDF from printing to stderr; problems are collected as warnings instead."""
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)


def _extract_range(path: str, password: str | None, start: int, stop: int) -> list[_RawPage]:
    quiet_mupdf()
    out: list[_RawPage] = []
    with _open(path, password) as doc:
        pymupdf.TOOLS.mupdf_warnings(reset=True)
        for pno in range(start, stop):
            try:
                page = doc[pno]
                rect = page.rect
                matrix = tuple(page.rotation_matrix)
                # sort=False keeps MuPDF's content order, which preserves reading
                # order in multi-column layouts (sort=True interleaves columns).
                words = [w[:7] for w in page.get_text("words", sort=False) if w[4].strip()]
                problems = pymupdf.TOOLS.mupdf_warnings(reset=True)
                out.append((pno, rect.width, rect.height, matrix, words, _first_line(problems)))
            except Exception as e:  # a damaged page shouldn't stop the comparison
                pymupdf.TOOLS.mupdf_warnings(reset=True)
                out.append((pno, 0.0, 0.0, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), None, str(e) or type(e).__name__))
    return out


def _first_line(messages: str) -> str | None:
    lines = [m for m in messages.splitlines() if m.strip()]
    if not lines:
        return None
    more = f" (+{len(lines) - 1} more)" if len(lines) > 1 else ""
    return lines[0] + more


def _extract_parallel(opened, workers: int) -> list[list[_RawPage]] | None:
    total = sum(n for _, _, n in opened)
    tasks = [
        (i, path, pw, start, stop)
        for i, (path, pw, n) in enumerate(opened)
        for start, stop in parallel.chunks(n, workers, max(_MIN_CHUNK, total // (workers * 4)))
    ]
    parts = parallel.run(_extract_range, [t[1:] for t in tasks], workers)
    if parts is None:
        return None
    results: list[list[_RawPage]] = [[] for _ in opened]
    for (i, *_), part in zip(tasks, parts):
        results[i].extend(part)
    return results


def _build(path: str, password: str | None, raw: list[_RawPage]) -> Document:
    pages: list[Page] = []
    warnings: list[str] = []
    for pno, width, height, matrix, words, problem in raw:
        if words is None:
            warnings.append(f"page {pno + 1}: text extraction failed ({problem})")
            words = []
        elif problem:
            warnings.append(f"page {pno + 1}: damaged content, text may be incomplete ({problem})")
        pages.append(
            Page(
                index=pno,
                width=width,
                height=height,
                words=[Word(w[4], pno, (w[0], w[1], w[2], w[3]), w[5], w[6]) for w in reading_order(words)],
                matrix=matrix,
            )
        )
    return Document(path=path, pages=pages, password=password, warnings=warnings)
