"""High-level comparison entry point."""

from __future__ import annotations

import os

from .diff import diff_tokens
from .extract import load_document
from .model import CompareOptions, DiffResult, Document
from .normalize import tokenize_document


def compare(
    old_path: str | os.PathLike,
    new_path: str | os.PathLike,
    options: CompareOptions | None = None,
    old_password: str | None = None,
    new_password: str | None = None,
) -> DiffResult:
    options = options or CompareOptions()
    old = load_document(old_path, old_password)
    new = load_document(new_path, new_password)
    return compare_documents(old, new, options)


def compare_documents(old: Document, new: Document, options: CompareOptions) -> DiffResult:
    result = DiffResult(old=old, new=new, options=options)
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
    result.changes = diff_tokens(
        tokenize_document(old, options), tokenize_document(new, options)
    )
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
