"""High-level comparison entry point."""

from __future__ import annotations

import os

from .align import analyze_pages
from .diff import diff_tokens, token_opcodes
from .extract import load_documents
from .headers import strip_headers_footers
from .model import CompareOptions, DiffResult, Document
from .normalize import tokenize_document


def compare(
    old_path: str | os.PathLike,
    new_path: str | os.PathLike,
    options: CompareOptions | None = None,
    old_password: str | None = None,
    new_password: str | None = None,
    jobs: int | None = None,
) -> DiffResult:
    """Compare two PDF files. ``jobs`` limits the worker processes used for text
    extraction (None = automatic, 1 = none)."""
    options = options or CompareOptions()
    old, new = load_documents([(old_path, old_password), (new_path, new_password)], jobs)
    return compare_documents(old, new, options)


def compare_documents(old: Document, new: Document, options: CompareOptions) -> DiffResult:
    result = DiffResult(old=old, new=new, options=options)
    for label, doc in (("old", old), ("new", new)):
        result.warnings += [f"{label} document {w}" for w in doc.warnings]
    for label, doc in (("old", old), ("new", new)):
        empty = [p.index + 1 for p in doc.pages if not p.has_text]
        if doc.page_count and len(empty) == doc.page_count:
            result.warnings.append(
                f"{label} document has no extractable text (scanned PDF? OCR is not supported)"
            )
        elif empty:
            result.warnings.append(
                f"{label} document pages without extractable text: {_ranges(empty)}"
            )
    if options.header_footer:
        old, _ = strip_headers_footers(old, options.header_footer)
        new, _ = strip_headers_footers(new, options.header_footer)
        result.old, result.new = old, new
    old_tokens = tokenize_document(old, options)
    new_tokens = tokenize_document(new, options)
    ops = token_opcodes(old_tokens, new_tokens)
    pages = analyze_pages(old, new, old_tokens, new_tokens, ops, options.page_threshold)
    result.page_changes = pages.page_changes
    result.page_map = pages.page_map
    result.changes = diff_tokens(old_tokens, new_tokens, ops, pages.skip_old, pages.skip_new)
    return result


def _ranges(nums: list[int]) -> str:
    parts: list[str] = []
    start = prev = nums[0]
    for n in nums[1:] + [None]:  # type: ignore[list-item]
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ", ".join(parts)
